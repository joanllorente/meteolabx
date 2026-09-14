"""
Servicio puro de LHMT (Lietuvos hidrometeorologijos tarnyba, Lituania) sobre
la API pública Meteo.lt (``api.meteo.lt/v1``).

Particularidades:

1. **API pública sin credenciales**, JSON REST, licencia CC BY-SA 4.0 (hay
   que citar a LHMT como fuente).

2. **Una petición por estación y día**: ``/stations/{code}/observations/
   {YYYY-MM-DD|latest}``. La fecha es UTC; ``latest`` son las últimas 24 h.
   Todas las variables llegan juntas en cada muestra horaria. No hay bulk.

3. **Límite**: 180 peticiones/minuto y ~20.000/día por IP (superarlo puede
   acabar en bloqueo sin aviso). Los días UTC ya cerrados no cambian, así
   que se cachean largo y la tendencia de 7 días solo pide lo reciente.

4. **Variables horarias**: temperatura instantánea, HR, presión a nivel del
   mar (hPa), viento medio y dirección, racha máxima de la hora (m/s),
   precipitación de la hora (mm), espesor de nieve, nubosidad y código de
   tiempo presente. Viento m/s → km/h. El punto de rocío no se toma de la
   sensación térmica ni de nada del proveedor: lo deriva el pipeline.

5. **Día local**: Europe/Vilnius para toda la red. Como ``latest`` cubre 24 h
   hacia atrás, siempre contiene el día local en curso completo.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional

import httpx

from data_files import LHMT_STATIONS_PATH
from domain.parsing.common import find_station_by_field, load_stations_json
from server.schemas.errors import ProviderError
from server.services.cache import AsyncTTLCache
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

PROVIDER = "LHMT"
BASE_URL = "https://api.meteo.lt/v1"
USER_AGENT = "MeteoLabX/1.0 (+https://meteolabx.com)"
STATION_TZ = ZoneInfo("Europe/Vilnius")

# Peticiones simultáneas contra la API: holgado bajo 180/min.
MAX_CONCURRENCY = 4

_NAN = float("nan")

# Días UTC cerrados: no cambian. Una semana de caché ahorra las descargas
# repetidas de la tendencia y de los huecos del histórico.
_CLOSED_DAY_CACHE = AsyncTTLCache[List[Dict[str, Any]]](
    default_ttl_s=7 * 24 * 3600, max_entries=4000,
)


def _is_nan(value: float) -> bool:
    return value != value


def _safe_float(value: Any, default: float = _NAN) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if _is_nan(number) else number


def _kmh(value: float) -> float:
    return value * 3.6 if not _is_nan(value) else _NAN


def normalize_station_id(station_id: Any) -> str:
    """El schema de la API de MeteoLabX pasa los ids a mayúsculas; los
    códigos de Meteo.lt son en minúsculas (``vilniaus-ams``)."""
    return str(station_id or "").strip().lower()


# =====================================================================
# Catálogo local
# =====================================================================

@lru_cache(maxsize=1)
def _load_stations() -> List[Dict[str, Any]]:
    try:
        return load_stations_json(str(LHMT_STATIONS_PATH), dict_key="stations")
    except Exception as exc:
        logger.warning("Catálogo LHMT no disponible (%s)", exc)
        return []


def _station_row(station_id: str) -> Dict[str, Any]:
    return find_station_by_field(_load_stations(), field="id", target=station_id)


# =====================================================================
# HTTP + parsing
# =====================================================================

async def _get_json(client: httpx.AsyncClient, url: str, *, timeout_s: float) -> Any:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        response = await client.get(url, headers=headers, timeout=timeout_s)
    except httpx.TimeoutException as exc:
        raise ProviderError(
            "provider_timeout",
            provider=PROVIDER,
            detail=f"LHMT timeout: {exc}",
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
        # Meteo.lt responde 404 cuando no guarda datos de esa fecha (o la
        # estación no existe): "sin observaciones", no un fallo de la red.
        return None
    if status == 429:
        raise ProviderError(
            "provider_ratelimit",
            provider=PROVIDER,
            detail="LHMT rate limit (HTTP 429)",
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


def parse_observation_time(value: Any) -> Optional[int]:
    """``2026-09-12 20:00:00`` (UTC) → epoch en segundos."""
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    return int(parsed.replace(tzinfo=timezone.utc).timestamp())


def parse_observations(payload: Any) -> List[Dict[str, Any]]:
    """Payload de observaciones → muestras ``{epoch, ...}`` ordenadas y sin
    duplicados. Las variables sin medida quedan en ``None``."""
    if not isinstance(payload, dict):
        return []
    by_epoch: Dict[int, Dict[str, Any]] = {}
    for item in payload.get("observations") or []:
        if not isinstance(item, dict):
            continue
        epoch = parse_observation_time(item.get("observationTimeUtc"))
        if epoch is None:
            continue
        by_epoch[epoch] = {**item, "epoch": epoch}
    return [by_epoch[epoch] for epoch in sorted(by_epoch)]


def observations_url(station_id: str, when: str) -> str:
    return f"{BASE_URL}/stations/{normalize_station_id(station_id)}/observations/{when}"


async def fetch_latest_observations(
    station_id: str,
    client: httpx.AsyncClient,
    *,
    timeout_s: float = 16.0,
) -> List[Dict[str, Any]]:
    """Últimas 24 h horarias de una estación."""
    payload = await _get_json(client, observations_url(station_id, "latest"), timeout_s=timeout_s)
    return parse_observations(payload)


async def _fetch_day_observations(
    station_id: str,
    day: date,
    client: httpx.AsyncClient,
    *,
    timeout_s: float,
    now_utc: datetime,
    pause_s: float = 0.0,
) -> List[Dict[str, Any]]:
    """Observaciones de un día UTC. Los días cerrados salen de caché (también
    los vacíos: un 404 de un día pasado no va a llenarse). ``pause_s`` espacia
    las descargas de verdad en las ráfagas largas; lo cacheado no espera."""

    async def _fetch() -> List[Dict[str, Any]]:
        try:
            payload = await _get_json(
                client, observations_url(station_id, day.isoformat()), timeout_s=timeout_s,
            )
        finally:
            if pause_s > 0:
                await asyncio.sleep(pause_s)
        return parse_observations(payload)

    # Un día recién cerrado aún puede recibir su última hora con retraso.
    if day >= (now_utc - timedelta(hours=3)).date():
        return await _fetch()
    key = f"{PROVIDER}|day|{normalize_station_id(station_id)}|{day.isoformat()}"
    return await _CLOSED_DAY_CACHE.get_or_fetch(key, _fetch)


def _value(sample: Dict[str, Any], field: str) -> float:
    return _safe_float(sample.get(field))


def _last(samples: List[Dict[str, Any]], field: str) -> float:
    for sample in reversed(samples):
        value = _value(sample, field)
        if not _is_nan(value):
            return value
    return _NAN


def _local_day_start_epoch(now_local: datetime) -> int:
    return int(now_local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


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
    """Observación actual = última muestra horaria, campo a campo, con los
    extremos y la lluvia del día local derivados de las últimas 24 h."""
    station_id = normalize_station_id(station_id)
    row = _station_row(station_id)
    now_local = (now or datetime.now(tz=STATION_TZ)).astimezone(STATION_TZ)
    day_start_epoch = _local_day_start_epoch(now_local)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        samples = await fetch_latest_observations(station_id, client, timeout_s=timeout_s)
    finally:
        if owns_client:
            await client.aclose()

    if not samples:
        # El proveedor contestó; es la estación la que no publica. El código lo
        # dice para que la ficha no acuse a la red de estar incomunicada.
        raise ProviderError(
            "provider_no_current_data",
            provider=PROVIDER,
            detail=f"LHMT sin observaciones para {station_id}",
            status_code=502,
        )

    epoch = int(samples[-1]["epoch"])
    for sample in reversed(samples):
        if not _is_nan(_value(sample, "airTemperature")):
            epoch = int(sample["epoch"])
            break
    dt_utc = datetime.fromtimestamp(epoch, tz=timezone.utc)

    today = [s for s in samples if int(s["epoch"]) > day_start_epoch]
    # La lluvia de la muestra de las 00:00 locales es la de la hora anterior
    # (pertenece a ayer); por eso el día empieza estrictamente después.
    precip_vals = [_value(s, "precipitation") for s in today]
    precip_vals = [v for v in precip_vals if not _is_nan(v)]
    temps_today = [
        _value(s, "airTemperature") for s in samples if int(s["epoch"]) >= day_start_epoch
    ]
    temps_today = [v for v in temps_today if not _is_nan(v)]
    gusts_today = [_value(s, "windGust") for s in today]
    gusts_today = [v for v in gusts_today if not _is_nan(v)]
    daily_extremes: Dict[str, float] = {}
    if temps_today:
        daily_extremes["temp_max"] = max(temps_today)
        daily_extremes["temp_min"] = min(temps_today)
    if gusts_today:
        daily_extremes["gust_max"] = _kmh(max(gusts_today))

    observation: Dict[str, Any] = {
        "Tc": _last(samples, "airTemperature"),
        "RH": _last(samples, "relativeHumidity"),
        "p_hpa": _last(samples, "seaLevelPressure"),
        "p_abs_hpa": _NAN,
        "wind": _kmh(_last(samples, "windSpeed")),
        "gust": _kmh(_last(samples, "windGust")),
        "wind_dir_deg": _last(samples, "windDirection"),
        "Td": _NAN,
        "feels_like": _NAN,
        "heat_index": _NAN,
        "wind_chill": _NAN,
        "precip_rate": _NAN,
        "precip_total": float(sum(max(0.0, v) for v in precip_vals)) if precip_vals else _NAN,
        "solar_radiation": _NAN,
        "uv": _NAN,
        "epoch": epoch,
        "time_local": dt_utc.astimezone(STATION_TZ).isoformat(),
        "time_utc": dt_utc.isoformat(),
        "lat": _safe_float(row.get("lat")),
        "lon": _safe_float(row.get("lon")),
        "elevation": _safe_float(row.get("elev"), default=0.0),
        "station_name": str(row.get("name", "") or "").strip() or station_id,
        "daily_extremes": daily_extremes,
    }
    from domain.observation_pipeline import add_basic_derived
    return add_basic_derived(observation)


def _empty_today_series(row: Dict[str, Any]) -> Dict[str, Any]:
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
        "lat": _safe_float(row.get("lat")),
        "lon": _safe_float(row.get("lon")),
        "has_data": False,
    }


def _column(samples: List[Dict[str, Any]], field: str, convert=None) -> List[float]:
    values = [_value(s, field) for s in samples]
    return [convert(v) for v in values] if convert else values


async def fetch_today_series(
    station_id: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 16.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Serie horaria del día local (Europe/Vilnius) en shape canónico."""
    station_id = normalize_station_id(station_id)
    row = _station_row(station_id)
    now_local = (now or datetime.now(tz=STATION_TZ)).astimezone(STATION_TZ)
    day_start_epoch = _local_day_start_epoch(now_local)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        samples = await fetch_latest_observations(station_id, client, timeout_s=timeout_s)
    finally:
        if owns_client:
            await client.aclose()

    samples = [s for s in samples if int(s["epoch"]) >= day_start_epoch]
    if not samples:
        return _empty_today_series(row)

    precip_steps = _column(samples, "precipitation")
    # La lluvia de la muestra de medianoche es la de la última hora de ayer.
    if int(samples[0]["epoch"]) == day_start_epoch:
        precip_steps[0] = _NAN
    return {
        "epochs": [int(s["epoch"]) for s in samples],
        "temps": _column(samples, "airTemperature"),
        "humidities": _column(samples, "relativeHumidity"),
        "dewpts": [_NAN] * len(samples),
        "pressures": _column(samples, "seaLevelPressure"),
        "uv_indexes": [_NAN] * len(samples),
        "solar_radiations": [_NAN] * len(samples),
        # Precipitación de la hora precedente a cada muestra.
        "precip_step_mm": precip_steps,
        "winds": _column(samples, "windSpeed", convert=_kmh),
        "gusts": _column(samples, "windGust", convert=_kmh),
        "wind_dirs": _column(samples, "windDirection"),
        "lat": _safe_float(row.get("lat")),
        "lon": _safe_float(row.get("lon")),
        "has_data": True,
    }


