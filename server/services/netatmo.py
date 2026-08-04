"""Netatmo public weather station observations (OAuth + getpublicdata/getmeasure).

El "payload" interno que comparten current/today/recent es un dict con:

- ``station``: ficha del catálogo SQLite (nombre, lat/lon, elevación, tz).
- ``public``: fila cruda de ``getpublicdata`` (última lectura de cada módulo).
- ``rows``: serie fusionada de ``getmeasure`` (buckets de 30 min, hasta 7 días)
  con las claves canónicas del pipeline (Tc, RH, p_hpa…).

Así una sola cadena de peticiones (token + getpublicdata + getmeasure por
módulo) alimenta observación actual y tendencias, igual que Windy comparte su
payload único.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from server.schemas.errors import ProviderError


PROVIDER = "NETATMO"
logger = logging.getLogger(__name__)
TOKEN_URL = "https://api.netatmo.com/oauth2/token"
PUBLIC_DATA_URL = "https://api.netatmo.com/api/getpublicdata"
GET_MEASURE_URL = "https://api.netatmo.com/api/getmeasure"

# Radios progresivos alrededor de las coordenadas guardadas. Netatmo puede
# devolver 503 cuando getpublicdata abarca demasiadas estaciones, sobre todo en
# ciudades densas; empezamos en ~200 m de ancho y ampliamos solo si hace falta.
_BBOX_DELTAS_DEG = (0.001, 0.0025, 0.005)
_UPSTREAM_503_RETRIES = 1
_UPSTREAM_RETRY_DELAY_S = 0.25
_INACTIVE_TTL_S = 12 * 60 * 60
_MEASURE_SCALE = "30min"

# Token de acceso cacheado en memoria del proceso. Netatmo puede rotar el
# refresh token: si la respuesta trae uno nuevo se usa a partir de entonces
# (el de la configuración queda obsoleto hasta el próximo reinicio, donde
# normalmente sigue siendo válido porque Netatmo solo rota al expirar).
_TOKEN_STATE: Dict[str, Any] = {"key": None, "access_token": "", "expires_at": 0.0, "refresh_token": ""}
_TOKEN_LOCK = asyncio.Lock()
_INACTIVE_UNTIL: Dict[str, float] = {}


def _safe_float(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return result if math.isfinite(result) else float("nan")


def _pressure_to_abs(p_msl: float, elevation_m: float) -> float:
    if not math.isfinite(p_msl):
        return float("nan")
    factor = math.exp(-elevation_m / 8000.0) if math.isfinite(elevation_m) else 1.0
    return p_msl * factor


def _local_day_start_epoch(now: Optional[datetime], tz_name: str) -> int:
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        local_tz = ZoneInfo(str(tz_name or "UTC"))
    except ZoneInfoNotFoundError:
        local_tz = timezone.utc
    local_now = now_utc.astimezone(local_tz)
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(local_midnight.timestamp())


async def _refresh_access_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    *,
    client: httpx.AsyncClient,
    timeout_s: float,
    force: bool = False,
) -> str:
    key = (client_id, refresh_token)
    async with _TOKEN_LOCK:
        if (
            not force
            and _TOKEN_STATE["key"] == key
            and _TOKEN_STATE["access_token"]
            and _TOKEN_STATE["expires_at"] > time.time() + 60.0
        ):
            return str(_TOKEN_STATE["access_token"])
        effective_refresh = (
            str(_TOKEN_STATE["refresh_token"])
            if _TOKEN_STATE["key"] == key and _TOKEN_STATE["refresh_token"]
            else refresh_token
        )
        try:
            response = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": effective_refresh,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                timeout=timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError("provider_timeout", provider=PROVIDER, status_code=504) from exc
        except httpx.RequestError as exc:
            raise ProviderError(
                "provider_network_error", provider=PROVIDER, detail=str(exc), status_code=502,
            ) from exc
        if response.status_code >= 400:
            raise ProviderError(
                "provider_unauthorized", provider=PROVIDER,
                detail="Netatmo token refresh failed", status_code=401,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError("provider_bad_response", provider=PROVIDER, status_code=502) from exc
        access_token = str(payload.get("access_token") or "")
        if not access_token:
            raise ProviderError(
                "provider_unauthorized", provider=PROVIDER,
                detail="Netatmo token refresh returned no access token", status_code=401,
            )
        _TOKEN_STATE.update({
            "key": key,
            "access_token": access_token,
            "expires_at": time.time() + _safe_float(payload.get("expires_in") or 10800.0),
            "refresh_token": str(payload.get("refresh_token") or effective_refresh),
        })
        return access_token


def _raise_for_api_status(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    body = ""
    try:
        body = response.text[:300]
    except Exception:
        pass
    if response.status_code == 403 and '"code":26' in body.replace(" ", ""):
        raise ProviderError("provider_ratelimit", provider=PROVIDER, status_code=429)
    if response.status_code in (401, 403):
        raise ProviderError("provider_unauthorized", provider=PROVIDER, status_code=401)
    if response.status_code == 404:
        raise ProviderError("station_not_found", provider=PROVIDER, status_code=404)
    if response.status_code == 429:
        raise ProviderError("provider_ratelimit", provider=PROVIDER, status_code=429)
    raise ProviderError(
        "provider_http_error", provider=PROVIDER,
        detail=f"Netatmo HTTP {response.status_code}", status_code=502,
    )


async def _api_get(
    url: str,
    params: Dict[str, Any],
    access_token: str,
    *,
    client: httpx.AsyncClient,
    timeout_s: float,
) -> Any:
    try:
        response = await client.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=timeout_s,
        )
    except httpx.TimeoutException as exc:
        raise ProviderError("provider_timeout", provider=PROVIDER, status_code=504) from exc
    except httpx.RequestError as exc:
        raise ProviderError(
            "provider_network_error", provider=PROVIDER, detail=str(exc), status_code=502,
        ) from exc
    _raise_for_api_status(response)
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderError("provider_bad_response", provider=PROVIDER, status_code=502) from exc
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise ProviderError("provider_bad_response", provider=PROVIDER, status_code=502)
    return payload.get("body")


def _inactive_key(station_id: str) -> str:
    return str(station_id or "").strip().lower()


def is_temporarily_inactive(station_id: str) -> bool:
    """Si la estación dejó de aparecer en getpublicdata durante 12 horas."""
    key = _inactive_key(station_id)
    expires_at = _INACTIVE_UNTIL.get(key, 0.0)
    if expires_at > time.time():
        return True
    if expires_at:
        _INACTIVE_UNTIL.pop(key, None)
    return False


def temporarily_inactive_station_ids() -> frozenset[str]:
    """Snapshot de IDs inactivos para filtrar catálogos sin llamadas por fila."""
    now = time.time()
    expired = [key for key, expires_at in _INACTIVE_UNTIL.items() if expires_at <= now]
    for key in expired:
        _INACTIVE_UNTIL.pop(key, None)
    return frozenset(_INACTIVE_UNTIL)


def _mark_temporarily_inactive(station_id: str) -> None:
    key = _inactive_key(station_id)
    if key:
        _INACTIVE_UNTIL[key] = time.time() + _INACTIVE_TTL_S


def _mark_active(station_id: str) -> None:
    _INACTIVE_UNTIL.pop(_inactive_key(station_id), None)


def _is_503(exc: ProviderError) -> bool:
    return (
        exc.error_code == "provider_http_error"
        and str(exc.detail or "").strip() == "Netatmo HTTP 503"
    )


async def _api_get_with_503_retry(
    url: str,
    params: Dict[str, Any],
    access_token: str,
    *,
    client: httpx.AsyncClient,
    timeout_s: float,
) -> Any:
    for attempt in range(_UPSTREAM_503_RETRIES + 1):
        try:
            return await _api_get(
                url, params, access_token, client=client, timeout_s=timeout_s,
            )
        except ProviderError as exc:
            if not _is_503(exc) or attempt >= _UPSTREAM_503_RETRIES:
                raise
            await asyncio.sleep(_UPSTREAM_RETRY_DELAY_S * (attempt + 1))
    raise AssertionError("unreachable")


async def _fetch_public_station(
    station_id: str,
    lat: float,
    lon: float,
    access_token: str,
    *,
    client: httpx.AsyncClient,
    timeout_s: float,
) -> Dict[str, Any]:
    for delta in _BBOX_DELTAS_DEG:
        body = await _api_get_with_503_retry(
            PUBLIC_DATA_URL,
            {
                "lat_sw": lat - delta, "lon_sw": lon - delta,
                "lat_ne": lat + delta, "lon_ne": lon + delta,
                "filter": "false",
            },
            access_token, client=client, timeout_s=timeout_s,
        )
        public_row = _find_public_row(body, station_id)
        if public_row is not None:
            _mark_active(station_id)
            return public_row

    _mark_temporarily_inactive(station_id)
    raise ProviderError(
        "provider_no_data", provider=PROVIDER,
        detail="Netatmo station is not currently publishing", status_code=404,
    )


def _find_public_row(body: Any, station_id: str) -> Optional[Dict[str, Any]]:
    target = str(station_id or "").strip().lower()
    for row in body if isinstance(body, list) else []:
        if isinstance(row, dict) and str(row.get("_id") or "").strip().lower() == target:
            return row
    return None


def _module_plan(public_row: Dict[str, Any], station_id: str) -> list[Dict[str, str]]:
    """Qué módulos pedir a getmeasure y con qué tipos, según getpublicdata."""
    plan: list[Dict[str, str]] = []
    measures = public_row.get("measures") if isinstance(public_row.get("measures"), dict) else {}
    for module_id, measure in measures.items():
        if not isinstance(measure, dict):
            continue
        types = {str(item).strip().lower() for item in (measure.get("type") or [])}
        if "temperature" in types or "humidity" in types:
            plan.append({
                "module_id": str(module_id),
                "type": "temperature,humidity",
                "fields": "temp_rh",
            })
        if "pressure" in types:
            plan.append({
                "module_id": str(module_id),
                "type": "pressure",
                "fields": "pressure",
            })
        if "wind_strength" in measure or "gust_strength" in measure:
            plan.append({
                "module_id": str(module_id),
                "type": "windstrength,guststrength,windangle",
                "fields": "wind",
            })
        if any(key in measure for key in ("rain_live", "rain_60min", "rain_24h")):
            plan.append({
                "module_id": str(module_id),
                "type": "sum_rain",
                "fields": "rain",
            })
    return plan


def _measure_points(body: Any) -> list[tuple[int, list[Any]]]:
    """Aplana la respuesta optimize=true de getmeasure a (epoch, values)."""
    points: list[tuple[int, list[Any]]] = []
    for chunk in body if isinstance(body, list) else []:
        if not isinstance(chunk, dict):
            continue
        begin = chunk.get("beg_time")
        step = chunk.get("step_time") or 0
        values = chunk.get("value")
        if not isinstance(begin, (int, float)) or not isinstance(values, list):
            continue
        for index, row in enumerate(values):
            epoch = int(begin) + int(step) * index
            points.append((epoch, row if isinstance(row, list) else [row]))
    return points


def _merge_measure(
    rows: Dict[int, Dict[str, Any]],
    fields: str,
    points: list[tuple[int, list[Any]]],
) -> None:
    def _slot(epoch: int) -> Dict[str, Any]:
        return rows.setdefault(epoch, {"epoch": epoch})

    for epoch, values in points:
        slot = _slot(epoch)
        if fields == "temp_rh":
            slot["Tc"] = _safe_float(values[0] if len(values) > 0 else None)
            slot["RH"] = _safe_float(values[1] if len(values) > 1 else None)
        elif fields == "pressure":
            slot["p_hpa"] = _safe_float(values[0] if len(values) > 0 else None)
        elif fields == "wind":
            slot["wind"] = _safe_float(values[0] if len(values) > 0 else None)
            slot["gust"] = _safe_float(values[1] if len(values) > 1 else None)
            slot["wind_dir_deg"] = _safe_float(values[2] if len(values) > 2 else None)
        elif fields == "rain":
            slot["precip_bucket_mm"] = _safe_float(values[0] if len(values) > 0 else None)


def _finalize_rows(raw_rows: Dict[int, Dict[str, Any]], elevation_m: float) -> list[Dict[str, Any]]:
    rows = []
    for epoch in sorted(raw_rows):
        slot = raw_rows[epoch]
        p_hpa = _safe_float(slot.get("p_hpa"))
        rows.append({
            "epoch": epoch,
            "Tc": _safe_float(slot.get("Tc")),
            "RH": _safe_float(slot.get("RH")),
            # Td lo deriva siempre el pipeline común desde Tc + RH.
            "Td": float("nan"),
            # Netatmo publica presión relativa (nivel del mar).
            "p_hpa": p_hpa,
            "p_abs_hpa": _pressure_to_abs(p_hpa, elevation_m),
            "wind": _safe_float(slot.get("wind")),
            "gust": _safe_float(slot.get("gust")),
            "wind_dir_deg": _safe_float(slot.get("wind_dir_deg")),
            "uv": float("nan"),
            "precip_bucket_mm": _safe_float(slot.get("precip_bucket_mm")),
        })
    return rows


def _filter_rows(rows, *, start_epoch: Optional[int] = None):
    return [row for row in rows if start_epoch is None or row["epoch"] >= start_epoch]


def _precip_cumulative(rows: list[Dict[str, Any]]) -> list[float]:
    cumulative = 0.0
    values = []
    for row in rows:
        bucket = _safe_float(row.get("precip_bucket_mm"))
        if math.isfinite(bucket):
            cumulative += max(0.0, bucket)
        values.append(cumulative)
    return values


def _series(payload: Dict[str, Any], rows: list[Dict[str, Any]]) -> Dict[str, Any]:
    station = payload.get("station") or {}
    return {
        "epochs": [row["epoch"] for row in rows],
        "temps": [row["Tc"] for row in rows],
        "humidities": [row["RH"] for row in rows],
        "dewpts": [row["Td"] for row in rows],
        "pressures": [row["p_hpa"] for row in rows],
        "pressures_abs": [row["p_abs_hpa"] for row in rows],
        "uv_indexes": [row["uv"] for row in rows],
        "solar_radiations": [float("nan") for _ in rows],
        "precips": _precip_cumulative(rows),
        "winds": [row["wind"] for row in rows],
        "gusts": [row["gust"] for row in rows],
        "wind_dirs": [row["wind_dir_deg"] for row in rows],
        "lat": _safe_float(station.get("lat")),
        "lon": _safe_float(station.get("lon")),
        "has_data": bool(rows),
    }


def _public_current(public_row: Dict[str, Any]) -> Dict[str, Any]:
    """Última lectura de cada módulo según getpublicdata (lo más fresco)."""
    current: Dict[str, Any] = {}
    epochs: list[int] = []
    measures = public_row.get("measures") if isinstance(public_row.get("measures"), dict) else {}
    for measure in measures.values():
        if not isinstance(measure, dict):
            continue
        types = [str(item).strip().lower() for item in (measure.get("type") or [])]
        res = measure.get("res")
        if isinstance(res, dict) and res:
            candidates = []
            for raw_epoch, values in res.items():
                try:
                    candidates.append((int(raw_epoch), values if isinstance(values, list) else []))
                except (TypeError, ValueError):
                    continue
            if candidates:
                epoch, values = max(candidates, key=lambda item: item[0])
                epochs.append(epoch)
                for index, type_name in enumerate(types):
                    value = _safe_float(values[index] if index < len(values) else None)
                    if type_name == "temperature":
                        current["Tc"] = value
                    elif type_name == "humidity":
                        current["RH"] = value
                    elif type_name == "pressure":
                        current["p_hpa"] = value
        if "wind_strength" in measure or "gust_strength" in measure:
            current["wind"] = _safe_float(measure.get("wind_strength"))
            current["gust"] = _safe_float(measure.get("gust_strength"))
            current["wind_dir_deg"] = _safe_float(measure.get("wind_angle"))
            if isinstance(measure.get("wind_timeutc"), (int, float)):
                epochs.append(int(measure["wind_timeutc"]))
        if any(key in measure for key in ("rain_live", "rain_60min", "rain_24h")):
            current["precip_rate"] = _safe_float(measure.get("rain_live"))
            if isinstance(measure.get("rain_timeutc"), (int, float)):
                epochs.append(int(measure["rain_timeutc"]))
    current["epoch"] = max(epochs) if epochs else 0
    return current


async def fetch_observations(
    station_id: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    *,
    client=None,
    timeout_s: float = 20.0,
    days_back: int = 7,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    if not (client_id and client_secret and refresh_token):
        raise ProviderError(
            "missing_api_key", provider=PROVIDER,
            detail="Netatmo credentials are not configured", status_code=500,
        )
    station_key = str(station_id or "").strip()
    if not station_key:
        raise ProviderError("station_not_found", provider=PROVIDER, status_code=404)

    from server.services import stations

    station = stations.get_station(PROVIDER, station_key)
    if not station:
        raise ProviderError("station_not_found", provider=PROVIDER, status_code=404)
    if is_temporarily_inactive(station_key):
        raise ProviderError(
            "provider_no_data", provider=PROVIDER,
            detail="Netatmo station is not currently publishing", status_code=404,
        )
    lat = _safe_float(station.get("lat"))
    lon = _safe_float(station.get("lon"))
    elevation = _safe_float(station.get("elevation"))

    async def _fetch_all(access_token: str) -> tuple[Dict[str, Any], Dict[int, Dict[str, Any]]]:
        public_row = await _fetch_public_station(
            station_key, lat, lon, access_token,
            client=client, timeout_s=timeout_s,
        )

        now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        date_begin = int((now_utc - timedelta(days=max(1, min(7, int(days_back))))).timestamp())
        raw_rows: Dict[int, Dict[str, Any]] = {}
        for step in _module_plan(public_row, station_key):
            try:
                measure_body = await _api_get_with_503_retry(
                    GET_MEASURE_URL,
                    {
                        "device_id": station_key,
                        "module_id": step["module_id"],
                        "scale": _MEASURE_SCALE,
                        "type": step["type"],
                        "date_begin": date_begin,
                        "date_end": int(now_utc.timestamp()),
                        "optimize": "true",
                        "real_time": "false",
                    },
                    access_token, client=client, timeout_s=timeout_s,
                )
            except ProviderError as exc:
                if exc.error_code == "provider_unauthorized":
                    # La capa exterior renueva el token y reintenta toda la
                    # operación una vez. No ocultar un fallo de autenticación.
                    raise
                # getpublicdata ya proporcionó una observación actual válida.
                # El histórico es complementario y su indisponibilidad no debe
                # impedir conectar la estación.
                logger.warning(
                    "NETATMO getmeasure no disponible para station=%s module=%s (%s); "
                    "continuando solo con la lectura actual",
                    station_key, step["module_id"], exc.error_code,
                )
                continue
            _merge_measure(raw_rows, step["fields"], _measure_points(measure_body))
        return public_row, raw_rows

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        access_token = await _refresh_access_token(
            client_id, client_secret, refresh_token, client=client, timeout_s=timeout_s,
        )
        try:
            public_row, raw_rows = await _fetch_all(access_token)
        except ProviderError as exc:
            if exc.error_code != "provider_unauthorized":
                raise
            # Otro proceso (p. ej. la construcción del catálogo) puede haber
            # renovado el token e invalidado el nuestro: se fuerza una
            # renovación y se reintenta una vez.
            access_token = await _refresh_access_token(
                client_id, client_secret, refresh_token,
                client=client, timeout_s=timeout_s, force=True,
            )
            public_row, raw_rows = await _fetch_all(access_token)
    finally:
        if owns_client:
            await client.aclose()

    return {
        "station": station,
        "public": public_row,
        "rows": _finalize_rows(raw_rows, elevation),
    }


def current_from_payload(
    payload: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
    tz_name: str = "UTC",
) -> Dict[str, Any]:
    station = payload.get("station") or {}
    public_row = payload.get("public") or {}
    rows = payload.get("rows") or []
    current = _public_current(public_row)
    if current.get("epoch", 0) <= 0 and not rows:
        raise ProviderError(
            "provider_no_data", provider=PROVIDER,
            detail="Netatmo station has no recent observations", status_code=404,
        )
    elevation = _safe_float(station.get("elevation"))
    p_hpa = _safe_float(current.get("p_hpa"))
    day_start = _local_day_start_epoch(now, tz_name)
    today_precip = _precip_cumulative(_filter_rows(rows, start_epoch=day_start))
    epoch = int(current.get("epoch") or 0)
    if epoch <= 0 and rows:
        epoch = int(rows[-1]["epoch"])
    return {
        "epoch": epoch,
        "time_utc": datetime.fromtimestamp(epoch, timezone.utc).isoformat(),
        "time_local": datetime.fromtimestamp(epoch, timezone.utc).isoformat(),
        "Tc": _safe_float(current.get("Tc")),
        "RH": _safe_float(current.get("RH")),
        "Td": float("nan"),
        "p_hpa": p_hpa,
        "p_abs_hpa": _pressure_to_abs(p_hpa, elevation),
        "wind": _safe_float(current.get("wind")),
        "gust": _safe_float(current.get("gust")),
        "wind_dir_deg": _safe_float(current.get("wind_dir_deg")),
        "uv": float("nan"),
        "precip_rate": _safe_float(current.get("precip_rate")),
        "precip_total": today_precip[-1] if today_precip else float("nan"),
        "solar_radiation": float("nan"),
        "lat": _safe_float(station.get("lat")),
        "lon": _safe_float(station.get("lon")),
        "elevation": elevation,
        "station_name": str(station.get("name") or "").strip(),
    }


def today_series_from_payload(
    payload: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
    tz_name: str = "UTC",
) -> Dict[str, Any]:
    rows = payload.get("rows") or []
    day_start = _local_day_start_epoch(now, tz_name)
    return _series(payload, _filter_rows(rows, start_epoch=day_start))


def recent_series_from_payload(
    payload: Dict[str, Any],
    *,
    days_back: int = 7,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    rows = payload.get("rows") or []
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = int((now_utc - timedelta(days=max(1, min(7, int(days_back))))).timestamp())
    return _series(payload, _filter_rows(rows, start_epoch=cutoff))


async def fetch_recent_series(
    station_id: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    *,
    days_back: int = 7,
    client=None,
) -> Dict[str, Any]:
    payload = await fetch_observations(
        station_id, client_id, client_secret, refresh_token,
        client=client, days_back=days_back,
    )
    return recent_series_from_payload(payload, days_back=days_back)
