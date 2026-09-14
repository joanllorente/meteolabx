"""
Servicio de IMGW-PIB (Polonia) sobre la API pública danepubliczne.imgw.pl.

Particularidades:

1. **Solo instantáneas, sin series.** ``/api/data/meteo`` devuelve de golpe las
   ~790 estaciones telemétricas con el ÚLTIMO valor de cada variable, cada una
   con su propia marca UTC. Humedad, viento y lluvia cambian cada 10 min; la
   temperatura, solo una vez por hora (la de las :10, publicada casi una hora
   después; medido el 13-09-2026). ``/api/data/synop`` hace lo mismo con las ~62 sinópticas, hora a
   hora, y es la única fuente de presión (a nivel del mar). No hay endpoint de
   histórico reciente: la serie del día y la tendencia salen de
   :class:`ImgwSeriesStore`, que un poller alimenta con el bulk cada 10 min
   (:func:`poll_loop`) y se guarda en disco para sobrevivir a los reinicios.

2. **Variables**: temperatura del aire, humedad, viento medio y máximo de 10
   min (m/s, el máximo hace de racha), dirección, lluvia de los últimos 10
   min, temperatura del suelo. Viento m/s → km/h. El punto de rocío lo deriva
   el pipeline.

3. **Día local**: Europe/Warsaw.

4. **Licencia**: datos públicos; hay que citar a IMGW-PIB como fuente.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import time
from array import array
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

from data_files import IMGW_STATIONS_PATH
from domain.parsing.common import find_station_by_field, load_stations_json
from server.schemas.errors import ProviderError

logger = logging.getLogger(__name__)

PROVIDER = "IMGW"
API_URL = "https://danepubliczne.imgw.pl/api/data"
METEO_URL = f"{API_URL}/meteo/"
SYNOP_URL = f"{API_URL}/synop/"
USER_AGENT = "MeteoLabX/1.0 (+https://meteolabx.com)"
STATION_TZ = ZoneInfo("Europe/Warsaw")

_NAN = float("nan")

# Campo de /api/data/meteo → variable del almacén.
METEO_FIELDS = {
    "temperatura_powietrza": "temp",
    "wilgotnosc_wzgledna": "rh",
    "wiatr_srednia_predkosc": "wind",
    "wiatr_predkosc_maksymalna": "wind_max",
    "wiatr_kierunek": "wind_dir",
    "opad_10min": "precip10",
}
# Una instantánea más vieja que esto no se presenta como observación actual.
CURRENT_MAX_AGE_S = 3 * 3600
# El bulk pesa ~350 KB: se comparte entre visitas durante un par de minutos.
BULK_TTL_S = 120.0


def _is_nan(value: float) -> bool:
    return value != value


def _num(value: Any) -> float:
    if value is None or value == "":
        return _NAN
    try:
        return float(value)
    except (TypeError, ValueError):
        return _NAN


def _kmh(value: float) -> float:
    return value * 3.6 if not _is_nan(value) else _NAN


def parse_utc(text: Any) -> Optional[int]:
    """``2026-09-13 08:40:00`` (UTC) → epoch."""
    try:
        parsed = datetime.strptime(str(text).strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return int(parsed.replace(tzinfo=timezone.utc).timestamp())


# =====================================================================
# Catálogo local
# =====================================================================

@lru_cache(maxsize=1)
def _load_stations() -> List[Dict[str, Any]]:
    try:
        return load_stations_json(str(IMGW_STATIONS_PATH), dict_key="stations")
    except Exception as exc:
        logger.warning("Catálogo IMGW no disponible (%s)", exc)
        return []


def _station_row(station_id: str) -> Dict[str, Any]:
    return find_station_by_field(_load_stations(), field="id", target=station_id)


def normalize_station_id(station_id: Any) -> str:
    return str(station_id or "").strip()


# =====================================================================
# Parsing de los bulks
# =====================================================================

def parse_meteo(payload: Any) -> Dict[str, Dict[str, Tuple[int, float]]]:
    """Bulk /meteo → {código: {variable: (epoch, valor)}}."""
    out: Dict[str, Dict[str, Tuple[int, float]]] = {}
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("kod_stacji") or "").strip()
        if not code:
            continue
        values: Dict[str, Tuple[int, float]] = {}
        for field, variable in METEO_FIELDS.items():
            value = _num(item.get(field))
            epoch = parse_utc(item.get(f"{field}_data"))
            if epoch is not None and not _is_nan(value):
                values[variable] = (epoch, value)
        if values:
            out[code] = values
    return out


def parse_synop(payload: Any) -> Dict[str, Dict[str, Tuple[int, float]]]:
    """Bulk /synop → {sufijo OMM de 3 cifras: {variable: (epoch, valor)}}.

    El código IMGW de una sinóptica termina en las tres últimas cifras de su
    indicativo OMM (Białystok 12295 → 353230295).
    """
    out: Dict[str, Dict[str, Tuple[int, float]]] = {}
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict):
            continue
        wmo = str(item.get("id_stacji") or "").strip()
        try:
            stamp = datetime.strptime(str(item.get("data_pomiaru")), "%Y-%m-%d").replace(
                hour=int(item.get("godzina_pomiaru")), tzinfo=timezone.utc,
            )
        except (TypeError, ValueError):
            continue
        epoch = int(stamp.timestamp())
        values = {
            "msl": (epoch, _num(item.get("cisnienie"))),
        }
        values = {key: value for key, value in values.items() if not _is_nan(value[1])}
        if wmo and values:
            out[wmo[-3:]] = values
    return out


# =====================================================================
# Almacén de series
# =====================================================================

class ImgwSeriesStore:
    """Series por estación y variable a partir de instantáneas sucesivas.

    Cada variable guarda sus marcas y valores en ``array`` (4 bytes cada uno):
    las ~790 estaciones con 8 días a 10 min caben en unas decenas de MB, y en
    dicts de Python pasarían de 300. Lo anterior a :attr:`FINE_WINDOW_S` se
    compacta a una muestra por hora (la lluvia, sumada), que es lo que usa la
    tendencia.
    """

    KEEP_S = 8 * 86400
    FINE_WINDOW_S = 36 * 3600
    _STATE_VERSION = 1

    def __init__(self) -> None:
        self._series: Dict[str, Dict[str, Tuple[array, array]]] = {}
        self.updated_at: Optional[float] = None

    def clear(self) -> None:
        self._series.clear()
        self.updated_at = None

    def __bool__(self) -> bool:
        return bool(self._series)

    def add(self, code: str, variable: str, epoch: int, value: float) -> bool:
        epochs, values = self._series.setdefault(code, {}).setdefault(
            variable, (array("i"), array("f")),
        )
        if epochs and epoch <= epochs[-1]:
            return False  # la misma instantánea vuelta a leer
        epochs.append(int(epoch))
        values.append(float(value))
        return True

    def ingest_meteo(self, parsed: Dict[str, Dict[str, Tuple[int, float]]]) -> int:
        added = 0
        for code, variables in parsed.items():
            for variable, (epoch, value) in variables.items():
                added += int(self.add(code, variable, epoch, value))
        self.updated_at = time.time()
        return added

    def ingest_synop(self, parsed: Dict[str, Dict[str, Tuple[int, float]]]) -> int:
        """Las sinópticas se casan con su estación por el sufijo OMM."""
        by_suffix = {
            str(row.get("wmo_id") or "")[-3:]: str(row.get("id"))
            for row in _load_stations() if row.get("wmo_id")
        }
        added = 0
        for suffix, variables in parsed.items():
            code = by_suffix.get(suffix)
            if not code:
                continue
            for variable, (epoch, value) in variables.items():
                added += int(self.add(code, variable, epoch, value))
        return added

    def samples(
        self, code: str, variable: str, *, since: Optional[int] = None,
    ) -> List[Tuple[int, float]]:
        epochs, values = self._series.get(code, {}).get(variable, (array("i"), array("f")))
        # float32 guarda 17.2 como 17.2000007…: se redondea al leer.
        pairs = zip(epochs, values)
        if since is not None:
            return [(int(e), round(float(v), 2)) for e, v in pairs if e >= since]
        return [(int(e), round(float(v), 2)) for e, v in pairs]

    def last(self, code: str, variable: str) -> Optional[Tuple[int, float]]:
        epochs, values = self._series.get(code, {}).get(variable, (None, None))
        if not epochs:
            return None
        return int(epochs[-1]), round(float(values[-1]), 2)

    def codes(self) -> List[str]:
        return list(self._series)

    def compact(self, *, now: Optional[float] = None) -> None:
        """Poda lo que pasa de :attr:`KEEP_S` y deja una muestra por hora en lo
        anterior a :attr:`FINE_WINDOW_S`."""
        now_s = int(now if now is not None else time.time())
        keep_from = now_s - self.KEEP_S
        fine_from = now_s - self.FINE_WINDOW_S
        for code in list(self._series):
            variables = self._series[code]
            for variable in list(variables):
                epochs, values = variables[variable]
                coarse: Dict[int, Tuple[int, float]] = {}
                fine_e, fine_v = array("i"), array("f")
                for epoch, value in zip(epochs, values):
                    if epoch < keep_from:
                        continue
                    if epoch >= fine_from:
                        fine_e.append(epoch)
                        fine_v.append(value)
                        continue
                    hour = -(-int(epoch) // 3600) * 3600  # la hora que cierra
                    if variable == "precip10":
                        previous = coarse.get(hour, (hour, 0.0))[1]
                        coarse[hour] = (hour, previous + float(value))
                    else:
                        coarse[hour] = (hour, float(value))
                new_e, new_v = array("i"), array("f")
                for hour in sorted(coarse):
                    new_e.append(hour)
                    new_v.append(coarse[hour][1])
                new_e.extend(fine_e)
                new_v.extend(fine_v)
                if new_e:
                    variables[variable] = (new_e, new_v)
                else:
                    del variables[variable]
            if not variables:
                del self._series[code]

    # -- persistencia ------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        """Copia serializable. Se toma en el bucle de eventos: escribirla en un
        hilo mientras una petición añade muestras rompería la iteración."""
        return {
            "version": self._STATE_VERSION,
            "updated_at": self.updated_at,
            "series": {
                code: {
                    variable: [list(epochs), [round(v, 2) for v in values]]
                    for variable, (epochs, values) in variables.items()
                }
                for code, variables in self._series.items()
            },
        }

    @staticmethod
    def write_snapshot(payload: Dict[str, Any], path: str) -> None:
        tmp_path = f"{path}.tmp"
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with gzip.open(tmp_path, "wt", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        os.replace(tmp_path, path)

    def save_to_disk(self, path: str) -> None:
        self.write_snapshot(self.snapshot(), path)

    def load_from_disk(self, path: str) -> bool:
        if not path or not os.path.isfile(path):
            return False
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                payload = json.load(fh)
            if payload.get("version") != self._STATE_VERSION:
                return False
            series: Dict[str, Dict[str, Tuple[array, array]]] = {}
            for code, variables in (payload.get("series") or {}).items():
                series[str(code)] = {
                    str(variable): (array("i", pair[0]), array("f", pair[1]))
                    for variable, pair in variables.items()
                    if isinstance(pair, list) and len(pair) == 2 and len(pair[0]) == len(pair[1])
                }
        except Exception:
            logger.warning("IMGW: no se pudo leer el almacén de series %s", path, exc_info=True)
            return False
        self._series = series
        self.updated_at = payload.get("updated_at")
        self.compact()
        return True


STORE = ImgwSeriesStore()


# =====================================================================
# HTTP
# =====================================================================

async def _get_json(client: httpx.AsyncClient, url: str, *, timeout_s: float) -> Any:
    try:
        response = await client.get(
            url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=timeout_s,
        )
    except httpx.TimeoutException as exc:
        raise ProviderError(
            "provider_timeout", provider=PROVIDER, detail=f"IMGW timeout: {exc}", status_code=504,
        ) from exc
    except httpx.RequestError as exc:
        raise ProviderError(
            "provider_network_error", provider=PROVIDER, detail=str(exc) or "Network error",
            status_code=502,
        ) from exc
    if response.status_code == 429:
        raise ProviderError(
            "provider_ratelimit", provider=PROVIDER, detail="IMGW rate limit (HTTP 429)",
            status_code=429,
        )
    if response.status_code >= 400:
        raise ProviderError(
            "provider_http_error", provider=PROVIDER, detail=f"HTTP {response.status_code}",
            status_code=502,
        )
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderError(
            "provider_bad_response", provider=PROVIDER, detail=f"JSON inválido: {exc!r}",
            status_code=502,
        ) from exc


_BULK: Dict[str, Tuple[float, Any]] = {}
_BULK_LOCK: Dict[str, asyncio.Lock] = {}


async def fetch_bulk(
    url: str, client: httpx.AsyncClient, *, timeout_s: float = 30.0, max_age_s: float = BULK_TTL_S,
) -> Any:
    """Bulk compartido: todas las fichas de Polonia salen de la misma descarga."""
    cached = _BULK.get(url)
    if cached and time.monotonic() - cached[0] < max_age_s:
        return cached[1]
    lock = _BULK_LOCK.setdefault(url, asyncio.Lock())
    async with lock:
        cached = _BULK.get(url)
        if cached and time.monotonic() - cached[0] < max_age_s:
            return cached[1]
        payload = await _get_json(client, url, timeout_s=timeout_s)
        _BULK[url] = (time.monotonic(), payload)
        return payload


async def refresh_store(
    client: httpx.AsyncClient, *, store: ImgwSeriesStore = STORE, include_synop: bool = True,
) -> int:
    """Una pasada del poller: bulk telemétrico + sinóptico al almacén."""
    meteo = await fetch_bulk(METEO_URL, client, max_age_s=30.0)
    added = store.ingest_meteo(parse_meteo(meteo))
    if include_synop:
        try:
            synop = await fetch_bulk(SYNOP_URL, client, max_age_s=30.0)
            added += store.ingest_synop(parse_synop(synop))
        except ProviderError as exc:
            logger.info("IMGW: bulk sinóptico no disponible (%s)", exc.detail or exc.error_code)
    return added


# Con el poller en marcha el almacén nunca pasa de ~10 min: más de 15 significa
# que el poller no está (en local) o está fallando.
STORE_FRESH_S = 15 * 60


def store_is_fresh(store: Optional["ImgwSeriesStore"] = None) -> bool:
    updated = (store if store is not None else STORE).updated_at
    return updated is not None and time.time() - updated <= STORE_FRESH_S


async def _ensure_fresh(client: httpx.AsyncClient, code: str) -> None:
    """Sin poller (en local, o recién arrancado) la ficha lee el bulk ella misma
    y deja la instantánea en el almacén. Si IMGW falla pero ya hay muestras de
    la estación, se sirven: mejor un dato de hace un rato que ninguno."""
    if store_is_fresh():
        return
    try:
        await refresh_store(client)
    except ProviderError:
        if not any(STORE.last(code, variable) for variable in METEO_FIELDS.values()):
            raise


# =====================================================================
# API pública del servicio
# =====================================================================

def _day_start_epoch(now_local: datetime) -> int:
    return int(now_local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def _last_value(code: str, variable: str, *, min_epoch: int) -> Tuple[float, Optional[int]]:
    sample = STORE.last(code, variable)
    if sample is None or sample[0] < min_epoch:
        return _NAN, None
    return sample[1], sample[0]


async def fetch_current(
    station_id: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 30.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    code = normalize_station_id(station_id)
    row = _station_row(code)
    if row and row.get("realtime") is False:
        # Observador manual del archivo: IMGW publica sus lecturas con uno o
        # dos meses de retraso y fuera de la API, así que no hay dato actual.
        raise ProviderError(
            "provider_no_current_data",
            provider=PROVIDER,
            detail=(
                f"IMGW {code}: estación manual sin datos en tiempo real; "
                "sus lecturas se publican en el archivo con uno o dos meses de retraso"
            ),
            status_code=502,
        )
    now_local = (now or datetime.now(tz=STATION_TZ)).astimezone(STATION_TZ)
    now_epoch = int(now_local.timestamp())
    day_start = _day_start_epoch(now_local)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        await _ensure_fresh(client, code)
    finally:
        if owns_client:
            await client.aclose()

    fresh_from = now_epoch - CURRENT_MAX_AGE_S
    temp, temp_epoch = _last_value(code, "temp", min_epoch=fresh_from)
    epochs = [
        sample[0] for variable in METEO_FIELDS.values()
        if (sample := STORE.last(code, variable)) is not None and sample[0] >= fresh_from
    ]
    if not epochs:
        # El proveedor contestó; es la estación la que no publica.
        raise ProviderError(
            "provider_no_current_data",
            provider=PROVIDER,
            detail=f"IMGW sin observaciones recientes para {code}",
            status_code=502,
        )
    epoch = temp_epoch or max(epochs)
    dt_utc = datetime.fromtimestamp(epoch, tz=timezone.utc)

    # La lluvia de cada muestra es la de los 10 min que terminan en ella.
    precip = [v for e, v in STORE.samples(code, "precip10", since=day_start + 600)]
    temps = [v for e, v in STORE.samples(code, "temp", since=day_start)]
    gusts = [v for e, v in STORE.samples(code, "wind_max", since=day_start)]
    daily_extremes: Dict[str, float] = {}
    if temps:
        daily_extremes["temp_max"] = max(temps)
        daily_extremes["temp_min"] = min(temps)
    if gusts:
        daily_extremes["gust_max"] = _kmh(max(gusts))

    observation: Dict[str, Any] = {
        "Tc": temp,
        "RH": _last_value(code, "rh", min_epoch=fresh_from)[0],
        "p_hpa": _last_value(code, "msl", min_epoch=fresh_from)[0],
        "p_abs_hpa": _NAN,
        "wind": _kmh(_last_value(code, "wind", min_epoch=fresh_from)[0]),
        "gust": _kmh(_last_value(code, "wind_max", min_epoch=fresh_from)[0]),
        "wind_dir_deg": _last_value(code, "wind_dir", min_epoch=fresh_from)[0],
        "Td": _NAN,
        "feels_like": _NAN,
        "heat_index": _NAN,
        "wind_chill": _NAN,
        "precip_rate": _NAN,
        "precip_total": float(sum(max(0.0, v) for v in precip)) if precip else _NAN,
        "solar_radiation": _NAN,
        "uv": _NAN,
        "epoch": epoch,
        "time_local": dt_utc.astimezone(STATION_TZ).isoformat(),
        "time_utc": dt_utc.isoformat(),
        "lat": _num(row.get("lat")),
        "lon": _num(row.get("lon")),
        "elevation": _num(row.get("elev")) if row.get("elev") is not None else 0.0,
        "station_name": str(row.get("name", "") or "").strip() or code,
        "daily_extremes": daily_extremes,
    }
    from domain.observation_pipeline import add_basic_derived
    return add_basic_derived(observation)


def _aligned(code: str, variables: Iterable[str], *, since: int) -> Tuple[List[int], Dict[str, Dict[int, float]]]:
    columns = {variable: dict(STORE.samples(code, variable, since=since)) for variable in variables}
    epochs = sorted(set().union(*(column.keys() for column in columns.values())))
    return epochs, columns


def _series_shape(code: str, row: Dict[str, Any], *, since: int) -> Dict[str, Any]:
    variables = ("temp", "rh", "msl", "wind", "wind_max", "wind_dir", "precip10")
    epochs, columns = _aligned(code, variables, since=since)
    lat, lon = _num(row.get("lat")), _num(row.get("lon"))
    if not epochs:
        return {
            "epochs": [], "temps": [], "humidities": [], "dewpts": [], "pressures": [],
            "uv_indexes": [], "solar_radiations": [], "winds": [], "gusts": [],
            "wind_dirs": [], "lat": lat, "lon": lon, "has_data": False,
        }

    def _col(variable: str, convert=None) -> List[float]:
        values = [columns[variable].get(epoch, _NAN) for epoch in epochs]
        return [convert(v) for v in values] if convert else values

    return {
        "epochs": epochs,
        "temps": _col("temp"),
        "humidities": _col("rh"),
        "dewpts": [_NAN] * len(epochs),
        "pressures": _col("msl"),
        "uv_indexes": [_NAN] * len(epochs),
        "solar_radiations": [_NAN] * len(epochs),
        "precip_step_mm": _col("precip10"),
        "winds": _col("wind", convert=_kmh),
        "gusts": _col("wind_max", convert=_kmh),
        "wind_dirs": _col("wind_dir"),
        "lat": lat,
        "lon": lon,
        "has_data": True,
    }


async def fetch_today_series(
    station_id: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 30.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Serie del día local desde el almacén (10 min donde la estación lo da)."""
    code = normalize_station_id(station_id)
    row = _station_row(code)
    now_local = (now or datetime.now(tz=STATION_TZ)).astimezone(STATION_TZ)
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        await _ensure_fresh(client, code)
    finally:
        if owns_client:
            await client.aclose()
    series = _series_shape(code, row, since=_day_start_epoch(now_local))
    if series["has_data"] and series["epochs"][0] == _day_start_epoch(now_local):
        # La lluvia de la muestra de medianoche es de los 10 min de ayer.
        series["precip_step_mm"][0] = _NAN
    return series


