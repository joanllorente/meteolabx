#!/usr/bin/env bash
# Arranque de producción: FastAPI (backend, interno), Streamlit (frontend,
# público) y el worker AROME persistente en un solo servicio.
#
# Railway enruta el tráfico HTTP al puerto $PORT → ahí escucha Streamlit.
# FastAPI queda interno en 127.0.0.1:8000; el frontend lo consume vía
# METEOLABX_API_URL (por defecto http://127.0.0.1:8000). El worker y la web
# comparten ${RAILWAY_VOLUME_MOUNT_PATH}/forecast. Si cualquiera de los tres
# procesos muere, el script sale con error y Railway reinicia el servicio
# entero (restartPolicyType=ON_FAILURE).
set -euo pipefail

cd "$(dirname "$0")/.."

STREAMLIT_PORT="${PORT:-8501}"
BACKEND_HOST="127.0.0.1"
BACKEND_PORT="8000"
export METEOLABX_API_URL="${METEOLABX_API_URL:-http://${BACKEND_HOST}:${BACKEND_PORT}}"
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
"${PYTHON}" -m uvicorn server.main:app \
  --host "${BACKEND_HOST}" \
  --port "${BACKEND_PORT}" &
UVICORN_PID=$!

# 2) Worker AROME en segundo plano. Comprueba el catálogo cada cinco minutos,
# completa solo los frames pendientes y conserva los cuatro turnos de RUN.
# Ejecutarlo como módulo mantiene la raíz del proyecto en sys.path también
# dentro de la imagen de Railway (la ejecución directa solo añade /app/scripts).
"${PYTHON}" -m scripts.forecast_worker \
  --watch \
  --isolate-tasks \
  --workers "${METEOLABX_FORECAST_WORKERS:-6}" \
  --heavy-workers "${METEOLABX_FORECAST_HEAVY_WORKERS:-1}" \
  --interval "${METEOLABX_FORECAST_WORKER_INTERVAL_S:-60}" &
FORECAST_WORKER_PID=$!

STREAMLIT_PID=""
BACKEND_READY_PID=""
cleanup() {
  trap - EXIT TERM INT
  for pid in "${BACKEND_READY_PID}" "${STREAMLIT_PID}" "${FORECAST_WORKER_PID}" "${UVICORN_PID}"; do
    if [ -n "${pid}" ]; then
      kill -TERM "${pid}" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 0' TERM INT

# 3) Frontend Streamlit en el puerto público.
# No bloqueamos la exposición del frontend esperando al health del backend:
# en cold starts de producción eso deja al navegador sin respuesta mientras
# arrancan dos procesos Python. La UI puede pintar su estado inicial aunque la
# API tarde unos segundos más; si el backend muere, el wait final reinicia todo.
"${PYTHON}" scripts/patch_streamlit_index.py
"${PYTHON}" scripts/install_forecast_frontend.py
# Paginas SEO estaticas: directorio e indices de estaciones publicas. Se
# escriben en el mismo directorio del paquete Streamlit que sirve el frontend,
# por lo que las URLs limpias funcionan sin proxy ni proceso adicional.
if ! "${PYTHON}" scripts/build_seo_pages.py; then
  echo "[start_web] AVISO: no se pudieron generar las paginas SEO; la app interactiva continuara disponible" >&2
fi
export MLX_BOOT_PROFILE="${MLX_BOOT_PROFILE:-0}"
# fileWatcherType=none: en producción no hay recarga en caliente y, sin
# watchdog instalado, Streamlit cae a un watcher por polling que consume
# CPU de forma continua en la instancia compartida.
"${PYTHON}" scripts/run_streamlit.py meteolabx.py \
  --server.port="${STREAMLIT_PORT}" \
  --server.address=0.0.0.0 \
  --server.headless=true \
  --server.fileWatcherType=none &
STREAMLIT_PID=$!

echo "⏳ Backend FastAPI arrancando en ${METEOLABX_API_URL} ..."
(
  for _ in $(seq 1 30); do
    if "${PYTHON}" -c "import urllib.request; urllib.request.urlopen('${METEOLABX_API_URL}/v1/health', timeout=2)" 2>/dev/null; then
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

# Si cualquiera de los tres cae, salimos → Railway reinicia el servicio.
wait -n "${UVICORN_PID}" "${STREAMLIT_PID}" "${FORECAST_WORKER_PID}"
echo "✗ Un proceso (backend, frontend o worker AROME) terminó; reiniciando servicio" >&2
exit 1
