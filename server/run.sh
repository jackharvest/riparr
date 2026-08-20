#!/bin/bash
# Riparr service. Single process, no external daemons (D2).
cd "$(dirname "$0")"
exec ./.venv/bin/python -m uvicorn riparr.main:app \
  --host "${RIPARR_HOST:-0.0.0.0}" --port "${RIPARR_PORT:-8000}" "$@"
