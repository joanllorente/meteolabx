"""SQLite-backed normalized station catalog used by FastAPI."""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import data_files


logger = logging.getLogger(__name__)

SENSOR_KEYS = (
    "thermometer", "hygrometer", "barometer", "anemometer",
    "wind_vane", "rain_gauge", "pyranometer", "uv",
)

CONNECTABLE_PROVIDERS = (
    "AEMET", "METEOCAT", "EUSKALMET", "FROST", "METEOFRANCE",
    "METEOGALICIA", "NWS", "POEM", "METOFFICE", "METEOHUB_IT",
    "IPMA", "GEOSPHERE", "SMHI", "ECCC", "IEM", "CLIMANTARTIDE",
    "WINDY", "NETATMO",
)
CATALOG_PROVIDERS = CONNECTABLE_PROVIDERS
PWS_CATALOG_PROVIDERS = ("WINDY", "NETATMO")
OFFICIAL_CATALOG_PROVIDERS = tuple(
    provider for provider in CATALOG_PROVIDERS
    if provider not in PWS_CATALOG_PROVIDERS
)

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
    "IPMA": "PT",
    "GEOSPHERE": "AT",
    "SMHI": "SE",
    "ECCC": "CA",
    "CLIMANTARTIDE": "AQ",
}

HISTORICAL_PROVIDER_IDS = {"AEMET", "METEOCAT", "METEOFRANCE", "METEOGALICIA"}
IEM_HISTORICAL_NETWORK_MARKERS = ("ASOS", "AWOS", "METAR")

IEM_COUNTRY_TIMEZONE_OVERRIDES = {
    ("ES", "Europe/Paris"): "FR",
}

COUNTRY_CODE_ALIASES = {
    "RQ": "PR",
    "TU": "TR",
    "DR": "DO",  # FIPS República Dominicana → ISO (evita país duplicado)
    "NN": "SX",  # FIPS Sint Maarten (RAOB TNCM) → ISO
}

# Códigos de "país" del catálogo que NO son un país real y se resuelven por
# coordenadas (point-in-polygon), igual que las redes globales: ``UN`` es el
# centinela de red global (WMO) y ``AN`` son las Antillas Neerlandesas,
# disueltas en 2010 en BQ/CW/SX → no existe alias 1:1, la isla decide.
COUNTRY_CODES_RESOLVED_BY_COORDS = {"UN", "AN"}


def _catalog_country(provider: Any, country: Any, latitude: Any) -> str:
    """País efectivo para respuestas del catálogo.

    Las estaciones antárticas de la red global WMO llegan con ``country=UN``.
    El ranking ya las resuelve por point-in-polygon, pero los filtros SQL del
    mapa necesitan una regla barata y determinista antes de devolver filas.
    Todo punto al sur de 60° S pertenece al ámbito antártico (AQ).
    """
    provider_id = str(provider or "").strip().upper()
    code = _normalize_country_code(country)
    try:
        lat = float(latitude)
    except (TypeError, ValueError):
        lat = None
    if (
        provider_id == "IEM"
        and code in COUNTRY_CODES_RESOLVED_BY_COORDS
        and lat is not None
        and lat <= -60.0
    ):
        return "AQ"
    return code


def _connect() -> sqlite3.Connection:
    path = Path(data_files.STATIONS_DB_PATH).resolve()
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _connect_pws() -> sqlite3.Connection:
    path = Path(data_files.PWS_STATIONS_DB_PATH).resolve()
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


@lru_cache(maxsize=1)
def hidden_station_identities() -> frozenset[tuple[str, str]]:
    """Identidades ocultas por decisiones de deduplicación del catálogo.

    IEM necesita incluir la red en el identificador público para evitar
    colisiones entre inventarios. El resultado se cachea porque también se
    consulta al construir el campo térmico, que puede contener decenas de
    miles de observaciones.
    """
    try:
        with _connect() as connection:
            rows = connection.execute(
                """
                SELECT s.provider, s.network_code, s.station_id
                FROM stations s
                JOIN station_visibility_overrides svo USING(station_pk)
                WHERE svo.hidden = 1
                """
            ).fetchall()
    except (OSError, sqlite3.Error):
        return frozenset()

    identities: set[tuple[str, str]] = set()
    for row in rows:
        provider = str(row["provider"] or "").strip().upper()
        station_id = str(row["station_id"] or "").strip()
        network = str(row["network_code"] or "").strip()
        if provider == "IEM" and network:
            station_id = f"{network}|{station_id}"
        identities.add((provider, station_id))
    return frozenset(identities)


def is_station_hidden(provider: Any, station_id: Any) -> bool:
    """Indica si una identidad pública está oculta en el catálogo SQLite."""
    identity = (
        str(provider or "").strip().upper(),
        str(station_id or "").strip(),
    )
    return identity in hidden_station_identities()


