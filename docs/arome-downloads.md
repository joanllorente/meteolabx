# Descargas AROME: precarga y espera de perfiles

La precarga utiliza cuatro conexiones por defecto
(`METEOLABX_FORECAST_PREFETCH_STREAMS=4`). Son slots de **paquetes**, no de
bloques completos. Los cuatro paquetes del bloque inmediato (IP1, IP3,
SP1 y SP2) pueden descargarse en paralelo; después se atienden los siguientes
bloques del horizonte configurado. Los bloques anteriores quedan al final.
Cada arranque de precarga calcula la prioridad a partir de la primera hora pendiente.
En vigilancia la precarga sobrevive a las renovaciones del catálogo.

Fuera del planificador continuo, los workers también pueden iniciar descargas:
cuatro no es el límite global
de conexiones del contenedor. El bloqueo por archivo evita descargar el
mismo paquete dos veces. La precarga no espera ese bloqueo: el paquete vuelve
a la cola y se reintenta pasados `METEOLABX_FORECAST_PREFETCH_RETRY_S` (45 s).

La precarga no va por rondas. Cada una de las cuatro conexiones toma el
paquete más prioritario que ya se pueda intentar en cuanto suelta el suyo, y
cada fallo espera su propio reintento. Así, una descarga lenta o parada solo
ocupa su conexión. Pasado `METEOLABX_FORECAST_PREFETCH_DEADLINE_S` (600 s) ya
no se reintenta nada, pero lo que no se ha probado nunca todavía se intenta
una vez.

## Alternativa WCS

En `--watch`, la espera por publicación ocurre en el planificador, fuera de los
procesos de cálculo. Véase [el contrato del planificador](arome-scheduler.md).
La espera descrita a continuación se mantiene para llamadas no planificadas.

Los perfiles IP1/IP3 tienen hasta 180 segundos de espera por publicación,
configurables mediante `METEOLABX_AROME_PACKAGE_WAIT_S`. Se reintentan sólo
404, 429 y errores HTTP 500/502/503/504, con pausa de 15 segundos o el
Retry-After numérico del servidor. Los errores de autorización no se
reintentan en este bucle. Un Retry-After superior al presupuesto agota la
espera sin adelantar el siguiente intento.

La espera de otro descargador es independiente de esos 180 segundos:
se sigue esperando mientras aumente el tamaño de su `.part`, aunque tarde
más de tres minutos. Se abandona tras 60 segundos sin crecimiento, ajustables
con `METEOLABX_AROME_PACKAGE_STALL_S`. El intervalo también cuenta si todavía
no hay datos escritos. La precarga mantiene timeout cero: salta inmediatamente
un bloqueo ocupado. Abandonar la espera no cancela al propietario del archivo.
Los mapas que leen paquetes ya en disco siguen siendo oportunistas.

Los 180 segundos NO son un plazo absoluto para recibir un paquete: una
transferencia propia conserva un timeout de conexión de 30 s y de lectura
inactiva igual a `METEOLABX_AROME_PACKAGE_STALL_S` (60 s, mínimo 10). Antes eran
1800 s, y una transferencia muerta podía retener el cerrojo media hora. No se interrumpe la descarga de otro proceso al abandonar
la espera. Al fallar una transferencia se elimina su archivo parcial.

## Medición

El registro persistido `downloads-<run>.jsonl` conserva por paquete:

- bytes y duración total de la petición;
- `headers_seconds`: hasta recibir cabeceras;
- `first_chunk_seconds`: hasta el primer bloque no vacío (hasta 64 KiB),
  **no** el tiempo exacto hasta el primer byte;
- `body_seconds`: desde cabeceras hasta completar la escritura;
- `transfer_seconds`: desde el primer bloque hasta completar la escritura;
- `body_mb_s`: bytes / body_seconds en MB decimales por segundo;
- `active_downloads_start/end`: número de archivos `.part` al inicio/final.

El contador no toma ningún bloqueo, por lo que no puede retrasar la precarga.
El `.part` se crea antes de pedir las cabeceras y se elimina al fallar o se
renombra al completar la descarga. Es una aproximación: un proceso terminado
abruptamente puede dejar archivos huérfanos y aumentar la cifra. No es un
máximo continuo ni una medida de TCP.
Los tiempos de cuerpo incluyen lectura de red y escritura local. Los
acumulados se añaden a `download_stats` y al manifiesto; sumar duraciones de
conexiones paralelas no equivale a duración del RUN.

Para evaluar 2 frente a 4, comparar RUN con caché fría, mismos motores y
workers: duración real, esperas de perfiles, desvíos WCS, caudal por petición,
429/5xx y picos de memoria. No prometer una duplicación del caudal agregado.

## Aprovechamiento de SP1

Desde el cambio del 30/09/2026 se lee selectivamente SP1 ya descargado para:

| Campo SP1 | Uso |
| --- | --- |
| TMP, 2-HTGL | Temperatura a 2 m; referencia de perfiles convectivos, iso 0, cota de nieve y perfiles puntuales térmicos |
| UGRD/VGRD, 10-HTGL | Viento a 10 m del visor, base de las tres cizalladuras y perfiles |
| PRMSL, 0-MSL | Isobaras del mapa theta-e a 850 hPa |
| TCDC, 0-SFC | Nubosidad total |
| GUST, 10-HTGL | Máximo de racha de la hora |
| TPRATE, 0-SFC | Lluvia horaria, acumulado desde el RUN y precipitación de la cota de nieve |
| DSWRF, 0-SFC | Radiación solar media de la hora |

