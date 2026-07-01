"""
Servicio puro de Meteocat (XEMA).

Versión "limpia" del cliente Meteocat legacy (``services/meteocat.py``):
sin ``streamlit``, sin ``st.cache_data``, cliente ``httpx.AsyncClient``
para integrarse con FastAPI.

Diferencias clave con WU/AEMET:

1. **Auth**: como AEMET, Meteocat usa una API key **del servidor**
   (env var ``METEOLABX_METEOCAT_API_KEY``), enviada en el header
   ``x-api-key``. No per-user.

2. **Modelo de datos por variable**: XEMA no tiene un endpoint de
   "observación actual" con todas las magnitudes; cada variable
   (temperatura=32, humedad=33, presión=34…) se consulta por separado
   (``/variables/mesurades/{codi}/ultimes``) o llega agrupada en el
   endpoint de día (``/estacions/mesurades/{codi}/{Y}/{M}/{D}``).

3. **Cuota**: la API de Meteocat tiene cuota mensual limitada. Por eso
   ``fetch_current`` NO replica el fan-out legacy de ~10 requests
   ``/ultimes``: deriva la observación actual del endpoint de día (1-2
   requests, las mismas que ya necesita ``fetch_today_series``) y solo
   cae al fan-out ``/ultimes`` si el día local aún no tiene datos
   (p. ej. justo después de medianoche o estación rezagada).

4. **Unidades**: viento y racha llegan en **m/s** → convertimos a km/h.
   La presión es **absoluta** de estación; la MSL se deriva con la
   exponencial barométrica (mismo factor 8000 m que el resto de la app).

5. **Metadatos de estación**: lat/lon/altitud/nombre no vienen en las
   lecturas; se resuelven del catálogo local
   ``data/data_estaciones_meteocat.json`` (mismo fichero que usa el
   frontend).

Mapeo de errores → ``ProviderError`` (códigos estables del contrato):

    timeout              → provider_timeout        (504)
    error de red         → provider_network_error  (502)
    HTTP 401/403         → provider_unauthorized   (401)
    HTTP 404             → station_not_found       (404)
    HTTP 429             → provider_ratelimit      (429)
    otros 4xx/5xx        → provider_http_error     (502)
    JSON inválido        → provider_bad_response   (502)
    api_key vacía        → provider_unauthorized   (401)
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

from data_files import METEOCAT_STATIONS_PATH
from server.schemas.errors import ProviderError

logger = logging.getLogger(__name__)

PROVIDER = "METEOCAT"
BASE_URL = "https://api.meteo.cat/xema/v1"
CAT_TZ = ZoneInfo("Europe/Madrid")

# Códigos de variable XEMA (mismos que el legacy services/meteocat.py).
V_WIND_VEC_10M = 20
V_WIND_DIR_VEC_10M = 21
V_WIND_VEC_6M = 23
V_WIND_DIR_VEC_6M = 24
V_WIND_VEC_2M = 26
V_WIND_DIR_VEC_2M = 27
V_WIND = 30
V_WIND_DIR = 31
V_TEMP = 32
V_RH = 33
V_PRESSURE = 34
V_PRECIP = 35
V_SOLAR = 36
V_UV = 39
V_TEMP_MAX = 40
V_TEMP_MIN = 42
V_RH_MAX = 3
V_RH_MIN = 44
V_WIND_2M = 46
V_WIND_DIR_2M = 47
V_WIND_6M = 48
V_WIND_DIR_6M = 49
V_GUST = 50
V_GUST_DIR = 51
V_GUST_6M = 53
V_GUST_DIR_6M = 54
V_GUST_2M = 56
V_GUST_DIR_2M = 57

# Variables que componen la "observación actual", con candidatos en
# orden de preferencia (algunas estaciones miden viento a 2/6 m en vez
# de 10 m; ídem para la racha).
LATEST_VARIABLES: Dict[str, List[int]] = {
    "temp": [V_TEMP],
    "rh": [V_RH],
    "pressure_abs": [V_PRESSURE],
    "solar": [V_SOLAR],
    "uv": [V_UV],
    "wind": [V_WIND, V_WIND_VEC_10M, V_WIND_6M, V_WIND_VEC_6M, V_WIND_2M, V_WIND_VEC_2M],
    "wind_dir": [V_WIND_DIR, V_WIND_DIR_VEC_10M, V_WIND_DIR_6M, V_WIND_DIR_VEC_6M, V_WIND_DIR_2M, V_WIND_DIR_VEC_2M],
    "gust": [V_GUST, V_GUST_6M, V_GUST_2M],
    "gust_dir": [V_GUST_DIR, V_GUST_DIR_6M, V_GUST_DIR_2M],
}


# =====================================================================
# Helpers numéricos / temporales (clonados del legacy y limpiados)
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


def _ms_to_kmh(value: float) -> float:
    return float("nan") if _is_nan(value) else value * 3.6


def _non_negative(value: float) -> float:
    if _is_nan(value):
        return float("nan")
    return max(0.0, float(value))


def _absolute_to_msl(p_abs_hpa: float, elevation_m: float) -> float:
    """Presión absoluta → MSL con la exponencial barométrica (z/8000)."""
    if _is_nan(p_abs_hpa) or _is_nan(elevation_m):
        return float("nan")
    try:
        return float(p_abs_hpa) * math.exp(float(elevation_m) / 8000.0)
    except Exception:
        return float("nan")


def _parse_measurement_epoch(value: Any) -> Optional[int]:
    """
    Timestamp de lectura XEMA → epoch UTC. Meteocat reporta ISO con
    ``Z`` o naive; los naive se interpretan en hora de Catalunya (mismo
    criterio que el legacy).
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CAT_TZ)
        return int(dt.timestamp())
    except Exception:
        return None


