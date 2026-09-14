"""
Servicio puro de DMI (Dinamarca, Groenlandia y Feroe) sobre la API abierta
``opendataapi.dmi.dk/v2/metObs``.

Particularidades:

1. **API pública sin credenciales**, OGC API Features, licencia CC BY 4.0.
   Oficialmente, 500 peticiones cada 5 s; en la práctica corta con HTTP 429 a
   partir de unas 12 SIMULTÁNEAS (medido: 10 a la vez pasan, 15 no). Todas las
   consultas del proceso comparten un tope de :data:`MAX_CONCURRENT` y un 429
   se reintenta tras una espera.

2. **Un parámetro por consulta**: ``observation/items?stationId=…&
   parameterId=…&datetime=…`` devuelve una fila por instante. La serie de una
   estación son varias consultas en paralelo que luego se alinean por marca;
   solo se piden los parámetros que la estación publica (inventario).

3. **Parámetros**: temperatura (``temp_dry``) y humedad instantáneas cada 10
   min, presión en la estación y a nivel del mar, viento medio de 10 min, racha
   máxima de 3 s en esos 10 min (``wind_max``), dirección, lluvia de 10 min,
   radiación global, y máxima/mínima de la hora. Viento m/s → km/h. El punto de
   rocío lo deriva el pipeline aunque DMI lo publique.

4. **Día local por estación**: Europe/Copenhagen, Atlantic/Faroe o uno de los
   cuatro husos de Groenlandia (inventario).

5. **Manuales** (lluvia de 24 h en Groenlandia): DMI las publica por tandas con
   semanas de retraso, así que no tienen observación actual.
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

from data_files import DMI_STATIONS_PATH
from domain.parsing.common import find_station_by_field, load_stations_json
from server.schemas.errors import ProviderError

logger = logging.getLogger(__name__)

PROVIDER = "DMI"
BASE_URL = "https://opendataapi.dmi.dk/v2/metObs/collections/observation/items"
USER_AGENT = "MeteoLabX/1.0 (+https://meteolabx.com)"
DEFAULT_TZ = "Europe/Copenhagen"
MAX_LIMIT = 300000

_NAN = float("nan")

# Parámetros que usa la ficha, por papel.
P_TEMP, P_RH, P_MSL, P_PABS = "temp_dry", "humidity", "pressure_at_sea", "pressure"
P_WIND, P_GUST, P_DIR = "wind_speed", "wind_max", "wind_dir"
P_RAIN10, P_SOLAR = "precip_past10min", "radia_glob"
P_TMAX1H, P_TMIN1H, P_GUST1H, P_RAIN1H = (
    "temp_max_past1h", "temp_min_past1h", "wind_gust_always_past1h", "precip_past1h",
)
TODAY_PARAMETERS = (
    P_TEMP, P_RH, P_MSL, P_PABS, P_WIND, P_GUST, P_DIR, P_RAIN10, P_SOLAR,
    P_TMAX1H, P_TMIN1H, P_GUST1H,
)
TREND_PARAMETERS = (P_TEMP, P_RH, P_MSL)
# Una lectura más vieja que esto no se presenta como observación actual.
CURRENT_MAX_AGE_S = 3 * 3600
MAX_CONCURRENT = 6
RETRY_DELAYS_S = (1.0, 2.5)

# Un semáforo por bucle de eventos: los de asyncio se atan al primero que los usa.
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


def _kmh(value: float) -> float:
    return value * 3.6 if not _is_nan(value) else _NAN


# =====================================================================
# Catálogo local
# =====================================================================

@lru_cache(maxsize=1)
def _load_stations() -> List[Dict[str, Any]]:
    try:
        return load_stations_json(str(DMI_STATIONS_PATH), dict_key="stations")
    except Exception as exc:
        logger.warning("Catálogo DMI no disponible (%s)", exc)
        return []


def _station_row(station_id: str) -> Dict[str, Any]:
    return find_station_by_field(_load_stations(), field="id", target=station_id)


def station_tz(row: Dict[str, Any]) -> ZoneInfo:
    try:
        return ZoneInfo(str(row.get("tz") or DEFAULT_TZ))
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def normalize_station_id(station_id: Any) -> str:
    return str(station_id or "").strip()


def _available(row: Dict[str, Any], parameters: Iterable[str]) -> Tuple[str, ...]:
    """Solo lo que la estación publica. Sin inventario, se prueba todo."""
    published = set(row.get("parameters") or [])
    if not published:
        return tuple(parameters)
    return tuple(parameter for parameter in parameters if parameter in published)


# =====================================================================
# HTTP + parsing
# =====================================================================

async def _get_json(client: httpx.AsyncClient, params: Dict[str, Any], *, timeout_s: float) -> Any:
    try:
        for attempt in range(len(RETRY_DELAYS_S) + 1):
            async with _semaphore():
                response = await client.get(
                    BASE_URL, params=params,
                    headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"},
                    timeout=timeout_s,
                )
            if response.status_code != 429 or attempt == len(RETRY_DELAYS_S):
                break
            await asyncio.sleep(RETRY_DELAYS_S[attempt])
    except httpx.TimeoutException as exc:
        raise ProviderError(
            "provider_timeout", provider=PROVIDER, detail=f"DMI timeout: {exc}", status_code=504,
        ) from exc
    except httpx.RequestError as exc:
        raise ProviderError(
            "provider_network_error", provider=PROVIDER, detail=str(exc) or "Network error",
            status_code=502,
        ) from exc
    if response.status_code == 429:
        raise ProviderError(
            "provider_ratelimit", provider=PROVIDER, detail="DMI rate limit (HTTP 429)",
            status_code=429,
        )
    if response.status_code >= 400:
        raise ProviderError(
            "provider_http_error", provider=PROVIDER, detail=f"HTTP {response.status_code}",
            status_code=502,
        )
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderError(
            "provider_bad_response", provider=PROVIDER, detail=f"JSON inválido: {exc!r}",
            status_code=502,
        ) from exc


def parse_time(text: Any) -> Optional[int]:
    try:
        return int(datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def parse_features(payload: Any) -> Dict[str, Dict[str, Dict[int, float]]]:
    """GeoJSON de observaciones → {estación: {parámetro: {epoch: valor}}}."""
    out: Dict[str, Dict[str, Dict[int, float]]] = {}
    if not isinstance(payload, dict):
        return out
    for feature in payload.get("features") or []:
        props = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(props, dict):
            continue
        epoch = parse_time(props.get("observed"))
        value = _num(props.get("value"))
        station = str(props.get("stationId") or "").strip()
        parameter = str(props.get("parameterId") or "").strip()
        if epoch is None or _is_nan(value) or not station or not parameter:
            continue
        out.setdefault(station, {}).setdefault(parameter, {})[epoch] = value
    return out


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_parameters(
    client: httpx.AsyncClient,
    *,
    parameters: Iterable[str],
    since_epoch: int,
    station_id: Optional[str] = None,
    timeout_s: float = 30.0,
) -> Dict[str, Dict[str, Dict[int, float]]]:
    """Varios parámetros en paralelo (uno por consulta), de una estación o de
    toda la red, desde ``since_epoch``."""

    async def _one(parameter: str) -> Dict[str, Dict[str, Dict[int, float]]]:
        params: Dict[str, Any] = {
            "parameterId": parameter,
            "datetime": f"{_iso(since_epoch)}/..",
            "limit": MAX_LIMIT,
        }
        if station_id:
            params["stationId"] = station_id
        return parse_features(await _get_json(client, params, timeout_s=timeout_s))

    merged: Dict[str, Dict[str, Dict[int, float]]] = {}
    for result in await asyncio.gather(*(_one(parameter) for parameter in parameters)):
        for station, series in result.items():
            merged.setdefault(station, {}).update(series)
    return merged


def _day_start_epoch(now_local: datetime) -> int:
    return int(now_local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def _last(series: Dict[str, Dict[int, float]], parameter: str, *, min_epoch: int) -> Tuple[float, Optional[int]]:
    values = series.get(parameter) or {}
    fresh = [epoch for epoch in values if epoch >= min_epoch]
    if not fresh:
        return _NAN, None
    epoch = max(fresh)
    return values[epoch], epoch


# =====================================================================
# API pública del servicio
# =====================================================================

def _no_current(code: str, detail: str) -> ProviderError:
    return ProviderError(
        "provider_no_current_data", provider=PROVIDER, detail=f"DMI {code}: {detail}", status_code=502,
    )


# La ficha pide la observación y la serie del día casi a la vez, y las dos salen
# de las mismas consultas: se comparten durante un minuto.
_TODAY_CACHE: Dict[Tuple[str, int], Tuple[float, Dict[str, Dict[int, float]]]] = {}
TODAY_TTL_S = 60.0


async def _today(
    code: str, row: Dict[str, Any], client: Optional[httpx.AsyncClient], *,
    now: Optional[datetime], timeout_s: float,
) -> Tuple[Dict[str, Dict[int, float]], datetime]:
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
        data = await fetch_parameters(
            client,
            parameters=_available(row, TODAY_PARAMETERS),
            # Una hora antes de medianoche: la máxima horaria de las 00:00 y la
            # última lectura de ayer ayudan si hoy aún no hay nada.
            since_epoch=_day_start_epoch(now_local) - 3600,
            station_id=code,
            timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()
    series = data.get(code, {})
    if len(_TODAY_CACHE) > 500:
        _TODAY_CACHE.clear()
    _TODAY_CACHE[key] = (time.monotonic(), series)
    return series, now_local


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
        raise _no_current(code, "estación manual; DMI publica sus lecturas con semanas de retraso")

    series, now_local = await _today(code, row, client, now=now, timeout_s=timeout_s)
    now_epoch = int(now_local.timestamp())
    day_start = _day_start_epoch(now_local)
    fresh_from = now_epoch - CURRENT_MAX_AGE_S
    stamps = [epoch for values in series.values() for epoch in values if epoch >= fresh_from]
    if not stamps:
        raise _no_current(code, "sin observaciones recientes")

    temp, temp_epoch = _last(series, P_TEMP, min_epoch=fresh_from)
    epoch = temp_epoch or max(stamps)
    dt_utc = datetime.fromtimestamp(epoch, tz=timezone.utc)

    def _today_values(parameter: str, *, after: int = day_start) -> List[float]:
        return [value for stamp, value in (series.get(parameter) or {}).items() if stamp > after]

    # Máxima y mínima: las de cada hora (cierran en su marca, así que la de las
    # 00:00 es de ayer) más las instantáneas de 10 min del día.
    temps_now = [value for stamp, value in (series.get(P_TEMP) or {}).items() if stamp >= day_start]
    highs = _today_values(P_TMAX1H) + temps_now
    lows = _today_values(P_TMIN1H) + temps_now
    gusts = _today_values(P_GUST1H) + [
        value for stamp, value in (series.get(P_GUST) or {}).items() if stamp > day_start
    ]
    rain = _today_values(P_RAIN10)
    daily_extremes: Dict[str, float] = {}
    if highs:
        daily_extremes["temp_max"] = max(highs)
    if lows:
        daily_extremes["temp_min"] = min(lows)
    if gusts:
        daily_extremes["gust_max"] = _kmh(max(gusts))

    tz = station_tz(row)
    observation: Dict[str, Any] = {
        "Tc": temp,
        "RH": _last(series, P_RH, min_epoch=fresh_from)[0],
        "p_hpa": _last(series, P_MSL, min_epoch=fresh_from)[0],
        "p_abs_hpa": _last(series, P_PABS, min_epoch=fresh_from)[0],
        "wind": _kmh(_last(series, P_WIND, min_epoch=fresh_from)[0]),
        "gust": _kmh(_last(series, P_GUST, min_epoch=fresh_from)[0]),
        "wind_dir_deg": _last(series, P_DIR, min_epoch=fresh_from)[0],
        "Td": _NAN,
        "feels_like": _NAN,
        "heat_index": _NAN,
        "wind_chill": _NAN,
        "precip_rate": _NAN,
        "precip_total": float(sum(max(0.0, value) for value in rain)) if rain else _NAN,
        "solar_radiation": _last(series, P_SOLAR, min_epoch=fresh_from)[0],
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
    series, now_local = await _today(code, row, client, now=now, timeout_s=timeout_s)
    day_start = _day_start_epoch(now_local)
    instant = (P_TEMP, P_RH, P_MSL, P_WIND, P_GUST, P_DIR, P_RAIN10, P_SOLAR)
    epochs = sorted({
        stamp for parameter in instant for stamp in (series.get(parameter) or {}) if stamp >= day_start
    })
    if not epochs:
        return _empty(row, keys)

    def _col(parameter: str, convert=None) -> List[float]:
        values = series.get(parameter) or {}
        column = [values.get(stamp, _NAN) for stamp in epochs]
        return [convert(value) for value in column] if convert else column

    precip = _col(P_RAIN10)
    if epochs[0] == day_start:
        precip[0] = _NAN  # la lluvia de medianoche es de los 10 min de ayer
    return {
        "epochs": epochs,
        "temps": _col(P_TEMP),
        "humidities": _col(P_RH),
        "dewpts": [_NAN] * len(epochs),
        "pressures": _col(P_MSL),
        "uv_indexes": [_NAN] * len(epochs),
        "solar_radiations": _col(P_SOLAR),
        "precip_step_mm": precip,
        "winds": _col(P_WIND, convert=_kmh),
        "gusts": _col(P_GUST, convert=_kmh),
        "wind_dirs": _col(P_DIR),
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
    """Tendencia horaria (T/HR/presión MSL): las lecturas en punto."""
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
        data = await fetch_parameters(
            client, parameters=_available(row, TREND_PARAMETERS), since_epoch=since,
            station_id=code, timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()
    series = data.get(code, {})
    epochs = sorted({
        stamp for parameter in TREND_PARAMETERS for stamp in (series.get(parameter) or {})
        if stamp % 3600 == 0
    })
    if not epochs:
        return _empty(row, keys)
    return {
        "epochs": epochs,
        "temps": [(series.get(P_TEMP) or {}).get(e, _NAN) for e in epochs],
        "humidities": [(series.get(P_RH) or {}).get(e, _NAN) for e in epochs],
        "pressures": [(series.get(P_MSL) or {}).get(e, _NAN) for e in epochs],
        "lat": _num(row.get("lat")),
        "lon": _num(row.get("lon")),
        "has_data": True,
    }
