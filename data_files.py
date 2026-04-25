"""
Rutas centralizadas para inventarios y datos locales.
"""
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"

AEMET_STATIONS_PATH = DATA_DIR / "data_estaciones_aemet.json"
METEOCAT_STATIONS_PATH = DATA_DIR / "data_estaciones_meteocat.json"
EUSKALMET_STATIONS_PATH = DATA_DIR / "data_estaciones_euskalmet.json"
FROST_STATIONS_PATH = DATA_DIR / "data_estaciones_frost.json"
METEOFRANCE_STATIONS_PATH = DATA_DIR / "data_estaciones_meteofrance.json"
METEOGALICIA_STATIONS_PATH = DATA_DIR / "data_estaciones_meteogalicia.json"
NWS_STATIONS_PATH = DATA_DIR / "data_estaciones_nws.json"
POEM_STATIONS_PATH = DATA_DIR / "data_estaciones_poem.json"

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
]

# Conteo precomputado para evitar leer ~12 MB de catalogos JSON en cada
# arranque frío solo para pintar el total del encabezado.
STATION_CATALOG_TOTAL = 42666
