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

exec "${PYTHON}" -m streamlit run meteolabx.py \
  --server.port="${PORT}" \
  --server.address="${HOST}" \
  --server.headless=true
