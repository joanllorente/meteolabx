"""
Servicio puro de Weather Underground.

Versión "limpia" del cliente WU: sin ``streamlit``, sin ``st.cache_data``,
sin ``st.session_state``. Cliente HTTP ``httpx.AsyncClient`` para integrarse
nativamente con FastAPI.

Lo que NO hace este módulo (a propósito):
- Cachear respuestas. El caché vivirá en una capa superior
  (``server/services/cache.py`` cuando exista) para mantener el servicio
  testeable y puro.
- Devolver Pydantic models. De momento ``dict[str, Any]`` con el mismo
  shape que el cliente legacy ``api/weather_underground.py``; eso facilita
  el cambio del frontend Streamlit a la API sin tocar tabs/observation.py.
- Decidir códigos HTTP. Lanza ``ProviderError`` con ``status_code``
  sugerido; la capa HTTP (el exception handler de FastAPI) lo serializa.

Mapeo de errores ``WuError`` → ``ProviderError``:

    timeout       → provider_timeout         (504)
    network       → provider_network_error   (502)
    unauthorized  → provider_unauthorized    (401)
    notfound      → station_not_found        (404)
    ratelimit     → provider_ratelimit       (429)
    http          → provider_http_error      (502)
    badjson       → provider_bad_response    (502)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

import httpx

# Importamos constantes meteo del config legacy del root. Ese módulo es
# Python puro (sin streamlit) y reutilizarlo evita duplicarlas. Cuando se
# limpie la base de código, las constantes podrán migrar a un módulo
# explícito tipo ``models/constants.py``.
from config import (
    RAIN_QUANTIZE_CORRECTION,
    RAIN_TIP_RESOLUTION,
    WU_TIMEOUT_SECONDS,
)

from server.schemas.errors import ProviderError

logger = logging.getLogger(__name__)

PROVIDER = "WU"

WU_URL_CURRENT = "https://api.weather.com/v2/pws/observations/current"
WU_URL_DAILY = "https://api.weather.com/v2/pws/observations/all/1day"


# =====================================================================
# Helpers puros
# =====================================================================

def _is_nan(x: Any) -> bool:
    """``True`` si ``x`` es NaN (``x != x``)."""
    return x != x


def _safe_float(val: Any, default: float = float("nan")) -> float:
    """Convierte a ``float`` tolerando ``None``, strings raros, etc."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


CARDINAL_16 = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)


def _parse_wind_direction(val: Any) -> float:
    """Acepta dirección como número (grados) o texto cardinal (``NNE``, ``CALM``…)."""
    f = _safe_float(val)
    if not _is_nan(f):
        return f % 360
    if val is None:
        return float("nan")
    s = str(val).strip().upper()
    if not s:
        return float("nan")
    if s in ("CALM", "CALMA"):
        return 0.0
    if s in CARDINAL_16:
        idx = CARDINAL_16.index(s)
        return (idx * 22.5) % 360
    return float("nan")


def _first_valid_number(*values: Any) -> float:
    """Primer valor convertible a float y no-NaN; si ninguno, NaN."""
    for value in values:
        f = _safe_float(value)
        if not _is_nan(f):
            return f
    return float("nan")


def _first_valid_wind_dir(*values: Any) -> float:
    """Primer valor convertible a dirección de viento válida (0-360°)."""
    for value in values:
        direction = _parse_wind_direction(value)
        if not _is_nan(direction):
            return direction
    return float("nan")


def _quantize_rain_mm_wu(mm_wu: float) -> float:
    """
    Cuantiza la precipitación reportada por WU a múltiplos del tip del
    pluviómetro, aplicando antes el factor de corrección. Detalles del
    porqué viven en el cliente legacy.
    """
    if _is_nan(mm_wu):
        return float("nan")
    mm_corr = mm_wu * RAIN_QUANTIZE_CORRECTION
    tips = round(mm_corr / RAIN_TIP_RESOLUTION)
    return tips * RAIN_TIP_RESOLUTION


# =====================================================================
# Mapeo de errores HTTP/red → ProviderError
# =====================================================================

def _raise_for_http_status(status_code: int) -> None:
    """Lanza ``ProviderError`` para códigos HTTP >= 400 de Weather Underground."""
    if status_code == 401:
        raise ProviderError(
            "provider_unauthorized",
            provider=PROVIDER,
            detail="Invalid API key",
            status_code=401,
        )
    if status_code == 404:
        raise ProviderError(
            "station_not_found",
            provider=PROVIDER,
            detail="Station not found",
            status_code=404,
        )
    if status_code == 429:
        raise ProviderError(
            "provider_ratelimit",
            provider=PROVIDER,
            detail="Rate limit exceeded",
            status_code=429,
        )
    if status_code >= 400:
        raise ProviderError(
            "provider_http_error",
            provider=PROVIDER,
            detail=f"HTTP {status_code}",
            status_code=502,
        )


