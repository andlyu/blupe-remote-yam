#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VENV="$PROJECT_ROOT/.venv"

if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi

if ! "$VENV/bin/python" -c 'import mujoco, numpy, websocket, stripe, uvicorn' >/dev/null 2>&1; then
  "$VENV/bin/python" -m pip install --disable-pip-version-check -r "$PROJECT_ROOT/requirements.txt"
fi

exec "$VENV/bin/python" "$PROJECT_ROOT/run.py" "$@"
