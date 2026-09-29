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
| AROME-IFS 0,025° | `/v1/forecast/arome-ifs/…` | `forecast/models/arome-ifs/runs/…`, `forecast/models/arome-ifs/manifests/…` |

AROME se queda sin prefijo a propósito: el volumen de producción ya tiene sus
pasadas escritas ahí y moverlas las dejaría huérfanas —invisibles para el visor
y fuera del alcance de la poda, que es lo único que impide que el volumen se
llene—. Cualquier modelo nuevo va bajo `forecast/models/<id>/`.

El formato binario de las rejillas sí lo comparten: vive en
`server/services/forecast_grid.py` y lo escriben los dos. Un cambio ahí obliga
a subir `FORECAST_DATA_REVISION` en `services/forecastApi.js`.

## ECMWF IFS 0,25°

Tres mapas: geopotencial de 500 hPa con isobaras, vorticidad relativa a
500 hPa con isohipsas cada 6 dam y vectores Q geostróficos a 700 hPa con divergencia de Q en color.

- **Origen**: open data de ECMWF, sin clave. Se consulta el `.index` y se
  descargan por rangos únicamente los mensajes necesarios. Vorticidad usa
  U/V/Z a 500 hPa y presión superficial; Q usa T/Z a 700 hPa y presión superficial.
- **Dominio**: Europa y Atlántico oriental, de 30° O a 60° E y de 25° N a
  75° N (361 × 201 celdas). Se configura con `METEOLABX_ECMWF_DOMAIN`.
  Los diagnósticos leen un halo de 3° antes de recortar; los frames históricos
  conservan su dominio y el visor los centra sobre Europa al abrirlos.
- **Diagnósticos**: derivadas esféricas, máscara bajo tierra y unidades SI.
  Vorticidad relativa sin Coriolis, presentada en 10⁻⁵ s⁻¹. Q usa viento
  geostrófico, filtro previo de T/Z de σ=2 celdas y la convención sin dividir
  por estabilidad estática. La divergencia se presenta en 10⁻¹⁶ m kg⁻¹ s⁻¹;
  azul negativo indica convergencia, rojo positivo divergencia. Las flechas
  indican dirección de Q, con longitud fija, y se conservan en SI en el frame.
- **Coste**: dos mensajes por frame Z500/MSLP, cuatro por vorticidad/Z500 y tres por Q;
  147 frames por pasada completa de 49 plazos. El coste depende de la red.
- **Alcance**: hasta +144 h cada 3 h, que es lo que publican las cuatro pasadas.
  Las 00 y 12Z llegan a +360 h; ese tramo se deja fuera por defecto.
- **Worker**: va primero en cada ciclo y aparte del grafo de trabajos de AROME.
  Un fallo suyo se registra y el ciclo sigue con la pasada convectiva, que es la
  cara.

Variables de entorno:

| Variable | Por defecto | Para qué |
| --- | --- | --- |
| `METEOLABX_ECMWF_MAX_FRAMES_PER_CYCLE` | `120` | Frames por ciclo; agrupa varios plazos para reutilizar las descargas. `0` los hace todos y `-1` desactiva el modelo. |
| `METEOLABX_ECMWF_MAX_HORIZON_H` | `144` | Alcance en horas. |
| `METEOLABX_ECMWF_DOMAIN` | `-80.125,14.875,45.125,75.125` | Recorte «oeste,sur,este,norte». |
| `METEOLABX_ECMWF_TIMEOUT_S` | `120` | Espera de lectura del open data. |

Para publicar unos cuantos frames sin arrancar el worker entero:

```bash
python -c "from server.services.ecmwf_forecast import run_cycle; print(run_cycle(max_frames=3))"
```

## AROME-IFS 0,025°

