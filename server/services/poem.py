"""
Servicio puro de POEM (Puertos del Estado).

Reutiliza las heurísticas puras de ``domain/parsing/poem.py``; aquí solo
vive el I/O httpx y el ensamblado canónico.

Particularidades:

1. **Endpoints por estación**: el catálogo local trae ``tr_endpoint``
   (tiempo real) y ``hourly_endpoint`` verificados por estación. Sin
   endpoint en catálogo → ``station_not_found``.

2. **Auth opcional**: POEM funciona normalmente sin credenciales; si
   el despliegue las necesita se configuran server-side
   (``METEOLABX_POEM_BEARER_TOKEN`` / ``METEOLABX_POEM_API_KEY`` /
   ``METEOLABX_POEM_BASIC_USER``+``_PASSWORD``).

3. **Boyas/mareógrafos a nivel del mar**: elevación 0 → presión MSL ≡
   absoluta. Frescura: si la serie TR es más vieja que 45 días se
   descarta (igual que el legacy).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from statistics import median
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import httpx

from data_files import POEM_STATIONS_PATH
from server.schemas.errors import ProviderError
from domain.parsing.common import load_stations_json
from domain.parsing.poem import (
    _extract_rows,
    _is_nan,
    _normalize_key,
    _normalize_station_token,
    _poem_wind_scale,
    _rows_to_series,
    _safe_float,
    _trim_series_window,
)

logger = logging.getLogger(__name__)

PROVIDER = "POEM"
BASE_URL = "https://poem.puertos.es"
LOCAL_TZ = ZoneInfo("Europe/Madrid")
TR_MAX_AGE_SECONDS = 45 * 86400


# =====================================================================
# Catálogo local
# =====================================================================

@lru_cache(maxsize=1)
def _load_stations() -> List[Dict[str, Any]]:
    try:
        return load_stations_json(str(POEM_STATIONS_PATH))
    except Exception as exc:
        logger.warning("Catálogo POEM no disponible (%s)", exc)
        return []


def _find_station(station_code: str) -> Dict[str, Any]:
    target = _normalize_station_token(station_code)
    if not target:
        return {}
    for station in _load_stations():
        if _normalize_station_token(station.get("codigo")) == target:
            return station
    return {}


# =====================================================================
# HTTP
# =====================================================================

def _auth_headers(settings: Optional[Any]) -> Tuple[Dict[str, str], Optional[Tuple[str, str]]]:
    """Headers + tupla basic-auth desde settings (todo opcional)."""
    headers: Dict[str, str] = {}
    basic: Optional[Tuple[str, str]] = None
    if settings is None:
        return headers, basic
    bearer = str(getattr(settings, "poem_bearer_token", "") or "").strip()
    api_key = str(getattr(settings, "poem_api_key", "") or "").strip()
    api_key_header = str(getattr(settings, "poem_api_key_header", "") or "X-API-Key").strip()
    basic_user = str(getattr(settings, "poem_basic_user", "") or "").strip()
    basic_password = str(getattr(settings, "poem_basic_password", "") or "").strip()
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    if api_key:
        headers[api_key_header or "X-API-Key"] = api_key
    if basic_user:
        basic = (basic_user, basic_password)
    return headers, basic


async def _fetch_endpoint_series(
    endpoint: str,
    station_code: str,
    client: httpx.AsyncClient,
    *,
    settings: Optional[Any],
    timeout_s: float,
) -> Dict[str, Any]:
    """
    GET de un endpoint POEM → serie legacy-shape (vía poem_parsing) ya
    recortada y con control de frescura. Serie vacía/obsoleta devuelve
    ``has_data: False`` en vez de error (hay varios endpoints candidatos).
    """
    sid = str(station_code).strip()
    sid_param: Any = int(sid) if sid.isdigit() else sid

    headers = {
        "Accept": "application/json",
        "User-Agent": "MeteoLabX/1.0 (+https://meteolabx.com)",
    }
    extra_headers, basic = _auth_headers(settings)
    headers.update(extra_headers)

    try:
        response = await client.get(
            f"{BASE_URL}{endpoint}",
            params={"codigo": sid_param, "OrderBy": "fecha.desc", "Limit": 1000},
            headers=headers,
            auth=basic,
            timeout=timeout_s,
        )
    except httpx.TimeoutException as exc:
        raise ProviderError(
            "provider_timeout",
            provider=PROVIDER,
            detail=f"POEM timeout: {exc}",
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
    if status in (401, 403):
        raise ProviderError(
            "provider_unauthorized",
            provider=PROVIDER,
            detail=f"POEM auth rechazada (HTTP {status})",
            status_code=401,
        )
    if status == 429:
        raise ProviderError(
            "provider_ratelimit",
            provider=PROVIDER,
            detail="POEM rate limit (HTTP 429)",
            status_code=429,
        )
    if status >= 400:
        # 404 de un endpoint candidato no es "estación no existe":
        # hay más candidatos. Lo señalamos como serie vacía.
        return {"has_data": False}

    try:
        payload = response.json()
    except ValueError:
        return {"has_data": False}

    rows = _extract_rows(payload)
    station_meta = _find_station(sid)
    allowed_cols = station_meta.get("parametros_meteo_cols", []) if isinstance(station_meta, dict) else []
    allowed_metric_keys = {
        _normalize_key(col) for col in allowed_cols if str(col or "").strip()
    } or None

    series = _rows_to_series(
        rows, sid,
        allowed_metric_keys=allowed_metric_keys,
        wind_scale=_poem_wind_scale(endpoint),
    )
    series = _trim_series_window(series)

    epochs = series.get("epochs", [])
    if epochs:
        age_s = int(datetime.now(tz=timezone.utc).timestamp()) - int(epochs[-1])
        if age_s > TR_MAX_AGE_SECONDS:
            return {"has_data": False}
    return series


# =====================================================================
# Selección de series (clonado del legacy)
# =====================================================================

def _valid_count(values: Sequence[float]) -> int:
    return sum(1 for v in values if not _is_nan(_safe_float(v)))


def _median_step_seconds(epochs: Sequence[int]) -> Optional[float]:
    if len(epochs) < 2:
        return None
    diffs = [float(epochs[i] - epochs[i - 1]) for i in range(1, len(epochs)) if epochs[i] > epochs[i - 1]]
    return float(median(diffs)) if diffs else None


def _series_score(series: Dict[str, Any]) -> Tuple[int, int, float]:
    epochs = series.get("epochs", [])
    coverage = sum(
        1 for key in ("temps", "humidities", "pressures_abs", "winds", "gusts", "wind_dirs")
        if _valid_count(series.get(key, [])) > 0
    )
    step_s = _median_step_seconds(epochs)
    resolution_score = 0.0 if step_s is None or step_s <= 0 else (1.0 / step_s)
    return coverage, len(epochs), resolution_score


def _pick_graph_series(hourly_series: Dict[str, Any], tr_series: Dict[str, Any]) -> Dict[str, Any]:
    has_hourly = bool(hourly_series.get("has_data"))
    has_tr = bool(tr_series.get("has_data"))
    if has_hourly and not has_tr:
        return hourly_series
    if has_tr and not has_hourly:
        return tr_series
    if not has_hourly and not has_tr:
        return {}

    hourly_score = _series_score(hourly_series)
    tr_score = _series_score(tr_series)
    if tr_score[0] > hourly_score[0]:
        return tr_series
    if hourly_score[0] > tr_score[0]:
        return hourly_series

    hourly_step = _median_step_seconds(hourly_series.get("epochs", []))
    tr_step = _median_step_seconds(tr_series.get("epochs", []))
    if tr_step and hourly_step and tr_step <= (hourly_step * 0.8):
        return tr_series
    return hourly_series if hourly_score[1] >= tr_score[1] else tr_series


def _last_valid(values: Sequence[float]) -> float:
    for value in reversed(list(values)):
        fv = _safe_float(value)
        if not _is_nan(fv):
            return fv
    return float("nan")


async def _fetch_station_series(
    station_id: str,
    client: httpx.AsyncClient,
    *,
    settings: Optional[Any],
    timeout_s: float,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """→ (tr_series, hourly_series, station_meta). Lanza si la estación
    no tiene endpoints en catálogo."""
    station_meta = _find_station(station_id)
    tr_endpoint = str(station_meta.get("tr_endpoint") or "").strip()
    hourly_endpoint = str(station_meta.get("hourly_endpoint") or "").strip()
    if not tr_endpoint and not hourly_endpoint:
        raise ProviderError(
            "station_not_found",
            provider=PROVIDER,
            detail=f"Sin endpoint POEM verificado para {station_id}",
            status_code=404,
        )

    async def _maybe(endpoint: str) -> Dict[str, Any]:
        if not endpoint:
            return {"has_data": False}
        return await _fetch_endpoint_series(
            endpoint, station_id, client, settings=settings, timeout_s=timeout_s,
        )

    tr_result, hourly_result = await asyncio.gather(
        _maybe(tr_endpoint), _maybe(hourly_endpoint), return_exceptions=True,
    )
    # Auth/ratelimit se propagan siempre (afectan a todos los endpoints);
    # otros errores de un candidato se toleran si el otro responde.
    for result in (tr_result, hourly_result):
        if isinstance(result, ProviderError) and result.error_code in (
            "provider_unauthorized", "provider_ratelimit",
        ):
            raise result
    if isinstance(tr_result, BaseException) and isinstance(hourly_result, BaseException):
        raise tr_result if isinstance(tr_result, ProviderError) else hourly_result
    tr_series = {"has_data": False} if isinstance(tr_result, BaseException) else tr_result
    hourly_series = {"has_data": False} if isinstance(hourly_result, BaseException) else hourly_result
    return tr_series, hourly_series, station_meta


def _local_day_bounds(now: Optional[datetime] = None) -> Tuple[int, int]:
    now_local = (now or datetime.now(tz=LOCAL_TZ)).astimezone(LOCAL_TZ)
    start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp()), int((start + timedelta(days=1)).timestamp())


# =====================================================================
# API pública del servicio
# =====================================================================

async def fetch_current(
    station_id: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    settings: Optional[Any] = None,
    timeout_s: float = 16.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Observación actual de una estación POEM (último valor válido de la
    serie TR, con fallback a la horaria). Elevación 0 (nivel del mar)
    → MSL ≡ absoluta.
    """
    station_id = str(station_id).strip()

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        tr_series, hourly_series, station_meta = await _fetch_station_series(
            station_id, client, settings=settings, timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()

    current_series = tr_series if tr_series.get("has_data") else _pick_graph_series(hourly_series, tr_series)
    if not current_series.get("has_data"):
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail=f"POEM sin datos en endpoints de {station_id}",
            status_code=502,
        )

    epochs = current_series.get("epochs", [])
    p_abs = _last_valid(current_series.get("pressures_abs", []))
    p_msl = _last_valid(current_series.get("pressures_msl", []))
    if _is_nan(p_abs) and not _is_nan(p_msl):
        p_abs = p_msl  # elevación 0
    if _is_nan(p_msl) and not _is_nan(p_abs):
        p_msl = p_abs

    lat = _last_valid(current_series.get("lats", []))
    lon = _last_valid(current_series.get("lons", []))
    if _is_nan(lat):
        lat = _safe_float(station_meta.get("lat", station_meta.get("latitud")))
    if _is_nan(lon):
        lon = _safe_float(station_meta.get("lon", station_meta.get("longitud")))

    # Precipitación del día local (las boyas rara vez la reportan).
    day_start, day_end = _local_day_bounds(now)
    precip_vals = [
        max(0.0, _safe_float(p))
        for ep, p in zip(epochs, current_series.get("precips", []))
        if day_start <= int(ep) < day_end and not _is_nan(_safe_float(p))
    ]
    precip_total = float(sum(precip_vals)) if precip_vals else float("nan")

    epoch = int(epochs[-1])
    dt_utc = datetime.fromtimestamp(epoch, tz=timezone.utc)

    observation: Dict[str, Any] = {
        "Tc": _last_valid(current_series.get("temps", [])),
        "RH": _last_valid(current_series.get("humidities", [])),
        "p_hpa": p_msl,
        "p_abs_hpa": p_abs,
        "wind": _last_valid(current_series.get("winds", [])),
        "gust": _last_valid(current_series.get("gusts", [])),
        "wind_dir_deg": _last_valid(current_series.get("wind_dirs", [])),
        "Td": float("nan"),
        "feels_like": float("nan"),
        "heat_index": float("nan"),
        "wind_chill": float("nan"),
        "precip_rate": float("nan"),
        "precip_total": precip_total,
        "solar_radiation": _last_valid(current_series.get("solar_radiations", [])),
        "uv": float("nan"),
        "epoch": epoch,
        "time_local": dt_utc.astimezone(LOCAL_TZ).isoformat(),
        "time_utc": dt_utc.isoformat(),
        "lat": lat,
        "lon": lon,
        "elevation": 0.0,
        "station_name": str(station_meta.get("nombre", "") or station_id).strip(),
    }

    from domain.observation_pipeline import add_basic_derived
    return add_basic_derived(observation)


