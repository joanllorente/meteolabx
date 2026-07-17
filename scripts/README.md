Scripts auxiliares de mantenimiento del proyecto.

## Runtime local

El proyecto fija Python en `.python-version` y el entorno local vive en
`.venv`. Los lanzadores seleccionan automáticamente `.venv/bin/python`, aunque
el `python3` global de macOS siga apuntando al Python incluido con Xcode.

```bash
./scripts/run_server.sh   # FastAPI, puerto 8000
./scripts/run_app.sh      # Streamlit, puerto 8501
```

Para ejecutar comandos manuales con el mismo runtime:

```bash
source .venv/bin/activate
python --version
```

También se puede usar `.venv/bin/python` directamente. Ejecutar `python3` sin
activar el entorno depende del `PATH` de la terminal y no aplica por sí solo
el contenido de `.python-version`.

Incluye generadores manuales de inventarios y mapas locales:

- `build_aemet_inventory.py`
- `build_frost_inventory.py`
- `build_geosphere_inventory.py`
- `build_ipma_inventory.py`
- `build_smhi_inventory.py`
- `build_eccc_inventory.py`
- `build_metoffice_inventory.py`
- `build_meteohub_inventory.py`
- `build_euskalmet_sensor_map.py`

No se ejecutan al arrancar la app ni forman parte del runtime normal.

## Catálogo SQLite unificado

`build_stations_sqlite.py` importa los inventarios JSON de los proveedores y
el inventario de IEM en `data/stations.sqlite`. Conserva cada registro original,
crea una representación normalizada, separa las capacidades de sensores y
construye un índice espacial RTree:

```bash
python3 scripts/build_stations_sqlite.py
```

`build_windy_pws_sqlite.py` descarga el catálogo abierto de estaciones amateur
de Windy en una base separada. La clave se lee de `METEOLABX_WINDY_API_KEY`
(también acepta `WINDY_API_KEY`) y el resultado se guarda en
`data/pws_stations.sqlite`; no modifica el catálogo oficial.

```bash
METEOLABX_WINDY_API_KEY=... python3 scripts/build_windy_pws_sqlite.py
```

FastAPI consulta esta base directamente. Las estaciones IEM se almacenan e
indexan, pero quedan fuera de `connectable_stations` hasta disponer de su
servicio de observaciones. La tabla `station_aliases` queda preparada para la
depuración posterior de duplicados con evidencia, sin fusionarlos por ID o
nombre solamente.

`build_station_aliases.py` busca solapamientos entre IEM y los proveedores
originales y guarda candidatos sin revisar en `station_aliases`. Las IDs cortas
o numéricas no cuentan como evidencia de identidad; los identificadores WMO,
ICAO, GHCN y NCEI sí conservan su namespace:

```bash
python3 scripts/build_station_aliases.py
```

El barrido también aplica la cobertura geográfica de cada proveedor. Para NWS
manda la pertenencia a su inventario, ya que incluye estaciones fuera de EE.
UU. continental. Las redes globales WMO/BUFR (`UN`) solo se aceptan junto a los
catálogos territoriales originales.

`validate_station_alias_observations.py` procesa de forma reanudable los alias
probables y ambiguos. Compara por hora varias variables del proveedor original
con `obhistory` de IEM y persiste cada intento en
`station_alias_observation_checks`:

```bash
python3 scripts/validate_station_alias_observations.py --limit 10
```

Una coincidencia fuerte pasa a `observation_confirmed` y un desacuerdo fuerte
a `observation_conflict`; ambos permanecen sin revisar y no fusionan registros.
Por defecto no procesa `inventory_secure`; para muestrearlos o validarlos
también, usa `--include-secure`.

`export_station_alias_report.py` genera un informe JSON por proveedor con los
alias confirmados, conflictos, inconclusos, errores y candidatos seguros de
inventario aun sin muestreo observacional:

```bash
python3 scripts/export_station_alias_report.py --provider NWS
```

`apply_station_visibility_overrides.py` aplica decisiones de visibilidad sin
borrar registros. Por ejemplo, oculta las copias IEM confirmadas como duplicado
de FROST y apunta a la estación FROST preferida:

```bash
python3 scripts/apply_station_visibility_overrides.py --provider FROST
```

Met Office Weather DataHub Land Observations no publica un endpoint de catalogo
de estaciones. `build_metoffice_inventory.py` reconstruye un inventario local
barriendo una malla UK contra `/observation-land/1/nearest`, deduplicando
geohashes y guardando el resultado en `data/data_estaciones_metoffice.json`.
Por defecto se frena en 320 llamadas no cacheadas para no agotar el plan gratis
de 360 llamadas/dia; se puede relanzar en dias sucesivos porque cachea las
respuestas ya descargadas.

La clave se puede pasar como `METOFFICE_API_KEY`, `--api-key`, o en
`.streamlit/secrets.toml`:

```toml
METOFFICE_API_KEY = "..."
```

Para comprobar la configuracion sin gastar llamadas:

```bash
python3 scripts/build_metoffice_inventory.py --check-config
```

El endpoint de Met Office espera los parametros `lat` y `lon` para
`/observation-land/1/nearest`.

MeteoHub Italia no publica un catalogo simple de estaciones con sensores. El
script `build_meteohub_inventory.py` reconstruye un inventario consultando
`/api/observations` con `onlyStations=true` por red y por producto BUFR, y
fusiona las estaciones encontradas para marcar capacidades como temperatura,
humedad, presion, viento y precipitacion. No requiere secretos para las redes
OBS publicas CCBY.

Para comprobar redes y productos sin construir el inventario:

```bash
python3 scripts/build_meteohub_inventory.py --check-config
```

Para probar con una sola red:

```bash
python3 scripts/build_meteohub_inventory.py --networks dpcn-lazio
```

Para generar el inventario completo:

```bash
python3 scripts/build_meteohub_inventory.py \
  --output data/data_estaciones_meteohub_it.json
```