El mismo AROME, acoplado e inicializado con el IFS del CEPPM en vez de con
ARPEGE. Météo-France lo publica sobre la misma rejilla (1121 × 717 a 0,025°),
con los mismos 241 campos WCS, los mismos once paquetes GRIB, los mismos 24
niveles isobáricos y los mismos bloques de plazos hasta +51 h. Solo corre en
las cuatro pasadas principales, 00/06/12/18Z, que son las únicas que calcula
AROME.

Por eso no tiene código propio: es el pipeline de AROME con otro origen.

- **Qué cambia**: el servicio WCS (`MF-NWP-HIGHRES-AROMEIFS-0025-FRANCE-WCS`,
  dentro de la misma suscripción a la API AROME) y la API de paquetes
  (`DPPaquetAROMEIFS`, modelo `AROMEIFS`, producto `productAROIFS`). Es una
  suscripción aparte en el portal, pero va con la misma aplicación y el mismo
  token OAuth.
- **Cómo se elige**: `server/services/arome_models.py` define las dos fuentes
  y cuál está activa. El worker la fija para todo el proceso (`--model`); la
  API, para cada petición según el prefijo de la ruta. Las URL, las claves del
  almacén y las cachés en memoria la leen de ahí.
- **Qué comparten**: la cuota del WCS (50 peticiones por minuto para los dos)
  y el token del portal. Los paquetes van a `…/meteolabx-arome-packages/arome-ifs/`,
  para que la limpieza de un modelo no borre los del otro.
- **Worker**: un segundo proceso, `forecast_worker --model arome-ifs`, con sus
  manifiestos, su estado y sus avisos por correo. No publica ECMWF.

Variables de entorno:

| Variable | Por defecto | Para qué |
| --- | --- | --- |
| `METEOLABX_ENABLE_AROME_IFS` | vacío | Con `1` arranca su worker y publica su ruta en la API. |
| `METEOLABX_AROME_IFS_WORKERS` | `2` | Huecos de su worker; comparte memoria con el de AROME. |
| `METEOLABX_AROME_IFS_HEAVY_WORKERS` | `1` | Perfiles convectivos simultáneos. |

El visor lo enseña siempre, igual que a ECMWF: sin la variable en el servicio,
su pestaña aparece pero la API responde 404.

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

## Avisos por correo

Todo lo que iba mal terminaba en el log de Railway, que solo guarda el
despliegue activo: un fallo de madrugada se descubría por la mañana, mirando
el mapa vacío, y sin rastro de cómo empezó. `server/services/alerts.py` es
ahora el único sitio que manda correo; los vigilantes le entregan un `Alert` y
él decide si toca enviarlo.

Qué dispara un correo:

| Disparador | Quién lo detecta | Gravedad |
| --- | --- | --- |
| Pasada terminada con huecos, errores o mucho más lenta de lo suyo | worker, al darla por completa | ⚠️ / ⛔ |
| Pasada que se va del volumen sin haberse completado | worker, al rotar el turno | ⛔ |
| Pasada parada más de `METEOLABX_RUN_STALL_MINUTES` | backend (`health_alerts`) | ⛔ |
| Backend sin salida a internet | `egress_watchdog` | ⛔ / ⚠️ |
| Varios proveedores de estaciones caídos a la vez | ranking, al cerrar el ciclo | ⛔ |
| Un proveedor con racha larga de fallos | ranking | ⚠️ |
| Resumen diario de las cuatro pasadas | backend, a `METEOLABX_ALERT_DIGEST_HOUR_UTC` | informativo |

El informe se genera y se guarda en **todas** las pasadas; lo que decide
`METEOLABX_ALERT_EMAIL_LEVEL` es cuáles llegan además al buzón. Por defecto
(`problems`) una pasada limpia no escribe: cuatro correos diarios de «todo
bien» acaban sin abrirse, y con ellos el que importaba. Con `all` llega uno
por pasada, que es lo razonable las primeras semanas, mientras se ve qué es
normal en este servicio. El resumen diario existe para que el silencio no sea
ambiguo —si no llega, el vigilante también se ha caído—.

