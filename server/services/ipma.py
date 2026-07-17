"""
Servicio puro de IPMA (api.ipma.pt, open-data de Portugal).

Particularidades:

1. **API pública sin credenciales**: endpoints estáticos de open-data
   con tasa de actualización horaria.

2. **Feed global, no por estación**: ``observations.json`` trae las
   últimas 24 h de TODA la red anidadas por ``timestamp → idEstacao``.
   Cada fetch descarga el feed completo y filtra la estación; el TTL
   del caché de series amortigua el coste.

3. **Centinela ``-99.0``**: cualquier campo puede venir a -99.0
   ("nodata"); se convierte a NaN campo a campo.

4. **Viento por clases**: ``idDireccVento`` usa clases 0-9 (0 = sin
   rumbo, 1 o 9 = N, 2 = NE… 8 = NW) que se convierten a grados.
   ``intensidadeVentoKM`` ya viene en km/h. Sin racha.

5. **Radiación en kJ/m²** acumulados de la hora → W/m² medios
   (× 1000 / 3600). ``pressao`` ya es MSL; la absoluta se deriva con
   la altitud del catálogo local (DEM, IPMA no la publica).

6. **Día local**: tz del catálogo (``Europe/Lisbon``,
   ``Atlantic/Madeira`` o ``Atlantic/Azores``), con UTC de fallback.
   Los timestamps del feed vienen en UTC sin sufijo de zona.

7. **Ventana máxima de 24 h**: la "serie reciente" de tendencias es el
   propio feed (ya horario); ``days_back`` solo puede recortar.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

from data_files import IPMA_STATIONS_PATH
from server.schemas.errors import ProviderError
from domain.parsing.common import find_station_by_field, load_stations_json, parse_epoch

logger = logging.getLogger(__name__)

PROVIDER = "IPMA"
BASE_URL = "https://api.ipma.pt/open-data/observation/meteorology/stations"
OBSERVATIONS_URL = f"{BASE_URL}/observations.json"
USER_AGENT = "MeteoLabX/1.0 (contact: meteolabx@gmail.com)"

NODATA = -99.0

# Clase de rumbo IPMA → grados (0 = sin rumbo → NaN; 1 y 9 = N).
_WIND_CLASS_DEG = {1: 0.0, 2: 45.0, 3: 90.0, 4: 135.0, 5: 180.0, 6: 225.0, 7: 270.0, 8: 315.0, 9: 0.0}


def _is_nan(value: float) -> bool:
    return value != value


def _safe_float(value: Any, default: float = float("nan")) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _field(value: Any) -> float:
    """Valor IPMA → float con el centinela -99.0 convertido a NaN."""
    number = _safe_float(value)
    if _is_nan(number) or number == NODATA:
        return float("nan")
    return number


def _wind_dir_deg(value: Any) -> float:
    try:
        return _WIND_CLASS_DEG.get(int(value), float("nan"))
    except (TypeError, ValueError):
        return float("nan")


# =====================================================================
# Catálogo local
# =====================================================================

@lru_cache(maxsize=1)
def _load_stations() -> List[Dict[str, Any]]:
    try:
        return load_stations_json(str(IPMA_STATIONS_PATH), dict_key="stations")
    except Exception as exc:
        logger.warning("Catálogo IPMA no disponible (%s)", exc)
        return []


def _station_meta(station_id: str) -> Tuple[float, float, float, str, str]:
    """→ (lat, lon, elevation, nombre, tz)."""
    station = find_station_by_field(_load_stations(), field="id", target=station_id)
    return (
        _safe_float(station.get("lat")),
        _safe_float(station.get("lon")),
        _safe_float(station.get("elev")),
        str(station.get("name", "") or "").strip(),
        str(station.get("tz", "") or "").strip(),
    )


def _station_tz(tz_name: str) -> timezone | ZoneInfo:
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            pass
    return timezone.utc


# =====================================================================
# HTTP + parsing del feed
# =====================================================================

async def _get_json(client: httpx.AsyncClient, url: str, *, timeout_s: float) -> Any:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        response = await client.get(url, headers=headers, timeout=timeout_s)
    except httpx.TimeoutException as exc:
        raise ProviderError(
            "provider_timeout",
            provider=PROVIDER,
            detail=f"IPMA timeout: {exc}",
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
    if status == 429:
        raise ProviderError(
            "provider_ratelimit",
            provider=PROVIDER,
            detail="IPMA rate limit (HTTP 429)",
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


def _parse_reading(epoch: int, reading: Dict[str, Any], elevation_m: float) -> Dict[str, float]:
    p_msl = _field(reading.get("pressao"))
    p_abs = float("nan")
    if not _is_nan(p_msl) and not _is_nan(elevation_m):
        p_abs = float(p_msl) / math.exp(float(elevation_m) / 8000.0)

    wind_kmh = _field(reading.get("intensidadeVentoKM"))
    if _is_nan(wind_kmh):
        wind_ms = _field(reading.get("intensidadeVento"))
        wind_kmh = wind_ms * 3.6 if not _is_nan(wind_ms) else float("nan")

    radiation_kj = _field(reading.get("radiacao"))
    precip_mm = _field(reading.get("precAcumulada"))

    return {
        "epoch": int(epoch),
        "temp_c": _field(reading.get("temperatura")),
        "rh": _field(reading.get("humidade")),
        "p_msl_hpa": p_msl,
        "p_abs_hpa": p_abs,
        "wind_kmh": wind_kmh,
        "wind_dir_deg": _wind_dir_deg(reading.get("idDireccVento")),
        "precip_mm": max(0.0, precip_mm) if not _is_nan(precip_mm) else float("nan"),
        "solar_wm2": radiation_kj * 1000.0 / 3600.0 if not _is_nan(radiation_kj) else float("nan"),
    }


def _row_has_data(row: Dict[str, float]) -> bool:
    # El viento no cuenta: las estaciones fuera de servicio siguen
    # emitiendo intensidadeVento(KM) = 0.0 con todo lo demás a -99.0.
    return any(
        not _is_nan(_safe_float(row.get(key)))
        for key in ("temp_c", "rh", "p_msl_hpa", "precip_mm", "solar_wm2")
    )


async def _fetch_station_rows(
    station_id: str,
    client: httpx.AsyncClient,
    *,
    elevation_m: float,
    timeout_s: float,
) -> List[Dict[str, float]]:
    """Feed de 24 h → filas de la estación ordenadas por epoch asc."""
    payload = await _get_json(client, OBSERVATIONS_URL, timeout_s=timeout_s)
    if not isinstance(payload, dict):
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail="IPMA devolvió un feed de observaciones no reconocido",
            status_code=502,
        )
    rows: Dict[int, Dict[str, float]] = {}
    for timestamp, readings in payload.items():
        if not isinstance(readings, dict):
            continue
        reading = readings.get(station_id)
        if not isinstance(reading, dict):
            continue
        epoch = parse_epoch(timestamp)
        if epoch is None:
            continue
        row = _parse_reading(epoch, reading, elevation_m)
        if _row_has_data(row):
            rows[row["epoch"]] = row
    return [rows[ep] for ep in sorted(rows)]


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
    Observación actual = fila más reciente del feed de 24 h, con
    fallback campo a campo hacia atrás. Devuelve el dict canónico.
    """
    station_id = str(station_id).strip()
    lat0, lon0, elevation, name, tz_name = _station_meta(station_id)
    tz = _station_tz(tz_name)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        rows = await _fetch_station_rows(
            station_id, client, elevation_m=elevation, timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()

    if not rows:
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail=f"IPMA sin observaciones para {station_id}",
            status_code=502,
        )

    current = rows[-1]

    def _value(key: str) -> float:
        for row in reversed(rows):
            row_value = _safe_float(row.get(key))
            if not _is_nan(row_value):
                return row_value
        return float("nan")

    # Precipitación del día local: suma de los acumulados horarios.
    now_local = (now or datetime.now(tz=tz)).astimezone(tz)
    day_start_epoch = int(
        now_local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    )
    precip_vals = [
        _safe_float(row.get("precip_mm"))
        for row in rows
        if int(row["epoch"]) >= day_start_epoch
        and not _is_nan(_safe_float(row.get("precip_mm")))
    ]
    precip_total = float(sum(precip_vals)) if precip_vals else float("nan")

    epoch = int(current["epoch"])
    dt_utc = datetime.fromtimestamp(epoch, tz=timezone.utc)

    observation: Dict[str, Any] = {
        "Tc": _value("temp_c"),
        "RH": _value("rh"),
        "p_hpa": _value("p_msl_hpa"),
        "p_abs_hpa": _value("p_abs_hpa"),
        "wind": _value("wind_kmh"),
        "gust": float("nan"),  # IPMA no expone racha
        "wind_dir_deg": _value("wind_dir_deg"),
        "Td": float("nan"),
        "feels_like": float("nan"),
        "heat_index": float("nan"),
        "wind_chill": float("nan"),
        "precip_rate": float("nan"),
        "precip_total": precip_total,
        "solar_radiation": _value("solar_wm2"),
        "uv": float("nan"),
        "epoch": epoch,
        "time_local": dt_utc.astimezone(tz).isoformat(),
        "time_utc": dt_utc.isoformat(),
        "lat": lat0,
        "lon": lon0,
        "elevation": elevation if not _is_nan(elevation) else 0.0,
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


async def fetch_today_series(
    station_id: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 16.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Serie del día local (tz de la estación) en shape canónico."""
    station_id = str(station_id).strip()
    lat0, lon0, elevation, _name, tz_name = _station_meta(station_id)
    tz = _station_tz(tz_name)
    now_local = (now or datetime.now(tz=tz)).astimezone(tz)
    day_start_epoch = int(
        now_local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    )

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        rows = await _fetch_station_rows(
            station_id, client, elevation_m=elevation, timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()

    rows = [row for row in rows if int(row["epoch"]) >= day_start_epoch]
    if not rows:
        return _empty_today_series()

    def _col(key: str) -> List[float]:
        return [_safe_float(row.get(key)) for row in rows]

    return {
        "epochs": [int(row["epoch"]) for row in rows],
        "temps": _col("temp_c"),
        "humidities": _col("rh"),
        "dewpts": [float("nan")] * len(rows),
        "pressures": _col("p_msl_hpa"),
        "uv_indexes": [float("nan")] * len(rows),
        "solar_radiations": _col("solar_wm2"),
        "winds": _col("wind_kmh"),
        "gusts": [float("nan")] * len(rows),
        "wind_dirs": _col("wind_dir_deg"),
        "lat": lat0,
        "lon": lon0,
        "has_data": True,
    }


async def fetch_recent_series(
    station_id: str,
    *,
    days_back: int = 7,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 16.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Serie reciente (T/HR/presión MSL) para tendencias. El feed público
    solo cubre 24 h, ya a resolución horaria; ``days_back`` no puede
    ampliarla.
    """
    station_id = str(station_id).strip()
    lat0, lon0, elevation, _name, _tz_name = _station_meta(station_id)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        rows = await _fetch_station_rows(
            station_id, client, elevation_m=elevation, timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()

    if not rows:
        return {
            "epochs": [], "temps": [], "humidities": [], "pressures": [],
            "lat": lat0, "lon": lon0, "has_data": False,
        }
    return {
        "epochs": [int(row["epoch"]) for row in rows],
        "temps": [_safe_float(row.get("temp_c")) for row in rows],
        "humidities": [_safe_float(row.get("rh")) for row in rows],
        "pressures": [_safe_float(row.get("p_msl_hpa")) for row in rows],
        "lat": lat0,
        "lon": lon0,
        "has_data": True,
    }