def _pws_fresh_cutoff_iso(hours: int = 3) -> str:
    return datetime.fromtimestamp(
        datetime.now(timezone.utc).timestamp() - max(1, int(hours)) * 3600,
        timezone.utc,
    ).isoformat().replace("+00:00", "Z")


def _pws_record(row: sqlite3.Row) -> Dict[str, Any]:
    lat = row["latitude"]
    lon = row["longitude"]
    country = row["country"] if "country" in row.keys() else None
    if not country and lat is not None and lon is not None:
        country = country_for_point(float(lat), float(lon))
    sensors: Dict[str, bool] = {}
    if row["temp_height_m"] is not None:
        sensors["thermometer"] = True
    if row["wind_height_m"] is not None:
        sensors["anemometer"] = True
        sensors["wind_vane"] = True
    return {
        "provider": "WINDY",
        "network": "PWS",
        "station_id": str(row["station_id"]),
        "name": str(row["name"]),
        "lat": lat,
        "lon": lon,
        "elevation": row["elevation_m"],
        "tz": None,
        "country": country or "UNSPECIFIED",
        "region": None,
        "locality": str(row["station_type"] or ""),
        "connectable": True,
        "has_historical": False,
        "is_historical_only": False,
        "manual": False,
        "sensors": sensors or None,
    }


def _pws_matches_sensors(record: Dict[str, Any], sensors: Optional[List[str]]) -> bool:
    wanted = [sensor for sensor in (sensors or []) if sensor in SENSOR_KEYS]
    if not wanted:
        return True
    available = record.get("sensors")
    return isinstance(available, dict) and all(bool(available.get(sensor)) for sensor in wanted)


def _pws_get_station(station_id: str) -> Optional[Dict[str, Any]]:
    if not Path(data_files.PWS_STATIONS_DB_PATH).exists():
        return None
    with _connect_pws() as connection:
        row = connection.execute(
            "SELECT * FROM pws_stations WHERE station_id = ? COLLATE NOCASE LIMIT 1",
            (str(station_id or "").strip(),),
        ).fetchone()
    return _pws_record(row) if row is not None else None


def _pws_find_by_name_slug(slug: str) -> Optional[Dict[str, Any]]:
    if not Path(data_files.PWS_STATIONS_DB_PATH).exists():
        return None
    from utils.station_slug import slugify

    target = slugify(slug)
    if not target:
        return None
    with _connect_pws() as connection:
        rows = connection.execute(
            "SELECT * FROM pws_stations ORDER BY station_id COLLATE NOCASE"
        ).fetchall()
    for row in rows:
        if slugify(row["name"]) == target:
            return _pws_record(row)
    return None


def _pws_search_near(
    lat: float,
    lon: float,
    *,
    radius_km: float,
    countries: Optional[List[str]],
    sensors: Optional[List[str]],
    limit: int,
) -> List[Dict[str, Any]]:
    if not Path(data_files.PWS_STATIONS_DB_PATH).exists():
        return []
    latitude_delta = radius_km / 110.574
    longitude_scale = max(0.01, abs(math.cos(math.radians(float(lat)))))
    longitude_delta = radius_km / (111.320 * longitude_scale)
    with _connect_pws() as connection:
        country_clause = ""
        country_parameters: tuple[str, ...] = ()
        wanted_countries = {
            _normalize_country_code(country) for country in (countries or []) if str(country).strip()
        }
        if wanted_countries:
            placeholders = ",".join("?" for _ in wanted_countries)
            country_clause = f" AND UPPER(COALESCE(s.country, '')) IN ({placeholders})"
            country_parameters = tuple(sorted(wanted_countries))
        rows = connection.execute(
            f"""
            SELECT s.*
            FROM pws_stations s
            JOIN pws_station_rtree r USING(station_pk)
            WHERE r.min_latitude >= ? AND r.max_latitude <= ?
              AND r.min_longitude >= ? AND r.max_longitude <= ?
              AND s.last_observation_time >= ?
              {country_clause}
            """,
            (
                float(lat) - latitude_delta, float(lat) + latitude_delta,
                float(lon) - longitude_delta, float(lon) + longitude_delta,
                _pws_fresh_cutoff_iso(),
                *country_parameters,
            ),
        ).fetchall()
    results = []
    for row in rows:
        distance = _haversine_km(float(lat), float(lon), row["latitude"], row["longitude"])
        if distance > radius_km:
            continue
        record = _pws_record(row)
        if not _pws_matches_sensors(record, sensors):
            continue
        results.append({**record, "distance_km": round(distance, 2)})
    results.sort(key=lambda item: item["distance_km"])
    return results[:max(1, int(limit))]