# =====================================================================
# Catálogo local de estaciones (lat/lon/altitud/nombre)
# =====================================================================

@lru_cache(maxsize=1)
def _load_station_catalog() -> Dict[str, Dict[str, Any]]:
    """
    Indexa ``data/data_estaciones_meteocat.json`` por ``codi``. Si el
    fichero no existe o está corrupto, devuelve catálogo vacío (las
    observaciones salen sin lat/lon/altitud, pero salen).
    """
    try:
        with open(METEOCAT_STATIONS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        logger.warning("Catálogo Meteocat no disponible (%s); metadatos vacíos", exc)
        return {}
    if not isinstance(data, list):
        return {}
    catalog: Dict[str, Dict[str, Any]] = {}
    for item in data:
        if isinstance(item, dict) and item.get("codi"):
            catalog[str(item["codi"]).strip().upper()] = item
    return catalog


def _station_meta(station_id: str) -> Tuple[float, float, float, str]:
    """→ (lat, lon, elevation, nombre); NaN/"" si la estación no está en catálogo."""
    station = _load_station_catalog().get(str(station_id).strip().upper(), {})
    coords = station.get("coordenades", {}) if isinstance(station, dict) else {}
    lat = _safe_float(coords.get("latitud") if isinstance(coords, dict) else None)
    lon = _safe_float(coords.get("longitud") if isinstance(coords, dict) else None)
    elevation = _safe_float(station.get("altitud"))
    name = str(station.get("nom", "") or "").strip()
    return lat, lon, elevation, name


# =====================================================================
# HTTP con mapeo de errores a ProviderError
# =====================================================================

def _raise_for_http_status(status_code: int) -> None:
    if status_code in (401, 403):
        raise ProviderError(
            "provider_unauthorized",
            provider=PROVIDER,
            detail=f"Invalid Meteocat API key (HTTP {status_code})",
            status_code=401,
        )
    if status_code == 404:
        raise ProviderError(
            "station_not_found",
            provider=PROVIDER,
            detail="Station or variable not found (HTTP 404)",
            status_code=404,
        )
    if status_code == 429:
        raise ProviderError(
            "provider_ratelimit",
            provider=PROVIDER,
            detail="Meteocat quota/rate limit (HTTP 429)",
            status_code=429,
        )
    if status_code >= 400:
        raise ProviderError(
            "provider_http_error",
            provider=PROVIDER,
            detail=f"HTTP {status_code}",
            status_code=502,
        )


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    api_key: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    timeout_s: float = 15.0,
    empty_statuses: Tuple[int, ...] = (),
) -> Any:
    headers = {"x-api-key": api_key, "Accept": "application/json"}
    try:
        response = await client.get(url, params=params or {}, headers=headers, timeout=timeout_s)
    except httpx.TimeoutException as exc:
        raise ProviderError(
            "provider_timeout",
            provider=PROVIDER,
            detail=f"Meteocat timeout: {exc}",
            status_code=504,
        ) from exc
    except httpx.RequestError as exc:
        raise ProviderError(
            "provider_network_error",
            provider=PROVIDER,
            detail=str(exc) or "Network error",
            status_code=502,
        ) from exc

    if response.status_code in empty_statuses:
        return []
    _raise_for_http_status(response.status_code)

    try:
        return response.json()
    except ValueError as exc:
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail=f"JSON inválido: {exc!r}",
            status_code=502,
        ) from exc


