Scripts auxiliares de mantenimiento del proyecto.

Incluye generadores manuales de inventarios y mapas locales:

- `build_aemet_inventory.py`
- `build_frost_inventory.py`
- `build_metoffice_inventory.py`
- `build_meteohub_inventory.py`
- `build_euskalmet_sensor_map.py`

No se ejecutan al arrancar la app ni forman parte del runtime normal.

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
