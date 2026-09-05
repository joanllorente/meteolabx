# Frontend web de MeteoLabX (SvelteKit, SSR)

Sirve las fichas de observación con renderizado en servidor y hace de proxy
con todo lo que todavía vive en la aplicación Streamlit. Es un servicio de
Railway aparte del Python: aquí solo hace falta Node.

## Rutas que contesta este servicio

| Ruta | Qué hace |
| --- | --- |
| `/` | El panel vacío: buscador de lugar y estaciones cercanas |
| `/{idioma}/observation/{slug}` | El panel de la estación, renderizado en servidor — gráficas incluidas |
| `/{idioma}/trends/{slug}` | Tendencias de la estación. `?rango=hoy` cambia la ventana; sin él, sinóptica de 7 días |
| `/{idioma}/historical/{slug}` | Histórico: extremos, resumen, climograma y tabla. `?modo=anual`, `?anyo=`, `?mes=` |
| `/{idioma}/ranking` | Ranking diario. `?metrica=`, `?pais=` y `?dia=` filtran, y cada puesto enlaza a su panel |
| `/{idioma}/map` | Mapa MapLibre: campo interpolado + estaciones. `?capa=temperatura\|viento\|precipitacion` |
| `/forecast/` | Visor AROME (SPA estático, ya no depende de Streamlit) |
| `/observation/{slug}` | 301 al castellano |
| `/{idioma}/{directorio}/{red}/{slug}.html` | 301 a la ficha nueva — son las URLs que Google ya tiene indexadas |
| `/sitemap.xml`, `/sitemap-static.xml`, `/sitemap-observation-N.xml` | Índice y tramos del sitemap |
| `/robots.txt` | Igual que el que servía el generador estático |
| `/v1/...` | Se reenvía al backend FastAPI |
| `/app/...` y todo lo demás | Se reenvía a la app antigua, WebSocket de Streamlit incluido |

Los idiomas son `es`, `ca`, `en`, `fr`, `it` y `pt`. Cada estación se publica
solo en los de su país, exactamente igual que antes de la migración.

## Variables de entorno

| Variable | Para qué |
| --- | --- |
| `PORT` | Puerto público. Railway lo inyecta |
| `METEOLABX_API_URL` | Backend FastAPI. Por red privada de Railway: `http://<servicio>.railway.internal:8000` |
| `METEOLABX_LEGACY_ORIGIN` | Servicio actual (Streamlit). Sin esta variable no hay proxy y las rutas no migradas dan 404 |
| `METEOLABX_API_TIMEOUT_MS` | Espera máxima al backend al renderizar. Por defecto 8000 |

Para que la red privada funcione, el servicio Python tiene que arrancar
uvicorn escuchando en todas las interfaces: `METEOLABX_BACKEND_HOST=::`
(la red privada de Railway es IPv6). Sin eso, el backend solo acepta
conexiones de su propio contenedor.

## Streamlit se muda a `/app`

Ninguna pestaña apunta ya a Streamlit. Sigue detrás del proxy porque
conserva la conexión de estaciones, los favoritos, las preferencias de
unidades y los directorios SEO estáticos.


La raíz del dominio ya no es suya: la sirve este frontend. `scripts/start_web.sh`
arranca Streamlit con `--server.baseUrlPath=app`, de modo que las pestañas que
todavía no están migradas —tendencias, histórico, mapa, ranking— siguen
funcionando en `https://www.meteolabx.com/app/`. Es una parada intermedia: la
idea es que ese prefijo desaparezca cuando no quede nada detrás.

## Desarrollo

```bash
./scripts/run_server.sh          # backend FastAPI en :8000, desde la raíz
cd web && npm install && npm run dev
```

`vite dev` proxya `/v1` al backend, así que basta con abrir
`http://localhost:5173/es/observation/barcelona-drassanes-0201x`.

La tabla que traduce slug → estación se construye una vez:

```bash
python scripts/build_station_url_slugs.py
```

En producción la lanza `scripts/start_web.sh` al arrancar, después de
descomprimir el catálogo.

## Producción local (el mismo servidor que en Railway)

```bash
npm run build
METEOLABX_API_URL=http://127.0.0.1:8000 \
METEOLABX_LEGACY_ORIGIN=https://www.meteolabx.com \
PORT=5180 node server.js
```

## Tests

```bash
npm test
```

Los textos del pie —versión, novedades, privacidad— salen de
`locales/*.json`, igual que en Streamlit. Si cambian ahí, hay que reexportar:

```bash
python scripts/export_app_i18n.py
```

`tests/seo-meta.test.mjs` compara los títulos, descripciones y datos
estructurados que genera este frontend con los que produce el generador
Python. Si alguien cambia un texto en `scripts/seo_pages_i18n.py` hay que
volver a exportar los dos ficheros generados:

```bash
python scripts/export_seo_i18n.py            # textos que consume el front
python scripts/export_seo_parity_fixture.py  # casos congelados del test
```
