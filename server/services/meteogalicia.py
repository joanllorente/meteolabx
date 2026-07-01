"""
Servicio puro de MeteoGalicia.

Versión "limpia" del cliente legacy (``services/meteogalicia.py``):
sin ``streamlit``, cliente ``httpx.AsyncClient``.

Particularidades:

1. **API pública sin key**: los endpoints RSS/JSON de MeteoGalicia no
   requieren autenticación. No hay settings de credenciales.

2. **Dos endpoints**:
   - 10-minutal (``ultimos10minEstacionsMeteo.action``) → observación
     más fresca.
   - Horario (``ultimosHorariosEstacions.action``) → serie de hasta
     72 h con todas las medidas por instante.
   ``fetch_current`` combina ambos (10-min preferente, horario como
   fallback campo a campo); ``fetch_today_series`` usa el horario
   recortado al día local.

3. **Medidas heterogéneas**: cada instante trae ``listaMedidas`` con
   códigos/nombres variados (TA_AVG_1.5m, VV_MAX_10m…). El scoring de
   ``_measure_kind_score`` (clonado del legacy) elige la mejor medida
   por tipo y descarta valores invalidados (códigos 3 y 9) o centinela
   (≤ -9999).

4. **Unidades**: viento puede llegar en m/s o km/h según la medida; se
   normaliza a km/h mirando el campo ``unidade``. La presión es
   absoluta de estación → MSL derivada con la altitud del catálogo.
"""

from __future__ import annotations

import logging
import math
import unicodedata
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

from data_files import METEOGALICIA_STATIONS_PATH
from server.schemas.errors import ProviderError
from server.services.cache import AsyncTTLCache
from domain.parsing.common import find_station_by_field, load_stations_json, parse_epoch

logger = logging.getLogger(__name__)

PROVIDER = "METEOGALICIA"
BASE_URL = "https://servizos.meteogalicia.gal/mgrss/observacion"
TENMIN_ENDPOINT = f"{BASE_URL}/ultimos10minEstacionsMeteo.action"
HOURLY_ENDPOINT = f"{BASE_URL}/ultimosHorariosEstacions.action"
DAILY_ENDPOINT = f"{BASE_URL}/datosDiariosEstacionsMeteo.action"
LOCAL_TZ = ZoneInfo("Europe/Madrid")

MEASURE_KINDS = ("temp", "rh", "pressure", "wind", "gust", "wind_dir", "precip", "solar")
_DAILY_EXTREMES_CACHE = AsyncTTLCache[Dict[str, Dict[str, float]]](
    default_ttl_s=10 * 60,
    max_entries=20,
)


# =====================================================================
# Helpers numéricos / texto (clonados del legacy)
# =====================================================================

def _is_nan(value: float) -> bool:
    return value != value


def _safe_float(value: Any, default: float = float("nan")) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_text(value: Any) -> str:
    txt = str(value or "").strip().lower()
    return "".join(c for c in unicodedata.normalize("NFD", txt) if unicodedata.category(c) != "Mn")


def _absolute_to_msl(p_abs_hpa: float, elevation_m: float) -> float:
    if _is_nan(p_abs_hpa) or _is_nan(elevation_m):
        return float("nan")
    try:
        return float(p_abs_hpa) * math.exp(float(elevation_m) / 8000.0)
    except Exception:
        return float("nan")


def _measure_kind_score(code_raw: str, name_raw: str) -> Tuple[str, int]:
    code = str(code_raw or "").strip().upper().replace(" ", "")
    name = _normalize_text(name_raw)

    # La racha primero para no caer en las reglas genéricas de viento.
    if (
        (code.startswith("VV_") and "_MAX_" in code)
        or "racha" in name
        or "refacho" in name
    ):
        return "gust", 80

    if code.startswith("DV_") or ("direccion" in name and ("vento" in name or "viento" in name)):
        score = 70
        if "media" in name or "avg" in code:
            score += 5
        return "wind_dir", score

    if code.startswith("VV_") or (
        ("velocidade" in name or "velocidad" in name)
        and ("vento" in name or "viento" in name)
    ):
        score = 60
        if "_AVG_" in code or "media" in name:
            score += 8
        if "10M" in code:
            score += 2
        return "wind", score

    if code.startswith("TA_") or "temperatura" in name:
        score = 50
        if "_AVG_" in code or "media" in name or "instant" in name:
            score += 10
        if "1.5M" in code:
            score += 2
        return "temp", score

    if code.startswith("HR_") or "humidade relativa" in name or "humedad relativa" in name:
        score = 45
        if "_AVG_" in code or "media" in name:
            score += 8
        if "1.5M" in code:
            score += 2
        return "rh", score

    if code.startswith("PA_") or "presion" in name:
        score = 40
        if "_AVG_" in code or "media" in name:
            score += 8
        return "pressure", score

    if code.startswith("PP_") or "precipit" in name:
        score = 35
        if "_SUM_" in code or "acum" in name:
            score += 8
        return "precip", score

    if (
        code.startswith("BIO_")
        or "ultravioleta" in name
        or "indice uv" in name
        or "indice uvi" in name
        or name == "uv"
        or " uv" in name
    ):
        score = 32
        if "_AVG_" in code or "media" in name:
            score += 5
        return "uv", score

    if code.startswith("RS_") or code.startswith("SR_") or code.startswith("RG_") or "radiacion" in name:
        return "solar", 30

    return "", -1


