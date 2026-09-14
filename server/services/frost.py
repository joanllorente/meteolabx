"""
Servicio puro de Frost (frost.met.no, MET Norway).

Versión "limpia" del cliente legacy (``services/frost.py``): sin
``streamlit``, cliente ``httpx.AsyncClient``.

Particularidades:

1. **Auth**: HTTP Basic con ``client_id``/``client_secret`` del
   servidor (``METEOLABX_FROST_CLIENT_ID`` / ``_SECRET``).

2. **Observaciones multi-elemento**: cada timestamp trae una lista de
   ``observations`` con variantes por resolución temporal (PT1M/PT10M/
   PT1H), nivel del sensor (2 m/10 m) y calidad. El scoring clonado del
   legacy (``_candidate_score``) elige la mejor variante por magnitud.

3. **Petición resiliente**: Frost responde 412 si ALGÚN elemento
   pedido no existe para la estación. Se intenta la petición combinada
   y, si falla con 4xx no-auth, se reintenta elemento a elemento en
   paralelo descartando los que fallen.

4. **Precipitación**: algunas estaciones reportan contador acumulado
   (``accumulated(...)``) y otras incrementos por intervalo. La total
   del día replica la heurística legacy (detección de resets del
   contador incluida).

5. **Presión**: Frost reporta absoluta (``surface_air_pressure``); la
   MSL se deriva con la altitud del catálogo. Día local: Europe/Oslo.
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

from data_files import FROST_STATIONS_PATH
from server.schemas.errors import ProviderError
from domain.parsing.common import find_station_by_field, load_stations_json, parse_epoch

logger = logging.getLogger(__name__)

PROVIDER = "FROST"
BASE_URL = "https://frost.met.no"
STATION_TZ = ZoneInfo("Europe/Oslo")

LATEST_ELEMENTS = (
    "accumulated(precipitation_amount)",
    "sum(precipitation_amount PT1M)",
    "sum(precipitation_amount PT1H)",
    "precipitation_amount",
    "surface_air_pressure",
    "wind_speed",
    "max(wind_speed_of_gust PT10M)",
    "max(wind_speed_of_gust PT1H)",
    "wind_speed_of_gust",
    "wind_from_direction",
    "relative_humidity",
    "air_temperature",
)

# Sensor del catálogo que respalda a cada elemento de Frost. Sirve para no
# pedirle a una estación variables que no mide: cuando Frost recibe un elemento
# que la estación no tiene, rechaza la petición ENTERA con un 412 y hay que
# reintentar uno por uno, de modo que una ficha pasa de 2 llamadas a 22. Con el
# rate limit de Frost eso se nota.
_ELEMENT_SENSOR = {
    "air_temperature": "thermometer",
    "relative_humidity": "hygrometer",
    "surface_air_pressure": "barometer",
    "wind_speed": "anemometer",
    "max(wind_speed_of_gust PT10M)": "anemometer",
    "max(wind_speed_of_gust PT1H)": "anemometer",
    "wind_speed_of_gust": "anemometer",
    "wind_from_direction": "wind_vane",
    "accumulated(precipitation_amount)": "rain_gauge",
    "sum(precipitation_amount PT1M)": "rain_gauge",
    "sum(precipitation_amount PT1H)": "rain_gauge",
    "precipitation_amount": "rain_gauge",
}

# Lo que de verdad respondió cada estación, aprendido del fan-out. El catálogo
# solo conoce los sensores de un tercio de las estaciones noruegas —2.191 de
# 3.462 los tienen todos a falso—, así que sin esta memoria el 63 % restante
# seguiría pagando el fan-out completo en cada visita. Clave: estación +
# conjunto de elementos pedido.
_WORKING_ELEMENTS_TTL_S = 24 * 60 * 60
_WORKING_ELEMENTS_MAX = 5000
_working_elements: "OrderedDict[Tuple[str, str], Tuple[float, Tuple[str, ...]]]" = OrderedDict()
_working_lock = threading.Lock()


def _remember_working(key: Tuple[str, str], elements: Tuple[str, ...]) -> None:
    with _working_lock:
        _working_elements[key] = (time.time() + _WORKING_ELEMENTS_TTL_S, elements)
        _working_elements.move_to_end(key)
        while len(_working_elements) > _WORKING_ELEMENTS_MAX:
            _working_elements.popitem(last=False)


def _recall_working(key: Tuple[str, str]) -> Optional[Tuple[str, ...]]:
    with _working_lock:
        entry = _working_elements.get(key)
        if entry is None:
            return None
        expires_at, elements = entry
        if expires_at <= time.time():
            del _working_elements[key]
            return None
        _working_elements.move_to_end(key)
        return elements


def _forget_working(key: Tuple[str, str]) -> None:
    with _working_lock:
        _working_elements.pop(key, None)


def _catalog_sensors(station_id: str) -> Dict[str, bool]:
    """Sensores que el catálogo atribuye a la estación, o ``{}`` si no consta.

    Import perezoso para no acoplar el servicio al catálogo en tiempo de carga.
    """
    try:
        from server.services import stations as stations_svc

        record = stations_svc.get_station(PROVIDER, station_id) or {}
    except Exception:  # noqa: BLE001 — el catálogo nunca debe tumbar una consulta
        return {}
    sensors = record.get("sensors")
    return sensors if isinstance(sensors, dict) else {}


def _elements_for_station(
    station_id: str, elements: Tuple[str, ...],
) -> Tuple[str, ...]:
    """Recorta la lista a lo que la estación puede medir según el catálogo.

    Si el catálogo no marca NINGÚN sensor no se filtra nada: eso no significa
    que la estación no mida nada, sino que no lo sabemos, y recortar ahí
    dejaría sin datos a las dos terceras partes de la red noruega.
    """
    sensors = _catalog_sensors(station_id)
    if not any(bool(value) for value in sensors.values()):
        return elements
    filtrados = tuple(
        element for element in elements
        if _ELEMENT_SENSOR.get(element) is None
        or bool(sensors.get(_ELEMENT_SENSOR[element]))
    )
    return filtrados or elements


_CANONICAL_BY_ELEMENT = {
    "air_temperature": "temp_c",
    "relative_humidity": "rh",
    "surface_air_pressure": "p_abs_hpa",
    "wind_speed": "wind_ms",
    "max(wind_speed_of_gust PT10M)": "gust_ms",
    "max(wind_speed_of_gust PT1H)": "gust_ms",
    "wind_speed_of_gust": "gust_ms",
    "wind_from_direction": "wind_dir_deg",
    "accumulated(precipitation_amount)": "precip_accum_mm",
    "sum(precipitation_amount PT1M)": "precip_step_mm",
    "sum(precipitation_amount PT1H)": "precip_step_mm",
    "precipitation_amount": "precip_step_mm",
}


# =====================================================================
# Helpers numéricos
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


def _ms_to_kmh(value: float) -> float:
    return float(value) * 3.6 if not _is_nan(value) else float("nan")


def _to_iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _absolute_to_msl(p_abs_hpa: float, elevation_m: float) -> float:
    if _is_nan(p_abs_hpa) or _is_nan(elevation_m):
        return float("nan")
    return float(p_abs_hpa) * math.exp(float(elevation_m) / 8000.0)


# =====================================================================
# Catálogo local
# =====================================================================

@lru_cache(maxsize=1)
def _load_stations() -> List[Dict[str, Any]]:
    try:
        return load_stations_json(str(FROST_STATIONS_PATH))
    except Exception as exc:
        logger.warning("Catálogo Frost no disponible (%s)", exc)
        return []


def _station_meta(station_id: str) -> Tuple[float, float, float, str]:
    station = find_station_by_field(_load_stations(), field="id", target=station_id)
    return (
        _safe_float(station.get("lat")),
        _safe_float(station.get("lon")),
        _safe_float(station.get("elev")),
        str(station.get("name", "") or "").strip(),
    )


# =====================================================================
# Scoring de variantes por magnitud (clonado del legacy)
# =====================================================================

def _level_value(obs: Dict[str, Any]) -> Optional[float]:
    level = obs.get("level")
    if not isinstance(level, dict):
        return None
    value = _safe_float(level.get("value"))
    return None if _is_nan(value) else float(value)


def _resolution_rank(resolution: str, canonical: str) -> int:
    res = str(resolution or "").strip().upper()
    if canonical in {"temp_c", "rh", "p_abs_hpa"}:
        order = {"PT1M": 4, "PT10M": 3, "PT1H": 2, "PT6H": 1}
    elif canonical in {"wind_ms", "gust_ms", "wind_dir_deg"}:
        order = {"PT1M": 4, "PT10M": 3, "PT1H": 2}
    elif canonical == "precip_accum_mm":
        order = {"PT10M": 4, "PT1H": 3, "PT1M": 2, "PT12H": 1}
    else:
        order = {}
    return int(order.get(res, 0))


def _level_rank(level_value: Optional[float], canonical: str) -> int:
    if level_value is None:
        return 0
    if canonical in {"temp_c", "rh", "p_abs_hpa"}:
        if abs(level_value - 2.0) < 0.01:
            return 3
        if abs(level_value - 10.0) < 0.01:
            return 2
    if canonical in {"wind_ms", "gust_ms", "wind_dir_deg"}:
        if abs(level_value - 10.0) < 0.01:
            return 3
        if abs(level_value - 2.0) < 0.01:
            return 2
    return 1


def _quality_rank(obs: Dict[str, Any]) -> int:
    try:
        quality = int(obs.get("qualityCode"))
    except Exception:
        return 0
    return max(0, 10 - quality)


def _quality_acceptable(obs: Dict[str, Any]) -> bool:
    """Acepta calidad Frost 0/1 (controlada, incluida corrección válida).

    La ausencia de código se conserva por compatibilidad con series antiguas;
    los códigos explícitos 2/4/5/6/7 no son suficientemente fiables para UI.
    """
    quality = obs.get("qualityCode")
    if quality in (None, ""):
        return True
    try:
        return int(quality) in (0, 1)
    except (TypeError, ValueError):
        return False


def _candidate_score(obs: Dict[str, Any], canonical: str) -> Tuple[int, int, int]:
    return (
        _resolution_rank(str(obs.get("timeResolution", "")), canonical),
        _level_rank(_level_value(obs), canonical),
        _quality_rank(obs),
    )


def _choose_observation(observations: List[Dict[str, Any]], canonical: str) -> Optional[Dict[str, Any]]:
    candidates = [
        obs for obs in observations
        if _CANONICAL_BY_ELEMENT.get(str(obs.get("elementId", "")).strip()) == canonical
        and _quality_acceptable(obs)
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda obs: (
            int(obs.get("_reference_epoch", 0)),
            *_candidate_score(obs, canonical),
        ),
        reverse=True,
    )
    return candidates[0]


def _build_row(reference_epoch: int, observations: List[Dict[str, Any]]) -> Dict[str, float]:
    def _value(canonical: str) -> float:
        obs = _choose_observation(observations, canonical)
        return _safe_float(obs.get("value")) if isinstance(obs, dict) else float("nan")

    return {
        "epoch": int(reference_epoch),
        "temp_c": _value("temp_c"),
        "rh": _value("rh"),
        "p_abs_hpa": _value("p_abs_hpa"),
        "wind_kmh": _ms_to_kmh(_value("wind_ms")),
        "gust_kmh": _ms_to_kmh(_value("gust_ms")),
        "wind_dir_deg": _value("wind_dir_deg"),
        "precip_accum_mm": _value("precip_accum_mm"),
        "precip_step_mm": _value("precip_step_mm"),
    }


def _bin_rows(payload: Dict[str, Any], *, bin_seconds: int) -> List[Dict[str, float]]:
    """Agrupa observaciones en bins y elige la mejor variante por bin."""
    bins: Dict[int, List[Dict[str, Any]]] = {}
    for item in payload.get("data", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        epoch = parse_epoch(item.get("referenceTime"))
        if epoch is None:
            continue
        observations = item.get("observations", [])
        if not isinstance(observations, list):
            continue
        bucket_epoch = (int(epoch) // bin_seconds) * bin_seconds
        bucket = bins.setdefault(bucket_epoch, [])
        for obs in observations:
            if not isinstance(obs, dict):
                continue
            obs_copy = dict(obs)
            obs_copy["_reference_epoch"] = int(epoch)
            bucket.append(obs_copy)

    return [_build_row(epoch, bins[epoch]) for epoch in sorted(bins)]


# =====================================================================
# Precipitación del día (heurística legacy con resets de contador)
# =====================================================================

def _precip_total_from_accum(values: List[float]) -> float:
    clean = [float(v) for v in values if not _is_nan(_safe_float(v))]
    if len(clean) < 2:
        return 0.0 if clean else float("nan")
    diffs = [clean[i] - clean[i - 1] for i in range(1, len(clean))]
    non_negative_ratio = (
        sum(1 for diff in diffs if diff >= -0.1) / len(diffs) if diffs else 1.0
    )
    if non_negative_ratio >= 0.65:
        return max(0.0, clean[-1] - clean[0])
    total = 0.0
    for prev, current in zip(clean, clean[1:]):
        diff = current - prev
        total += diff if diff >= 0 else max(0.0, current)
    return max(0.0, total)


def _precip_total_from_steps(values: List[float]) -> float:
    clean = [max(0.0, float(v)) for v in values if not _is_nan(_safe_float(v))]
    return max(0.0, sum(clean)) if clean else float("nan")


def _precip_total(values_accum: List[float], values_step: List[float]) -> float:
    accum_total = _precip_total_from_accum(values_accum)
    if not _is_nan(accum_total) and accum_total > 0:
        return accum_total
    step_total = _precip_total_from_steps(values_step)
    if not _is_nan(step_total):
        return step_total
    return accum_total


# =====================================================================
# HTTP resiliente
# =====================================================================

def _require_credentials(client_id: str, client_secret: str) -> None:
    if not client_id or not client_secret:
        raise ProviderError(
            "provider_unauthorized",
            provider=PROVIDER,
            detail="Missing FROST_CLIENT_ID / FROST_CLIENT_SECRET",
            status_code=401,
        )


async def _get_observations(
    client: httpx.AsyncClient,
    params: Dict[str, Any],
    client_id: str,
    client_secret: str,
    *,
    timeout_s: float,
) -> Dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "MeteoLabX/1.0 (+https://meteolabx.com)",
    }
    try:
        response = await client.get(
            f"{BASE_URL}/observations/v0.jsonld",
            params=params,
            headers=headers,
            auth=(client_id, client_secret),
            timeout=timeout_s,
        )
    except httpx.TimeoutException as exc:
        raise ProviderError(
            "provider_timeout",
            provider=PROVIDER,
            detail=f"Frost timeout: {exc}",
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
            detail=f"Frost auth rechazada (HTTP {status})",
            status_code=401,
        )
    if status == 429:
        raise ProviderError(
            "provider_ratelimit",
            provider=PROVIDER,
            detail="Frost rate limit (HTTP 429)",
            status_code=429,
        )
    if status >= 400:
        # 404/412 de Frost = "no hay datos para esos elementos/ventana";
        # el caller decide si reintentar por-elemento.
        raise ProviderError(
            "provider_http_error",
            provider=PROVIDER,
            detail=f"HTTP {status}",
            status_code=502,
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail=f"JSON inválido: {exc!r}",
            status_code=502,
        ) from exc
    return payload if isinstance(payload, dict) else {"data": []}


def _merge_payloads(payloads: List[Dict[str, Any]]) -> Dict[str, Any]:
    merged_items: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for payload in payloads:
        for item in payload.get("data", []) if isinstance(payload, dict) else []:
            if not isinstance(item, dict):
                continue
            key = (
                str(item.get("sourceId", "")).strip(),
                str(item.get("referenceTime", "")).strip(),
            )
            target = merged_items.setdefault(
                key,
                {
                    "sourceId": item.get("sourceId"),
                    "referenceTime": item.get("referenceTime"),
                    "observations": [],
                },
            )
            observations = item.get("observations", [])
            if isinstance(observations, list):
                target["observations"].extend(
                    obs for obs in observations if isinstance(obs, dict)
                )
    merged_data = sorted(
        merged_items.values(),
        key=lambda item: parse_epoch(item.get("referenceTime")) or 0,
    )
    return {"data": merged_data}


async def _request_observations_resilient(
    station_id: str,
    client_id: str,
    client_secret: str,
    client: httpx.AsyncClient,
    *,
    referencetime: str,
    elements: Tuple[str, ...],
    maxage: str = "",
    timeout_s: float,
) -> Dict[str, Any]:
    """
    Petición combinada; si Frost la rechaza (412 cuando algún elemento
    no existe para la estación), fan-out por-elemento en paralelo
    descartando los que fallen. Auth/ratelimit se propagan siempre.
    """
    source = str(station_id).strip().upper()
    # 1) Lo que ya sabemos que contesta esta estación (aprendido de un fan-out
    #    anterior). 2) Si no, lo que el catálogo dice que mide. 3) Si tampoco,
    #    la lista entera.
    memoria_key = (source, ",".join(elements))
    recordados = _recall_working(memoria_key)
    pedidos = recordados or _elements_for_station(source, elements)

    base_params: Dict[str, Any] = {
        "sources": source,
        "referencetime": referencetime,
        "elements": ",".join(pedidos),
    }
    if maxage:
        base_params["maxage"] = maxage

    try:
        return await _get_observations(
            client, base_params, client_id, client_secret, timeout_s=timeout_s,
        )
    except ProviderError as exc:
        if exc.error_code in ("provider_unauthorized", "provider_ratelimit"):
            raise
        if recordados:
            # Lo recordado ha dejado de valer (la estación cambió de sensores o
            # la memoria era de un día raro): se vuelve a partir de cero.
            _forget_working(memoria_key)
            pedidos = _elements_for_station(source, elements)

    async def _one(element_id: str) -> Optional[Dict[str, Any]]:
        params = dict(base_params)
        params["elements"] = element_id
        try:
            payload = await _get_observations(
                client, params, client_id, client_secret, timeout_s=timeout_s,
            )
        except ProviderError as exc:
            if exc.error_code in ("provider_unauthorized", "provider_ratelimit"):
                raise
            return None
        return payload if payload.get("data") else None

    results = await asyncio.gather(*(_one(element) for element in pedidos))
    # Los que han contestado son los que se pedirán la próxima vez: la ficha
    # pasa de 22 llamadas a 1 mientras la memoria siga viva.
    funcionan = tuple(
        element for element, payload in zip(pedidos, results) if payload is not None
    )
    if funcionan:
        _remember_working(memoria_key, funcionan)
    payloads = [payload for payload in results if payload is not None]
    return _merge_payloads(payloads) if payloads else {"data": []}


def _local_day_window(now: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    now_local = (now or datetime.now(tz=STATION_TZ)).astimezone(STATION_TZ)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local, start_local + timedelta(days=1)


# =====================================================================
# API pública del servicio
# =====================================================================

async def fetch_current(
    station_id: str,
    client_id: str,
    client_secret: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 18.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Observación actual: ``referencetime=latest`` (maxage 12 h) con
    fallback campo a campo a la serie del día. ``precip_total`` con la
    heurística contador/incrementos del legacy.
    """
    _require_credentials(client_id, client_secret)
    station_id = str(station_id).strip().upper()
    lat, lon, elevation, name = _station_meta(station_id)
    day_start, day_end = _local_day_window(now)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        latest_task = _request_observations_resilient(
            station_id, client_id, client_secret, client,
            referencetime="latest", elements=LATEST_ELEMENTS,
            maxage="PT12H", timeout_s=timeout_s,
        )
        today_task = _request_observations_resilient(
            station_id, client_id, client_secret, client,
            referencetime=f"{_to_iso_z(day_start)}/{_to_iso_z(day_end)}",
            elements=LATEST_ELEMENTS, timeout_s=timeout_s,
        )
        latest_result, today_result = await asyncio.gather(
            latest_task, today_task, return_exceptions=True,
        )
    finally:
        if owns_client:
            await client.aclose()

    if isinstance(latest_result, BaseException) and isinstance(today_result, BaseException):
        raise latest_result if isinstance(latest_result, ProviderError) else today_result
    latest_payload = {} if isinstance(latest_result, BaseException) else latest_result
    today_payload = {} if isinstance(today_result, BaseException) else today_result

    latest_rows = _bin_rows(latest_payload, bin_seconds=60)
    today_rows = _bin_rows(today_payload, bin_seconds=600)

    if not latest_rows and not today_rows:
        # El proveedor contestó; es la estación la que no publica. El código lo
        # dice para que la ficha no acuse a la red de estar incomunicada.
        raise ProviderError(
            "provider_no_current_data",
            provider=PROVIDER,
            detail=f"Frost sin observaciones para {station_id}",
            status_code=502,
        )

    current = latest_rows[-1] if latest_rows else today_rows[-1]

    def _value(key: str) -> float:
        value = _safe_float(current.get(key))
        if not _is_nan(value):
            return value
        for row in reversed(today_rows):
            row_value = _safe_float(row.get(key))
            if not _is_nan(row_value):
                return row_value
        return float("nan")

    precip_total = _precip_total(
        [row.get("precip_accum_mm", float("nan")) for row in today_rows],
        [row.get("precip_step_mm", float("nan")) for row in today_rows],
    )

    p_abs = _value("p_abs_hpa")
    epoch = int(current.get("epoch") or 0) or int(datetime.now(tz=timezone.utc).timestamp())
    dt_utc = datetime.fromtimestamp(epoch, tz=timezone.utc)

    observation: Dict[str, Any] = {
        "Tc": _value("temp_c"),
        "RH": _value("rh"),
        "p_hpa": _absolute_to_msl(p_abs, elevation),
        "p_abs_hpa": p_abs,
        "wind": _value("wind_kmh"),
        "gust": _value("gust_kmh"),
        "wind_dir_deg": _value("wind_dir_deg"),
        "Td": float("nan"),
        "feels_like": float("nan"),
        "heat_index": float("nan"),
        "wind_chill": float("nan"),
        "precip_rate": float("nan"),
        "precip_total": precip_total,
        "solar_radiation": float("nan"),
        "uv": float("nan"),
        "epoch": epoch,
        "time_local": dt_utc.astimezone(STATION_TZ).isoformat(),
        "time_utc": dt_utc.isoformat(),
        "lat": lat,
        "lon": lon,
        "elevation": elevation,
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


TREND_ELEMENTS = (
    "surface_air_pressure",
    "relative_humidity",
    "air_temperature",
)


async def fetch_recent_series(
    station_id: str,
    client_id: str,
    client_secret: str,
    *,
    days_back: int = 7,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 18.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Serie reciente (T/HR/presión MSL) binned a 1 h para tendencias.
    Solo pide los elementos de tendencia (menos payload que el día).
    """
    _require_credentials(client_id, client_secret)
    station_id = str(station_id).strip().upper()
    lat, lon, elevation, _name = _station_meta(station_id)

    now_local = (now or datetime.now(tz=STATION_TZ)).astimezone(STATION_TZ)
    start_local = now_local - timedelta(days=max(1, int(days_back)))

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        payload = await _request_observations_resilient(
            station_id, client_id, client_secret, client,
            referencetime=f"{_to_iso_z(start_local)}/{_to_iso_z(now_local)}",
            elements=TREND_ELEMENTS, timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()

    rows = _bin_rows(payload, bin_seconds=3600)
    if not rows:
        return {
            "epochs": [], "temps": [], "humidities": [], "pressures": [],
            "lat": lat, "lon": lon, "has_data": False,
        }
    return {
        "epochs": [int(row["epoch"]) for row in rows],
        "temps": [_safe_float(row.get("temp_c")) for row in rows],
        "humidities": [_safe_float(row.get("rh")) for row in rows],
        "pressures": [
            _absolute_to_msl(_safe_float(row.get("p_abs_hpa")), elevation)
            for row in rows
        ],
        "lat": lat,
        "lon": lon,
        "has_data": True,
    }


async def fetch_today_series(
    station_id: str,
    client_id: str,
    client_secret: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 18.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Serie del día local (Europe/Oslo) binned a 10 min, shape canónico."""
    _require_credentials(client_id, client_secret)
    station_id = str(station_id).strip().upper()
    lat, lon, elevation, _name = _station_meta(station_id)
    day_start, day_end = _local_day_window(now)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        payload = await _request_observations_resilient(
            station_id, client_id, client_secret, client,
            referencetime=f"{_to_iso_z(day_start)}/{_to_iso_z(day_end)}",
            elements=LATEST_ELEMENTS, timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()

    rows = _bin_rows(payload, bin_seconds=600)
    if not rows:
        return _empty_today_series()

    def _col(key: str) -> List[float]:
        return [_safe_float(row.get(key)) for row in rows]

    return {
        "epochs": [int(row["epoch"]) for row in rows],
        "temps": _col("temp_c"),
        "humidities": _col("rh"),
        "dewpts": [float("nan")] * len(rows),
        "pressures": [_absolute_to_msl(_safe_float(row.get("p_abs_hpa")), elevation) for row in rows],
        "uv_indexes": [float("nan")] * len(rows),
        "solar_radiations": [float("nan")] * len(rows),
        "winds": _col("wind_kmh"),
        "gusts": _col("gust_kmh"),
        "wind_dirs": _col("wind_dir_deg"),
        "lat": lat,
        "lon": lon,
        "has_data": True,
    }