def _require_api_key(api_key: str) -> None:
    if not api_key:
        raise ProviderError(
            "provider_unauthorized",
            provider=PROVIDER,
            detail="Missing METEOCAT_API_KEY",
            status_code=401,
        )


# =====================================================================
# Endpoint de día: var_map {codi_variable: [(epoch, valor), ...]}
# =====================================================================

VarMap = Dict[int, List[Tuple[int, float]]]


def _parse_day_payload(payload: Any) -> VarMap:
    """Respuesta de ``/estacions/mesurades/{codi}/{Y}/{M}/{D}`` → VarMap."""
    if isinstance(payload, list) and payload:
        station_block = payload[0]
    elif isinstance(payload, dict):
        station_block = payload
    else:
        return {}
    if not isinstance(station_block, dict):
        return {}

    var_map: VarMap = {}
    variables = station_block.get("variables", [])
    for variable in variables if isinstance(variables, list) else []:
        if not isinstance(variable, dict):
            continue
        try:
            code = int(variable.get("codi"))
        except (TypeError, ValueError):
            continue
        readings = variable.get("lectures", [])
        rows: List[Tuple[int, float]] = []
        for reading in readings if isinstance(readings, list) else []:
            if not isinstance(reading, dict):
                continue
            epoch = _parse_measurement_epoch(reading.get("data"))
            if epoch is None:
                continue
            rows.append((epoch, _safe_float(reading.get("valor"))))
        rows.sort(key=lambda item: item[0])
        var_map[code] = rows
    return var_map


def _merge_var_maps(var_maps: List[VarMap]) -> VarMap:
    merged: Dict[int, Dict[int, float]] = {}
    for var_map in var_maps:
        for code, rows in var_map.items():
            bucket = merged.setdefault(code, {})
            for epoch, value in rows:
                bucket[epoch] = value
    return {
        code: sorted(rows.items(), key=lambda item: item[0])
        for code, rows in merged.items()
    }


def _filter_var_map(var_map: VarMap, start_epoch: int, end_epoch: int) -> VarMap:
    filtered: VarMap = {}
    for code, rows in var_map.items():
        keep = [(ep, val) for ep, val in rows if start_epoch <= ep < end_epoch]
        if keep:
            filtered[code] = keep
    return filtered


def _local_day_bounds(now: Optional[datetime] = None) -> Tuple[int, int]:
    """Inicio/fin (epochs UTC) del día local de Catalunya en curso."""
    now_local = (now or datetime.now(tz=CAT_TZ)).astimezone(CAT_TZ)
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(day_start.timestamp()), int((day_start + timedelta(days=1)).timestamp())


def _utc_dates_for_window(start_epoch: int, end_epoch: int) -> List[datetime]:
    """Días UTC que cubren la ventana (el endpoint de día va por fecha UTC)."""
    safe_end = max(start_epoch, end_epoch - 1)
    cursor = datetime.fromtimestamp(start_epoch, tz=timezone.utc).date()
    limit = datetime.fromtimestamp(safe_end, tz=timezone.utc).date()
    days: List[datetime] = []
    while cursor <= limit:
        days.append(datetime(cursor.year, cursor.month, cursor.day, tzinfo=timezone.utc))
        cursor += timedelta(days=1)
    return days


