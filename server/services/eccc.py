"""
Servicio puro de ECCC MSC GeoMet (api.weather.gc.ca, Canadá).

Particularidades:

1. **OGC API Features sin credenciales** (colecciones ``swob-realtime``,
   ``climate-daily``). Los queryables llevan sufijo ``-value`` para los
   campos de identidad (``msc_id-value``).

2. **SWOB en tiempo real**: observaciones minutales u horarias de las
   redes MSC y asociadas, con retención de ~30 días. Una consulta por
   estación con ``properties=`` recorta el payload (las obs traen 200+
   campos). El viento YA viene en km/h; presión de estación (``stn_pres``,
   la MSL se deriva con la altitud del catálogo); racha =
   ``max_wnd_spd_10m_pst1hr``.

3. **Estaciones CLIMATE** (``network: "CLIMATE"``, manuales): red
   climatológica con dato DIARIO vía ``climate-daily`` (1-2 días de
   decalaje). Su "observación" es el último día publicado.

4. **Husos**: Canadá cruza 6 zonas; el día local usa la tz por estación
   del catálogo (por provincia).

5. **Series minutales**: la serie del día se muestrea a 1 punto/10 min
   y la precipitación diaria suma ``pcpn_amt_pst1hr`` en los cortes de
   hora exactos (sumar cada minuto duplicaría el acumulado).
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

from data_files import ECCC_STATIONS_PATH
from server.schemas.errors import ProviderError
from domain.parsing.common import find_station_by_field, load_stations_json, parse_epoch

logger = logging.getLogger(__name__)

PROVIDER = "ECCC"
BASE_URL = "https://api.weather.gc.ca"
SWOB_URL = f"{BASE_URL}/collections/swob-realtime/items"
CLIMATE_DAILY_URL = f"{BASE_URL}/collections/climate-daily/items"
USER_AGENT = "MeteoLabX/1.0 (+https://meteolabx.com)"

SWOB_PROPERTIES = ",".join((
    "obs_date_tm", "msc_id-value", "air_temp", "rel_hum", "stn_pres",
    "avg_wnd_spd_10m_pst10mts", "avg_wnd_dir_10m_pst10mts",
    "avg_wnd_spd_10m_pst2mts", "avg_wnd_dir_10m_pst2mts",
    "avg_wnd_spd_10m_pst1hr", "avg_wnd_dir_10m_pst1hr",
    "max_wnd_spd_10m_pst1hr", "pcpn_amt_pst1hr",
    # Extremos horarios EXPLÍCITOS (los publica ~95% de la red): el
    # máx/mín diario sale de ellos, exacto, no de las instantáneas.
    "max_air_temp_pst1hr", "min_air_temp_pst1hr",
))
TREND_PROPERTIES = "obs_date_tm,air_temp,rel_hum,stn_pres"


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
        return load_stations_json(str(ECCC_STATIONS_PATH), dict_key="stations")
    except Exception as exc:
        logger.warning("Catálogo ECCC no disponible (%s)", exc)
        return []


def _station_row(station_id: str) -> Dict[str, Any]:
    return find_station_by_field(_load_stations(), field="id", target=station_id)


def _is_climate_station(row: Dict[str, Any]) -> bool:
    return str(row.get("network") or "").upper() == "CLIMATE"


def _station_tz(row: Dict[str, Any]) -> timezone | ZoneInfo:
    try:
        return ZoneInfo(str(row.get("tz") or "America/Toronto"))
    except Exception:
        return timezone.utc


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
            detail=f"ECCC timeout: {exc}",
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
    if status == 429:
        raise ProviderError(
            "provider_ratelimit",
            provider=PROVIDER,
            detail="ECCC rate limit (HTTP 429)",
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


def _swob_rows(payload: Any) -> List[Dict[str, Any]]:
    """Features SWOB → filas {epoch, campos} ordenadas por epoch asc."""
    rows: Dict[int, Dict[str, Any]] = {}
    for feature in (payload.get("features") or []) if isinstance(payload, dict) else []:
        props = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(props, dict):
            continue
        epoch = parse_epoch(props.get("obs_date_tm"))
        if epoch is None:
            continue
        rows[int(epoch)] = props
    return [{"epoch": ep, **rows[ep]} for ep in sorted(rows)]


def _first_field(row: Dict[str, Any], *names: str) -> float:
    for name in names:
        value = _safe_float(row.get(name))
        if not _is_nan(value):
            return value
    return float("nan")


def _wind_speed(row: Dict[str, Any]) -> float:
    return _first_field(
        row, "avg_wnd_spd_10m_pst10mts", "avg_wnd_spd_10m_pst2mts",
        "avg_wnd_spd_10m_pst1hr",
    )


def _wind_dir(row: Dict[str, Any]) -> float:
    return _first_field(
        row, "avg_wnd_dir_10m_pst10mts", "avg_wnd_dir_10m_pst2mts",
        "avg_wnd_dir_10m_pst1hr",
    )


def _msl_from_station(p_abs: float, elevation_m: float) -> float:
    if _is_nan(p_abs) or _is_nan(elevation_m):
        return float("nan")
    return float(p_abs) * math.exp(float(elevation_m) / 8000.0)


async def _fetch_swob_window(
    station_id: str,
    client: httpx.AsyncClient,
    *,
    start: datetime,
    end: datetime,
    properties: str,
    limit: int,
    timeout_s: float,
) -> List[Dict[str, Any]]:
    payload = await _get_json(
        client,
        SWOB_URL,
        {
            "f": "json",
            "msc_id-value": str(station_id),
            "datetime": (
                start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                + "/"
                + end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            ),
            "limit": int(limit),
            "properties": properties,
        },
        timeout_s=timeout_s,
    )
    return _swob_rows(payload)


# =====================================================================
# Rama CLIMATE: red climatológica con dato diario
# =====================================================================

async def _fetch_climate_days(
    climate_id: str,
    client: httpx.AsyncClient,
    *,
    limit: int,
    timeout_s: float,
) -> List[Dict[str, Any]]:
    payload = await _get_json(
        client,
        CLIMATE_DAILY_URL,
        {
            "f": "json",
            "CLIMATE_IDENTIFIER": str(climate_id),
            "sortby": "-LOCAL_DATE",
            "limit": int(limit),
        },
        timeout_s=timeout_s,
    )
    rows = []
    for feature in (payload.get("features") or []) if isinstance(payload, dict) else []:
        props = feature.get("properties") if isinstance(feature, dict) else None
        if isinstance(props, dict):
            rows.append(props)
    rows.sort(key=lambda p: str(p.get("LOCAL_DATE") or ""))
    return rows


async def _fetch_climate_current(
    station_id: str,
    row: Dict[str, Any],
    client: httpx.AsyncClient,
    *,
    timeout_s: float,
) -> Dict[str, Any]:
    climate_id = str(row.get("climate_identifier") or station_id).strip()
    days = await _fetch_climate_days(climate_id, client, limit=10, timeout_s=timeout_s)
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    days = [
        d for d in days
        if str(d.get("LOCAL_DATE") or "")[:10] >= cutoff
        and any(
            d.get(k) is not None
            for k in ("MEAN_TEMPERATURE", "MAX_TEMPERATURE", "TOTAL_PRECIPITATION")
        )
    ]
    if not days:
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail=f"ECCC climate sin días publicados para {station_id}",
            status_code=502,
        )
    latest = days[-1]
    tz = _station_tz(row)
    epoch = parse_epoch(str(latest.get("LOCAL_DATE") or "")[:10]) or int(
        datetime.now(tz=timezone.utc).timestamp()
    )
    dt_utc = datetime.fromtimestamp(epoch, tz=timezone.utc)
    rain = _safe_float(latest.get("TOTAL_PRECIPITATION"))
    observation: Dict[str, Any] = {
        "Tc": _safe_float(latest.get("MEAN_TEMPERATURE")),
        "RH": float("nan"),
        "p_hpa": float("nan"),
        "p_abs_hpa": float("nan"),
        "wind": float("nan"),
        "gust": _safe_float(latest.get("SPEED_MAX_GUST")),  # ya en km/h
        "wind_dir_deg": float("nan"),
        "Td": float("nan"),
        "feels_like": float("nan"),
        "heat_index": float("nan"),
        "wind_chill": float("nan"),
        "precip_rate": float("nan"),
        "precip_total": max(0.0, rain) if not _is_nan(rain) else float("nan"),
        "solar_radiation": float("nan"),
        "uv": float("nan"),
        "epoch": epoch,
        "time_local": dt_utc.astimezone(tz).isoformat(),
        "time_utc": dt_utc.isoformat(),
        "lat": _safe_float(row.get("lat")),
        "lon": _safe_float(row.get("lon")),
        "elevation": _safe_float(row.get("elev"), default=0.0),
        "station_name": str(row.get("name", "") or "").strip() or station_id,
    }
    from domain.observation_pipeline import add_basic_derived
    return add_basic_derived(observation)


async def _fetch_climate_recent_series(
    station_id: str,
    row: Dict[str, Any],
    client: httpx.AsyncClient,
    *,
    days_back: int,
    timeout_s: float,
) -> Dict[str, Any]:
    climate_id = str(row.get("climate_identifier") or station_id).strip()
    days = await _fetch_climate_days(
        climate_id, client, limit=max(7, int(days_back) + 3), timeout_s=timeout_s,
    )
    lat = _safe_float(row.get("lat"))
    lon = _safe_float(row.get("lon"))
    epochs, temps = [], []
    for day in days:
        temp = _safe_float(day.get("MEAN_TEMPERATURE"))
        epoch = parse_epoch(str(day.get("LOCAL_DATE") or "")[:10])
        if epoch is None or _is_nan(temp):
            continue
        epochs.append(int(epoch))
        temps.append(temp)
    if not epochs:
        return {
            "epochs": [], "temps": [], "humidities": [], "pressures": [],
            "lat": lat, "lon": lon, "has_data": False,
        }
    return {
        "epochs": epochs,
        "temps": temps,
        "humidities": [float("nan")] * len(epochs),
        "pressures": [float("nan")] * len(epochs),
        "lat": lat,
        "lon": lon,
        "has_data": True,
    }


# =====================================================================
# API pública del servicio
# =====================================================================

async def fetch_current(
    station_id: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 25.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Observación actual = última obs SWOB del día local, con fallback
    campo a campo. Devuelve el dict canónico."""
    station_id = str(station_id).strip()
    row = _station_row(station_id)
    tz = _station_tz(row)
    now_local = (now or datetime.now(tz=tz)).astimezone(tz)
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        if _is_climate_station(row):
            return await _fetch_climate_current(
                station_id, row, client, timeout_s=timeout_s,
            )
        rows = await _fetch_swob_window(
            station_id, client,
            start=day_start, end=now_local,
            properties=SWOB_PROPERTIES, limit=2000, timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()

    if not rows:
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail=f"ECCC sin observaciones para {station_id}",
            status_code=502,
        )

    elevation = _safe_float(row.get("elev"))

    def _last(getter) -> float:
        for item in reversed(rows):
            value = getter(item)
            if not _is_nan(value):
                return value
        return float("nan")

    temp = _last(lambda r: _safe_float(r.get("air_temp")))
    epoch = int(rows[-1]["epoch"])
    dt_utc = datetime.fromtimestamp(epoch, tz=timezone.utc)

    # Precipitación del día: suma de pcpn_amt_pst1hr en los cortes de hora.
    day_start_epoch = int(day_start.timestamp())
    precip_vals = [
        _safe_float(item.get("pcpn_amt_pst1hr"))
        for item in rows
        if int(item["epoch"]) % 3600 == 0 and int(item["epoch"]) >= day_start_epoch
        and not _is_nan(_safe_float(item.get("pcpn_amt_pst1hr")))
    ]

    # Extremos del día EXACTOS desde los máx/mín horarios explícitos
    # (con las instantáneas como red de seguridad).
    temp_highs = [
        v for item in rows
        if not _is_nan(v := _first_field(item, "max_air_temp_pst1hr", "air_temp"))
    ]
    temp_lows = [
        v for item in rows
        if not _is_nan(v := _first_field(item, "min_air_temp_pst1hr", "air_temp"))
    ]
    gust_highs = [
        v for item in rows
        if not _is_nan(v := _safe_float(item.get("max_wnd_spd_10m_pst1hr")))
    ]
    daily_extremes: Dict[str, float] = {}
    if temp_highs:
        daily_extremes["temp_max"] = max(temp_highs)
    if temp_lows:
        daily_extremes["temp_min"] = min(temp_lows)
    if gust_highs:
        daily_extremes["gust_max"] = max(gust_highs)

    p_abs = _last(lambda r: _safe_float(r.get("stn_pres")))
    observation: Dict[str, Any] = {
        "Tc": temp,
        "RH": _last(lambda r: _safe_float(r.get("rel_hum"))),
        "p_hpa": _msl_from_station(p_abs, elevation),
        "p_abs_hpa": p_abs,
        "wind": _last(_wind_speed),   # SWOB ya reporta km/h
        "gust": _last(lambda r: _safe_float(r.get("max_wnd_spd_10m_pst1hr"))),
        "wind_dir_deg": _last(_wind_dir),
        "Td": float("nan"),
        "feels_like": float("nan"),
        "heat_index": float("nan"),
        "wind_chill": float("nan"),
        "precip_rate": float("nan"),
        "precip_total": float(sum(max(0.0, v) for v in precip_vals)) if precip_vals else float("nan"),
        "solar_radiation": float("nan"),
        "uv": float("nan"),
        "epoch": epoch,
        "time_local": dt_utc.astimezone(tz).isoformat(),
        "time_utc": dt_utc.isoformat(),
        "lat": _safe_float(row.get("lat")),
        "lon": _safe_float(row.get("lon")),
        "elevation": elevation if not _is_nan(elevation) else 0.0,
        "station_name": str(row.get("name", "") or "").strip() or station_id,
        "daily_extremes": daily_extremes,
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
    timeout_s: float = 25.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Serie del día local en shape canónico, muestreada a 1 punto/10 min.
    Las estaciones CLIMATE (dato diario) no tienen serie intradía."""
    station_id = str(station_id).strip()
    row = _station_row(station_id)
    if _is_climate_station(row):
        return _empty_today_series()
    tz = _station_tz(row)
    now_local = (now or datetime.now(tz=tz)).astimezone(tz)
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        rows = await _fetch_swob_window(
            station_id, client,
            start=day_start, end=now_local,
            properties=SWOB_PROPERTIES, limit=2000, timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()

    if not rows:
        return _empty_today_series()

    # 1 punto/10 min: la última obs de cada bloque.
    by_slot: Dict[int, Dict[str, Any]] = {}
    for item in rows:
        by_slot[(int(item["epoch"]) // 600) * 600] = item
    slots = sorted(by_slot)
    elevation = _safe_float(row.get("elev"))

    def _col(getter) -> List[float]:
        return [getter(by_slot[slot]) for slot in slots]

    return {
        "epochs": [int(by_slot[slot]["epoch"]) for slot in slots],
        "temps": _col(lambda r: _safe_float(r.get("air_temp"))),
        "humidities": _col(lambda r: _safe_float(r.get("rel_hum"))),
        "dewpts": [float("nan")] * len(slots),
        "pressures": _col(lambda r: _msl_from_station(_safe_float(r.get("stn_pres")), elevation)),
        "uv_indexes": [float("nan")] * len(slots),
        "solar_radiations": [float("nan")] * len(slots),
        "winds": _col(_wind_speed),
        "gusts": _col(lambda r: _safe_float(r.get("max_wnd_spd_10m_pst1hr"))),
        "wind_dirs": _col(_wind_dir),
        "lat": _safe_float(row.get("lat")),
        "lon": _safe_float(row.get("lon")),
        "has_data": True,
    }


async def fetch_recent_series(
    station_id: str,
    *,
    days_back: int = 7,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 40.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Serie reciente (T/HR/presión MSL) para tendencias, binned a 1 h.
    Las estaciones CLIMATE devuelven un punto por día."""
    station_id = str(station_id).strip()
    row = _station_row(station_id)
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        if _is_climate_station(row):
            return await _fetch_climate_recent_series(
                station_id, row, client, days_back=days_back, timeout_s=timeout_s,
            )
        now_utc = (now or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)
        rows = await _fetch_swob_window(
            station_id, client,
            start=now_utc - timedelta(days=max(1, int(days_back))), end=now_utc,
            properties=TREND_PROPERTIES, limit=10000, timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()

    lat = _safe_float(row.get("lat"))
    lon = _safe_float(row.get("lon"))
    if not rows:
        return {
            "epochs": [], "temps": [], "humidities": [], "pressures": [],
            "lat": lat, "lon": lon, "has_data": False,
        }
    elevation = _safe_float(row.get("elev"))
    by_hour: Dict[int, Dict[str, Any]] = {}
    for item in rows:
        by_hour[(int(item["epoch"]) // 3600) * 3600] = item
    buckets = sorted(by_hour)
    return {
        "epochs": buckets,
        "temps": [_safe_float(by_hour[b].get("air_temp")) for b in buckets],
        "humidities": [_safe_float(by_hour[b].get("rel_hum")) for b in buckets],
        "pressures": [
            _msl_from_station(_safe_float(by_hour[b].get("stn_pres")), elevation)
            for b in buckets
        ],
        "lat": lat,
        "lon": lon,
        "has_data": True,
    }