Cada aviso lleva una clave estable y no se repite hasta pasadas seis horas
(una, en los atascos de red). La marca de enviado vive en el volumen, no en
memoria: un reinicio por falta de memoria no puede reabrir la compuerta.

### Qué lleva el informe

Tiempos (duración total, reparto por nivel y comparación con las pasadas
conservadas), cobertura (frames publicados, productos vacíos), errores
agrupados por producto y, desde la instrumentación del worker:

- **Memoria**: pico y media del cgroup durante la pasada, contra el techo del
  contenedor. Se muestrea al cerrar cada trabajo, sobre las funciones que ya
  usaba el freno de perfiles pesados.
- **CPU**: `RUSAGE_SELF` + `RUSAGE_CHILDREN`, acumulada por deltas en el
  manifiesto. Por deltas y no en absoluto porque la pasada sobrevive a los
  reinicios del worker; los trabajos aislados cuentan porque se recogen con
  `join`.
- **Descargas GRIB**: paquetes, GB, minutos y qué parte de la pasada se fue
  esperando a Météo-France. El registro es un `downloads-<pasada>.jsonl` junto
  a los paquetes, no un acumulador en memoria: cada trabajo aislado es otro
  proceso y lo que baje allí no llegaría al padre. Se borra con sus paquetes.
- **Trabajos caídos por causa**: `killed` (el contenedor los mató, o sea
  memoria), `timeout`, `provider` y `other`. Distinguirlos importa porque
  piden cosas distintas: más memoria, menos paralelismo o nada que se pueda
  hacer desde aquí.
- **Coste aproximado**: memoria (GB-min × tarifa) + CPU (vCPU-min × tarifa),
  con el desglose a la vista. Las descargas **no** suman: son ingress y no se
  facturan; el egress de la factura lo genera el servicio `web`. Las tarifas
  salen de la facturación de Railway (09/2026) y se cambian con
  `METEOLABX_PRICE_MEMORY_GB_MIN`, `METEOLABX_PRICE_CPU_VCPU_MIN`,
  `METEOLABX_PRICE_VOLUME_GB_MIN` y `METEOLABX_PRICE_EGRESS_GB`.

Dos de esas cifras generan aviso por sí solas: un trabajo matado por memoria
y un pico por encima del 90 % del techo. Son el aviso temprano del OOM que
hasta ahora solo se veía cuando la pasada ya había quedado a medias.

El informe de cada pasada se guarda junto a ella y se consulta sin correo:

```
GET /v1/forecast/arome/report          # la pasada en curso, calculada al vuelo
GET /v1/forecast/arome/report?run=...  # una pasada concreta
```

Variables del servicio (todas opcionales; sin `RESEND_API_KEY` no sale ningún
correo y los avisos se quedan en el log, que es el comportamiento en local y
en los tests):

| Variable | Por defecto | Para qué |
| --- | --- | --- |
| `METEOLABX_RESEND_API_KEY` | — | Clave de la API de Resend. Sin ella no hay correo. |
| `METEOLABX_ALERT_EMAIL_TO` | `joan.llorente@protonmail.com` | Destinatario. |
| `METEOLABX_ALERT_EMAIL_FROM` | `MeteoLabX <alertas@meteolabx.com>` | Remitente; el dominio debe estar verificado en Resend. |
| `METEOLABX_ALERT_EMAIL_ENABLED` | activo si hay clave | `false` apaga el envío sin borrar la clave. |
| `METEOLABX_ALERT_EMAIL_LEVEL` | `problems` | `all` manda correo también con las pasadas limpias. |
| `METEOLABX_ALERT_DIGEST_HOUR_UTC` | `6` | Hora del resumen diario; negativa lo desactiva. |
| `METEOLABX_RUN_STALL_MINUTES` | `45` | Minutos sin avanzar antes de dar una pasada por atascada. |
| `METEOLABX_HEALTH_ALERTS_INTERVAL_S` | `300` | Cadencia del vigilante. |
