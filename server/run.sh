#!/bin/bash
# Riparr service. Single process, no external daemons (D2).
#
# Creates the virtualenv on first run. It used to exec straight into .venv/bin/python,
# which works on the box -- install.sh builds the venv there -- but the README offers
# this same script as the way to look at the interface on your own machine, and on a
# fresh clone there is no .venv, so it failed with a bare "No such file or directory"
# naming a path the reader had never heard of.
set -e
cd "$(dirname "$0")"

PY="${RIPARR_PYTHON:-python3}"

if [ ! -x ./.venv/bin/python ]; then
  echo "Setting up server/.venv (first run only)…"
  "$PY" -m venv .venv || {
    echo "Could not create a virtualenv with $PY." >&2
    echo "Set RIPARR_PYTHON to a Python 3.10+ interpreter and try again." >&2
    exit 1
  }
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
  echo "Done."
fi

exec ./.venv/bin/python -m uvicorn riparr.main:app \
  --host "${RIPARR_HOST:-0.0.0.0}" --port "${RIPARR_PORT:-8000}" "$@"
