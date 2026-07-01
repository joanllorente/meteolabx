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

# 0) Descomprimir el catálogo de estaciones. `data/stations.sqlite` (~232 MB)
# viaja comprimido en git (`data/stations.sqlite.gz`, ~48 MB) para no chocar
# con el límite de 100 MB/fichero de GitHub. El FS de Railway es efímero, así
# que lo descomprimimos en cada arranque en frío si el crudo no está. El
# backend lo necesita para el catálogo (mapa, ranking, deep-links).
if [ ! -f data/stations.sqlite ] && [ -f data/stations.sqlite.gz ]; then
  echo "🗜  Descomprimiendo data/stations.sqlite.gz ..."
  gunzip -kc data/stations.sqlite.gz > data/stations.sqlite
  echo "✓ data/stations.sqlite listo"
fi

# 1) Backend FastAPI en segundo plano (interno).
python3 -m uvicorn server.main:app \
  --host "${BACKEND_HOST}" \
  --port "${BACKEND_PORT}" &
UVICORN_PID=$!

# Tumbar el backend si el script sale por cualquier motivo.
trap 'kill -TERM "${UVICORN_PID}" 2>/dev/null || true' EXIT

# 2) Esperar a que el backend responda /v1/health antes de exponer el frontend.
echo "⏳ Esperando al backend FastAPI en ${METEOLABX_API_URL} ..."
for _ in $(seq 1 30); do
  if python3 -c "import urllib.request; urllib.request.urlopen('${METEOLABX_API_URL}/v1/health', timeout=2)" 2>/dev/null; then
    echo "✓ Backend FastAPI listo"
    break
  fi
  if ! kill -0 "${UVICORN_PID}" 2>/dev/null; then
    echo "✗ El backend FastAPI murió durante el arranque" >&2
    exit 1
  fi
  sleep 1
done

# 3) Frontend Streamlit en el puerto público.
python3 scripts/patch_streamlit_index.py
streamlit run meteolabx.py \
  --server.port="${STREAMLIT_PORT}" \
  --server.address=0.0.0.0 \
  --server.headless=true &
STREAMLIT_PID=$!

# Si cualquiera de los dos cae, salimos → Railway reinicia ambos.
wait -n "${UVICORN_PID}" "${STREAMLIT_PID}"
echo "✗ Un proceso (backend o frontend) terminó; reiniciando servicio" >&2
exit 1
