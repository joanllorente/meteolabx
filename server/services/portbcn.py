"""
Servicio puro de las estaciones meteorológicas del Port de Barcelona, sobre
el almacén de datos de su portal CKAN (``opendata.portdebarcelona.cat``,
CC BY-SA 4.0).

Particularidades:

1. **Un recurso por tramo**: cada estación publica los últimos tres días y un
   fichero por año (que se actualiza una vez al día). El almacén los sirve en
   JSON con ``datastore_search``; la consulta SQL está desactivada, así que no
   se filtra por fechas: se ordena por ``TIMESTAMP`` y se limita el número de
   registros. Todos los valores llegan como texto, con ``"NAN"`` de hueco.

2. **Registros de 10 minutos, en UTC y cerrando el intervalo** (comprobado
   contra la XEMA: la ZAL Prat del puerto es la YQ de Meteocat y su máxima cae
   a la misma hora). La lluvia de las 00:00 es de ayer.

3. **Unidades**: viento y racha en m/s (la rosa de vientos del propio puerto
   lo indica), presión de estación en hPa (como la de la XEMA), radiación en
   W/m². La racha es el máximo de 3 s; su columna se llama ``VV10m3Seg_Max`` o
   ``VV_Max`` según la estación.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

from data_files import PORTBCN_STATIONS_PATH
from domain.parsing.common import find_station_by_field, load_stations_json
from server.schemas.errors import ProviderError

logger = logging.getLogger(__name__)

PROVIDER = "PORTBCN"
CKAN_URL = "https://opendata.portdebarcelona.cat/api/3/action"
USER_AGENT = "MeteoLabX/1.0 (+https://meteolabx.com)"
DEFAULT_TZ = "Europe/Madrid"
STEP_S = 600

C_WIND, C_DIR, C_PRES = "VV_S_WVT", "DV_D1_WVT", "PRE_Avg"
C_TEMP, C_RH, C_SOLAR, C_RAIN = "TEM_Avg", "HUM_Avg", "RAD_Avg", "PLU_Tot"
GUST_COLUMNS = ("VV10m3Seg_Max", "VV_Max")

# Registros de tres días (432 como mucho): con 300 se cubren hoy, las últimas
# 24 h y el final de ayer.
RECENT_LIMIT = 300
CURRENT_MAX_AGE_S = 3 * 3600
RETRY_DELAYS_S = (0.5, 2.0)

_NAN = float("nan")

Rows = Dict[int, Dict[str, float]]


# =====================================================================
# Catálogo local
# =====================================================================

@lru_cache(maxsize=1)
def _load_stations() -> List[Dict[str, Any]]:
    try:
        return load_stations_json(str(PORTBCN_STATIONS_PATH), dict_key="stations")
    except Exception as exc:
        logger.warning("Catálogo del Port de Barcelona no disponible (%s)", exc)
        return []


def stations() -> List[Dict[str, Any]]:
    return list(_load_stations())


def normalize_station_id(station_id: Any) -> str:
    return str(station_id or "").strip()


def station_row(station_id: str) -> Dict[str, Any]:
    return find_station_by_field(_load_stations(), field="id", target=normalize_station_id(station_id))


def station_tz(row: Dict[str, Any]) -> ZoneInfo:
    try:
        return ZoneInfo(str(row.get("tz") or DEFAULT_TZ))
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def _num(value: Any) -> float:
    if value is None:
        return _NAN
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return _NAN
    return number if math.isfinite(number) else _NAN


def _is_nan(value: float) -> bool:
    return value != value


def kmh(value: float) -> float:
    return value * 3.6 if not _is_nan(value) else _NAN


def msl_from_station(p_station: float, elevation_m: float) -> float:
    """Presión de estación → nivel del mar (exponencial barométrica z/8000,
    como Meteocat)."""
    if _is_nan(p_station) or _is_nan(elevation_m):
        return _NAN
    return p_station * math.exp(elevation_m / 8000.0)


# =====================================================================
# HTTP + parsing
# =====================================================================

def parse_records(records: Any) -> Rows:
    """Registros del almacén → {epoch UTC: {columna: valor}} sin los huecos."""
    out: Rows = {}
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict):
            continue
        try:
            stamp = datetime.strptime(str(record.get("TIMESTAMP") or ""), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        epoch = int(stamp.replace(tzinfo=timezone.utc).timestamp())
        values = {}
        for column, raw in record.items():
            if column in ("_id", "TIMESTAMP", "RECORD"):
                continue
            value = _num(raw)
            if not _is_nan(value):
                values[column] = value
        gust = next((values[name] for name in GUST_COLUMNS if name in values), None)
        if gust is not None:
            values["gust"] = gust
        out[epoch] = values
    return out


async def fetch_resource(
    client: httpx.AsyncClient, resource_id: str, *, limit: int, timeout_s: float = 30.0,
) -> Rows:
    """Los ``limit`` registros más recientes de un recurso del almacén."""
    params = {"resource_id": resource_id, "sort": "TIMESTAMP desc", "limit": int(limit)}
    for attempt, delay in enumerate((*RETRY_DELAYS_S, None)):
        try:
            response = await client.get(
                f"{CKAN_URL}/datastore_search",
                params=params,
                headers={"Accept": "application/json", "User-Agent": USER_AGENT},
                timeout=timeout_s,
            )
            break
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "provider_timeout", provider=PROVIDER, detail=f"Port de Barcelona timeout: {exc}",
                status_code=504,
            ) from exc
        except httpx.RequestError as exc:
            if delay is None:
                raise ProviderError(
                    "provider_network_error", provider=PROVIDER,
                    detail=str(exc) or "Network error", status_code=502,
                ) from exc
            logger.info("Port de Barcelona: reintento %d (%s)", attempt + 1, type(exc).__name__)
            await asyncio.sleep(delay)
    if response.status_code >= 400:
        raise ProviderError(
            "provider_http_error", provider=PROVIDER, detail=f"HTTP {response.status_code}", status_code=502,
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderError(
            "provider_bad_response", provider=PROVIDER, detail=f"JSON inválido: {exc!r}", status_code=502,
        ) from exc
    if not isinstance(payload, dict) or not payload.get("success"):
        raise ProviderError(
            "provider_bad_response", provider=PROVIDER, detail="Respuesta CKAN sin éxito", status_code=502,
        )
    return parse_records((payload.get("result") or {}).get("records"))


# =====================================================================
# API pública del servicio
# =====================================================================

def _station(station_id: str) -> Dict[str, Any]:
    row = station_row(station_id)
    if not row or not row.get("resource_recent"):
        raise ProviderError(
            "station_not_found", provider=PROVIDER,
            detail=f"Estación del Port de Barcelona no encontrada: {station_id}", status_code=404,
        )
    return row


def _day_start_epoch(now_local: datetime) -> int:
    return int(now_local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def column(rows: Rows, name: str, *, since: Optional[int] = None, until: Optional[int] = None) -> Dict[int, float]:
    return {
        epoch: values[name] for epoch, values in rows.items()
        if name in values and (since is None or epoch > since) and (until is None or epoch <= until)
    }


def _last(series: Dict[int, float], *, min_epoch: int) -> float:
    fresh = [epoch for epoch in series if epoch >= min_epoch]
    return series[max(fresh)] if fresh else _NAN


# La ficha pide la observación y la serie del día casi a la vez: comparten la
# consulta dos minutos.
_RECENT_CACHE: Dict[str, Tuple[float, Rows]] = {}
RECENT_TTL_S = 120.0


async def _recent(row: Dict[str, Any], client: Optional[httpx.AsyncClient], *, timeout_s: float) -> Rows:
    resource = str(row["resource_recent"])
    cached = _RECENT_CACHE.get(resource)
    if cached and time.monotonic() - cached[0] < RECENT_TTL_S:
        return cached[1]
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        rows = await fetch_resource(client, resource, limit=RECENT_LIMIT, timeout_s=timeout_s)
    finally:
        if owns_client:
            await client.aclose()
    if len(_RECENT_CACHE) > 100:
        _RECENT_CACHE.clear()
    _RECENT_CACHE[resource] = (time.monotonic(), rows)
    return rows


async def fetch_current(
    station_id: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 30.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    row = _station(station_id)
    rows = await _recent(row, client, timeout_s=timeout_s)
    tz = station_tz(row)
    now_local = (now or datetime.now(tz=tz)).astimezone(tz)
    now_epoch = int(now_local.timestamp())
    fresh_from = now_epoch - CURRENT_MAX_AGE_S
    stamps = [epoch for epoch, values in rows.items() if values and fresh_from <= epoch <= now_epoch]
    if not stamps:
        raise ProviderError(
            "provider_no_current_data", provider=PROVIDER,
            detail=f"Port de Barcelona {station_id}: sin observaciones recientes", status_code=502,
        )
    epoch = max(stamps)
    day_start = _day_start_epoch(now_local)
    elevation = _num(row.get("elev"))
    temps = list(column(rows, C_TEMP, since=day_start, until=now_epoch).values())
    gusts = list(column(rows, "gust", since=day_start, until=now_epoch).values())
    rain = list(column(rows, C_RAIN, since=day_start, until=now_epoch).values())
    daily_extremes: Dict[str, float] = {}
    if temps:
        daily_extremes["temp_max"] = max(temps)
        daily_extremes["temp_min"] = min(temps)
    if gusts:
        daily_extremes["gust_max"] = kmh(max(gusts))

    def _now(name: str) -> float:
        return _last(column(rows, name, until=now_epoch), min_epoch=fresh_from)

    p_abs = _now(C_PRES)
    dt_utc = datetime.fromtimestamp(epoch, tz=timezone.utc)
    observation: Dict[str, Any] = {
        "Tc": _now(C_TEMP),
        "RH": _now(C_RH),
        "p_hpa": msl_from_station(p_abs, elevation if not _is_nan(elevation) else 0.0),
        "p_abs_hpa": p_abs,
        "wind": kmh(_now(C_WIND)),
        "gust": kmh(_now("gust")),
        "wind_dir_deg": _now(C_DIR),
        "Td": _NAN,
        "feels_like": _NAN,
        "heat_index": _NAN,
        "wind_chill": _NAN,
        "precip_rate": _NAN,
        "precip_total": float(sum(max(0.0, value) for value in rain)) if rain else _NAN,
        "solar_radiation": _now(C_SOLAR),
        "uv": _NAN,
        "epoch": epoch,
        "time_local": dt_utc.astimezone(tz).isoformat(),
        "time_utc": dt_utc.isoformat(),
        "lat": _num(row.get("lat")),
        "lon": _num(row.get("lon")),
        "elevation": elevation if not _is_nan(elevation) else 0.0,
        "station_name": str(row.get("name") or "").strip() or normalize_station_id(station_id),
        "daily_extremes": daily_extremes,
    }
    from domain.observation_pipeline import add_basic_derived
    return add_basic_derived(observation)


def _empty(row: Dict[str, Any], keys: Iterable[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {key: [] for key in keys}
    out.update(lat=_num(row.get("lat")), lon=_num(row.get("lon")), has_data=False)
    return out


_TODAY_KEYS = (
    "epochs", "temps", "humidities", "dewpts", "pressures", "uv_indexes",
    "solar_radiations", "precip_step_mm", "winds", "gusts", "wind_dirs",
)


async def fetch_today_series(
    station_id: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 30.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Serie de 10 minutos del día local."""
    row = _station(station_id)
    rows = await _recent(row, client, timeout_s=timeout_s)
    tz = station_tz(row)
    now_local = (now or datetime.now(tz=tz)).astimezone(tz)
    day_start = _day_start_epoch(now_local)
    now_epoch = int(now_local.timestamp())
    epochs = sorted(epoch for epoch, values in rows.items() if values and day_start < epoch <= now_epoch)
    if not epochs:
        return _empty(row, _TODAY_KEYS)
    elevation = _num(row.get("elev"))
    elevation = 0.0 if _is_nan(elevation) else elevation

    def _col(name: str, convert=None) -> List[float]:
        values = [rows[epoch].get(name, _NAN) for epoch in epochs]
        return [convert(value) for value in values] if convert else values

    return {
        "epochs": epochs,
        "temps": _col(C_TEMP),
        "humidities": _col(C_RH),
        "dewpts": [_NAN] * len(epochs),
        "pressures": _col(C_PRES, convert=lambda value: msl_from_station(value, elevation)),
        "uv_indexes": [_NAN] * len(epochs),
        "solar_radiations": _col(C_SOLAR),
        "precip_step_mm": _col(C_RAIN),
        "winds": _col(C_WIND, convert=kmh),
        "gusts": _col("gust", convert=kmh),
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
    """Tendencia (T/HR/presión) de los últimos días: el fichero del año, que se
    cierra una vez al día, más los tres días recientes para el último tramo."""
    row = _station(station_id)
    keys = ("epochs", "temps", "humidities", "pressures")
    now_utc = (now or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)
    since = int((now_utc - timedelta(days=max(1, int(days_back)))).timestamp())
    years = row.get("resource_years") or {}
    resources = [years[year] for year in sorted({str(now_utc.year), str((now_utc - timedelta(days=max(1, int(days_back)))).year)}) if year in years]
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        limit = max(1, int(days_back)) * 144 + 144
        parts = await asyncio.gather(*(
            fetch_resource(client, resource, limit=limit, timeout_s=timeout_s) for resource in resources
        ))
        recent = await _recent(row, client, timeout_s=timeout_s)
    finally:
        if owns_client:
            await client.aclose()
    rows: Rows = {}
    for part in (*parts, recent):
        rows.update(part)
    epochs = sorted(epoch for epoch in rows if epoch >= since and rows[epoch])
    if not epochs:
        return _empty(row, keys)
    elevation = _num(row.get("elev"))
    elevation = 0.0 if _is_nan(elevation) else elevation
    return {
        "epochs": epochs,
        "temps": [rows[epoch].get(C_TEMP, _NAN) for epoch in epochs],
        "humidities": [rows[epoch].get(C_RH, _NAN) for epoch in epochs],
        "pressures": [msl_from_station(rows[epoch].get(C_PRES, _NAN), elevation) for epoch in epochs],
        "lat": _num(row.get("lat")),
        "lon": _num(row.get("lon")),
        "has_data": True,
    }


# =====================================================================
# Agregados diarios (ranking)
# =====================================================================

def day_aggregates(
    rows: Rows, *, day_start: int, day_end: int, min_coverage: float,
) -> Optional[Dict[str, Any]]:
    """Máxima, mínima, racha y lluvia de ``(day_start, day_end]``.

    ``None`` si faltan demasiados registros: una estación parada a media
    mañana daría como máxima la de la madrugada.
    """
    stamps = sorted(epoch for epoch, values in rows.items() if values and day_start < epoch <= day_end)
    if not stamps:
        return None
    expected = max(1, (day_end - day_start) // STEP_S)
    if expected >= 3 and len(stamps) < min_coverage * expected:
        return None
    temps = [rows[epoch][C_TEMP] for epoch in stamps if C_TEMP in rows[epoch]]
    gusts = [rows[epoch]["gust"] for epoch in stamps if "gust" in rows[epoch]]
    rain = [rows[epoch][C_RAIN] for epoch in stamps if C_RAIN in rows[epoch]]
    return {
        "tmax": max(temps) if temps else None,
        "tmin": min(temps) if temps else None,
        "gust": round(kmh(max(gusts)), 1) if gusts else None,
        "rain": round(sum(max(0.0, value) for value in rain), 1) if rain else None,
        "last": stamps[-1],
    }
