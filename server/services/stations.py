"""SQLite-backed normalized station catalog used by FastAPI."""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import data_files


SENSOR_KEYS = (
    "thermometer", "hygrometer", "barometer", "anemometer",
    "wind_vane", "rain_gauge", "pyranometer", "uv",
)

CONNECTABLE_PROVIDERS = (
    "AEMET", "METEOCAT", "EUSKALMET", "FROST", "METEOFRANCE",
    "METEOGALICIA", "NWS", "POEM", "METOFFICE", "METEOHUB_IT",
    "IEM",
)
CATALOG_PROVIDERS = CONNECTABLE_PROVIDERS

PROVIDER_COUNTRIES = {
    "AEMET": "ES",
    "METEOCAT": "ES",
    "EUSKALMET": "ES",
    "METEOGALICIA": "ES",
    "POEM": "ES",
    "METEOFRANCE": "FR",
    "FROST": "NO",
    "NWS": "US",
    "METOFFICE": "GB",
    "METEOHUB_IT": "IT",
}

HISTORICAL_PROVIDER_IDS = {"AEMET", "METEOCAT", "METEOFRANCE", "METEOGALICIA"}
IEM_HISTORICAL_NETWORK_MARKERS = ("ASOS", "AWOS", "METAR")

IEM_COUNTRY_TIMEZONE_OVERRIDES = {
    ("ES", "Europe/Paris"): "FR",
}

COUNTRY_CODE_ALIASES = {
    "RQ": "PR",
    "TU": "TR",
}


def _connect() -> sqlite3.Connection:
    path = Path(data_files.STATIONS_DB_PATH).resolve()
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _sensors(row: sqlite3.Row) -> Optional[Dict[str, bool]]:
    if row["sensor_station_pk"] is None:
        return None
    return {
        key: bool(row[key])
        for key in SENSOR_KEYS
        if row[key] is not None
    }


def _normalize_country_code(country: Any) -> str:
    code = str(country or "").strip().upper()
    if not code:
        return "UNSPECIFIED"
    return COUNTRY_CODE_ALIASES.get(code, code)


def _computed_has_historical(provider: Any, network: Any) -> bool:
    provider_id = str(provider or "").strip().upper()
    network_code = str(network or "").strip().upper()
    return provider_id in HISTORICAL_PROVIDER_IDS or (
        provider_id == "IEM"
        and any(marker in network_code for marker in IEM_HISTORICAL_NETWORK_MARKERS)
    )


def _is_historical_only(row: sqlite3.Row) -> bool:
    return bool(
        row["provider"] == "IEM"
        and bool(row["has_historical"])
        and row["online"] == 0
    )


def _is_connectable(row: sqlite3.Row) -> bool:
    if row["provider"] not in CONNECTABLE_PROVIDERS:
        return False
    return True


def _record(row: sqlite3.Row) -> Dict[str, Any]:
    country = PROVIDER_COUNTRIES.get(row["provider"]) or row["country"]
    if row["provider"] == "IEM":
        country_key = str(country or "").strip().upper()
        timezone_key = str(row["timezone"] or "").strip()
        country = IEM_COUNTRY_TIMEZONE_OVERRIDES.get((country_key, timezone_key), country)
    country = _normalize_country_code(country)
    return {
        "provider": row["provider"],
        "network": row["network_code"],
        "station_id": row["station_id"],
        "name": row["name"],
        "lat": row["latitude"],
        "lon": row["longitude"],
        "elevation": row["elevation_m"],
        "tz": row["timezone"],
        "country": country,
        "region": row["region"],
        "locality": row["locality"],
        "connectable": _is_connectable(row),
        "has_historical": bool(row["has_historical"])
        if "has_historical" in row.keys()
        else _computed_has_historical(row["provider"], row["network_code"]),
        "is_historical_only": _is_historical_only(row),
        "sensors": _sensors(row),
    }


def _effective_country_sql() -> str:
    raw_country = "UPPER(COALESCE(NULLIF(TRIM(s.country), ''), 'UNSPECIFIED'))"
    iem_cases = " ".join(
        f"WHEN s.provider = 'IEM' AND UPPER(COALESCE(s.country, '')) = '{country}' "
        f"AND COALESCE(s.timezone, '') = '{timezone}' THEN '{override}'"
        for (country, timezone), override in sorted(IEM_COUNTRY_TIMEZONE_OVERRIDES.items())
    )
    cases = " ".join(
        f"WHEN s.provider = '{provider}' THEN '{country}'"
        for provider, country in sorted(PROVIDER_COUNTRIES.items())
    )
    alias_cases = " ".join(
        f"WHEN {raw_country} = '{source}' THEN '{target}'"
        for source, target in sorted(COUNTRY_CODE_ALIASES.items())
    )
    return (
        f"CASE {iem_cases} {cases} {alias_cases} "
        f"ELSE {raw_country} END"
    )


