"""
Servicio puro de WeatherLink v2 (Davis Instruments).

Reutiliza la normalización pura de ``domain/parsing/weatherlink.py``;
aquí solo vive el I/O httpx.

Particularidades:

1. **Credenciales per-user** (como WU): cada usuario aporta su
   ``api_key`` (query ``api-key``) y ``api_secret`` (header
   ``X-Api-Secret``). Viajan en el body de la request a esta API y
   nunca se loguean.

2. **Dos pasos**: ``/stations`` da metadatos (nombre, altitud,
   timezone) y ``/current/{id}`` la observación; se piden en paralelo.
   La serie del día usa ``/historic/{id}`` con la ventana del día
   local de la estación (timezone del payload de /stations).

3. **Unidades imperiales** (°F, mph, inHg, in) → métricas, resuelto
   por ``normalize_weatherlink_current`` / ``..._historic_series``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from server.schemas.errors import ProviderError
from domain.parsing.weatherlink import (
    _current_records,
    _is_nan,
    _safe_float,
    _station_tzinfo,
    _today_window,
    find_weatherlink_station,
    normalize_weatherlink_current,
    normalize_weatherlink_historic_series,
    normalize_weatherlink_stations,
)

logger = logging.getLogger(__name__)

PROVIDER = "WEATHERLINK"
BASE_URL = "https://api.weatherlink.com/v2"


def _require_credentials(api_key: str, api_secret: str) -> None:
    if not api_key or not api_secret:
        raise ProviderError(
            "missing_api_key",
            provider=PROVIDER,
            detail="WeatherLink requires per-user api_key and api_secret",
            status_code=400,
        )


async def _get_json(
    client: httpx.AsyncClient,
    path: str,
    api_key: str,
    api_secret: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    timeout_s: float,
) -> Any:
    query = {"api-key": api_key}
    if params:
        query.update(params)
    headers = {
        "Accept": "application/json",
        "User-Agent": "MeteoLabX/1.0 (+https://meteolabx.com)",
        "X-Api-Secret": api_secret,
    }
    try:
        response = await client.get(
            f"{BASE_URL}/{path.lstrip('/')}", params=query, headers=headers, timeout=timeout_s,
        )
    except httpx.TimeoutException as exc:
        raise ProviderError(
            "provider_timeout",
            provider=PROVIDER,
            detail=f"WeatherLink timeout: {exc}",
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
            detail=f"WeatherLink auth rechazada (HTTP {status})",
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
            detail="WeatherLink rate limit (HTTP 429)",
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


async def _fetch_station_meta(
    station_id: str,
    api_key: str,
    api_secret: str,
    client: httpx.AsyncClient,
    *,
    timeout_s: float,
) -> Dict[str, Any]:
    payload = await _get_json(client, "stations", api_key, api_secret, timeout_s=timeout_s)
    stations = normalize_weatherlink_stations(payload)
    return find_weatherlink_station(stations, station_id)


async def fetch_stations(
    api_key: str,
    api_secret: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 16.0,
) -> list[Dict[str, Any]]:
    """Lista normalizada de estaciones accesibles con estas credenciales."""
    _require_credentials(api_key, api_secret)
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        payload = await _get_json(
            client, "stations", api_key, api_secret, timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()
    return normalize_weatherlink_stations(payload)


# =====================================================================
# API pública del servicio
# =====================================================================

async def fetch_current(
    station_id: str,
    api_key: str,
    api_secret: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 16.0,
) -> Dict[str, Any]:
    """
    Observación actual: ``/current/{id}`` + metadatos de ``/stations``
    en paralelo. Devuelve el dict canónico; Td/heat_index/wind_chill
    nativos del feed se preservan.
    """
    _require_credentials(api_key, api_secret)
    station_id = str(station_id).strip()

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        meta_task = _fetch_station_meta(
            station_id, api_key, api_secret, client, timeout_s=timeout_s,
        )
        current_task = _get_json(
            client, f"current/{station_id}", api_key, api_secret, timeout_s=timeout_s,
        )
        meta_result, current_result = await asyncio.gather(
            meta_task, current_task, return_exceptions=True,
        )
    finally:
        if owns_client:
            await client.aclose()

    if isinstance(current_result, BaseException):
        raise current_result
    station_meta = {} if isinstance(meta_result, BaseException) else meta_result

    # Sin registros de sensor el normalizador devolvería un dict de
    # NaNs con epoch sintético; lo tratamos como respuesta inválida.
    if not _current_records(current_result if isinstance(current_result, dict) else {}):
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail=f"WeatherLink sin registros de sensor para {station_id}",
            status_code=502,
        )

    normalized = normalize_weatherlink_current(current_result, station=station_meta)
    if not normalized or normalized.get("epoch", 0) <= 0:
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail=f"WeatherLink sin observación para {station_id}",
            status_code=502,
        )

    epoch = int(normalized["epoch"])
    dt_utc = datetime.fromtimestamp(epoch, tz=timezone.utc)
    tzinfo = _station_tzinfo(station_meta or {})

    observation: Dict[str, Any] = {
        "Tc": normalized.get("Tc", float("nan")),
        "RH": normalized.get("RH", float("nan")),
        "p_hpa": normalized.get("p_hpa", float("nan")),
        "p_abs_hpa": normalized.get("p_abs_hpa", float("nan")),
        "wind": normalized.get("wind", float("nan")),
        "gust": normalized.get("gust", float("nan")),
        "wind_dir_deg": normalized.get("wind_dir_deg", float("nan")),
        "Td": normalized.get("Td", float("nan")),
        "feels_like": float("nan"),
        "heat_index": normalized.get("heat_index", float("nan")),
        "wind_chill": normalized.get("wind_chill", float("nan")),
        "precip_rate": normalized.get("precip_rate", float("nan")),
        "precip_total": normalized.get("precip_total", float("nan")),
        "solar_radiation": normalized.get("solar_radiation", float("nan")),
        "uv": normalized.get("uv", float("nan")),
        "epoch": epoch,
        "time_local": dt_utc.astimezone(tzinfo).isoformat(),
        "time_utc": dt_utc.isoformat(),
        "lat": normalized.get("lat", float("nan")),
        "lon": normalized.get("lon", float("nan")),
        "elevation": normalized.get("elevation", float("nan")),
        "station_name": str(normalized.get("station_name") or station_id),
    }

    # Derivadas: preservar nativos del feed (Davis los calcula).
    native = {
        key: observation[key]
        for key in ("Td", "heat_index", "wind_chill")
        if not _is_nan(_safe_float(observation[key]))
    }
    from domain.observation_pipeline import add_basic_derived
    derived = add_basic_derived(observation)
    derived.update(native)
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
    }


async def fetch_today_series(
    station_id: str,
    api_key: str,
    api_secret: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 16.0,
    now_epoch: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Serie del día local de la estación (timezone de /stations) desde
    ``/historic/{id}``, en shape canónico (incluye ``dewpts`` nativos).
    """
    _require_credentials(api_key, api_secret)
    station_id = str(station_id).strip()

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        station_meta = await _fetch_station_meta(
            station_id, api_key, api_secret, client, timeout_s=timeout_s,
        )
        start_ts, end_ts = _today_window(station_meta or {}, now_epoch=now_epoch)
        if end_ts <= start_ts:
            return _empty_today_series()
        payload = await _get_json(
            client, f"historic/{station_id}", api_key, api_secret,
            params={"start-timestamp": int(start_ts), "end-timestamp": int(end_ts)},
            timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()

    altitude = _safe_float((station_meta or {}).get("elevation"))
    series = normalize_weatherlink_historic_series(payload, altitude_m=altitude)
    epochs = series.get("epochs", [])
    if not epochs:
        return _empty_today_series()

    n = len(epochs)

    def _col(key: str) -> List[float]:
        values = series.get(key, [])
        return [
            _safe_float(values[i]) if i < len(values) else float("nan")
            for i in range(n)
        ]

    return {
        "epochs": [int(ep) for ep in epochs],
        "temps": _col("temps"),
        "humidities": _col("humidities"),
        "dewpts": _col("dewpts"),
        "pressures": _col("pressures_msl"),
        "uv_indexes": _col("uv_indexes"),
        "solar_radiations": _col("solar_radiations"),
        "winds": _col("winds"),
        "gusts": _col("gusts"),
        "wind_dirs": _col("wind_dirs"),
        "precips": _col("precips"),
        "lat": _safe_float(
            (station_meta or {}).get("latitude", (station_meta or {}).get("lat")),
        ),
        "lon": _safe_float(
            (station_meta or {}).get("longitude", (station_meta or {}).get("lon")),
        ),
        "daily_extremes": dict(series.get("daily_extremes", {})),
        "has_data": True,
    }


async def fetch_recent_series(
    station_id: str,
    api_key: str,
    api_secret: str,
    *,
    days_back: int = 7,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 16.0,
    now_epoch: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Serie reciente (T/HR/presión MSL) para tendencias. El histórico de
    WeatherLink limita ~24 h por petición → fan-out de chunks diarios
    en paralelo limitado, binned a 1 h.
    """
    _require_credentials(api_key, api_secret)
    station_id = str(station_id).strip()

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        station_meta = await _fetch_station_meta(
            station_id, api_key, api_secret, client, timeout_s=timeout_s,
        )
        end_ts = int(now_epoch or datetime.now(tz=timezone.utc).timestamp())
        days = max(1, int(days_back))
        altitude = _safe_float((station_meta or {}).get("elevation"))

        semaphore = asyncio.Semaphore(4)

        async def _chunk(idx: int) -> Dict[str, Any]:
            chunk_end = end_ts - idx * 86400
            chunk_start = chunk_end - 86400
            async with semaphore:
                try:
                    payload = await _get_json(
                        client, f"historic/{station_id}", api_key, api_secret,
                        params={
                            "start-timestamp": chunk_start,
                            "end-timestamp": chunk_end,
                        },
                        timeout_s=timeout_s,
                    )
                except ProviderError as exc:
                    if exc.error_code in ("provider_unauthorized", "provider_ratelimit"):
                        raise
                    return {}
            return normalize_weatherlink_historic_series(payload, altitude_m=altitude)

        chunks = await asyncio.gather(*(_chunk(idx) for idx in range(days)))
    finally:
        if owns_client:
            await client.aclose()

    # Merge + bin horario (última lectura de cada hora).
    by_hour: Dict[int, Tuple[int, float, float, float]] = {}
    for chunk in chunks:
        if not isinstance(chunk, dict) or not chunk.get("has_data"):
            continue
        epochs = chunk.get("epochs", [])
        temps = chunk.get("temps", [])
        hums = chunk.get("humidities", [])
        press = chunk.get("pressures_msl", [])
        for idx, epoch in enumerate(epochs):
            bucket = (int(epoch) // 3600) * 3600
            current = by_hour.get(bucket)
            if current is None or int(epoch) >= current[0]:
                by_hour[bucket] = (
                    int(epoch),
                    _safe_float(temps[idx]) if idx < len(temps) else float("nan"),
                    _safe_float(hums[idx]) if idx < len(hums) else float("nan"),
                    _safe_float(press[idx]) if idx < len(press) else float("nan"),
                )

    buckets = sorted(by_hour)
    lat = _safe_float(
        (station_meta or {}).get("latitude", (station_meta or {}).get("lat")),
    )
    lon = _safe_float(
        (station_meta or {}).get("longitude", (station_meta or {}).get("lon")),
    )
    if not buckets:
        return {
            "epochs": [], "temps": [], "humidities": [], "pressures": [],
            "lat": lat, "lon": lon, "has_data": False,
        }
    return {
        "epochs": buckets,
        "temps": [by_hour[b][1] for b in buckets],
        "humidities": [by_hour[b][2] for b in buckets],
        "pressures": [by_hour[b][3] for b in buckets],
        "lat": lat,
        "lon": lon,
        "has_data": True,
    }
