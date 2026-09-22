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
mismo paquete dos veces. La precarga no espera ese bloqueo; vuelve a probar
en la siguiente ronda para poder adelantar otros paquetes mientras tanto.

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
inactiva de 1800 s. No se interrumpe la descarga de otro proceso al abandonar
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
