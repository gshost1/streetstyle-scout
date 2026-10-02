#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
# Load workshop team config into the environment without echoing values.
if [[ -d /config ]]; then
  mapfile -t TEAM_CONFIGS < <(find /config -maxdepth 1 -type f -name '*.config' | sort)
  if (( ${#TEAM_CONFIGS[@]} == 1 )); then
    set -a
    # shellcheck disable=SC1090
    source "${TEAM_CONFIGS[0]}"
    set +a
  fi
fi
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$ROOT/.venv/bin/python" -m uvicorn app.main:app --host "${SSS_HOST:-0.0.0.0}" --port "${SSS_PORT:-8080}"