_SELECT = """
SELECT s.*, ss.station_pk AS sensor_station_pk,
       ss.thermometer, ss.hygrometer, ss.barometer, ss.anemometer,
       ss.wind_vane, ss.rain_gauge, ss.pyranometer, ss.uv
FROM stations s
LEFT JOIN station_sensors ss USING(station_pk)
LEFT JOIN station_visibility_overrides svo USING(station_pk)
"""

_VISIBLE = " AND COALESCE(svo.hidden, 0) = 0"


def get_station(provider: str, station_id: str) -> Optional[Dict[str, Any]]:
    """Return one connectable station by case-insensitive provider identity."""
    provider = str(provider or "").strip().upper()
    station_id = str(station_id or "").strip()
    if provider not in CATALOG_PROVIDERS or not station_id:
        return None
    network = ""
    if provider == "IEM" and "|" in station_id:
        network, station_id = (part.strip() for part in station_id.split("|", 1))
    with _connect() as connection:
        if network:
            row = connection.execute(
                _SELECT + (
                    " WHERE s.provider = ? AND s.network_code = ? COLLATE NOCASE"
                    " AND s.station_id = ? COLLATE NOCASE"
                ) + _VISIBLE + " LIMIT 1",
                (provider, network, station_id),
            ).fetchone()
        else:
            row = connection.execute(
                _SELECT + " WHERE s.provider = ? AND s.station_id = ? COLLATE NOCASE" + _VISIBLE + " LIMIT 1",
                (provider, station_id),
            ).fetchone()
    return _record(row) if row is not None else None


def find_by_slug(provider: str, slug: str) -> Optional[Dict[str, Any]]:
    """Resolve one connectable station from ``provider`` + name slug.

    El slug es el que produce :func:`utils.station_slug.slugify` sobre el
    nombre de la estación, de modo que el ida-y-vuelta con el frontend es
    estable. Si varias estaciones del proveedor comparten slug (raro) se
    devuelve la de menor ``station_id`` para que la resolución sea
    determinista.
    """
    from utils.station_slug import slugify

    provider = str(provider or "").strip().upper()
    target = slugify(slug)
    if provider not in CATALOG_PROVIDERS or not target:
        return None

    with _connect() as connection:
        rows = connection.execute(
            _SELECT + " WHERE s.provider = ?" + _VISIBLE
            + " AND s.name IS NOT NULL AND TRIM(s.name) <> ''"
            + " ORDER BY s.station_id COLLATE NOCASE",
            (provider,),
        ).fetchall()

    for row in rows:
        if slugify(row["name"]) == target:
            return _record(row)
    return None


# Países con proveedor de ranking DEDICADO (con bulk propio) → se EXCLUYEN de
# IEM para no duplicar. EE.UU. NO está aquí: NWS no tiene endpoint bulk
# (observaciones solo por estación), así que el ranking de EE.UU. lo cubre IEM.
# (NWS se sigue usando para el MAPA, no para el ranking.)
IEM_RANKING_EXCLUDE_COUNTRIES = ("ES", "FR", "NO", "IT")

# Redes IEM que NO aportan al ranking (no se llaman, ahorrando peticiones):
#   - COCORAHS: pluviómetros ciudadanos, sin termómetro, volumen enorme.
#   - RWIS: sensores de carretera (sesgo de asfalto).
#   - *CLIMATE: resúmenes diarios COOP; NO sirven datos por ``currents.json``
#     (devuelven 0 estaciones con temperatura), así que llamarlas es inútil.
#   - ISUSM: humedad de suelo de Iowa State (granjas agronómicas); sensores poco
#     fiables (mín −40°C en junio), ninguna estación importante.
# Se mantienen ASOS/COOP/USCRN, etc.
IEM_EXCLUDE_NETWORK_KEYWORDS = ("COCORAHS", "RWIS", "CLIMATE", "ISUSM")

