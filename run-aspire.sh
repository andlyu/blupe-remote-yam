#!/bin/sh
set -eu
PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$PROJECT_ROOT/codex-runner/launch_aspire.py" "$@"
