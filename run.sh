#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VENV="$PROJECT_ROOT/.venv"

if ! command -v python3 >/dev/null 2>&1; then
  echo "BluPe Remote YAM requires Python 3.10 or newer. Install Python, then rerun ./run.sh." >&2
  exit 1
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "BluPe Remote YAM requires Python 3.10 or newer.")'

if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi

if ! "$VENV/bin/python" -c 'import mujoco, numpy, websocket' >/dev/null 2>&1; then
  "$VENV/bin/python" -m pip install --disable-pip-version-check -r "$PROJECT_ROOT/requirements.txt"
fi

exec "$VENV/bin/python" "$PROJECT_ROOT/run.py" "$@"