# =====================================================================
# Fetch principal
# =====================================================================

async def fetch_current(
    station_id: str,
    api_key: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = WU_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """
    Obtiene la observación actual de una estación WU.

    Parámetros
    ----------
    station_id : str
        Identificador de la estación (ej. ``IBARCE12345``).
    api_key : str
        API key de Weather Underground (no se loguea ni se cachea).
    client : httpx.AsyncClient | None
        Cliente HTTP a reutilizar. Si es ``None`` se crea uno efímero
        para esta llamada. Para servir muchas peticiones, **siempre**
        pasar uno compartido desde FastAPI (vía ``Depends``).
    timeout_s : float
        Timeout total de la petición en segundos.

    Devuelve
    --------
    dict con el mismo shape que el cliente legacy::

        {
            "Tc", "RH", "p_hpa", "Td", "wind", "gust",
            "feels_like", "heat_index", "wind_chill",
            "precip_rate", "precip_total", "wind_dir_deg",
            "solar_radiation", "uv",
            "epoch", "time_local", "time_utc",
            "lat", "lon", "elevation"
        }

    Lanza
    -----
    ProviderError
        En cualquier fallo de red, autenticación, formato o estación no
        encontrada. Ver mapeo en el docstring del módulo.
    """
    params = {
        "stationId": station_id,
        "format": "json",
        "units": "m",
        "apiKey": api_key,
        "numericPrecision": "decimal",
    }

    # Usamos un AsyncClient temporal si no nos pasan uno. En producción
    # FastAPI inyectará uno compartido (lifespan-managed) para reusar
    # keep-alive y limitar conexiones.
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)

    try:
        try:
            response = await client.get(WU_URL_CURRENT, params=params)
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "provider_timeout",
                provider=PROVIDER,
                detail=str(exc) or "Request timed out",
                status_code=504,
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderError(
                "provider_network_error",
                provider=PROVIDER,
                detail=str(exc) or "Network error",
                status_code=502,
            ) from exc

        _raise_for_http_status(response.status_code)

        try:
            data = response.json()
            obs = data["observations"][0]
            metric = obs["metric"]
        except (KeyError, IndexError, ValueError) as exc:
            raise ProviderError(
                "provider_bad_response",
                provider=PROVIDER,
                detail=f"Unexpected payload shape: {exc!r}",
                status_code=502,
            ) from exc
    finally:
        if owns_client:
            await client.aclose()

    return _normalize_current_observation(obs, metric)


