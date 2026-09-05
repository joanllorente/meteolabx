# Revisión de carga y conexiones — 2026-09-05

Se ha revisado la web SvelteKit de `web/`: navegación, carga SSR, consultas a FastAPI, refresco de observaciones, proxy y carga del mapa. Se han aplicado mejoras locales y compilado la web; no se ha desplegado ni medido latencia en producción.

| Mejora aplicada | Resultado esperado y límite |
| --- | --- |
| Caché de metadatos públicos | La ficha por slug/identificador y los países se reutilizan durante 5 minutos, hasta 512 entradas por proceso. Observación → Tendencias → Histórico no repite la misma consulta de ficha mientras siga en caché. No se cachean aquí observaciones, históricos ni credenciales. |
| Agrupación de solicitudes simultáneas | Consultas concurrentes de la misma ficha comparten la promesa; cada consumidor recibe una copia. Los errores se eliminan de caché para permitir reintentos. |
| Conexiones persistentes de la API | El proxy usa un agente HTTP/HTTPS con keep-alive, hasta 64 conexiones activas y 16 libres por origen. El WebSocket de Streamlit conserva su configuración. |
| Refresco sin solapamiento | Solo hay una petición de refresco en curso por ciclo. Las peticiones se cancelan al salir y tienen un límite de 30 segundos. Cambiar la visibilidad no dispara otra si ya hay una pendiente. |
| Conexión inicial de estaciones propias | El refresco empieza después de finalizar la consulta inicial; cambiar de estación cancela esa consulta. Antes, una consulta inicial lenta podía coincidir con los primeros refrescos. |
| Carga del mapa | La lista de países empieza a descargarse mientras se resuelve el país inicial, en lugar de esperar ese paso. |

La reutilización del agente sigue la [documentación HTTP de Node.js](https://nodejs.org/api/http.html). Además se comprobó en el código instalado que `http-proxy`, sin agente explícito, configura `agent=false` y `Connection: close`.

Validación: `npm test` en `web/`: **131 pruebas aprobadas**. Las nuevas verifican agrupación de metadatos, independencia de copias, caducidad, límite de memoria, reintentos, ausencia de solapamiento y cancelación. Dos solicitudes HTTP consecutivas al servidor de prueba usan **una conexión TCP**. `npm run build` también termina correctamente; conserva advertencias de Svelte y tamaño de chunks que no se han abordado en esta revisión.

Lo que ya estaba bien resuelto: fuente local precargada, compresión de archivos con adapter-node, división por rutas, MapLibre importado dinámicamente y consulta histórica solo al solicitarla. Se mantiene la precarga de datos al pasar el cursor, porque anticipa la navegación; no se ha desactivado indiscriminadamente.

Limitaciones y siguiente medición: la observación y las tendencias públicas esperan datos durante SSR para conservar el contenido inicial indexable. La primera consulta todavía puede tardar por el proveedor. Para atribuir tiempos reales hacen falta trazas de producción que separen resolución de ficha, consulta meteorológica y renderizado. Forecast continúa siendo una aplicación separada: entrar o salir supone cambiar de documento; integrarlo en el shell requeriría una migración mayor. No se ha cambiado la frescura de observaciones ni el contenido meteorológico para obtener una carga aparentemente más rápida.
