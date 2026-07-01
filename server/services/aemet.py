"""
Servicio puro de AEMET OpenData.

Versión "limpia" del cliente AEMET legacy: sin ``streamlit``, sin
``st.cache_data``, sin ``st.session_state``. Cliente HTTP
``httpx.AsyncClient`` para integrarse con FastAPI.

Diferencias clave con WU:

1. **Auth**: AEMET usa una API key **del servidor** (env var
   ``METEOLABX_AEMET_API_KEY``), no per-user. Por eso ``fetch_current``
   recibe la key vía ``ProcessingContext`` / settings, no en el body
   de la request.

2. **Patrón 2-step**: AEMET responde primero con una URL temporal,
   luego hay que ir a esa URL a por los datos reales. Sumamos dos
   round-trips por petición (timeout extendido en el segundo).

3. **Encoding**: el endpoint de datos a veces devuelve latin-1 en vez
   de UTF-8. ``httpx`` decodifica con fallback explícito.

4. **Shape de respuesta**: AEMET reporta ``p_hpa`` como MSL y
   ``p_station`` como absoluta. El pipeline pide ``p_abs_hpa``; lo
   rellenamos desde ``p_station`` cuando está disponible, con
   ``msl_to_absolute`` como fallback.

5. **Sin solar/uv**: la red AEMET no tiene piranómetros ni sensores UV
   en la mayoría de estaciones; queda NaN. ``has_radiation`` será
   False salvo que aparezcan en alguna estación específica.

Mapeo de errores ``RuntimeError`` → ``ProviderError``:

    timeout       → provider_timeout         (504)
    network       → provider_network_error   (502)
    estado != 200 + URL nula → provider_bad_response (502)
    api_key vacío → provider_unauthorized    (401)
    cualquier 4xx/5xx HTTP no clasificado → provider_http_error (502)
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from server.schemas.errors import ProviderError
from server.services.cache import AsyncTTLCache, make_cache_key

logger = logging.getLogger(__name__)

PROVIDER = "AEMET"
BASE_URL = "https://opendata.aemet.es/opendata/api"
LOCAL_TZ = ZoneInfo("Europe/Madrid")
_DAILY_EXTREMES_CACHE = AsyncTTLCache[Dict[str, float]](
    default_ttl_s=10 * 60,
    max_entries=1000,
)


# =====================================================================
# Helpers de parsing (clonados de services/aemet.py legacy y limpiados)
# =====================================================================

def _is_nan(value: Any) -> bool:
    return value != value


def _parse_num(value: Any) -> float:
    """
    Parseo robusto de números AEMET (coma decimal, vacíos, paréntesis…).

    Acepta: ``"22.4"``, ``"22,4"``, ``"37.4(27)"`` (extremo con día entre
    paréntesis), ``"99/21.1"`` (dir/vel del viento)…
    """
    if value is None:
        return float("nan")
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except Exception:
            return float("nan")
    try:
        s = str(value).strip()
        if not s:
            return float("nan")
        paren_idx = s.find("(")
        if paren_idx > 0:
            s = s[:paren_idx].strip()
        if "/" in s:
            s = s.rsplit("/", 1)[-1].strip()
        s = s.replace(",", ".")
        if s.lower() in {"ip", "nan", "none", "--", "-"}:
            return float("nan")
        return float(s)
    except Exception:
        return float("nan")


def _parse_epoch_any(fint_str: Any) -> Optional[int]:
    """Parsea timestamps en varios formatos habituales de AEMET."""
    if not fint_str:
        return None

    if isinstance(fint_str, (int, float)):
        try:
            epoch = int(fint_str)
        except (TypeError, ValueError):
            return None
        if epoch > 10**12:
            epoch = int(epoch / 1000)
        return epoch if epoch > 0 else None

    raw = str(fint_str).strip()
    clean = raw.replace("UTC", "").replace("Z", "").strip()
    clean = re.sub(r"([+-])(\d{2})(\d{2})$", r"\1\2:\3", clean)

    try:
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        pass

    patterns = [
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
    ]
    for pattern in patterns:
        try:
            dt = datetime.strptime(clean, pattern).replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except Exception:
            continue

    return None


_CARDINAL_ES_EN = {
    "N": 0, "NNE": 22.5, "NE": 45, "ENE": 67.5,
    "E": 90, "ESE": 112.5, "SE": 135, "SSE": 157.5,
    "S": 180, "SSW": 202.5, "SO": 225, "SW": 225, "WSW": 247.5, "OSO": 247.5,
    "W": 270, "O": 270, "WNW": 292.5, "ONO": 292.5,
    "NW": 315, "NO": 315, "NNW": 337.5, "NNO": 337.5,
    "CALMA": 0.0, "CALM": 0.0,
}


def _parse_wind_dir_deg(value: Any) -> float:
    """Parsea dirección de viento (numérico o cardinal ES/EN)."""
    if value is None:
        return float("nan")
    f = _parse_num(value)
    if not _is_nan(f):
        return f % 360
    s = str(value).strip().upper()
    if not s:
        return float("nan")
    if s in _CARDINAL_ES_EN:
        return _CARDINAL_ES_EN[s]
    return float("nan")


def _field(record: Dict[str, Any], *keys: str) -> Any:
    """Toma el primer campo no-vacío entre ``keys``, case-insensitive."""
    record_ci = {str(k).lower(): v for k, v in record.items()}
    for key in keys:
        value = record.get(key)
        if value is None:
            value = record_ci.get(str(key).lower())
        if value is not None and value != "":
            return value
    return None


def _ms_to_kmh(ms: Any) -> float:
    num = _parse_num(ms)
    return num * 3.6 if not _is_nan(num) else float("nan")


# =====================================================================
# Helper común: patrón 2-step de AEMET
# =====================================================================

async def _fetch_aemet_two_step(
    endpoint_path: str,
    api_key: str,
    *,
    client: httpx.AsyncClient,
    step1_timeout_s: float,
    step2_timeout_s: float,
) -> Any:
    """
    Ejecuta el patrón estándar de AEMET OpenData:

    1. ``GET {BASE_URL}{endpoint_path}`` con header ``api_key`` →
       JSON con campos ``estado`` y ``datos`` (URL temporal).
    2. ``GET {datos_url}`` (sin headers) → la respuesta real, que
       suele ser una lista de records JSON (a veces en latin-1).

    Devuelve el contenido del paso 2 (lista o dict) tal cual.
    Mapea cualquier error a ``ProviderError`` con códigos estables.
    """
    # ----- Paso 1 -----
    full_url = f"{BASE_URL}{endpoint_path}"
    headers = {"api_key": api_key}

    try:
        response = await client.get(full_url, headers=headers, timeout=step1_timeout_s)
    except httpx.TimeoutException as exc:
        raise ProviderError(
            "provider_timeout",
            provider=PROVIDER,
            detail=f"AEMET step 1 timeout: {exc}",
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
        result = response.json()
    except ValueError as exc:
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail=f"Step 1 JSON inválido: {exc!r}",
            status_code=502,
        ) from exc

    if not isinstance(result, dict):
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail="Step 1 no devolvió un objeto JSON",
            status_code=502,
        )

    # AEMET reporta auth/404/etc en el body, no en el HTTP status.
    aemet_estado = result.get("estado")
    if aemet_estado == 401:
        raise ProviderError(
            "provider_unauthorized",
            provider=PROVIDER,
            detail=str(result.get("descripcion") or "AEMET 401"),
            status_code=401,
        )
    if aemet_estado == 404:
        raise ProviderError(
            "station_not_found",
            provider=PROVIDER,
            detail=str(result.get("descripcion") or "AEMET 404"),
            status_code=404,
        )
    if aemet_estado == 429:
        raise ProviderError(
            "provider_ratelimit",
            provider=PROVIDER,
            detail=str(result.get("descripcion") or "AEMET 429"),
            status_code=429,
        )
    if aemet_estado and aemet_estado != 200:
        raise ProviderError(
            "provider_http_error",
            provider=PROVIDER,
            detail=f"AEMET estado={aemet_estado}: {result.get('descripcion')}",
            status_code=502,
        )

    datos_url = result.get("datos")
    if not datos_url:
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail="AEMET no devolvió URL de datos",
            status_code=502,
        )

    # ----- Paso 2 -----
    try:
        data_response = await client.get(datos_url, timeout=step2_timeout_s)
    except httpx.TimeoutException as exc:
        raise ProviderError(
            "provider_timeout",
            provider=PROVIDER,
            detail=f"AEMET step 2 timeout: {exc}",
            status_code=504,
        ) from exc
    except httpx.RequestError as exc:
        raise ProviderError(
            "provider_network_error",
            provider=PROVIDER,
            detail=str(exc) or "Network error step 2",
            status_code=502,
        ) from exc

    _raise_for_http_status(data_response.status_code)

    # AEMET a veces devuelve latin-1 en este endpoint.
    try:
        return data_response.json()
    except ValueError:
        try:
            return _json.loads(data_response.content.decode("latin-1"))
        except Exception as exc:
            raise ProviderError(
                "provider_bad_response",
                provider=PROVIDER,
                detail=f"Step 2 JSON inválido (UTF-8 y latin-1): {exc!r}",
                status_code=502,
            ) from exc


# =====================================================================
# Fetch principal: observación actual
# =====================================================================

async def fetch_current(
    station_id: str,
    api_key: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    step1_timeout_s: float = 15.0,
    step2_timeout_s: float = 60.0,
    include_daily_extremes: bool = True,
) -> Dict[str, Any]:
    """
    Obtiene la observación actual de una estación AEMET (IDEMA).

    Parámetros
    ----------
    station_id : str
        Código IDEMA de la estación (ej. ``"0201X"``).
    api_key : str
        API key de AEMET (server-side). Vacío → ``provider_unauthorized``.
    client : httpx.AsyncClient | None
        Cliente HTTP compartido. Si es ``None`` se crea uno efímero.
    step1_timeout_s : float
        Timeout para el primer paso (metadata + URL temporal).
    step2_timeout_s : float
        Timeout para el segundo paso (descargar datos). AEMET tiende a
        ser lento; el legacy usa 60s.

    Devuelve un ``dict`` con el shape de observación canónica más:
    ``p_station`` (presión absoluta reportada por AEMET; algunas
    estaciones la traen). ``Td``, ``feels_like``, ``heat_index`` se
    calculan vía ``domain.observation_pipeline.add_basic_derived``.
    """
    if not api_key:
        raise ProviderError(
            "provider_unauthorized",
            provider=PROVIDER,
            detail="Missing AEMET_API_KEY",
            status_code=401,
        )

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=step1_timeout_s)

    try:
        endpoint_path = f"/observacion/convencional/datos/estacion/{station_id}"
        today_local = datetime.now(tz=LOCAL_TZ).date()
        current_task = _fetch_aemet_two_step(
            endpoint_path,
            api_key,
            client=client,
            step1_timeout_s=step1_timeout_s,
            step2_timeout_s=step2_timeout_s,
        )
        if include_daily_extremes:
            daily_task = _cached_aemet_daily_extremes(
                station_id,
                api_key,
                client=client,
                day=today_local,
            )
            data, daily_result = await asyncio.gather(
                current_task, daily_task, return_exceptions=True,
            )
        else:
            data = await current_task
            daily_result = {}
        if isinstance(data, BaseException):
            raise data
        fallback_daily_extremes = (
            _daily_extremes_from_aemet_records(data)
            if isinstance(data, list)
            else _daily_extremes_from_aemet_records([data])
            if isinstance(data, dict)
            else {}
        )
        if isinstance(daily_result, BaseException):
            logger.warning("AEMET extremos diarios no disponibles para %s: %s", station_id, daily_result)
            official_daily_extremes = fallback_daily_extremes
        else:
            official_daily_extremes = _merge_daily_extremes(daily_result, fallback_daily_extremes)

        # AEMET devuelve lista ordenada cronológicamente; el último es el más reciente.
        if isinstance(data, list) and data:
            record = data[-1]
        elif isinstance(data, dict):
            record = data
        else:
            raise ProviderError(
                "provider_bad_response",
                provider=PROVIDER,
                detail="Step 2 devolvió tipo inesperado",
                status_code=502,
            )

        if not isinstance(record, dict):
            raise ProviderError(
                "provider_bad_response",
                provider=PROVIDER,
                detail="Último registro no es un objeto",
                status_code=502,
            )
    finally:
        if owns_client:
            await client.aclose()

    return _normalize_aemet_record(record, daily_extremes=official_daily_extremes)


# =====================================================================
# Serie diezminutal del día (~144 puntos por estación)
# =====================================================================

async def fetch_today_series(
    station_id: str,
    api_key: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    step1_timeout_s: float = 15.0,
    step2_timeout_s: float = 60.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Serie temporal del día de una estación AEMET, a partir del endpoint
    diezminutal por estación.

    AEMET reporta la serie con una cadencia típica de **10 min**
    (~144 puntos/día). Si el endpoint diezminutal devuelve serie vacía,
    devolvemos shape vacío con ``has_data: False`` igual que WU (en lugar
    de probar el endpoint legacy con todas las estaciones, que es
    pesadísimo y rara vez aporta valor).

    Diferencias con WU:
    - AEMET reporta viento en **m/s** → convertimos a km/h.
    - AEMET reporta presión MSL (``pres_nmar``) y absoluta (``pres``).
      El shape canónico usa ``pressures`` (MSL); ``pressures_abs`` se
      podría incluir si lo necesitamos en el futuro.
    - AEMET no expone radiación ni UV → listas vacías de esos campos.
    """
    if not api_key:
        raise ProviderError(
            "provider_unauthorized",
            provider=PROVIDER,
            detail="Missing AEMET_API_KEY",
            status_code=401,
        )

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=step1_timeout_s)

    try:
        # El endpoint diezminutal por estación devuelve el día UTC. Para
        # que el gráfico del día LOCAL no empiece a las 01:00/02:00 (CET/
        # CEST), pedimos también la fecha UTC anterior (endpoint datado)
        # y luego recortamos al día local. El fetch de ayer es
        # best-effort: si falla, el día UTC sigue siendo útil.
        import asyncio as _asyncio
        from urllib.parse import quote as _quote

        endpoint_today = f"/observacion/convencional/diezminutal/datos/estacion/{station_id}"

        now_local = (now or datetime.now(tz=LOCAL_TZ)).astimezone(LOCAL_TZ)
        yesterday_utc = (now_local.astimezone(timezone.utc) - timedelta(days=1)).date()
        fecha = _quote(f"{yesterday_utc.isoformat()}T00:00:00UTC", safe="")
        endpoint_yesterday = (
            f"/observacion/convencional/diezminutal/datos/fecha/{fecha}/estacion/{station_id}"
        )

        async def _fetch(path: str) -> list:
            payload = await _fetch_aemet_two_step(
                path, api_key, client=client,
                step1_timeout_s=step1_timeout_s, step2_timeout_s=step2_timeout_s,
            )
            return payload if isinstance(payload, list) else []

        today_result, yesterday_result = await _asyncio.gather(
            _fetch(endpoint_today), _fetch(endpoint_yesterday), return_exceptions=True,
        )
    finally:
        if owns_client:
            await client.aclose()

    if isinstance(today_result, BaseException):
        raise today_result
    observations = list(today_result)
    if not isinstance(yesterday_result, BaseException):
        observations.extend(yesterday_result)

    if not observations:
        return _empty_today_series()

    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return _normalize_today_series(
        observations,
        start_epoch=int(day_start.timestamp()),
        end_epoch=int((day_start + timedelta(days=1)).timestamp()),
    )