async def fetch_recent_series(
    station_id: str,
    *,
    days_back: int = 7,
    client: Optional[httpx.AsyncClient] = None,
    settings: Optional[Any] = None,
    timeout_s: float = 16.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Serie reciente (T/HR/presión MSL) para tendencias: la serie con
    mayor cobertura temporal entre TR y horaria, recortada a la ventana.
    """
    station_id = str(station_id).strip()

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        tr_series, hourly_series, station_meta = await _fetch_station_series(
            station_id, client, settings=settings, timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()

    # Mayor cobertura temporal gana (criterio del legacy para trends).
    def _span(series: Dict[str, Any]) -> float:
        epochs = series.get("epochs", [])
        return float(epochs[-1] - epochs[0]) if len(epochs) >= 2 else 0.0

    candidates = [s for s in (tr_series, hourly_series) if s.get("has_data")]
    if not candidates:
        return {
            "epochs": [], "temps": [], "humidities": [], "pressures": [],
            "lat": float("nan"), "lon": float("nan"), "has_data": False,
        }
    chart = max(candidates, key=_span)

    now_epoch = int((now or datetime.now(tz=timezone.utc)).timestamp())
    cutoff = now_epoch - max(1, int(days_back)) * 86400
    epochs_all = [int(ep) for ep in chart.get("epochs", [])]
    keep = [i for i, ep in enumerate(epochs_all) if ep >= cutoff]

    def _col(key: str) -> List[float]:
        values = chart.get(key, [])
        return [
            _safe_float(values[i]) if i < len(values) else float("nan")
            for i in keep
        ]

    pressures_msl = _col("pressures_msl")
    pressures_abs = _col("pressures_abs")
    pressures = [
        msl if not _is_nan(msl) else abs_  # elevación 0: MSL ≡ absoluta
        for msl, abs_ in zip(pressures_msl, pressures_abs)
    ]
    lat = _last_valid(chart.get("lats", []))
    lon = _last_valid(chart.get("lons", []))
    if _is_nan(lat):
        lat = _safe_float(station_meta.get("lat", station_meta.get("latitud")))
    if _is_nan(lon):
        lon = _safe_float(station_meta.get("lon", station_meta.get("longitud")))

    return {
        "epochs": [epochs_all[i] for i in keep],
        "temps": _col("temps"),
        "humidities": _col("humidities"),
        "pressures": pressures,
        "lat": lat,
        "lon": lon,
        "has_data": bool(keep),
    }


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
    settings: Optional[Any] = None,
    timeout_s: float = 16.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Serie del día local en shape canónico, eligiendo la mejor fuente
    (TR vs horaria) con el scoring legacy.
    """
    station_id = str(station_id).strip()

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        tr_series, hourly_series, station_meta = await _fetch_station_series(
            station_id, client, settings=settings, timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()

    chart = _pick_graph_series(hourly_series, tr_series)
    if not chart.get("has_data"):
        return _empty_today_series()

    day_start, day_end = _local_day_bounds(now)
    epochs_all = [int(ep) for ep in chart.get("epochs", [])]
    keep = [i for i, ep in enumerate(epochs_all) if day_start <= ep < day_end]
    if not keep:
        return _empty_today_series()

    def _col(key: str, transform=None) -> List[float]:
        values = chart.get(key, [])
        out = []
        for i in keep:
            v = _safe_float(values[i]) if i < len(values) else float("nan")
            out.append(transform(v) if transform else v)
        return out

    # Presión canónica = MSL; en POEM (nivel del mar) usa la MSL nativa
    # si existe y si no la absoluta tal cual.
    pressures_msl = _col("pressures_msl")
    pressures_abs = _col("pressures_abs")
    pressures = [
        msl if not _is_nan(msl) else abs_
        for msl, abs_ in zip(pressures_msl, pressures_abs)
    ]

    lat = _last_valid(chart.get("lats", []))
    lon = _last_valid(chart.get("lons", []))
    if _is_nan(lat):
        lat = _safe_float(station_meta.get("lat", station_meta.get("latitud")))
    if _is_nan(lon):
        lon = _safe_float(station_meta.get("lon", station_meta.get("longitud")))

    return {
        "epochs": [epochs_all[i] for i in keep],
        "temps": _col("temps"),
        "humidities": _col("humidities"),
        "dewpts": [float("nan")] * len(keep),
        "pressures": pressures,
        "uv_indexes": [float("nan")] * len(keep),
        "solar_radiations": _col("solar_radiations"),
        "winds": _col("winds"),
        "gusts": _col("gusts"),
        "wind_dirs": _col("wind_dirs"),
        "lat": lat,
        "lon": lon,
        "has_data": True,
    }
