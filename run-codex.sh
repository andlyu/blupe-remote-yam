#!/bin/sh
set -eu
PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -z "${YAM_DASHBOARD_ROBOTS:-}" ]; then
  YAM_DASHBOARD_ROBOTS=$(cat "$PROJECT_ROOT/codex-runner/config/robots.json")
  export YAM_DASHBOARD_ROBOTS
fi
exec "$PROJECT_ROOT/codex-runner/run.sh" --provider codex --codex-login --session-api https://yam-session-api.n5hthc3gj4cqy.us-east-1.cs.amazonlightsail.com "$@"
