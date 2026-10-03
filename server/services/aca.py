"""
Servicio puro de la ACA (Agència Catalana de l'Aigua): pluviómetros de su
plataforma Sentilo de datos en tiempo real
(``aplicacions.aca.gencat.cat/sdim2/apirest``).

Particularidades:

1. **Solo lluvia, y en intensidad**: cada estación tiene un único sensor,
   «Intensitat de precipitació» en mm/h cada 5 minutos. La lluvia de cada
   paso es ``intensidad × 5/60``: los valores van en múltiplos de 1,2 mm/h,
   que son los 0,1 mm del balancín en 5 minutos. El resto de variables va a
   NaN.

2. **Marca en UTC que cierra el intervalo**: el dato de las 10:05 es la lluvia
   de 10:00-10:05, así que el de las 00:00 locales es de ayer. Cada
   observación trae ``time`` (epoch en ms) y ``timestamp``
   (``dd/mm/aaaaTHH:MM:SS``, UTC).

3. **Topes por consulta**: la de un sensor devuelve como mucho 200 datos (16 h
   y media) y la de toda la red, 50 por sensor (4 h). En los dos casos, los
   MÁS RECIENTES del intervalo pedido: un intervalo más largo no da error,
   pierde el principio. Por eso se trocea: 12 h por sensor y 4 h por red.

4. **Sin histórico**: la plataforma borra las medidas a los tres meses y los
   datos no están validados.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

from data_files import ACA_STATIONS_PATH
from domain.parsing.common import find_station_by_field, load_stations_json
from server.schemas.errors import ProviderError

logger = logging.getLogger(__name__)

PROVIDER = "ACA"
BASE_URL = "https://aplicacions.aca.gencat.cat/sdim2/apirest"
SENTILO_PROVIDER = "PLUVIOMETREACA-EST"
USER_AGENT = "MeteoLabX/1.0 (+https://meteolabx.com)"
DEFAULT_TZ = "Europe/Madrid"

# Todos los pluviómetros miden cada 5 minutos, también los 21 que no lo
# declaran en el catálogo (comprobado con sus series).
STEP_S = 300
# Tramos por consulta, por debajo de los topes de la API (ver arriba): 144 y
# 48 datos.
SENSOR_WINDOW_S = 12 * 3600
NETWORK_WINDOW_S = 4 * 3600
NETWORK_CONCURRENCY = 3
RETRY_DELAYS_S = (0.5, 2.0)
# Una lectura más vieja que esto no se presenta como observación actual.
CURRENT_MAX_AGE_S = 3 * 3600

_NAN = float("nan")


# =====================================================================
# Catálogo local
# =====================================================================

@lru_cache(maxsize=1)
def _load_stations() -> List[Dict[str, Any]]:
    try:
        return load_stations_json(str(ACA_STATIONS_PATH), dict_key="stations")
    except Exception as exc:
        logger.warning("Catálogo ACA no disponible (%s)", exc)
        return []


def station_row(station_id: str) -> Dict[str, Any]:
    return find_station_by_field(_load_stations(), field="id", target=normalize_station_id(station_id))


def stations_by_sensor() -> Dict[str, Dict[str, Any]]:
    return {str(row["sensor"]): row for row in _load_stations() if row.get("sensor")}


def normalize_station_id(station_id: Any) -> str:
    return str(station_id or "").strip()


def station_tz(row: Dict[str, Any]) -> ZoneInfo:
    try:
        return ZoneInfo(str(row.get("tz") or DEFAULT_TZ))
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def _num(value: Any) -> float:
    if value is None:
        return _NAN
    try:
        return float(value)
    except (TypeError, ValueError):
        return _NAN


def step_mm(intensity: float) -> float:
    """Lluvia de un paso de 5 minutos a partir de su intensidad en mm/h."""
    if intensity != intensity or intensity < 0:
        return _NAN
    return intensity * STEP_S / 3600.0


# =====================================================================
# HTTP + parsing
# =====================================================================

def _stamp(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%d/%m/%YT%H:%M:%S")


def _parse_epoch(observation: Dict[str, Any]) -> Optional[int]:
    raw = observation.get("time")
    if raw is not None:
        try:
            return int(raw) // 1000
        except (TypeError, ValueError):
            pass
    try:
        parsed = datetime.strptime(str(observation.get("timestamp") or ""), "%d/%m/%YT%H:%M:%S")
    except ValueError:
        return None
    return int(parsed.replace(tzinfo=timezone.utc).timestamp())


def parse_observations(observations: Any) -> Dict[int, float]:
    """``[{"value": "2.4", "time": ms, ...}]`` → {epoch UTC: mm/h}."""
    out: Dict[int, float] = {}
    for observation in observations if isinstance(observations, list) else []:
        if not isinstance(observation, dict):
            continue
        epoch = _parse_epoch(observation)
        value = _num(str(observation.get("value") or "").strip() or None)
        if epoch is None or value != value:
            continue
        out[epoch] = value
    return out


async def _get_json(
    client: httpx.AsyncClient, path: str, params: Dict[str, Any], *, timeout_s: float,
) -> Any:
    for attempt, delay in enumerate((*RETRY_DELAYS_S, None)):
        try:
            response = await client.get(
                f"{BASE_URL}{path}",
                params=params,
                headers={"Accept": "application/json", "User-Agent": USER_AGENT},
                timeout=timeout_s,
            )
            break
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "provider_timeout", provider=PROVIDER, detail=f"ACA timeout: {exc}", status_code=504,
            ) from exc
        except httpx.RequestError as exc:
            # El servidor corta a veces la conexión reutilizada sin responder
            # («Server disconnected»): a la segunda suele contestar.
            if delay is None:
                raise ProviderError(
                    "provider_network_error", provider=PROVIDER,
                    detail=str(exc) or "Network error", status_code=502,
                ) from exc
            logger.info("ACA: reintento %d de %s (%s)", attempt + 1, path, type(exc).__name__)
            await asyncio.sleep(delay)
    if response.status_code >= 400:
        raise ProviderError(
            "provider_http_error", provider=PROVIDER, detail=f"HTTP {response.status_code}", status_code=502,
        )
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderError(
            "provider_bad_response", provider=PROVIDER, detail=f"JSON inválido: {exc!r}", status_code=502,
        ) from exc


def _windows(since_epoch: int, until_epoch: int, size_s: int) -> List[Tuple[int, int]]:
    """Tramos ``[inicio, fin]`` contiguos y sin solape (el fin, un segundo antes
    del siguiente inicio)."""
    out = []
    start = since_epoch
    while start <= until_epoch:
        end = min(start + size_s - 1, until_epoch)
        out.append((start, end))
        start = end + 1
    return out


async def fetch_sensor(
    client: httpx.AsyncClient,
    sensor: str,
    *,
    since_epoch: int,
    until_epoch: int,
    timeout_s: float = 30.0,
) -> Dict[int, float]:
    """Serie de un sensor entre dos instantes (incluidos)."""
    out: Dict[int, float] = {}
    for start, end in _windows(since_epoch, until_epoch, SENSOR_WINDOW_S):
        payload = await _get_json(
            client,
            f"/data/{SENTILO_PROVIDER}/{sensor}",
            {"limit": 200, "from": _stamp(start), "to": _stamp(end)},
            timeout_s=timeout_s,
        )
        observations = payload.get("observations") if isinstance(payload, dict) else None
        out.update(parse_observations(observations))
    return {epoch: value for epoch, value in out.items() if since_epoch <= epoch <= until_epoch}


async def fetch_network(
    client: httpx.AsyncClient,
    *,
    since_epoch: int,
    until_epoch: int,
    timeout_s: float = 60.0,
) -> Dict[str, Dict[int, float]]:
    """Series de TODOS los pluviómetros entre dos instantes (incluidos):
    {sensor: {epoch UTC: mm/h}}."""
    semaphore = asyncio.Semaphore(NETWORK_CONCURRENCY)

    async def _window(start: int, end: int) -> Any:
        async with semaphore:
            return await _get_json(
                client,
                f"/data/{SENTILO_PROVIDER}",
                {"limit": 200, "from": _stamp(start), "to": _stamp(end)},
                timeout_s=timeout_s,
            )

    payloads = await asyncio.gather(*(
        _window(start, end) for start, end in _windows(since_epoch, until_epoch, NETWORK_WINDOW_S)
    ))
    out: Dict[str, Dict[int, float]] = {}
    for payload in payloads:
        sensors = payload.get("sensors") if isinstance(payload, dict) else None
        for entry in sensors if isinstance(sensors, list) else []:
            if not isinstance(entry, dict) or not entry.get("sensor"):
                continue
            series = out.setdefault(str(entry["sensor"]), {})
            for epoch, value in parse_observations(entry.get("observations")).items():
                if since_epoch <= epoch <= until_epoch:
                    series[epoch] = value
    return out


# =====================================================================
# API pública del servicio
# =====================================================================

def _station(station_id: str) -> Dict[str, Any]:
    row = station_row(station_id)
    if not row or not row.get("sensor"):
        raise ProviderError(
            "station_not_found",
            provider=PROVIDER,
            detail=f"Pluviómetro ACA no encontrado: {station_id}",
            status_code=404,
        )
    return row


def _day_start_epoch(now_local: datetime) -> int:
    return int(now_local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


# La ficha pide la observación y la serie del día casi a la vez, y las dos salen
# de la misma consulta: se comparte durante dos minutos (el sensor publica cada
# cinco).
_TODAY_CACHE: Dict[Tuple[str, int], Tuple[float, Dict[int, float]]] = {}
TODAY_TTL_S = 120.0


async def _today(
    row: Dict[str, Any], client: Optional[httpx.AsyncClient], *,
    now: Optional[datetime], timeout_s: float,
) -> Tuple[Dict[int, float], datetime]:
    tz = station_tz(row)
    now_local = (now or datetime.now(tz=tz)).astimezone(tz)
    sensor = str(row["sensor"])
    day_start = _day_start_epoch(now_local)
    key = (sensor, day_start)
    cached = _TODAY_CACHE.get(key)
    if cached and time.monotonic() - cached[0] < TODAY_TTL_S:
        return cached[1], now_local
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        series = await fetch_sensor(
            client, sensor,
            # La última hora de ayer: de madrugada da la lectura actual aunque
            # hoy aún no haya nada.
            since_epoch=day_start - 3600,
            until_epoch=int(now_local.timestamp()),
            timeout_s=timeout_s,
        )
    finally:
        if owns_client:
            await client.aclose()
    if len(_TODAY_CACHE) > 500:
        _TODAY_CACHE.clear()
    _TODAY_CACHE[key] = (time.monotonic(), series)
    return series, now_local


def _no_current(station_id: str, detail: str) -> ProviderError:
    return ProviderError(
        "provider_no_current_data", provider=PROVIDER, detail=f"ACA {station_id}: {detail}", status_code=502,
    )


async def fetch_current(
    station_id: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 30.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    row = _station(station_id)
    series, now_local = await _today(row, client, now=now, timeout_s=timeout_s)
    now_epoch = int(now_local.timestamp())
    fresh = [epoch for epoch in series if epoch >= now_epoch - CURRENT_MAX_AGE_S]
    if not fresh:
        raise _no_current(station_id, "sin observaciones recientes")
    epoch = max(fresh)
    day_start = _day_start_epoch(now_local)
    steps = [step_mm(value) for stamp, value in series.items() if stamp > day_start]
    tz = station_tz(row)
    dt_utc = datetime.fromtimestamp(epoch, tz=timezone.utc)
    observation: Dict[str, Any] = {
        "Tc": _NAN,
        "RH": _NAN,
        "p_hpa": _NAN,
        "p_abs_hpa": _NAN,
        "Td": _NAN,
        "wind": _NAN,
        "gust": _NAN,
        "wind_dir_deg": _NAN,
        "feels_like": _NAN,
        "heat_index": _NAN,
        "wind_chill": _NAN,
        "precip_rate": series[epoch] if series[epoch] >= 0 else _NAN,
        "precip_total": float(sum(value for value in steps if value == value)),
        "solar_radiation": _NAN,
        "uv": _NAN,
        "epoch": epoch,
        "time_local": dt_utc.astimezone(tz).isoformat(),
        "time_utc": dt_utc.isoformat(),
        "lat": _num(row.get("lat")),
        "lon": _num(row.get("lon")),
        "elevation": _num(row.get("elev")) if row.get("elev") is not None else 0.0,
        "station_name": str(row.get("name") or "").strip() or normalize_station_id(station_id),
        "daily_extremes": {},
    }
    from domain.observation_pipeline import add_basic_derived
    return add_basic_derived(observation)


_SERIES_KEYS = (
    "epochs", "temps", "humidities", "dewpts", "pressures", "uv_indexes",
    "solar_radiations", "winds", "gusts", "wind_dirs", "precip_step_mm",
)


def _empty(row: Dict[str, Any], keys) -> Dict[str, Any]:
    out: Dict[str, Any] = {key: [] for key in keys}
    out.update(lat=_num(row.get("lat")), lon=_num(row.get("lon")), has_data=False)
    return out


async def fetch_today_series(
    station_id: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 30.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Serie de 5 minutos del día local: solo la lluvia de cada paso."""
    row = _station(station_id)
    series, now_local = await _today(row, client, now=now, timeout_s=timeout_s)
    day_start = _day_start_epoch(now_local)
    epochs = sorted(epoch for epoch in series if epoch > day_start)
    if not epochs:
        return _empty(row, _SERIES_KEYS)
    nans = [_NAN] * len(epochs)
    return {
        "epochs": epochs,
        "temps": list(nans),
        "humidities": list(nans),
        "dewpts": list(nans),
        "pressures": list(nans),
        "uv_indexes": list(nans),
        "solar_radiations": list(nans),
        "winds": list(nans),
        "gusts": list(nans),
        "wind_dirs": list(nans),
        "precip_step_mm": [step_mm(series[epoch]) for epoch in epochs],
        "lat": _num(row.get("lat")),
        "lon": _num(row.get("lon")),
        "has_data": True,
    }


