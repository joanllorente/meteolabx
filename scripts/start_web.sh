#!/usr/bin/env bash
# Arranque de producción: FastAPI (backend, interno) + Streamlit (frontend,
# público) en un solo servicio.
#
# Railway enruta el tráfico HTTP al puerto $PORT → ahí escucha Streamlit.
# FastAPI queda interno en 127.0.0.1:8000; el frontend lo consume vía
# METEOLABX_API_URL (por defecto http://127.0.0.1:8000). Si cualquiera de
# los dos procesos muere, el script sale con error y Railway reinicia el
# servicio entero (restartPolicyType=ON_FAILURE).
set -euo pipefail

cd "$(dirname "$0")/.."

STREAMLIT_PORT="${PORT:-8501}"
BACKEND_HOST="127.0.0.1"
BACKEND_PORT="8000"
export METEOLABX_API_URL="${METEOLABX_API_URL:-http://${BACKEND_HOST}:${BACKEND_PORT}}"

# 0) Catálogos de estaciones: en el repo viajan SOLO comprimidos
# (data/*.sqlite.gz; los .sqlite superan o rondan el límite de 100 MB de
# GitHub y están en .gitignore). Descomprimir aquí hace el deploy
# autosuficiente: sin este paso, el backend arranca sin catálogo y el
# mapa/ranking/deep links quedan vacíos en producción.
python3 - <<'PY'
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
python3 -m uvicorn server.main:app \
  --host "${BACKEND_HOST}" \
  --port "${BACKEND_PORT}" &
UVICORN_PID=$!

# Tumbar el backend si el script sale por cualquier motivo.
trap 'kill -TERM "${UVICORN_PID}" 2>/dev/null || true' EXIT

# 2) Frontend Streamlit en el puerto público.
# No bloqueamos la exposición del frontend esperando al health del backend:
# en cold starts de producción eso deja al navegador sin respuesta mientras
# arrancan dos procesos Python. La UI puede pintar su estado inicial aunque la
# API tarde unos segundos más; si el backend muere, el wait final reinicia todo.
python3 scripts/patch_streamlit_index.py
export MLX_BOOT_PROFILE="${MLX_BOOT_PROFILE:-0}"
# fileWatcherType=none: en producción no hay recarga en caliente y, sin
# watchdog instalado, Streamlit cae a un watcher por polling que consume
# CPU de forma continua en la instancia compartida.
streamlit run meteolabx.py \
  --server.port="${STREAMLIT_PORT}" \
  --server.address=0.0.0.0 \
  --server.headless=true \
  --server.fileWatcherType=none &
STREAMLIT_PID=$!

echo "⏳ Backend FastAPI arrancando en ${METEOLABX_API_URL} ..."
(
  for _ in $(seq 1 30); do
    if python3 -c "import urllib.request; urllib.request.urlopen('${METEOLABX_API_URL}/v1/health', timeout=2)" 2>/dev/null; then
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

# Si cualquiera de los dos cae, salimos → Railway reinicia ambos.
wait -n "${UVICORN_PID}" "${STREAMLIT_PID}"
echo "✗ Un proceso (backend o frontend) terminó; reiniciando servicio" >&2
exit 1