def _normalize_current_observation(obs: Dict[str, Any], metric: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convierte el JSON crudo de WU al ``dict`` estable que consume el
    frontend. Aislado en función propia para poder testearlo con fixtures
    sin pegar HTTP.

    **Política de derivados**: ``Td``, ``feels_like``, ``heat_index`` se
    **calculan** desde campos primarios (Tc, RH, wind) vía
    ``domain.observation_pipeline.add_basic_derived``; NUNCA se toman del
    payload de WU. Esto garantiza consistencia entre proveedores: cada
    API usa su propia fórmula, y los valores que mostramos al usuario
    deben ser los nuestros. Lo mismo aplica a ``wind_chill`` (queda NaN
    aquí; no lo computamos en stateless current). Y a ``precip_rate``,
    que requiere historial temporal (lo calcula el pipeline desde la
    serie cuando se llama a ``/processed``).
    """
    raw_dir = _safe_float(obs.get("winddir"))
    wind_dir_deg = raw_dir % 360 if not _is_nan(raw_dir) else float("nan")

    # Campos primarios: solo lo que la estación REALMENTE mide.
    Tc = _safe_float(metric.get("temp"))
    RH = _safe_float(obs.get("humidity"))
    p_hpa = _safe_float(metric.get("pressure"))
    wind = _safe_float(metric.get("windSpeed"))
    gust = _safe_float(metric.get("windGust"))
    precip_total = _quantize_rain_mm_wu(_safe_float(metric.get("precipTotal")))
    solar_radiation = _safe_float(obs.get("solarRadiation"))
    uv_index = _safe_float(obs.get("uv"))

    epoch_raw = obs.get("epoch", 0)
    if not isinstance(epoch_raw, (int, float)) or epoch_raw <= 0:
        epoch = int(time.time())
    else:
        epoch = int(epoch_raw)

    lat = _safe_float(obs.get("lat"))
    lon = _safe_float(obs.get("lon"))
    elevation = _safe_float(obs.get("elev"))
    if _is_nan(elevation):
        elevation = _safe_float(obs.get("elevation"))

    observation: Dict[str, Any] = {
        "Tc": Tc,
        "RH": RH,
        "p_hpa": p_hpa,
        "wind": wind,
        "gust": gust,
        "wind_dir_deg": wind_dir_deg,
        # Derivados se rellenan vía add_basic_derived (NaN si no procede).
        "Td": float("nan"),
        "feels_like": float("nan"),
        "heat_index": float("nan"),
        # No calculamos wind_chill ni precip_rate en stateless current:
        # quedan NaN; el pipeline (``/processed``) los rellena cuando
        # tiene contexto (serie del día, altitud, etc.).
        "wind_chill": float("nan"),
        "precip_rate": float("nan"),
        "precip_total": precip_total,
        "solar_radiation": solar_radiation,
        "uv": uv_index,
        "epoch": epoch,
        "time_local": obs.get("obsTimeLocal", ""),
        "time_utc": obs.get("obsTimeUtc", ""),
        "lat": lat,
        "lon": lon,
        "elevation": elevation,
    }

    # Import diferido para evitar ciclo: ``domain`` no debe importar
    # ``server.services.wu``, pero ``wu`` puede usar ``domain`` para
    # cálculos puros. Lo hacemos local para que un cambio en
    # ``observation_pipeline`` no obligue a recargar todo el módulo de WU.
    from domain.observation_pipeline import add_basic_derived

    return add_basic_derived(observation)


# =====================================================================
# Series temporales del día (/all/1day)
# =====================================================================

# Keys vacías que devuelve ``_empty_today_series`` ante errores
# (manteniendo el contrato del frontend que espera listas vacías).
_EMPTY_SERIES_KEYS = (
    "epochs", "temps", "humidities", "dewpts", "pressures",
    "uv_indexes", "solar_radiations", "winds", "gusts", "wind_dirs",
)


async def fetch_today_series(
    station_id: str,
    api_key: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = WU_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """
    Obtiene las series temporales del día (~288 puntos a 5 min en una
    estación WU típica) y las normaliza a listas alineadas.

    Devuelve el mismo shape que el cliente legacy
    ``api.weather_underground.fetch_daily_timeseries``: listas paralelas
    de epochs, temps, humidities, dewpts, pressures, uv_indexes,
    solar_radiations, winds, gusts, wind_dirs, lat, lon, has_data.

    A diferencia del legacy (que devuelve dict vacío en cualquier error),
    aquí lanzamos ``ProviderError``: el contrato HTTP queda uniforme con
    ``fetch_current``. El cliente frontend ``utils.api_client`` traduce
    de vuelta a dict vacío para mantener compatibilidad con la UI
    existente.
    """
    params = {
        "stationId": station_id,
        "format": "json",
        "units": "m",
        "apiKey": api_key,
        "numericPrecision": "decimal",
    }

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)

    try:
        try:
            response = await client.get(WU_URL_DAILY, params=params)
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "provider_timeout",
                provider=PROVIDER,
                detail=str(exc) or "Request timed out",
                status_code=504,
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderError(
                "provider_network_error",
                provider=PROVIDER,
                detail=str(exc) or "Network error",
                status_code=502,
            ) from exc

        _raise_for_http_status(response.status_code)

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError(
                "provider_bad_response",
                provider=PROVIDER,
                detail=f"Invalid JSON: {exc!r}",
                status_code=502,
            ) from exc
    finally:
        if owns_client:
            await client.aclose()

    observations = data.get("observations", []) if isinstance(data, dict) else []
    if not isinstance(observations, list):
        # WU mal formado pero con HTTP 200: tratamos como serie vacía
        # (más útil para la UI que un 502; la pestaña queda sin gráfico
        # en vez de bloquear toda la app).
        return _empty_today_series()

    return _normalize_today_series(observations)


def _empty_today_series() -> Dict[str, Any]:
    """Shape vacío con todas las listas a [] para no romper el frontend."""
    series: Dict[str, Any] = {key: [] for key in _EMPTY_SERIES_KEYS}
    series["lat"] = float("nan")
    series["lon"] = float("nan")
    series["has_data"] = False
    return series


def _normalize_today_series(observations: list) -> Dict[str, Any]:
    """
    Recorre cada observación del payload ``/all/1day`` y construye las
    listas paralelas. Lógica de fallback de nombres clonada del cliente
    legacy: WU no es consistente entre estaciones (algunas reportan
    ``humidityAvg``, otras solo ``humidityHigh``, etc.).
    """
    epochs: list = []
    temps: list = []
    humidities: list = []
    dewpts: list = []
    pressures: list = []
    uv_indexes: list = []
    solar_radiations: list = []
    winds: list = []
    gusts: list = []
    wind_dirs: list = []

    lat_series = float("nan")
    lon_series = float("nan")

    for obs in observations:
        if not isinstance(obs, dict):
            continue
        epoch = obs.get("epoch", 0)
        if not isinstance(epoch, (int, float)) or epoch <= 0:
            continue

        metric = obs.get("metric", {})
        if not isinstance(metric, dict):
            metric = {}

        # Coordenadas: tomar la primera observación que las traiga.
        if _is_nan(lat_series):
            lat_series = _first_valid_number(
                obs.get("lat"), obs.get("latitude"),
                metric.get("lat"), metric.get("latitude"),
            )
        if _is_nan(lon_series):
            lon_series = _first_valid_number(
                obs.get("lon"), obs.get("longitude"),
                metric.get("lon"), metric.get("longitude"),
            )

        # Temperatura: avg → high → low (algunas estaciones solo reportan algunos).
        temp = _first_valid_number(
            metric.get("tempAvg"), metric.get("tempHigh"), metric.get("tempLow"),
        )

        humidity = _first_valid_number(
            obs.get("humidityAvg"), obs.get("humidityHigh"), obs.get("humidityLow"),
        )

        # all/1day no expone dewptAvg; aproximamos con dewptLow (más representativo
        # del contenido de humedad real del día que dewptHigh).
        dewpt = _first_valid_number(metric.get("dewptLow"), metric.get("dewptHigh"))

        # Idem para presión: tomamos pressureMin como proxy.
        pressure = _first_valid_number(metric.get("pressureMin"), metric.get("pressureMax"))

        uv_index = _first_valid_number(
            obs.get("uv"), obs.get("uvAvg"), obs.get("uvHigh"),
            obs.get("uvIndex"), obs.get("uvIndexAvg"), obs.get("uvIndexHigh"),
            metric.get("uv"), metric.get("uvAvg"), metric.get("uvHigh"),
            metric.get("uvIndex"), metric.get("uvIndexAvg"), metric.get("uvIndexHigh"),
        )

        solar_radiation = _first_valid_number(
            obs.get("solarRadiation"), obs.get("solarRadiationHigh"),
            metric.get("solarRadiation"), metric.get("solarRadiationHigh"),
        )

        wind = _first_valid_number(
            metric.get("windSpeed"), metric.get("windspeed"),
            metric.get("windSpeedAvg"), metric.get("windspeedAvg"),
            metric.get("windspeedHigh"), metric.get("windSpeedHigh"),
            obs.get("windSpeed"), obs.get("windspeed"),
            obs.get("windSpeedAvg"), obs.get("windspeedAvg"),
        )

        gust = _first_valid_number(
            metric.get("windgustHigh"), metric.get("windGust"),
            metric.get("windgust"), metric.get("windgustAvg"),
            obs.get("windgustHigh"), obs.get("windGust"), obs.get("windGustHigh"),
        )

        # Dirección de viento: probar muchas variantes nombrales y de
        # codificación (cardinal, grados, en obs o en metric).
        raw_dir = _first_valid_wind_dir(
            obs.get("winddir"), obs.get("windDir"),
            obs.get("winddirAvg"), obs.get("windDirAvg"),
            obs.get("windDirection"), obs.get("windDirectionAvg"),
            obs.get("windCardinal"), obs.get("windDirectionCardinal"),
            metric.get("winddir"), metric.get("windDir"),
            metric.get("winddirAvg"), metric.get("windDirAvg"),
            metric.get("windDirection"), metric.get("windDirectionAvg"),
            metric.get("windCardinal"), metric.get("windDirectionCardinal"),
        )
        wind_dir = raw_dir % 360 if not _is_nan(raw_dir) else float("nan")

        # Si hay temperatura, añadimos el punto; el resto puede ser NaN.
        # Sin temperatura el punto no es útil para ningún gráfico, lo
        # descartamos.
        if _is_nan(temp):
            continue

        epochs.append(int(epoch))
        temps.append(float(temp))
        humidities.append(float(humidity) if not _is_nan(humidity) else float("nan"))
        dewpts.append(float(dewpt) if not _is_nan(dewpt) else float("nan"))
        pressures.append(float(pressure) if not _is_nan(pressure) else float("nan"))
        uv_indexes.append(float(uv_index) if not _is_nan(uv_index) else float("nan"))
        solar_radiations.append(float(solar_radiation) if not _is_nan(solar_radiation) else float("nan"))
        winds.append(float(wind) if not _is_nan(wind) else float("nan"))
        gusts.append(float(gust) if not _is_nan(gust) else float("nan"))
        wind_dirs.append(float(wind_dir) if not _is_nan(wind_dir) else float("nan"))

    return {
        "epochs": epochs,
        "temps": temps,
        "humidities": humidities,
        "dewpts": dewpts,
        "pressures": pressures,
        "uv_indexes": uv_indexes,
        "solar_radiations": solar_radiations,
        "winds": winds,
        "gusts": gusts,
        "wind_dirs": wind_dirs,
        "lat": lat_series,
        "lon": lon_series,
        "has_data": len(epochs) > 0,
    }