async def fetch_recent_series(
    station_id: str,
    *,
    days_back: int = 7,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 25.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Serie reciente horaria (T/HR/presión MSL) para tendencias: un día UTC
    por petición, en paralelo limitado; los cerrados salen de caché."""
    station_id = normalize_station_id(station_id)
    row = _station_row(station_id)
    now_utc = (now or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)
    start = now_utc - timedelta(days=max(1, int(days_back)))
    days = [
        start.date() + timedelta(days=offset)
        for offset in range((now_utc.date() - start.date()).days + 1)
    ]

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def _one(day: date) -> List[Dict[str, Any]]:
        async with semaphore:
            return await _fetch_day_observations(
                station_id, day, client, timeout_s=timeout_s, now_utc=now_utc,
            )

    try:
        batches = await asyncio.gather(*(_one(day) for day in days))
    finally:
        if owns_client:
            await client.aclose()

    cutoff = int(start.timestamp())
    by_epoch = {
        int(sample["epoch"]): sample
        for batch in batches
        for sample in batch
        if int(sample["epoch"]) >= cutoff
    }
    samples = [by_epoch[epoch] for epoch in sorted(by_epoch)]
    lat = _safe_float(row.get("lat"))
    lon = _safe_float(row.get("lon"))
    if not samples:
        return {
            "epochs": [], "temps": [], "humidities": [], "pressures": [],
            "lat": lat, "lon": lon, "has_data": False,
        }
    return {
        "epochs": [int(s["epoch"]) for s in samples],
        "temps": _column(samples, "airTemperature"),
        "humidities": _column(samples, "relativeHumidity"),
        "pressures": _column(samples, "seaLevelPressure"),
        "lat": lat,
        "lon": lon,
        "has_data": True,
    }
