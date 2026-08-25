#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -n "${PYTHON_BIN:-}" ]; then
  PYTHON="${PYTHON_BIN}"
elif [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi

export PYTHONPATH="${PYTHONPATH:-.}"
exec "${PYTHON}" scripts/forecast_worker.py "$@"