El lector también reconoce RH a 2 m. No sustituye el rocío de SP2 por uno
calculado a partir de RH: los perfiles conservan el dato publicado y SP2
sigue siendo necesario para la presión **en superficie**. PRMSL es presión
**reducida al nivel del mar** y no puede reemplazarla. IP1 e IP3 siguen
aportando los niveles y campos de altura. El ahorro es de coberturas WCS;
no se elimina ninguno de esos paquetes de la precarga.

### Intervalos y unidades

En el SP1 inspeccionado, GDAL etiqueta TPRATE como `kg/(m^2*s)` y DSWRF
como `W/(m^2)`, pero la plantilla GRIB 4.8 declara **acumulación desde el
RUN**. Los valores son, respectivamente, mm y J/m². Para el producto horario
se resta H+n − H+(n−1); en H+1 se toma directamente el acumulado. La
radiación se divide después por 3600 una sola vez, en la conversión del mapa.
El acumulado de lluvia usa directamente H+n, también en la publicación de
la serie del worker. No necesita conservar ni descargar todos los bloques
anteriores. Si falta SP1, se conserva la suma de incrementos horarios.

Para lluvia/radiación horaria, H+7 necesita también H+6 del bloque anterior
(y lo mismo en los demás límites). Si falta ese fichero, la hora o una
rejilla compatible, se vuelve al WCS. Se verifican RUN, hora válida, nivel,
unidades, tipo estadístico e intervalo; una tasa instantánea o un intervalo
ambiguo no se interpreta como acumulado. Se conservan las máscaras sin dato.
Los pequeños incrementos negativos por empaquetado se recortan a cero.

Los mapas solo leen paquetes presentes; nunca inician una descarga SP1.
El planificador da a los mapas SP1 una espera máxima propia de 45 s
(`METEOLABX_AROME_SP1_WAIT_S`, 0 para no esperar), desde la primera aparición
de la hora en el catálogo. Comprueba el bloque anterior cuando hace falta.
Al caducar, incluso con un paquete en descarga, usa `wcs_parallel`: admite
trabajos hasta llenar los huecos normales, sin ocupar la cola serializada
de IP1/perfiles. Se conserva el limitador global WCS. Si el paquete llega
antes de leerlo, el mapa todavía puede aprovecharlo. Para theta-e espera
IP1/SP1/SP2 con la regla anterior; la MSLP tiene fallback independiente.

### Peticiones WCS y trabajos por vía

El informe incorpora `wcs.http` y `wcs.jobs`, también visibles en su texto:

- Peticiones HTTP reales de los trabajos: total, `GetCoverage` y metadatos,
  reintentos incluidos, respuestas 429, errores de conexión e intentos sin
  respuesta registrada. El JSON añade el desglose por operación, producto
  (o grupo de productos del trabajo) y estado HTTP.
- Trabajos admitidos, completados y fallidos en `wcs` (cola serializada) y
  `wcs_parallel` (SP1). Se cuentan intentos, de modo que un trabajo fallido
  y su reintento son dos admisiones. No se deduce de estos modos el número
  de peticiones; se mide por separado.

Los hijos registran cada intento HTTP justo antes de enviarlo en
`wcs-<run>.jsonl`, junto a los paquetes del modelo. Un hijo que muere durante
la petición deja el intento sin respuesta. Las cachés de campos y de
metadatos no cuentan; tampoco se atribuyen a una pasada los sondeos de
catálogo en segundo plano ni las consultas de visitantes de la API. Sí se
incluyen los metadatos y campos auxiliares que pide cada trabajo, incluso
si un campo auxiliar procede de otra pasada. La atribución es al trabajo
que generó el coste. Los contextos se propagan a sus hilos de descarga.

La cuenta se conserva entre procesos y reinicios y se separa por modelo y
RUN. El padre copia el total al manifiesto en `resource_usage.wcs`; los
registros se retiran con la caché de paquetes antiguos. No se guardan
credenciales ni URLs. Si no existe medición se muestra como ausente, no
como cero; una pasada ya iniciada se etiqueta como parcial. Esto permite
comparar peticiones y vías con la duración y los minutos de huecos libres,
sin prometer un ahorro de tiempo a partir solo del número de mapas.

### Validación con datos reales

Pasada AROME 2026-09-30 12Z, H+2, dominio completo, SP1 frente a WCS:

| Campo | Máxima diferencia absoluta |
| --- | ---: |
| Temperatura 2 m | 0 °C |
| Presión reducida al nivel del mar | 0 Pa |
| Nubosidad total | 0 puntos porcentuales |
| Racha | 0,007813 m/s |
| Lluvia de una hora | 0,001953125 mm |
| Radiación media de una hora | 0,03757 W/m² |

Las diferencias pequeñas son compatibles con el empaquetado GRIB y la
resta de acumulados. Se ejecutó además el nuevo lector contra los paquetes
reales en H+1, H+2 y H+7, sin cambiar el servicio de producción. Esta muestra
no demuestra igualdad de todas las pasadas ni constituye una medición del
ahorro total de tiempo; AROME-IFS tiene pruebas de aislamiento de rutas,
pero no una comparación numérica de producción en esta validación.
