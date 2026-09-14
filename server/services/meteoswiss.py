"""
Servicio puro de MeteoSwiss (Suiza y Liechtenstein) sobre sus datos abiertos
en ``data.geo.admin.ch``.

Particularidades:

1. **Ficheros CSV por estación**, sin clave ni API de consulta: licencia
   CC BY 4.0, sin límite declarado (piden reintentos con espera). Separador
   ``;``, codificación Windows-1252, marcas en UTC con formato
   ``dd.mm.aaaa HH:MM``.

2. **Tres tramos por granularidad** (``t`` 10 min, ``h`` hora, ``d`` día):
   ``now`` (el día en curso y algo del anterior, cada 10 min), ``recent``
   (del 1 de enero a ayer, se regenera hacia las 02 UTC) e ``historical``
   (hasta el 31 de diciembre pasado). El día local suizo empieza una o dos
   horas antes de las 00 UTC, así que la serie del día junta ``now`` con la
   COLA de ``recent``, pedida con una cabecera ``Range`` (el fichero entero
   pesa varios MB; la cola, unos KB).

3. **La marca cierra el intervalo** en 10 min y horas (16:00 = 15:50-16:00):
   la lluvia de las 00:00 locales es de ayer.

4. **Redes**: SwissMetNet (``ogd-smn``, completas), pluviómetros automáticos
   (``ogd-smn-precip``, solo lluvia) y manuales (``ogd-nime``, lluvia diaria
   publicada al día siguiente: sin observación actual).

5. **Unidades**: viento y racha en km/h (``fu3010*``), presión reducida QFF
   (QNH donde no hay QFF, como en estaciones de montaña). El punto de rocío lo
   deriva el pipeline aunque MeteoSwiss lo publique.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

from data_files import METEOSWISS_STATIONS_PATH
from domain.parsing.common import find_station_by_field, load_stations_json
from server.schemas.errors import ProviderError

logger = logging.getLogger(__name__)

PROVIDER = "METEOSWISS"
BASE_URL = "https://data.geo.admin.ch"
USER_AGENT = "MeteoLabX/1.0 (+https://meteolabx.com)"
DEFAULT_TZ = "Europe/Zurich"
DEFAULT_COLLECTION = "ogd-smn"
ENCODING = "cp1252"

_NAN = float("nan")

# Columnas de 10 min.
C_TEMP, C_RH, C_QFF, C_QNH, C_PABS = "tre200s0", "ure200s0", "pp0qffs0", "pp0qnhs0", "prestas0"
C_WIND, C_GUST, C_DIR, C_RAIN, C_SOLAR = "fu3010z0", "fu3010z1", "dkl010z0", "rre150z0", "gre000z0"
# Columnas horarias.
H_TEMP, H_TMAX, H_TMIN, H_RH = "tre200h0", "tre200hx", "tre200hn", "ure200h0"
H_QFF, H_QNH, H_GUST, H_RAIN = "pp0qffh0", "pp0qnhh0", "fu3010h1", "rre150h0"
H_WIND, H_DIR = "fu3010h0", "dkl010h0"

# Una lectura más vieja que esto no se presenta como observación actual.
CURRENT_MAX_AGE_S = 3 * 3600
MAX_CONCURRENT = 12
RETRY_DELAYS_S = (1.0, 3.0)
# Tope de la cola pedida a un ``recent`` (el de 10 min pesa ~4,5 MB).
MAX_TAIL_BYTES = 400_000

_SEMAPHORES: Dict[int, asyncio.Semaphore] = {}


def _semaphore() -> asyncio.Semaphore:
    loop_id = id(asyncio.get_running_loop())
    if loop_id not in _SEMAPHORES:
        if len(_SEMAPHORES) > 16:
            _SEMAPHORES.clear()
        _SEMAPHORES[loop_id] = asyncio.Semaphore(MAX_CONCURRENT)
    return _SEMAPHORES[loop_id]


def _is_nan(value: float) -> bool:
    return value != value


def _num(value: Any) -> float:
    if value is None:
        return _NAN
    try:
        return float(value)
    except (TypeError, ValueError):
        return _NAN


# =====================================================================
# Catálogo local
# =====================================================================

@lru_cache(maxsize=1)
def _load_stations() -> List[Dict[str, Any]]:
    try:
        return load_stations_json(str(METEOSWISS_STATIONS_PATH), dict_key="stations")
    except Exception as exc:
        logger.warning("Catálogo MeteoSwiss no disponible (%s)", exc)
        return []


def _station_row(station_id: str) -> Dict[str, Any]:
    return find_station_by_field(_load_stations(), field="id", target=station_id)


def station_tz(row: Dict[str, Any]) -> ZoneInfo:
    try:
        return ZoneInfo(str(row.get("tz") or DEFAULT_TZ))
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def normalize_station_id(station_id: Any) -> str:
    return str(station_id or "").strip().upper()


def file_url(collection: str, code: str, suffix: str) -> str:
    lower = code.lower()
    return f"{BASE_URL}/ch.meteoschweiz.{collection}/{lower}/{collection}_{lower}_{suffix}.csv"


# =====================================================================
# HTTP + parsing
# =====================================================================

async def get_text(
    client: httpx.AsyncClient, url: str, *, tail_bytes: Optional[int] = None, timeout_s: float = 30.0,
) -> str:
    """Texto de un CSV (o su cola). Un fichero que no existe es texto vacío:
    no todas las estaciones tienen todos los tramos."""
    headers = {"User-Agent": USER_AGENT}
    if tail_bytes:
        headers["Range"] = f"bytes=-{int(tail_bytes)}"
    try:
        for attempt in range(len(RETRY_DELAYS_S) + 1):
            async with _semaphore():
                response = await client.get(url, headers=headers, timeout=timeout_s)
            if response.status_code not in (429, 500, 502, 503, 504) or attempt == len(RETRY_DELAYS_S):
                break
            await asyncio.sleep(RETRY_DELAYS_S[attempt])
    except httpx.TimeoutException as exc:
        raise ProviderError(
            "provider_timeout", provider=PROVIDER, detail=f"MeteoSwiss timeout: {exc}", status_code=504,
        ) from exc
    except httpx.RequestError as exc:
        raise ProviderError(
            "provider_network_error", provider=PROVIDER, detail=str(exc) or "Network error",
            status_code=502,
        ) from exc
    # S3 responde 403 (AccessDenied) a lo que no existe; 416, a una cola de un
    # fichero vacío.
    if response.status_code in (403, 404, 416):
        return ""
    if response.status_code >= 400:
        raise ProviderError(
            "provider_http_error", provider=PROVIDER, detail=f"HTTP {response.status_code}",
            status_code=502,
        )
    return response.content.decode(ENCODING, errors="replace")


def parse_stamp(text: str) -> Optional[int]:
    try:
        return int(datetime.strptime(text.strip(), "%d.%m.%Y %H:%M").replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


Rows = Dict[int, Dict[str, float]]


def parse_rows(text: str, *, columns: Optional[List[str]] = None) -> Tuple[List[str], Rows]:
    """CSV → (columnas, {epoch: {columna: valor}}).

    Sin ``columns`` la primera línea es la cabecera. Con ``columns`` el texto
    es una cola sin cabecera y su primera línea, cortada, se descarta.
    """
    lines = text.splitlines()
    if columns is None:
        if not lines:
            return [], {}
        columns = [name.strip() for name in lines[0].split(";")]
        body = lines[1:]
    else:
        body = lines[1:]
    rows: Rows = {}
    for line in body:
        parts = line.split(";")
        if len(parts) < 3:
            continue
        epoch = parse_stamp(parts[1])
        if epoch is None:
            continue
        values: Dict[str, float] = {}
        for name, raw in zip(columns[2:], parts[2:]):
            raw = raw.strip()
            if raw and raw != "-":
                value = _num(raw)
                if not _is_nan(value):
                    values[name] = value
        rows[epoch] = values
    return columns, rows


CURRENT_BULK_URL = f"{BASE_URL}/ch.meteoschweiz.messwerte-aktuell/VQHA80.csv"


def parse_current_bulk(text: str) -> Dict[str, Tuple[int, Dict[str, float]]]:
    """``VQHA80.csv`` (último 10 min de toda SwissMetNet) → {código: (epoch,
    valores)}. Misma nomenclatura de columnas que los ficheros por estación;
    la marca va como ``aaaammddHHMM`` UTC y el dato ausente, como ``-``."""
    lines = text.splitlines()
    if len(lines) < 2:
        return {}
    columns = [name.strip() for name in lines[0].split(";")]
    out: Dict[str, Tuple[int, Dict[str, float]]] = {}
    for line in lines[1:]:
        parts = line.split(";")
        if len(parts) < 3:
            continue
        try:
            epoch = int(datetime.strptime(parts[1].strip(), "%Y%m%d%H%M").replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
        values = {
            name: _num(raw) for name, raw in zip(columns[2:], parts[2:])
            if raw.strip() not in ("", "-") and not _is_nan(_num(raw))
        }
        if values:
            out[parts[0].strip().upper()] = (epoch, values)
    return out


async def fetch_span(
    client: httpx.AsyncClient,
    collection: str,
    code: str,
    granularity: str,
    *,
    since_epoch: int,
    timeout_s: float = 30.0,
) -> Rows:
    """Filas desde ``since_epoch``: ``now`` entero y, si no llega tan atrás,
    la cola justa de ``recent``."""
    now_text = await get_text(client, file_url(collection, code, f"{granularity}_now"), timeout_s=timeout_s)
    columns, rows = parse_rows(now_text)
    earliest = min(rows) if rows else None
    if earliest is not None and earliest <= since_epoch:
        return {epoch: values for epoch, values in rows.items() if epoch >= since_epoch}

    step = 600 if granularity == "t" else 3600
    until = earliest if earliest is not None else int(time.time())
    needed = max(1, (until - since_epoch) // step + 2)
    line_len = max((len(line) for line in now_text.splitlines()[1:]), default=0) + 2
    if not columns:
        # Sin ``now`` no hay cabecera: se toma la del principio de ``recent``.
        columns = await _recent_header(client, collection, code, granularity, timeout_s=timeout_s)
        line_len = max(line_len, 40 + 12 * max(0, len(columns) - 2))
        if not columns:
            return {}
    tail_bytes = min(MAX_TAIL_BYTES, needed * line_len + 512)
    tail = await get_text(
        client, file_url(collection, code, f"{granularity}_recent"), tail_bytes=tail_bytes, timeout_s=timeout_s,
    )
    _cols, older = parse_rows(tail, columns=columns)
    merged = {epoch: values for epoch, values in older.items() if epoch >= since_epoch}
    merged.update({epoch: values for epoch, values in rows.items() if epoch >= since_epoch})
    return merged


async def _recent_header(
    client: httpx.AsyncClient, collection: str, code: str, granularity: str, *, timeout_s: float,
) -> List[str]:
    url = file_url(collection, code, f"{granularity}_recent")
    headers = {"User-Agent": USER_AGENT, "Range": "bytes=0-2047"}
    try:
        async with _semaphore():
            response = await client.get(url, headers=headers, timeout=timeout_s)
    except httpx.HTTPError:
        return []
    if response.status_code >= 400:
        return []
    first = response.content.decode(ENCODING, errors="replace").splitlines()[:1]
    return [name.strip() for name in first[0].split(";")] if first else []


def _day_start_epoch(now_local: datetime) -> int:
    return int(now_local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def _column(rows: Rows, *names: str) -> Dict[int, float]:
    """Serie de la primera columna con valor en cada fila (QFF → QNH)."""
    out: Dict[int, float] = {}
    for epoch, values in rows.items():
        for name in names:
            if name in values:
                out[epoch] = values[name]
                break
    return out


def _last(series: Dict[int, float], *, min_epoch: int) -> Tuple[float, Optional[int]]:
    fresh = [epoch for epoch in series if epoch >= min_epoch]
    if not fresh:
        return _NAN, None
    epoch = max(fresh)
    return series[epoch], epoch


# =====================================================================
# API pública del servicio
# =====================================================================

def _no_current(code: str, detail: str) -> ProviderError:
    return ProviderError(
        "provider_no_current_data", provider=PROVIDER, detail=f"MeteoSwiss {code}: {detail}", status_code=502,
    )


# La ficha pide la observación y la serie del día casi a la vez, y las dos salen
# de los mismos ficheros: se comparten durante un minuto.
_TODAY_CACHE: Dict[Tuple[str, int], Tuple[float, Rows]] = {}
TODAY_TTL_S = 60.0


async def _today(
    code: str, row: Dict[str, Any], client: Optional[httpx.AsyncClient], *,
    now: Optional[datetime], timeout_s: float,
) -> Tuple[Rows, datetime]:
    tz = station_tz(row)
    now_local = (now or datetime.now(tz=tz)).astimezone(tz)
    key = (code, _day_start_epoch(now_local))
    cached = _TODAY_CACHE.get(key)
    if cached and time.monotonic() - cached[0] < TODAY_TTL_S:
        return cached[1], now_local
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        rows = await fetch_span(
            client, str(row.get("collection") or DEFAULT_COLLECTION), code, "t",
            # Una hora antes de medianoche: la última lectura de ayer ayuda si
            # hoy aún no hay nada.
            since_epoch=_day_start_epoch(now_local) - 3600,
            timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()
    if len(_TODAY_CACHE) > 500:
        _TODAY_CACHE.clear()
    _TODAY_CACHE[key] = (time.monotonic(), rows)
    return rows, now_local


async def fetch_current(
    station_id: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 30.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    code = normalize_station_id(station_id)
    row = _station_row(code)
    if row and row.get("realtime") is False:
        raise _no_current(code, "pluviómetro manual; MeteoSwiss publica la lectura diaria al día siguiente")

    rows, now_local = await _today(code, row, client, now=now, timeout_s=timeout_s)
    now_epoch = int(now_local.timestamp())
    day_start = _day_start_epoch(now_local)
    fresh_from = now_epoch - CURRENT_MAX_AGE_S
    stamps = [epoch for epoch, values in rows.items() if values and epoch >= fresh_from]
    if not stamps:
        raise _no_current(code, "sin observaciones recientes")

    temps = _column(rows, C_TEMP)
    temp, temp_epoch = _last(temps, min_epoch=fresh_from)
    epoch = temp_epoch or max(stamps)
    dt_utc = datetime.fromtimestamp(epoch, tz=timezone.utc)

    today_temps = [value for stamp, value in temps.items() if stamp >= day_start]
    gusts = [value for stamp, value in _column(rows, C_GUST).items() if stamp > day_start]
    rain = [value for stamp, value in _column(rows, C_RAIN).items() if stamp > day_start]
    daily_extremes: Dict[str, float] = {}
    if today_temps:
        daily_extremes["temp_max"] = max(today_temps)
        daily_extremes["temp_min"] = min(today_temps)
    if gusts:
        daily_extremes["gust_max"] = max(gusts)

    tz = station_tz(row)
    observation: Dict[str, Any] = {
        "Tc": temp,
        "RH": _last(_column(rows, C_RH), min_epoch=fresh_from)[0],
        "p_hpa": _last(_column(rows, C_QFF, C_QNH), min_epoch=fresh_from)[0],
        "p_abs_hpa": _last(_column(rows, C_PABS), min_epoch=fresh_from)[0],
        "wind": _last(_column(rows, C_WIND), min_epoch=fresh_from)[0],
        "gust": _last(_column(rows, C_GUST), min_epoch=fresh_from)[0],
        "wind_dir_deg": _last(_column(rows, C_DIR), min_epoch=fresh_from)[0],
        "Td": _NAN,
        "feels_like": _NAN,
        "heat_index": _NAN,
        "wind_chill": _NAN,
        "precip_rate": _NAN,
        "precip_total": float(sum(max(0.0, value) for value in rain)) if rain else _NAN,
        "solar_radiation": _last(_column(rows, C_SOLAR), min_epoch=fresh_from)[0],
        "uv": _NAN,
        "epoch": epoch,
        "time_local": dt_utc.astimezone(tz).isoformat(),
        "time_utc": dt_utc.isoformat(),
        "lat": _num(row.get("lat")),
        "lon": _num(row.get("lon")),
        "elevation": _num(row.get("elev")) if row.get("elev") is not None else 0.0,
        "station_name": str(row.get("name", "") or "").strip() or code,
        "daily_extremes": daily_extremes,
    }
    from domain.observation_pipeline import add_basic_derived
    return add_basic_derived(observation)


def _empty(row: Dict[str, Any], keys: Iterable[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {key: [] for key in keys}
    out.update(lat=_num(row.get("lat")), lon=_num(row.get("lon")), has_data=False)
    return out


async def fetch_today_series(
    station_id: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 30.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Serie de 10 min del día local de la estación."""
    code = normalize_station_id(station_id)
    row = _station_row(code)
    keys = ("epochs", "temps", "humidities", "dewpts", "pressures", "uv_indexes",
            "solar_radiations", "winds", "gusts", "wind_dirs")
    if row and row.get("realtime") is False:
        return _empty(row, keys)
    rows, now_local = await _today(code, row, client, now=now, timeout_s=timeout_s)
    day_start = _day_start_epoch(now_local)
    epochs = sorted(epoch for epoch, values in rows.items() if values and epoch >= day_start)
    if not epochs:
        return _empty(row, keys)

    def _col(*names: str) -> List[float]:
        series = _column(rows, *names)
        return [series.get(epoch, _NAN) for epoch in epochs]

    precip = _col(C_RAIN)
    if epochs[0] == day_start:
        precip[0] = _NAN  # la lluvia de medianoche es de los 10 min de ayer
    return {
        "epochs": epochs,
        "temps": _col(C_TEMP),
        "humidities": _col(C_RH),
        "dewpts": [_NAN] * len(epochs),
        "pressures": _col(C_QFF, C_QNH),
        "uv_indexes": [_NAN] * len(epochs),
        "solar_radiations": _col(C_SOLAR),
        "precip_step_mm": precip,
        "winds": _col(C_WIND),
        "gusts": _col(C_GUST),
        "wind_dirs": _col(C_DIR),
        "lat": _num(row.get("lat")),
        "lon": _num(row.get("lon")),
        "has_data": True,
    }


async def fetch_recent_series(
    station_id: str,
    *,
    days_back: int = 7,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 45.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Tendencia horaria (T/HR/presión) de los ficheros horarios."""
    code = normalize_station_id(station_id)
    row = _station_row(code)
    keys = ("epochs", "temps", "humidities", "pressures")
    if row and row.get("realtime") is False:
        return _empty(row, keys)
    now_utc = (now or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)
    since = int((now_utc - timedelta(days=max(1, int(days_back)))).timestamp())
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        rows = await fetch_span(
            client, str(row.get("collection") or DEFAULT_COLLECTION), code, "h",
            since_epoch=since, timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()
    temps = _column(rows, H_TEMP)
    humidities = _column(rows, H_RH)
    pressures = _column(rows, H_QFF, H_QNH)
    epochs = sorted(set(temps) | set(humidities) | set(pressures))
    if not epochs:
        return _empty(row, keys)
    return {
        "epochs": epochs,
        "temps": [temps.get(e, _NAN) for e in epochs],
        "humidities": [humidities.get(e, _NAN) for e in epochs],
        "pressures": [pressures.get(e, _NAN) for e in epochs],
        "lat": _num(row.get("lat")),
        "lon": _num(row.get("lon")),
        "has_data": True,
    }
