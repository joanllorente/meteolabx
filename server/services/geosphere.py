"""
Servicio puro de GeoSphere Austria (dataset.api.hub.geosphere.at).

Particularidades:

1. **API pública sin credenciales** (Data Hub open-data, CC BY 4.0).
   Rate limit generoso pero real: los TTL de caché del router amortiguan.

2. **Dataset TAWES de 10 minutos**: ``station/historical/tawes-v1-10min``
   sirve hasta ~10 min del presente, así que cubre current + series con
   una sola fuente. Respuesta GeoJSON con ``timestamps`` globales y
   ``parameters.<NOMBRE>.data`` alineado por estación.

3. **Riqueza de campos**: racha (``FFX``) con extremos por bloque de
   10 min, presión de estación (``P``) y MSL (``PRED``) ambas nativas,
   radiación global (``GLOW``) en W/m². El punto de rocío NO se toma del
   proveedor (existe ``TP``): la app lo deriva de T/HR en el pipeline.

4. **Unidades**: viento en m/s → km/h; el resto ya viene en unidades
   canónicas (°C, %, hPa, mm por 10 min, W/m²).

5. **Día local**: Europe/Vienna para toda la red (un solo huso).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from data_files import GEOSPHERE_STATIONS_PATH
from server.schemas.errors import ProviderError
from domain.parsing.common import find_station_by_field, load_stations_json, parse_epoch

logger = logging.getLogger(__name__)

PROVIDER = "GEOSPHERE"
BASE_URL = "https://dataset.api.hub.geosphere.at/v1"
DATASET_URL = f"{BASE_URL}/station/historical/tawes-v1-10min"
KLIMA_DATASET_URL = f"{BASE_URL}/station/historical/klima-v2-1d"
USER_AGENT = "MeteoLabX/1.0 (+https://meteolabx.com)"

SERIES_PARAMETERS = ("TL", "RF", "PRED", "P", "FF", "FFX", "DD", "RR", "GLOW")
TREND_PARAMETERS = ("TL", "RF", "PRED")
# Estaciones KLIMA (red convencional, dato diario): parámetros del archivo.
KLIMA_PARAMETERS = ("tl_mittel", "tlmax", "tlmin", "rr", "ffx", "rf_mittel", "p_mittel")
STATION_TZ = ZoneInfo("Europe/Vienna")


def _is_nan(value: float) -> bool:
    return value != value


def _safe_float(value: Any, default: float = float("nan")) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# =====================================================================
# Catálogo local
# =====================================================================

@lru_cache(maxsize=1)
def _load_stations() -> List[Dict[str, Any]]:
    try:
        return load_stations_json(str(GEOSPHERE_STATIONS_PATH), dict_key="stations")
    except Exception as exc:
        logger.warning("Catálogo GeoSphere no disponible (%s)", exc)
        return []


def _station_row(station_id: str) -> Dict[str, Any]:
    return find_station_by_field(_load_stations(), field="id", target=station_id)


def _station_meta(station_id: str) -> Tuple[float, float, float, str]:
    """→ (lat, lon, elevation, nombre)."""
    station = _station_row(station_id)
    return (
        _safe_float(station.get("lat")),
        _safe_float(station.get("lon")),
        _safe_float(station.get("elev")),
        str(station.get("name", "") or "").strip(),
    )


def _is_klima_station(station_id: str, row: Dict[str, Any]) -> bool:
    """Red KLIMA (convencional, dato diario): id con prefijo K o catálogo."""
    return str(row.get("network") or "").upper() == "KLIMA" or (
        station_id[:1].upper() == "K" and station_id[1:].isdigit()
    )


def _klima_api_id(station_id: str, row: Dict[str, Any]) -> str:
    return str(row.get("klima_station_id") or station_id.lstrip("Kk")).strip()


# =====================================================================
# HTTP + parsing
# =====================================================================

async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    timeout_s: float,
) -> Any:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        response = await client.get(url, params=params or {}, headers=headers, timeout=timeout_s)
    except httpx.TimeoutException as exc:
        raise ProviderError(
            "provider_timeout",
            provider=PROVIDER,
            detail=f"GeoSphere timeout: {exc}",
            status_code=504,
        ) from exc
    except httpx.RequestError as exc:
        raise ProviderError(
            "provider_network_error",
            provider=PROVIDER,
            detail=str(exc) or "Network error",
            status_code=502,
        ) from exc

    status = response.status_code
    if status == 404:
        raise ProviderError(
            "station_not_found",
            provider=PROVIDER,
            detail="Station not found (HTTP 404)",
            status_code=404,
        )
    if status == 429:
        raise ProviderError(
            "provider_ratelimit",
            provider=PROVIDER,
            detail="GeoSphere rate limit (HTTP 429)",
            status_code=429,
        )
    if status >= 400:
        raise ProviderError(
            "provider_http_error",
            provider=PROVIDER,
            detail=f"HTTP {status}",
            status_code=502,
        )

    try:
        return response.json()
    except ValueError as exc:
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail=f"JSON inválido: {exc!r}",
            status_code=502,
        ) from exc


def _to_iso_minute(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")


def _parse_payload(payload: Any, station_id: str) -> Dict[str, List[float]]:
    """Respuesta del Data Hub → columnas alineadas con ``epochs``."""
    if not isinstance(payload, dict):
        return {}
    timestamps = payload.get("timestamps")
    features = payload.get("features")
    if not isinstance(timestamps, list) or not isinstance(features, list) or not features:
        return {}
    properties = features[0].get("properties") if isinstance(features[0], dict) else None
    parameters = properties.get("parameters") if isinstance(properties, dict) else None
    if not isinstance(parameters, dict):
        return {}

    epochs = [parse_epoch(ts) for ts in timestamps]

    def _column(name: str) -> List[float]:
        block = parameters.get(name)
        values = block.get("data") if isinstance(block, dict) else None
        if not isinstance(values, list):
            return [float("nan")] * len(epochs)
        column = [_safe_float(v) for v in values]
        column += [float("nan")] * (len(epochs) - len(column))
        return column

    columns = {name: _column(name) for name in SERIES_PARAMETERS}
    # Filtra timestamps no parseables manteniendo la alineación.
    keep = [idx for idx, epoch in enumerate(epochs) if epoch is not None]
    return {
        "epochs": [int(epochs[idx]) for idx in keep],
        **{name: [columns[name][idx] for idx in keep] for name in columns},
    }


async def _fetch_series(
    station_id: str,
    client: httpx.AsyncClient,
    *,
    start: datetime,
    end: datetime,
    parameters: Tuple[str, ...],
    timeout_s: float,
) -> Dict[str, List[float]]:
    payload = await _get_json(
        client,
        DATASET_URL,
        {
            "parameters": ",".join(parameters),
            "station_ids": quote(str(station_id)),
            "start": _to_iso_minute(start),
            "end": _to_iso_minute(end),
        },
        timeout_s=timeout_s,
    )
    return _parse_payload(payload, station_id)


def _kmh(value: float) -> float:
    return value * 3.6 if not _is_nan(value) else float("nan")


# =====================================================================
# Rama KLIMA: estaciones convencionales con dato diario
# =====================================================================

async def _fetch_klima_days(
    api_id: str,
    client: httpx.AsyncClient,
    *,
    start: datetime,
    end: datetime,
    timeout_s: float,
) -> Dict[str, List[float]]:
    payload = await _get_json(
        client,
        KLIMA_DATASET_URL,
        {
            "parameters": ",".join(KLIMA_PARAMETERS),
            "station_ids": api_id,
            "start": start.astimezone(timezone.utc).strftime("%Y-%m-%d"),
            "end": end.astimezone(timezone.utc).strftime("%Y-%m-%d"),
        },
        timeout_s=timeout_s,
    )
    if not isinstance(payload, dict):
        return {}
    timestamps = payload.get("timestamps")
    features = payload.get("features")
    if not isinstance(timestamps, list) or not isinstance(features, list) or not features:
        return {}
    properties = features[0].get("properties") if isinstance(features[0], dict) else None
    parameters = properties.get("parameters") if isinstance(properties, dict) else None
    if not isinstance(parameters, dict):
        return {}
    epochs = [parse_epoch(ts) for ts in timestamps]
    keep = [idx for idx, epoch in enumerate(epochs) if epoch is not None]

    def _column(name: str) -> List[float]:
        block = parameters.get(name)
        values = block.get("data") if isinstance(block, dict) else None
        if not isinstance(values, list):
            return [float("nan")] * len(keep)
        values = values + [None] * (len(epochs) - len(values))
        return [_safe_float(values[idx]) for idx in keep]

    return {
        "epochs": [int(epochs[idx]) for idx in keep],
        **{name: _column(name) for name in KLIMA_PARAMETERS},
    }


def _klima_rain(value: float) -> float:
    # El archivo usa -1.0 como "sin precipitación medible" (traza).
    if _is_nan(value):
        return float("nan")
    return max(0.0, float(value))


async def _fetch_klima_current(
    station_id: str,
    row: Dict[str, Any],
    client: httpx.AsyncClient,
    *,
    timeout_s: float,
    now: Optional[datetime],
) -> Dict[str, Any]:
    """Observación de una estación manual: el último día publicado."""
    lat0 = _safe_float(row.get("lat"))
    lon0 = _safe_float(row.get("lon"))
    elevation = _safe_float(row.get("elev"))
    name = str(row.get("name", "") or "").strip()
    now_local = (now or datetime.now(tz=STATION_TZ)).astimezone(STATION_TZ)

    series = await _fetch_klima_days(
        _klima_api_id(station_id, row), client,
        start=now_local - timedelta(days=6), end=now_local, timeout_s=timeout_s,
    )
    epochs = series.get("epochs") or []
    day_idx = None
    for idx in range(len(epochs) - 1, -1, -1):
        if any(
            not _is_nan(_safe_float(series[name_][idx]))
            for name_ in ("tl_mittel", "tlmax", "tlmin", "rr")
        ):
            day_idx = idx
            break
    if day_idx is None:
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail=f"GeoSphere klima sin días publicados para {station_id}",
            status_code=502,
        )

    def _at(name_: str) -> float:
        return _safe_float(series[name_][day_idx])

    p_abs = _at("p_mittel")
    p_msl = float("nan")
    if not _is_nan(p_abs) and not _is_nan(elevation):
        import math as _math

        p_msl = p_abs * _math.exp(float(elevation) / 8000.0)

    epoch = int(epochs[day_idx])
    dt_utc = datetime.fromtimestamp(epoch, tz=timezone.utc)
    observation: Dict[str, Any] = {
        "Tc": _at("tl_mittel"),
        "RH": _at("rf_mittel"),
        "p_hpa": p_msl,
        "p_abs_hpa": p_abs,
        "wind": float("nan"),
        "gust": _kmh(_at("ffx")),
        "wind_dir_deg": float("nan"),
        "Td": float("nan"),
        "feels_like": float("nan"),
        "heat_index": float("nan"),
        "wind_chill": float("nan"),
        "precip_rate": float("nan"),
        "precip_total": _klima_rain(_at("rr")),
        "solar_radiation": float("nan"),
        "uv": float("nan"),
        "epoch": epoch,
        "time_local": dt_utc.astimezone(STATION_TZ).isoformat(),
        "time_utc": dt_utc.isoformat(),
        "lat": lat0,
        "lon": lon0,
        "elevation": elevation if not _is_nan(elevation) else 0.0,
        "station_name": name or station_id,
    }
    from domain.observation_pipeline import add_basic_derived
    return add_basic_derived(observation)


async def _fetch_klima_recent_series(
    station_id: str,
    row: Dict[str, Any],
    client: httpx.AsyncClient,
    *,
    days_back: int,
    timeout_s: float,
    now: Optional[datetime],
) -> Dict[str, Any]:
    """Tendencias de una estación manual: un punto por día (media diaria)."""
    import math as _math

    lat0 = _safe_float(row.get("lat"))
    lon0 = _safe_float(row.get("lon"))
    elevation = _safe_float(row.get("elev"))
    now_local = (now or datetime.now(tz=STATION_TZ)).astimezone(STATION_TZ)
    series = await _fetch_klima_days(
        _klima_api_id(station_id, row), client,
        start=now_local - timedelta(days=max(1, int(days_back))),
        end=now_local, timeout_s=timeout_s,
    )
    epochs = series.get("epochs") or []
    keep = [
        idx for idx in range(len(epochs))
        if not _is_nan(_safe_float(series["tl_mittel"][idx]))
        or not _is_nan(_safe_float(series["rf_mittel"][idx]))
    ]
    if not keep:
        return {
            "epochs": [], "temps": [], "humidities": [], "pressures": [],
            "lat": lat0, "lon": lon0, "has_data": False,
        }
    factor = _math.exp(float(elevation) / 8000.0) if not _is_nan(elevation) else 1.0
    return {
        "epochs": [int(epochs[idx]) for idx in keep],
        "temps": [_safe_float(series["tl_mittel"][idx]) for idx in keep],
        "humidities": [_safe_float(series["rf_mittel"][idx]) for idx in keep],
        "pressures": [
            (_safe_float(series["p_mittel"][idx]) * factor)
            if not _is_nan(_safe_float(series["p_mittel"][idx])) else float("nan")
            for idx in keep
        ],
        "lat": lat0,
        "lon": lon0,
        "has_data": True,
    }


# =====================================================================
# API pública del servicio
# =====================================================================

async def fetch_current(
    station_id: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 16.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Observación actual = última muestra de 10 min del día local, con
    fallback campo a campo hacia atrás. Devuelve el dict canónico.
    """
    station_id = str(station_id).strip()
    row = _station_row(station_id)
    now_local = (now or datetime.now(tz=STATION_TZ)).astimezone(STATION_TZ)
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        if _is_klima_station(station_id, row):
            return await _fetch_klima_current(
                station_id, row, client, timeout_s=timeout_s, now=now,
            )
        lat0, lon0, elevation, name = _station_meta(station_id)
        series = await _fetch_series(
            station_id, client,
            start=day_start, end=now_local,
            parameters=SERIES_PARAMETERS, timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()

    epochs = series.get("epochs") or []
    if not epochs:
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail=f"GeoSphere sin observaciones para {station_id}",
            status_code=502,
        )

    def _last(name: str) -> Tuple[float, Optional[int]]:
        values = series.get(name, [])
        for idx in range(len(values) - 1, -1, -1):
            value = _safe_float(values[idx])
            if not _is_nan(value):
                return value, int(epochs[idx])
        return float("nan"), None

    temp, temp_epoch = _last("TL")
    epoch = temp_epoch or int(epochs[-1])
    dt_utc = datetime.fromtimestamp(epoch, tz=timezone.utc)

    precip_vals = [v for v in series.get("RR", []) if not _is_nan(_safe_float(v))]

    observation: Dict[str, Any] = {
        "Tc": temp,
        "RH": _last("RF")[0],
        "p_hpa": _last("PRED")[0],
        "p_abs_hpa": _last("P")[0],
        "wind": _kmh(_last("FF")[0]),
        "gust": _kmh(_last("FFX")[0]),
        "wind_dir_deg": _last("DD")[0],
        # GeoSphere trae TP (rocío medido), pero el criterio de la app es
        # derivar Td de T/HR en el pipeline, uniforme entre proveedores.
        "Td": float("nan"),
        "feels_like": float("nan"),
        "heat_index": float("nan"),
        "wind_chill": float("nan"),
        "precip_rate": float("nan"),
        "precip_total": float(sum(max(0.0, v) for v in precip_vals)) if precip_vals else float("nan"),
        "solar_radiation": _last("GLOW")[0],
        "uv": float("nan"),
        "epoch": epoch,
        "time_local": dt_utc.astimezone(STATION_TZ).isoformat(),
        "time_utc": dt_utc.isoformat(),
        "lat": lat0,
        "lon": lon0,
        "elevation": elevation if not _is_nan(elevation) else 0.0,
        "station_name": name or station_id,
    }

    from domain.observation_pipeline import add_basic_derived
    return add_basic_derived(observation)