# Las redes DCP/SCAN (plataformas automáticas de río/suelo/incendios) están
# plagadas de sensores rotos que cuelan máx/mín imposibles (Genoa 60°C, Kings
# Canyon −59°C…). Se DESCARTAN enteras EXCEPTO estas estaciones concretas, que
# son joyas fiables. Las redes que no contienen ninguna ni se llaman.
_IEM_DCP_SCAN_KEEP = frozenset({
    "CA_DCP|DEVC1",  # Death Valley · Furnace Creek Visitor Center
})
_IEM_DCP_SCAN_KEEP_NETWORKS = frozenset(s.split("|", 1)[0] for s in _IEM_DCP_SCAN_KEEP)


def _is_dcp_scan(network: str) -> bool:
    """Red IEM de plataforma automática (poco fiable para ranking)."""
    return "DCP" in network or network == "SCAN"


def iem_ranking_networks() -> List[str]:
    """Nombres de las redes IEM aptas para el ranking: ``online=1`` y cuyo país
    MAYORITARIO no está cubierto por un proveedor de ranking dedicado.

    Se filtra por la MAYORÍA de la red (no por estación) para no arrastrar redes
    de un país por una estación suelta de otro. Para EE.UU. (sin proveedor de
    ranking, pero con 324 redes) solo se admiten las ``*_ASOS``; el resto de
    países entran enteros. El país REAL de cada estación se resuelve luego por
    coordenadas (:func:`iem_station_countries`), no por la red, así que las redes
    globales ``WMO_BUFR_SRF`` (mayoría ``UN``) entran y sus estaciones se ubican
    por point-in-polygon.
    """
    from collections import Counter, defaultdict

    excluded = set(IEM_RANKING_EXCLUDE_COUNTRIES)
    with _connect() as connection:
        rows = connection.execute(
            "SELECT network_code, "
            "       COALESCE(NULLIF(TRIM(country), ''), 'UN') AS country, "
            "       COUNT(*) AS n "
            "FROM stations "
            "WHERE provider = 'IEM' AND online = 1 "
            "  AND network_code IS NOT NULL AND TRIM(network_code) <> '' "
            "GROUP BY network_code, country"
        ).fetchall()

    by_network: Dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        by_network[str(row["network_code"]).strip()][str(row["country"]).strip().upper()] += int(row["n"])

    out = []
    for network, counter in by_network.items():
        majority = counter.most_common(1)[0][0]
        if majority in excluded:
            continue
        # Descarta redes ruidosas (COCORAHS sin temperatura, RWIS de carretera).
        if any(kw in network for kw in IEM_EXCLUDE_NETWORK_KEYWORDS):
            continue
        # DCP/SCAN: solo se llaman las redes que contienen una joya whitelisteada
        # (p.ej. CA_DCP por Furnace Creek); el resto ni se piden.
        if _is_dcp_scan(network) and network not in _IEM_DCP_SCAN_KEEP_NETWORKS:
            continue
        out.append(network)
    return sorted(out)


# Fronteras de países cacheadas (STRtree shapely) para point-in-polygon.
_COUNTRY_BORDERS: Optional[tuple] = None


def _load_country_borders() -> Optional[tuple]:
    """Carga las fronteras (GeoJSON Natural Earth) y construye un STRtree.
    Devuelve ``(tree, geoms, isos)`` o ``None`` si falta el dataset o shapely.
    Cacheado en memoria (se carga una vez)."""
    global _COUNTRY_BORDERS
    if _COUNTRY_BORDERS is not None:
        return _COUNTRY_BORDERS or None
    try:
        import json

        from shapely.geometry import shape
        from shapely.strtree import STRtree

        with open(data_files.COUNTRY_BORDERS_PATH, encoding="utf-8") as handle:
            features = json.load(handle).get("features", [])
    except (OSError, ValueError, ImportError) as exc:
        logging.getLogger(__name__).warning("Fronteras de países no disponibles: %s", exc)
        _COUNTRY_BORDERS = ()  # marca "intentado y fallido" → no reintenta
        return None

    def _iso2(props: Dict[str, Any]) -> Optional[str]:
        # ISO_A2_EH trae el código correcto donde ISO_A2 es ``-99`` (Francia,
        # Noruega, territorios disputados).
        for key in ("ISO_A2_EH", "ISO_A2", "iso_a2"):
            value = str(props.get(key, "")).strip().upper()
            if value and value != "-99":
                return value
        return None

    geoms = []
    isos: List[Optional[str]] = []
    for feature in features:
        geom = feature.get("geometry")
        if not geom:
            continue
        geoms.append(shape(geom))
        isos.append(_iso2(feature.get("properties", {})))
    _COUNTRY_BORDERS = (STRtree(geoms), geoms, isos)
    return _COUNTRY_BORDERS