def _pws_search_catalog(
    *,
    lat: Optional[float],
    lon: Optional[float],
    countries: List[str],
    sensors: Optional[List[str]],
    limit: int,
) -> List[Dict[str, Any]]:
    if not Path(data_files.PWS_STATIONS_DB_PATH).exists() or not countries:
        return []
    wanted_countries = sorted({_normalize_country_code(country) for country in countries})
    placeholders = ",".join("?" for _ in wanted_countries)
    with _connect_pws() as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM pws_stations
            WHERE UPPER(COALESCE(country, '')) IN ({placeholders})
              AND last_observation_time >= ?
              AND latitude IS NOT NULL
              AND longitude IS NOT NULL
            """,
            (*wanted_countries, _pws_fresh_cutoff_iso()),
        ).fetchall()
    has_distance = lat is not None and lon is not None
    results = []
    for row in rows:
        record = _pws_record(row)
        if not _pws_matches_sensors(record, sensors):
            continue
        record["distance_km"] = (
            round(_haversine_km(float(lat), float(lon), row["latitude"], row["longitude"]), 2)
            if has_distance else 0.0
        )
        results.append(record)
    results.sort(key=lambda item: item["station_id"].casefold())
    return results[:max(1, int(limit))]


def _connect_netatmo() -> sqlite3.Connection:
    path = Path(data_files.NETATMO_PWS_STATIONS_DB_PATH).resolve()
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _netatmo_record(row: sqlite3.Row) -> Dict[str, Any]:
    sensors = {key: True for key in SENSOR_KEYS if row[key]}
    return {
        "provider": "NETATMO",
        "network": "PWS",
        "station_id": str(row["station_id"]),
        "name": str(row["name"]),
        "lat": row["latitude"],
        "lon": row["longitude"],
        "elevation": row["elevation_m"],
        "tz": row["timezone"],
        "country": _normalize_country_code(row["country"]),
        "region": None,
        "locality": str(row["city"] or ""),
        "connectable": True,
        "has_historical": False,
        "is_historical_only": False,
        "manual": False,
        "sensors": sensors or None,
    }


def _netatmo_inactive_ids() -> frozenset[str]:
    # Import diferido: netatmo importa este módulo al conectar una estación.
    from server.services import netatmo

    return netatmo.temporarily_inactive_station_ids()


def _netatmo_get_station(station_id: str) -> Optional[Dict[str, Any]]:
    if not Path(data_files.NETATMO_PWS_STATIONS_DB_PATH).exists():
        return None
    with _connect_netatmo() as connection:
        row = connection.execute(
            "SELECT * FROM netatmo_stations WHERE station_id = ? COLLATE NOCASE LIMIT 1",
            (str(station_id or "").strip(),),
        ).fetchone()
    return _netatmo_record(row) if row is not None else None


def _netatmo_find_by_name_slug(slug: str) -> Optional[Dict[str, Any]]:
    if not Path(data_files.NETATMO_PWS_STATIONS_DB_PATH).exists():
        return None
    from utils.station_slug import slugify

    target = slugify(slug)
    if not target:
        return None
    with _connect_netatmo() as connection:
        rows = connection.execute(
            "SELECT * FROM netatmo_stations ORDER BY station_id COLLATE NOCASE"
        ).fetchall()
    for row in rows:
        if slugify(row["name"]) == target:
            return _netatmo_record(row)
    return None


def _netatmo_search_near(
    lat: float,
    lon: float,
    *,
    radius_km: float,
    countries: Optional[List[str]],
    sensors: Optional[List[str]],
    limit: int,
) -> List[Dict[str, Any]]:
    if not Path(data_files.NETATMO_PWS_STATIONS_DB_PATH).exists():
        return []
    latitude_delta = radius_km / 110.574
    longitude_scale = max(0.01, abs(math.cos(math.radians(float(lat)))))
    longitude_delta = radius_km / (111.320 * longitude_scale)
    with _connect_netatmo() as connection:
        country_clause = ""
        country_parameters: tuple[str, ...] = ()
        wanted_countries = {
            _normalize_country_code(country) for country in (countries or []) if str(country).strip()
        }
        if wanted_countries:
            placeholders = ",".join("?" for _ in wanted_countries)
            country_clause = f" AND UPPER(COALESCE(s.country, '')) IN ({placeholders})"
            country_parameters = tuple(sorted(wanted_countries))
        rows = connection.execute(
            f"""
            SELECT s.*
            FROM netatmo_stations s
            JOIN netatmo_station_rtree r USING(station_pk)
            WHERE r.min_latitude >= ? AND r.max_latitude <= ?
              AND r.min_longitude >= ? AND r.max_longitude <= ?
              {country_clause}
            """,
            (
                float(lat) - latitude_delta, float(lat) + latitude_delta,
                float(lon) - longitude_delta, float(lon) + longitude_delta,
                *country_parameters,
            ),
        ).fetchall()
    inactive_ids = _netatmo_inactive_ids()
    results = []
    for row in rows:
        if str(row["station_id"]).lower() in inactive_ids:
            continue
        distance = _haversine_km(float(lat), float(lon), row["latitude"], row["longitude"])
        if distance > radius_km:
            continue
        record = _netatmo_record(row)
        if not _pws_matches_sensors(record, sensors):
            continue
        results.append({**record, "distance_km": round(distance, 2)})
    results.sort(key=lambda item: item["distance_km"])
    return results[:max(1, int(limit))]


def _netatmo_search_catalog(
    *,
    lat: Optional[float],
    lon: Optional[float],
    countries: List[str],
    sensors: Optional[List[str]],
    limit: int,
) -> List[Dict[str, Any]]:
    if not Path(data_files.NETATMO_PWS_STATIONS_DB_PATH).exists() or not countries:
        return []
    wanted_countries = sorted({_normalize_country_code(country) for country in countries})
    placeholders = ",".join("?" for _ in wanted_countries)
    with _connect_netatmo() as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM netatmo_stations
            WHERE UPPER(COALESCE(country, '')) IN ({placeholders})
              AND latitude IS NOT NULL
              AND longitude IS NOT NULL
            """,
            wanted_countries,
        ).fetchall()
    has_distance = lat is not None and lon is not None
    inactive_ids = _netatmo_inactive_ids()
    results = []
    for row in rows:
        if str(row["station_id"]).lower() in inactive_ids:
            continue
        record = _netatmo_record(row)
        if not _pws_matches_sensors(record, sensors):
            continue
        record["distance_km"] = (
            round(_haversine_km(float(lat), float(lon), row["latitude"], row["longitude"]), 2)
            if has_distance else 0.0
        )
        results.append(record)
    results.sort(key=lambda item: item["station_id"].casefold())
    return results[:max(1, int(limit))]


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
    # Archivada = tiene histórico pero está offline, sea cual sea el
    # proveedor (IEM, red KLIMA de GeoSphere…). ``online`` NULL (proveedor
    # sin el flag) no cuenta como offline.
    return bool(bool(row["has_historical"]) and row["online"] == 0)


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
    country = _catalog_country(row["provider"], country, row["latitude"])
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
        # Estación de observador MANUAL (IEM COOP/CoCoRaHS: lecturas a mano
        # una vez al día) frente a automática. Catálogos antiguos sin la
        # columna caen a False (todas las redes del resto de proveedores
        # son automáticas).
        "manual": bool(row["manual"]) if "manual" in row.keys() else False,
        "sensors": _sensors(row),
    }