async def _fetch_local_day_var_map(
    station_id: str,
    api_key: str,
    client: httpx.AsyncClient,
    *,
    timeout_s: float,
    now: Optional[datetime] = None,
) -> VarMap:
    """
    Lecturas del día local en curso (todas las variables). Une los 1-2
    días UTC que cubren el día local y filtra al rango exacto.

    Un 404 en una de las fechas UTC se tolera. Meteocat también responde
    400 cuando la fecha actual todavía no tiene lecturas publicadas; esa
    respuesta se trata como colección vacía para poder usar la fecha UTC
    anterior. Si TODAS las fechas devuelven 404 la estación no existe.
    """
    start_epoch, end_epoch = _local_day_bounds(now)
    days = _utc_dates_for_window(start_epoch, end_epoch)

    async def _fetch_day(day: datetime) -> Any:
        url = f"{BASE_URL}/estacions/mesurades/{station_id}/{day.year:04d}/{day.month:02d}/{day.day:02d}"
        return await _get_json(
            client, url, api_key, timeout_s=timeout_s, empty_statuses=(400,),
        )

    results = await asyncio.gather(
        *(_fetch_day(day) for day in days), return_exceptions=True,
    )

    var_maps: List[VarMap] = []
    not_found = 0
    for result in results:
        if isinstance(result, ProviderError):
            if result.error_code == "station_not_found":
                not_found += 1
                continue
            raise result
        if isinstance(result, BaseException):
            raise result
        var_maps.append(_parse_day_payload(result))

    if not_found == len(days):
        raise ProviderError(
            "station_not_found",
            provider=PROVIDER,
            detail=f"Meteocat no devolvió datos para {station_id} (HTTP 404)",
            status_code=404,
        )

    return _filter_var_map(_merge_var_maps(var_maps), start_epoch, end_epoch)


# =====================================================================
# Fallback /ultimes (solo cuando el día local aún no tiene lecturas)
# =====================================================================

async def _fetch_latest_values(
    station_id: str,
    api_key: str,
    client: httpx.AsyncClient,
    *,
    timeout_s: float,
) -> Dict[str, Tuple[float, Optional[int]]]:
    """
    Fan-out a ``/variables/mesurades/{codi}/ultimes`` por variable.
    Devuelve ``{target: (valor, epoch)}``; errores por-variable se
    toleran (NaN), igual que el snapshot legacy.
    """

    async def _latest_for(candidates: List[int]) -> Tuple[float, Optional[int]]:
        for code in candidates:
            url = f"{BASE_URL}/variables/mesurades/{code}/ultimes"
            try:
                payload = await _get_json(
                    client, url, api_key,
                    params={"codiEstacio": station_id},
                    timeout_s=timeout_s,
                )
            except ProviderError:
                continue
            readings = payload.get("lectures", []) if isinstance(payload, dict) else []
            best_value, best_epoch = float("nan"), None
            for reading in readings if isinstance(readings, list) else []:
                if not isinstance(reading, dict):
                    continue
                epoch = _parse_measurement_epoch(reading.get("data"))
                if epoch is None:
                    continue
                if best_epoch is None or epoch > best_epoch:
                    best_epoch = epoch
                    best_value = _safe_float(reading.get("valor"))
            if not _is_nan(best_value):
                return best_value, best_epoch
        return float("nan"), None

    targets = list(LATEST_VARIABLES.items())
    results = await asyncio.gather(*(_latest_for(candidates) for _, candidates in targets))
    return {name: result for (name, _), result in zip(targets, results)}


# =====================================================================
# Fetch principal: observación actual
# =====================================================================

def _last_value(var_map: VarMap, *codes: int) -> Tuple[float, Optional[int]]:
    """Última lectura no-NaN entre los códigos candidatos, en orden."""
    for code in codes:
        rows = var_map.get(code, [])
        for epoch, value in reversed(rows):
            if not _is_nan(value):
                return value, epoch
    return float("nan"), None


def _series_from_first_available(var_map: VarMap, codes: List[int]) -> List[Tuple[int, float]]:
    for code in codes:
        selected: List[Tuple[int, float]] = []
        for epoch, raw_value in var_map.get(code, []):
            value = _safe_float(raw_value)
            if _is_nan(value):
                continue
            selected.append((int(epoch), value))
        if selected:
            return sorted(selected)
    return []


