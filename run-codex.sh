#!/bin/sh
set -eu
PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$PROJECT_ROOT/codex-runner/run.sh" --provider codex --codex-login --session-api https://yam-session-api.n5hthc3gj4cqy.us-east-1.cs.amazonlightsail.com "$@"
