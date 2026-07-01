"""
Servicio puro de Euskalmet (Euskadi).

Versión "limpia" del cliente legacy (``services/euskalmet.py``): sin
``streamlit``, sin ``st.cache_data``, cliente ``httpx.AsyncClient``.

Particularidades de Euskalmet:

1. **Auth doble**: JWT RS256 en ``Authorization: Bearer`` (manual vía
   ``METEOLABX_EUSKALMET_JWT`` o autogenerado firmando con la clave
   privada PEM, igual que el legacy: openssl por subprocess) + api key
   opcional en headers ``apikey``/``x-api-key``.

2. **Modelo por medida y hora**: no hay endpoint de observación actual.
   Cada medida (temperatura, humedad…) se lee por sensor y hora:
   ``/euskalmet/readings/forStation/{sid}/{sensor}/measures/{tipo}/{id}/at/{Y}/{M}/{D}/{H}``
   → lista de valores en slots de 10 min dentro de esa hora local.

3. **Mapa de sensores estático**: el sensor que sirve cada medida por
   estación vive en ``data/data_station_sensor_map_euskalmet.json``
   (mismo fichero que el frontend, modo estricto: sin discovery). Si la
   estación no está mapeada → ``station_not_found``.

4. **Fan-out controlado**: ``fetch_today_series`` lanza medidas × horas
   en paralelo con un semáforo; ``fetch_current`` solo sondea las 2
   últimas horas por medida (+ todas las horas de precipitación para el
   acumulado del día), para no quemar la API en cada refresco.

5. **Unidades**: viento/racha en m/s → km/h. Euskalmet expone presión
   absoluta Y a nivel del mar como medidas separadas; ``pressures``
   canónico = MSL (derivada de la absoluta si falta la medida MSL).

Mapeo de errores → ``ProviderError``: igual que Meteocat (401/403 →
``provider_unauthorized``; 404 por-hora se tolera como "sin datos").
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import subprocess
import time
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

from data_files import (
    EUSKALMET_SENSOR_MAP_PATH,
    EUSKALMET_STATIONS_PATH,
    ROOT_DIR,
)
from server.schemas.errors import ProviderError

logger = logging.getLogger(__name__)

PROVIDER = "EUSKALMET"
BASE_URL = "https://api.euskadi.eus"
LOCAL_TZ = ZoneInfo("Europe/Madrid")

JWT_AUD = "met01.apikey"
JWT_VERSION = "1.0.0"
DEFAULT_PRIVATE_KEY_PATH = ROOT_DIR / "keys" / "euskalmet" / "privateKey.pem"

# Medidas que componen la observación (mismos specs que el legacy).
MEASURE_SPECS: Dict[str, Tuple[str, str]] = {
    "temp": ("measuresForAir", "temperature"),
    "rh": ("measuresForAir", "humidity"),
    "pressure_abs": ("measuresForAtmosphere", "pressure"),
    "pressure_msl": ("measuresForAtmosphere", "sea_level_pressure"),
    "wind": ("measuresForWind", "mean_speed"),
    "gust": ("measuresForWind", "max_speed"),
    "wind_dir": ("measuresForWind", "mean_direction"),
    "precip": ("measuresForWater", "precipitation"),
    "solar": ("measuresForSun", "irradiance"),
}

# Concurrencia máxima contra la API de Euskalmet (fan-out de horas).
MAX_CONCURRENT_REQUESTS = 8


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


def _ms_to_kmh(value: float) -> float:
    return float("nan") if _is_nan(value) else value * 3.6


def _absolute_to_msl(p_abs_hpa: float, elevation_m: float) -> float:
    if _is_nan(p_abs_hpa) or _is_nan(elevation_m):
        return float("nan")
    try:
        return float(p_abs_hpa) * math.exp(float(elevation_m) / 8000.0)
    except Exception:
        return float("nan")


# =====================================================================
# JWT autogenerado (idéntico al legacy, sin session_state)
# =====================================================================

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


@lru_cache(maxsize=16)
def _build_auto_jwt_cached(
    bucket_epoch: int,
    private_key_path: str,
    iss: str,
    email: str,
) -> str:
    """
    Firma un JWT RS256 con openssl. Cacheado por hora (``bucket_epoch``)
    para no relanzar el subprocess en cada request; el token expira a
    la hora de su emisión + 1h.
    """
    if not private_key_path:
        return ""
    now = int(bucket_epoch)
    payload: Dict[str, Any] = {
        "aud": JWT_AUD,
        "iss": iss,
        "version": JWT_VERSION,
        "iat": now,
        "exp": now + 3600,
    }
    if email:
        payload["email"] = email

    header = {"alg": "RS256", "typ": "JWT"}
    header_b64 = _b64url(json.dumps(header, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")

    try:
        proc = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", private_key_path],
            input=signing_input,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except Exception:
        return ""

    if proc.returncode != 0 or not proc.stdout:
        return ""

    return f"{header_b64}.{payload_b64}.{_b64url(proc.stdout)}"


def resolve_jwt(
    manual_jwt: str = "",
    private_key_path: str = "",
    iss: str = "meteolabx",
    email: str = "",
) -> str:
    """
    JWT efectivo: el manual si está configurado; si no, autogenerado
    desde la clave privada (``private_key_path`` o el default del repo).
    Devuelve ``""`` si no hay forma de obtener token.
    """
    if manual_jwt.strip():
        return manual_jwt.strip()
    key_path = private_key_path.strip() or (
        str(DEFAULT_PRIVATE_KEY_PATH) if DEFAULT_PRIVATE_KEY_PATH.exists() else ""
    )
    if not key_path:
        return ""
    bucket = int(time.time() // 3600 * 3600)
    return _build_auto_jwt_cached(bucket, key_path, iss.strip() or "meteolabx", email.strip())


def materialize_private_key(pem: str) -> str:
    """
    Escribe el contenido PEM en un fichero temporal (permisos 0600) y
    devuelve su ruta. Para plataformas sin FS persistente ni acceso al repo
    (Railway): la clave privada llega por variable de entorno y aquí se
    materializa a disco para que ``openssl dgst -sign`` pueda firmar el JWT.

    Idempotente por contenido (el nombre incluye un hash), así que reinicios
    o llamadas repetidas reutilizan el mismo fichero. Devuelve ``""`` si el
    PEM viene vacío.
    """
    import hashlib
    import os
    import tempfile

    content = (pem or "").strip()
    if not content:
        return ""
    # Algunas plataformas (o el pegar en un editor de una línea) entregan el PEM
    # con los saltos ESCAPADOS (``\n`` literal) en vez de reales; openssl los
    # necesita reales. Si no hay ningún salto real, deshacemos el escape.
    if "\\n" in content and "\n" not in content:
        content = content.replace("\\r\\n", "\n").replace("\\n", "\n")
    if not content.endswith("\n"):  # openssl es tiquismiquis con el salto final
        content += "\n"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    path = os.path.join(tempfile.gettempdir(), f"meteolabx_euskalmet_{digest}.pem")
    if not os.path.exists(path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, content.encode("utf-8"))
        finally:
            os.close(fd)
    return path


# =====================================================================
# Catálogos locales: estaciones + mapa de sensores
# =====================================================================

@lru_cache(maxsize=1)
def _load_station_catalog() -> Dict[str, Dict[str, Any]]:
    try:
        with open(EUSKALMET_STATIONS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        logger.warning("Catálogo Euskalmet no disponible (%s)", exc)
        return {}
    if not isinstance(data, list):
        return {}
    return {
        str(item["stationId"]).strip().upper(): item
        for item in data
        if isinstance(item, dict) and item.get("stationId")
    }


def _station_meta(station_id: str) -> Tuple[float, float, float, str]:
    """→ (lat, lon, elevation, nombre)."""
    station = _load_station_catalog().get(station_id, {})
    lat = _safe_float(station.get("lat"))
    lon = _safe_float(station.get("lon"))
    elevation = _safe_float(station.get("altitude_m"))
    name = str(station.get("displayName", "") or "").strip()
    return lat, lon, elevation, name


@lru_cache(maxsize=1)
def _load_sensor_map() -> Dict[str, Dict[str, str]]:
    """``{station_id: {"measuresForAir/temperature": "TA05", ...}}``"""
    try:
        with open(EUSKALMET_SENSOR_MAP_PATH, encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception as exc:
        logger.warning("Mapa de sensores Euskalmet no disponible (%s)", exc)
        return {}
    if not isinstance(payload, dict):
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for stid, mapping in payload.items():
        if not isinstance(mapping, dict):
            continue
        cleaned = {
            str(mkey).strip(): str(sid).strip().upper()
            for mkey, sid in mapping.items()
            if str(mkey).strip() and str(sid).strip()
        }
        if cleaned:
            out[str(stid).strip().upper()] = cleaned
    return out


def _sensor_for(station_id: str, measure_type: str, measure_id: str) -> str:
    mapping = _load_sensor_map().get(station_id, {})
    return mapping.get(f"{measure_type}/{measure_id}", "")


# =====================================================================
# HTTP
# =====================================================================

def _require_credentials(jwt: str) -> None:
    if not jwt:
        raise ProviderError(
            "provider_unauthorized",
            provider=PROVIDER,
            detail="No Euskalmet JWT available (manual or auto-generated from PEM)",
            status_code=401,
        )


def _headers(jwt: str, api_key: str) -> Dict[str, str]:
    headers = {"Accept": "application/json", "Authorization": f"Bearer {jwt}"}
    if api_key:
        headers["apikey"] = api_key
        headers["x-api-key"] = api_key
    return headers


async def _get_json(
    client: httpx.AsyncClient,
    path: str,
    jwt: str,
    api_key: str,
    *,
    timeout_s: float,
) -> Any:
    url = f"{BASE_URL}/{path.lstrip('/')}"
    try:
        response = await client.get(url, headers=_headers(jwt, api_key), timeout=timeout_s)
    except httpx.TimeoutException as exc:
        raise ProviderError(
            "provider_timeout",
            provider=PROVIDER,
            detail=f"Euskalmet timeout: {exc}",
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
            detail=f"Euskalmet auth rechazada (HTTP {status})",
            status_code=401,
        )
    if status == 404:
        raise ProviderError(
            "station_not_found",
            provider=PROVIDER,
            detail=f"Sin datos: {path} (HTTP 404)",
            status_code=404,
        )
    if status == 429:
        raise ProviderError(
            "provider_ratelimit",
            provider=PROVIDER,
            detail="Euskalmet rate limit (HTTP 429)",
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
# Lecturas por hora → puntos (epoch, valor)
# =====================================================================

def _hour_points(year: int, month: int, day: int, hour: int, values: List[Any]) -> List[Tuple[int, float]]:
    """Valores de una hora local → puntos cada 10 min con epoch UTC."""
    base = datetime(year, month, day, hour, 0, 0, tzinfo=LOCAL_TZ)
    return [
        (int((base + timedelta(minutes=idx * 10)).timestamp()), _safe_float(raw))
        for idx, raw in enumerate(values)
    ]


def _extract_values(payload: Any) -> List[Any]:
    data = payload
    if isinstance(data, list):
        data = data[0] if data and isinstance(data[0], dict) else {"values": data}
    if not isinstance(data, dict):
        return []
    values = data.get("values", [])
    if not isinstance(values, list):
        for alt in ("lectures", "datos", "data"):
            v = data.get(alt)
            if isinstance(v, list):
                values = v
                break
    return values if isinstance(values, list) else []


async def _fetch_measure_points(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    station_id: str,
    sensor_id: str,
    measure_type: str,
    measure_id: str,
    hours: List[Tuple[int, int, int, int]],
    jwt: str,
    api_key: str,
    *,
    timeout_s: float,
) -> List[Tuple[int, float]]:
    """
    Puntos de una medida en las horas dadas (``(y, m, d, h)``).

    Errores por-hora se toleran (hora sin datos publicados todavía es
    lo normal); ``provider_unauthorized``/``provider_ratelimit`` se
    propagan porque invalidan todo el fan-out.
    """

    async def _one(year: int, month: int, day: int, hour: int) -> List[Tuple[int, float]]:
        path = (
            f"euskalmet/readings/forStation/{station_id}/{sensor_id}/measures/"
            f"{measure_type}/{measure_id}/at/{year:04d}/{month:02d}/{day:02d}/{hour:02d}"
        )
        async with semaphore:
            try:
                payload = await _get_json(client, path, jwt, api_key, timeout_s=timeout_s)
            except ProviderError as exc:
                if exc.error_code in ("provider_unauthorized", "provider_ratelimit"):
                    raise
                return []
        values = _extract_values(payload)
        return _hour_points(year, month, day, hour, values) if values else []

    chunks = await asyncio.gather(*(_one(*spec) for spec in hours))
    # Dedup por epoch conservando el último valor.
    dedup: Dict[int, float] = {}
    for chunk in chunks:
        for epoch, value in chunk:
            dedup[epoch] = value
    return sorted(dedup.items(), key=lambda item: item[0])


def _local_day_hours(now: Optional[datetime] = None) -> List[Tuple[int, int, int, int]]:
    """Horas del día local en curso hasta la hora actual inclusive."""
    now_local = (now or datetime.now(tz=LOCAL_TZ)).astimezone(LOCAL_TZ)
    return [
        (now_local.year, now_local.month, now_local.day, hour)
        for hour in range(now_local.hour + 1)
    ]


def _require_mapped_station(station_id: str) -> Dict[str, str]:
    mapping = _load_sensor_map().get(station_id, {})
    if not mapping:
        raise ProviderError(
            "station_not_found",
            provider=PROVIDER,
            detail=f"Estación sin sensores mapeados en inventario: {station_id}",
            status_code=404,
        )
    return mapping


async def _fetch_measures(
    station_id: str,
    jwt: str,
    api_key: str,
    client: httpx.AsyncClient,
    *,
    hours_by_measure: Dict[str, List[Tuple[int, int, int, int]]],
    timeout_s: float,
) -> Dict[str, List[Tuple[int, float]]]:
    """Fan-out de medidas mapeadas → ``{measure: [(epoch, valor), ...]}``."""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    async def _measure(name: str) -> Tuple[str, List[Tuple[int, float]]]:
        mtype, mid = MEASURE_SPECS[name]
        sensor = _sensor_for(station_id, mtype, mid)
        hours = hours_by_measure.get(name, [])
        if not sensor or not hours:
            return name, []
        points = await _fetch_measure_points(
            client, semaphore, station_id, sensor, mtype, mid,
            hours, jwt, api_key, timeout_s=timeout_s,
        )
        return name, points

    results = await asyncio.gather(*(_measure(name) for name in hours_by_measure))
    return dict(results)


# =====================================================================
# API pública del servicio
# =====================================================================

async def fetch_current(
    station_id: str,
    jwt: str,
    api_key: str = "",
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 12.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Observación actual de una estación Euskalmet.

    Sondea las 2 últimas horas locales de cada medida (último valor
    válido) y todas las horas del día para la precipitación (acumulado
    diario por suma de incrementos). Devuelve el dict canónico más
    ``station_code`` y ``station_name``.
    """
    _require_credentials(jwt)
    station_id = str(station_id).strip().upper()
    _require_mapped_station(station_id)

    day_hours = _local_day_hours(now)
    # Euskalmet publica con retraso variable: mirar solo las 2 últimas horas
    # dejaba "sin lecturas del día" cuando la hora actual/anterior aún no
    # estaban publicadas. Sondeamos las últimas 6 horas para encontrar el
    # último valor válido (el legacy sondea un rango amplio similar).
    recent_hours = day_hours[-6:]
    hours_by_measure: Dict[str, List[Tuple[int, int, int, int]]] = {
        name: (day_hours if name == "precip" else recent_hours)
        for name in MEASURE_SPECS
    }

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        measures = await _fetch_measures(
            station_id, jwt, api_key, client,
            hours_by_measure=hours_by_measure, timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()

    return _normalize_current(station_id, measures)


def _last_valid(points: List[Tuple[int, float]]) -> Tuple[float, Optional[int]]:
    for epoch, value in reversed(points):
        if not _is_nan(value):
            return value, epoch
    return float("nan"), None


def _normalize_current(
    station_id: str,
    measures: Dict[str, List[Tuple[int, float]]],
) -> Dict[str, Any]:
    lat, lon, elevation, name = _station_meta(station_id)

    values: Dict[str, float] = {}
    epochs: List[int] = []
    for key in ("temp", "rh", "pressure_abs", "pressure_msl", "wind", "gust", "wind_dir", "solar"):
        value, epoch = _last_valid(measures.get(key, []))
        values[key] = value
        if epoch is not None:
            epochs.append(epoch)

    precip_points = measures.get("precip", [])
    precip_vals = [max(0.0, v) for _, v in precip_points if not _is_nan(v)]
    precip_total = float(sum(precip_vals)) if precip_vals else float("nan")
    if precip_points:
        epochs.append(precip_points[-1][0])

    if not epochs:
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail=f"Euskalmet sin lecturas del día para {station_id}",
            status_code=502,
        )

    epoch = max(epochs)
    p_abs = values["pressure_abs"]
    p_msl = values["pressure_msl"]
    if _is_nan(p_msl):
        p_msl = _absolute_to_msl(p_abs, elevation)

    from datetime import timezone as _tz
    dt_utc = datetime.fromtimestamp(epoch, tz=_tz.utc)

    observation: Dict[str, Any] = {
        "Tc": values["temp"],
        "RH": values["rh"],
        "p_hpa": p_msl,
        "p_abs_hpa": p_abs,
        "wind": _ms_to_kmh(values["wind"]),
        "gust": _ms_to_kmh(values["gust"]),
        "wind_dir_deg": values["wind_dir"],
        "Td": float("nan"),
        "feels_like": float("nan"),
        "heat_index": float("nan"),
        "wind_chill": float("nan"),
        "precip_rate": float("nan"),
        "precip_total": precip_total,
        "solar_radiation": values["solar"],
        "uv": float("nan"),  # Euskalmet no expone UV
        "epoch": epoch,
        "time_local": dt_utc.astimezone(LOCAL_TZ).isoformat(),
        "time_utc": dt_utc.isoformat(),
        "lat": lat,
        "lon": lon,
        "elevation": elevation,
        "station_name": name,
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
    jwt: str,
    api_key: str = "",
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 12.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Serie del día local (slots de 10 min) en shape canónico
    ``TodaySeries``. Fan-out de medidas × horas con concurrencia
    limitada (``MAX_CONCURRENT_REQUESTS``).
    """
    _require_credentials(jwt)
    station_id = str(station_id).strip().upper()
    _require_mapped_station(station_id)

    day_hours = _local_day_hours(now)
    hours_by_measure = {name: day_hours for name in MEASURE_SPECS}

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        measures = await _fetch_measures(
            station_id, jwt, api_key, client,
            hours_by_measure=hours_by_measure, timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()

    return _normalize_today_series(station_id, measures)


def _normalize_today_series(
    station_id: str,
    measures: Dict[str, List[Tuple[int, float]]],
) -> Dict[str, Any]:
    lat, lon, elevation, _name = _station_meta(station_id)

    all_epochs: set = set()
    for key in ("temp", "rh", "pressure_abs", "pressure_msl", "wind", "gust", "wind_dir", "solar"):
        all_epochs.update(ep for ep, _ in measures.get(key, []))
    epochs = sorted(all_epochs)
    if not epochs:
        return _empty_today_series()

    indexes = {
        key: dict(measures.get(key, []))
        for key in MEASURE_SPECS
    }

    def _at(key: str, epoch: int) -> float:
        return indexes[key].get(epoch, float("nan"))

    pressures: List[float] = []
    for ep in epochs:
        p_msl = _at("pressure_msl", ep)
        if _is_nan(p_msl):
            p_msl = _absolute_to_msl(_at("pressure_abs", ep), elevation)
        pressures.append(p_msl)

    return {
        "epochs": epochs,
        "temps": [_at("temp", ep) for ep in epochs],
        "humidities": [_at("rh", ep) for ep in epochs],
        "dewpts": [float("nan")] * len(epochs),
        "pressures": pressures,
        "uv_indexes": [float("nan")] * len(epochs),
        "solar_radiations": [_at("solar", ep) for ep in epochs],
        "winds": [_ms_to_kmh(_at("wind", ep)) for ep in epochs],
        "gusts": [_ms_to_kmh(_at("gust", ep)) for ep in epochs],
        "wind_dirs": [_at("wind_dir", ep) for ep in epochs],
        "lat": lat,
        "lon": lon,
        "has_data": True,
    }


async def fetch_recent_series(
    station_id: str,
    jwt: str,
    api_key: str = "",
    *,
    days_back: int = 1,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 12.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Serie reciente para el LOOKBACK de ``/series/today``.

    Euskalmet no tiene endpoint sinóptico propio; reutilizamos la serie del
    DÍA ANTERIOR (slots de 10 min, resolución nativa) para sembrar la
    tendencia de presión 3h al inicio del día local. Solo se usa con
    ``days_back=1`` (el lookback); la pestaña sinóptica no enruta aquí.
    """
    now_local = (now or datetime.now(tz=LOCAL_TZ)).astimezone(LOCAL_TZ)
    # Día anterior COMPLETO: pasamos las 23:00 de ayer para que
    # ``_local_day_hours`` genere todas las horas de ayer (devuelve las horas
    # "hasta la actual inclusive").
    yesterday_end = (now_local - timedelta(days=1)).replace(
        hour=23, minute=0, second=0, microsecond=0
    )
    return await fetch_today_series(
        station_id, jwt, api_key, client=client, timeout_s=timeout_s, now=yesterday_end,
    )
