"""Servicio puro de Climantartide (ENEA) — AWS italianas de la Antártida.

El Osservatorio Meteo-Climatologico Antartico publica en tiempo real la
temperatura HORARIA (últimos ~5 días) de sus AWS mediante un único endpoint
JSONP sin autenticación (``realtime/graph/jsonp.php?caso=real_allt``). Es la
única fuente viva para el frío extremo de la meseta: el decodificador BUFR de
IEM anula las temperaturas por debajo de ~−73,15°C (suelo de 200 K), con lo
que Concordia llegaba en null en pleno invierno.

Particularidades:

1. **Bulk único**: una llamada trae TODAS las estaciones; el servicio filtra
   la serie de la pedida.
2. **Solo temperatura**: humedad, viento, presión… van a NaN (el punto de
   rocío lo deriva ``add_basic_derived`` cuando hay RH; aquí queda NaN).
3. **Fallback IEM para máx/mín**: para las estaciones con contraparte en IEM
   (solo Concordia) se piden los extremos del día a ``currents.json``; si
   responden se COMBINAN con los derivados de la serie horaria (``min`` de
   mínimas, ``max`` de máximas: un extremo intrahorario de IEM solo puede
   afinar, nunca recortar — IEM clampa el frío a ~−73). Si IEM no responde,
   se quedan los derivados de Climantartide.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

from domain.observation_pipeline import add_basic_derived
from server.schemas.errors import ProviderError
from server.services import stations

logger = logging.getLogger(__name__)

PROVIDER = "CLIMANTARTIDE"
REALTIME_URL = "https://www.climantartide.it/realtime/graph/jsonp.php"
USER_AGENT = "MeteoLabX/1.0 (contact: meteolabx@gmail.com)"

# Estación Climantartide → id IEM (network|station) con extremos diarios
# propios en ``currents.json``. Solo Concordia existe en ambos catálogos.
IEM_COUNTERPARTS = {"Concordia": "WMO_BUFR_SRF|0-380-0-625"}

_NAN = float("nan")


def _is_nan(value: float) -> bool:
    return value != value


def _valid(value: Any) -> bool:
    return isinstance(value, (int, float)) and not _is_nan(float(value))


def _station_meta(station_id: str) -> Dict[str, Any]:
    record = stations.get_station(PROVIDER, station_id)
    if not record:
        raise ProviderError(
            "station_not_found",
            provider=PROVIDER,
            detail=f"Estación Climantartide no encontrada: {station_id}",
            status_code=404,
        )
    return record


def _station_tz(meta: Dict[str, Any]) -> ZoneInfo:
    try:
        return ZoneInfo(str(meta.get("tz") or "UTC"))
    except Exception:
        return ZoneInfo("UTC")


async def _fetch_bulk(
    client: httpx.AsyncClient, *, timeout_s: float
) -> Dict[str, List[Tuple[int, float]]]:
    """Feed completo → {nombre de estación: [(epoch_s, temp_c), …] ordenado}."""
    try:
        response = await client.get(
            REALTIME_URL,
            params={"caso": "real_allt"},
            headers={"Accept": "*/*", "User-Agent": USER_AGENT},
            timeout=timeout_s,
        )
    except httpx.TimeoutException as exc:
        raise ProviderError(
            "provider_timeout",
            provider=PROVIDER,
            detail=f"Climantartide timeout: {exc}",
            status_code=504,
        ) from exc
    except httpx.RequestError as exc:
        raise ProviderError(
            "provider_network_error",
            provider=PROVIDER,
            detail=str(exc) or "Network error",
            status_code=502,
        ) from exc

    if response.status_code >= 400:
        raise ProviderError(
            "provider_http_error",
            provider=PROVIDER,
            detail=f"HTTP {response.status_code}",
            status_code=502,
        )

    raw = response.text.strip()
    start, end = raw.find("("), raw.rfind(")")
    if start < 0 or end <= start:
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail="Respuesta no JSONP",
            status_code=502,
        )
    try:
        payload = json.loads(raw[start + 1:end])
    except ValueError as exc:
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail=f"JSON inválido: {exc!r}",
            status_code=502,
        ) from exc

    series = payload.get("data") if isinstance(payload, dict) else None
    out: Dict[str, List[Tuple[int, float]]] = {}
    for entry in series if isinstance(series, list) else []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        points: List[Tuple[int, float]] = []
        for point in entry.get("data") or []:
            try:
                ms, value = point
            except (TypeError, ValueError):
                continue
            if value is None:
                continue
            try:
                points.append((int(ms) // 1000, float(value)))
            except (TypeError, ValueError):
                continue
        points.sort()
        out[name] = points
    return out


def _station_points(
    bulk: Dict[str, List[Tuple[int, float]]], station_id: str
) -> List[Tuple[int, float]]:
    wanted = str(station_id or "").strip().casefold()
    for name, points in bulk.items():
        if name.casefold() == wanted:
            return points
    return []


async def _iem_daily_extremes(
    station_id: str, client: httpx.AsyncClient, *, timeout_s: float
) -> Dict[str, float]:
    """Extremos diarios de la contraparte IEM (si existe y responde). Best
    effort: cualquier fallo devuelve {} y la observación sigue con los
    derivados de Climantartide."""
    counterpart = IEM_COUNTERPARTS.get(str(station_id or "").strip())
    if not counterpart:
        return {}
    from server.services import iem

    network, station = counterpart.split("|", 1)
    row = await iem._fetch_current_summary(
        network, station, client, timeout_s=timeout_s
    )
    if not row:
        return {}
    extremes, _ = iem._daily_summary_from_current(row, None)
    extremes.pop("gust_max", None)  # aquí solo interesan las temperaturas
    return extremes


def _merge_extremes(derived: Dict[str, float], iem_extremes: Dict[str, float]) -> Dict[str, float]:
    """Combina extremos propios con los de IEM: mínima más baja y máxima más
    alta ganan (IEM afina extremos intrahorarios pero clampa el frío)."""
    merged = dict(derived)
    if _valid(iem_extremes.get("temp_min")):
        value = float(iem_extremes["temp_min"])
        merged["temp_min"] = min(value, merged["temp_min"]) if "temp_min" in merged else value
    if _valid(iem_extremes.get("temp_max")):
        value = float(iem_extremes["temp_max"])
        merged["temp_max"] = max(value, merged["temp_max"]) if "temp_max" in merged else value
    return merged


def _today_points(
    points: List[Tuple[int, float]],
    tz: ZoneInfo,
    now: Optional[datetime] = None,
) -> List[Tuple[int, float]]:
    today = (now or datetime.now(tz=timezone.utc)).astimezone(tz).date()
    return [
        (epoch, value) for epoch, value in points
        if datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone(tz).date() == today
    ]


async def fetch_current(
    station_id: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 30.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    meta = _station_meta(station_id)
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        bulk = await _fetch_bulk(client, timeout_s=timeout_s)
        points = _station_points(bulk, station_id)
        if not points:
            raise ProviderError(
                "provider_no_current_data",
                provider=PROVIDER,
                detail=f"Climantartide sin observaciones para {station_id}",
                status_code=502,
            )
        try:
            iem_extremes = await _iem_daily_extremes(
                station_id, client, timeout_s=min(timeout_s, 12.0)
            )
        except Exception as exc:  # noqa: BLE001 — fallback best-effort
            logger.info(
                "Climantartide: fallback IEM de extremos falló para %s: %s",
                station_id, exc,
            )
            iem_extremes = {}
    finally:
        if owns_client:
            await client.aclose()

    epoch, temp = points[-1]
    tz = _station_tz(meta)
    dt_utc = datetime.fromtimestamp(epoch, tz=timezone.utc)

    today = _today_points(points, tz, now)
    daily_extremes: Dict[str, float] = {}
    if today:
        temps = [value for _, value in today]
        daily_extremes = {"temp_max": max(temps), "temp_min": min(temps)}
    daily_extremes = _merge_extremes(daily_extremes, iem_extremes)

    observation: Dict[str, Any] = {
        "Tc": float(temp),
        "RH": _NAN,
        "p_hpa": _NAN,
        "p_abs_hpa": _NAN,
        "Td": _NAN,
        "wind": _NAN,
        "gust": _NAN,
        "wind_dir_deg": _NAN,
        "precip_rate": _NAN,
        "precip_total": _NAN,
        "solar_radiation": _NAN,
        "uv": _NAN,
        "epoch": int(epoch),
        "time_utc": dt_utc.isoformat(),
        "time_local": dt_utc.astimezone(tz).isoformat(),
        "lat": meta.get("lat"),
        "lon": meta.get("lon"),
        "elevation": meta.get("elevation"),
        "station_name": meta.get("name") or station_id,
    }
    if daily_extremes:
        observation["daily_extremes"] = daily_extremes
    return add_basic_derived(observation)


async def fetch_today_series(
    station_id: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 30.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    meta = _station_meta(station_id)
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        bulk = await _fetch_bulk(client, timeout_s=timeout_s)
    finally:
        if owns_client:
            await client.aclose()

    tz = _station_tz(meta)
    points = _station_points(bulk, station_id)
    today = _today_points(points, tz, now)
    if not today and points:
        # Recién pasada la medianoche local el feed aún no trae puntos del
        # día: se sirve el último día local con datos (igual que IEM).
        last_day = max(
            datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone(tz).date()
            for epoch, _ in points
        )
        today = [
            (epoch, value) for epoch, value in points
            if datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone(tz).date() == last_day
        ]
    epochs = [epoch for epoch, _ in today]
    temps = [value for _, value in today]
    nans = [_NAN] * len(epochs)
    return {
        "epochs": epochs,
        "temps": temps,
        "humidities": list(nans),
        "dewpts": list(nans),
        "pressures": list(nans),
        "winds": list(nans),
        "gusts": list(nans),
        "wind_dirs": list(nans),
        "precips": list(nans),
        "uv_indexes": list(nans),
        "solar_radiations": list(nans),
        "lat": meta.get("lat"),
        "lon": meta.get("lon"),
        "has_data": bool(epochs),
    }


async def fetch_recent_series(
    station_id: str,
    *,
    days_back: int = 7,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 30.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Serie horaria reciente. El feed solo cubre ~5 días; ``days_back``
    mayores devuelven la ventana completa disponible."""
    meta = _station_meta(station_id)
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        bulk = await _fetch_bulk(client, timeout_s=timeout_s)
    finally:
        if owns_client:
            await client.aclose()

    reference = (now or datetime.now(tz=timezone.utc)).timestamp()
    floor = reference - max(1, int(days_back)) * 86400
    points = [
        (epoch, value) for epoch, value in _station_points(bulk, station_id)
        if epoch >= floor
    ]
    epochs = [epoch for epoch, _ in points]
    return {
        "epochs": epochs,
        "temps": [value for _, value in points],
        "humidities": [_NAN] * len(epochs),
        "pressures": [_NAN] * len(epochs),
        "lat": meta.get("lat"),
        "lon": meta.get("lon"),
        "has_data": bool(epochs),
    }