async def fetch_current(
    station_id: str,
    api_key: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 15.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Observación actual de una estación XEMA (codi tipo ``"C6"``).

    Estrategia (ver docstring del módulo): día local → últimas lecturas
    por variable + precip acumulada del día; fallback a ``/ultimes`` si
    el día aún no tiene datos. Devuelve el dict canónico (mismo shape
    que WU/AEMET) más ``station_code`` y ``station_name``.
    """
    _require_api_key(api_key)
    station_id = str(station_id).strip().upper()

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)

    try:
        var_map = await _fetch_local_day_var_map(
            station_id, api_key, client, timeout_s=timeout_s, now=now,
        )

        values: Dict[str, Tuple[float, Optional[int]]] = {}
        if var_map:
            for name, candidates in LATEST_VARIABLES.items():
                values[name] = _last_value(var_map, *candidates)
            # Precipitación del día: suma de PPT (codi 35) por intervalo.
            # PPTacu (codi 70) es contador del datalogger; no sirve.
            precip_rows = var_map.get(V_PRECIP, [])
            precip_vals = [max(0.0, v) for _, v in precip_rows if not _is_nan(v)]
            precip_total = float(sum(precip_vals)) if precip_vals else float("nan")
        else:
            logger.info(
                "Meteocat: día local sin lecturas para %s; fallback a /ultimes",
                station_id,
            )
            values = await _fetch_latest_values(
                station_id, api_key, client, timeout_s=timeout_s,
            )
            precip_total = float("nan")
    finally:
        if owns_client:
            await client.aclose()

    return _normalize_current(station_id, values, precip_total)


def _normalize_current(
    station_id: str,
    values: Dict[str, Tuple[float, Optional[int]]],
    precip_total: float,
) -> Dict[str, Any]:
    lat, lon, elevation, name = _station_meta(station_id)

    epochs = [epoch for _, epoch in values.values() if epoch is not None]
    epoch = max(epochs) if epochs else int(time.time())

    def _value(key: str) -> float:
        return values.get(key, (float("nan"), None))[0]

    wind_kmh = _ms_to_kmh(_value("wind"))
    gust_kmh = _ms_to_kmh(_value("gust"))
    wind_dir = _value("wind_dir")
    if _is_nan(wind_dir):
        wind_dir = _value("gust_dir")

    p_abs = _value("pressure_abs")
    p_msl = _absolute_to_msl(p_abs, elevation)

    dt_utc = datetime.fromtimestamp(epoch, tz=timezone.utc)

    observation: Dict[str, Any] = {
        "Tc": _value("temp"),
        "RH": _value("rh"),
        "p_hpa": p_msl,       # MSL derivada (Meteocat solo reporta absoluta)
        "p_abs_hpa": p_abs,   # absoluta nativa
        "wind": wind_kmh,
        "gust": gust_kmh,
        "wind_dir_deg": wind_dir,
        # Derivados: SIEMPRE via add_basic_derived, nunca del API.
        "Td": float("nan"),
        "feels_like": float("nan"),
        "heat_index": float("nan"),
        "wind_chill": float("nan"),
        "precip_rate": float("nan"),
        "precip_total": precip_total,
        "solar_radiation": _non_negative(_value("solar")),
        "uv": _value("uv"),
        # Tiempo y posición
        "epoch": epoch,
        "time_local": dt_utc.astimezone(CAT_TZ).isoformat(),
        "time_utc": dt_utc.isoformat(),
        "lat": lat,
        "lon": lon,
        "elevation": elevation,
        "station_name": name,
    }

    # Derivadas básicas (Td Magnus-Tetens, feels_like, heat_index).
    # Import diferido como en server/services/aemet.py.
    from domain.observation_pipeline import add_basic_derived
    return add_basic_derived(observation)


# =====================================================================
# Series del día
# =====================================================================

def _empty_today_series() -> Dict[str, Any]:
    """Shape vacío que coincide con TodaySeries del schema."""
    return {
        "epochs": [],
        "temps": [],
        "humidities": [],
        "dewpts": [],
        "pressures": [],
        "pressures_abs": [],
        "uv_indexes": [],
        "solar_radiations": [],
        "winds": [],
        "gusts": [],
        "wind_dirs": [],
        "lat": float("nan"),
        "lon": float("nan"),
        "has_data": False,
        "daily_extremes": {},
    }


async def fetch_today_series(
    station_id: str,
    api_key: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 15.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Serie del día local (~48-288 puntos según cadencia de la estación)
    en el shape canónico ``TodaySeries``.

    Conversión de unidades igual que ``fetch_current``: viento m/s →
    km/h; presión absoluta → MSL (el campo canónico ``pressures`` es
    MSL para coherencia con WU/AEMET).
    """
    _require_api_key(api_key)
    station_id = str(station_id).strip().upper()

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)

    try:
        var_map = await _fetch_local_day_var_map(
            station_id, api_key, client, timeout_s=timeout_s, now=now,
        )
    finally:
        if owns_client:
            await client.aclose()

    if not var_map:
        return _empty_today_series()

    return _normalize_today_series(station_id, var_map)


