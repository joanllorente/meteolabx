# Pipeline incremental AROME

MeteoLabX puede publicar los diagnósticos propios hora a hora mientras
Météo-France completa un RUN. El navegador no ejecuta cálculos: consume las
rejillas generadas por `scripts/forecast_worker.py`.

## Arquitectura Railway

El despliegue actual usa un único servicio con el volumen `meteolabx-volume`.
`scripts/start_web.sh` arranca FastAPI, Streamlit y el worker AROME como tres
procesos independientes dentro del mismo contenedor. Railway inyecta
`RAILWAY_VOLUME_MOUNT_PATH` y los frames se guardan automáticamente bajo
`${RAILWAY_VOLUME_MOUNT_PATH}/forecast`.

1. Mantener el volumen conectado al servicio `meteolabx`.
2. Configurar dos credenciales distintas en Variables:

   - `METEOLABX_METEOFRANCE_API_KEY`: observaciones DPObs.
   - `METEOLABX_AROME_API_KEY`: catálogo y coberturas AROME.

3. El worker consulta Météo-France cada cinco minutos. Puede ajustarse con
   `METEOLABX_FORECAST_WORKER_INTERVAL_S`; el valor por defecto es `300`.
4. `METEOLABX_FORECAST_CALCULATION_SCOPE` puede fijarse a `model`. Si no se
   define, Railway selecciona automáticamente el dominio completo.
5. Cuando el primer frame aparezca correctamente, activar:

   ```text
   METEOLABX_FORECAST_PRECOMPUTED_ONLY=true
   ```

En desarrollo puede usarse `METEOLABX_FORECAST_STORE_PATH`. Sin volumen ni
configuración, el fallback es `data/forecast_store`.

## Dominio y recorte local

- En Railway se solicita al WCS la rejilla nativa completa de AROME France y el
  visor conserva sus bounds reales; no hay un recorte fijo al nordeste.
- En local, el alcance por defecto es `catalonia`: el subset se aplica en la
  propia petición WCS, antes de descargar temperaturas, humedad y viento. Esto
  reduce tanto la transferencia como el cálculo de cada perfil.
- El visor conserva aun así el encuadre EURW1S40 completo (803.757 celdas):
  coloca el recorte catalán en su posición y deja el exterior como `NaN`. Esas
  celdas vacías se serializan para mantener la geometría, pero no se calculan.
- El frame muestra `Recorte local: Cataluña` para que una prueba no pueda
  confundirse con un producto operativo completo.
- Para comprobar localmente el dominio de producción:

  ```bash
  METEOLABX_FORECAST_CALCULATION_SCOPE=model bash scripts/run_forecast_worker.sh --max-hours 1
  ```

Los objetos locales y completos usan namespaces diferentes, por lo que un
frame de prueba de Cataluña nunca se publicará accidentalmente como dominio
completo.

## Funcionamiento

- Cada ciclo descubre el RUN más reciente y sus horas ya publicadas.
- Solo se conservan cuatro RUN, uno por turno 00/06/12/18Z. Al aparecer un
  nuevo RUN se elimina el anterior del mismo turno, una vez publicado el nuevo
  manifiesto.
- Los campos nativos y los diagnósticos MLX seleccionados se precalculan y
  persisten. El viento de 10 m se precalcula; otros niveles se guardan de forma
  inmutable tras su primera solicitud para evitar multiplicar el coste del RUN.
- El manifiesto `forecast/manifests/latest.json` registra qué producto/hora
  está disponible.
- Un objeto ya existente no vuelve a calcularse; el proceso es idempotente.
- Si falta una variable o falla Météo-France, solo ese frame queda pendiente y
  se reintenta en el siguiente ciclo.
- Los siete diagnósticos convectivos comparten la misma descarga y cálculo del
  perfil en memoria.
- La API sirve primero el objeto persistido con caché inmutable. Si
  `PRECOMPUTED_ONLY` está activo, una hora pendiente responde HTTP 425 y nunca
  bloquea el servidor web haciendo cálculos de varios minutos.
- El visor refresca el manifiesto cada 30 segundos y habilita el mapa en cuanto
  el worker lo publica.

## Mapas reales en local, sin descargas

Trabajar en el visor no necesita clave de AROME ni bajar un solo GRIB: se copia
una foto de frames ya calculados de una instancia en marcha al mismo almacén
que lee el servidor local.

```bash
python scripts/capture_forecast_fixtures.py --list
python scripts/capture_forecast_fixtures.py --hours 6
```

Por defecto toma la pasada más reciente de `https://www.meteolabx.com` y siete
productos —uno por familia de unidades más los dos mapas convectivos nuevos—;
`--products`, `--all`, `--run` y `--level` ajustan el resto. Cada frame del
dominio completo ocupa entre 1 y 3 MB, y todo cae en `data/forecast_store`, que
está en `.gitignore`.

La foto incluye su manifiesto, recortado a lo que se ha guardado: el visor solo
ofrece las horas y los niveles que existen en disco, y cualquier otra responde
425 en un milisegundo en vez de intentar calcularse. Para servirla:

```bash
METEOLABX_FORECAST_PRECOMPUTED_ONLY=true \
METEOLABX_FORECAST_CALCULATION_SCOPE=model \
./scripts/run_server.sh
```

El alcance importa: los frames copiados son del dominio entero, así que sin
`model` los contornos se dibujarían con el recorte catalán del modo local.

## Ejecución local limitada

Para comprobar una única hora nueva sin procesar todo el RUN:

```bash
METEOLABX_FORECAST_WORKER_MAX_HOURS=1 bash scripts/run_forecast_worker.sh
```

No ejecutar dos workers contra el mismo RUN simultáneamente. El despliegue
utiliza una única réplica del servicio; la paralelización futura debe hacerse
dentro del worker por bloques, conservando un único escritor del manifiesto.