def _effective_country_sql() -> str:
    raw_country = "UPPER(COALESCE(NULLIF(TRIM(s.country), ''), 'UNSPECIFIED'))"
    # Las WMO globales usan ``UN`` aunque sus coordenadas sean antárticas.
    # Resolver AQ aquí hace que country_counts/search_catalog/search_near
    # coincidan con el point-in-polygon que ya usa el ranking.
    antarctica_case = (
        "WHEN s.provider = 'IEM' "
        f"AND {raw_country} IN ('UN', 'AN') "
        "AND s.latitude <= -60.0 THEN 'AQ'"
    )
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
        f"CASE {antarctica_case} {iem_cases} {cases} {alias_cases} "
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


def _meteohub_canonical_id(station_id: str) -> str:
    """Deja un identificador de MeteoHub como lo guarda el catálogo.

    Su identificador es ``red|lat|lon|nombre``, y cada fuente lo escribe a su
    manera: el catálogo redondea las coordenadas a cinco decimales y convierte
    el nombre en slug —``dpcn-puglia|41.88050|16.17583|vieste``—, mientras el
    ranking usa lo que trae el feed —``dpcn-puglia|41.8805|16.17583|vieste``, y
    ``buttigliera d'asti`` con espacio y apóstrofo—. Eran la misma estación
    escrita de dos formas, así que pulsarla en el ranking llevaba a un 404: 21
    de los 30 enlaces del ranking italiano estaban rotos.
    """
    from utils.station_slug import slugify

    parts = str(station_id or "").split("|")
    if len(parts) < 4:
        return station_id
    try:
        lat = f"{float(parts[1]):.5f}"
        lon = f"{float(parts[2]):.5f}"
    except (TypeError, ValueError):
        return station_id
    name = slugify("|".join(parts[3:]))
    return f"{parts[0].strip().lower()}|{lat}|{lon}|{name}"


def get_station(provider: str, station_id: str) -> Optional[Dict[str, Any]]:
    """Return one connectable station by case-insensitive provider identity."""
    provider = str(provider or "").strip().upper()
    station_id = str(station_id or "").strip()
    if provider == "WINDY":
        return _pws_get_station(station_id)
    if provider == "NETATMO":
        return _netatmo_get_station(station_id)
    if provider not in CATALOG_PROVIDERS or not station_id:
        return None
    if provider == "METEOHUB_IT":
        station_id = _meteohub_canonical_id(station_id)

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
    if row is not None:
        return _record(row)

    # MeteoHub publica en su feed estaciones que el inventario local todavía
    # no tiene, y su identificador ya lleva dentro todo lo que hace falta para
    # abrirla: red, coordenadas y nombre. Rechazarlas con un 404 era negar una
    # estación que el ranking acababa de enseñar y que el servicio de
    # observación sabe consultar.
    if provider == "METEOHUB_IT":
        return _meteohub_station_from_id(station_id)
    return None