def _normalize_today_series(station_id: str, var_map: VarMap) -> Dict[str, Any]:
    lat, lon, elevation, _name = _station_meta(station_id)

    # Unimos por epoch las series de cada variable (cada una puede tener
    # cadencia distinta; los huecos quedan NaN en esa posición).
    sources = {
        "temp": var_map.get(V_TEMP, []),
        "rh": var_map.get(V_RH, []),
        "p_abs": var_map.get(V_PRESSURE, []),
        "wind": _series_from_first_available(var_map, LATEST_VARIABLES["wind"]),
        "gust": _series_from_first_available(var_map, LATEST_VARIABLES["gust"]),
        "dir": _series_from_first_available(var_map, LATEST_VARIABLES["wind_dir"]),
        "solar": var_map.get(V_SOLAR, []),
        "uv": var_map.get(V_UV, []),
    }
    joined: Dict[int, Dict[str, float]] = {}
    for key, rows in sources.items():
        for epoch, value in rows:
            joined.setdefault(epoch, {})[key] = value

    epochs = sorted(joined.keys())
    if not epochs:
        return _empty_today_series()

    def _row(epoch: int, key: str) -> float:
        return joined[epoch].get(key, float("nan"))

    def _official_extreme(code: int, reducer) -> float:
        values = [
            _safe_float(value)
            for _epoch, value in var_map.get(code, [])
            if not _is_nan(_safe_float(value))
        ]
        return reducer(values) if values else float("nan")

    def _official_candidate_extreme(codes: List[int], reducer) -> float:
        # Una estación puede publicar la racha a 10, 6 o 2 m. Elegimos una
        # sola altura, en el mismo orden de preferencia que el dato actual.
        for code in codes:
            value = _official_extreme(code, reducer)
            if not _is_nan(value):
                return value
        return float("nan")

    return {
        "epochs": epochs,
        "temps": [_row(ep, "temp") for ep in epochs],
        "humidities": [_row(ep, "rh") for ep in epochs],
        # Dewpoint no se mide; el pipeline lo calcula de Tc+RH.
        "dewpts": [float("nan")] * len(epochs),
        "pressures": [_absolute_to_msl(_row(ep, "p_abs"), elevation) for ep in epochs],
        # Meteocat mide presión absoluta. La conservamos en el contrato
        # canónico para que Tendencias no tenga que reconstruirla desde MSL.
        "pressures_abs": [_row(ep, "p_abs") for ep in epochs],
        "uv_indexes": [_row(ep, "uv") for ep in epochs],
        "solar_radiations": [_non_negative(_row(ep, "solar")) for ep in epochs],
        "winds": [_ms_to_kmh(_row(ep, "wind")) for ep in epochs],
        "gusts": [_ms_to_kmh(_row(ep, "gust")) for ep in epochs],
        "wind_dirs": [_row(ep, "dir") for ep in epochs],
        "lat": lat,
        "lon": lon,
        "has_data": True,
        # Extremos oficiales de Meteocat. No se derivan de la variable 32:
        # Tx/Tn incorporan los extremos del intervalo que el gráfico de T
        # instantánea no necesariamente alcanza a muestrear.
        "daily_extremes": {
            "temp_max": _official_extreme(V_TEMP_MAX, max),
            "temp_min": _official_extreme(V_TEMP_MIN, min),
            "rh_max": _official_extreme(V_RH_MAX, max),
            "rh_min": _official_extreme(V_RH_MIN, min),
            "gust_max": _ms_to_kmh(
                _official_candidate_extreme(
                    [V_GUST, V_GUST_6M, V_GUST_2M], max,
                )
            ),
        },
    }


