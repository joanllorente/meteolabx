"""
Servicio puro de MeteoHub (Agenzia ItaliaMeteo).

Versión "limpia" del cliente legacy (``services/meteohub.py``): sin
``streamlit``, cliente ``httpx.AsyncClient``.

Particularidades:

1. **API pública** (sin credenciales): ``/api/observations`` con un
   query DSL (productos BUFR + niveles + timeranges + licencia). Una
   sola petición devuelve estación + todas las series del día.

2. **station_id codificado**: ``network|lat|lon|nombre`` en minúsculas
   (catálogo local ``data_estaciones_meteohub_it.json``). El schema de
   la API lo normaliza a mayúsculas, así que aquí se re-minusculiza.

3. **Unidades BUFR**: temperatura en Kelvin (B12101), presión en Pa
   (B10004, con saneado de valores no barométricos), viento en m/s
   (B11002). Elevación desde los detalles de estación (B07030/B07031).

4. **Día local**: Europe/Rome.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

from data_files import METEOHUB_IT_STATIONS_PATH
from server.schemas.errors import ProviderError
from domain.parsing.common import load_stations_json, parse_epoch

logger = logging.getLogger(__name__)

PROVIDER = "METEOHUB_IT"
BASE_URL = "https://meteohub.agenziaitaliameteo.it"
LICENSE_GROUP = "CCBY_COMPLIANT"
STATION_TZ = ZoneInfo("Europe/Rome")

P_TEMP = "B12101"
P_WIND_SPEED = "B11002"
P_WIND_DIR = "B11001"
P_PRESSURE = "B10004"
P_RH = "B13003"
P_PRECIP = "B13011"

QUERY_PRODUCTS = (P_TEMP, P_RH, P_PRESSURE, P_WIND_SPEED, P_WIND_DIR, P_PRECIP)
QUERY_LEVELS = ("103,2000,0,0", "103,6000,0,0", "103,10000,0,0", "1,0,0,0")
QUERY_TIMERANGES = ("254,0,0", "1,0,3600")


# =====================================================================
# Helpers numéricos (clonados del legacy)
# =====================================================================

def _is_nan(value: float) -> bool:
    return value != value


def _safe_float(value: Any, default: float = float("nan")) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _kelvin_to_c(value: Any) -> float:
    raw = _safe_float(value)
    if _is_nan(raw):
        return float("nan")
    return raw - 273.15 if raw > 170.0 else raw


def _pa_to_hpa(value: Any) -> float:
    raw = _safe_float(value)
    if _is_nan(raw):
        return float("nan")
    hpa = raw / 100.0 if abs(raw) > 2000.0 else raw
    # B10004 puede aparecer en estaciones hidrológicas; fuera de rango
    # barométrico no se usa.
    if hpa < 300.0 or hpa > 1100.0:
        return float("nan")
    return hpa


def _ms_to_kmh(value: Any) -> float:
    raw = _safe_float(value)
    return raw * 3.6 if not _is_nan(raw) else float("nan")


def _non_negative(value: Any) -> float:
    raw = _safe_float(value)
    if _is_nan(raw):
        return float("nan")
    return max(0.0, raw)


# =====================================================================
# Catálogo / id codificado
# =====================================================================

@lru_cache(maxsize=1)
def _load_stations() -> List[Dict[str, Any]]:
    try:
        return load_stations_json(str(METEOHUB_IT_STATIONS_PATH))
    except Exception as exc:
        logger.warning("Catálogo MeteoHub no disponible (%s)", exc)
        return []


def _station_from_encoded_id(station_id: str) -> Dict[str, Any]:
    parts = str(station_id or "").split("|")
    if len(parts) < 4:
        return {}
    try:
        lat = float(parts[1])
        lon = float(parts[2])
    except Exception:
        return {}
    return {
        "id": station_id,
        "network": parts[0],
        "lat": lat,
        "lon": lon,
        "name": parts[3].replace("-", " ").strip() or station_id,
    }


def _resolve_station(station_id: str) -> Dict[str, Any]:
    target = str(station_id or "").strip().lower()
    for station in _load_stations():
        if not isinstance(station, dict):
            continue
        if str(station.get("id") or station.get("source_id") or "").strip().lower() == target:
            return station
    return _station_from_encoded_id(target)


# =====================================================================
# Parsing del payload (clonado del legacy)
# =====================================================================

def _first_station_block(payload: Any) -> Dict[str, Any]:
    data = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return {}


def _station_detail_value(details: Any, code: str) -> Any:
    if not isinstance(details, list):
        return None
    for item in details:
        if isinstance(item, dict) and str(item.get("var") or "").strip() == code:
            return item.get("val")
    return None


def _station_details(station_block: Dict[str, Any]) -> List[Dict[str, Any]]:
    stat = station_block.get("stat") if isinstance(station_block, dict) else {}
    details = stat.get("details") if isinstance(stat, dict) else []
    return details if isinstance(details, list) else []


def _product_rank(product: Dict[str, Any]) -> int:
    level = str(product.get("lev") or "")
    return {"103,10000,0,0": 0, "103,6000,0,0": 1, "103,2000,0,0": 2}.get(level, 3)


def _products_by_code(station_block: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    products = station_block.get("prod") if isinstance(station_block, dict) else []
    by_code: Dict[str, List[Dict[str, Any]]] = {}
    for product in products if isinstance(products, list) else []:
        if not isinstance(product, dict):
            continue
        code = str(product.get("var") or "").strip()
        if code:
            by_code.setdefault(code, []).append(product)
    for items in by_code.values():
        items.sort(key=_product_rank)
    return by_code


def _series_for_code(by_code: Dict[str, List[Dict[str, Any]]], code: str) -> List[Tuple[int, float]]:
    rows: Dict[int, float] = {}
    for product in by_code.get(code, []):
        values = product.get("val") if isinstance(product, dict) else []
        for item in values if isinstance(values, list) else []:
            if not isinstance(item, dict):
                continue
            epoch = parse_epoch(item.get("ref"))
            if epoch is None:
                continue
            rows[int(epoch)] = _safe_float(item.get("val"))
    return sorted(rows.items())


def _align_series(by_code: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    rows: Dict[int, Dict[str, float]] = {}

    def add(code: str, key: str, converter) -> None:
        for epoch, value in _series_for_code(by_code, code):
            rows.setdefault(int(epoch), {})[key] = converter(value)

    add(P_TEMP, "temp", _kelvin_to_c)
    add(P_RH, "rh", _safe_float)
    add(P_PRESSURE, "p_abs", _pa_to_hpa)
    add(P_WIND_SPEED, "wind", _ms_to_kmh)
    add(P_WIND_DIR, "dir", _safe_float)
    add(P_PRECIP, "precip", _non_negative)

    epochs = sorted(rows.keys())
    return {
        "epochs": epochs,
        "temps": [rows[ep].get("temp", float("nan")) for ep in epochs],
        "humidities": [rows[ep].get("rh", float("nan")) for ep in epochs],
        "pressures_abs": [rows[ep].get("p_abs", float("nan")) for ep in epochs],
        "winds": [rows[ep].get("wind", float("nan")) for ep in epochs],
        "wind_dirs": [rows[ep].get("dir", float("nan")) for ep in epochs],
        "precips": [rows[ep].get("precip", float("nan")) for ep in epochs],
        "has_data": len(epochs) > 0,
    }


# =====================================================================
# HTTP
# =====================================================================

def _build_query(start_utc: datetime, end_utc: datetime) -> str:
    products = " or ".join(QUERY_PRODUCTS)
    levels = " or ".join(QUERY_LEVELS)
    timeranges = " or ".join(QUERY_TIMERANGES)
    start_text = start_utc.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
    end_text = end_utc.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return (
        f"reftime: >={start_text},<={end_text};"
        f"timerange:{timeranges};"
        f"level:{levels};"
        f"license:{LICENSE_GROUP};"
        f"product:{products}"
    )


async def _fetch_observations(
    network: str,
    lat: float,
    lon: float,
    client: httpx.AsyncClient,
    *,
    timeout_s: float,
    now: Optional[datetime] = None,
    days_back: int = 0,
) -> Dict[str, Any]:
    now_local = (now or datetime.now(tz=STATION_TZ)).astimezone(STATION_TZ)
    start_local = (now_local - timedelta(days=max(0, int(days_back)))).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    params = {
        "q": _build_query(start_local, now_local),
        "networks": network,
        "lat": float(lat),
        "lon": float(lon),
        "stationDetails": "true",
        "allStationProducts": "false",
    }
    headers = {
        "Accept": "application/json",
        "User-Agent": "MeteoLabX/1.0 (+https://meteolabx.com)",
    }
    try:
        response = await client.get(
            f"{BASE_URL}/api/observations", params=params, headers=headers, timeout=timeout_s,
        )
    except httpx.TimeoutException as exc:
        raise ProviderError(
            "provider_timeout",
            provider=PROVIDER,
            detail=f"MeteoHub timeout: {exc}",
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
            detail="MeteoHub rate limit (HTTP 429)",
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


async def _fetch_station_payload(
    station_id: str,
    client: httpx.AsyncClient,
    *,
    timeout_s: float,
    now: Optional[datetime] = None,
    days_back: int = 0,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """→ (station_block, station_meta). Resuelve red+coords del catálogo."""
    station_id = str(station_id).strip().lower()
    station_meta = _resolve_station(station_id)
    network = str(station_meta.get("network") or "").strip()
    lat = _safe_float(station_meta.get("lat"))
    lon = _safe_float(station_meta.get("lon"))
    if not network or _is_nan(lat) or _is_nan(lon):
        raise ProviderError(
            "station_not_found",
            provider=PROVIDER,
            detail=f"Estación MeteoHub sin red o coordenadas: {station_id}",
            status_code=404,
        )

    payload = await _fetch_observations(
        network, lat, lon, client, timeout_s=timeout_s, now=now, days_back=days_back,
    )
    return _first_station_block(payload), station_meta


def _resolve_geometry(
    station_block: Dict[str, Any],
    station_meta: Dict[str, Any],
) -> Tuple[float, float, float, str]:
    """→ (lat, lon, elevation, nombre) combinando detalles + catálogo."""
    details = _station_details(station_block)
    name = str(
        _station_detail_value(details, "B01019")
        or station_meta.get("name")
        or station_meta.get("id")
        or ""
    ).strip()
    lat = _safe_float(_station_detail_value(details, "B05001"), default=_safe_float(station_meta.get("lat")))
    lon = _safe_float(_station_detail_value(details, "B06001"), default=_safe_float(station_meta.get("lon")))
    elevation = _safe_float(
        _station_detail_value(details, "B07030"),
        default=_safe_float(
            _station_detail_value(details, "B07031"),
            default=_safe_float(station_meta.get("elev"), default=0.0),
        ),
    )
    if _is_nan(elevation):
        elevation = 0.0
    return lat, lon, elevation, name


# =====================================================================
# API pública del servicio
# =====================================================================

async def fetch_current(
    station_id: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 18.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Observación actual = últimos valores válidos de las series del día.
    MeteoHub no expone racha/radiación/UV en estos productos.
    """
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        station_block, station_meta = await _fetch_station_payload(
            station_id, client, timeout_s=timeout_s, now=now,
        )
    finally:
        if owns_client:
            await client.aclose()

    series = _align_series(_products_by_code(station_block))
    if not series.get("has_data"):
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail=f"MeteoHub sin observaciones para {station_id}",
            status_code=502,
        )

    lat, lon, elevation, name = _resolve_geometry(station_block, station_meta)

    def _last(key: str) -> float:
        for value in reversed(series.get(key, [])):
            fv = _safe_float(value)
            if not _is_nan(fv):
                return fv
        return float("nan")

    p_abs = _last("pressures_abs")
    p_msl = p_abs * math.exp(float(elevation) / 8000.0) if not _is_nan(p_abs) else float("nan")
    precip_vals = [
        max(0.0, _safe_float(v))
        for v in series.get("precips", [])
        if not _is_nan(_safe_float(v))
    ]

    epoch = int(series["epochs"][-1])
    dt_utc = datetime.fromtimestamp(epoch, tz=timezone.utc)

    observation: Dict[str, Any] = {
        "Tc": _last("temps"),
        "RH": _last("humidities"),
        "p_hpa": p_msl,
        "p_abs_hpa": p_abs,
        "wind": _last("winds"),
        "gust": float("nan"),
        "wind_dir_deg": _last("wind_dirs"),
        "Td": float("nan"),
        "feels_like": float("nan"),
        "heat_index": float("nan"),
        "wind_chill": float("nan"),
        "precip_rate": float("nan"),
        "precip_total": float(sum(precip_vals)) if precip_vals else float("nan"),
        "solar_radiation": float("nan"),
        "uv": float("nan"),
        "epoch": epoch,
        "time_local": dt_utc.astimezone(STATION_TZ).isoformat(),
        "time_utc": dt_utc.isoformat(),
        "lat": lat,
        "lon": lon,
        "elevation": float(elevation),
        "station_name": name or str(station_id),
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
    timeout_s: float = 18.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Serie del día local (Europe/Rome) en shape canónico."""
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        station_block, station_meta = await _fetch_station_payload(
            station_id, client, timeout_s=timeout_s, now=now,
        )
    finally:
        if owns_client:
            await client.aclose()

    series = _align_series(_products_by_code(station_block))
    if not series.get("has_data"):
        return _empty_today_series()

    lat, lon, elevation, _name = _resolve_geometry(station_block, station_meta)
    n = len(series["epochs"])
    factor = math.exp(float(elevation) / 8000.0)

    return {
        "epochs": [int(ep) for ep in series["epochs"]],
        "temps": series["temps"],
        "humidities": series["humidities"],
        "dewpts": [float("nan")] * n,
        "pressures": [
            (float(p) * factor) if not _is_nan(_safe_float(p)) else float("nan")
            for p in series["pressures_abs"]
        ],
        "uv_indexes": [float("nan")] * n,
        "solar_radiations": [float("nan")] * n,
        "winds": series["winds"],
        "gusts": [float("nan")] * n,
        "wind_dirs": series["wind_dirs"],
        "lat": lat,
        "lon": lon,
        "has_data": True,
    }


async def fetch_recent_series(
    station_id: str,
    *,
    days_back: int = 7,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 18.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Serie reciente (T/HR/presión MSL) para tendencias: misma query que
    el día pero con la ventana ampliada hacia atrás, binned a 1 h.
    """
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        station_block, station_meta = await _fetch_station_payload(
            station_id, client, timeout_s=timeout_s, now=now,
            days_back=max(1, int(days_back)),
        )
    finally:
        if owns_client:
            await client.aclose()

    series = _align_series(_products_by_code(station_block))
    lat, lon, elevation, _name = _resolve_geometry(station_block, station_meta)
    if not series.get("has_data"):
        return {
            "epochs": [], "temps": [], "humidities": [], "pressures": [],
            "lat": lat, "lon": lon, "has_data": False,
        }

    factor = math.exp(float(elevation) / 8000.0)
    # Bin horario: última lectura de cada hora.
    by_hour: Dict[int, int] = {}
    epochs_all = series["epochs"]
    for idx, epoch in enumerate(epochs_all):
        bucket = (int(epoch) // 3600) * 3600
        by_hour[bucket] = idx

    buckets = sorted(by_hour)
    def _col(key: str, convert=None) -> List[float]:
        values = series.get(key, [])
        out = []
        for bucket in buckets:
            idx = by_hour[bucket]
            v = _safe_float(values[idx]) if idx < len(values) else float("nan")
            out.append(convert(v) if convert else v)
        return out

    return {
        "epochs": buckets,
        "temps": _col("temps"),
        "humidities": _col("humidities"),
        "pressures": _col(
            "pressures_abs",
            convert=lambda p: (p * factor) if not _is_nan(p) else float("nan"),
        ),
        "lat": lat,
        "lon": lon,
        "has_data": True,
    }
