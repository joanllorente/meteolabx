#!/usr/bin/env bash
# Lanza el backend FastAPI en local con autoreload.
#
# Uso:
#   ./scripts/run_server.sh             # puerto por defecto 8000
#   PORT=9000 ./scripts/run_server.sh   # puerto custom
#
# Asume que pip install -r requirements.txt ya se hizo.
set -euo pipefail

cd "$(dirname "$0")/.."

PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"

# Mata cualquier instancia previa de uvicorn en el mismo puerto para
# evitar "Address already in use" tras lanzamientos sucesivos.
# Si no hay nada en ese puerto, ``lsof`` devuelve vacío y ``kill`` no
# se invoca; usamos ``|| true`` para no fallar ``set -e``.
if command -v lsof >/dev/null 2>&1; then
  PIDS=$(lsof -ti tcp:"${PORT}" 2>/dev/null || true)
  if [ -n "${PIDS}" ]; then
    echo "⚠️  Puerto ${PORT} ocupado por PID(s) ${PIDS}; los mato antes de arrancar."
    echo "${PIDS}" | xargs kill -9 2>/dev/null || true
    sleep 1
  fi
fi

# debug=True para que /docs y /redoc estén accesibles en local.
export METEOLABX_DEBUG="${METEOLABX_DEBUG:-true}"
export METEOLABX_LOG_LEVEL="${METEOLABX_LOG_LEVEL:-DEBUG}"

echo "▶ MeteoLabX API en http://${HOST}:${PORT}"
echo "  Docs: http://${HOST}:${PORT}/docs"
echo "  Health: http://${HOST}:${PORT}/v1/health"
echo ""

exec python3 -m uvicorn server.main:app \
  --host "${HOST}" \
  --port "${PORT}" \
  --reload \
  --reload-dir server
