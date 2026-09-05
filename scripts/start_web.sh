#!/usr/bin/env bash
# Arranque de producción del servicio Python: FastAPI y el worker AROME.
#
# Streamlit se retiró con la 2.0.0: la web es el servicio SvelteKit, que vive
# aparte y solo necesita esta API. Por eso FastAPI escucha ahora en el puerto
# público ($PORT) en vez de quedarse dentro del contenedor, y el healthcheck
# de Railway —/v1/health— lo contesta él mismo en lugar de un proxy dentro de
# Streamlit.
#
# El worker y la API comparten ${RAILWAY_VOLUME_MOUNT_PATH}/forecast. Si
# cualquiera de los dos procesos muere, el script sale con error y Railway
# reinicia el servicio (restartPolicyType=ON_FAILURE).
set -euo pipefail

cd "$(dirname "$0")/.."

# El frontend vive en otro servicio y llama por la red privada de Railway,
# que es IPv6: `::` escucha en todas las interfaces, no solo dentro del
# contenedor. En local, 127.0.0.1 basta.
BACKEND_HOST="${METEOLABX_BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${PORT:-8000}"
export METEOLABX_API_URL="${METEOLABX_API_URL:-http://127.0.0.1:${BACKEND_PORT}}"
case "${BACKEND_HOST}" in
  "::") BACKEND_HEALTHCHECK_HOST="::1" ;;
  "0.0.0.0") BACKEND_HEALTHCHECK_HOST="127.0.0.1" ;;
  *) BACKEND_HEALTHCHECK_HOST="${BACKEND_HOST}" ;;
esac
if [ -n "${PYTHON_BIN:-}" ]; then
  PYTHON="${PYTHON_BIN}"