def _meteohub_station_from_id(station_id: str) -> Optional[Dict[str, Any]]:
    parts = str(station_id or "").split("|")
    if len(parts) < 4:
        return None
    try:
        lat, lon = float(parts[1]), float(parts[2])
    except (TypeError, ValueError):
        return None
    name = parts[3].replace("-", " ").strip()
    return {
        "provider": "METEOHUB_IT",
        "station_id": station_id,
        "name": name.title() if name.islower() else name,
        "locality": "",
        "region": "",
        "country": "IT",
        "lat": lat,
        "lon": lon,
        "elevation": None,
        "tz": "Europe/Rome",
        "manual": False,
        "online": True,
        "has_historical": False,
        "sensors": {},
        "url_slug": "",
        "distance_km": 0.0,
    }


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
    if provider == "WINDY":
        return _pws_find_by_name_slug(target)
    if provider == "NETATMO":
        return _netatmo_find_by_name_slug(target)
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


# ---------------------------------------------------------------------------
# Slug de URL indexable (``/{idioma}/observation/{slug}``)
# ---------------------------------------------------------------------------

_URL_SLUG_SELECT = """
SELECT s.*, ss.station_pk AS sensor_station_pk,
       ss.thermometer, ss.hygrometer, ss.barometer, ss.anemometer,
       ss.wind_vane, ss.rain_gauge, ss.pyranometer, ss.uv,
       u.url_slug, u.indexable
FROM station_url_slugs u
JOIN stations s USING(station_pk)
LEFT JOIN station_sensors ss USING(station_pk)
"""


def _url_slug_table_missing(error: sqlite3.OperationalError) -> bool:
    return "no such table" in str(error).lower()


def find_by_url_slug(slug: str) -> Optional[Dict[str, Any]]:
    """Ficha de estación a partir del slug de su URL indexable.

    El slug (``barcelona-drassanes-0201x``) sale de ``utils.station_url`` y
    vive materializado en ``station_url_slugs``; lo escribe
    ``scripts/build_station_url_slugs.py`` al arrancar el servicio.

    Devuelve el registro habitual más ``url_slug`` e ``indexable``. Las
    estaciones no indexables —ocultas, sin coordenadas o fuera de servicio—
    se siguen resolviendo: sus URLs pueden estar ya en el índice de Google y
    preferimos servir la ficha con ``noindex`` antes que un 404. Quien
    consuma esto decide qué hacer con la bandera.
    """
    target = str(slug or "").strip().lower()
    if not target:
        return None
    with _connect() as connection:
        try:
            row = connection.execute(
                _URL_SLUG_SELECT + " WHERE u.url_slug = ? LIMIT 1", (target,)
            ).fetchone()
        except sqlite3.OperationalError as error:
            if not _url_slug_table_missing(error):
                raise
            logger.warning(
                "station_url_slugs no existe todavía; ejecuta "
                "scripts/build_station_url_slugs.py"
            )
            return None
    if row is None:
        return None
    record = _record(row)
    record["url_slug"] = row["url_slug"]
    record["indexable"] = bool(row["indexable"])
    # País TAL CUAL está en el catálogo, sin las correcciones que ``_record``
    # aplica por proveedor. El generador SEO decide con este valor en qué
    # idiomas existe cada ficha, y las alternates ya indexadas dependen de él.
    record["catalog_country"] = str(row["country"] or "").strip().upper()
    return record


def url_slug_for(provider: str, station_id: str) -> Optional[str]:
    """Slug de URL de una estación ya identificada (para canonical y enlaces)."""
    provider = str(provider or "").strip().upper()
    station_id = str(station_id or "").strip()
    if not provider or not station_id:
        return None
    with _connect() as connection:
        try:
            row = connection.execute(
                """
                SELECT u.url_slug FROM station_url_slugs u
                JOIN stations s USING(station_pk)
                WHERE s.provider = ? AND s.station_id = ? COLLATE NOCASE
                LIMIT 1
                """,
                (provider, station_id),
            ).fetchone()
        except sqlite3.OperationalError as error:
            if not _url_slug_table_missing(error):
                raise
            return None
    return row["url_slug"] if row is not None else None