def _wind_to_kmh(value: float, unit: str) -> float:
    if _is_nan(value):
        return float("nan")
    unit_norm = _normalize_text(unit)
    if "m/s" in unit_norm or "m.s" in unit_norm or "m s" in unit_norm:
        return value * 3.6
    return value


def _uv_to_index(value: float, unit: str) -> float:
    if _is_nan(value):
        return float("nan")
    unit_norm = _normalize_text(unit)
    if "w/m2" in unit_norm or "w m-2" in unit_norm or "w.m-2" in unit_norm:
        return max(0.0, float(value) * 40.0)
    return max(0.0, float(value))


def _extract_measures(lista_medidas: Any) -> Dict[str, float]:
    """``listaMedidas`` → ``{kind: valor}`` con el mejor score por tipo."""
    best: Dict[str, Tuple[int, float, str]] = {}
    if not isinstance(lista_medidas, list):
        return {}

    for measure in lista_medidas:
        if not isinstance(measure, dict):
            continue

        validation = measure.get("lnCodigoValidacion")
        try:
            validation_int = int(validation)
            if validation_int in (3, 9):  # invalidado / sospechoso
                continue
        except Exception:
            validation_int = None

        value = _safe_float(measure.get("valor"))
        if _is_nan(value) or value <= -9999:
            continue

        kind, score = _measure_kind_score(
            str(measure.get("codigoParametro", "")),
            str(measure.get("nomeParametro", "")),
        )
        if not kind:
            continue
        if validation_int in (1, 5):  # validado/interpolado: pequeño bonus
            score += 1

        current = best.get(kind)
        if current is None or score >= current[0]:
            best[kind] = (score, value, str(measure.get("unidade", "")))

    out: Dict[str, float] = {}
    for kind, (_score, value, unit) in best.items():
        if kind in ("wind", "gust"):
            out[kind] = _wind_to_kmh(value, unit)
        elif kind == "uv":
            out[kind] = _uv_to_index(value, unit)
        else:
            out[kind] = value
    return out


# =====================================================================
# Catálogo local de estaciones
# =====================================================================

@lru_cache(maxsize=1)
def _load_stations() -> List[Dict[str, Any]]:
    try:
        return load_stations_json(
            str(METEOGALICIA_STATIONS_PATH), dict_key="listaEstacionsMeteo",
        )
    except Exception as exc:
        logger.warning("Catálogo MeteoGalicia no disponible (%s)", exc)
        return []


def _station_meta(station_id: str) -> Tuple[float, float, float, str]:
    """→ (lat, lon, elevation, nombre)."""
    station = find_station_by_field(
        _load_stations(), field="idEstacion", target=station_id,
    )
    lat = _safe_float(station.get("lat"))
    lon = _safe_float(station.get("lon"))
    elevation = _safe_float(station.get("altitude"))
    name = str(station.get("estacion", "") or "").strip()
    return lat, lon, elevation, name


# =====================================================================
# HTTP
# =====================================================================

