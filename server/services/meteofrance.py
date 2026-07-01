"""
Servicio puro de Météo-France (DPObs).

Versión "limpia" del cliente legacy (``services/meteofrance.py``): sin
``streamlit``, cliente ``httpx.AsyncClient``.

Particularidades:

1. **Auth**: API key del servidor (``METEOLABX_METEOFRANCE_API_KEY``)
   en el header ``apikey``. Cuota de 50 req/min → fan-out con semáforo
   conservador.

2. **Modelo por instante**: ``/station/horaire`` devuelve UNA fila por
   fecha pedida; la serie del día exige una petición por hora (el
   legacy lo hace secuencial; aquí van en paralelo limitado).
   ``/station/infrahoraire-6m`` da la observación más fresca (se
   prueban la hora actual y hasta 3 anteriores).

3. **Unidades del feed**: temperatura/rocío en Kelvin, presiones en
   Pa (``pres`` absoluta, ``pmer`` MSL), viento en m/s. Si falta una
   presión se deriva de la otra con la altitud del catálogo.

4. **Td nativo**: como NWS, se preserva el dewpoint del proveedor en
   vez del calculado por ``add_basic_derived``.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

from data_files import METEOFRANCE_STATIONS_PATH
from server.schemas.errors import ProviderError
from domain.parsing.common import find_station_by_field, load_stations_json, parse_epoch

logger = logging.getLogger(__name__)

PROVIDER = "METEOFRANCE"
BASE_URL = "https://public-api.meteofrance.fr/public/DPObs/v1"
LOCAL_TZ = ZoneInfo("Europe/Paris")

# Cuota Météo-France: 50 req/min. El fan-out diario son ≤24 requests.
MAX_CONCURRENT_REQUESTS = 6


# =====================================================================
# Helpers numéricos
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


def _k_to_c(value: Any) -> float:
    v = _safe_float(value)
    return v - 273.15 if not _is_nan(v) else float("nan")


def _pa_to_hpa(value: Any) -> float:
    v = _safe_float(value)
    return v / 100.0 if not _is_nan(v) else float("nan")


def _ms_to_kmh(value: Any) -> float:
    v = _safe_float(value)
    return v * 3.6 if not _is_nan(v) else float("nan")


def _first_valid(*values: Any) -> float:
    for value in values:
        v = _safe_float(value)
        if not _is_nan(v):
            return v
    return float("nan")


def _jm2_to_w_m2(joules_m2: float, period_s: float) -> float:
    """Energía del periodo (J/m²) → irradiancia media (W/m²)."""
    if _is_nan(joules_m2) or period_s <= 0:
        return float("nan")
    return max(0.0, float(joules_m2) / float(period_s))


def _solar_w_m2(
    current: Dict[str, Any],
    hourly_rows: List[Dict[str, Any]],
    *,
    current_period_s: float,
) -> float:
    """
    Irradiancia actual: ray_glo01 del ``current`` dividido por los
    segundos de su cadencia (360 si vino del 6-minutal, 3600 si vino
    del fallback horario); si falta, la última fila horaria con dato.
    """
    value = _jm2_to_w_m2(_safe_float(current.get("solar_jm2")), current_period_s)
    if not _is_nan(value):
        return value
    for row in reversed(hourly_rows):
        value = _jm2_to_w_m2(_safe_float(row.get("solar_jm2")), 3600.0)
        if not _is_nan(value):
            return value
    return float("nan")


def _utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# =====================================================================
# Catálogo local
# =====================================================================

@lru_cache(maxsize=1)
def _load_stations() -> List[Dict[str, Any]]:
    try:
        return load_stations_json(str(METEOFRANCE_STATIONS_PATH))
    except Exception as exc:
        logger.warning("Catálogo Météo-France no disponible (%s)", exc)
        return []


def _station_meta(station_id: str) -> Tuple[float, float, float, str]:
    """→ (lat, lon, elevation, nombre). id_station preserva dígitos."""
    station = find_station_by_field(
        _load_stations(), field="id_station", target=station_id, case_insensitive=False,
    )
    return (
        _safe_float(station.get("lat")),
        _safe_float(station.get("lon")),
        _safe_float(station.get("elev")),
        str(station.get("name", "") or "").strip(),
    )


# =====================================================================
# HTTP
# =====================================================================

def _require_api_key(api_key: str) -> None:
    if not api_key:
        raise ProviderError(
            "provider_unauthorized",
            provider=PROVIDER,
            detail="Missing METEOFRANCE_API_KEY",
            status_code=401,
        )


async def _get_json(
    client: httpx.AsyncClient,
    path: str,
    params: Dict[str, Any],
    api_key: str,
    *,
    timeout_s: float,
) -> Any:
    headers = {
        "Accept": "application/json",
        "apikey": api_key,
        "User-Agent": "MeteoLabX/1.0",
    }
    try:
        response = await client.get(
            f"{BASE_URL}{path}", params=params, headers=headers, timeout=timeout_s,
        )
    except httpx.TimeoutException as exc:
        raise ProviderError(
            "provider_timeout",
            provider=PROVIDER,
            detail=f"Météo-France timeout: {exc}",
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
            detail=f"Météo-France auth rechazada (HTTP {status})",
            status_code=401,
        )
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
            detail="Météo-France rate limit (HTTP 429)",
            status_code=429,
        )
    if status >= 400:
        raise ProviderError(
            "provider_http_error",
            provider=PROVIDER,
            detail=f"HTTP {status} en {path}",
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


# =====================================================================
# Parsing de filas DPObs (Kelvin/Pa/m/s → °C/hPa/km/h)
# =====================================================================

def _parse_obs_row(row: Dict[str, Any], elevation_m: float) -> Dict[str, Any]:
    epoch = (
        parse_epoch(row.get("validity_time"))
        or parse_epoch(row.get("reference_time"))
        or parse_epoch(row.get("insert_time"))
    )
    p_abs = _pa_to_hpa(row.get("pres"))
    p_msl = _pa_to_hpa(row.get("pmer"))
    if _is_nan(p_abs) and not _is_nan(p_msl):
        p_abs = float(p_msl) / math.exp(float(elevation_m or 0.0) / 8000.0)
    if _is_nan(p_msl) and not _is_nan(p_abs):
        p_msl = float(p_abs) * math.exp(float(elevation_m or 0.0) / 8000.0)

    gust_3s = _ms_to_kmh(_first_valid(
        row.get("fxi3s"), row.get("fxi3"), row.get("fxi_3s"), row.get("FXI3s"),
    ))
    gust_10m = _ms_to_kmh(row.get("fxi10"))
    gust_instant = _ms_to_kmh(row.get("fxi"))
    gust_other = _ms_to_kmh(row.get("fxy"))

    return {
        "epoch": int(epoch) if epoch is not None else None,
        "lat": _safe_float(row.get("lat")),
        "lon": _safe_float(row.get("lon")),
        "temp_c": _k_to_c(row.get("t")),
        "temp_max_c": _k_to_c(row.get("tx")),
        "temp_min_c": _k_to_c(row.get("tn")),
        "dewpoint_c": _k_to_c(row.get("td")),
        "rh": _safe_float(row.get("u")),
        "rh_max": _safe_float(row.get("ux")),
        "rh_min": _safe_float(row.get("un")),
        "p_abs_hpa": p_abs,
        "p_msl_hpa": p_msl,
        "wind_kmh": _ms_to_kmh(row.get("ff")),
        "gust_kmh": _first_valid(gust_3s, gust_10m, gust_instant, gust_other),
        "gust_3s_kmh": gust_3s,
        "gust_10m_kmh": gust_10m,
        "gust_instant_kmh": gust_instant,
        "gust_other_kmh": gust_other,
        "wind_dir_deg": _safe_float(row.get("dd")),
        "precip_mm": _first_valid(row.get("rr_per"), row.get("rr1")),
        # Radiación global del periodo en J/m² (ray_glo01). La media en
        # W/m² depende de la cadencia: el caller divide por los segundos
        # del periodo (3600 horaire, 360 infrahoraire-6m).
        "solar_jm2": _safe_float(row.get("ray_glo01")),
    }


# =====================================================================
# Fetchers
# =====================================================================

async def _fetch_latest_6m_row(
    station_id: str,
    api_key: str,
    client: httpx.AsyncClient,
    *,
    elevation_m: float,
    timeout_s: float,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """
    Última observación 6-minutal: prueba la hora actual UTC y hasta 3
    anteriores (igual que el legacy). ``None`` si ninguna trae datos.
    """
    now_utc = (now or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)
    current_hour = now_utc.replace(minute=0, second=0, microsecond=0)
    last_error: Optional[ProviderError] = None

    for offset in range(4):
        candidate = current_hour - timedelta(hours=offset)
        try:
            payload = await _get_json(
                client,
                "/station/infrahoraire-6m",
                {"id_station": station_id, "date": _utc_iso(candidate), "format": "json"},
                api_key,
                timeout_s=timeout_s,
            )
        except ProviderError as exc:
            if exc.error_code in ("provider_unauthorized", "provider_ratelimit"):
                raise
            last_error = exc
            continue
        if isinstance(payload, list) and payload:
            rows = [
                _parse_obs_row(item, elevation_m)
                for item in payload
                if isinstance(item, dict)
            ]
            rows = [row for row in rows if row.get("epoch") is not None]
            if rows:
                return max(rows, key=lambda row: row["epoch"])

    if last_error is not None:
        raise last_error
    return None


async def _fetch_today_hourly_rows(
    station_id: str,
    api_key: str,
    client: httpx.AsyncClient,
    *,
    elevation_m: float,
    timeout_s: float,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    Filas horarias del día local (una petición por hora, en paralelo
    limitado). Horas sin datos publicados se toleran; auth/ratelimit se
    propagan.
    """
    now_local = (now or datetime.now(tz=LOCAL_TZ)).astimezone(LOCAL_TZ)
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    hours = [day_start + timedelta(hours=h) for h in range(now_local.hour + 1)]

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    async def _one(hour_dt: datetime) -> Optional[Dict[str, Any]]:
        async with semaphore:
            try:
                payload = await _get_json(
                    client,
                    "/station/horaire",
                    {"id_station": station_id, "date": _utc_iso(hour_dt), "format": "json"},
                    api_key,
                    timeout_s=timeout_s,
                )
            except ProviderError as exc:
                if exc.error_code in ("provider_unauthorized", "provider_ratelimit"):
                    raise
                return None
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            row = _parse_obs_row(payload[0], elevation_m)
            if row.get("epoch") is not None:
                return row
        return None

    results = await asyncio.gather(*(_one(hour) for hour in hours))
    rows = {row["epoch"]: row for row in results if row is not None}

    # Recorte al día local (validity_time puede caer fuera de la hora pedida).
    start_epoch = int(day_start.timestamp())
    end_epoch = int((day_start + timedelta(days=1)).timestamp())
    return [rows[ep] for ep in sorted(rows) if start_epoch <= ep < end_epoch]