def country_for_point(lat: float, lon: float, *, tolerance: float = 0.35) -> Optional[str]:
    """ISO2 del país que contiene el punto (point-in-polygon). Si el punto cae
    fuera de todo polígono (costa/isla pequeña) usa el polígono más cercano
    dentro de ``tolerance`` grados. ``None`` si no hay dataset o no resuelve."""
    borders = _load_country_borders()
    if borders is None:
        return None
    tree, geoms, isos = borders
    try:
        from shapely.geometry import Point

        point = Point(float(lon), float(lat))
    except (TypeError, ValueError, ImportError):
        return None

    for idx in tree.query(point):
        if geoms[idx].contains(point):
            return isos[idx]
    # Fallback: polígono más cercano dentro de la tolerancia.
    best_iso: Optional[str] = None
    best_dist = float(tolerance)
    for idx in tree.query(point.buffer(tolerance)):
        dist = geoms[idx].distance(point)
        if dist < best_dist:
            best_dist = dist
            best_iso = isos[idx]
    return best_iso


_IEM_STATION_COUNTRIES_CACHE: Optional[Dict[str, str]] = None


def iem_station_countries() -> Dict[str, str]:
    """País (ISO2) de cada estación IEM de las redes aptas. Clave:
    ``"network_code|station_id"`` (id interno IEM).

    Resuelto POR ESTACIÓN, no por red (las redes IEM no siempre son de un solo
    país: USCRN = 231 US + 1 CA + 1 RS). Para las estaciones con país en el
    catálogo se usa ese (ya es per-estación y autoritativo); solo las de redes
    globales sin país (``WMO_BUFR_SRF``, ``country='UN'``) se ubican por
    POINT-IN-POLYGON sobre sus coordenadas. Las que caen en un país con
    proveedor de ranking dedicado (ES/FR/NO/IT) o que no resuelven se OMITEN.

    Cacheado en memoria (catálogo y fronteras estáticos durante el proceso → el
    job del ranking no recalcula nada cada ciclo)."""
    global _IEM_STATION_COUNTRIES_CACHE
    if _IEM_STATION_COUNTRIES_CACHE is not None:
        return _IEM_STATION_COUNTRIES_CACHE
    networks = set(iem_ranking_networks())
    excluded = set(IEM_RANKING_EXCLUDE_COUNTRIES)
    out: Dict[str, str] = {}
    with _connect() as connection:
        rows = connection.execute(
            "SELECT network_code, station_id, country, latitude, longitude FROM stations "
            "WHERE provider = 'IEM' AND online = 1 "
            "  AND latitude IS NOT NULL AND longitude IS NOT NULL "
            "  AND network_code IS NOT NULL AND station_id IS NOT NULL"
        ).fetchall()
    for row in rows:
        network = str(row["network_code"]).strip()
        if network not in networks:
            continue
        # DCP/SCAN: solo las estaciones whitelisteadas (Furnace Creek); el resto
        # se descartan (sensores rotos). Al no entrar en el mapa de países, el
        # adaptador del ranking las omite automáticamente.
        if _is_dcp_scan(network) and f"{network}|{row['station_id']}" not in _IEM_DCP_SCAN_KEEP:
            continue
        catalog_country = str(row["country"] or "").strip().upper()
        if catalog_country and catalog_country != "UN":
            iso = catalog_country
        else:
            # Solo las globales sin país (WMO) pagan el point-in-polygon.
            iso = country_for_point(row["latitude"], row["longitude"])
        if iso and iso not in excluded:
            out[f"{network}|{row['station_id']}"] = iso
    _IEM_STATION_COUNTRIES_CACHE = out
    return out


_TZ_COUNTRY_CACHE: Optional[Dict[str, str]] = None


def _timezone_country_map() -> Dict[str, str]:
    """Mapa ``IANA tz → ISO2`` por voto mayoritario del catálogo (excluye el
    centinela ``UN``). Sirve para aproximar el país del usuario desde la zona
    horaria del navegador cuando no hay geolocalización precisa."""
    global _TZ_COUNTRY_CACHE
    if _TZ_COUNTRY_CACHE is not None:
        return _TZ_COUNTRY_CACHE
    from collections import Counter, defaultdict

    votes: Dict[str, Counter] = defaultdict(Counter)
    with _connect() as connection:
        rows = connection.execute(
            "SELECT timezone, country FROM stations "
            "WHERE timezone IS NOT NULL AND TRIM(timezone) <> '' "
            "  AND country IS NOT NULL AND TRIM(country) NOT IN ('', 'UN')"
        ).fetchall()
    for row in rows:
        votes[str(row["timezone"]).strip()][_normalize_country_code(row["country"])] += 1
    _TZ_COUNTRY_CACHE = {
        tz: counter.most_common(1)[0][0] for tz, counter in votes.items()
    }
    return _TZ_COUNTRY_CACHE


