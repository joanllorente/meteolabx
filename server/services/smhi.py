"""
Servicio puro de SMHI metobs (opendata-download-metobs.smhi.se, Suecia).

Particularidades:

1. **API pública sin credenciales**, JSON REST, licencia CC BY.

2. **Por parámetro**: cada variable es un recurso independiente
   (``/parameter/{p}/station/{id}/period/{period}/data.json``), así que
   la serie de una estación son varias peticiones EN PARALELO que luego
   se alinean por timestamp. Periodos: ``latest-day`` (rodante 24 h),
   ``latest-months`` (~4 meses).

3. **Parámetros horarios** (red automática): 1 temperatura (instantánea),
   6 HR, 9 presión MSL, 4 viento medio 10 min (m/s), 3 dirección,
   21 racha máx de la hora (m/s), 7 lluvia de la hora, 11 irradiancia
   global (W/m²). Viento m/s → km/h; el resto ya es canónico.

4. **Estaciones MANUALES** (``network: "MANUAL"`` en el catálogo): red
   convencional con dato DIARIO (2 temp media, 19/20 mín/máx, 5 lluvia).
   Su "observación" es el último día publicado, como los COOP de IEM.

5. **Calidad**: cada valor lleva ``quality`` G (verificado) o Y
   (preliminar); ambos se aceptan. Los valores llegan como strings.

6. **Día local**: Europe/Stockholm para toda la red.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

from data_files import SMHI_STATIONS_PATH
from server.schemas.errors import ProviderError
from domain.parsing.common import find_station_by_field, load_stations_json

logger = logging.getLogger(__name__)

PROVIDER = "SMHI"
BASE_URL = "https://opendata-download-metobs.smhi.se/api/version/1.0"
USER_AGENT = "MeteoLabX/1.0 (+https://meteolabx.com)"

# Parámetros horarios de la red automática.
P_TEMP, P_RH, P_MSL, P_WIND, P_DIR, P_GUST, P_RAIN, P_SOLAR = (
    "1", "6", "9", "4", "3", "21", "7", "11",
)
HOURLY_PARAMETERS = (P_TEMP, P_RH, P_MSL, P_WIND, P_DIR, P_GUST, P_RAIN, P_SOLAR)
TREND_PARAMETERS = (P_TEMP, P_RH, P_MSL)
# Parámetros diarios de la red manual.
PD_TMEAN, PD_TMIN, PD_TMAX, PD_RAIN = "2", "19", "20", "5"
DAILY_PARAMETERS = (PD_TMEAN, PD_TMIN, PD_TMAX, PD_RAIN)
# Extremos bidiarios (12 h, a las 06 y 18 UTC): mínima nocturna y máxima
# diurna EXACTAS para los extremos de la card (no hay extremos horarios).
P_TMIN_12H, P_TMAX_12H = "26", "27"
# Parámetros MINUTALES (misma red automática): serie del día a resolución
# nativa y extremos de card prácticamente exactos. Racha/radiación siguen
# siendo horarias (no tienen versión minutal).
P_TEMP_1MIN, P_RH_1MIN, P_MSL_1MIN, P_WIND_1MIN, P_DIR_1MIN = (
    "45", "43", "44", "47", "48",
)
MINUTELY_PARAMETERS = (P_TEMP_1MIN, P_RH_1MIN, P_MSL_1MIN, P_WIND_1MIN, P_DIR_1MIN)

STATION_TZ = ZoneInfo("Europe/Stockholm")


def _is_nan(value: float) -> bool:
    return value != value


def _safe_float(value: Any, default: float = float("nan")) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _kmh(value: float) -> float:
    return value * 3.6 if not _is_nan(value) else float("nan")


# =====================================================================
# Catálogo local
# =====================================================================

@lru_cache(maxsize=1)
def _load_stations() -> List[Dict[str, Any]]:
    try:
        return load_stations_json(str(SMHI_STATIONS_PATH), dict_key="stations")
    except Exception as exc:
        logger.warning("Catálogo SMHI no disponible (%s)", exc)
        return []


def _station_row(station_id: str) -> Dict[str, Any]:
    return find_station_by_field(_load_stations(), field="id", target=station_id)


def _is_manual_station(row: Dict[str, Any]) -> bool:
    return str(row.get("network") or "").upper() == "MANUAL" or bool(row.get("manual"))


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
            detail=f"SMHI timeout: {exc}",
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
        # SMHI responde 404 cuando la estación no publica ese parámetro:
        # se trata como "sin datos de esa variable", no como error.
        return None
    if status == 429:
        raise ProviderError(
            "provider_ratelimit",
            provider=PROVIDER,
            detail="SMHI rate limit (HTTP 429)",
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


def _parse_values(payload: Any) -> Dict[int, float]:
    """Payload de un parámetro → {epoch_s: valor}. Solo calidades G/Y.

    Los parámetros instantáneos traen ``date``; los agregados (diarios)
    traen ``from``/``to``/``ref`` — se usa ``to`` (fin del periodo).
    """
    if not isinstance(payload, dict):
        return {}
    out: Dict[int, float] = {}
    for item in payload.get("value") or []:
        if not isinstance(item, dict):
            continue
        quality = str(item.get("quality") or "").strip().upper()
        if quality and quality not in ("G", "Y"):
            continue
        value = _safe_float(item.get("value"))
        raw_epoch = item.get("date", item.get("to", item.get("from")))
        try:
            epoch = int(raw_epoch) // 1000
        except (TypeError, ValueError):
            continue
        if not _is_nan(value):
            out[epoch] = value
    return out


async def _fetch_parameters(
    station_id: str,
    parameters: Tuple[str, ...],
    period: str,
    client: httpx.AsyncClient,
    *,
    timeout_s: float,
) -> Dict[str, Dict[int, float]]:
    """Series de varios parámetros EN PARALELO → {param: {epoch: valor}}."""

    async def _one(parameter: str) -> Tuple[str, Dict[int, float]]:
        # El 404 (la estación no publica ese parámetro) ya se degrada a
        # None dentro de _get_json; el resto de errores se propaga.
        url = (
            f"{BASE_URL}/parameter/{parameter}/station/{station_id}"
            f"/period/{period}/data.json"
        )
        payload = await _get_json(client, url, timeout_s=timeout_s)
        return parameter, _parse_values(payload)

    results = await asyncio.gather(*(_one(p) for p in parameters))
    return dict(results)


# =====================================================================
# Rama MANUAL: red convencional con dato diario
# =====================================================================

async def _fetch_manual_current(
    station_id: str,
    row: Dict[str, Any],
    client: httpx.AsyncClient,
    *,
    timeout_s: float,
) -> Dict[str, Any]:
    """Observación de una estación manual: el último día publicado."""
    series = await _fetch_parameters(
        station_id, DAILY_PARAMETERS, "latest-months", client, timeout_s=timeout_s,
    )
    epochs = sorted(set().union(*(series[p].keys() for p in DAILY_PARAMETERS)))
    # Solo días recientes: una estación de archivo con meses de retraso no
    # debe presentarse como observación "actual".
    cutoff = int(datetime.now(tz=timezone.utc).timestamp()) - 7 * 86400
    epochs = [ep for ep in epochs if ep >= cutoff]
    if not epochs:
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail=f"SMHI sin datos recientes para {station_id}",
            status_code=502,
        )

    def _last(param: str) -> Tuple[float, Optional[int]]:
        values = series.get(param) or {}
        for epoch in reversed(epochs):
            if epoch in values:
                return values[epoch], epoch
        return float("nan"), None

    temp, temp_epoch = _last(PD_TMEAN)
    rain, rain_epoch = _last(PD_RAIN)
    epoch = temp_epoch or rain_epoch or int(epochs[-1])
    dt_utc = datetime.fromtimestamp(epoch, tz=timezone.utc)

    observation: Dict[str, Any] = {
        "Tc": temp,
        "RH": float("nan"),
        "p_hpa": float("nan"),
        "p_abs_hpa": float("nan"),
        "wind": float("nan"),
        "gust": float("nan"),
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
        "time_local": dt_utc.astimezone(STATION_TZ).isoformat(),
        "time_utc": dt_utc.isoformat(),
        "lat": _safe_float(row.get("lat")),
        "lon": _safe_float(row.get("lon")),
        "elevation": _safe_float(row.get("elev"), default=0.0),
        "station_name": str(row.get("name", "") or "").strip() or station_id,
    }
    from domain.observation_pipeline import add_basic_derived
    return add_basic_derived(observation)


async def _fetch_manual_recent_series(
    station_id: str,
    row: Dict[str, Any],
    client: httpx.AsyncClient,
    *,
    days_back: int,
    timeout_s: float,
) -> Dict[str, Any]:
    """Tendencias de una estación manual: un punto por día (media diaria)."""
    series = await _fetch_parameters(
        station_id, (PD_TMEAN,), "latest-months", client, timeout_s=timeout_s,
    )
    values = series.get(PD_TMEAN) or {}
    cutoff = int(datetime.now(tz=timezone.utc).timestamp()) - max(1, int(days_back)) * 86400
    epochs = sorted(ep for ep in values if ep >= cutoff)
    lat = _safe_float(row.get("lat"))
    lon = _safe_float(row.get("lon"))
    if not epochs:
        return {
            "epochs": [], "temps": [], "humidities": [], "pressures": [],
            "lat": lat, "lon": lon, "has_data": False,
        }
    return {
        "epochs": [int(ep) for ep in epochs],
        "temps": [values[ep] for ep in epochs],
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
    timeout_s: float = 16.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Observación actual = últimos valores del periodo rodante de 24 h,
    campo a campo. Devuelve el dict canónico.
    """
    station_id = str(station_id).strip()
    row = _station_row(station_id)
    now_local = (now or datetime.now(tz=STATION_TZ)).astimezone(STATION_TZ)
    day_start_epoch = int(
        now_local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    )

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        if _is_manual_station(row):
            return await _fetch_manual_current(
                station_id, row, client, timeout_s=timeout_s,
            )
        series = await _fetch_parameters(
            station_id,
            HOURLY_PARAMETERS + (P_TMIN_12H, P_TMAX_12H, P_TEMP_1MIN),
            "latest-day", client, timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()

    all_epochs = sorted(set().union(*(series[p].keys() for p in HOURLY_PARAMETERS)))
    if not all_epochs:
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail=f"SMHI sin observaciones para {station_id}",
            status_code=502,
        )

    def _last(param: str) -> Tuple[float, Optional[int]]:
        values = series.get(param) or {}
        for epoch in reversed(all_epochs):
            if epoch in values:
                return values[epoch], epoch
        return float("nan"), None

    temp, temp_epoch = _last(P_TEMP)
    # La temperatura minutal, si existe, es más fresca que la horaria.
    minutely_temps = series.get(P_TEMP_1MIN) or {}
    if minutely_temps:
        freshest = max(minutely_temps)
        if temp_epoch is None or freshest > temp_epoch:
            temp, temp_epoch = minutely_temps[freshest], freshest
    epoch = temp_epoch or int(all_epochs[-1])
    dt_utc = datetime.fromtimestamp(epoch, tz=timezone.utc)

    # Precipitación del día LOCAL: suma de las horas desde medianoche.
    rain_values = series.get(P_RAIN) or {}
    precip_vals = [v for ep, v in rain_values.items() if ep >= day_start_epoch]

    # Extremos de la card: instantáneas horarias del día + los extremos
    # bidiarios exactos que ya hayan cerrado. La MÁXIMA solo puede usar la
    # ventana diurna (el corte de las 18 UTC, 06-18): la ventana nocturna
    # de las 06 incluye la tarde de AYER y inflaría la máxima de hoy. La
    # mínima usa ambas (la de las 06 es la mínima matinal estándar). La
    # racha ya es exacta (param 21 = máx horaria).
    def _today(param: str, *, utc_hour: Optional[int] = None) -> List[float]:
        return [
            v for ep, v in (series.get(param) or {}).items()
            if ep >= day_start_epoch
            and (utc_hour is None or datetime.fromtimestamp(ep, tz=timezone.utc).hour == utc_hour)
        ]

    # Las minutales hacen el extremo del día prácticamente exacto.
    temp_highs = _today(P_TEMP) + _today(P_TEMP_1MIN) + _today(P_TMAX_12H, utc_hour=18)
    temp_lows = _today(P_TEMP) + _today(P_TEMP_1MIN) + _today(P_TMIN_12H)
    gust_highs = _today(P_GUST)
    daily_extremes: Dict[str, float] = {}
    if temp_highs:
        daily_extremes["temp_max"] = max(temp_highs)
    if temp_lows:
        daily_extremes["temp_min"] = min(temp_lows)
    if gust_highs:
        daily_extremes["gust_max"] = _kmh(max(gust_highs))

    observation: Dict[str, Any] = {
        "Tc": temp,
        "RH": _last(P_RH)[0],
        "p_hpa": _last(P_MSL)[0],
        "p_abs_hpa": float("nan"),
        "wind": _kmh(_last(P_WIND)[0]),
        "gust": _kmh(_last(P_GUST)[0]),
        "wind_dir_deg": _last(P_DIR)[0],
        "Td": float("nan"),
        "feels_like": float("nan"),
        "heat_index": float("nan"),
        "wind_chill": float("nan"),
        "precip_rate": float("nan"),
        "precip_total": float(sum(max(0.0, v) for v in precip_vals)) if precip_vals else float("nan"),
        "solar_radiation": _last(P_SOLAR)[0],
        "uv": float("nan"),
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
    """Serie del día local (Europe/Stockholm) en shape canónico, a
    resolución MINUTAL donde la estación la publica (params 43-48; la
    racha y la radiación siguen siendo horarias) y horaria en el resto.
    Las estaciones manuales (dato diario) no tienen serie intradía."""
    station_id = str(station_id).strip()
    row = _station_row(station_id)
    if _is_manual_station(row):
        return _empty_today_series()
    now_local = (now or datetime.now(tz=STATION_TZ)).astimezone(STATION_TZ)
    day_start_epoch = int(
        now_local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    )

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        series = await _fetch_parameters(
            station_id,
            MINUTELY_PARAMETERS + (P_GUST, P_SOLAR),
            "latest-day", client, timeout_s=timeout_s,
        )
        # Fallback POR PARÁMETRO: cada estación publica un subconjunto
        # minutal distinto (p. ej. temperatura minutal pero viento solo
        # horario); lo que falte se completa con su equivalente horario.
        pairs = (
            (P_TEMP_1MIN, P_TEMP), (P_RH_1MIN, P_RH), (P_MSL_1MIN, P_MSL),
            (P_WIND_1MIN, P_WIND), (P_DIR_1MIN, P_DIR),
        )
        missing_hourly = tuple(h for m, h in pairs if not series.get(m))
        if missing_hourly:
            hourly = await _fetch_parameters(
                station_id, missing_hourly, "latest-day", client, timeout_s=timeout_s,
            )
            series.update(hourly)
        for minutely_param, hourly_param in pairs:
            if series.get(minutely_param):
                series[hourly_param] = series.pop(minutely_param)
    finally:
        if owns_client:
            await client.aclose()

    epochs = sorted(
        ep
        for ep in set().union(*(series.get(p, {}).keys() for p in HOURLY_PARAMETERS))
        if ep >= day_start_epoch
    )
    if not epochs:
        return _empty_today_series()

    def _col(param: str, convert=None) -> List[float]:
        values = series.get(param) or {}
        out = [values.get(ep, float("nan")) for ep in epochs]
        return [convert(v) if convert else v for v in out]

    return {
        "epochs": [int(ep) for ep in epochs],
        "temps": _col(P_TEMP),
        "humidities": _col(P_RH),
        "dewpts": [float("nan")] * len(epochs),
        "pressures": _col(P_MSL),
        "uv_indexes": [float("nan")] * len(epochs),
        "solar_radiations": _col(P_SOLAR),
        "winds": _col(P_WIND, convert=_kmh),
        "gusts": _col(P_GUST, convert=_kmh),
        "wind_dirs": _col(P_DIR),
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
    """Serie reciente (T/HR/presión MSL) para tendencias, ya horaria.
    Usa ``latest-months`` recortado a la ventana pedida; las estaciones
    manuales devuelven un punto por día."""
    station_id = str(station_id).strip()
    row = _station_row(station_id)
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        if _is_manual_station(row):
            return await _fetch_manual_recent_series(
                station_id, row, client, days_back=days_back, timeout_s=timeout_s,
            )
        series = await _fetch_parameters(
            station_id, TREND_PARAMETERS, "latest-months", client, timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()

    cutoff = int(
        ((now or datetime.now(tz=timezone.utc)) - timedelta(days=max(1, int(days_back))))
        .timestamp()
    )
    temps = series.get(P_TEMP) or {}
    rhs = series.get(P_RH) or {}
    msls = series.get(P_MSL) or {}
    epochs = sorted(ep for ep in set(temps) | set(rhs) | set(msls) if ep >= cutoff)
    lat = _safe_float(row.get("lat"))
    lon = _safe_float(row.get("lon"))
    if not epochs:
        return {
            "epochs": [], "temps": [], "humidities": [], "pressures": [],
            "lat": lat, "lon": lon, "has_data": False,
        }
    return {
        "epochs": [int(ep) for ep in epochs],
        "temps": [temps.get(ep, float("nan")) for ep in epochs],
        "humidities": [rhs.get(ep, float("nan")) for ep in epochs],
        "pressures": [msls.get(ep, float("nan")) for ep in epochs],
        "lat": lat,
        "lon": lon,
        "has_data": True,
    }