async def fetch_recent_series(
    station_id: str,
    api_key: str,
    *,
    days_back: int = 7,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 15.0,
    now: Optional[datetime] = None,
    fine: bool = False,
) -> Dict[str, Any]:
    """
    Serie reciente (T/HR/presión MSL) para tendencias: un fetch del
    endpoint de día por cada fecha UTC de la ventana (404 tolerados),
    remuestreado a buckets de 3 h.

    ``fine=True`` usa buckets de 1 h en vez de 3 h: lo pide el lookback de
    ``/series/today`` (la tendencia de presión 3h necesita un punto ~3 h
    antes de medianoche local; con buckets de 3 h alineados a UTC esos
    puntos quedan descolocados y la curva arranca tarde).
    """
    _require_api_key(api_key)
    station_id = str(station_id).strip().upper()
    lat, lon, elevation, _name = _station_meta(station_id)

    now_utc = (now or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)
    days = max(1, int(days_back))

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        async def _day(offset: int) -> VarMap:
            day = (now_utc - timedelta(days=offset)).date()
            url = f"{BASE_URL}/estacions/mesurades/{station_id}/{day.year:04d}/{day.month:02d}/{day.day:02d}"
            try:
                payload = await _get_json(client, url, api_key, timeout_s=timeout_s)
            except ProviderError as exc:
                if exc.error_code in ("provider_unauthorized", "provider_ratelimit"):
                    raise
                return {}
            return _parse_day_payload(payload)

        var_maps = list(await asyncio.gather(*(_day(o) for o in range(days + 1))))
    finally:
        if owns_client:
            await client.aclose()

    merged = _merge_var_maps(var_maps)
    cutoff = int(now_utc.timestamp()) - days * 86400
    buckets: Dict[int, tuple] = {}

    def _series(code: int) -> Dict[int, float]:
        return {ep: val for ep, val in merged.get(code, []) if ep >= cutoff}

    temps = _series(V_TEMP)
    rhs = _series(V_RH)
    pressures = _series(V_PRESSURE)
    bucket_s = 3600 if fine else 3 * 3600
    for epoch in set(temps) | set(rhs) | set(pressures):
        bucket = (epoch // bucket_s) * bucket_s
        current = buckets.get(bucket)
        if current is None or epoch >= current[0]:
            p_abs = pressures.get(epoch, float("nan"))
            buckets[bucket] = (
                epoch,
                temps.get(epoch, float("nan")),
                rhs.get(epoch, float("nan")),
                _absolute_to_msl(p_abs, elevation),
                p_abs,
            )

    epochs = sorted(buckets)
    if not epochs:
        return {
            "epochs": [], "temps": [], "humidities": [], "pressures": [],
            "lat": lat, "lon": lon, "has_data": False,
        }
    result = {
        "epochs": epochs,
        "temps": [buckets[b][1] for b in epochs],
        "humidities": [buckets[b][2] for b in epochs],
        "pressures": [buckets[b][3] for b in epochs],
        "lat": lat,
        "lon": lon,
        "has_data": True,
    }
    # En modo fino (lookback de /series/today) exponemos también la presión
    # ABSOLUTA: el today de Meteocat trae ``pressures_abs``, así que
    # ``derive_trend_series`` calcula la tendencia sobre abs (no cae al
    # fallback MSL); sin este campo los puntos de lookback quedarían NaN en
    # abs y la tendencia 3h no arrancaría a las 00:00. En el path sinóptico
    # (no-fino) NO se emite, para no cambiar la presión mostrada (MSL) allí.
    if fine:
        result["pressures_abs"] = [buckets[b][4] for b in epochs]
    return result