def country_for_timezone(timezone: str) -> Optional[str]:
    """ISO2 aproximado para una zona horaria IANA, o ``None`` si no se conoce."""
    tz = str(timezone or "").strip()
    return _timezone_country_map().get(tz) if tz else None


def provider_counts() -> Dict[str, int]:
    placeholders = ",".join("?" for _ in CATALOG_PROVIDERS)
    with _connect() as connection:
        rows = connection.execute(
            f"""
            SELECT provider, COUNT(*) AS station_count
            FROM stations s
            LEFT JOIN station_visibility_overrides svo USING(station_pk)
            WHERE provider IN ({placeholders})
              AND COALESCE(svo.hidden, 0) = 0
            GROUP BY provider
            """,
            CATALOG_PROVIDERS,
        ).fetchall()
    counts = {row["provider"]: int(row["station_count"]) for row in rows}
    return {provider: counts.get(provider, 0) for provider in CATALOG_PROVIDERS}


def country_counts(*, providers: Optional[List[str]] = None) -> Dict[str, int]:
    wanted_providers = [
        provider for provider in (
            str(value).strip().upper()
            for value in (providers or list(CATALOG_PROVIDERS))
        )
        if provider in CATALOG_PROVIDERS
    ]
    if not wanted_providers:
        return {}
    placeholders = ",".join("?" for _ in wanted_providers)
    with _connect() as connection:
        country_expr = _effective_country_sql()
        rows = connection.execute(
            f"""
            SELECT {country_expr} AS country,
                   COUNT(*) AS station_count
            FROM stations s
            LEFT JOIN station_visibility_overrides svo USING(station_pk)
            WHERE s.provider IN ({placeholders})
              AND COALESCE(svo.hidden, 0) = 0
            GROUP BY {country_expr}
            ORDER BY station_count DESC, country
            """,
            wanted_providers,
        ).fetchall()
    return {row["country"]: int(row["station_count"]) for row in rows}


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rad = math.radians
    dlat = rad(lat2 - lat1)
    dlon = rad(lon2 - lon1)
    value = (
        math.sin(dlat / 2) ** 2
        + math.cos(rad(lat1)) * math.cos(rad(lat2)) * math.sin(dlon / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(value)))


