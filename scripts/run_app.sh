#!/usr/bin/env bash
# Lanza Streamlit en local usando el entorno Python del proyecto.
set -euo pipefail

cd "$(dirname "$0")/.."

PORT="${PORT:-8501}"
HOST="${HOST:-0.0.0.0}"
if [ -n "${PYTHON_BIN:-}" ]; then
  PYTHON="${PYTHON_BIN}"
elif [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi

echo "▶ MeteoLabX en http://127.0.0.1:${PORT}"
echo "  Python: $("${PYTHON}" --version 2>&1) (${PYTHON})"

# Streamlit sirve /forecast desde su propio directorio static, no desde
# static/forecast_app: sin este paso, un `npm run build:forecast` no llega al
# navegador y la página se queda con el bundle de la instalación anterior.
"${PYTHON}" scripts/install_forecast_frontend.py

exec "${PYTHON}" scripts/run_streamlit.py meteolabx.py \
  --server.port="${PORT}" \
  --server.address="${HOST}" \
  --server.headless=true
