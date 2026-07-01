"""
Rutas centralizadas para inventarios y datos locales.
"""
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
STATIONS_DB_PATH = DATA_DIR / "stations.sqlite"

AEMET_STATIONS_PATH = DATA_DIR / "data_estaciones_aemet.json"
METEOCAT_STATIONS_PATH = DATA_DIR / "data_estaciones_meteocat.json"
EUSKALMET_STATIONS_PATH = DATA_DIR / "data_estaciones_euskalmet.json"
FROST_STATIONS_PATH = DATA_DIR / "data_estaciones_frost.json"
METEOFRANCE_STATIONS_PATH = DATA_DIR / "data_estaciones_meteofrance.json"
METEOGALICIA_STATIONS_PATH = DATA_DIR / "data_estaciones_meteogalicia.json"
NWS_STATIONS_PATH = DATA_DIR / "data_estaciones_nws.json"
POEM_STATIONS_PATH = DATA_DIR / "data_estaciones_poem.json"
METOFFICE_STATIONS_PATH = DATA_DIR / "data_estaciones_metoffice.json"
METEOHUB_IT_STATIONS_PATH = DATA_DIR / "data_estaciones_meteohub_it.json"

# Fronteras de países (Natural Earth 1:50m, ISO_A2_EH) para resolver el país
# de una estación por sus coordenadas (point-in-polygon). Usado para colocar en
# su país real las estaciones IEM/WMO que vienen sin código de país.
COUNTRY_BORDERS_PATH = DATA_DIR / "ne_50m_admin_0_countries.geojson"

EUSKALMET_SENSORS_PATH = DATA_DIR / "data_sensors_euskalmet.json"
EUSKALMET_SENSOR_MAP_PATH = DATA_DIR / "data_station_sensor_map_euskalmet.json"

STATION_CATALOG_PATHS = [
    AEMET_STATIONS_PATH,
    METEOCAT_STATIONS_PATH,
    EUSKALMET_STATIONS_PATH,
    FROST_STATIONS_PATH,
    METEOFRANCE_STATIONS_PATH,
    METEOGALICIA_STATIONS_PATH,
    NWS_STATIONS_PATH,
    POEM_STATIONS_PATH,
    METOFFICE_STATIONS_PATH,
    METEOHUB_IT_STATIONS_PATH,
]

# Conteo visible del SQLite unificado: estaciones conectables + estaciones IEM
# de inventario. Los duplicados ocultos por station_visibility_overrides no
# cuentan.
STATION_CATALOG_TOTAL = 245313