def indexable_url_slugs(
    *, offset: int = 0, limit: int = 50_000
) -> List[Dict[str, Any]]:
    """Página del catálogo indexable, ordenada, para construir el sitemap."""
    with _connect() as connection:
        try:
            rows = connection.execute(
                """
                SELECT u.url_slug, s.provider, s.country
                FROM station_url_slugs u
                JOIN stations s USING(station_pk)
                WHERE u.indexable = 1
                ORDER BY u.url_slug
                LIMIT ? OFFSET ?
                """,
                (int(limit), int(offset)),
            ).fetchall()
        except sqlite3.OperationalError as error:
            if not _url_slug_table_missing(error):
                raise
            return []
    return [
        {
            "url_slug": row["url_slug"],
            "provider": row["provider"],
            # Sin normalizar: el sitemap tiene que elegir los mismos idiomas
            # que eligió el generador estático, que lee esta misma columna.
            "catalog_country": str(row["country"] or "").strip().upper(),
        }
        for row in rows
    ]


def catalog_details_for(
    pairs: List[tuple[str, str]]
) -> Dict[tuple[str, str], Dict[str, Any]]:
    """Slug, región y localidad de varias estaciones en una sola consulta.

    Lo usa el ranking: sus filas identifican la estación por el ID de su red
    y necesita saber a qué URL enlazar y de qué provincia o comunidad es. Una
    consulta por fila multiplicaría por cuarenta el coste de pintar la tabla.

    El ``LEFT JOIN`` con la tabla de slugs es intencionado: las redes que no
    se publican siguen devolviendo región y localidad, solo que sin slug.
    """
    wanted = [
        (str(provider or "").strip().upper(), str(station_id or "").strip())
        for provider, station_id in pairs
    ]
    wanted = [pair for pair in wanted if pair[0] and pair[1]]
    if not wanted:
        return {}

    placeholders = ",".join("(?, ?)" for _ in wanted)
    flat: List[str] = [value for pair in wanted for value in pair]
    with _connect() as connection:
        try:
            rows = connection.execute(
                f"""
                SELECT s.provider, s.station_id, s.region, s.locality,
                       s.elevation_m, u.url_slug
                FROM stations s
                LEFT JOIN station_url_slugs u USING(station_pk)
                WHERE (s.provider, s.station_id) IN (VALUES {placeholders})
                """,
                flat,
            ).fetchall()
        except sqlite3.OperationalError as error:
            if not _url_slug_table_missing(error):
                raise
            return {}
    return {
        (row["provider"], row["station_id"]): {
            "url_slug": row["url_slug"] or "",
            "region": row["region"] or "",
            "locality": row["locality"] or "",
            "elevation": row["elevation_m"],
        }
        for row in rows
    }


def url_slugs_for(pairs: List[tuple[str, str]]) -> Dict[tuple[str, str], str]:
    """Solo los slugs, para quien no necesita el resto de la ficha."""
    return {
        key: details["url_slug"]
        for key, details in catalog_details_for(pairs).items()
        if details["url_slug"]
    }


def indexable_stations_near(
    lat: float,
    lon: float,
    *,
    exclude: Optional[tuple[str, str]] = None,
    limit: int = 6,
) -> List[Dict[str, Any]]:
    """Las estaciones indexables más cercanas a un punto, con su slug.

    Alimenta el bloque de «estaciones cercanas» de cada ficha, que es el
    enlazado interno que hoy reparte autoridad entre las páginas. Se busca
    en una caja creciente en vez de recorrer el catálogo entero: en zonas
    densas la primera vuelta ya trae de sobra.
    """
    try:
        origin_lat = float(lat)
        origin_lon = float(lon)
    except (TypeError, ValueError):
        return []

    excluded_provider, excluded_station = (exclude or ("", ""))
    found: List[Dict[str, Any]] = []
    with _connect() as connection:
        for span in (0.35, 1.0, 3.0, 8.0):
            try:
                rows = connection.execute(
                    """
                    SELECT s.provider, s.station_id, s.name, s.latitude, s.longitude,
                           u.url_slug
                    FROM station_url_slugs u
                    JOIN stations s USING(station_pk)
                    WHERE u.indexable = 1
                      AND s.latitude BETWEEN ? AND ?
                      AND s.longitude BETWEEN ? AND ?
                    """,
                    (
                        origin_lat - span, origin_lat + span,
                        origin_lon - span, origin_lon + span,
                    ),
                ).fetchall()
            except sqlite3.OperationalError as error:
                if not _url_slug_table_missing(error):
                    raise
                return []
            found = [
                {
                    "provider": row["provider"],
                    "station_id": row["station_id"],
                    "name": row["name"],
                    "url_slug": row["url_slug"],
                    "distance_km": _haversine_km(
                        origin_lat, origin_lon, row["latitude"], row["longitude"]
                    ),
                }
                for row in rows
                if not (
                    row["provider"] == excluded_provider
                    and str(row["station_id"]) == str(excluded_station)
                )
            ]
            if len(found) >= limit:
                break
    found.sort(key=lambda item: item["distance_km"])
    return found[:limit]


def indexable_url_slug_count() -> int:
    with _connect() as connection:
        try:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM station_url_slugs WHERE indexable = 1"
            ).fetchone()
        except sqlite3.OperationalError as error:
            if not _url_slug_table_missing(error):
                raise
            return 0
    return int(row["total"])


