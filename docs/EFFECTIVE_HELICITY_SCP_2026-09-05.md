# Helicidad efectiva y Supercell Composite Parameter

Se incorporan `esrh` y `scp` al catálogo AROME, selector de convección,
guías, raster, persistencia y grupo compartido de precálculo convectivo.
El visor compilado queda en `static/forecast_app`.

## Cálculo y datos ausentes

La capa efectiva es la primera secuencia continua de orígenes con CAPE
≥100 J/kg y CIN ≥−250 J/kg. Se conserva la búsqueda preexistente desde
superficie hasta 500 hPa. Los límites son niveles nativos: primer y último
origen que cumplen, cerrando al encontrar un origen que falla. No se
extrapola un techo que no se observa. Una capa de espesor cero o sin techo,
y un perfil de viento incompleto, producen NaN en ESRH y SCP.

ESRH integra todos los segmentos del hodógrafo entre esos límites, con
Bunkers derecho ya calculado; sus flechas muestran ese movimiento.
SCP = (MUCAPE/1000) × (ESRH/50) × f(EBWD), con EBWD en m/s:
f=0 debajo de 10, EBWD/20 entre 10 y 20 y 1 por encima de 20.
Se conserva el signo y los datos ausentes. La escala de SCP es 0–20;
ESRH usa −300–600 m²/s².

Referencia: [MetPy / formulación SPC](https://unidata.github.io/MetPy/latest/api/generated/metpy.calc.supercell_composite.html).

## Coste

No se añaden descargas ni trabajos independientes por producto. La búsqueda
de la capa reutiliza SB y MU; compacta las columnas que necesitan otra
parcela y deja de procesarlas al cerrar su capa. ESRH reutiliza el perfil y
Bunkers, y SCP consume MUCAPE, ESRH y EBWD del mismo cálculo por bandas.

Medición local: tres repeticiones de `diagnose_convection`, sin DCAPE,
sobre `_synthetic_profile(64, 60)` con semilla 13. Mediana anterior 0,356 s;
nueva 0,290 s (−18,5 %), incluyendo el techo efectivo. Todos los campos
preexistentes coinciden con `allclose(equal_nan=True)` en esa muestra.
No es una medición de la pasada completa ni del dominio operacional.

## Validación

Las pruebas nuevas contrastan ESRH elevada y límites interpolados con MetPy,
SCP en los umbrales 10/20 m/s, signo, datos ausentes, capas continuas,
reutilización de parcelas y pertenencia al grupo de cálculo compartido.
También se ejecutan los diagnósticos, auditoría científica, almacenamiento,
worker y endpoints. Dos pruebas de límites geográficos dependen de un
servidor externo cuya resolución DNS no está disponible en este entorno.

`npm run build:forecast` compila correctamente. `npm test` tiene un fallo
preexistente: `forecastI18n.test.mjs` importa `localizedForecastGuide` desde
`forecast-i18n.js`, aunque actualmente vive en `forecast-guides.svelte.js`.

Los nuevos frames se generarán al ejecutar el worker con esta versión;
compilar el visor no recalcula ni publica una pasada del modelo.

## STP efectivo con CIN y actualización local

Añadido `stp`, etiquetado «STP efectivo (con CIN)», escala 0–10.
Usa MLCAPE/1500, ESRH/150, factor LCL clip((2000−MLLCL)/1000, 0, 1),
factor CIN clip((MLCIN+200)/150, 0, 1), y EBWD/20 con cero por debajo
de 12,5 m/s y máximo 1,5. Base efectiva elevada: cero; ingredientes
desconocidos: NaN; resultado no negativo para Bunkers derecho.
No es la versión de capa fija.

Se conserva la CIN de la parcela ML100 y se expone la altura del LCL ya
interpolada para obtener su LFC, restando el terreno. El STP no añade
ascensos de parcelas, perfiles ni descargas.
Referencias: [SPC](https://origin-west-www-spc.woc.noaa.gov/exper/mesoanalysis/help/begin.html)
y [NOAA](https://vlab.noaa.gov/web/oclo/nsharp-hail-and-tornado-reference).

Resuelto el visor local desactualizado: faltaba instalar el build en
`web/static/forecast`. `npm run build:forecast` ahora ejecuta ese paso
automáticamente mediante `--web-only`, sin requerir Streamlit.
Verificados ESRH, SCP y STP en Convección en `http://localhost:5173/forecast`.
La pasada local del 29 de agosto todavía no tiene frames para estos productos.
Las pruebas cubren los umbrales, la base elevada, datos ausentes, alturas AGL,
reutilización de la parcela ML y sincronización del visor compilado/instalado.