elif [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi
echo "[start_web] Python: $("${PYTHON}" --version 2>&1) (${PYTHON})"

# Railway inyecta RAILWAY_VOLUME_MOUNT_PATH cuando el volumen está conectado.
# Fijar la ruta explícitamente evita que los frames terminen en el filesystem
# efímero del contenedor. En local, forecast_store conserva su fallback propio.
if [ -n "${RAILWAY_VOLUME_MOUNT_PATH:-}" ]; then
  export METEOLABX_FORECAST_STORE_PATH="${METEOLABX_FORECAST_STORE_PATH:-${RAILWAY_VOLUME_MOUNT_PATH}/forecast}"
  echo "[start_web] Almacén AROME persistente: ${METEOLABX_FORECAST_STORE_PATH}"
fi

# 0) Catálogos de estaciones: en el repo viajan SOLO comprimidos
# (data/*.sqlite.gz; los .sqlite superan o rondan el límite de 100 MB de
# GitHub y están en .gitignore). Descomprimir aquí hace el deploy
# autosuficiente: sin este paso, el backend arranca sin catálogo y el
# mapa/ranking/deep links quedan vacíos en producción.
"${PYTHON}" - <<'PY'
import gzip, os, shutil

CATALOGS = (
    ("data/stations.sqlite.gz", "data/stations.sqlite"),
    ("data/netatmo_pws_stations_world.sqlite.gz", "data/netatmo_pws_stations_world.sqlite"),
)
for src, dst in CATALOGS:
    if os.path.isfile(src) and (
        not os.path.isfile(dst) or os.path.getmtime(src) > os.path.getmtime(dst)
    ):
        with gzip.open(src, "rb") as fin, open(dst, "wb") as fout:
            shutil.copyfileobj(fin, fout)
        print(f"[start_web] Catálogo descomprimido: {dst} ({os.path.getsize(dst)} bytes)")
    else:
        print(f"[start_web] Catálogo ya presente: {dst}")
PY

# 1) Backend FastAPI en segundo plano (interno).
if [ "${BACKEND_HOST}" = "::" ]; then
  "${PYTHON}" -m scripts.run_uvicorn_dual_stack &
else
  "${PYTHON}" -m uvicorn server.main:app \
    --host "${BACKEND_HOST}" \
    --port "${BACKEND_PORT}" &
fi
UVICORN_PID=$!

# 2) Worker AROME en segundo plano. Comprueba el catálogo cada cinco minutos,
# completa solo los frames pendientes y conserva los cuatro turnos de RUN.
# Ejecutarlo como módulo mantiene la raíz del proyecto en sys.path también
# dentro de la imagen de Railway (la ejecución directa solo añade /app/scripts).
# Con nice: cuando la CPU está saturada calculando perfiles, una visita a la
# web no tiene que esperar detrás de siete procesos. No les quita tiempo
# mientras haya de sobra, sólo los adelanta cuando compiten.
nice -n "${METEOLABX_FORECAST_WORKER_NICE:-10}" \
  "${PYTHON}" -m scripts.forecast_worker \
  --watch \
  --isolate-tasks \
  --workers "${METEOLABX_FORECAST_WORKERS:-6}" \
  --heavy-workers "${METEOLABX_FORECAST_HEAVY_WORKERS:-0}" \
  --diagnostic-max-hours "${METEOLABX_FORECAST_DIAGNOSTIC_MAX_HOURS:-36}" \
  --interval "${METEOLABX_FORECAST_WORKER_INTERVAL_S:-60}" &
FORECAST_WORKER_PID=$!

BACKEND_READY_PID=""
cleanup() {
  trap - EXIT TERM INT
  for pid in "${BACKEND_READY_PID}" "${FORECAST_WORKER_PID}" "${UVICORN_PID}"; do
    if [ -n "${pid}" ]; then
      kill -TERM "${pid}" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 0' TERM INT

# 3) Preparativos de la web, que sirve el servicio SvelteKit.
"${PYTHON}" scripts/install_forecast_frontend.py
# Tabla que traduce el slug de una URL indexable a su estacion. La consulta
# /v1/stations/by-url-slug, que es como el frontend SvelteKit resuelve
# /{idioma}/observation/{slug}. Sin ella esas fichas devuelven 404.
if ! "${PYTHON}" scripts/build_station_url_slugs.py; then
  echo "[start_web] AVISO: no se pudo construir la tabla de slugs; las fichas de observacion no resolveran" >&2
fi

# Los directorios, los indices de red y las paginas de ciudad ya no se generan
# aqui: viajan como estaticos del servicio SvelteKit, que es quien los sirve.
# Se construyen con `scripts/build_seo_pages.py` antes de publicar.
export MLX_BOOT_PROFILE="${MLX_BOOT_PROFILE:-0}"

echo "⏳ Backend FastAPI arrancando en ${BACKEND_HOST}:${BACKEND_PORT} ..."
(
  for _ in $(seq 1 30); do
    if "${PYTHON}" -c "import http.client; c = http.client.HTTPConnection('${BACKEND_HEALTHCHECK_HOST}', ${BACKEND_PORT}, timeout=2); c.request('GET', '/v1/health'); r = c.getresponse(); raise SystemExit(0 if 200 <= r.status < 300 else 1)" 2>/dev/null; then
      echo "✓ Backend FastAPI listo"
      exit 0
    fi
    if ! kill -0 "${UVICORN_PID}" 2>/dev/null; then
      echo "✗ El backend FastAPI murió durante el arranque" >&2
      exit 1
    fi
    sleep 1
  done
  echo "✗ El backend FastAPI no respondió al healthcheck inicial" >&2
  kill -TERM "${UVICORN_PID}" 2>/dev/null || true
  exit 1
) &
BACKEND_READY_PID=$!

# Si cualquiera de los dos cae, salimos → Railway reinicia el servicio.
wait -n "${UVICORN_PID}" "${FORECAST_WORKER_PID}"
echo "✗ Un proceso (backend o worker AROME) terminó; reiniciando servicio" >&2
exit 1