# Países con proveedor de ranking DEDICADO (con bulk propio) → se EXCLUYEN de
# IEM para no duplicar. EE.UU. NO está aquí: NWS no tiene endpoint bulk
# (observaciones solo por estación), así que el ranking de EE.UU. lo cubre IEM.
# (NWS se sigue usando para el MAPA, no para el ranking.)
IEM_RANKING_EXCLUDE_COUNTRIES = ("ES", "FR", "NO", "IT", "PT", "AT", "SE", "CA")

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
_IEM_STATION_ELEVATIONS_CACHE: Optional[Dict[str, float]] = None


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
        # Normaliza códigos legacy (FIPS/obsoletos) del catálogo: TU→TR, etc.
        # Sin esto, el selector de países del ranking mostraba "TU" o "AN".
        catalog_country = COUNTRY_CODE_ALIASES.get(catalog_country, catalog_country)
        if catalog_country and catalog_country not in COUNTRY_CODES_RESOLVED_BY_COORDS:
            iso = catalog_country
        else:
            # Globales sin país (WMO) y códigos no-país (AN) pagan el
            # point-in-polygon sobre sus coordenadas.
            iso = country_for_point(row["latitude"], row["longitude"])
        if iso and iso not in excluded:
            out[f"{network}|{row['station_id']}"] = iso
    _IEM_STATION_COUNTRIES_CACHE = out
    return out


def iem_station_elevations() -> Dict[str, float]:
    """Altitud en metros de las estaciones IEM aptas para el ranking."""
    global _IEM_STATION_ELEVATIONS_CACHE
    if _IEM_STATION_ELEVATIONS_CACHE is not None:
        return _IEM_STATION_ELEVATIONS_CACHE
    networks = set(iem_ranking_networks())
    out: Dict[str, float] = {}
    with _connect() as connection:
        rows = connection.execute(
            "SELECT network_code, station_id, elevation_m FROM stations "
            "WHERE provider = 'IEM' AND online = 1 "
            "  AND network_code IS NOT NULL AND station_id IS NOT NULL "
            "  AND elevation_m IS NOT NULL"
        ).fetchall()
    for row in rows:
        network = str(row["network_code"]).strip()
        station_id = f"{network}|{row['station_id']}"
        if network not in networks:
            continue
        if _is_dcp_scan(network) and station_id not in _IEM_DCP_SCAN_KEEP:
            continue
        try:
            out[station_id] = float(row["elevation_m"])
        except (TypeError, ValueError):
            continue
    _IEM_STATION_ELEVATIONS_CACHE = out
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
    placeholders = ",".join("?" for _ in OFFICIAL_CATALOG_PROVIDERS)
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
            OFFICIAL_CATALOG_PROVIDERS,
        ).fetchall()
    counts = {row["provider"]: int(row["station_count"]) for row in rows}
    counts["WINDY"] = 0
    if Path(data_files.PWS_STATIONS_DB_PATH).exists():
        with _connect_pws() as connection:
            counts["WINDY"] = int(connection.execute(
                "SELECT COUNT(*) FROM pws_stations WHERE last_observation_time >= ?",
                (_pws_fresh_cutoff_iso(),),
            ).fetchone()[0])
    counts["NETATMO"] = 0
    if Path(data_files.NETATMO_PWS_STATIONS_DB_PATH).exists():
        with _connect_netatmo() as connection:
            counts["NETATMO"] = int(connection.execute(
                "SELECT COUNT(*) FROM netatmo_stations"
            ).fetchone()[0])
    return {provider: counts.get(provider, 0) for provider in CATALOG_PROVIDERS}


# Cuántas estaciones tiene cada país, con su fecha de cálculo.
#
# El recuento agrupa las 230.000 filas del catálogo y tarda cuatro décimas:
# poco para una consulta suelta, demasiado para pagarlo en cada carga del mapa,
# que lo pide dos veces —una para el selector de países y otra para el total—.
# El catálogo es un fichero de solo lectura, así que el resultado aguanta;
# el plazo existe porque el recuento de las redes de particulares depende de
# cuáles siguen publicando, y eso sí cambia con las horas.
_COUNTRY_COUNTS_TTL_S = 600.0
_country_counts_cache: Dict[tuple, tuple[float, Dict[str, int]]] = {}