def _empty_today_series() -> Dict[str, Any]:
    """Shape vacío que coincide con TodaySeries del schema."""
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


def _normalize_today_series(
    observations: List[Dict[str, Any]],
    *,
    start_epoch: Optional[int] = None,
    end_epoch: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Convierte la lista de records diezminutales AEMET en arrays
    paralelas alineadas por epoch.

    Mantiene posición incluso cuando un punto carece de algún campo
    (NaN en esa slot). Solo descarta el punto si no hay timestamp
    parseable.
    """
    rows: list[tuple[int, float, float, float, float, float, float, float, float]] = []
    lat_seen: float = float("nan")
    lon_seen: float = float("nan")

    for record in observations:
        if not isinstance(record, dict):
            continue

        ts = _field(record, "fint", "FINT", "Fecha", "fecha", "fhora")
        epoch = _parse_epoch_any(ts) if ts else None
        if epoch is None or epoch <= 0:
            continue
        # Recorte al día local cuando el caller lo pide (la serie del
        # día mezcla el día UTC actual + la fecha UTC anterior).
        if start_epoch is not None and epoch < start_epoch:
            continue
        if end_epoch is not None and epoch >= end_epoch:
            continue

        # Primary measurements
        temp = _parse_num(_field(record, "ta", "TA", "t", "T", "temp", "TEMP", "tpre", "TPRE"))
        rh = _parse_num(_field(record, "hr", "HR", "hrel", "HREL"))
        # Presión: priorizamos MSL para coherencia con WU (cuyo p_hpa también es MSL).
        # La absoluta queda derivada en el pipeline vía msl_to_absolute.
        p_msl = _parse_num(_field(record, "pres_nmar", "PRES_NMAR", "pnm", "PNM"))

        # Viento: en m/s en AEMET; convertimos a km/h.
        wind = _ms_to_kmh(
            _field(record, "VV10m", "vv10m", "vv", "VV", "ff", "FF", "viento"),
        )
        gust = _ms_to_kmh(
            _field(record, "VMAX10m", "vmax10m", "vmax", "VMAX", "fx", "FX", "racha"),
        )
        wind_dir = _parse_wind_dir_deg(
            _field(record, "DV10m", "dv10m", "dv", "DV", "dd", "DD", "dir", "DIR"),
        )

        # Punto de rocío: AEMET no lo expone directamente en diezminutal,
        # se calcula en el pipeline a partir de Tc + RH. Dejamos NaN.
        dewpt = float("nan")

        if _is_nan(temp) and _is_nan(rh) and _is_nan(p_msl) and _is_nan(wind):
            # Punto totalmente vacío → descartamos.
            continue

        # Coordenadas: la mayoría de records las traen iguales; nos
        # quedamos con las del primer no-nan.
        if _is_nan(lat_seen):
            lat_seen = _parse_num(_field(record, "lat", "LAT"))
        if _is_nan(lon_seen):
            lon_seen = _parse_num(_field(record, "lon", "LON"))

        rows.append((epoch, temp, rh, dewpt, p_msl, wind, gust, wind_dir,
                     float("nan")))  # último slot reservado para futuras métricas (uv, etc.)

    if not rows:
        return _empty_today_series()

    # Orden cronológico ascendente; dedup por epoch.
    rows.sort(key=lambda item: item[0])
    seen: dict[int, tuple] = {}
    for row in rows:
        seen[row[0]] = row

    epochs_sorted = sorted(seen.keys())
    epochs: list[int] = []
    temps: list[float] = []
    humidities: list[float] = []
    dewpts: list[float] = []
    pressures: list[float] = []
    winds: list[float] = []
    gusts: list[float] = []
    wind_dirs: list[float] = []

    for ep in epochs_sorted:
        _ep, temp, rh, dewpt, p_msl, wind, gust, wind_dir, _reserved = seen[ep]
        epochs.append(int(_ep))
        temps.append(float(temp))
        humidities.append(float(rh))
        dewpts.append(float(dewpt))
        pressures.append(float(p_msl))
        winds.append(float(wind))
        gusts.append(float(gust))
        wind_dirs.append(float(wind_dir))

    return {
        "epochs": epochs,
        "temps": temps,
        "humidities": humidities,
        "dewpts": dewpts,
        "pressures": pressures,
        "uv_indexes": [float("nan")] * len(epochs),       # AEMET conv. no expone UV
        "solar_radiations": [float("nan")] * len(epochs),  # idem
        "winds": winds,
        "gusts": gusts,
        "wind_dirs": wind_dirs,
        "lat": lat_seen,
        "lon": lon_seen,
        "has_data": len(epochs) > 0,
    }


def _raise_for_http_status(status_code: int) -> None:
    """Mapeo HTTP → ProviderError para errores HTTP-level (a diferencia
    del estado en el body que AEMET usa para auth)."""
    if status_code == 401:
        raise ProviderError(
            "provider_unauthorized",
            provider=PROVIDER,
            detail="Invalid AEMET API key (HTTP 401)",
            status_code=401,
        )
    if status_code == 404:
        raise ProviderError(
            "station_not_found",
            provider=PROVIDER,
            detail="Station not found (HTTP 404)",
            status_code=404,
        )
    if status_code == 429:
        raise ProviderError(
            "provider_ratelimit",
            provider=PROVIDER,
            detail="Rate limit (HTTP 429)",
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
# Normalización: record AEMET → shape canónico
# =====================================================================


def _daily_extremes_from_aemet_records(records: List[Dict[str, Any]]) -> Dict[str, float]:
    temp_max_values: list[float] = []
    temp_min_values: list[float] = []
    rh_max_values: list[float] = []
    rh_min_values: list[float] = []
    gust_max_values: list[float] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        temp_max = _parse_num(_field(record, "tamax", "TAMAX", "ta_max", "TA_MAX", "tmax", "TMAX"))
        temp_min = _parse_num(_field(record, "tamin", "TAMIN", "ta_min", "TA_MIN", "tmin", "TMIN"))
        rh_max = _parse_num(_field(record, "hrmax", "HRMAX", "hr_max", "HR_MAX", "hmax", "HMAX"))
        rh_min = _parse_num(_field(record, "hrmin", "HRMIN", "hr_min", "HR_MIN", "hmin", "HMIN"))
        rh = _parse_num(_field(record, "hr", "HR", "hrel", "HREL"))
        gust = _ms_to_kmh(
            _field(record, "VMAX10m", "vmax10m", "vmax", "VMAX", "fx", "FX", "racha", "RACHA")
        )
        if not _is_nan(temp_max):
            temp_max_values.append(temp_max)
        if not _is_nan(temp_min):
            temp_min_values.append(temp_min)
        if not _is_nan(rh_max):
            rh_max_values.append(rh_max)
        if not _is_nan(rh_min):
            rh_min_values.append(rh_min)
        if _is_nan(rh_max) and not _is_nan(rh):
            rh_max_values.append(rh)
        if _is_nan(rh_min) and not _is_nan(rh):
            rh_min_values.append(rh)
        if not _is_nan(gust):
            gust_max_values.append(gust)
    return {
        "temp_max": max(temp_max_values) if temp_max_values else float("nan"),
        "temp_min": min(temp_min_values) if temp_min_values else float("nan"),
        "rh_max": max(rh_max_values) if rh_max_values else float("nan"),
        "rh_min": min(rh_min_values) if rh_min_values else float("nan"),
        "gust_max": max(gust_max_values) if gust_max_values else float("nan"),
    }


def _merge_daily_extremes(primary: Dict[str, Any], fallback: Dict[str, Any]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for key in ("temp_max", "temp_min", "rh_max", "rh_min", "gust_max"):
        value = _parse_num(primary.get(key)) if isinstance(primary, dict) else float("nan")
        if _is_nan(value):
            value = _parse_num(fallback.get(key)) if isinstance(fallback, dict) else float("nan")
        out[key] = value
    return out


async def _cached_aemet_daily_extremes(
    station_id: str,
    api_key: str,
    *,
    client: httpx.AsyncClient,
    day,
) -> Dict[str, float]:
    station = str(station_id).strip().upper()
    day_key = day.isoformat()
    cache_key = make_cache_key("AEMET", f"daily-extremes:{day_key}", station, api_key)
    return await _DAILY_EXTREMES_CACHE.get_or_fetch(
        cache_key,
        lambda: _fetch_aemet_daily_extremes(station, api_key, client=client, day=day),
    )


async def _fetch_aemet_daily_extremes(
    station_id: str,
    api_key: str,
    *,
    client: httpx.AsyncClient,
    day,
) -> Dict[str, float]:
    fecha_ini = quote(f"{day.strftime('%Y-%m-%d')}T00:00:00UTC", safe="")
    fecha_fin = quote(f"{day.strftime('%Y-%m-%d')}T23:59:59UTC", safe="")
    endpoint = (
        f"/valores/climatologicos/diarios/datos/"
        f"fechaini/{fecha_ini}/fechafin/{fecha_fin}/estacion/{station_id}"
    )
    payload = await _fetch_aemet_two_step(
        endpoint,
        api_key,
        client=client,
        step1_timeout_s=10.0,
        step2_timeout_s=20.0,
    )
    records = payload if isinstance(payload, list) else [payload] if isinstance(payload, dict) else []
    for record in records:
        if not isinstance(record, dict):
            continue
        temp_max = _parse_num(_field(record, "tmax", "TMAX", "tamax", "TAMAX"))
        temp_min = _parse_num(_field(record, "tmin", "TMIN", "tamin", "TAMIN"))
        rh_max = _parse_num(_field(record, "hrmax", "HRMAX", "hr_max", "HR_MAX", "hmax", "HMAX"))
        rh_min = _parse_num(_field(record, "hrmin", "HRMIN", "hr_min", "HR_MIN", "hmin", "HMIN"))
        gust_max = _ms_to_kmh(
            _field(record, "racha", "RACHA", "vmax", "VMAX", "fx", "FX", "VMAX10m", "vmax10m")
        )
        return {
            "temp_max": temp_max,
            "temp_min": temp_min,
            "rh_max": rh_max,
            "rh_min": rh_min,
            "gust_max": gust_max,
        }
    return {}


def _normalize_aemet_record(
    record: Dict[str, Any],
    *,
    daily_extremes: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Convierte un record AEMET (último observación de la lista que
    devuelve OpenData) al ``dict`` canónico que comparte con WU.

    El shape de salida es **idéntico** al de ``server.services.wu``
    (mismas keys + ``add_basic_derived`` aplicado), excepto que AEMET
    no reporta ``solar_radiation`` ni ``uv`` (quedan NaN).
    """
    # Timestamp
    fint = _field(record, "fint", "FINT", "Fecha", "fecha", "fhora")
    epoch = _parse_epoch_any(fint) if fint else None
    if epoch is None or epoch <= 0:
        epoch = int(time.time())

    # Temperatura
    Tc = _parse_num(_field(record, "ta", "TA", "t", "T", "temp", "TEMP", "tpre", "TPRE"))
    official_extremes = (
        dict(daily_extremes)
        if isinstance(daily_extremes, dict)
        else _daily_extremes_from_aemet_records([record])
    )

    # Humedad relativa
    RH = _parse_num(_field(record, "hr", "HR", "hrel", "HREL"))

    # Presiones: AEMET reporta ambas — MSL ("pres_nmar") y absoluta de
    # estación ("pres"). Mantenemos ambas para que el pipeline pueda
    # elegir; ``p_abs_hpa`` se rellena con ``p_station`` cuando está,
    # si no se computará en el endpoint vía ``msl_to_absolute``.
    p_hpa = _parse_num(_field(record, "pres_nmar", "PRES_NMAR", "pnm", "PNM"))
    p_station = _parse_num(_field(record, "pres", "PRES"))

    # Viento: AEMET en m/s
    wind_kmh = _ms_to_kmh(
        _field(record, "VV10m", "vv10m", "vv", "VV", "ff", "FF", "viento"),
    )
    gust_kmh = _ms_to_kmh(
        _field(record, "VMAX10m", "vmax10m", "vmax", "VMAX", "fx", "FX", "racha", "RACHA"),
    )
    wind_dir_deg = _parse_wind_dir_deg(
        _field(record, "DV10m", "dv10m", "dv", "DV", "dd", "DD", "dir", "DIR"),
    )

    # Precipitación
    precip_total = _parse_num(_field(record, "prec", "PREC", "precip", "PR", "pr", "lluvia"))

    # Metadatos espaciales
    lat = _parse_num(_field(record, "lat", "LAT"))
    lon = _parse_num(_field(record, "lon", "LON"))
    elevation = _parse_num(_field(record, "alt", "ALT", "elev", "ELEV"))

    observation: Dict[str, Any] = {
        "Tc": Tc,
        "RH": RH,
        "p_hpa": p_hpa,
        "p_abs_hpa": p_station,  # AEMET reporta absoluta nativa; pipeline la usará directamente
        "wind": wind_kmh,
        "gust": gust_kmh,
        "wind_dir_deg": wind_dir_deg,
        # Derivados: se rellenan con add_basic_derived (NUNCA del API)
        "Td": float("nan"),
        "feels_like": float("nan"),
        "heat_index": float("nan"),
        # Wind chill y precip_rate: stateless, NaN aquí (el pipeline los
        # rellena cuando es aplicable y tiene contexto)
        "wind_chill": float("nan"),
        "precip_rate": float("nan"),
        "precip_total": precip_total,
        # AEMET no reporta radiación ni UV en la red convencional
        "solar_radiation": float("nan"),
        "uv": float("nan"),
        # Tiempo y posición
        "epoch": epoch,
        "time_local": str(fint or ""),
        "time_utc": "",
        "lat": lat,
        "lon": lon,
        "elevation": elevation,
        "station_name": str(_field(record, "ubi", "UBI") or "").strip(),
        "daily_extremes": {
            "temp_max": _parse_num(official_extremes.get("temp_max")),
            "temp_min": _parse_num(official_extremes.get("temp_min")),
            "rh_max": _parse_num(official_extremes.get("rh_max")),
            "rh_min": _parse_num(official_extremes.get("rh_min")),
            "gust_max": _parse_num(official_extremes.get("gust_max")),
        },
    }

    # Aplicar derivadas básicas (Td Magnus-Tetens, feels_like Steadman,
    # heat_index Rothfusz). Import diferido para no contaminar con
    # streamlit si el módulo se importa sin haber arrancado domain todavía.
    from domain.observation_pipeline import add_basic_derived
    return add_basic_derived(observation)


async def fetch_recent_series(
    station_id: str,
    api_key: str,
    *,
    days_back: int = 7,
    client: Optional[httpx.AsyncClient] = None,
    step1_timeout_s: float = 15.0,
    step2_timeout_s: float = 60.0,
    fine: bool = False,
) -> Dict[str, Any]:
    """
    Serie reciente (T/HR/presión MSL) para tendencias, desde el endpoint
    de climatologías horarias (una sola llamada two-step para toda la
    ventana). Remuestreo a buckets de 3 h (última lectura por bucket).

    ``fine=True`` usa buckets de 1 h (lo pide el lookback de
    ``/series/today`` para que la tendencia de presión 3h arranque a las
    00:00 local; ver nota en el lookback del router).
    """
    if not api_key:
        raise ProviderError(
            "provider_unauthorized",
            provider=PROVIDER,
            detail="Missing AEMET_API_KEY",
            status_code=401,
        )

    from urllib.parse import quote as _quote

    now_utc = datetime.now(timezone.utc)
    ini_utc = now_utc - timedelta(days=max(1, int(days_back)))
    fecha_ini = _quote(ini_utc.strftime("%Y-%m-%dT%H:%M:%SUTC"), safe="")
    fecha_fin = _quote(now_utc.strftime("%Y-%m-%dT%H:%M:%SUTC"), safe="")
    endpoint_path = (
        f"/valores/climatologicos/horarios/datos/"
        f"fechaini/{fecha_ini}/fechafin/{fecha_fin}/estacion/{station_id}"
    )

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=step1_timeout_s)
    try:
        data = await _fetch_aemet_two_step(
            endpoint_path, api_key, client=client,
            step1_timeout_s=step1_timeout_s, step2_timeout_s=step2_timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()

    rows = data if isinstance(data, list) else []
    bucket_s = 3600 if fine else 3 * 3600
    buckets: Dict[int, tuple] = {}
    lat = lon = float("nan")
    for record in rows:
        if not isinstance(record, dict):
            continue
        ts = _field(record, "fint", "FINT", "Fecha", "fecha", "fhora")
        epoch = _parse_epoch_any(ts) if ts else None
        if epoch is None:
            continue
        temp = _parse_num(_field(record, "ta", "TA", "t", "T", "temp", "TEMP", "tpre", "TPRE"))
        rh = _parse_num(_field(record, "hr", "HR", "hrel", "HREL"))
        p_msl = _parse_num(_field(record, "pres_nmar", "PRES_NMAR", "pnm", "PNM"))
        if _is_nan(p_msl):
            p_abs = _parse_num(_field(record, "pres", "PRES"))
            alt = _parse_num(_field(record, "alt", "ALT"))
            if not _is_nan(p_abs) and not _is_nan(alt):
                import math as _math
                p_msl = p_abs * _math.exp(alt / 8000.0)
        if _is_nan(temp) and _is_nan(rh) and _is_nan(p_msl):
            continue
        if _is_nan(lat):
            lat = _parse_num(_field(record, "lat", "LAT"))
            lon = _parse_num(_field(record, "lon", "LON"))
        bucket = (int(epoch) // bucket_s) * bucket_s
        current = buckets.get(bucket)
        if current is None or int(epoch) >= current[0]:
            buckets[bucket] = (int(epoch), temp, rh, p_msl)

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