def _empty_today_series() -> Dict[str, Any]:
    return {
        "epochs": [],
        "temps": [],
        "humidities": [],
        "dewpts": [],
        "pressures": [],
        "uv_indexes": [],
        "solar_radiations": [],
        "winds": [],
        "gusts": [],
        "wind_dirs": [],
        "lat": float("nan"),
        "lon": float("nan"),
        "has_data": False,
    }


async def fetch_today_series(
    station_id: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 16.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Serie del día local (Europe/Vienna) en shape canónico. Las
    estaciones KLIMA (dato diario) no tienen serie intradía."""
    station_id = str(station_id).strip()
    row = _station_row(station_id)
    if _is_klima_station(station_id, row):
        return _empty_today_series()
    lat0, lon0, _elevation, _name = _station_meta(station_id)
    now_local = (now or datetime.now(tz=STATION_TZ)).astimezone(STATION_TZ)
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        series = await _fetch_series(
            station_id, client,
            start=day_start, end=now_local,
            parameters=SERIES_PARAMETERS, timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()

    epochs = series.get("epochs") or []
    # Descarta la cola de bloques aún sin publicar (todo NaN al final).
    rows = [
        idx for idx, _ in enumerate(epochs)
        if any(
            not _is_nan(_safe_float(series[name][idx]))
            for name in ("TL", "RF", "PRED", "FF", "RR", "GLOW")
        )
    ]
    if not rows:
        return _empty_today_series()

    def _col(name: str, convert=None) -> List[float]:
        values = series.get(name, [])
        out = [_safe_float(values[idx]) for idx in rows]
        return [convert(v) if convert else v for v in out]

    return {
        "epochs": [int(epochs[idx]) for idx in rows],
        "temps": _col("TL"),
        "humidities": _col("RF"),
        "dewpts": [float("nan")] * len(rows),
        "pressures": _col("PRED"),
        "uv_indexes": [float("nan")] * len(rows),
        "solar_radiations": _col("GLOW"),
        "winds": _col("FF", convert=_kmh),
        "gusts": _col("FFX", convert=_kmh),
        "wind_dirs": _col("DD"),
        "lat": lat0,
        "lon": lon0,
        "has_data": True,
    }


async def fetch_recent_series(
    station_id: str,
    *,
    days_back: int = 7,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 20.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Serie reciente (T/HR/presión MSL) para tendencias, binned a 1 h
    (última muestra de 10 min de cada hora). Las estaciones KLIMA
    devuelven un punto por día."""
    station_id = str(station_id).strip()
    row = _station_row(station_id)
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    if _is_klima_station(station_id, row):
        try:
            return await _fetch_klima_recent_series(
                station_id, row, client,
                days_back=days_back, timeout_s=timeout_s, now=now,
            )
        finally:
            if owns_client:
                await client.aclose()
    lat0, lon0, _elevation, _name = _station_meta(station_id)
    now_utc = (now or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)
    start = now_utc - timedelta(days=max(1, int(days_back)))

    try:
        series = await _fetch_series(
            station_id, client,
            start=start, end=now_utc,
            parameters=TREND_PARAMETERS, timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()

    epochs = series.get("epochs") or []
    if not epochs:
        return {
            "epochs": [], "temps": [], "humidities": [], "pressures": [],
            "lat": lat0, "lon": lon0, "has_data": False,
        }

    by_hour: Dict[int, int] = {}
    for idx, epoch in enumerate(epochs):
        if _is_nan(_safe_float(series["TL"][idx])) and _is_nan(_safe_float(series["RF"][idx])):
            continue
        bucket = (int(epoch) // 3600) * 3600
        by_hour[bucket] = idx

    buckets = sorted(by_hour)
    if not buckets:
        return {
            "epochs": [], "temps": [], "humidities": [], "pressures": [],
            "lat": lat0, "lon": lon0, "has_data": False,
        }

    def _col(name: str) -> List[float]:
        values = series.get(name, [])
        return [_safe_float(values[by_hour[bucket]]) for bucket in buckets]

    return {
        "epochs": buckets,
        "temps": _col("TL"),
        "humidities": _col("RF"),
        "pressures": _col("PRED"),
        "lat": lat0,
        "lon": lon0,
        "has_data": True,
    }