async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    params: Dict[str, Any],
    *,
    timeout_s: float,
) -> Any:
    try:
        response = await client.get(
            url, params=params, headers={"Accept": "application/json"}, timeout=timeout_s,
        )
    except httpx.TimeoutException as exc:
        raise ProviderError(
            "provider_timeout",
            provider=PROVIDER,
            detail=f"MeteoGalicia timeout: {exc}",
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
            detail="Recurso no encontrado (HTTP 404)",
            status_code=404,
        )
    if status == 429:
        raise ProviderError(
            "provider_ratelimit",
            provider=PROVIDER,
            detail="MeteoGalicia rate limit (HTTP 429)",
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


def _extract_items(payload: Any, keys: List[str]) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _pick_station_item(items: List[Dict[str, Any]], station_id: str) -> Dict[str, Any]:
    sid = str(station_id).strip()
    if sid:
        for item in items:
            if str(item.get("idEstacion", "")).strip() == sid:
                return item
    return items[0] if items else {}


def _instant_epoch(item: Dict[str, Any]) -> Optional[int]:
    return parse_epoch(
        item.get("instanteLecturaUTC")
        or item.get("instanteUTC")
        or item.get("instanteLectura")
    )


# =====================================================================
# Serie horaria → filas por epoch
# =====================================================================

async def _fetch_hourly_rows(
    station_id: str,
    client: httpx.AsyncClient,
    *,
    num_hours: int,
    timeout_s: float,
) -> Dict[int, Dict[str, float]]:
    """Filas ``{epoch: {kind: valor}}`` del endpoint horario."""
    nh = max(1, min(72, int(num_hours)))
    payload = await _get_json(
        client, HOURLY_ENDPOINT,
        {"idEst": str(station_id).strip(), "numHoras": nh},
        timeout_s=timeout_s,
    )
    blocks = _extract_items(payload, keys=["listHorarios", "listaHorarios", "horarios"])
    block = _pick_station_item(blocks, station_id)
    instants = block.get("listaInstantes", []) if isinstance(block, dict) else []

    rows: Dict[int, Dict[str, float]] = {}
    for instant in instants if isinstance(instants, list) else []:
        if not isinstance(instant, dict):
            continue
        epoch = _instant_epoch(instant)
        if epoch is None:
            continue
        extracted = _extract_measures(instant.get("listaMedidas", []))
        if not extracted:
            continue
        row = rows.setdefault(epoch, {})
        row.update(extracted)
    return rows


async def _fetch_tenmin_snapshot(
    station_id: str,
    client: httpx.AsyncClient,
    *,
    timeout_s: float,
) -> Tuple[Dict[str, float], Optional[int]]:
    """Observación 10-minutal → (medidas, epoch). Vacía si no hay datos."""
    payload = await _get_json(
        client, TENMIN_ENDPOINT,
        {"idEst": str(station_id).strip()},
        timeout_s=timeout_s,
    )
    items = _extract_items(
        payload, keys=["listUltimos10min", "listaUltimos10min", "ultimos10min"],
    )
    item = _pick_station_item(items, station_id)
    if not item:
        return {}, None
    return _extract_measures(item.get("listaMedidas", [])), _instant_epoch(item)


def _local_day_start_epoch(now: Optional[datetime] = None) -> int:
    now_local = (now or datetime.now(tz=LOCAL_TZ)).astimezone(LOCAL_TZ)
    return int(now_local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


# =====================================================================
# API pública del servicio
# =====================================================================

async def fetch_current(
    station_id: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 12.0,
    now: Optional[datetime] = None,
    include_daily_extremes: bool = True,
) -> Dict[str, Any]:
    """
    Observación actual: medidas 10-minutales con fallback campo a campo
    a la última fila horaria. ``precip_total`` = suma de precipitación
    horaria del día local. Devuelve el dict canónico + ``station_code``
    y ``station_name``.
    """
    import asyncio

    station_id = str(station_id).strip()

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        tenmin_task = _fetch_tenmin_snapshot(station_id, client, timeout_s=timeout_s)
        hourly_task = _fetch_hourly_rows(station_id, client, num_hours=24, timeout_s=timeout_s)
        if include_daily_extremes:
            today_local = (now or datetime.now(tz=LOCAL_TZ)).astimezone(LOCAL_TZ).date()
            daily_task = _cached_daily_extremes_by_station(client, today_local, timeout_s=timeout_s)
            tenmin_result, hourly_result, daily_result = await asyncio.gather(
                tenmin_task, hourly_task, daily_task, return_exceptions=True,
            )
        else:
            tenmin_result, hourly_result = await asyncio.gather(
                tenmin_task, hourly_task, return_exceptions=True,
            )
            daily_result = {}
    finally:
        if owns_client:
            await client.aclose()

    # Toleramos que falle uno de los dos; si fallan ambos, propagamos.
    if isinstance(tenmin_result, BaseException) and isinstance(hourly_result, BaseException):
        raise tenmin_result if isinstance(tenmin_result, ProviderError) else hourly_result
    measures: Dict[str, float] = {}
    epoch: Optional[int] = None
    if not isinstance(tenmin_result, BaseException):
        measures, epoch = tenmin_result
    rows = {} if isinstance(hourly_result, BaseException) else hourly_result
    daily_extremes = {}
    if isinstance(daily_result, BaseException):
        logger.warning("MeteoGalicia extremos diarios no disponibles para %s: %s", station_id, daily_result)
    else:
        daily_extremes = daily_result.get(station_id, {})

    return _normalize_current(station_id, measures, epoch, rows, now=now, daily_extremes=daily_extremes)


def _normalize_current(
    station_id: str,
    tenmin: Dict[str, float],
    tenmin_epoch: Optional[int],
    hourly_rows: Dict[int, Dict[str, float]],
    *,
    now: Optional[datetime] = None,
    daily_extremes: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    lat, lon, elevation, name = _station_meta(station_id)

    epochs = sorted(hourly_rows.keys())
    last_row = hourly_rows[epochs[-1]] if epochs else {}

    if not tenmin and not last_row:
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail=f"MeteoGalicia sin observaciones para {station_id}",
            status_code=502,
        )

    def _value(kind: str) -> float:
        value = _safe_float(tenmin.get(kind))
        if not _is_nan(value):
            return value
        # Fallback: último valor válido de la serie horaria.
        for ep in reversed(epochs):
            row_value = _safe_float(hourly_rows[ep].get(kind))
            if not _is_nan(row_value):
                return row_value
        return float("nan")

    epoch = tenmin_epoch
    if epoch is None and epochs:
        epoch = epochs[-1]
    if epoch is None:
        epoch = int(datetime.now(tz=timezone.utc).timestamp())

    p_abs = _value("pressure")
    p_msl = _absolute_to_msl(p_abs, elevation)

    # Precipitación del día local: suma de incrementos horarios.
    day_start = _local_day_start_epoch(now)
    precip_vals = [
        max(0.0, _safe_float(hourly_rows[ep].get("precip")))
        for ep in epochs
        if ep >= day_start and not _is_nan(_safe_float(hourly_rows[ep].get("precip")))
    ]
    if precip_vals:
        precip_total = float(sum(precip_vals))
    else:
        tenmin_precip = _safe_float(tenmin.get("precip"))
        precip_total = max(0.0, tenmin_precip) if not _is_nan(tenmin_precip) else float("nan")

    dt_utc = datetime.fromtimestamp(int(epoch), tz=timezone.utc)

    observation: Dict[str, Any] = {
        "Tc": _value("temp"),
        "RH": _value("rh"),
        "p_hpa": p_msl,
        "p_abs_hpa": p_abs,
        "wind": _value("wind"),       # ya en km/h (_extract_measures)
        "gust": _value("gust"),
        "wind_dir_deg": _value("wind_dir"),
        "Td": float("nan"),
        "feels_like": float("nan"),
        "heat_index": float("nan"),
        "wind_chill": float("nan"),
        "precip_rate": float("nan"),
        "precip_total": precip_total,
        "solar_radiation": _value("solar"),
        "uv": _value("uv"),
        "epoch": int(epoch),
        "time_local": dt_utc.astimezone(LOCAL_TZ).isoformat(),
        "time_utc": dt_utc.isoformat(),
        "lat": lat,
        "lon": lon,
        "elevation": elevation,
        "station_name": name,
        "daily_extremes": dict(daily_extremes or {}),
    }

    from domain.observation_pipeline import add_basic_derived
    return add_basic_derived(observation)


async def _cached_daily_extremes_by_station(
    client: httpx.AsyncClient,
    day,
    *,
    timeout_s: float,
) -> Dict[str, Dict[str, float]]:
    day_key = day.isoformat()
    return await _DAILY_EXTREMES_CACHE.get_or_fetch(
        f"meteogalicia:daily-extremes:{day_key}",
        lambda: _fetch_daily_extremes_by_station(client, day, timeout_s=timeout_s),
    )


async def _fetch_daily_extremes_by_station(
    client: httpx.AsyncClient,
    day,
    *,
    timeout_s: float,
) -> Dict[str, Dict[str, float]]:
    payload = await _get_json(
        client,
        DAILY_ENDPOINT,
        {"dataIni": day.isoformat(), "dataFin": day.isoformat()},
        timeout_s=timeout_s,
    )
    days = payload.get("listDatosDiarios") if isinstance(payload, dict) else None
    if not isinstance(days, list) or not days:
        return {}

    out: Dict[str, Dict[str, float]] = {}
    day_block = days[-1] if isinstance(days[-1], dict) else {}
    stations = day_block.get("listaEstacions", []) if isinstance(day_block, dict) else []
    for station in stations if isinstance(stations, list) else []:
        if not isinstance(station, dict):
            continue
        station_id = str(station.get("idEstacion", "")).strip()
        if not station_id:
            continue
        measures = station.get("listaMedidas", []) or []
        by_code = {
            str(measure.get("codigoParametro", "")): measure
            for measure in measures
            if isinstance(measure, dict)
        }
        def _daily_value(code: str, *, wind: bool = False) -> float:
            measure = by_code.get(code, {})
            value = _safe_float(measure.get("valor"))
            if _is_nan(value) or value <= -9990:
                return float("nan")
            if wind:
                return _wind_to_kmh(value, str(measure.get("unidade", "")))
            return value

        extremes: Dict[str, float] = {}
        for key, value in (
            ("temp_max", _daily_value("TA_MAX_1.5m")),
            ("temp_min", _daily_value("TA_MIN_1.5m")),
            ("rh_max", _daily_value("HR_MAX_1.5m")),
            ("rh_min", _daily_value("HR_MIN_1.5m")),
            ("gust_max", _daily_value("VV_MAX_10m", wind=True)),
        ):
            if not _is_nan(value):
                extremes[key] = value
        if extremes:
            out[station_id] = extremes
    return out


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
    timeout_s: float = 12.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Serie horaria del día local en shape canónico ``TodaySeries``.
    La presión canónica es MSL (derivada de la absoluta con la altitud
    del catálogo).
    """
    station_id = str(station_id).strip()

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        rows = await _fetch_hourly_rows(station_id, client, num_hours=24, timeout_s=timeout_s)
    finally:
        if owns_client:
            await client.aclose()

    day_start = _local_day_start_epoch(now)
    epochs = sorted(ep for ep in rows if ep >= day_start)
    if not epochs:
        return _empty_today_series()

    lat, lon, elevation, _name = _station_meta(station_id)

    def _at(epoch: int, kind: str) -> float:
        return _safe_float(rows[epoch].get(kind))

    return {
        "epochs": epochs,
        "temps": [_at(ep, "temp") for ep in epochs],
        "humidities": [_at(ep, "rh") for ep in epochs],
        "dewpts": [float("nan")] * len(epochs),
        "pressures": [_absolute_to_msl(_at(ep, "pressure"), elevation) for ep in epochs],
        "uv_indexes": [_at(ep, "uv") for ep in epochs],
        "solar_radiations": [_at(ep, "solar") for ep in epochs],
        "winds": [_at(ep, "wind") for ep in epochs],
        "gusts": [_at(ep, "gust") for ep in epochs],
        "wind_dirs": [_at(ep, "wind_dir") for ep in epochs],
        "lat": lat,
        "lon": lon,
        "has_data": True,
    }


async def fetch_recent_series(
    station_id: str,
    *,
    days_back: int = 7,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 12.0,
    now: Optional[datetime] = None,
    fine: bool = False,
) -> Dict[str, Any]:
    """
    Serie reciente (T/HR/presión MSL) para tendencias. El endpoint
    horario de MeteoGalicia cubre ~72 h como máximo (límite práctico
    del legacy); buckets de 3 h con la última lectura por bucket.

    ``fine=True`` usa buckets de 1 h (lo pide el lookback de
    ``/series/today`` para que la tendencia de presión 3h arranque a las
    00:00 local; ver nota en el lookback del router).
    """
    station_id = str(station_id).strip()
    lat, lon, elevation, _name = _station_meta(station_id)
    hours = max(24, min(72, int(days_back) * 24))

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        rows = await _fetch_hourly_rows(
            station_id, client, num_hours=hours, timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()

    cutoff = int((now or datetime.now(tz=timezone.utc)).timestamp()) - max(1, int(days_back)) * 86400
    bucket_s = 3600 if fine else 3 * 3600
    buckets: Dict[int, tuple] = {}
    for epoch in sorted(rows):
        if epoch < cutoff:
            continue
        row = rows[epoch]
        bucket = (int(epoch) // bucket_s) * bucket_s
        buckets[bucket] = (
            epoch,
            _safe_float(row.get("temp")),
            _safe_float(row.get("rh")),
            _absolute_to_msl(_safe_float(row.get("pressure")), elevation),
        )

    epochs = sorted(buckets)
    if not epochs:
        return {
            "epochs": [], "temps": [], "humidities": [], "pressures": [],
            "lat": lat, "lon": lon, "has_data": False,
        }
    return {
        "epochs": epochs,
        "temps": [buckets[b][1] for b in epochs],
        "humidities": [buckets[b][2] for b in epochs],
        "pressures": [buckets[b][3] for b in epochs],
        "lat": lat,
        "lon": lon,
        "has_data": True,
    }
