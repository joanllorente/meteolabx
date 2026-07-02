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
    "FROST": "Europe/Oslo",
    # IEM mezcla 204 husos: usamos UTC como clave de "día" del bucket; cada
    # estación trae su propio día local en ``currents.json`` (campo agregado).
    "IEM": "UTC",
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
    "FROST": "NO",
}

# País → proveedor nacional para el "ranking del país del usuario".
# Los regionales (Meteocat/MeteoGalicia/Euskalmet) no son "país".
COUNTRY_PROVIDER = {
    "ES": "AEMET",
    "FR": "METEOFRANCE",
    "IT": "METEOHUB_IT",
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
    return any(r.value(m) is not None for m in METRICS)


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
        return parse_meteogalicia_daily(resp.json())
    finally:
        if owns:
            await client.aclose()


# ----------------------------------------------------------------------
# Adaptador: Meteocat (directo, API key; 1 llamada por variable)
# ----------------------------------------------------------------------
MC_BASE = "https://api.meteo.cat/xema/v1"
# Variables XEMA: Tmáx=40, Tmín=42, lluvia=35, ráfaga (ratxa màx 10m, m/s)=50.
_MC_VAR = {"tmax": 40, "tmin": 42, "rain": 35, "gust": 50}


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


def _mc_build_records(raw: Dict[str, Dict[str, List[float]]]) -> List[StationDaily]:
    from server.services import meteocat

    codis = set()
    for m in METRICS:
        codis |= set(raw.get(m, {}).keys())
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
                gust=_daily_gust_max_from_series([v * 3.6 for v in gust_vals]) if gust_vals else None,
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
                raw[metric] = await _mc_fetch_variable(client, api_key, var, day, timeout_s)
            return _mc_build_records(raw)

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


def _mh_parse_station(st: dict) -> Optional[StationDaily]:
    from server.services import meteohub as mh

    stat = st.get("stat", {}) if isinstance(st, dict) else {}
    lat, lon, net = stat.get("lat"), stat.get("lon"), str(stat.get("net", ""))
    name = ""
    for det in stat.get("details", []) or []:
        if isinstance(det, dict) and det.get("var") == "B01019":
            name = str(det.get("val") or "").strip()
    aligned = mh._align_series(mh._products_by_code(st))
    temps = [t for t in aligned["temps"] if t == t]
    precs = [p for p in aligned["precips"] if p == p]
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
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
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
                    rec = _mh_parse_station(st)
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
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    # Meteo-France publica normalmente el paquete horario sobre H+10 min.
    # Intentamos la hora actual; si aún no existe, quedará como missing y se
    # reintentará en el siguiente ciclo.
    latest = now_local.replace(minute=0, second=0, microsecond=0)

    # Horas necesarias del día (hour_key UTC → date param UTC).
    needed: Dict[str, str] = {}
    h = start_local
    while h <= latest:
        utc = h.astimezone(timezone.utc)
        needed[utc.strftime("%Y-%m-%dT%H")] = utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        h += timedelta(hours=1)

    existing = store.accumulated_hours("METEOFRANCE", day)
    missing = {hk: ds for hk, ds in needed.items() if hk not in existing}

    if missing:
        catalog = {
            str(s.get("id_station")): s
            for s in mf._load_stations()
            if isinstance(s, dict) and s.get("id_station")
        }
        headers = {"apikey": api_key, "Accept": "*/*"}
        sem = asyncio.Semaphore(8)

        async def _fetch_hour(hour_key: str, date_str: str):
            async with sem:
                try:
                    resp = await client.get(
                        f"{MF_PAQUET_BASE}/paquet/stations/horaire",
                        params={"date": date_str, "format": "json"},
                        headers=headers, timeout=60.0, follow_redirects=True,
                    )
                    if resp.status_code != 200:
                        return (hour_key, [])
                    data = resp.json()
                    return (hour_key, data if isinstance(data, list) else [])
                except Exception as exc:
                    logger.warning("ranking: fallo hora Meteo-France %s (%s)", date_str, type(exc).__name__)
                    return (hour_key, [])

        results = await asyncio.gather(*[_fetch_hour(hk, ds) for hk, ds in missing.items()])
        for hour_key, hour_data in results:
            for rec in hour_data:
                if not isinstance(rec, dict):
                    continue
                sid = str(rec.get("geo_id_insee", "")).strip()
                if not sid:
                    continue
                gust_ms = _num(rec.get("raf"))
                if gust_ms is None:
                    gust_ms = _num(rec.get("fxy"))
                station = catalog.get(sid, {})
                store.upsert_hourly(
                    "METEOFRANCE", sid,
                    day=day, hour_key=hour_key,
                    name=str(station.get("name", "") or sid).strip(),
                    locality="",
                    lat=_clean(station.get("lat") if station else rec.get("lat")),
                    lon=_clean(station.get("lon") if station else rec.get("lon")),
                    values={
                        "tmax": _k_to_c(rec.get("tx")),
                        "tmin": _k_to_c(rec.get("tn")),
                        "gust": _ms_to_kmh(gust_ms),
                        "rain": _num(rec.get("rr1")),
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
    "air_temperature,max(wind_speed_of_gust PT1H),sum(precipitation_amount PT1H)"
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
    ref = (
        f"{start_local.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}/"
        f"{now_local.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )
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
                    agg = local.setdefault(sid, {"temps": [], "gusts": [], "rains": []})
                    for obs in item.get("observations", []) or []:
                        el = str(obs.get("elementId", ""))
                        v = _num(obs.get("value"))
                        if v is None:
                            continue
                        if el == "air_temperature":
                            agg["temps"].append(v)
                        elif el.startswith("max(wind_speed_of_gust"):
                            agg["gusts"].append(v)
                        elif el.startswith("sum(precipitation_amount"):
                            agg["rains"].append(v)
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
            m = merged.setdefault(sid, {"temps": [], "gusts": [], "rains": []})
            m["temps"].extend(agg["temps"])
            m["gusts"].extend(agg["gusts"])
            m["rains"].extend(agg["rains"])

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
        # Tmáx/Tmín desde la temperatura INSTANTÁNEA `ta`, NO desde
        # `tamax`/`tamin`: el campo `tamax` de /todas es un máximo horario
        # CRUDO sin validar que puede superar el máximo oficial de AEMET (p.ej.
        # Andújar: oficial 44.8, tamax 45.1) y, al ser ruidoso, colaría
        # estaciones rotas al top del ranking. Con max/min de `ta` nunca
        # mostramos un valor por encima del oficial (a costa de quedarnos un
        # poco cortos: el bulk es horario y se pierde el pico entre muestras).
        ta = _clean(aemet._parse_num(aemet._field(rec, "ta", "TA")))
        store.upsert_hourly(
            "AEMET", idema,
            day=local.date().isoformat(),
            hour_key=local.strftime("%Y-%m-%dT%H"),
            name=str(aemet._field(rec, "ubi", "UBI") or idema).strip(),
            locality="",
            lat=_clean(aemet._parse_num(aemet._field(rec, "lat", "LAT"))),
            lon=_clean(aemet._parse_num(aemet._field(rec, "lon", "LON"))),
            values={
                "tmax": ta,
                "tmin": ta,
                "gust": _clean(aemet._ms_to_kmh(aemet._field(rec, "vmax", "VMAX"))),
                "rain": _clean(aemet._parse_num(aemet._field(rec, "prec", "PREC"))),
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
_MAX_DIURNAL_RANGE_C = 40.0   # salto diurno máx−mín imposible (real ≲30°C)
# Diferencia ACTUAL−mín máxima coherente: un sitio realmente frío tiene la actual
# fría (diferencia pequeña); actual templada + mín −12 (Meadows of Dan, Virginia
# en junio, actual 17,8 / mín −12,8 = 30,6) es un pico roto de la mínima. Solo
# afecta a mínimas de sitios CON la actual templada → nunca récords de frío.
_MAX_TCUR_TMIN_GAP_C = 25.0
_WORLD_GUST_RECORD_KMH = 420.0  # récord mundial 408 km/h (Barrow I.) + margen
# Suelo de frío por latitud Y ESTACIÓN DEL AÑO (imposibilidad climatológica, no
# "tope de récord"). En VERANO no-polar no se baja de ~−15°C (Francia/Sicilia/
# Ohio en junio a −30/−42 = sensor roto); los trópicos nunca de ~−25°C; en
# invierno continental puede llegar muy bajo (−60); zonas polares al récord
# mundial (−89,2°C, Vostok). Así el frío real (Antártida −68, Patagonia −25 en
# su invierno) se conserva y la basura de verano cae.
_TMIN_FLOOR_SUMMER_C = -20.0
_TMIN_FLOOR_TROPICAL_C = -25.0
_TMIN_FLOOR_NONPOLAR_C = -60.0
_TMIN_FLOOR_POLAR_SUMMER_C = -45.0  # Ártico/Antártida en SU verano (Groenlandia ~−30)
_TMIN_FLOOR_POLAR_C = -92.0         # invierno polar (Vostok −89,2)
_TROPICAL_LAT = 25.0
_POLAR_LAT = 60.0


def _tmin_floor(lat: Optional[float], month: Optional[int] = None) -> float:
    if lat is None:
        return _TMIN_FLOOR_NONPOLAR_C
    if month is None:
        month = datetime.now(tz=timezone.utc).month
    a = abs(lat)
    # ¿es verano en el hemisferio de la estación? (afecta a todas las bandas
    # salvo trópicos, que no tienen estaciones marcadas).
    summer = (5 <= month <= 9) if lat >= 0 else (month in (11, 12, 1, 2, 3))
    if a >= _POLAR_LAT:
        return _TMIN_FLOOR_POLAR_SUMMER_C if summer else _TMIN_FLOOR_POLAR_C
    if a < _TROPICAL_LAT:
        return _TMIN_FLOOR_TROPICAL_C
    return _TMIN_FLOOR_SUMMER_C if summer else _TMIN_FLOOR_NONPOLAR_C


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
        # 1) Mínima imposible PRIMERO (así un pico de mínima no arrastra luego la
        #    máxima REAL por rango imposible). Dos señales:
        #    - suelo por latitud (los trópicos no bajan de −25°C…);
        #    - incoherencia con la ACTUAL: un sitio de verdad frío tiene la
        #      temperatura actual fría; si la actual es templada y la mín −40
        #      (Iowa/Ohio en junio), la mín es un pico roto. Un récord real
        #      (Antártida −68 con actual −60) tiene poca diferencia → se conserva.
        if tmin is not None and (
            tmin < _tmin_floor(lat) or (tcur - tmin) > _MAX_TCUR_TMIN_GAP_C
        ):
            tmin = None
        # 2) Máxima imposible: por encima del récord mundial, o un rango diurno
        #    imposible respecto a una mínima ya saneada (pico de la máxima).
        if tmax is not None:
            if tmax > _WORLD_TMAX_RECORD_C:
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

    Los extras de IEM (sin-actual, incoherencia mín↔actual) ya se aplican en
    `_parse_iem_network`; esto es la red de seguridad común a todos."""
    if rec.tmin is not None and rec.tmin < _tmin_floor(rec.lat):
        rec.tmin = None
    if rec.tmax is not None:
        if rec.tmax > _WORLD_TMAX_RECORD_C:
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

        # País REAL por coordenadas (precalculado en el catálogo). Si la
        # estación no resuelve o cae en un país ya cubierto, se descarta (no
        # duplica ni coloca nada en el país equivocado).
        rec_country = station_countries.get(station_id, "")
        if not rec_country:
            continue

        local_valid = str(row.get("local_valid") or "")
        tmax, tmin, gust = _clean_iem_extremes(
            _f_to_c_num(row.get("max_tmpf")),
            _f_to_c_num(row.get("min_tmpf")),
            _knots_to_kmh_num(row.get("max_gust")),
            _num(row.get("lat")),
            _f_to_c_num(row.get("tmpf")),
        )
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
            return _parse_iem_network(network, rows, station_countries)

    try:
        chunks = await asyncio.gather(*[_one(net) for net in networks])
    finally:
        if owns:
            await client.aclose()

    records: List[StationDaily] = []
    for chunk in chunks:
        records.extend(chunk)
    return records


# ----------------------------------------------------------------------
# Store en memoria
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# Saneamiento de datos (redes heterogéneas → sensores rotos)
# ----------------------------------------------------------------------
# Rango físico plausible; fuera de esto → dato basura (se descarta).
_HARD_BOUNDS = {
    "tmax": (-60.0, 56.0),   # récord mundial ~56.7 °C
    "tmin": (-70.0, 45.0),
    "gust": (0.0, 360.0),
    "rain": (0.0, 1000.0),   # mm/día
}
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
        st["hours"][hour_key] = values

    def accumulated_hours(self, provider: str, day: str) -> set:
        """Horas ya almacenadas para (proveedor, día) — para pedir solo las
        que faltan (Meteo-France acumula 1 llamada por hora nueva)."""
        hours: set = set()
        for st in self._hourly.get((provider, day), {}).values():
            hours.update(st.get("hours", {}).keys())
        return hours

    def reduce_accumulable_records(
        self, provider: str, *, now: Optional[datetime] = None
    ) -> List[StationDaily]:
        """Reduce las horas acumuladas a registros diarios y los DEVUELVE (sin
        publicarlos; el commit del ciclo los escribe). Mantiene hoy+ayer; de
        madrugada (hoy aún flojo) sirve ayer, como los directos."""
        today = self.local_day(provider, now)
        yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
        keep = {today, yesterday}
        for k in [k for k in self._hourly if k[0] == provider and k[1] not in keep]:
            self._hourly.pop(k, None)

        day_records: Dict[str, List[StationDaily]] = {}
        for day in (today, yesterday):
            recs: List[StationDaily] = []
            for sid, st in self._hourly.get((provider, day), {}).items():
                hours = list(st["hours"].values())
                txs = [h["tmax"] for h in hours if h.get("tmax") is not None]
                tns = [h["tmin"] for h in hours if h.get("tmin") is not None]
                gus = [h["gust"] for h in hours if h.get("gust") is not None]
                rns = [h["rain"] for h in hours if h.get("rain") is not None]
                meta = st["meta"]
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
                    )
                )
            day_records[day] = recs
        return _pick_best_day(day_records)

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
        fecha PRINCIPAL (la que más estaciones tienen en curso). La principal es
        el día por defecto; las flechas del frontend permiten ver las demás."""
        counts: Dict[str, int] = {}
        for d, r in self._filtered_records(providers=providers, country=country, day=None):
            if _station_has_data(r):
                counts[d] = counts.get(d, 0) + 1
        if not counts:
            return [], None
        days = sorted(counts)
        main = max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
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
        now: Optional[datetime] = None,
    ) -> List[StationDaily]:
        """Top-N de UNA fecha local concreta (``day``). Si no se pasa, usa la
        fecha principal del pool (no mezcla husos: una sola fecha por lista).
        ``exclude_countries`` quita esos países (toggle "sin Antártida")."""
        if metric not in METRICS:
            return []
        if day is None:
            _, day = self.day_options(providers=providers, country=country)
            if day is None:
                return []
        ranked = [
            r
            for _, r in self._filtered_records(
                providers=providers, country=country, day=day, exclude_countries=exclude_countries
            )
            if r.value(metric) is not None
        ]
        ranked.sort(key=lambda r: r.value(metric), reverse=METRIC_DESC[metric])
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
        viva. Excluye el centinela ``UN`` (estaciones de redes globales)."""
        out: set = set()
        for (_prov, _d), recs in self._daily.items():
            for r in recs.values():
                code = str(r.country or "").strip().upper()
                if code and code != "UN" and _station_has_data(r):
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
    # publican juntos. Así el ciclo dura lo que el más lento (~15s), no la
    # suma (~40s). Los `fetch_*` ya limitan su propia concurrencia interna.
    tasks: Dict[str, Any] = {}
    if _want("METEOGALICIA"):
        tasks["METEOGALICIA"] = fetch_meteogalicia_daily(client=client)
    if mc_key and _want("METEOCAT"):
        tasks["METEOCAT"] = fetch_meteocat_daily(mc_key, client=client)
    if aemet_key and _want("AEMET"):
        tasks["AEMET"] = fetch_aemet_records(store, aemet_key, client=client)
    if _want("METEOHUB_IT"):
        tasks["METEOHUB_IT"] = fetch_meteohub_daily(client=client)
    if frost_id and _want("FROST"):
        tasks["FROST"] = fetch_frost_daily(frost_id, frost_secret, client=client)
    if mf_key and _want("METEOFRANCE"):
        tasks["METEOFRANCE"] = fetch_meteofrance_records(store, mf_key, client=client)
    if _want("IEM"):
        tasks["IEM"] = fetch_iem_daily(client=client)

    staged: Dict[str, List[StationDaily]] = {}
    failed: set = set()
    if tasks:
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for provider, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                logger.warning("ranking: %-12s FALLO · %s (reintento)", provider, type(result).__name__)
                failed.add(provider)
            else:
                staged[provider] = result
                logger.info("ranking: %-12s OK · %d estaciones", provider, len(result))

    # Publica lo conseguido (commit solo avanza updated_at si hubo algo).
    store.commit(staged)
    return failed


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
) -> None:
    """Bucle de refresco ALINEADO a la hora: un ciclo COMPLETO en el minuto
    ``ranking_refresh_offset_min`` (def. :05) de cada hora, para pillar la
    publicación horaria de los proveedores en vez de a minutos arbitrarios. Hace
    un primer ciclo INMEDIATO al arrancar (para no salir vacío hasta el próximo
    :05). Entre ciclos, si quedaron proveedores con fallo, reintenta SOLO esos
    cada ``retry_interval_s`` (sin re-llamar a los que van bien). Cancela limpio
    al apagar el server."""
    import asyncio

    offset_min = int(getattr(settings, "ranking_refresh_offset_min", 5)) if settings else 5

    # Ciclo inmediato al arrancar: el ranking no debe salir vacío hasta el :05.
    pending = await refresh_once(store, client=client, settings=settings)
    next_full = _next_aligned_run(offset_min)

    while True:
        secs_to_full = (next_full - datetime.now(tz=timezone.utc)).total_seconds()
        if secs_to_full <= 0:
            pending = await refresh_once(store, client=client, settings=settings)  # completo
            next_full = _next_aligned_run(offset_min)
            continue
        if pending:
            logger.info(
                "ranking: con fallo %s → reintento solo esos (próximo completo a las :%02d)",
                sorted(pending), max(0, min(59, offset_min)),
            )
            await asyncio.sleep(max(1.0, min(secs_to_full, retry_interval_s)))
            if datetime.now(tz=timezone.utc) < next_full:
                pending = await refresh_once(store, client=client, settings=settings, only=pending)
        else:
            await asyncio.sleep(max(1.0, secs_to_full))