def search_near(
    lat: float,
    lon: float,
    *,
    radius_km: float = 50.0,
    providers: Optional[List[str]] = None,
    countries: Optional[List[str]] = None,
    sensors: Optional[List[str]] = None,
    has_historical: bool = False,
    hide_historical_only: bool = False,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Search the RTree and apply exact distance and sensor filters."""
    wanted_providers = [
        provider for provider in (
            str(value).strip().upper()
            for value in (providers or list(CATALOG_PROVIDERS))
        )
        if provider in CATALOG_PROVIDERS
    ]
    if not wanted_providers:
        return []
    wanted_countries = [
        _normalize_country_code(value) for value in (countries or [])
        if str(value).strip()
    ]
    wanted_sensors = [
        str(value).strip().lower() for value in (sensors or [])
        if str(value).strip().lower() in SENSOR_KEYS
    ]

    latitude_delta = radius_km / 110.574
    longitude_scale = max(0.01, abs(math.cos(math.radians(float(lat)))))
    longitude_delta = radius_km / (111.320 * longitude_scale)
    provider_placeholders = ",".join("?" for _ in wanted_providers)
    country_clause = ""
    country_parameters: tuple[str, ...] = ()
    if wanted_countries:
        country_placeholders = ",".join("?" for _ in wanted_countries)
        country_clause = f" AND {_effective_country_sql()} IN ({country_placeholders})"
        country_parameters = tuple(wanted_countries)
    sensor_clauses = [f"ss.{key} = 1" for key in wanted_sensors]
    extra_where = "".join(f" AND {clause}" for clause in sensor_clauses)
    historical_clause = " AND s.has_historical = 1" if has_historical else ""
    historical_only_clause = (
        " AND NOT (s.provider = 'IEM' AND s.has_historical = 1 AND s.online = 0)"
        if hide_historical_only else ""
    )

    query = _SELECT + f"""
    JOIN station_rtree r USING(station_pk)
    WHERE r.min_latitude >= ? AND r.max_latitude <= ?
      AND r.min_longitude >= ? AND r.max_longitude <= ?
      AND s.provider IN ({provider_placeholders})
      AND COALESCE(svo.hidden, 0) = 0
      {historical_clause}
      {historical_only_clause}
      {country_clause}
      {extra_where}
    """
    parameters = (
        float(lat) - latitude_delta, float(lat) + latitude_delta,
        float(lon) - longitude_delta, float(lon) + longitude_delta,
        *wanted_providers,
        *country_parameters,
    )
    with _connect() as connection:
        rows = connection.execute(query, parameters).fetchall()

    results = []
    for row in rows:
        distance = _haversine_km(float(lat), float(lon), row["latitude"], row["longitude"])
        if distance <= radius_km:
            results.append({**_record(row), "distance_km": round(distance, 2)})
    results.sort(key=lambda item: item["distance_km"])
    return results[:max(1, int(limit))]


def search_catalog(
    *,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    providers: Optional[List[str]] = None,
    countries: Optional[List[str]] = None,
    sensors: Optional[List[str]] = None,
    has_historical: bool = False,
    hide_historical_only: bool = False,
    limit: int = 50000,
) -> List[Dict[str, Any]]:
    """Return visible catalog stations filtered by metadata, without spatial clipping."""
    wanted_providers = [
        provider for provider in (
            str(value).strip().upper()
            for value in (providers or list(CATALOG_PROVIDERS))
        )
        if provider in CATALOG_PROVIDERS
    ]
    wanted_countries = [
        _normalize_country_code(value) for value in (countries or [])
        if str(value).strip()
    ]
    if not wanted_providers or not wanted_countries:
        return []
    wanted_sensors = [
        str(value).strip().lower() for value in (sensors or [])
        if str(value).strip().lower() in SENSOR_KEYS
    ]

    provider_placeholders = ",".join("?" for _ in wanted_providers)
    country_placeholders = ",".join("?" for _ in wanted_countries)
    sensor_clauses = [f"ss.{key} = 1" for key in wanted_sensors]
    extra_where = "".join(f" AND {clause}" for clause in sensor_clauses)
    historical_clause = " AND s.has_historical = 1" if has_historical else ""
    historical_only_clause = (
        " AND NOT (s.provider = 'IEM' AND s.has_historical = 1 AND s.online = 0)"
        if hide_historical_only else ""
    )
    query = _SELECT + f"""
    WHERE s.provider IN ({provider_placeholders})
      AND COALESCE(svo.hidden, 0) = 0
      {historical_clause}
      {historical_only_clause}
      AND {_effective_country_sql()} IN ({country_placeholders})
      AND s.latitude IS NOT NULL
      AND s.longitude IS NOT NULL
      {extra_where}
    """
    parameters = (*wanted_providers, *wanted_countries)
    with _connect() as connection:
        rows = connection.execute(query, parameters).fetchall()

    results = []
    has_distance = lat is not None and lon is not None
    for row in rows:
        record = _record(row)
        if has_distance:
            record["distance_km"] = round(_haversine_km(float(lat), float(lon), row["latitude"], row["longitude"]), 2)
        else:
            record["distance_km"] = 0.0
        results.append(record)
    results.sort(key=lambda item: (item["distance_km"], item["provider"], item["station_id"]))
    return results[:max(1, int(limit))]


def raw_metadata(provider: str, station_id: str) -> Optional[Dict[str, Any]]:
    """Expose preserved provider metadata for maintenance and future migrations."""
    provider_id = str(provider or "").strip().upper()
    station_key = str(station_id or "").strip()
    network = ""
    if provider_id == "IEM" and "|" in station_key:
        network, station_key = (part.strip() for part in station_key.split("|", 1))
    network_clause = "AND s.network_code = ? COLLATE NOCASE" if network else ""
    parameters: tuple[str, ...] = (
        (provider_id, station_key, network) if network else (provider_id, station_key)
    )
    with _connect() as connection:
        row = connection.execute(
            f"""
            SELECT r.raw_json
            FROM stations s
            JOIN station_inventory_records r ON r.record_pk = s.source_record_pk
            WHERE s.provider = ? AND s.station_id = ? COLLATE NOCASE
            {network_clause}
            LIMIT 1
            """,
            parameters,
        ).fetchone()
    return json.loads(row["raw_json"]) if row is not None else None
