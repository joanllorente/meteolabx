"""
Ranking diario de estaciones por Tmáx / Tmín / ráfaga / lluvia.

Sirve la pestaña "Ranking": top-N de estaciones del día local por cada
métrica, por proveedor (país del usuario) y combinado global.

Dos clases de proveedor:

- **Directos**: el endpoint bulk ya devuelve el día agregado en una
  llamada (MeteoGalicia, Meteocat, MeteoHub). Se re-fetchea y se
  **sobrescribe** el agregado diario.
- **Acumulables**: el bulk es un *snapshot* horario / de 12 h (AEMET,
  Meteo-France). Se hace **upsert por hora** y se reduce a diario
  (``max`` Tmáx, ``min`` Tmín, ``max`` ráfaga, ``sum`` lluvia). El
  upsert por ``hour`` es idempotente → resistente a solapes de ventana
  y re-polls (la lluvia, que es suma, no se duplica).

El estado vive en memoria (``RankingStore``, en ``app.state``). No
persiste entre reinicios: AEMET backfillea 12 h en el primer poll;
Meteo-France pierde las horas previas del día → degradación aceptable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

# Métricas del ranking y su reducción diaria a partir de valores horarios.
METRICS = ("tmax", "tmin", "gust", "rain")
METRIC_REDUCER = {"tmax": "max", "tmin": "min", "gust": "max", "rain": "sum"}
# Orden del ranking: Tmín asciende (la más baja primero), el resto desciende.
METRIC_DESC = {"tmax": True, "tmin": False, "gust": True, "rain": True}

# Huso local por proveedor: define el "día en curso" del ranking.
PROVIDER_TZ = {
    "AEMET": "Europe/Madrid",
    "METEOCAT": "Europe/Madrid",
    "METEOGALICIA": "Europe/Madrid",
    "METEOFRANCE": "Europe/Paris",
    "METEOHUB_IT": "Europe/Rome",
    "IPMA": "Europe/Lisbon",
    "GEOSPHERE": "Europe/Vienna",
    "SMHI": "Europe/Stockholm",
    "FROST": "Europe/Oslo",
    # Canadá cruza 6 husos: UTC como clave de bucket; cada estación aporta
    # su día local (como IEM).
    "ECCC": "UTC",
    # IEM mezcla 204 husos: usamos UTC como clave de "día" del bucket; cada
    # estación trae su propio día local en ``currents.json`` (campo agregado).
    "IEM": "UTC",
    # AWS antárticas italianas: cada registro trae su día local nominal
    # (huso solar por longitud); UTC solo como fallback.
    "CLIMANTARTIDE": "UTC",
}

# País fijo (ISO2) de los proveedores de un solo país. El store lo estampa en
# cada registro que no traiga ya su país, de modo que el filtro por país del
# ranking funcione de forma uniforme. IEM NO está aquí: trae país por estación.
PROVIDER_FIXED_COUNTRY = {
    "AEMET": "ES",
    "METEOCAT": "ES",
    "METEOGALICIA": "ES",
    "EUSKALMET": "ES",
    "POEM": "ES",
    "METEOFRANCE": "FR",
    "METEOHUB_IT": "IT",
    "IPMA": "PT",
    "GEOSPHERE": "AT",
    "SMHI": "SE",
    "FROST": "NO",
    "ECCC": "CA",
}

# País → proveedor nacional para el "ranking del país del usuario".
# Los regionales (Meteocat/MeteoGalicia/Euskalmet) no son "país".
COUNTRY_PROVIDER = {
    "ES": "AEMET",
    "FR": "METEOFRANCE",
    "IT": "METEOHUB_IT",
    "PT": "IPMA",
    "AT": "GEOSPHERE",
    "SE": "SMHI",
    "CA": "ECCC",
}

MG_BASE = "https://servizos.meteogalicia.gal/mgrss/observacion"
MG_DAILY_ENDPOINT = f"{MG_BASE}/datosDiariosEstacionsMeteo.action"


@dataclass
class StationDaily:
    """Agregado diario de una estación para las 4 métricas."""

    provider: str
    station_id: str
    name: str
    locality: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    tmax: Optional[float] = None
    tmin: Optional[float] = None
    gust: Optional[float] = None
    rain: Optional[float] = None
    # Precipitacion acumulada en una ventana movil REAL de 24 horas. No se
    # rellena a partir del total civil de ``rain``: solo cuando el bulk trae
    # muestras horarias suficientes para reconstruir [ahora-24h, ahora].
    rain_24h: Optional[float] = None
    rain_24h_at: Optional[int] = None
    # Temperatura ACTUAL (última lectura), para el mapa de temperaturas.
    # No participa en el ranking; solo la aportan los adaptadores cuyo bulk
    # trae la instantánea (IEM, AEMET, Meteo-France).
    tcur: Optional[float] = None
    # Epoch UTC de esa instantánea: el mapa descarta lecturas viejas (una
    # estación parada de madrugada pintaría frío nocturno a mediodía).
    tcur_at: Optional[int] = None
    # Viento medio ACTUAL en km/h y dirección meteorológica de procedencia
    # (0°=N, 90°=E). La flecha del mapa usa un tamaño fijo; la velocidad se
    # representa únicamente mediante el campo de color.
    wind: Optional[float] = None
    wind_dir: Optional[float] = None
    wind_at: Optional[int] = None
    # ISO2 del país de la estación. Para proveedores de un solo país lo
    # estampa el store; IEM (multi-país) lo trae por estación. Permite el
    # "ranking del país del usuario" filtrando por país en vez de por proveedor.
    country: str = ""
    # Fecha local de la estación (ISO ``YYYY-MM-DD``) a la que pertenece este
    # agregado diario. Es la CLAVE de bucket del ranking: una mínima de
    # madrugada del 22 cae en el 22, no en el 21. IEM la trae por estación
    # (``currents.json`` campo ``local_date``); los proveedores de un solo huso
    # caen al día local del proveedor.
    local_date: str = ""
    # Hora local de la última lectura (``HH:MM``), solo para mostrar en la
    # cajita de la estación. IEM la da en ``local_valid``.
    local_time: str = ""

    def value(self, metric: str) -> Optional[float]:
        return getattr(self, metric, None)


def _num(value) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    # MeteoGalicia (y otros) usan -9999 como centinela de "sin dato".
    if f <= -9990.0:
        return None
    return f


# ----------------------------------------------------------------------
# Adaptador: MeteoGalicia (directo, sin API key)
# ----------------------------------------------------------------------
# Códigos de parámetro del servicio diario de MeteoGalicia.
# Tmáx/Tmín en ºC, lluvia en L/m2 (= mm); ráfaga VV_MAX_10m en m/s
# (se convierte a km/h para homogeneizar con AEMET/Meteo-France).
_MG_CODE = {
    "tmax": "TA_MAX_1.5m",
    "tmin": "TA_MIN_1.5m",
    "rain": "PP_SUM_1.5m",
    "gust": "VV_MAX_10m",
}


def _ms_to_kmh(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value * 3.6, 1)


def _k_to_c(value) -> Optional[float]:
    v = _num(value)
    if v is None:
        return None
    return v - 273.15 if v > 170.0 else v  # tolera datos ya en °C


def _parse_mg_day(dia: dict) -> List[StationDaily]:
    estaciones = dia.get("listaEstacions", []) if isinstance(dia, dict) else []
    out: List[StationDaily] = []
    for est in estaciones:
        if not isinstance(est, dict):
            continue
        measures = est.get("listaMedidas", []) or []
        by_code = {str(m.get("codigoParametro", "")): m for m in measures if isinstance(m, dict)}
        rec = StationDaily(
            provider="METEOGALICIA",
            station_id=str(est.get("idEstacion", "")).strip(),
            name=str(est.get("estacion", "")).strip(),
            locality=str(est.get("concello", "")).strip(),
            tmax=_num(by_code.get(_MG_CODE["tmax"], {}).get("valor")),
            tmin=_num(by_code.get(_MG_CODE["tmin"], {}).get("valor")),
            rain=_num(by_code.get(_MG_CODE["rain"], {}).get("valor")),
            gust=_ms_to_kmh(_num(by_code.get(_MG_CODE["gust"], {}).get("valor"))),
        )
        if rec.station_id:
            out.append(rec)
    return out


def _station_has_data(r: StationDaily) -> bool:
    return (
        r.tcur is not None
        or (r.wind is not None and r.wind_dir is not None)
        or r.rain_24h is not None
        or any(r.value(m) is not None for m in METRICS)
    )


def parse_meteogalicia_daily(payload: dict) -> List[StationDaily]:
    """Devuelve el día MÁS RECIENTE que esté suficientemente poblado.

    De madrugada el día en curso aún viene con centinelas (o con una o dos
    estaciones sueltas): en ese caso se mantiene el día anterior para no
    mostrar un ranking vacío ni saltar a un "hoy" casi vacío. Se cambia a
    hoy cuando alcanza un umbral razonable de estaciones con dato (~30% del
    mejor día, mínimo 15)."""
    dias = payload.get("listDatosDiarios") if isinstance(payload, dict) else None
    if not isinstance(dias, list) or not dias:
        return []
    days = [_parse_mg_day(d) for d in dias]  # orden: más antiguo → más reciente
    counts = [sum(1 for r in recs if _station_has_data(r)) for recs in days]
    best = max(counts) if counts else 0
    if best == 0:
        return []
    # Umbral ABSOLUTO: hoy gana en cuanto tiene ≥ _DAY_FLOOR estaciones.
    for recs, n in zip(reversed(days), reversed(counts)):  # del más reciente
        if n >= _DAY_FLOOR:
            return recs
    return days[counts.index(best)]


async def fetch_meteogalicia_daily(
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 30.0,
) -> List[StationDaily]:
    """TODAS las estaciones de MeteoGalicia (1 llamada). Pide ayer+hoy para
    que de madrugada (hoy aún sin datos) caiga al día anterior."""
    owns = client is None
    if owns:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        today = datetime.now(ZoneInfo(PROVIDER_TZ["METEOGALICIA"])).date()
        yesterday = today - timedelta(days=1)
        resp = await client.get(
            MG_DAILY_ENDPOINT,
            params={"dataIni": yesterday.isoformat(), "dataFin": today.isoformat()},
            headers={"Accept": "application/json"},
            timeout=timeout_s,
        )
        resp.raise_for_status()
        records = parse_meteogalicia_daily(resp.json())

        # Instantánea 10-minutal en bulk (1 llamada extra, todas las
        # estaciones): el feed diario no trae lectura actual y sin ella las
        # estaciones gallegas quedan fuera del mapa de temperaturas.
        try:
            from server.services import meteogalicia as mg

            payload = await mg._get_json(client, mg.TENMIN_ENDPOINT, {}, timeout_s=timeout_s)
            items = mg._extract_items(
                payload, keys=["listUltimos10min", "listaUltimos10min", "ultimos10min"],
            )
            instant: Dict[str, Tuple[int, float]] = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                sid = str(item.get("idEstacion") or "").strip()
                epoch = mg._instant_epoch(item)
                temp = mg._extract_measures(item.get("listaMedidas", [])).get("temp")
                if sid and epoch and temp is not None and temp == temp:
                    instant[sid] = (int(epoch), float(temp))
            for rec in records:
                held = instant.get(rec.station_id)
                if held:
                    rec.tcur_at, rec.tcur = held[0], round(held[1], 1)
        except Exception as exc:
            logger.warning("ranking: instantánea MeteoGalicia no disponible (%s)", type(exc).__name__)
        return records
    finally:
        if owns:
            await client.aclose()


# ----------------------------------------------------------------------
# Adaptador: Meteocat (directo, API key; 1 llamada por variable)
# ----------------------------------------------------------------------
MC_BASE = "https://api.meteo.cat/xema/v1"
# Variables XEMA: Tmáx=40, Tmín=42, lluvia=35, ráfaga (ratxa màx 10m, m/s)=50.
_MC_VAR = {"tmax": 40, "tmin": 42, "rain": 35, "gust": 50}
# Variable 32 = temperatura semihoraria: su última lectura es la instantánea
# para el mapa de temperaturas (una 5ª llamada por ciclo).
_MC_TCUR_VAR = 32
_MC_WIND_VAR = 30
_MC_WIND_DIR_VAR = 31


async def _mc_fetch_instant(
    client: httpx.AsyncClient, api_key: str, day, timeout_s: float,
    var_code: int = _MC_TCUR_VAR,
) -> Dict[str, Tuple[int, float]]:
    """Última lectura de una variable por estación → {codi: (epoch, valor)}."""
    url = f"{MC_BASE}/variables/mesurades/{var_code}/{day.year:04d}/{day.month:02d}/{day.day:02d}"
    resp = await client.get(
        url, headers={"x-api-key": api_key, "Accept": "application/json"}, timeout=timeout_s
    )
    if resp.status_code in (400, 404):
        return {}
    resp.raise_for_status()
    data = resp.json()
    out: Dict[str, Tuple[int, float]] = {}
    for st in data if isinstance(data, list) else []:
        codi = str(st.get("codi", "")).strip().upper()
        if not codi:
            continue
        best: Optional[Tuple[int, float]] = None
        for var in st.get("variables", []) or []:
            for lec in var.get("lectures", []) or []:
                value = _num(lec.get("valor"))
                if value is None:
                    continue
                try:
                    epoch = int(datetime.fromisoformat(
                        str(lec.get("data") or "").replace("Z", "+00:00")
                    ).timestamp())
                except ValueError:
                    continue
                if best is None or epoch > best[0]:
                    best = (epoch, value)
        if best:
            out[codi] = best
    return out


async def _mc_fetch_variable(
    client: httpx.AsyncClient, api_key: str, var_code: int, day, timeout_s: float
) -> Dict[str, List[float]]:
    """``/variables/mesurades/{var}/{Y}/{M}/{D}`` (todas las estaciones) →
    {codi_estacion: [valores semihorarios]}."""
    url = f"{MC_BASE}/variables/mesurades/{var_code}/{day.year:04d}/{day.month:02d}/{day.day:02d}"
    resp = await client.get(
        url, headers={"x-api-key": api_key, "Accept": "application/json"}, timeout=timeout_s
    )
    # Meteocat devuelve 400/404 cuando el día/variable aún no tiene datos
    # (p. ej. el día en curso de madrugada) → lo tratamos como "sin datos"
    # para que el fallback caiga al día anterior. El resto sí propaga.
    if resp.status_code in (400, 404):
        return {}
    resp.raise_for_status()
    data = resp.json()
    out: Dict[str, List[float]] = {}
    if not isinstance(data, list):
        return out
    for st in data:
        codi = str(st.get("codi", "")).strip().upper()
        if not codi:
            continue
        vals: List[float] = []
        for var in st.get("variables", []) or []:
            for lec in var.get("lectures", []) or []:
                v = _num(lec.get("valor"))
                if v is not None:
                    vals.append(v)
        if vals:
            out[codi] = vals
    return out


async def _mc_fetch_variable_samples(
    client: httpx.AsyncClient, api_key: str, var_code: int, day, timeout_s: float,
) -> Dict[str, List[Tuple[int, float]]]:
    """Como ``_mc_fetch_variable``, conservando el epoch de cada lectura."""
    url = f"{MC_BASE}/variables/mesurades/{var_code}/{day.year:04d}/{day.month:02d}/{day.day:02d}"
    resp = await client.get(
        url, headers={"x-api-key": api_key, "Accept": "application/json"}, timeout=timeout_s,
    )
    if resp.status_code in (400, 404):
        return {}
    resp.raise_for_status()
    data = resp.json()
    out: Dict[str, List[Tuple[int, float]]] = {}
    for station in data if isinstance(data, list) else []:
        codi = str(station.get("codi", "")).strip().upper()
        if not codi:
            continue
        samples: List[Tuple[int, float]] = []
        for variable in station.get("variables", []) or []:
            for reading in variable.get("lectures", []) or []:
                value = _num(reading.get("valor"))
                try:
                    epoch = int(datetime.fromisoformat(
                        str(reading.get("data") or "").replace("Z", "+00:00")
                    ).timestamp())
                except ValueError:
                    continue
                if value is not None:
                    samples.append((epoch, value))
        if samples:
            out[codi] = sorted(samples)
    return out


def _mc_build_records(
    raw: Dict[str, Dict[str, List[float]]],
    instant: Optional[Dict[str, Tuple[int, float]]] = None,
    wind_instant: Optional[Dict[str, Tuple[int, float]]] = None,
    direction_instant: Optional[Dict[str, Tuple[int, float]]] = None,
    rain_24h: Optional[Dict[str, Tuple[float, int]]] = None,
) -> List[StationDaily]:
    from server.services import meteocat

    instant = instant or {}
    wind_instant = wind_instant or {}
    direction_instant = direction_instant or {}
    rain_24h = rain_24h or {}
    codis = set()
    for m in METRICS:
        codis |= set(raw.get(m, {}).keys())
    codis |= set(instant.keys())
    codis |= set(wind_instant.keys()) & set(direction_instant.keys())
    catalog = meteocat._load_station_catalog()
    out: List[StationDaily] = []
    for codi in codis:
        lat, lon, _elev, name = meteocat._station_meta(codi)
        station = catalog.get(codi, {})
        municipi = station.get("municipi") if isinstance(station, dict) else None
        locality = str(municipi.get("nom", "")).strip() if isinstance(municipi, dict) else ""
        tmax_vals = raw.get("tmax", {}).get(codi)
        tmin_vals = raw.get("tmin", {}).get(codi)
        rain_vals = raw.get("rain", {}).get(codi)
        gust_vals = raw.get("gust", {}).get(codi)
        wind_sample = wind_instant.get(codi)
        direction_sample = direction_instant.get(codi)
        has_current_wind = bool(
            wind_sample and direction_sample
            and abs(int(wind_sample[0]) - int(direction_sample[0])) <= 3600
        )
        out.append(
            StationDaily(
                provider="METEOCAT",
                station_id=codi,
                name=name or codi,
                locality=locality,
                lat=lat if lat == lat else None,
                lon=lon if lon == lon else None,
                tmax=round(max(tmax_vals), 1) if tmax_vals else None,
                tmin=round(min(tmin_vals), 1) if tmin_vals else None,
                rain=round(sum(rain_vals), 1) if rain_vals else None,
                rain_24h=(round(rain_24h[codi][0], 1) if codi in rain_24h else None),
                rain_24h_at=(rain_24h[codi][1] if codi in rain_24h else None),
                gust=_daily_gust_max_from_series([v * 3.6 for v in gust_vals]) if gust_vals else None,
                tcur=round(instant[codi][1], 1) if codi in instant else None,
                tcur_at=instant[codi][0] if codi in instant else None,
                wind=round(float(wind_sample[1]) * 3.6, 1) if has_current_wind else None,
                wind_dir=float(direction_sample[1]) % 360.0 if has_current_wind else None,
                wind_at=max(int(wind_sample[0]), int(direction_sample[0])) if has_current_wind else None,
            )
        )
    return out


async def fetch_meteocat_daily(
    api_key: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 30.0,
) -> List[StationDaily]:
    """Día de TODAS las estaciones Meteocat (4 llamadas, una por variable).
    Fallback a ayer si hoy aún está poco poblado (madrugada)."""
    tz = ZoneInfo(PROVIDER_TZ["METEOCAT"])
    today = datetime.now(tz).date()

    owns = client is None
    if owns:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        async def _day(day) -> List[StationDaily]:
            raw = {}
            for metric, var in _MC_VAR.items():
                if metric == "rain":
                    samples = await _mc_fetch_variable_samples(
                        client, api_key, var, day, timeout_s,
                    )
                    raw[metric] = {
                        sid: [value for _epoch, value in rows]
                        for sid, rows in samples.items()
                    }
                else:
                    raw[metric] = await _mc_fetch_variable(client, api_key, var, day, timeout_s)
            try:
                instant = await _mc_fetch_instant(client, api_key, day, timeout_s)
                wind_instant, direction_instant = await asyncio.gather(
                    _mc_fetch_instant(
                        client, api_key, day, timeout_s, _MC_WIND_VAR,
                    ),
                    _mc_fetch_instant(
                        client, api_key, day, timeout_s, _MC_WIND_DIR_VAR,
                    ),
                )
            except Exception as exc:
                logger.warning("ranking: instantánea Meteocat no disponible (%s)", type(exc).__name__)
                instant = {}
                wind_instant = {}
                direction_instant = {}
            rolling: Dict[str, Tuple[float, int]] = {}
            if day == today:
                yesterday_samples = await _mc_fetch_variable_samples(
                    client, api_key, _MC_VAR["rain"], today - timedelta(days=1), timeout_s,
                )
                cutoff = int(datetime.now(tz).timestamp()) - 24 * 3600
                for sid in set(samples) | set(yesterday_samples):
                    rows = [
                        item
                        for item in yesterday_samples.get(sid, []) + samples.get(sid, [])
                        if item[0] >= cutoff
                    ]
                    if rows:
                        rolling[sid] = (
                            sum(max(0.0, value) for _epoch, value in rows),
                            max(epoch for epoch, _value in rows),
                        )
            return _mc_build_records(
                raw, instant, wind_instant, direction_instant, rolling,
            )

        recs = await _day(today)
        if sum(1 for r in recs if _station_has_data(r)) < 15:
            recs = await _day(today - timedelta(days=1))
        return recs
    finally:
        if owns:
            await client.aclose()


# ----------------------------------------------------------------------
# Adaptador: MeteoHub IT (directo; 1 llamada por red → 25 redes)
# ----------------------------------------------------------------------
# Sin API key. La API exige el parámetro `networks` (no admite "todas") →
# se itera red a red. La respuesta trae nombre (B01019) y lat/lon en `stat`,
# así que no hace falta el catálogo. MeteoHub NO reporta ráfaga → gust=None.
def _mh_networks() -> List[str]:
    from data_files import METEOHUB_IT_STATIONS_PATH

    rows = json.load(open(METEOHUB_IT_STATIONS_PATH, encoding="utf-8"))
    rows = rows.get("estaciones", rows) if isinstance(rows, dict) else rows
    return sorted({str(r.get("network")) for r in rows if r.get("network")})


def _mh_parse_station(st: dict, *, day_start_epoch: Optional[int] = None) -> Optional[StationDaily]:
    from server.services import meteohub as mh

    stat = st.get("stat", {}) if isinstance(st, dict) else {}
    lat, lon, net = stat.get("lat"), stat.get("lon"), str(stat.get("net", ""))
    name = ""
    for det in stat.get("details", []) or []:
        if isinstance(det, dict) and det.get("var") == "B01019":
            name = str(det.get("val") or "").strip()
    aligned = mh._align_series(mh._products_by_code(st))
    daily_pairs = [
        (epoch, temp, precip)
        for epoch, temp, precip in zip(
            aligned["epochs"], aligned["temps"], aligned["precips"],
        )
        if day_start_epoch is None or int(epoch) >= int(day_start_epoch)
    ]
    temps = [temp for _epoch, temp, _precip in daily_pairs if temp == temp]
    precs = [precip for _epoch, _temp, precip in daily_pairs if precip == precip]
    rolling_precip = [
        (int(epoch), float(precip))
        for epoch, precip in zip(aligned["epochs"], aligned["precips"])
        if precip == precip
    ]
    # Instantánea: última muestra válida de la serie del día (para el mapa
    # de temperaturas; el filtro de frescura descarta estaciones paradas).
    tcur = tcur_at = None
    for epoch, value in zip(reversed(aligned["epochs"]), reversed(aligned["temps"])):
        if value == value:
            tcur, tcur_at = round(float(value), 1), int(epoch)
            break
    wind = wind_dir = wind_at = None
    for epoch, speed, direction in zip(
        reversed(aligned["epochs"]),
        reversed(aligned["winds"]),
        reversed(aligned["wind_dirs"]),
    ):
        if speed == speed and direction == direction:
            wind = round(float(speed), 1)
            wind_dir = float(direction) % 360.0
            wind_at = int(epoch)
            break
    sid = f"{net}|{lat}|{lon}|{name}".lower()
    return StationDaily(
        provider="METEOHUB_IT",
        station_id=sid,
        name=name or sid,
        locality="",
        lat=float(lat) if isinstance(lat, (int, float)) else None,
        lon=float(lon) if isinstance(lon, (int, float)) else None,
        tmax=round(max(temps), 1) if temps else None,
        tmin=round(min(temps), 1) if temps else None,
        gust=None,  # MeteoHub no reporta ráfaga
        rain=round(sum(precs), 1) if precs else None,
        rain_24h=(
            round(sum(max(0.0, value) for _epoch, value in rolling_precip), 1)
            if rolling_precip else None
        ),
        rain_24h_at=max((epoch for epoch, _value in rolling_precip), default=None),
        tcur=tcur,
        tcur_at=tcur_at,
        wind=wind,
        wind_dir=wind_dir,
        wind_at=wind_at,
    )


async def fetch_meteohub_daily(
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 90.0,
) -> List[StationDaily]:
    """Estaciones italianas del día en curso (1 llamada por red, 25 redes).
    Las redes se consultan EN PARALELO (con límite de concurrencia) para que
    una red lenta/caída no bloquee el ciclo — conexiones a MeteoHub pueden
    tardar ~5s (fallback IPv6). Tmáx/Tmín de la serie de temp, lluvia = suma;
    sin ráfaga."""
    import asyncio

    from server.services import meteohub as mh

    now = datetime.now(tz=mh.STATION_TZ)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = now - timedelta(hours=24)
    # Query ligera: solo temperatura (B12101) y lluvia (B13011) — las únicas
    # que usa el ranking — en vez de las 6 productos de _build_query. Reduce
    # mucho el tamaño de respuesta (25 redes) y el tiempo del ciclo.
    start_text = start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
    end_text = now.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
    q = (
        f"reftime: >={start_text},<={end_text};"
        f"timerange:{' or '.join(mh.QUERY_TIMERANGES)};"
        f"level:{' or '.join(mh.QUERY_LEVELS)};"
        f"license:{mh.LICENSE_GROUP};"
        f"product:{mh.P_TEMP} or {mh.P_PRECIP}"
    )
    headers = {"Accept": "application/json", "User-Agent": "MeteoLabX/1.0 (+https://meteolabx.com)"}

    owns = client is None
    if owns:
        client = httpx.AsyncClient(timeout=timeout_s)

    sem = asyncio.Semaphore(8)  # ≤ max_keepalive del pool; evita saturar

    async def _fetch_network(net: str) -> List[StationDaily]:
        async with sem:
            try:
                resp = await client.get(
                    f"{mh.BASE_URL}/api/observations",
                    params={"q": q, "networks": net}, headers=headers, timeout=timeout_s,
                )
                if resp.status_code != 200:
                    return []
                payload = json.loads(resp.text)
                data = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(data, list):
                    return []
                recs = []
                for st in data:
                    rec = _mh_parse_station(
                        st, day_start_epoch=int(day_start.timestamp()),
                    )
                    if rec and rec.station_id and _station_has_data(rec):
                        recs.append(rec)
                return recs
            except Exception as exc:
                logger.warning("ranking: red MeteoHub %s sin datos (%s)", net, type(exc).__name__)
                return []

    try:
        results = await asyncio.gather(*[_fetch_network(net) for net in _mh_networks()])
        return [rec for sub in results for rec in sub]
    finally:
        if owns:
            await client.aclose()


# ----------------------------------------------------------------------
# Adaptador: IPMA (Portugal, directo; bulk nativo)
# ----------------------------------------------------------------------
# `observations.json` trae las últimas 24 h de TODA la red en una llamada.
# Tmáx/Tmín se reducen de las medias horarias (IPMA no publica extremos →
# quedan ligeramente recortados frente a un extremo minutal). Sin ráfaga.
# Nombre/coordenadas/huso desde el catálogo local; cada estación usa SU huso
# (continente/Madeira/Azores) para decidir el día en curso.
def _ipma_catalog() -> Dict[str, dict]:
    from data_files import IPMA_STATIONS_PATH

    payload = json.load(open(IPMA_STATIONS_PATH, encoding="utf-8"))
    rows = payload.get("stations", payload) if isinstance(payload, dict) else payload
    return {str(r.get("id")): r for r in rows if isinstance(r, dict) and r.get("id")}


async def fetch_ipma_daily(
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 60.0,
    now: Optional[datetime] = None,
) -> List[StationDaily]:
    """Estaciones portuguesas del día en curso (1 llamada, feed de 24 h).
    De madrugada el día local aún tiene pocas horas pobladas: si menos de 15
    estaciones traen datos se mantiene el día anterior (mismo criterio que
    Meteocat/MeteoGalicia) para no publicar un ranking vacío."""
    from server.services import ipma as ip

    owns = client is None
    if owns:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        payload = await ip._get_json(client, ip.OBSERVATIONS_URL, timeout_s=timeout_s)
    finally:
        if owns:
            await client.aclose()
    if not isinstance(payload, dict):
        return []

    from domain.parsing.common import parse_epoch

    # station_id → [(epoch, temp, precip, viento km/h, dirección)] con el
    # centinela -99.0 ya convertido a None.
    samples: Dict[str, List[Tuple[
        int, Optional[float], Optional[float], Optional[float], Optional[float],
    ]]] = {}
    for timestamp, readings in payload.items():
        if not isinstance(readings, dict):
            continue
        epoch = parse_epoch(timestamp)
        if epoch is None:
            continue
        for sid, reading in readings.items():
            if not isinstance(reading, dict):
                continue
            temp = _num(reading.get("temperatura"))
            prec = _num(reading.get("precAcumulada"))
            wind = _num(reading.get("intensidadeVentoKM"))
            if wind is None:
                wind_ms = _num(reading.get("intensidadeVento"))
                wind = wind_ms * 3.6 if wind_ms is not None else None
            wind_dir_raw = ip._wind_dir_deg(reading.get("idDireccVento"))
            wind_dir = float(wind_dir_raw) if wind_dir_raw == wind_dir_raw else None
            if temp is not None and temp <= -99.0:
                temp = None
            if prec is not None and prec <= -99.0:
                prec = None
            if wind is not None and wind <= -99.0:
                wind = None
            if temp is None and prec is None and wind is None:
                continue
            samples.setdefault(str(sid), []).append(
                (int(epoch), temp, prec, wind, wind_dir),
            )

    catalog = _ipma_catalog()
    now_utc = (now or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)

    def _build(day_offset: int) -> List[StationDaily]:
        recs: List[StationDaily] = []
        for sid, rows in samples.items():
            rows = sorted(rows)  # el feed no garantiza orden temporal
            meta = catalog.get(sid, {})
            try:
                tz = ZoneInfo(str(meta.get("tz") or PROVIDER_TZ["IPMA"]))
            except Exception:
                tz = ZoneInfo(PROVIDER_TZ["IPMA"])
            day_local = (now_utc.astimezone(tz) - timedelta(days=day_offset)).date()
            day_rows = [
                row for row in rows
                if datetime.fromtimestamp(row[0], tz=timezone.utc).astimezone(tz).date() == day_local
            ]
            temps = [t for _, t, _, _, _ in day_rows if t is not None]
            precs = [p for _, _, p, _, _ in day_rows if p is not None]
            tcur = tcur_at = None
            for epoch, temp, _, _, _ in sorted(rows, reverse=True):
                if temp is not None:
                    tcur, tcur_at = round(float(temp), 1), int(epoch)
                    break
            wind = wind_dir = wind_at = None
            for epoch, _, _, speed, direction in sorted(rows, reverse=True):
                if speed is not None and direction is not None:
                    wind = round(float(speed), 1)
                    wind_dir = float(direction) % 360.0
                    wind_at = int(epoch)
                    break
            local_time = ""
            if day_rows:
                local_time = (
                    datetime.fromtimestamp(day_rows[-1][0], tz=timezone.utc)
                    .astimezone(tz).strftime("%H:%M")
                )
            rec = StationDaily(
                provider="IPMA",
                station_id=sid,
                name=str(meta.get("name") or sid).strip(),
                locality=str(meta.get("region") or "").strip(),
                lat=_num(meta.get("lat")),
                lon=_num(meta.get("lon")),
                tmax=round(max(temps), 1) if temps else None,
                tmin=round(min(temps), 1) if temps else None,
                gust=None,  # IPMA no reporta ráfaga
                rain=round(sum(precs), 1) if precs else None,
                rain_24h=round(sum(
                    max(0.0, float(prec))
                    for epoch, _temp, prec, _wind, _direction in rows
                    if prec is not None and epoch >= int(now_utc.timestamp()) - 24 * 3600
                ), 1) if any(
                    prec is not None and epoch >= int(now_utc.timestamp()) - 24 * 3600
                    for epoch, _temp, prec, _wind, _direction in rows
                ) else None,
                rain_24h_at=max(
                    (
                        epoch for epoch, _temp, prec, _wind, _direction in rows
                        if prec is not None and epoch >= int(now_utc.timestamp()) - 24 * 3600
                    ),
                    default=None,
                ),
                tcur=tcur,
                tcur_at=tcur_at,
                wind=wind,
                wind_dir=wind_dir,
                wind_at=wind_at,
                local_date=day_local.isoformat(),
                local_time=local_time,
            )
            if _station_has_data(rec):
                recs.append(rec)
        return recs

    recs = _build(0)
    if sum(1 for r in recs if r.tmax is not None or r.rain is not None) < 15:
        recs = _build(1)
    return recs


# ----------------------------------------------------------------------
# Adaptador: GeoSphere Austria (directo; bulk nativo)
# ----------------------------------------------------------------------
# El Data Hub acepta todas las estaciones en una llamada. Se piden los
# extremos POR BLOQUE de 10 min (TLMAX/TLMIN/FFX): el agregado diario es el
# extremo real medido, no el de medias. RR es el acumulado de cada bloque.
def _gs_station_ids() -> List[str]:
    """Solo la red TAWES (10 min): las estaciones KLIMA (``manual``, ids
    ``K…``) son de dato diario y el dataset 10-minutal las rechaza con 400."""
    from data_files import GEOSPHERE_STATIONS_PATH

    payload = json.load(open(GEOSPHERE_STATIONS_PATH, encoding="utf-8"))
    rows = payload.get("stations", payload) if isinstance(payload, dict) else payload
    return [
        str(r.get("id")) for r in rows
        if isinstance(r, dict) and r.get("id") and not r.get("manual")
    ]


def _gs_catalog() -> Dict[str, dict]:
    from data_files import GEOSPHERE_STATIONS_PATH

    payload = json.load(open(GEOSPHERE_STATIONS_PATH, encoding="utf-8"))
    rows = payload.get("stations", payload) if isinstance(payload, dict) else payload
    return {str(r.get("id")): r for r in rows if isinstance(r, dict) and r.get("id")}


async def fetch_geosphere_daily(
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 90.0,
    now: Optional[datetime] = None,
) -> List[StationDaily]:
    """Estaciones austríacas del día en curso (1 llamada bulk de 10 min).
    De madrugada, si menos de 15 estaciones traen datos aún, se mantiene el
    día anterior (mismo criterio que Meteocat/MeteoGalicia/IPMA)."""
    from server.services import geosphere as gs

    tz = ZoneInfo(PROVIDER_TZ["GEOSPHERE"])
    now_local = (now or datetime.now(tz)).astimezone(tz)
    station_ids = _gs_station_ids()
    if not station_ids:
        return []

    async def _day(day_offset: int) -> List[StationDaily]:
        day_local = (now_local - timedelta(days=day_offset)).date()
        day_start = datetime(day_local.year, day_local.month, day_local.day, tzinfo=tz)
        # La consulta principal abarca 24 h exactas. Los extremos/ranking se
        # filtran despues al dia local; RR conserva la ventana completa.
        start = now_local - timedelta(hours=24) if day_offset == 0 else day_start
        end = now_local if day_offset == 0 else min(now_local, day_start + timedelta(days=1))
        payload = await gs._get_json(
            client,
            gs.DATASET_URL,
            {
                "parameters": "TL,TLMAX,TLMIN,FF,DD,FFX,RR",
                "station_ids": ",".join(station_ids),
                "start": gs._to_iso_minute(start),
                "end": gs._to_iso_minute(end),
            },
            timeout_s=timeout_s,
        )
        timestamps = payload.get("timestamps") if isinstance(payload, dict) else None
        features = payload.get("features") if isinstance(payload, dict) else None
        if not isinstance(timestamps, list) or not isinstance(features, list):
            return []
        from domain.parsing.common import parse_epoch

        epochs = [parse_epoch(ts) for ts in timestamps]
        catalog = _gs_catalog()
        recs: List[StationDaily] = []
        for feature in features:
            properties = feature.get("properties") if isinstance(feature, dict) else None
            if not isinstance(properties, dict):
                continue
            sid = str(properties.get("station") or "").strip()
            parameters = properties.get("parameters")
            if not sid or not isinstance(parameters, dict):
                continue

            def _series(name: str) -> List[Optional[float]]:
                block = parameters.get(name)
                values = block.get("data") if isinstance(block, dict) else None
                return [_num(v) for v in values] if isinstance(values, list) else []

            def _series_for_day(name: str) -> List[Optional[float]]:
                values = _series(name)
                return [
                    value
                    for index, value in enumerate(values)
                    if (
                        index < len(epochs)
                        and epochs[index] is not None
                        and datetime.fromtimestamp(
                            epochs[index], tz=timezone.utc,
                        ).astimezone(tz).date() == day_local
                    )
                ]

            tl = _series("TL")
            day_tl = _series_for_day("TL")
            tlmax = [v for v in _series_for_day("TLMAX") if v is not None] or [v for v in day_tl if v is not None]
            tlmin = [v for v in _series_for_day("TLMIN") if v is not None] or [v for v in day_tl if v is not None]
            gusts_kmh = [v * 3.6 for v in _series_for_day("FFX") if v is not None]
            precs = [max(0.0, v) for v in _series_for_day("RR") if v is not None]
            rolling_precs = [max(0.0, v) for v in _series("RR") if v is not None]
            wind_series = _series("FF")
            direction_series = _series("DD")
            tcur = tcur_at = None
            for idx in range(len(tl) - 1, -1, -1):
                if tl[idx] is not None and idx < len(epochs) and epochs[idx] is not None:
                    tcur, tcur_at = round(float(tl[idx]), 1), int(epochs[idx])
                    break
            wind = wind_dir = wind_at = None
            for idx in range(min(len(wind_series), len(direction_series), len(epochs)) - 1, -1, -1):
                if (
                    wind_series[idx] is not None
                    and direction_series[idx] is not None
                    and epochs[idx] is not None
                ):
                    wind = round(float(wind_series[idx]) * 3.6, 1)
                    wind_dir = float(direction_series[idx]) % 360.0
                    wind_at = int(epochs[idx])
                    break
            meta = catalog.get(sid, {})
            rec = StationDaily(
                provider="GEOSPHERE",
                station_id=sid,
                name=str(meta.get("name") or sid).strip(),
                locality=str(meta.get("region") or "").strip(),
                lat=_num(meta.get("lat")),
                lon=_num(meta.get("lon")),
                tmax=round(max(tlmax), 1) if tlmax else None,
                tmin=round(min(tlmin), 1) if tlmin else None,
                gust=round(_daily_gust_max_from_series(gusts_kmh), 1) if gusts_kmh else None,
                rain=round(sum(precs), 1) if precs else None,
                rain_24h=(
                    round(sum(rolling_precs), 1)
                    if day_offset == 0 and rolling_precs else None
                ),
                rain_24h_at=(
                    max((int(epoch) for epoch in epochs if epoch is not None), default=None)
                    if day_offset == 0 and rolling_precs else None
                ),
                tcur=tcur,
                tcur_at=tcur_at,
                wind=wind,
                wind_dir=wind_dir,
                wind_at=wind_at,
                local_date=day_local.isoformat(),
                local_time=(
                    datetime.fromtimestamp(tcur_at, tz=timezone.utc).astimezone(tz).strftime("%H:%M")
                    if tcur_at else ""
                ),
            )
            if _station_has_data(rec):
                recs.append(rec)
        return recs

    owns = client is None
    if owns:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        recs = await _day(0)
        if sum(1 for r in recs if r.tmax is not None or r.rain is not None) < 15:
            recs = await _day(1)
        return recs
    finally:
        if owns:
            await client.aclose()


# ----------------------------------------------------------------------
# Adaptador: SMHI (Suecia, ACUMULABLE; bulk de la última hora)
# ----------------------------------------------------------------------
# ``station-set/all/period/latest-hour`` devuelve TODA la red de un
# parámetro en una llamada (3 por ciclo: temperatura, racha, lluvia 1 h).
# SMHI no ofrece backfill bulk del día, así que los extremos se ACUMULAN
# hora a hora en el store (como Meteo-France); la persistencia a disco
# cubre los reinicios. La temperatura es instantánea horaria → los
# extremos diarios quedan ligeramente recortados (como IPMA).
def _smhi_catalog() -> Dict[str, dict]:
    from data_files import SMHI_STATIONS_PATH

    payload = json.load(open(SMHI_STATIONS_PATH, encoding="utf-8"))
    rows = payload.get("stations", payload) if isinstance(payload, dict) else payload
    return {str(r.get("id")): r for r in rows if isinstance(r, dict) and r.get("id")}


async def fetch_smhi_records(
    store: "RankingStore",
    *,
    client: httpx.AsyncClient,
    timeout_s: float = 60.0,
    now: Optional[datetime] = None,
) -> List[StationDaily]:
    """ACUMULABLE: 3 llamadas bulk (temp/racha/lluvia de la última hora),
    upsert por hora en el store y reducción a agregados diarios."""
    import asyncio

    from server.services import smhi as sm

    tz = ZoneInfo(PROVIDER_TZ["SMHI"])

    async def _bulk(parameter: str) -> Dict[str, Tuple[int, float]]:
        url = (
            f"{sm.BASE_URL}/parameter/{parameter}"
            "/station-set/all/period/latest-hour/data.json"
        )
        payload = await sm._get_json(client, url, timeout_s=timeout_s)
        out: Dict[str, Tuple[int, float]] = {}
        for station in payload.get("station", []) if isinstance(payload, dict) else []:
            if not isinstance(station, dict):
                continue
            sid = str(station.get("key") or "").strip()
            values = station.get("value") or []
            if not sid or not isinstance(values, list) or not values:
                continue
            item = values[-1]
            value = _num(item.get("value")) if isinstance(item, dict) else None
            try:
                epoch = int(item.get("date")) // 1000
            except (TypeError, ValueError, AttributeError):
                continue
            if value is not None:
                out[sid] = (epoch, value)
        return out

    async def _rain_bulk() -> Dict[str, List[Tuple[int, float]]]:
        url = (
            f"{sm.BASE_URL}/parameter/{sm.P_RAIN}"
            "/station-set/all/period/latest-day/data.json"
        )
        payload = await sm._get_json(client, url, timeout_s=timeout_s)
        out: Dict[str, List[Tuple[int, float]]] = {}
        for station in payload.get("station", []) if isinstance(payload, dict) else []:
            if not isinstance(station, dict):
                continue
            sid = str(station.get("key") or "").strip()
            if not sid:
                continue
            samples: List[Tuple[int, float]] = []
            for item in station.get("value") or []:
                value = _num(item.get("value")) if isinstance(item, dict) else None
                try:
                    epoch = int(item.get("date")) // 1000
                except (TypeError, ValueError, AttributeError):
                    continue
                if value is not None:
                    samples.append((epoch, max(0.0, value)))
            if samples:
                out[sid] = sorted(samples)
        return out

    temps, winds, directions, gusts, rain_series = await asyncio.gather(
        _bulk(sm.P_TEMP), _bulk(sm.P_WIND), _bulk(sm.P_DIR),
        _bulk(sm.P_GUST), _rain_bulk(),
    )

    catalog = _smhi_catalog()
    for sid in set(temps) | set(winds) | set(directions) | set(gusts) | set(rain_series):
        meta = catalog.get(sid, {})
        epoch = (
            temps.get(sid) or winds.get(sid) or directions.get(sid)
            or gusts.get(sid) or (rain_series.get(sid) or [(None, None)])[-1]
        )[0]
        local_dt = datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone(tz)
        temp = temps.get(sid, (None, None))[1]
        gust_ms = gusts.get(sid, (None, None))[1]
        wind_ms = winds.get(sid, (None, None))[1]
        wind_dir = directions.get(sid, (None, None))[1]
        wind_epoch = (winds.get(sid) or directions.get(sid) or (None, None))[0]
        store.upsert_hourly(
            "SMHI",
            sid,
            day=local_dt.date().isoformat(),
            hour_key=local_dt.strftime("%Y-%m-%dT%H"),
            name=str(meta.get("name") or sid).strip(),
            locality="",
            lat=_num(meta.get("lat")),
            lon=_num(meta.get("lon")),
            values={
                "tmax": temp,
                "tmin": temp,
                "gust": round(gust_ms * 3.6, 1) if gust_ms is not None else None,
                "tcur": temp,
                "tcur_at": epoch,
                "wind": round(wind_ms * 3.6, 1) if wind_ms is not None else None,
                "wind_dir": float(wind_dir) % 360.0 if wind_dir is not None else None,
                "wind_at": wind_epoch,
            },
        )
        for rain_epoch, rain_value in rain_series.get(sid, []):
            rain_local = datetime.fromtimestamp(
                rain_epoch, tz=timezone.utc,
            ).astimezone(tz)
            store.upsert_hourly(
                "SMHI",
                sid,
                day=rain_local.date().isoformat(),
                hour_key=rain_local.strftime("%Y-%m-%dT%H"),
                name=str(meta.get("name") or sid).strip(),
                locality="",
                lat=_num(meta.get("lat")),
                lon=_num(meta.get("lon")),
                values={"rain": rain_value, "rain_at": rain_epoch},
            )
    return store.reduce_accumulable_records("SMHI", now=now)


# ----------------------------------------------------------------------
# Adaptador: ECCC (Canadá, ACUMULABLE; bulk de la hora en punto)
# ----------------------------------------------------------------------
# swob-realtime no tiene "última obs por estación", pero casi toda la red
# reporta a la hora en punto: la ventana [H:00, H:10) captura la obs de
# ~99,9% de las estaciones de la hora (medido: 1283 distintas/hora, 12
# fuera de los 6 primeros minutos, casi todas en :06-:08) con ~5k items,
# bajo el límite de 10k. Los extremos se ACUMULAN hora a hora (como SMHI).
# Canadá cruza 6 husos → el día de cada estación usa SU tz del catálogo.
def _eccc_catalog() -> Dict[str, dict]:
    from data_files import ECCC_STATIONS_PATH

    payload = json.load(open(ECCC_STATIONS_PATH, encoding="utf-8"))
    rows = payload.get("stations", payload) if isinstance(payload, dict) else payload
    return {str(r.get("id")): r for r in rows if isinstance(r, dict) and r.get("id")}


async def fetch_eccc_records(
    store: "RankingStore",
    *,
    client: httpx.AsyncClient,
    timeout_s: float = 90.0,
    now: Optional[datetime] = None,
) -> List[StationDaily]:
    """ACUMULABLE: 1 llamada bulk por ciclo (ventana de la última hora en
    punto), upsert por hora local de cada estación y reducción diaria."""
    from server.services import eccc as ec

    now_utc = (now or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)
    top = now_utc.replace(minute=0, second=0, microsecond=0)
    if (now_utc - top).total_seconds() < 600:
        top -= timedelta(hours=1)

    payload = await ec._get_json(
        client,
        ec.SWOB_URL,
        {
            "f": "json",
            "datetime": (
                top.strftime("%Y-%m-%dT%H:%M:%SZ")
                + "/"
                + (top + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
            ),
            "limit": 10000,
            "properties": (
                "obs_date_tm,msc_id-value,air_temp,"
                "max_air_temp_pst1hr,min_air_temp_pst1hr,"
                "max_wnd_spd_10m_pst1hr,pcpn_amt_pst1hr"
            ),
        },
        timeout_s=timeout_s,
    )

    # Última obs de la ventana por estación.
    latest: Dict[str, Dict[str, Any]] = {}
    for feature in (payload.get("features") or []) if isinstance(payload, dict) else []:
        props = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(props, dict):
            continue
        sid = str(props.get("msc_id-value") or "").strip()
        from domain.parsing.common import parse_epoch

        epoch = parse_epoch(props.get("obs_date_tm"))
        if not sid or epoch is None:
            continue
        current = latest.get(sid)
        if current is None or epoch >= current["epoch"]:
            latest[sid] = {"epoch": int(epoch), **props}

    catalog = _eccc_catalog()
    for sid, props in latest.items():
        meta = catalog.get(sid)
        if not meta:
            continue
        try:
            tz = ZoneInfo(str(meta.get("tz") or "America/Toronto"))
        except Exception:
            tz = timezone.utc
        local_dt = datetime.fromtimestamp(props["epoch"], tz=timezone.utc).astimezone(tz)
        temp = _num(props.get("air_temp"))
        # Extremos horarios EXPLÍCITOS → máx/mín diario exacto; la
        # instantánea es solo la red de seguridad.
        temp_high = _num(props.get("max_air_temp_pst1hr"))
        temp_low = _num(props.get("min_air_temp_pst1hr"))
        gust = _num(props.get("max_wnd_spd_10m_pst1hr"))  # SWOB ya da km/h
        rain = _num(props.get("pcpn_amt_pst1hr"))
        store.upsert_hourly(
            "ECCC",
            sid,
            day=local_dt.date().isoformat(),
            hour_key=local_dt.strftime("%Y-%m-%dT%H"),
            name=str(meta.get("name") or sid).strip(),
            locality=str(meta.get("region") or "").strip(),
            lat=_num(meta.get("lat")),
            lon=_num(meta.get("lon")),
            values={
                "tmax": temp_high if temp_high is not None else temp,
                "tmin": temp_low if temp_low is not None else temp,
                "gust": gust,
                "rain": max(0.0, rain) if rain is not None else None,
                "rain_at": props["epoch"] if rain is not None else None,
                "tcur": temp,
                "tcur_at": props["epoch"],
            },
        )
    return store.reduce_accumulable_records("ECCC", now=now)


# ----------------------------------------------------------------------
# Adaptador: Meteo-France (Francia, directo; API de Paquets DPPaquetObs)
# ----------------------------------------------------------------------
# `/paquet/stations/horaire?date=H` devuelve TODAS las estaciones francesas
# para UNA hora. Para el día completo se piden las horas de hoy (local Paris)
# en paralelo y se reduce → directo, sin acumular. Campos en Kelvin (t/tx/tn),
# viento en m/s (raf/fxy), lluvia mm (rr1). Nombre desde el catálogo del
# proyecto por `geo_id_insee` (= id_station).
MF_PAQUET_BASE = "https://public-api.meteofrance.fr/public/DPPaquetObs/v2"


async def fetch_meteofrance_records(
    store: "RankingStore",
    api_key: str,
    *,
    client: httpx.AsyncClient,
    now: Optional[datetime] = None,
) -> List[StationDaily]:
    """ACUMULABLE: pide solo las horas de hoy que NO están ya en el store
    (1 llamada por hora nueva → ~24 al día, + backfill al arrancar en frío).
    Cada `/paquet/stations/horaire` trae todas las estaciones de UNA hora.
    Devuelve los registros diarios reducidos (el commit los publica)."""
    import asyncio

    from server.services import meteofrance as mf

    tz = ZoneInfo(PROVIDER_TZ["METEOFRANCE"])
    now_local = (now or datetime.now(tz)).astimezone(tz)
    day = now_local.date().isoformat()
    # El ranking diario sigue reduciendo por fecha local, pero conservamos
    # tambien las horas de las ultimas 24 h para el mapa de precipitacion.
    start_local = (now_local - timedelta(hours=24)).replace(
        minute=0, second=0, microsecond=0,
    )
    # Meteo-France publica normalmente el paquete horario sobre H+10 min.
    # Intentamos la hora actual; si aún no existe, quedará como missing y se
    # reintentará en el siguiente ciclo.
    latest = now_local.replace(minute=0, second=0, microsecond=0)

    # Horas necesarias del día (hour_key UTC → date param UTC).
    needed: Dict[str, Tuple[str, str]] = {}
    h = start_local
    while h <= latest:
        utc = h.astimezone(timezone.utc)
        needed[utc.strftime("%Y-%m-%dT%H")] = (
            utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            h.date().isoformat(),
        )
        h += timedelta(hours=1)

    existing_by_day = {
        local_day: store.accumulated_hours("METEOFRANCE", local_day)
        for _date_str, local_day in needed.values()
    }
    missing = {
        hk: (date_str, local_day)
        for hk, (date_str, local_day) in needed.items()
        if hk not in existing_by_day.get(local_day, set())
    }

    if missing:
        catalog = {
            str(s.get("id_station")): s
            for s in mf._load_stations()
            if isinstance(s, dict) and s.get("id_station")
        }
        headers = {"apikey": api_key, "Accept": "*/*"}
        sem = asyncio.Semaphore(8)

        async def _fetch_hour(hour_key: str, date_str: str, local_day: str):
            async with sem:
                try:
                    resp = await client.get(
                        f"{MF_PAQUET_BASE}/paquet/stations/horaire",
                        params={"date": date_str, "format": "json"},
                        headers=headers, timeout=60.0, follow_redirects=True,
                    )
                    if resp.status_code != 200:
                        return (hour_key, local_day, [])
                    data = resp.json()
                    return (hour_key, local_day, data if isinstance(data, list) else [])
                except Exception as exc:
                    logger.warning("ranking: fallo hora Meteo-France %s (%s)", date_str, type(exc).__name__)
                    return (hour_key, local_day, [])

        results = await asyncio.gather(*[
            _fetch_hour(hk, date_str, local_day)
            for hk, (date_str, local_day) in missing.items()
        ])
        for hour_key, observation_day, hour_data in results:
            for rec in hour_data:
                if not isinstance(rec, dict):
                    continue
                sid = str(rec.get("geo_id_insee", "")).strip()
                if not sid:
                    continue
                gust_ms = _num(rec.get("raf"))
                if gust_ms is None:
                    gust_ms = _num(rec.get("fxy"))
                wind_ms = _num(rec.get("ff"))
                wind_dir = _num(rec.get("dd"))
                observation_epoch = int(
                    datetime.strptime(hour_key, "%Y-%m-%dT%H")
                    .replace(tzinfo=timezone.utc).timestamp()
                )
                station = catalog.get(sid, {})
                store.upsert_hourly(
                    "METEOFRANCE", sid,
                    day=observation_day, hour_key=hour_key,
                    name=str(station.get("name", "") or sid).strip(),
                    locality="",
                    lat=_clean(station.get("lat") if station else rec.get("lat")),
                    lon=_clean(station.get("lon") if station else rec.get("lon")),
                    values={
                        "tmax": _k_to_c(rec.get("tx")),
                        "tmin": _k_to_c(rec.get("tn")),
                        "gust": _ms_to_kmh(gust_ms),
                        "rain": _num(rec.get("rr1")),
                        "rain_at": observation_epoch if _num(rec.get("rr1")) is not None else None,
                        "tcur": _k_to_c(rec.get("t")),
                        # hour_key es UTC ("%Y-%m-%dT%H"): epoch de esa hora.
                        "tcur_at": observation_epoch,
                        "wind": _ms_to_kmh(wind_ms),
                        "wind_dir": wind_dir % 360.0 if wind_dir is not None else None,
                        "wind_at": observation_epoch,
                    },
                )

    return store.reduce_accumulable_records("METEOFRANCE", now=now)


# ----------------------------------------------------------------------
# Adaptador: Frost / met.no (Noruega, directo; solo estaciones oficiales)
# ----------------------------------------------------------------------
# La API exige `sources` explícitos (no hay "todas") y la URL limita a ~150
# por llamada → se batchea en paralelo. Nos quedamos con las ~547 oficiales
# (con `wmo_id`, red SYNOP): evitan el ruido amateur y bajan a ~4 llamadas.
# Cada llamada trae la serie del día → directo, sin acumular.
FROST_BASE = "https://frost.met.no"
FROST_BATCH = 150
FROST_ELEMENTS = (
    "air_temperature,wind_speed,wind_from_direction,"
    "max(wind_speed_of_gust PT1H),sum(precipitation_amount PT1H)"
)


def _frost_official_sources() -> List[str]:
    from data_files import FROST_STATIONS_PATH

    rows = json.load(open(FROST_STATIONS_PATH, encoding="utf-8"))
    rows = rows.get("estaciones", rows) if isinstance(rows, dict) else rows
    return [
        str(r.get("id"))
        for r in rows
        if isinstance(r, dict) and r.get("id") and r.get("wmo_id") not in (None, "", "null")
    ]


async def fetch_frost_daily(
    client_id: str,
    client_secret: str = "",
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 60.0,
) -> List[StationDaily]:
    """Estaciones oficiales noruegas del día (batches de ~150 en paralelo).
    Tmáx/Tmín de `air_temperature`, ráfaga de `max(wind_speed_of_gust PT1H)`
    (m/s→km/h), lluvia = suma de `sum(precipitation_amount PT1H)`."""
    import asyncio

    from server.services import frost

    tz = ZoneInfo(PROVIDER_TZ["FROST"])
    now_local = datetime.now(tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    rolling_start = now_local - timedelta(hours=24)
    ref = (
        f"{rolling_start.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}/"
        f"{now_local.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )
    day_start_epoch = int(start_local.timestamp())
    sources = _frost_official_sources()
    batches = [sources[i:i + FROST_BATCH] for i in range(0, len(sources), FROST_BATCH)]
    auth = (client_id, client_secret or "")

    owns = client is None
    if owns:
        client = httpx.AsyncClient(timeout=timeout_s)
    sem = asyncio.Semaphore(6)

    async def _fetch_batch(batch: List[str]) -> Dict[str, dict]:
        async with sem:
            try:
                resp = await client.get(
                    f"{FROST_BASE}/observations/v0.jsonld",
                    params={"sources": ",".join(batch), "elements": FROST_ELEMENTS, "referencetime": ref},
                    auth=auth, timeout=timeout_s,
                )
                if resp.status_code != 200:
                    return {}
                data = json.loads(resp.text).get("data", [])
                local: Dict[str, dict] = {}
                for item in data if isinstance(data, list) else []:
                    sid = str(item.get("sourceId", "")).split(":")[0]
                    if not sid:
                        continue
                    agg = local.setdefault(
                        sid, {
                            "temps": [], "gusts": [], "rains": [], "rolling_rains": [],
                            "rain_at": -1,
                            "t_last": None, "t_at": -1,
                            "wind_last": None, "wind_dir_last": None, "wind_at": -1,
                        },
                    )
                    try:
                        rt_epoch = int(datetime.fromisoformat(
                            str(item.get("referenceTime") or "").replace("Z", "+00:00")
                        ).timestamp())
                    except ValueError:
                        rt_epoch = -1
                    item_wind = item_direction = None
                    for obs in item.get("observations", []) or []:
                        el = str(obs.get("elementId", ""))
                        v = _num(obs.get("value"))
                        if v is None:
                            continue
                        if el == "air_temperature":
                            if rt_epoch >= day_start_epoch:
                                agg["temps"].append(v)
                            if rt_epoch > agg["t_at"]:
                                agg["t_last"], agg["t_at"] = v, rt_epoch
                        elif el.startswith("max(wind_speed_of_gust"):
                            if rt_epoch >= day_start_epoch:
                                agg["gusts"].append(v)
                        elif el.startswith("sum(precipitation_amount"):
                            agg["rolling_rains"].append(v)
                            agg["rain_at"] = max(agg["rain_at"], rt_epoch)
                            if rt_epoch >= day_start_epoch:
                                agg["rains"].append(v)
                        elif el == "wind_speed":
                            item_wind = v
                        elif el == "wind_from_direction":
                            item_direction = v
                    if (
                        item_wind is not None and item_direction is not None
                        and rt_epoch > agg["wind_at"]
                    ):
                        agg["wind_last"] = item_wind
                        agg["wind_dir_last"] = item_direction
                        agg["wind_at"] = rt_epoch
                return local
            except Exception as exc:
                logger.warning("ranking: fallo batch Frost (%s)", type(exc).__name__)
                return {}

    try:
        results = await asyncio.gather(*[_fetch_batch(b) for b in batches])
    finally:
        if owns:
            await client.aclose()

    merged: Dict[str, dict] = {}
    for local in results:
        for sid, agg in local.items():
            m = merged.setdefault(
                sid, {
                    "temps": [], "gusts": [], "rains": [], "rolling_rains": [],
                    "rain_at": -1,
                    "t_last": None, "t_at": -1,
                    "wind_last": None, "wind_dir_last": None, "wind_at": -1,
                },
            )
            m["temps"].extend(agg["temps"])
            m["gusts"].extend(agg["gusts"])
            m["rains"].extend(agg["rains"])
            m["rolling_rains"].extend(agg["rolling_rains"])
            m["rain_at"] = max(m["rain_at"], agg.get("rain_at", -1))
            if agg.get("t_at", -1) > m["t_at"]:
                m["t_last"], m["t_at"] = agg.get("t_last"), agg.get("t_at", -1)
            if agg.get("wind_at", -1) > m["wind_at"]:
                m["wind_last"] = agg.get("wind_last")
                m["wind_dir_last"] = agg.get("wind_dir_last")
                m["wind_at"] = agg.get("wind_at", -1)

    out: List[StationDaily] = []
    for sid, agg in merged.items():
        lat, lon, _elev, name = frost._station_meta(sid)
        out.append(
            StationDaily(
                provider="FROST",
                station_id=sid,
                name=name or sid,
                locality="",
                lat=lat if lat == lat else None,
                lon=lon if lon == lon else None,
                tmax=round(max(agg["temps"]), 1) if agg["temps"] else None,
                tmin=round(min(agg["temps"]), 1) if agg["temps"] else None,
                gust=_ms_to_kmh(max(agg["gusts"])) if agg["gusts"] else None,
                rain=round(sum(agg["rains"]), 1) if agg["rains"] else None,
                rain_24h=(
                    round(sum(max(0.0, value) for value in agg["rolling_rains"]), 1)
                    if agg["rolling_rains"] else None
                ),
                rain_24h_at=agg["rain_at"] if agg["rain_at"] > 0 else None,
                tcur=round(agg["t_last"], 1) if agg["t_last"] is not None else None,
                tcur_at=agg["t_at"] if agg["t_at"] > 0 else None,
                wind=_ms_to_kmh(agg["wind_last"]),
                wind_dir=(
                    float(agg["wind_dir_last"]) % 360.0
                    if agg["wind_dir_last"] is not None else None
                ),
                wind_at=agg["wind_at"] if agg["wind_at"] > 0 else None,
            )
        )
    return out


# ----------------------------------------------------------------------
# Adaptador: AEMET (acumulable; /todas = 12 h de todas las estaciones)
# ----------------------------------------------------------------------
def _clean(x) -> Optional[float]:
    return None if (x is None or x != x) else float(x)


_TEMPORAL_GUST_MIN_SAMPLES = 6
_TEMPORAL_GUST_SUSPECT_FLOOR_KMH = 120.0
_TEMPORAL_GUST_MIN_DELTA_KMH = 70.0
_TEMPORAL_GUST_MIN_RATIO = 1.65


def _daily_gust_max_from_series(values: List[float]) -> Optional[float]:
    """Máxima diaria de racha con descarte de picos temporales aislados.

    Algunos proveedores publican una racha horaria puntual claramente espuria
    (p. ej. un único 267 km/h rodeado de valores de 60-90 km/h). No aplicamos
    un techo fijo porque puede haber temporales reales: solo retiramos el
    máximo cuando hay suficientes muestras y está muy separado del segundo
    valor más alto. Si hay varias lecturas altas, se conserva.
    """
    valid = sorted(
        float(v)
        for v in values
        if v is not None and v == v and 0.0 <= float(v) <= _WORLD_GUST_RECORD_KMH
    )
    if not valid:
        return None
    if len(valid) < _TEMPORAL_GUST_MIN_SAMPLES:
        return round(max(valid), 1)

    max_v = valid[-1]
    second = valid[-2]
    if (
        max_v >= _TEMPORAL_GUST_SUSPECT_FLOOR_KMH
        and max_v >= second + _TEMPORAL_GUST_MIN_DELTA_KMH
        and max_v >= second * _TEMPORAL_GUST_MIN_RATIO
    ):
        logger.info(
            "ranking: racha máxima aislada descartada %.1f km/h; siguiente %.1f km/h",
            max_v,
            second,
        )
        return round(second, 1)
    return round(max_v, 1)


async def fetch_aemet_records(
    store: "RankingStore",
    api_key: str,
    *,
    client: httpx.AsyncClient,
    now: Optional[datetime] = None,
) -> List[StationDaily]:
    """1 llamada `/todas` (2-step, ~12 h de todas las estaciones). Hace upsert
    por hora en el buffer del store y DEVUELVE los registros diarios reducidos
    (el commit del ciclo los publica)."""
    from server.services import aemet

    records = await aemet._fetch_aemet_two_step(
        "/observacion/convencional/todas", api_key,
        client=client, step1_timeout_s=15.0, step2_timeout_s=60.0,
    )
    if not isinstance(records, list):
        return []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        idema = str(aemet._field(rec, "idema", "IDEMA") or "").strip()
        epoch = aemet._parse_epoch_any(aemet._field(rec, "fint", "FINT"))
        if not idema or epoch is None:
            continue
        local = datetime.fromtimestamp(epoch, tz=aemet.LOCAL_TZ)
        # Tmáx/Tmín desde los EXTREMOS INTRA-HORARIOS `tamax`/`tamin` de
        # /todas (con fallback a la instantánea `ta` si faltan). Los extremos
        # oficiales de AEMET salen de datos 10-minutales: usando solo `ta`
        # (horaria) el ranking se quedaba 0,1-0,9 °C corto siempre que el pico
        # caía entre muestras (Montoro 43,2 oficial vs 42,6; Sevilla/San Pablo
        # 43,2 vs 42,3). Riesgo asumido: `tamax` es crudo sin validar y a
        # veces excede el oficial en ~0,3 (Andújar 45,1 vs 44,8 oficial);
        # los disparates físicos los sigue cortando
        # ``_sanitize_record_extremes`` en el commit.
        ta = _clean(aemet._parse_num(aemet._field(rec, "ta", "TA")))
        tamax = _clean(aemet._parse_num(aemet._field(rec, "tamax", "TAMAX")))
        tamin = _clean(aemet._parse_num(aemet._field(rec, "tamin", "TAMIN")))
        wind_kmh = _clean(aemet._ms_to_kmh(aemet._field(rec, "vv", "VV")))
        wind_dir = _clean(aemet._parse_wind_dir_deg(aemet._field(rec, "dv", "DV")))
        store.upsert_hourly(
            "AEMET", idema,
            day=local.date().isoformat(),
            hour_key=local.strftime("%Y-%m-%dT%H"),
            name=str(aemet._field(rec, "ubi", "UBI") or idema).strip(),
            locality="",
            lat=_clean(aemet._parse_num(aemet._field(rec, "lat", "LAT"))),
            lon=_clean(aemet._parse_num(aemet._field(rec, "lon", "LON"))),
            values={
                "tmax": tamax if tamax is not None else ta,
                "tmin": tamin if tamin is not None else ta,
                "gust": _clean(aemet._ms_to_kmh(aemet._field(rec, "vmax", "VMAX"))),
                "rain": _clean(aemet._parse_num(aemet._field(rec, "prec", "PREC"))),
                "rain_at": epoch,
                "tcur": ta,
                "tcur_at": epoch,
                "wind": wind_kmh,
                "wind_dir": wind_dir % 360.0 if wind_dir is not None else None,
                "wind_at": epoch,
            },
        )
    return store.reduce_accumulable_records("AEMET", now=now)


# ----------------------------------------------------------------------
# Adaptador: IEM (Iowa Environmental Mesonet) — directo, multi-país
# ----------------------------------------------------------------------
# IEM agrega ASOS/METAR de todo el mundo. ``currents.json?network=XXX`` ya
# devuelve, por estación, los AGREGADOS DEL DÍA LOCAL (max_tmpf/min_tmpf/
# max_gust/pday), así que NO hay que acumular snapshots (como MeteoGalicia).
# Una llamada por red; ~260 redes online de países sin proveedor dedicado.
IEM_CURRENTS_ENDPOINT = "https://mesonet.agron.iastate.edu/api/1/currents.json"
_IEM_CONCURRENCY = 24


def _f_to_c_num(value) -> Optional[float]:
    v = _num(value)
    return round((v - 32.0) * 5.0 / 9.0, 1) if v is not None else None


def _knots_to_kmh_num(value) -> Optional[float]:
    v = _num(value)
    return round(v * 1.852, 1) if v is not None else None


def _inch_to_mm_num(value) -> Optional[float]:
    v = _num(value)
    return round(max(0.0, v * 25.4), 1) if v is not None else None


# Las redes DCP (Data Collection Platform) agregan gauges hidrológicos
# (embalse/SNOTEL/río) cuyo campo `pday` NO es lluvia diaria limpia: da valores
# imposibles (p.ej. PAONIA RSVR 1159 mm, OVANDO 753 mm). Se mantienen para
# TEMPERATURA/VIENTO —incluyen Furnace Creek / Death Valley— pero su LLUVIA no se
# publica. Toda la lluvia basura medida estaba en DCP; NW y ciudades conservan su
# lluvia vía los ASOS de aeropuerto.
_IEM_NO_RAIN_NETWORK_TOKEN = "DCP"

# Algunos sensores IEM cuelan en `currents.json` un extremo espurio (un pico que
# la serie real no tiene): Genoa Canyon 60°C, PIUTES 59,4°C (real ~20°C),
# Camboya −40°C (tropical), Waco ráfaga 468,6 km/h (real 22). NO es el filtro
# espacial (ese mataba récords reales); esto es solo IMPOSIBILIDAD FÍSICA, que
# los extremos reales (Death Valley 43°C, Antártida −68°C…) pasan de sobra.
_WORLD_TMAX_RECORD_C = 59.0   # récord mundial 56,7°C + margen (clima cambiante)
# Techo de máxima por latitud (imposibilidad climatológica, espejo de
# ``_tmin_floor``). El récord mundial (56,7°C, Death Valley) es SUBTROPICAL: en el
# trópico profundo (|lat|<25) no se pasa de ~48-50°C ni en los márgenes saharianos
# (Bilma, Faya Largeau ~44-45°C reales). Así una estación tropical con serie del
# día rota que reporta 54-60°C (Koh Kong, Siem Reap) cae, y el calor REAL del
# Sahel y de Death Valley se conserva.
_TMAX_CEIL_TROPICAL_C = 50.0
_MAX_DIURNAL_RANGE_C = 40.0   # salto diurno máx−mín imposible (real ≲30°C)
_WORLD_GUST_RECORD_KMH = 420.0  # récord mundial 408 km/h (Barrow I.) + margen
# El lado FRÍO no se filtra: el suelo por latitud (``_tmin_floor``) anulaba
# frío extremo REAL (IEM ya recorta a ~−73°C en BUFR y las bases antárticas de
# meseta bajan de −80). Un sensor roto en frío es mucho más raro que en calor
# y el coste de tragar un pico frío es menor que perder un récord real.
_TROPICAL_LAT = 25.0


def _tmax_ceiling(lat: Optional[float]) -> float:
    """Techo de máxima plausible según latitud: estricto en el trópico
    (|lat|<25 → 50°C, las máximas reales no pasan de ~45-48°C ni en el Sahel),
    récord mundial fuera (Death Valley 56,7°C es subtropical). Sin latitud, el
    récord mundial (no se filtra por falta de contexto)."""
    if lat is None:
        return _WORLD_TMAX_RECORD_C
    return _TMAX_CEIL_TROPICAL_C if abs(lat) < _TROPICAL_LAT else _WORLD_TMAX_RECORD_C


def _clean_iem_extremes(
    tmax: Optional[float],
    tmin: Optional[float],
    gust: Optional[float],
    lat: Optional[float],
    tcur: Optional[float],
) -> tuple:
    """Anula extremos físicamente imposibles (sensores rotos de IEM) sin tocar
    extremos reales. Devuelve ``(tmax, tmin, gust)`` saneados."""
    # 0) Sin temperatura ACTUAL → la estación no reporta de verdad; su máx/mín es
    #    un valor basura colgado. Las reales (Furnace Creek incluido) sí dan
    #    temperatura actual.
    if tcur is None:
        tmax = tmin = None
    else:
        # 1) La mínima NO se filtra por frío (ver nota junto a _TROPICAL_LAT):
        #    el suelo por latitud y la incoherencia con la actual anulaban
        #    récords de frío reales (bases antárticas).
        # 2) Máxima imposible: por encima del techo por latitud (trópico ≤50°C,
        #    récord mundial fuera), o un rango diurno imposible respecto a la
        #    mínima (pico de la máxima). El techo tropical pilla la
        #    estación de serie rota (Koh Kong 54°C a lat 11) que el récord
        #    mundial dejaba pasar.
        if tmax is not None:
            if tmax > _tmax_ceiling(lat):
                tmax = None
            elif tmin is not None and (tmax - tmin) > _MAX_DIURNAL_RANGE_C:
                tmax = None
    # 3) Ráfaga por encima del récord mundial = anemómetro roto (Waco 468 km/h).
    if gust is not None and gust > _WORLD_GUST_RECORD_KMH:
        gust = None
    return tmax, tmin, gust


_WORLD_RAIN_RECORD_MM = 1900.0  # récord 24h ~1825mm (Foc-Foc, Reunión) + margen


def _sanitize_record_extremes(rec: StationDaily) -> None:
    """Anula valores físicamente imposibles en CUALQUIER proveedor (no solo IEM):
    sensores rotos como Patti SIAS (MeteoHub) 112,5°C. Solo IMPOSIBILIDAD FÍSICA
    (récords mundiales / rango / suelo por latitud), nunca comparación espacial
    con vecinas (eso mataba récords reales). Muta el registro in situ.

    Los extras de IEM (sin-actual) ya se aplican en `_parse_iem_network`; esto
    es la red de seguridad común a todos. La mínima no se filtra por frío (los
    suelos por latitud anulaban récords de frío reales)."""
    if rec.tmax is not None:
        if rec.tmax > _tmax_ceiling(rec.lat):
            rec.tmax = None
        elif rec.tmin is not None and (rec.tmax - rec.tmin) > _MAX_DIURNAL_RANGE_C:
            rec.tmax = None
    if rec.gust is not None and rec.gust > _WORLD_GUST_RECORD_KMH:
        rec.gust = None
    if rec.rain is not None and rec.rain > _WORLD_RAIN_RECORD_MM:
        rec.rain = None


def _parse_iem_network(
    network: str,
    rows: List[dict],
    station_countries: Dict[str, str],
) -> List[StationDaily]:
    network = str(network).strip()
    drop_rain = _IEM_NO_RAIN_NETWORK_TOKEN in network
    out: List[StationDaily] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        station = str(row.get("station", "")).strip()
        if not station:
            continue
        station_id = f"{network}|{station}"
        # Base con fuente dedicada mejor (Climantartide) → fuera del ranking IEM.
        if station_id in _IEM_SUPERSEDED_BY_CLIMANTARTIDE:
            continue

        # País REAL por coordenadas (precalculado en el catálogo). Si la
        # estación no resuelve o cae en un país ya cubierto, se descarta (no
        # duplica ni coloca nada en el país equivocado).
        rec_country = station_countries.get(station_id, "")
        if not rec_country:
            continue

        local_valid = str(row.get("local_valid") or "")
        tcur = _f_to_c_num(row.get("tmpf"))
        wind = _knots_to_kmh_num(row.get("sknt"))
        wind_dir = _num(row.get("drct"))
        tmax, tmin, gust = _clean_iem_extremes(
            _f_to_c_num(row.get("max_tmpf")),
            _f_to_c_num(row.get("min_tmpf")),
            _knots_to_kmh_num(row.get("max_gust")),
            _num(row.get("lat")),
            tcur,
        )
        # Plausibilidad física de la instantánea (sensores rotos de IEM).
        # Solo techo de calor; el lado frío no se filtra (récords antárticos).
        if tcur is not None and tcur > _WORLD_TMAX_RECORD_C:
            tcur = None
        tcur_at = None
        try:
            tcur_at = int(datetime.fromisoformat(
                str(row.get("utc_valid") or "").replace("Z", "+00:00")
            ).timestamp())
        except ValueError:
            pass
        rec = StationDaily(
            provider="IEM",
            # id interno = network|station (igual que el resto del backend IEM).
            station_id=station_id,
            name=str(row.get("name") or station).strip(),
            locality=str(row.get("state") or "").strip(),
            lat=_num(row.get("lat")),
            lon=_num(row.get("lon")),
            tmax=tmax,
            tmin=tmin,
            gust=gust,
            rain=None if drop_rain else _inch_to_mm_num(row.get("pday")),
            tcur=tcur,
            tcur_at=tcur_at,
            wind=wind,
            wind_dir=wind_dir % 360.0 if wind_dir is not None else None,
            wind_at=tcur_at,
            country=rec_country,
            # Día local de la estación (clave de bucket) y hora local (display).
            local_date=str(row.get("local_date") or "").strip(),
            local_time=local_valid[11:16] if len(local_valid) >= 16 else "",
        )
        if _station_has_data(rec):
            out.append(rec)
    return out


async def fetch_iem_daily(
    *,
    store: Optional["RankingStore"] = None,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 25.0,
) -> List[StationDaily]:
    """TODAS las estaciones IEM aptas para ranking (1 llamada por red, en
    paralelo). Excluye países con proveedor dedicado (US/ES/FR/NO/IT) para no
    duplicar. Devuelve agregados diarios listos (sin acumular)."""
    import asyncio
    from server.services import stations as stations_svc

    networks = stations_svc.iem_ranking_networks()
    if not networks:
        return []
    # País real por coordenadas (point-in-polygon) de cada estación, precalculado.
    station_countries = stations_svc.iem_station_countries()

    owns = client is None
    if owns:
        client = httpx.AsyncClient(timeout=timeout_s)
    sem = asyncio.Semaphore(_IEM_CONCURRENCY)

    async def _one(network: str) -> List[StationDaily]:
        async with sem:
            try:
                resp = await client.get(
                    IEM_CURRENTS_ENDPOINT, params={"network": network}
                )
                resp.raise_for_status()
                rows = resp.json().get("data") or []
            except (httpx.HTTPError, ValueError) as exc:
                logger.info("ranking IEM: red %s falló (%s)", network, type(exc).__name__)
                return []
            if store is not None and _IEM_NO_RAIN_NETWORK_TOKEN not in network:
                for row in rows:
                    station = str(row.get("station") or "").strip()
                    station_id = f"{network}|{station}"
                    if not station or not station_countries.get(station_id):
                        continue
                    rain_hour = _inch_to_mm_num(row.get("phour"))
                    try:
                        observed_at = int(datetime.fromisoformat(
                            str(row.get("utc_valid") or "").replace("Z", "+00:00")
                        ).timestamp())
                    except ValueError:
                        continue
                    local_valid = str(row.get("local_valid") or "")
                    local_day = str(row.get("local_date") or "").strip()
                    if rain_hour is None or not local_day:
                        continue
                    store.upsert_hourly(
                        "IEM", station_id,
                        day=local_day,
                        hour_key=(local_valid[:13] if len(local_valid) >= 13 else str(observed_at // 3600)),
                        name=str(row.get("name") or station).strip(),
                        locality=str(row.get("state") or "").strip(),
                        lat=_num(row.get("lat")),
                        lon=_num(row.get("lon")),
                        values={"rain": rain_hour, "rain_at": observed_at},
                    )
            return _parse_iem_network(network, rows, station_countries)

    try:
        chunks = await asyncio.gather(*[_one(net) for net in networks])
    finally:
        if owns:
            await client.aclose()

    records: List[StationDaily] = []
    for chunk in chunks:
        records.extend(chunk)
    if store is not None:
        rolling = store.rolling_rain_24h_by_station("IEM")
        for record in records:
            held = rolling.get(record.station_id)
            if held is not None:
                record.rain_24h, record.rain_24h_at = held
    return records


# ----------------------------------------------------------------------
# Climantartide (ENEA) — AWS italianas de la Antártida
# ----------------------------------------------------------------------
# El observatorio meteo-climatológico antártico italiano (climantartide.it)
# publica en tiempo real la temperatura HORARIA de sus AWS (Concordia
# incluida) vía un JSONP sin auth. Sustituye a IEM para estas bases: el
# decodificador BUFR de IEM anula las temperaturas por debajo de ~−73,15°C
# (suelo de 200 K), con lo que el frío extremo real de la meseta (Concordia
# −84°C) llegaba en null. Solo temperatura; sin viento/presión/lluvia.
CLIMANTARTIDE_REALTIME_ENDPOINT = "https://www.climantartide.it/realtime/graph/jsonp.php"
# Nombre en el feed → (nombre para mostrar, paraje, lat, lon). Coordenadas de
# las fichas de https://www.climantartide.it/strumenti/aws/.
_CLIMANTARTIDE_STATIONS: Dict[str, Tuple[str, str, float, float]] = {
    "Alessandra": ("Alessandra", "Cape King", -73.5861, 166.6211),
    "Arelis": ("Arelis", "Cape Ross", -76.7150, 162.9700),
    "Concordia": ("Concordia", "Dome C", -75.1000, 123.4000),
    "Eneide": ("Eneide", "Terra Nova Bay", -74.6958, 164.0922),
    "Giulia": ("Giulia", "Mid Point", -75.5361, 145.8589),
    "Irene": ("Irene", "Sitry", -71.6525, 148.6556),
    "Lola": ("Lola", "Tourmaline Plateau", -74.1350, 163.4306),
    "Lucia": ("Lucia", "Larsen Glacier", -74.9506, 161.7719),
    "Modesta": ("Modesta", "Priestley Névé", -73.6392, 160.6456),
    "Paola": ("Paola", "Talos Dome", -72.8292, 159.1933),
    "Rita": ("Rita", "Enigma Lake", -74.7250, 164.0331),
    "Silvia": ("Silvia", "Cape Phillips", -73.0500, 169.6000),
    "Sofiab": ("Sofiab", "David Glacier", -75.6117, 158.5906),
    "Zoraida": ("Zoraida", "Priestley Glacier", -74.1600, 162.7392),
}
# Estaciones IEM redundantes con Climantartide (mismo emplazamiento): se
# excluyen del ranking IEM para no duplicar el marcador. Solo Concordia
# aparece en ambos catálogos.
_IEM_SUPERSEDED_BY_CLIMANTARTIDE = {"WMO_BUFR_SRF|0-380-0-625"}


def _climantartide_local_day(epoch_s: int, lon: float) -> str:
    """Día local NOMINAL por longitud (huso solar, lon/15). Las bases usan
    husos administrativos dispares (Concordia UTC+8, Terra Nova Bay hora NZ);
    el huso solar aproxima el día real de la mínima sin una tabla por base."""
    offset = timedelta(hours=round(lon / 15.0))
    return (datetime.fromtimestamp(epoch_s, tz=timezone.utc) + offset).date().isoformat()


async def fetch_climantartide_daily(
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout_s: float = 30.0,
) -> List[StationDaily]:
    """Serie horaria de temperatura (última semana) de todas las AWS → un
    registro por estación y día local presente en el feed. El día actual
    trae además la instantánea (``tcur``/``tcur_at``) para el mapa."""
    owns = client is None
    if owns:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        resp = await client.get(
            CLIMANTARTIDE_REALTIME_ENDPOINT, params={"caso": "real_allt"}
        )
        resp.raise_for_status()
        raw = resp.text.strip()
    finally:
        if owns:
            await client.aclose()

    # El endpoint es JSONP: ``({...});`` incluso sin ``callback``.
    start, end = raw.find("("), raw.rfind(")")
    if start < 0 or end <= start:
        raise ValueError("Climantartide: respuesta no JSONP")
    payload = json.loads(raw[start + 1:end])
    series = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(series, list):
        raise ValueError("Climantartide: payload sin 'data'")

    records: List[StationDaily] = []
    for entry in series:
        if not isinstance(entry, dict):
            continue
        meta = _CLIMANTARTIDE_STATIONS.get(str(entry.get("name") or "").strip())
        if meta is None:
            continue
        name, locality, lat, lon = meta
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
        if not points:
            continue
        points.sort()
        by_day: Dict[str, List[Tuple[int, float]]] = {}
        for epoch_s, value in points:
            by_day.setdefault(_climantartide_local_day(epoch_s, lon), []).append((epoch_s, value))
        last_day = max(by_day)
        last_epoch, last_value = points[-1]
        for day, day_points in by_day.items():
            temps = [value for _, value in day_points]
            is_last = day == last_day
            records.append(StationDaily(
                provider="CLIMANTARTIDE",
                # id = nombre del feed, igual que el station_id del catálogo.
                station_id=name,
                name=f"{name} ({locality})" if locality else name,
                locality=locality,
                lat=lat,
                lon=lon,
                tmax=max(temps),
                tmin=min(temps),
                tcur=last_value if is_last else None,
                tcur_at=last_epoch if is_last else None,
                country="AQ",
                local_date=day,
                local_time=(
                    (datetime.fromtimestamp(last_epoch, tz=timezone.utc)
                     + timedelta(hours=round(lon / 15.0))).strftime("%H:%M")
                    if is_last else ""
                ),
            ))
    return records


# ----------------------------------------------------------------------
# Store en memoria
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# Saneamiento de datos (redes heterogéneas → sensores rotos)
# ----------------------------------------------------------------------
# Chequeo de coherencia espacial ONE-SIDED: solo en la dirección
# "anómalamente alto" (Tmáx/ráfaga), donde un outlier es casi siempre un
# sensor roto y la altitud no genera falsos positivos (la altura enfría,
# no calienta). Tmín y lluvia NO se filtran espacialmente: una cima fría o
# una tormenta local son reales. Calderari (45.9 °C entre vecinas a ~32)
# se descarta; un récord real (rodeado de valores altos) sobrevive.
_SPATIAL_DELTA = {"tmax": 8.0, "gust": 90.0}
_SPATIAL_RADIUS_KM = 60.0
_SPATIAL_MIN_NEIGHBORS = 3


def _grid_key(lat: float, lon: float, size: float = 0.5):
    return (int(lat // size), int(lon // size))


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia en km. Inline (no `utils.geo`) para que el backend del job
    NO importe el paquete `utils`, que arrastra Streamlit y ensucia el log."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


_DAY_FLOOR = 15  # nº mínimo de estaciones para dar un día por "vivo"


def _pick_best_day(day_records: Dict[str, List[StationDaily]]) -> List[StationDaily]:
    """De {día: registros}, elige el día MÁS RECIENTE que tenga al menos
    ``_DAY_FLOOR`` estaciones con dato. Umbral ABSOLUTO (no relativo al mejor
    día): así el día en curso gana en cuanto tiene datos reales y solo se
    cae a ayer de madrugada (cuando hoy aún está casi vacío)."""
    counts = {d: sum(1 for r in recs if _station_has_data(r)) for d, recs in day_records.items()}
    if not counts or max(counts.values()) == 0:
        return []

    def _stamp(day: str) -> List[StationDaily]:
        # Marca la fecha REAL elegida en cada registro para que el store lo meta
        # en el bucket correcto (de madrugada el dato es de AYER, no de hoy).
        recs = day_records[day]
        for r in recs:
            r.local_date = day
        return recs

    for day in sorted(day_records, reverse=True):  # más reciente primero
        if counts.get(day, 0) >= _DAY_FLOOR:
            return _stamp(day)
    return _stamp(max(counts, key=counts.get))


@dataclass
class RankingStore:
    """Agregados diarios por (proveedor, día local) → {station_id: StationDaily}.

    Directos → ``replace_daily`` (sobrescribe). Acumulables (AEMET,
    Meteo-France) → ``upsert_hourly`` por hora + ``reduce_accumulable``."""

    _daily: Dict[Tuple[str, str], Dict[str, StationDaily]] = field(default_factory=dict)
    # (proveedor, día) → {station_id: {"meta": {...}, "hours": {hora: {metric: val}}}}
    _hourly: Dict[Tuple[str, str], Dict[str, dict]] = field(default_factory=dict)
    # Marca de tiempo (UTC) del último ciclo de refresco completado.
    updated_at: Optional[datetime] = None

    def mark_refreshed(self) -> None:
        self.updated_at = datetime.now(tz=timezone.utc)

    # ------------------------------------------------------------------
    # Persistencia en disco (Railway Volume). El store es memoria pura: sin
    # snapshot, cada redeploy pierde los días anteriores del selector y las
    # horas acumuladas de AEMET/Meteo-France. Se guarda tras cada ciclo del
    # refresh_loop y se restaura en el lifespan al arrancar.
    _STATE_VERSION = 1

    def save_to_disk(self, path: str) -> None:
        """Vuelca el estado a ``path`` (gzip JSON). Escritura atómica: fichero
        temporal + ``os.replace``, para que un reinicio a mitad de escritura
        no deje un snapshot corrupto. Es I/O síncrona: llamar con
        ``asyncio.to_thread`` desde el event loop."""
        import gzip
        import os
        from dataclasses import asdict

        payload = {
            "version": self._STATE_VERSION,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            # Las claves tupla (proveedor, día) no son serializables en JSON:
            # se aplanan a listas [proveedor, día, estaciones].
            "daily": [
                [provider, day, {sid: asdict(rec) for sid, rec in stations.items()}]
                for (provider, day), stations in self._daily.items()
            ],
            "hourly": [
                [provider, day, stations]
                for (provider, day), stations in self._hourly.items()
            ],
        }
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp_path = f"{path}.tmp"
        with gzip.open(tmp_path, "wt", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        os.replace(tmp_path, path)

    def load_from_disk(self, path: str) -> bool:
        """Restaura el estado desde ``path``. Devuelve True si cargó algo.
        Cualquier problema (fichero ausente, corrupto, versión desconocida)
        deja el store intacto y devuelve False: el ranking arranca vacío,
        exactamente como antes de existir la persistencia."""
        import gzip
        import os
        from dataclasses import fields as dc_fields

        if not path or not os.path.isfile(path):
            return False
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                payload = json.load(fh)
            if payload.get("version") != self._STATE_VERSION:
                logger.warning(
                    "ranking: snapshot con versión desconocida (%s) en %s; se ignora",
                    payload.get("version"), path,
                )
                return False
            # Solo campos conocidos de StationDaily: un snapshot escrito por
            # una versión más nueva del código no revienta la carga.
            known = {f.name for f in dc_fields(StationDaily)}
            daily: Dict[Tuple[str, str], Dict[str, StationDaily]] = {}
            for provider, day, stations in payload.get("daily", []):
                daily[(str(provider), str(day))] = {
                    str(sid): StationDaily(**{k: v for k, v in rec.items() if k in known})
                    for sid, rec in stations.items()
                }
            hourly: Dict[Tuple[str, str], Dict[str, dict]] = {
                (str(provider), str(day)): stations
                for provider, day, stations in payload.get("hourly", [])
            }
        except Exception:
            logger.warning("ranking: snapshot ilegible en %s; se ignora", path, exc_info=True)
            return False

        self._daily = daily
        self._hourly = hourly
        raw_updated = payload.get("updated_at")
        try:
            self.updated_at = datetime.fromisoformat(raw_updated) if raw_updated else None
        except (TypeError, ValueError):
            self.updated_at = None
        for provider in {key[0] for key in self._daily}:
            self._prune_days(provider)
        logger.info(
            "ranking: snapshot restaurado de %s · %d buckets diarios, %d acumulables",
            path, len(self._daily), len(self._hourly),
        )
        return True

    @staticmethod
    def local_day(provider: str, now: Optional[datetime] = None) -> str:
        tz = ZoneInfo(PROVIDER_TZ.get(provider, "UTC"))
        ref = (now or datetime.now(tz=tz)).astimezone(tz)
        return ref.date().isoformat()

    @staticmethod
    def _stamp_country(provider: str, records: List[StationDaily]) -> None:
        """Rellena ``country`` desde el país fijo del proveedor cuando el
        registro no lo trae (IEM ya lo trae por estación)."""
        fixed = PROVIDER_FIXED_COUNTRY.get(provider)
        if not fixed:
            return
        for r in records:
            if not r.country:
                r.country = fixed

    # Nº de fechas locales que se conservan por proveedor. Una fecha está "viva
    # en algún punto del planeta" ~50h (hasta 3 fechas coexisten); 4 da margen
    # para mirar el día anterior ya cerrado.
    _KEEP_DAYS = 4

    def _bucket_day(self, provider: str, record: StationDaily, fallback_day: str) -> str:
        return record.local_date or fallback_day

    def replace_daily(self, provider: str, records: List[StationDaily], *, now=None) -> None:
        """Publica registros agrupándolos por su FECHA LOCAL. SIN filtros de
        saneamiento (anulaban récords reales como Death Valley)."""
        from collections import defaultdict

        self._stamp_country(provider, records)
        fallback_day = self.local_day(provider, now)
        by_day: Dict[str, Dict[str, StationDaily]] = defaultdict(dict)
        for r in records:
            _sanitize_record_extremes(r)
            day = self._bucket_day(provider, r, fallback_day)
            r.local_date = day
            by_day[day][r.station_id] = r
        for day, recs in by_day.items():
            self._daily[(provider, day)] = recs
        self._prune_days(provider)

    def _prune_days(self, provider: str) -> None:
        """Conserva solo las ``_KEEP_DAYS`` fechas más recientes del proveedor."""
        days = sorted({k[1] for k in self._daily if k[0] == provider}, reverse=True)
        for day in days[self._KEEP_DAYS:]:
            self._daily.pop((provider, day), None)

    # --- Acumulables (snapshots horarios → upsert idempotente por hora) ---
    def upsert_hourly(
        self,
        provider: str,
        station_id: str,
        *,
        day: str,
        hour_key: str,
        name: str,
        locality: str,
        lat: Optional[float],
        lon: Optional[float],
        values: Dict[str, Optional[float]],
    ) -> None:
        bucket = self._hourly.setdefault((provider, day), {})
        st = bucket.setdefault(station_id, {"meta": {}, "hours": {}})
        st["meta"] = {"name": name, "locality": locality, "lat": lat, "lon": lon}
        # Upsert por hora: re-poll de la misma hora la sobrescribe (la suma de
        # lluvia no se duplica al solaparse ventanas).
        # Distintos bulks del mismo proveedor pueden completar una misma hora
        # por separado (p. ej. SMHI: lluvia latest-day + viento latest-hour).
        # Mezclar evita que el segundo borre la lluvia ya almacenada.
        st["hours"].setdefault(hour_key, {}).update(values)

    def accumulated_hours(self, provider: str, day: str) -> set:
        """Horas ya almacenadas para (proveedor, día) — para pedir solo las
        que faltan (Meteo-France acumula 1 llamada por hora nueva)."""
        hours: set = set()
        for st in self._hourly.get((provider, day), {}).values():
            hours.update(st.get("hours", {}).keys())
        return hours

    def rolling_rain_24h_by_station(
        self, provider: str, *, now: Optional[datetime] = None,
    ) -> Dict[str, Tuple[float, int]]:
        """Suma muestras horarias de [ahora-24 h, ahora] con cobertura real.

        Se exige un arco de al menos 20 h entre la primera y la ultima muestra
        para no presentar un arranque parcial como si fueran 24 horas.
        """
        now_utc = (now or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)
        days = sorted(
            {day for hour_provider, day in self._hourly if hour_provider == provider},
            reverse=True,
        )
        for old_day in days[self._KEEP_DAYS:]:
            self._hourly.pop((provider, old_day), None)
        cutoff = int(now_utc.timestamp()) - 24 * 3600
        totals: Dict[str, Tuple[float, int]] = {}
        spans: Dict[str, Tuple[int, int]] = {}
        for (hour_provider, _hour_day), station_rows in self._hourly.items():
            if hour_provider != provider:
                continue
            for sid, station_row in station_rows.items():
                for values in (station_row.get("hours") or {}).values():
                    value = _num(values.get("rain"))
                    try:
                        observed_at = int(values.get("rain_at"))
                    except (TypeError, ValueError):
                        continue
                    if value is None or not (
                        cutoff <= observed_at <= int(now_utc.timestamp()) + 3600
                    ):
                        continue
                    total, latest = totals.get(sid, (0.0, 0))
                    totals[sid] = (total + max(0.0, value), max(latest, observed_at))
                    earliest, span_latest = spans.get(sid, (observed_at, observed_at))
                    spans[sid] = (min(earliest, observed_at), max(span_latest, observed_at))
        return {
            sid: (round(total, 1), latest)
            for sid, (total, latest) in totals.items()
            if sid in spans and spans[sid][1] - spans[sid][0] >= 20 * 3600
        }

    def reduce_accumulable_records(
        self, provider: str, *, now: Optional[datetime] = None
    ) -> List[StationDaily]:
        """Reduce las horas acumuladas a registros diarios y los DEVUELVE (sin
        publicarlos; el commit del ciclo los escribe). Mantiene hoy+ayer; de
        madrugada (hoy aún flojo) sirve ayer, como los directos."""
        now_utc = (now or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)
        today = self.local_day(provider, now_utc)
        yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
        keep = {today, yesterday}
        for k in [k for k in self._hourly if k[0] == provider and k[1] not in keep]:
            self._hourly.pop(k, None)

        # Ventana movil independiente de los dias civiles. Cada muestra
        # horaria guarda ``rain_at`` en epoch UTC, por lo que funciona tambien
        # cuando la ventana cruza medianoche o un cambio horario.
        rolling_rain = self.rolling_rain_24h_by_station(provider, now=now_utc)

        day_records: Dict[str, List[StationDaily]] = {}
        for day in (today, yesterday):
            recs: List[StationDaily] = []
            for sid, st in self._hourly.get((provider, day), {}).items():
                hours = list(st["hours"].values())
                txs = [h["tmax"] for h in hours if h.get("tmax") is not None]
                tns = [h["tmin"] for h in hours if h.get("tmin") is not None]
                gus = [h["gust"] for h in hours if h.get("gust") is not None]
                rns = [h["rain"] for h in hours if h.get("rain") is not None]
                # Instantánea de la HORA MÁS RECIENTE que la traiga (para el
                # mapa de temperaturas; no participa en los extremos).
                tcs = [
                    (st["hours"][hour_key].get("tcur"), st["hours"][hour_key].get("tcur_at"))
                    for hour_key in sorted(st["hours"])
                    if st["hours"][hour_key].get("tcur") is not None
                ]
                winds = [
                    (
                        st["hours"][hour_key].get("wind"),
                        st["hours"][hour_key].get("wind_dir"),
                        st["hours"][hour_key].get("wind_at"),
                    )
                    for hour_key in sorted(st["hours"])
                    if (
                        st["hours"][hour_key].get("wind") is not None
                        and st["hours"][hour_key].get("wind_dir") is not None
                    )
                ]
                meta = st["meta"]
                rain_24h = rolling_rain.get(sid)
                recs.append(
                    StationDaily(
                        provider=provider,
                        station_id=sid,
                        name=meta.get("name") or sid,
                        locality=meta.get("locality") or "",
                        lat=meta.get("lat"),
                        lon=meta.get("lon"),
                        tmax=round(max(txs), 1) if txs else None,
                        tmin=round(min(tns), 1) if tns else None,
                        gust=_daily_gust_max_from_series(gus) if gus else None,
                        rain=round(sum(rns), 1) if rns else None,
                        rain_24h=(
                            round(rain_24h[0], 1)
                            if rain_24h is not None else None
                        ),
                        rain_24h_at=(
                            rain_24h[1]
                            if rain_24h is not None else None
                        ),
                        tcur=round(tcs[-1][0], 1) if tcs else None,
                        tcur_at=tcs[-1][1] if tcs else None,
                        wind=round(winds[-1][0], 1) if winds else None,
                        wind_dir=float(winds[-1][1]) % 360.0 if winds else None,
                        wind_at=winds[-1][2] if winds else None,
                    )
                )
            day_records[day] = recs
        return _pick_best_day(day_records)

    def current_temperature_points(
        self, *, max_age_s: int = 7200, now: Optional[datetime] = None,
    ) -> List[Tuple[float, float, float]]:
        """``(lat, lon, tcur)`` de las estaciones con instantánea RECIENTE
        (≤ ``max_age_s``), del día más reciente de cada proveedor. Una lectura
        colgada de hace horas pintaría frío nocturno a mediodía, así que las
        viejas (o sin timestamp) se descartan. El margen de 2 h absorbe la
        cadena real de retrasos —obs publicada con 30-90 min de demora
        (AEMET/SYNOP) más el ciclo horario del refresh del ranking—; con 1 h
        el mapa se vaciaba hacia el final de cada ciclo. Alimenta el campo de
        temperatura del mapa; no interviene en el ranking."""
        return [
            (float(rec.lat), float(rec.lon), float(rec.tcur))
            for rec in self.current_temperature_records(max_age_s=max_age_s, now=now)
        ]

    def current_temperature_records(
        self, *, max_age_s: int = 7200, now: Optional[datetime] = None,
    ) -> List[StationDaily]:
        """Registros con instantánea RECIENTE (≤ ``max_age_s``). Se miran
        TODOS los buckets de día, no solo el más reciente: IEM agrupa por
        fecha local de cada estación, así que al atardecer UTC conviven
        "hoy" (Asia/Oceanía, ya en el día siguiente) y "ayer" (América/
        Europa); quedarse solo con el día más nuevo vaciaba medio planeta.
        La frescura real la garantiza el cutoff sobre ``tcur_at``; si una
        estación aparece en dos buckets gana su lectura más reciente."""
        from server.services.stations import is_station_hidden

        cutoff = int((now or datetime.now(tz=timezone.utc)).timestamp()) - max(60, int(max_age_s))
        freshest: Dict[Tuple[str, str], StationDaily] = {}
        for (provider, _day), stations in self._daily.items():
            for sid, rec in stations.items():
                if is_station_hidden(provider, sid):
                    continue
                if rec.tcur is None or rec.lat is None or rec.lon is None:
                    continue
                if rec.tcur_at is None or int(rec.tcur_at) < cutoff:
                    continue
                key = (provider, sid)
                held = freshest.get(key)
                if held is None or int(rec.tcur_at) > int(held.tcur_at or 0):
                    freshest[key] = rec
        return list(freshest.values())

    def current_wind_points(
        self, *, max_age_s: int = 7200, now: Optional[datetime] = None,
    ) -> List[Tuple[float, float, float]]:
        """``(lat, lon, km/h)`` de estaciones con vector de viento reciente."""
        return [
            (float(rec.lat), float(rec.lon), float(rec.wind))
            for rec in self.current_wind_records(max_age_s=max_age_s, now=now)
        ]

    def current_wind_records(
        self, *, max_age_s: int = 7200, now: Optional[datetime] = None,
    ) -> List[StationDaily]:
        """Último vector válido por estación, sin duplicados ocultos.

        ``wind_dir`` es la procedencia meteorológica en grados; el frontend
        gira la flecha 180° para mostrar hacia dónde se desplaza el aire.
        """
        from server.services.stations import is_station_hidden

        cutoff = int((now or datetime.now(tz=timezone.utc)).timestamp()) - max(
            60, int(max_age_s),
        )
        freshest: Dict[Tuple[str, str], StationDaily] = {}
        for (provider, _day), station_rows in self._daily.items():
            for sid, rec in station_rows.items():
                if is_station_hidden(provider, sid):
                    continue
                if rec.lat is None or rec.lon is None:
                    continue
                if rec.wind is None or rec.wind_dir is None or rec.wind_at is None:
                    continue
                try:
                    speed = float(rec.wind)
                    direction = float(rec.wind_dir)
                    observed_at = int(rec.wind_at)
                except (TypeError, ValueError):
                    continue
                if not (
                    math.isfinite(speed) and math.isfinite(direction)
                    and 0.0 <= speed <= 450.0 and observed_at >= cutoff
                ):
                    continue
                rec.wind = round(speed, 1)
                rec.wind_dir = direction % 360.0
                key = (provider, sid)
                held = freshest.get(key)
                if held is None or observed_at > int(held.wind_at or 0):
                    freshest[key] = rec
        return list(freshest.values())

    def current_precipitation_points(
        self, *, max_age_s: int = 10800, now: Optional[datetime] = None,
    ) -> List[Tuple[float, float, float]]:
        """``(lat, lon, mm)`` de acumulados moviles reales de 24 horas."""
        return [
            (float(rec.lat), float(rec.lon), float(rec.rain_24h))
            for rec in self.current_precipitation_records(max_age_s=max_age_s, now=now)
        ]

    def current_precipitation_records(
        self, *, max_age_s: int = 10800, now: Optional[datetime] = None,
    ) -> List[StationDaily]:
        """Ultimo acumulado 24 h reciente de cada estacion.

        ``rain`` (dia civil) no se usa como fallback: una lectura de las 15:00
        solo acumula 15 horas y no es comparable con una ventana movil.
        """
        from server.services.stations import is_station_hidden

        cutoff = int((now or datetime.now(tz=timezone.utc)).timestamp()) - max(60, int(max_age_s))
        freshest: Dict[Tuple[str, str], StationDaily] = {}
        for (provider, _day), station_rows in self._daily.items():
            for sid, rec in station_rows.items():
                if is_station_hidden(provider, sid):
                    continue
                if rec.lat is None or rec.lon is None or rec.rain_24h is None:
                    continue
                try:
                    amount = float(rec.rain_24h)
                    observed_at = int(rec.rain_24h_at or 0)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(amount) or not (0.0 <= amount <= _WORLD_RAIN_RECORD_MM):
                    continue
                if observed_at < cutoff:
                    continue
                rec.rain_24h = round(amount, 1)
                key = (provider, sid)
                held = freshest.get(key)
                if held is None or observed_at > int(held.rain_24h_at or 0):
                    freshest[key] = rec
        return list(freshest.values())

    def commit(self, staged: Dict[str, List[StationDaily]], *, now: Optional[datetime] = None) -> None:
        """Publica ATÓMICAMENTE los resultados de un ciclo. Síncrono → ninguna
        otra corutina lee a medias (asyncio monohilo). Los proveedores que NO
        están en ``staged`` (fallaron con excepción) conservan su último dato
        bueno. Marca ``updated_at`` al final."""
        for provider, records in staged.items():
            self._stamp_country(provider, records)
            fallback_day = self.local_day(provider, now)
            # UPSERT por (proveedor, fecha local): la estación se mete en el
            # bucket de SU día local. Cuando cruza medianoche, su nuevo dato va
            # al día siguiente y el día anterior queda CONGELADO con su último
            # valor (currents.json deja de servirlo, pero el store lo conserva).
            for r in records:
                _sanitize_record_extremes(r)  # imposibilidad física (todos)
                day = self._bucket_day(provider, r, fallback_day)
                r.local_date = day
                self._daily.setdefault((provider, day), {})[r.station_id] = r
            self._prune_days(provider)
        if staged:  # no avanzar la marca si un reintento no consiguió nada
            self.updated_at = datetime.now(tz=timezone.utc)

    def _filtered_records(
        self,
        *,
        providers: Optional[List[str]],
        country: Optional[str],
        day: Optional[str],
        exclude_countries: Optional[set] = None,
    ):
        """Itera (fecha, registro) del pool filtrado por proveedor/país/fecha.
        ``exclude_countries`` quita esos ISO2 (p.ej. excluir la Antártida del
        ranking global de mínimas)."""
        provs = set(providers) if providers else None
        country_filter = str(country or "").strip().upper()
        excluded = exclude_countries or set()
        for (prov, d), recs in self._daily.items():
            if provs is not None and prov not in provs:
                continue
            if day is not None and d != day:
                continue
            for r in recs.values():
                if country_filter and r.country != country_filter:
                    continue
                if r.country in excluded:
                    continue
                yield d, r

    def day_options(
        self,
        *,
        providers: Optional[List[str]] = None,
        country: Optional[str] = None,
    ) -> tuple[List[str], Optional[str]]:
        """Fechas locales disponibles para ese pool (orden cronológico) y la
        fecha PRINCIPAL. La principal es el día por defecto; las flechas del
        frontend permiten ver las demás.

        Principal = la fecha MÁS RECIENTE con una cobertura razonable (≥25%
        de la fecha más poblada). Elegir simplemente "la que más estaciones
        tiene" hacía que un día pasado ya COMPLETO (todas sus estaciones
        reportaron) ganara siempre al día en curso, que arranca con pocas —
        el ranking abría enseñando el viernes un domingo."""
        counts: Dict[str, int] = {}
        for d, r in self._filtered_records(providers=providers, country=country, day=None):
            if _station_has_data(r):
                counts[d] = counts.get(d, 0) + 1
        if not counts:
            return [], None
        days = sorted(counts)
        threshold = max(counts.values()) * 0.25
        main = max(d for d, n in counts.items() if n >= threshold)
        return days, main

    def top(
        self,
        metric: str,
        *,
        providers: Optional[List[str]] = None,
        country: Optional[str] = None,
        day: Optional[str] = None,
        exclude_countries: Optional[set] = None,
        limit: int = 10,
        descending: Optional[bool] = None,
        now: Optional[datetime] = None,
    ) -> List[StationDaily]:
        """Top-N de UNA fecha local concreta (``day``). Si no se pasa, usa la
        fecha principal del pool (no mezcla husos: una sola fecha por lista).
        ``exclude_countries`` quita esos países (toggle "sin Antártida").

        ``descending`` invierte el sentido del ranking respecto al natural de la
        métrica (``METRIC_DESC``): con ``None`` se usa el natural (Tmáx de mayor
        a menor, Tmín de menor a mayor); ``True``/``False`` lo fuerza, de modo
        que se puede pedir p.ej. la Tmáx MÁS BAJA (mínimas de máximas) o la Tmín
        MÁS ALTA (máximas de mínimas) — el otro extremo, no solo el top-N
        invertido en pantalla."""
        if metric not in METRICS:
            return []
        if day is None:
            _, day = self.day_options(providers=providers, country=country)
            if day is None:
                return []
        reverse = METRIC_DESC[metric] if descending is None else bool(descending)
        ranked = [
            r
            for _, r in self._filtered_records(
                providers=providers, country=country, day=day, exclude_countries=exclude_countries
            )
            if r.value(metric) is not None
        ]
        ranked.sort(key=lambda r: r.value(metric), reverse=reverse)
        return ranked[:limit]

    def station_daily(
        self,
        provider: str,
        station_id: str,
        *,
        now: Optional[datetime] = None,
    ) -> Optional[StationDaily]:
        """Agregado diario de una estación: el de su fecha local más reciente."""
        provider_id = str(provider or "").strip().upper()
        sid = str(station_id or "").strip().upper()
        if not provider_id or not sid:
            return None
        days = sorted({k[1] for k in self._daily if k[0] == provider_id}, reverse=True)
        for d in days:
            rec = self._daily.get((provider_id, d), {}).get(sid)
            if rec is not None:
                return rec
        return None

    def countries(self, *, now: Optional[datetime] = None) -> List[str]:
        """Países (ISO2) con al menos una estación con dato en cualquier fecha
        viva. Excluye el centinela ``UN`` (redes globales) y normaliza códigos
        legacy (TU→TR, AN fuera): defensa para registros antiguos que entren
        por el snapshot persistido, escritos antes de la normalización."""
        from server.services.stations import (
            COUNTRY_CODE_ALIASES,
            COUNTRY_CODES_RESOLVED_BY_COORDS,
        )

        out: set = set()
        for (_prov, _d), recs in self._daily.items():
            for r in recs.values():
                code = str(r.country or "").strip().upper()
                code = COUNTRY_CODE_ALIASES.get(code, code)
                if code and code not in COUNTRY_CODES_RESOLVED_BY_COORDS and _station_has_data(r):
                    out.add(code)
        return sorted(out)

    def providers(self, *, now: Optional[datetime] = None) -> List[str]:
        """Proveedores con datos en cualquier fecha viva."""
        return sorted({prov for (prov, _d), recs in self._daily.items() if recs})


# ----------------------------------------------------------------------
# Refresco periódico (job del lifespan)
# ----------------------------------------------------------------------
async def refresh_once(
    store: RankingStore,
    *,
    client: httpx.AsyncClient,
    settings=None,
    only: Optional[set] = None,
) -> set:
    """Refresca proveedores y los PUBLICA (``store.commit``). ``only`` limita a
    un subconjunto (para reintentar SOLO los que fallaron, sin re-llamar a los
    que ya van bien). Un proveedor que falle (excepción) se omite del commit →
    conserva su último dato bueno. Devuelve el conjunto de proveedores que
    FALLARON este ciclo."""
    import asyncio

    def _want(provider: str) -> bool:
        return only is None or provider in only

    mc_key = getattr(settings, "meteocat_api_key", "") if settings else ""
    aemet_key = getattr(settings, "aemet_api_key", "") if settings else ""
    mf_key = getattr(settings, "meteofrance_api_key", "") if settings else ""
    frost_id = getattr(settings, "frost_client_id", "") if settings else ""
    frost_secret = getattr(settings, "frost_client_secret", "") if settings else ""

    # Cada proveedor es independiente → se lanzan TODOS en paralelo y se
    # publican POR SEPARADO en cuanto cada uno termina (no se espera al gather
    # completo). Así un proveedor inalcanzable (p.ej. MeteoHub, cuyo host puede
    # colgar hasta el timeout de connect desde ciertas redes) no retiene la
    # publicación de los que ya respondieron: MeteoGalicia/IEM/AEMET/… aparecen
    # en segundos y el lento se marca fallido → reintento a los 60s.
    tasks: Dict[str, Any] = {}
    if _want("METEOGALICIA"):
        tasks["METEOGALICIA"] = fetch_meteogalicia_daily(client=client)
    if mc_key and _want("METEOCAT"):
        tasks["METEOCAT"] = fetch_meteocat_daily(mc_key, client=client)
    if aemet_key and _want("AEMET"):
        tasks["AEMET"] = fetch_aemet_records(store, aemet_key, client=client)
    if _want("METEOHUB_IT"):
        tasks["METEOHUB_IT"] = fetch_meteohub_daily(client=client)
    if _want("IPMA"):
        tasks["IPMA"] = fetch_ipma_daily(client=client)
    if _want("GEOSPHERE"):
        tasks["GEOSPHERE"] = fetch_geosphere_daily(client=client)
    if _want("SMHI"):
        tasks["SMHI"] = fetch_smhi_records(store, client=client)
    if _want("ECCC"):
        tasks["ECCC"] = fetch_eccc_records(store, client=client)
    if frost_id and _want("FROST"):
        tasks["FROST"] = fetch_frost_daily(frost_id, frost_secret, client=client)
    if mf_key and _want("METEOFRANCE"):
        tasks["METEOFRANCE"] = fetch_meteofrance_records(store, mf_key, client=client)
    if _want("IEM"):
        tasks["IEM"] = fetch_iem_daily(store=store, client=client)
    if _want("CLIMANTARTIDE"):
        tasks["CLIMANTARTIDE"] = fetch_climantartide_daily(client=client)

    async def _run(provider: str, coro):
        """Envuelve el fetch para devolver ``(proveedor, resultado|excepción)``,
        de modo que ``as_completed`` los entregue en orden de finalización sin
        que una excepción cancele a los hermanos."""
        try:
            return provider, await coro
        except Exception as exc:  # noqa: BLE001 — cualquier fallo → reintento del ciclo
            return provider, exc

    failed: set = set()
    for finished in asyncio.as_completed([_run(p, c) for p, c in tasks.items()]):
        provider, result = await finished
        if isinstance(result, Exception):
            logger.warning("ranking: %-12s FALLO · %s (reintento)", provider, type(result).__name__)
            failed.add(provider)
        else:
            logger.info("ranking: %-12s OK · %d estaciones", provider, len(result))
            # Commit incremental: publica ESTE proveedor ya, sin esperar a los
            # demás. ``commit`` es síncrono y atómico por llamada → los lectores
            # ven el pool consistente (nuevos de este proveedor + últimos buenos
            # de los que aún no han terminado).
            store.commit({provider: result})
    return failed


def _retry_backoff_s(
    consecutive_failures: int,
    base_s: float = 60.0,
    max_s: float = 900.0,
) -> float:
    """Espera antes del próximo reintento de un proveedor caído.

    Las primeras 4 rachas reintentan al ritmo base (fallos transitorios:
    un 500 suelto, un timeout); a partir de la 5ª el intervalo se duplica
    por fallo hasta el techo, para no martillear en bucle a un proveedor
    caído de verdad (AEMET puede pasarse horas devolviendo errores).
    """
    count = max(1, int(consecutive_failures))
    if count <= 4:
        return float(base_s)
    return float(min(max_s, base_s * (2 ** (count - 4))))


def _next_aligned_run(offset_min: int, now: Optional[datetime] = None) -> datetime:
    """Próximo instante con minuto == ``offset_min`` (segundo 0). El ciclo del
    ranking se alinea justo después de la publicación horaria de los proveedores.
    El minuto es independiente del huso, así que se calcula en UTC."""
    now = now or datetime.now(tz=timezone.utc)
    nxt = now.replace(minute=max(0, min(59, offset_min)), second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(hours=1)
    return nxt


async def refresh_loop(
    store: RankingStore,
    *,
    client: httpx.AsyncClient,
    settings=None,
    interval_s: float = 3600.0,  # compat; la cadencia real es horaria alineada
    retry_interval_s: float = 60.0,
    state_path: str = "",
) -> None:
    """Bucle de refresco ALINEADO a la hora: un ciclo COMPLETO en el minuto
    ``ranking_refresh_offset_min`` (def. :05) de cada hora, para pillar la
    publicación horaria de los proveedores en vez de a minutos arbitrarios. Hace
    un primer ciclo INMEDIATO al arrancar (para no salir vacío hasta el próximo
    :05). Entre ciclos, si quedaron proveedores con fallo, reintenta SOLO esos
    con backoff por proveedor: ``retry_interval_s`` las primeras 4 rachas y
    duplicando después hasta 15 min (``_retry_backoff_s``), para no martillear
    a un proveedor caído de verdad. Cancela limpio al apagar el server. Con
    ``state_path``, tras cada ciclo se vuelca el store a disco para que
    reinicios/redeploys no pierdan el estado."""
    import asyncio

    offset_min = int(getattr(settings, "ranking_refresh_offset_min", 5)) if settings else 5
    # Racha de fallos consecutivos y próximo reintento por proveedor.
    failure_counts: Dict[str, int] = {}
    next_retry_at: Dict[str, datetime] = {}

    def _register_attempt(attempted: set, failed: set) -> None:
        now = datetime.now(tz=timezone.utc)
        for provider in attempted - failed:
            failure_counts.pop(provider, None)
            next_retry_at.pop(provider, None)
        for provider in failed:
            failure_counts[provider] = failure_counts.get(provider, 0) + 1
            backoff = _retry_backoff_s(failure_counts[provider], base_s=retry_interval_s)
            next_retry_at[provider] = now + timedelta(seconds=backoff)
            if failure_counts[provider] > 4:
                logger.warning(
                    "ranking: %-12s lleva %d fallos seguidos → backoff %.0fs",
                    provider, failure_counts[provider], backoff,
                )

    async def _persist() -> None:
        if not state_path:
            return
        try:
            # En thread aparte: el dump gzip puede tardar algún segundo con
            # muchos buckets y no debe congelar las requests del ranking.
            await asyncio.to_thread(store.save_to_disk, state_path)
        except Exception:
            logger.warning(
                "ranking: no se pudo guardar el snapshot en %s", state_path, exc_info=True,
            )

    async def _prebuild_map_fields() -> None:
        """Mueve el coste de los mapas al job horario, nunca al visitante."""
        try:
            from server.services.map_field_assets import build_map_field_assets

            await asyncio.to_thread(build_map_field_assets, store)
        except Exception:
            # El ranking sigue siendo válido aunque falle una textura. El
            # frontend conserva su fallback bajo demanda y el próximo ciclo
            # vuelve a intentarlo.
            logger.warning(
                "ranking: no se pudieron pregenerar los mapas de valores",
                exc_info=True,
            )

    # Si se restauró un snapshot del Volume, publica sus texturas antes de
    # iniciar llamadas a proveedores. Así un redeploy tampoco deja al primer
    # visitante pagando la generación mientras termina el refresco inmediato.
    await _prebuild_map_fields()

    # Ciclo inmediato al arrancar: el ranking no debe salir vacío hasta el :05.
    pending = await refresh_once(store, client=client, settings=settings)
    _register_attempt(set(failure_counts) | pending, pending)
    await _persist()
    await _prebuild_map_fields()
    next_full = _next_aligned_run(offset_min)

    while True:
        now = datetime.now(tz=timezone.utc)
        secs_to_full = (next_full - now).total_seconds()
        if secs_to_full <= 0:
            pending = await refresh_once(store, client=client, settings=settings)  # completo
            _register_attempt(set(failure_counts) | pending, pending)
            await _persist()
            await _prebuild_map_fields()
            next_full = _next_aligned_run(offset_min)
            continue
        if pending:
            # Solo los proveedores cuyo backoff ya venció; el resto espera.
            due = {p for p in pending if next_retry_at.get(p, now) <= now}
            if due:
                logger.info(
                    "ranking: con fallo %s → reintento %s (próximo completo a las :%02d)",
                    sorted(pending), sorted(due), max(0, min(59, offset_min)),
                )
                failed_again = await refresh_once(
                    store, client=client, settings=settings, only=due,
                )
                _register_attempt(due, failed_again)
                pending = (pending - due) | failed_again
                await _persist()
                await _prebuild_map_fields()
                continue
            next_due = min(
                (next_retry_at[p] for p in pending if p in next_retry_at),
                default=next_full,
            )
            wait_s = (min(next_due, next_full) - datetime.now(tz=timezone.utc)).total_seconds()
            await asyncio.sleep(max(1.0, wait_s))
        else:
            await asyncio.sleep(max(1.0, secs_to_full))