# =====================================================================
# API pública del servicio
# =====================================================================

async def fetch_current(
    station_id: str,
    api_key: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 18.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Observación actual: 6-minutal preferente con fallback campo a campo
    a la última fila horaria del día. ``precip_total`` = suma de
    precipitación horaria del día local.
    """
    _require_api_key(api_key)
    station_id = str(station_id).strip()
    lat0, lon0, elevation, name = _station_meta(station_id)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        latest_task = _fetch_latest_6m_row(
            station_id, api_key, client,
            elevation_m=elevation, timeout_s=timeout_s, now=now,
        )
        hourly_task = _fetch_today_hourly_rows(
            station_id, api_key, client,
            elevation_m=elevation, timeout_s=timeout_s, now=now,
        )
        latest_result, hourly_result = await asyncio.gather(
            latest_task, hourly_task, return_exceptions=True,
        )
    finally:
        if owns_client:
            await client.aclose()

    if isinstance(latest_result, BaseException) and isinstance(hourly_result, BaseException):
        raise latest_result if isinstance(latest_result, ProviderError) else hourly_result
    latest_row = None if isinstance(latest_result, BaseException) else latest_result
    hourly_rows = [] if isinstance(hourly_result, BaseException) else hourly_result

    current = latest_row or (hourly_rows[-1] if hourly_rows else None)
    if current is None:
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail=f"Météo-France sin observaciones para {station_id}",
            status_code=502,
        )

    def _value(key: str) -> float:
        value = _safe_float(current.get(key))
        if not _is_nan(value):
            return value
        for row in reversed(hourly_rows):
            row_value = _safe_float(row.get(key))
            if not _is_nan(row_value):
                return row_value
        return float("nan")

    precip_vals = [
        max(0.0, _safe_float(row.get("precip_mm")))
        for row in hourly_rows
        if not _is_nan(_safe_float(row.get("precip_mm")))
    ]
    precip_total = float(sum(precip_vals)) if precip_vals else float("nan")

    epoch = int(current.get("epoch") or 0) or int(datetime.now(tz=timezone.utc).timestamp())
    dt_utc = datetime.fromtimestamp(epoch, tz=timezone.utc)
    lat = _safe_float(current.get("lat"))
    lon = _safe_float(current.get("lon"))

    observation: Dict[str, Any] = {
        "Tc": _value("temp_c"),
        "RH": _value("rh"),
        "p_hpa": _value("p_msl_hpa"),
        "p_abs_hpa": _value("p_abs_hpa"),
        "wind": _value("wind_kmh"),
        "gust": _value("gust_kmh"),
        "wind_dir_deg": _value("wind_dir_deg"),
        "Td": _value("dewpoint_c"),  # nativo del feed (Kelvin → °C)
        "feels_like": float("nan"),
        "heat_index": float("nan"),
        "wind_chill": float("nan"),
        "precip_rate": float("nan"),
        "precip_total": precip_total,
        "solar_radiation": _solar_w_m2(
            current, hourly_rows,
            current_period_s=360.0 if latest_row is not None else 3600.0,
        ),
        "uv": float("nan"),
        "epoch": epoch,
        "time_local": dt_utc.astimezone(LOCAL_TZ).isoformat(),
        "time_utc": dt_utc.isoformat(),
        "lat": lat if not _is_nan(lat) else lat0,
        "lon": lon if not _is_nan(lon) else lon0,
        "elevation": elevation,
        "station_name": name or station_id,
    }

    # Preservar el Td nativo frente al recalculado por el pipeline.
    native_td = observation["Td"]
    from domain.observation_pipeline import add_basic_derived
    derived = add_basic_derived(observation)
    if not _is_nan(_safe_float(native_td)):
        derived["Td"] = native_td
    return derived


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
        "daily_extremes": {},
    }


async def fetch_today_series(
    station_id: str,
    api_key: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 18.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Serie horaria del día local en shape canónico (``dewpts`` nativos)."""
    _require_api_key(api_key)
    station_id = str(station_id).strip()
    lat0, lon0, elevation, _name = _station_meta(station_id)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        rows = await _fetch_today_hourly_rows(
            station_id, api_key, client,
            elevation_m=elevation, timeout_s=timeout_s, now=now,
        )
    finally:
        if owns_client:
            await client.aclose()

    if not rows:
        return _empty_today_series()

    def _col(key: str) -> List[float]:
        return [_safe_float(row.get(key)) for row in rows]

    def _official_extreme(key: str, reducer) -> float:
        values = [value for value in _col(key) if not _is_nan(value)]
        return reducer(values) if values else float("nan")

    def _official_candidate_extreme(keys: List[str], reducer) -> float:
        # Prioridad por serie completa: si existe FXI3s en algún punto del
        # día, el máximo se calcula solo con FXI3s. No se mezclan métricas.
        for key in keys:
            value = _official_extreme(key, reducer)
            if not _is_nan(value):
                return value
        return float("nan")

    lats = [v for v in _col("lat") if not _is_nan(v)]
    lons = [v for v in _col("lon") if not _is_nan(v)]

    return {
        "epochs": [int(row["epoch"]) for row in rows],
        "temps": _col("temp_c"),
        "humidities": _col("rh"),
        "dewpts": _col("dewpoint_c"),
        "pressures": _col("p_msl_hpa"),
        "uv_indexes": [float("nan")] * len(rows),
        "solar_radiations": [
            _jm2_to_w_m2(_safe_float(row.get("solar_jm2")), 3600.0) for row in rows
        ],
        "winds": _col("wind_kmh"),
        "gusts": _col("gust_kmh"),
        "wind_dirs": _col("wind_dir_deg"),
        "lat": lats[0] if lats else lat0,
        "lon": lons[0] if lons else lon0,
        "has_data": True,
        # DPObs publica los extremos intra-horarios por separado. ``t`` es
        # solo la temperatura instantánea horaria y no reproduce Tx/Tn.
        "daily_extremes": {
            "temp_max": _official_extreme("temp_max_c", max),
            "temp_min": _official_extreme("temp_min_c", min),
            "rh_max": _official_extreme("rh_max", max),
            "rh_min": _official_extreme("rh_min", min),
            "gust_max": _official_candidate_extreme(
                [
                    "gust_3s_kmh",
                    "gust_10m_kmh",
                    "gust_instant_kmh",
                    "gust_other_kmh",
                ],
                max,
            ),
        },
    }


async def fetch_recent_series(
    station_id: str,
    api_key: str,
    *,
    days_back: int = 7,
    step_hours: int = 3,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 18.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Serie reciente (T/HR/presión MSL) para tendencias: una petición
    ``/station/horaire`` por paso sinóptico (~56 con 7 días / 3 h),
    en paralelo limitado por el semáforo de cuota.
    """
    _require_api_key(api_key)
    station_id = str(station_id).strip()
    lat0, lon0, elevation, _name = _station_meta(station_id)

    now_utc = (now or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)
    now_utc = now_utc.replace(minute=0, second=0, microsecond=0)
    step = max(1, int(step_hours))
    steps = (max(1, int(days_back)) * 24) // step

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        async def _one(idx: int) -> Optional[Dict[str, Any]]:
            target = now_utc - timedelta(hours=idx * step)
            async with semaphore:
                try:
                    payload = await _get_json(
                        client, "/station/horaire",
                        {"id_station": station_id, "date": _utc_iso(target), "format": "json"},
                        api_key, timeout_s=timeout_s,
                    )
                except ProviderError as exc:
                    if exc.error_code in ("provider_unauthorized", "provider_ratelimit"):
                        raise
                    return None
            if isinstance(payload, list) and payload and isinstance(payload[0], dict):
                row = _parse_obs_row(payload[0], elevation)
                if row.get("epoch") is not None:
                    return row
            return None

        results = await asyncio.gather(*(_one(idx) for idx in range(steps + 1)))
    finally:
        if owns_client:
            await client.aclose()

    rows = {row["epoch"]: row for row in results if row is not None}
    epochs = sorted(rows)
    if not epochs:
        return {
            "epochs": [], "temps": [], "humidities": [], "pressures": [],
            "lat": lat0, "lon": lon0, "has_data": False,
        }
    return {
        "epochs": epochs,
        "temps": [_safe_float(rows[ep].get("temp_c")) for ep in epochs],
        "humidities": [_safe_float(rows[ep].get("rh")) for ep in epochs],
        "pressures": [_safe_float(rows[ep].get("p_msl_hpa")) for ep in epochs],
        "lat": lat0,
        "lon": lon0,
        "has_data": True,
    }
