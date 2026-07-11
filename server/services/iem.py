"""Servicio puro de Iowa Environmental Mesonet (IEM).

IEM agrega redes globales y regionales. Para evitar colisiones de IDs, el
``station_id`` interno de MeteoLabX para IEM debe ser ``network|station``.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

from domain.observation_pipeline import add_basic_derived
from server.schemas.errors import ProviderError
from server.services import stations

logger = logging.getLogger(__name__)

PROVIDER = "IEM"
# obhistory es el endpoint por-estación/por-día de IEM y vale para TODAS las
# redes, ASOS/METAR incluidas. El servicio de descarga masiva
# ``cgi-bin/request/asos.py`` que se usaba antes para redes ASOS aplica
# rate-limit agresivo (429 frecuentes) porque está pensado para exports, no
# para tráfico interactivo; con él la observación actual y los gráficos
# fallaban de forma intermitente.
BASE_URL = "https://mesonet.agron.iastate.edu/api/1/obhistory.json"
CURRENTS_URL = "https://mesonet.agron.iastate.edu/api/1/currents.json"
USER_AGENT = "MeteoLabX/1.0 (contact: meteolabx@gmail.com)"
_IEM_TRACE_PRECIP_IN = 0.0001


def _is_nan(value: float) -> bool:
    return value != value


def _safe_float(value: Any, default: float = float("nan")) -> float:
    if value is None or value == "":
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _f_to_c(value: Any) -> float:
    raw = _safe_float(value)
    return (raw - 32.0) * 5.0 / 9.0 if not _is_nan(raw) else float("nan")


def _knots_to_kmh(value: Any) -> float:
    raw = _safe_float(value)
    return raw * 1.852 if not _is_nan(raw) else float("nan")


def _inch_to_mm(value: Any) -> float:
    raw = _safe_float(value)
    if _is_nan(raw):
        return float("nan")
    # IEM codifica los METAR ``P0000``/traza como 0.0001". Convertirlo
    # literalmente genera 0.00254 mm, que no es lluvia medible.
    if 0.0 <= raw <= _IEM_TRACE_PRECIP_IN:
        return 0.0
    return max(0.0, raw * 25.4)


def _inhg_to_hpa(value: Any) -> float:
    raw = _safe_float(value)
    if _is_nan(raw):
        return float("nan")
    hpa = raw * 33.8638866667 if raw < 100.0 else raw
    return hpa if 800.0 <= hpa <= 1100.0 else float("nan")


def _parse_epoch(row: Dict[str, Any]) -> Optional[int]:
    raw = row.get("utc_valid") or row.get("valid")
    if not raw:
        return None
    try:
        text = str(raw).replace("Z", "+00:00")
        if "+" not in text[-6:] and not text.endswith("+00:00"):
            text = f"{text}+00:00"
        return int(datetime.fromisoformat(text).timestamp())
    except (TypeError, ValueError, OSError):
        return None


def _station_parts(station_id: str) -> Tuple[str, str]:
    raw = str(station_id or "").strip()
    if "|" not in raw:
        raise ProviderError(
            "station_not_found",
            provider=PROVIDER,
            detail="IEM station_id debe tener formato network|station",
            status_code=404,
        )
    network, station = (part.strip() for part in raw.split("|", 1))
    if not network or not station:
        raise ProviderError(
            "station_not_found",
            provider=PROVIDER,
            detail=f"IEM station_id inválido: {station_id}",
            status_code=404,
        )
    return network, station


def _station_meta(station_id: str) -> Dict[str, Any]:
    record = stations.get_station(PROVIDER, station_id)
    if not record:
        raise ProviderError(
            "station_not_found",
            provider=PROVIDER,
            detail=f"Estación IEM no encontrada: {station_id}",
            status_code=404,
        )
    return record


def _station_tz(meta: Dict[str, Any]) -> ZoneInfo:
    try:
        return ZoneInfo(str(meta.get("tz") or "UTC"))
    except Exception:
        return ZoneInfo("UTC")


def _local_date(meta: Dict[str, Any], now: Optional[datetime]) -> date:
    tz = _station_tz(meta)
    return (now or datetime.now(tz=timezone.utc)).astimezone(tz).date()


async def _fetch_current_summary(
    network: str,
    station: str,
    client: httpx.AsyncClient,
    *,
    timeout_s: float,
) -> Dict[str, Any]:
    try:
        response = await client.get(
            CURRENTS_URL,
            params={"network": network},
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            timeout=timeout_s,
        )
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        logger.info("IEM currents falló para %s|%s: %s", network, station, exc)
        return {}

    if response.status_code >= 400:
        logger.info(
            "IEM currents falló para %s|%s: HTTP %s",
            network, station, response.status_code,
        )
        return {}

    try:
        payload = response.json()
    except ValueError as exc:
        logger.info("IEM currents devolvió JSON inválido para %s|%s: %s", network, station, exc)
        return {}

    rows = payload.get("data") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return {}
    station_key = str(station or "").strip().upper()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("station") or "").strip().upper() == station_key:
            return row
    return {}


def _daily_summary_from_current(
    row: Dict[str, Any], lat: Optional[float] = None
) -> Tuple[Dict[str, float], float]:
    daily_extremes: Dict[str, float] = {}
    for source, target, converter in (
        ("max_tmpf", "temp_max", _f_to_c),
        ("min_tmpf", "temp_min", _f_to_c),
        ("max_gust", "gust_max", _knots_to_kmh),
    ):
        value = converter(row.get(source))
        # Las temperaturas extremas del summary también vienen de sensores IEM
        # sin validar → mismo filtro climatológico que la actual (la ráfaga no).
        if target in ("temp_max", "temp_min"):
            value = _plausible_temp_c(value, lat)
        if _valid(value):
            daily_extremes[target] = float(value)

    precip_total = _inch_to_mm(row.get("pday"))
    if not _valid(precip_total):
        precip_total = _inch_to_mm(row.get("ob_pday"))
    return daily_extremes, precip_total


def _rows_have_temperature(rows: Iterable[Dict[str, Any]]) -> bool:
    for row in rows:
        if _valid(_f_to_c(row.get("tmpf"))):
            return True
    return False


def _summary_has_current_temperature(row: Dict[str, Any]) -> bool:
    return _valid(_f_to_c(row.get("tmpf")))


async def _fetch_date(
    network: str,
    station: str,
    day: date,
    client: httpx.AsyncClient,
    *,
    timeout_s: float,
) -> List[Dict[str, Any]]:
    try:
        response = await client.get(
            BASE_URL,
            params={"network": network, "station": station, "date": day.isoformat()},
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            timeout=timeout_s,
        )
    except httpx.TimeoutException as exc:
        raise ProviderError(
            "provider_timeout",
            provider=PROVIDER,
            detail=f"IEM timeout: {exc}",
            status_code=504,
        ) from exc
    except httpx.RequestError as exc:
        raise ProviderError(
            "provider_network_error",
            provider=PROVIDER,
            detail=str(exc) or "Network error",
            status_code=502,
        ) from exc

    if response.status_code == 404:
        raise ProviderError(
            "station_not_found",
            provider=PROVIDER,
            detail=f"IEM no encontró {network}|{station}",
            status_code=404,
        )
    if response.status_code == 429:
        raise ProviderError(
            "provider_ratelimit",
            provider=PROVIDER,
            detail="IEM rate limit (HTTP 429)",
            status_code=429,
        )
    if response.status_code >= 400:
        raise ProviderError(
            "provider_http_error",
            provider=PROVIDER,
            detail=f"HTTP {response.status_code}",
            status_code=502,
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderError(
            "provider_bad_response",
            provider=PROVIDER,
            detail=f"JSON inválido: {exc!r}",
            status_code=502,
        ) from exc

    rows = payload.get("data") if isinstance(payload, dict) else []
    return [row for row in rows if isinstance(row, dict)]


async def _fetch_rows(
    station_id: str,
    client: httpx.AsyncClient,
    *,
    timeout_s: float,
    now: Optional[datetime] = None,
    days_back: int = 0,
    include_previous_for_current: bool = False,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    network, station = _station_parts(station_id)
    meta = _station_meta(station_id)
    today = _local_date(meta, now)
    start = today - timedelta(days=max(0, int(days_back)))
    days = [start + timedelta(days=offset) for offset in range((today - start).days + 1)]
    if include_previous_for_current and days[0] == today:
        days.insert(0, today - timedelta(days=1))

    # Un request por día, en paralelo. Si algún día falla (red/rate-limit)
    # seguimos con los que sí llegaron: una serie parcial es mejor que un
    # gráfico vacío. Solo propagamos error si TODOS los días fallaron.
    results = await asyncio.gather(
        *(_fetch_date(network, station, day, client, timeout_s=timeout_s) for day in days),
        return_exceptions=True,
    )
    rows: List[Dict[str, Any]] = []
    first_error: Optional[BaseException] = None
    for day, result in zip(days, results):
        if isinstance(result, BaseException):
            if first_error is None:
                first_error = result
            logger.info("IEM obhistory falló para %s|%s date=%s: %s", network, station, day, result)
            continue
        rows.extend(result)
    if not rows and first_error is not None:
        raise first_error
    rows.sort(key=lambda row: _parse_epoch(row) or 0)
    return rows, meta


def _plausible_temp_c(value: float, lat: Optional[float]) -> float:
    """Anula (→ NaN) una temperatura climatológicamente IMPOSIBLE para la
    latitud de la estación. IEM agrega redes sin validar y cuela sensores rotos:
    la estación BUFR de Camboya (lat ~11) llegó a reportar −39.7°C (y otra 54°C),
    imposibles en los trópicos. Reutiliza el suelo y el techo por latitud del
    ranking (``_tmin_floor`` / ``_tmax_ceiling``), de modo que el frío/calor REAL
    (Ártico en invierno, Death Valley, Sahel ~45°C) se conserva y solo cae lo
    imposible. Sin latitud conocida (o no numérica) no se filtra."""
    if not _valid(value) or not _valid(lat):
        return value
    # Import perezoso: no acopla iem.py al módulo de ranking en tiempo de carga.
    from server.services.ranking import _tmax_ceiling, _tmin_floor

    v = float(value)
    if v < _tmin_floor(float(lat)) or v > _tmax_ceiling(float(lat)):
        return float("nan")
    return value


def _row_to_values(row: Dict[str, Any], lat: Optional[float] = None) -> Dict[str, float]:
    return {
        "temp": _plausible_temp_c(_f_to_c(row.get("tmpf")), lat),
        "dewpt": _plausible_temp_c(_f_to_c(row.get("dwpf")), lat),
        "rh": _safe_float(row.get("relh")),
        "pressure": _inhg_to_hpa(row.get("alti") or row.get("mslp")),
        "wind": _knots_to_kmh(row.get("sknt")),
        "gust": _knots_to_kmh(row.get("gust")),
        "wind_dir": _safe_float(row.get("drct")),
        "precip": _inch_to_mm(row.get("p01i")),
    }


def _valid(value: Any) -> bool:
    return isinstance(value, (int, float)) and not _is_nan(float(value))


def _latest_row(rows: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    candidates = [row for row in rows if _parse_epoch(row)]
    return candidates[-1] if candidates else None


def _no_current_detail(station_id: str) -> str:
    metadata = stations.raw_metadata(PROVIDER, station_id) or {}
    archive_begin = str(metadata.get("archive_begin") or "").strip()
    archive_end = str(metadata.get("archive_end") or "").strip()
    if archive_begin and archive_end:
        return (
            f"IEM sin observación actual para {station_id}; estación fuera de servicio "
            f"con histórico disponible entre {archive_begin} y {archive_end}"
        )
    if archive_begin:
        return (
            f"IEM sin observación actual para {station_id}; histórico disponible "
            f"desde {archive_begin}"
        )
    return f"IEM sin observaciones para {station_id}"


def _series_from_rows(rows: List[Dict[str, Any]], meta: Dict[str, Any]) -> Dict[str, Any]:
    epochs: List[int] = []
    values: Dict[str, List[float]] = {
        "temps": [], "humidities": [], "dewpts": [], "pressures": [],
        "winds": [], "gusts": [], "wind_dirs": [], "precips": [],
    }
    # ``p01i`` es la precipitación ACUMULADA dentro de la hora en curso: los
    # partes especiales intra-horarios repiten (y van incrementando) el mismo
    # acumulado, así que sumar cada fila contaría la misma lluvia varias
    # veces. El acumulado correcto es el máximo de cada hora sumado entre
    # horas; lo mantenemos incremental para poder emitir la serie punto a punto.
    precip_total = 0.0
    hourly_max: Dict[int, float] = {}
    for row in rows:
        epoch = _parse_epoch(row)
        if epoch is None:
            continue
        parsed = _row_to_values(row, meta.get("lat"))
        precip = parsed["precip"]
        if _valid(precip):
            bucket = int(epoch) // 3600
            previous_max = hourly_max.get(bucket, 0.0)
            if float(precip) > previous_max:
                precip_total += float(precip) - previous_max
                hourly_max[bucket] = float(precip)
        epochs.append(epoch)
        values["temps"].append(parsed["temp"])
        values["humidities"].append(parsed["rh"])
        values["dewpts"].append(parsed["dewpt"])
        values["pressures"].append(parsed["pressure"])
        values["winds"].append(parsed["wind"])
        values["gusts"].append(parsed["gust"])
        values["wind_dirs"].append(parsed["wind_dir"])
        values["precips"].append(precip_total if _valid(precip) else float("nan"))

    return {
        "epochs": epochs,
        **values,
        "uv_indexes": [float("nan")] * len(epochs),
        "solar_radiations": [float("nan")] * len(epochs),
        "lat": meta.get("lat"),
        "lon": meta.get("lon"),
        "has_data": bool(epochs),
    }


def _empty_series(meta: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "epochs": [], "temps": [], "humidities": [], "dewpts": [],
        "pressures": [], "uv_indexes": [], "solar_radiations": [],
        "winds": [], "gusts": [], "wind_dirs": [], "precips": [],
        "lat": meta.get("lat"), "lon": meta.get("lon"), "has_data": False,
    }


async def fetch_current(
    station_id: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 18.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    network, station = _station_parts(station_id)
    meta = _station_meta(station_id)
    if bool(meta.get("is_historical_only", False)):
        raise ProviderError(
            "provider_no_current_data",
            provider=PROVIDER,
            detail=_no_current_detail(station_id),
            status_code=502,
        )

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        rows, meta = await _fetch_rows(
            station_id, client, timeout_s=timeout_s, now=now,
            include_previous_for_current=True,
        )
        current_summary = await _fetch_current_summary(
            network, station, client, timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()

    latest = _latest_row(rows)
    if latest is None:
        raise ProviderError(
            "provider_no_current_data",
            provider=PROVIDER,
            detail=_no_current_detail(station_id),
            status_code=502,
        )

    parsed = _row_to_values(latest, meta.get("lat"))
    epoch = int(_parse_epoch(latest) or 0)
    dt_utc = datetime.fromtimestamp(epoch, tz=timezone.utc)
    tz = _station_tz(meta)
    # El acumulado diario solo puede salir de las filas de HOY (fecha local de
    # la estación). ``rows`` incluye también ayer —solo para tener una última
    # observación válida justo pasada la medianoche— y sin este filtro la
    # lluvia de ayer se colaba en el total de hoy.
    today = _local_date(meta, now)
    today_rows = [
        row for row in rows
        if (row_epoch := _parse_epoch(row)) is not None
        and datetime.fromtimestamp(row_epoch, tz=timezone.utc).astimezone(tz).date() == today
    ]
    series = _series_from_rows(today_rows, meta)
    precip_total = next(
        (float(value) for value in reversed(series["precips"]) if _valid(value)),
        float("nan"),
    )
    daily_extremes: Dict[str, float] = {}
    if current_summary:
        summary_extremes, summary_precip_total = _daily_summary_from_current(
            current_summary, meta.get("lat")
        )
        if (
            ("temp_max" in summary_extremes or "temp_min" in summary_extremes)
            and not _summary_has_current_temperature(current_summary)
            and not _rows_have_temperature(today_rows)
        ):
            summary_extremes.pop("temp_max", None)
            summary_extremes.pop("temp_min", None)
        daily_extremes.update(summary_extremes)
        if _valid(summary_precip_total):
            precip_total = summary_precip_total

    observation: Dict[str, Any] = {
        "Tc": parsed["temp"],
        "RH": parsed["rh"],
        "p_hpa": parsed["pressure"],
        "p_abs_hpa": float("nan"),
        "Td": parsed["dewpt"],
        "wind": parsed["wind"],
        "gust": parsed["gust"],
        "wind_dir_deg": parsed["wind_dir"],
        "precip_rate": parsed["precip"],
        "precip_total": precip_total,
        "solar_radiation": float("nan"),
        "uv": float("nan"),
        "epoch": epoch,
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
    timeout_s: float = 18.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        rows, meta = await _fetch_rows(station_id, client, timeout_s=timeout_s, now=now)
        if not rows:
            network, station = _station_parts(station_id)
            fallback_day = _local_date(meta, now) - timedelta(days=1)
            try:
                rows = await _fetch_date(
                    network, station, fallback_day, client, timeout_s=timeout_s,
                )
            except ProviderError as exc:
                logger.info(
                    "IEM fallback serie día anterior no disponible para %s date=%s: %s",
                    station_id, fallback_day, exc.detail or exc.error_code,
                )
                rows = []
    finally:
        if owns_client:
            await client.aclose()
    return _series_from_rows(rows, meta) if rows else _empty_series(meta)


async def fetch_recent_series(
    station_id: str,
    *,
    days_back: int = 7,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 18.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        rows, meta = await _fetch_rows(
            station_id, client, timeout_s=timeout_s, now=now,
            days_back=max(1, int(days_back)),
        )
    finally:
        if owns_client:
            await client.aclose()

    if not rows:
        return {
            "epochs": [], "temps": [], "humidities": [], "pressures": [],
            "lat": meta.get("lat"), "lon": meta.get("lon"), "has_data": False,
        }

    series = _series_from_rows(rows, meta)
    by_hour: Dict[int, int] = {}
    for index, epoch in enumerate(series["epochs"]):
        by_hour[(int(epoch) // 3600) * 3600] = index
    buckets = sorted(by_hour)

    def _col(key: str) -> List[float]:
        source = series.get(key, [])
        return [
            _safe_float(source[by_hour[bucket]]) if by_hour[bucket] < len(source) else float("nan")
            for bucket in buckets
        ]

    return {
        "epochs": buckets,
        "temps": _col("temps"),
        "humidities": _col("humidities"),
        "pressures": _col("pressures"),
        "lat": meta.get("lat"),
        "lon": meta.get("lon"),
        "has_data": bool(buckets),
    }