def country_counts(*, providers: Optional[List[str]] = None) -> Dict[str, int]:
    cache_key = tuple(sorted(
        str(value).strip().upper() for value in (providers or list(CATALOG_PROVIDERS))
    ))
    cached = _country_counts_cache.get(cache_key)
    if cached is not None and (time.time() - cached[0]) < _COUNTRY_COUNTS_TTL_S:
        return dict(cached[1])

    requested_providers = [
        str(value).strip().upper()
        for value in (providers or list(CATALOG_PROVIDERS))
        if str(value).strip().upper() in CATALOG_PROVIDERS
    ]
    include_windy = "WINDY" in requested_providers
    include_netatmo = "NETATMO" in requested_providers
    wanted_providers = [
        provider for provider in requested_providers
        if provider in OFFICIAL_CATALOG_PROVIDERS
    ]
    counts: Dict[str, int] = {}
    if wanted_providers:
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
        counts.update({row["country"]: int(row["station_count"]) for row in rows})

    if include_windy and Path(data_files.PWS_STATIONS_DB_PATH).exists():
        with _connect_pws() as connection:
            rows = connection.execute(
                """
                SELECT UPPER(COALESCE(country, 'UNSPECIFIED')) AS country,
                       COUNT(*) AS station_count
                FROM pws_stations
                WHERE last_observation_time >= ?
                GROUP BY UPPER(COALESCE(country, 'UNSPECIFIED'))
                """,
                (_pws_fresh_cutoff_iso(),),
            ).fetchall()
        for row in rows:
            country = _normalize_country_code(row["country"])
            counts[country] = counts.get(country, 0) + int(row["station_count"])

    if include_netatmo and Path(data_files.NETATMO_PWS_STATIONS_DB_PATH).exists():
        with _connect_netatmo() as connection:
            rows = connection.execute(
                """
                SELECT UPPER(COALESCE(country, 'UNSPECIFIED')) AS country,
                       COUNT(*) AS station_count
                FROM netatmo_stations
                GROUP BY UPPER(COALESCE(country, 'UNSPECIFIED'))
                """
            ).fetchall()
        for row in rows:
            country = _normalize_country_code(row["country"])
            counts[country] = counts.get(country, 0) + int(row["station_count"])

    _country_counts_cache[cache_key] = (time.time(), dict(counts))
    return counts


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
    include_windy = "WINDY" in wanted_providers
    include_netatmo = "NETATMO" in wanted_providers
    official_providers = [
        provider for provider in wanted_providers
        if provider not in PWS_CATALOG_PROVIDERS
    ]
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
    provider_placeholders = ",".join("?" for _ in official_providers)
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
        " AND NOT (s.has_historical = 1 AND COALESCE(s.online, 1) = 0)"
        if hide_historical_only else ""
    )

    rows = []
    if official_providers:
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
            *official_providers,
            *country_parameters,
        )
        with _connect() as connection:
            rows = connection.execute(query, parameters).fetchall()

    results = []
    for row in rows:
        distance = _haversine_km(float(lat), float(lon), row["latitude"], row["longitude"])
        if distance <= radius_km:
            results.append({**_record(row), "distance_km": round(distance, 2)})
    if include_windy and not has_historical:
        results.extend(_pws_search_near(
            lat, lon, radius_km=radius_km, countries=countries,
            sensors=wanted_sensors, limit=limit,
        ))
    if include_netatmo and not has_historical:
        results.extend(_netatmo_search_near(
            lat, lon, radius_km=radius_km, countries=countries,
            sensors=wanted_sensors, limit=limit,
        ))
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
    include_windy = "WINDY" in wanted_providers
    include_netatmo = "NETATMO" in wanted_providers
    official_providers = [
        provider for provider in wanted_providers
        if provider not in PWS_CATALOG_PROVIDERS
    ]
    wanted_sensors = [
        str(value).strip().lower() for value in (sensors or [])
        if str(value).strip().lower() in SENSOR_KEYS
    ]

    provider_placeholders = ",".join("?" for _ in official_providers)
    country_placeholders = ",".join("?" for _ in wanted_countries)
    sensor_clauses = [f"ss.{key} = 1" for key in wanted_sensors]
    extra_where = "".join(f" AND {clause}" for clause in sensor_clauses)
    historical_clause = " AND s.has_historical = 1" if has_historical else ""
    historical_only_clause = (
        " AND NOT (s.has_historical = 1 AND COALESCE(s.online, 1) = 0)"
        if hide_historical_only else ""
    )
    rows = []
    if official_providers:
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
        parameters = (*official_providers, *wanted_countries)
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
    if include_windy and not has_historical:
        results.extend(_pws_search_catalog(
            lat=lat,
            lon=lon,
            countries=wanted_countries,
            sensors=wanted_sensors,
            limit=limit,
        ))
    if include_netatmo and not has_historical:
        results.extend(_netatmo_search_catalog(
            lat=lat,
            lon=lon,
            countries=wanted_countries,
            sensors=wanted_sensors,
            limit=limit,
        ))
    # PWS al final: con ``limit`` justo, los catálogos masivos (Netatmo)
    # no deben expulsar a los proveedores oficiales del resultado.
    results.sort(key=lambda item: (
        item["provider"] in PWS_CATALOG_PROVIDERS,
        item["provider"],
        item["station_id"].casefold(),
    ))
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
