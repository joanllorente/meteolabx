# Pipeline incremental de predicción

MeteoLabX puede publicar los diagnósticos propios hora a hora mientras
Météo-France completa un RUN. El navegador no ejecuta cálculos: consume las
rejillas generadas por `scripts/forecast_worker.py`.

## Modelos y separación en el almacén

El visor sirve más de un modelo. Cada uno tiene su namespace de claves, su
manifiesto y su rotación de pasadas, de modo que ninguno puede pisar ni contar
dentro del otro:

| Modelo | Ruta de la API | Claves del volumen |
| --- | --- | --- |
| AROME 0,025° | `/v1/forecast/arome/…` | `forecast/runs/…`, `forecast/manifests/…` |
| ECMWF IFS 0,25° | `/v1/forecast/ecmwf/…` | `forecast/models/ecmwf/runs/…`, `forecast/models/ecmwf/manifests/…` |

AROME se queda sin prefijo a propósito: el volumen de producción ya tiene sus
pasadas escritas ahí y moverlas las dejaría huérfanas —invisibles para el visor
y fuera del alcance de la poda, que es lo único que impide que el volumen se
llene—. Cualquier modelo nuevo va bajo `forecast/models/<id>/`.

El formato binario de las rejillas sí lo comparten: vive en
`server/services/forecast_grid.py` y lo escriben los dos. Un cambio ahí obliga
a subir `FORECAST_DATA_REVISION` en `services/forecastApi.js`.

## ECMWF IFS 0,25°

Un solo mapa por ahora: geopotencial de 500 hPa en color con la presión al
nivel del mar en isobaras.

- **Origen**: open data de ECMWF, sin clave. Cada plazo es un GRIB2 global de
  unos 140 MB con 184 mensajes; se lee el `.index` que ECMWF publica al lado y
  se bajan por rango de bytes solo los dos mensajes del mapa, ~0,9 MB. Bajar el
  fichero entero costaría más que toda la pasada de AROME.
- **Dominio**: la rejilla nativa es global (1440 × 721 = 1.038.240 celdas), pero
  se recorta al leer a una ventana euroatlántica de 501 × 241. El frame queda en
  ~150 KB comprimidos y la pasada entera en unos 7 MB.
- **Coste**: entre uno y tres segundos por frame —descarga y decodificación, sin
  perfiles verticales—. Una pasada completa de 49 plazos son unos 80 segundos.
- **Alcance**: hasta +144 h cada 3 h, que es lo que publican las cuatro pasadas.
  Las 00 y 12Z llegan a +360 h; ese tramo se deja fuera por defecto.
- **Worker**: va primero en cada ciclo y aparte del grafo de trabajos de AROME.
  Un fallo suyo se registra y el ciclo sigue con la pasada convectiva, que es la
  cara.

Variables de entorno:

| Variable | Por defecto | Para qué |
| --- | --- | --- |
| `METEOLABX_ECMWF_MAX_FRAMES_PER_CYCLE` | `12` | Frames por ciclo; `0` los hace todos y `-1` desactiva el modelo. |
| `METEOLABX_ECMWF_MAX_HORIZON_H` | `144` | Alcance en horas. |
| `METEOLABX_ECMWF_DOMAIN` | `-80.125,14.875,45.125,75.125` | Recorte «oeste,sur,este,norte». |
| `METEOLABX_ECMWF_TIMEOUT_S` | `120` | Espera de lectura del open data. |

Para publicar unos cuantos frames sin arrancar el worker entero:

```bash
python -c "from server.services.ecmwf_forecast import run_cycle; print(run_cycle(max_frames=3))"
```

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
