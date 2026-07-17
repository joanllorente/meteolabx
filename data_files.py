"""
Rutas centralizadas para inventarios y datos locales.
"""
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
STATIONS_DB_PATH = DATA_DIR / "stations.sqlite"
PWS_STATIONS_DB_PATH = DATA_DIR / "pws_stations.sqlite"
# Catálogo mundial (scripts/build_netatmo_pws_sqlite.py --world); el fichero
# netatmo_pws_stations.sqlite (solo ES, más denso) queda para análisis.
NETATMO_PWS_STATIONS_DB_PATH = DATA_DIR / "netatmo_pws_stations_world.sqlite"

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
IPMA_STATIONS_PATH = DATA_DIR / "data_estaciones_ipma.json"
GEOSPHERE_STATIONS_PATH = DATA_DIR / "data_estaciones_geosphere.json"
SMHI_STATIONS_PATH = DATA_DIR / "data_estaciones_smhi.json"
ECCC_STATIONS_PATH = DATA_DIR / "data_estaciones_eccc.json"

# Fronteras de países (Natural Earth 1:50m, ISO_A2_EH) para resolver el país
# de una estación por sus coordenadas (point-in-polygon). Usado para colocar en
# su país real las estaciones IEM/WMO que vienen sin código de país.
COUNTRY_BORDERS_PATH = DATA_DIR / "ne_50m_admin_0_countries.geojson"
# Costa Natural Earth 1:10m (polígonos de tierra, comprimidos). El campo de
# temperatura la usa como máscara visual: la geometría 1:50m anterior es
# adecuada para resolver países, pero demasiado simplificada al ampliar islas
# como Mallorca o Ibiza.
LAND_BORDERS_HIGH_RES_PATH = DATA_DIR / "ne_10m_land.geojson.gz"

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
    IPMA_STATIONS_PATH,
    GEOSPHERE_STATIONS_PATH,
    SMHI_STATIONS_PATH,
    ECCC_STATIONS_PATH,
]

# Valor de respaldo del conteo visible del SQLite unificado (conectables +
# IEM de inventario, sin duplicados ocultos). Solo se usa si stations.sqlite
# no está disponible; el contador de la cabecera lo calcula en vivo desde los
# SQLite (catálogo unificado + Windy online + Netatmo).
STATION_CATALOG_TOTAL = 230824