async def fetch_recent_series(
    station_id: str,
    *,
    days_back: int = 7,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 30.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Tendencia (T/HR/presión) desde el almacén: los días que lleve
    acumulados, como máximo :attr:`ImgwSeriesStore.KEEP_S`."""
    code = normalize_station_id(station_id)
    row = _station_row(code)
    now_utc = (now or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        await _ensure_fresh(client, code)
    finally:
        if owns_client:
            await client.aclose()
    since = int((now_utc - timedelta(days=max(1, int(days_back)))).timestamp())
    epochs, columns = _aligned(code, ("temp", "rh", "msl"), since=since)
    lat, lon = _num(row.get("lat")), _num(row.get("lon"))
    if not epochs:
        return {"epochs": [], "temps": [], "humidities": [], "pressures": [],
                "lat": lat, "lon": lon, "has_data": False}
    return {
        "epochs": epochs,
        "temps": [columns["temp"].get(e, _NAN) for e in epochs],
        "humidities": [columns["rh"].get(e, _NAN) for e in epochs],
        "pressures": [columns["msl"].get(e, _NAN) for e in epochs],
        "lat": lat,
        "lon": lon,
        "has_data": True,
    }


# =====================================================================
# Poller
# =====================================================================

POLL_INTERVAL_S = 600.0
SAVE_EVERY_S = 1800.0


async def poll_loop(
    client: httpx.AsyncClient,
    *,
    state_path: str = "",
    interval_s: float = POLL_INTERVAL_S,
    store: ImgwSeriesStore = STORE,
) -> None:
    """Lee el bulk cada 10 min, compacta cada hora y guarda cada media hora."""
    last_save = time.monotonic()
    last_compact = time.monotonic()
    while True:
        try:
            added = await refresh_store(client, store=store)
            logger.debug("IMGW: %d muestras nuevas", added)
        except Exception as exc:  # noqa: BLE001 — el poller no muere por un fallo
            logger.warning("IMGW: pasada del poller fallida (%s)", type(exc).__name__)
        if time.monotonic() - last_compact >= 3600:
            store.compact()
            last_compact = time.monotonic()
        if state_path and time.monotonic() - last_save >= SAVE_EVERY_S:
            try:
                await asyncio.to_thread(store.write_snapshot, store.snapshot(), state_path)
            except Exception:
                logger.warning("IMGW: no se pudo guardar el almacén en %s", state_path, exc_info=True)
            last_save = time.monotonic()
        await asyncio.sleep(interval_s)