async def fetch_recent_series(
    station_id: str,
    *,
    days_back: int = 7,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 30.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """La tendencia es de temperatura, humedad y presión: un pluviómetro no
    tiene nada que aportar y no se consulta."""
    return _empty(_station(station_id), ("epochs", "temps", "humidities", "pressures"))


# =====================================================================
# Totales diarios (ranking)
# =====================================================================

def daily_totals(
    series: Dict[int, float],
    *,
    day_start: int,
    day_end: int,
    min_coverage: float,
) -> Optional[Tuple[float, int, int]]:
    """Lluvia de ``(day_start, day_end]`` como ``(mm, pasos, último epoch)``.

    ``None`` sin datos o si faltan demasiados pasos: con solo intensidades, un
    hueco es lluvia que no se cuenta, y un total al que le faltan horas no se
    puede presentar como el del día.
    """
    stamps = sorted(epoch for epoch in series if day_start < epoch <= day_end)
    if not stamps:
        return None
    expected = max(1, (day_end - day_start) // STEP_S)
    # Al empezar el día, pocos pasos: no se exige cobertura hasta la media hora.
    if expected >= 6 and len(stamps) < min_coverage * expected:
        return None
    values = [step_mm(series[epoch]) for epoch in stamps]
    total = sum(value for value in values if value == value)
    return total, len(stamps), stamps[-1]


def network_window(now: datetime, *, tz_name: str = DEFAULT_TZ) -> Tuple[int, int, int]:
    """``(desde, inicio de hoy, ahora)`` para el ranking: las últimas 24 h (para
    la ventana móvil) y, de madrugada, el día de ayer entero para cerrarlo."""
    tz = ZoneInfo(tz_name)
    now_local = now.astimezone(tz)
    day_start = _day_start_epoch(now_local)
    now_epoch = int(now_local.timestamp())
    since = now_epoch - 24 * 3600
    if now_local.hour < 3:
        yesterday = (now_local - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        since = min(since, int(yesterday.timestamp()))
    return since, day_start, now_epoch
