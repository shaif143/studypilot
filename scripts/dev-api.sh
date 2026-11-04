#!/usr/bin/env bash
# Run the FastAPI backend for local development with reload.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -x .venv/bin/python ]]; then
  echo "No virtual environment found. Run: ./scripts/setup.sh" >&2
  exit 1
fi

export STUDYPILOT_LOG_LEVEL="${STUDYPILOT_LOG_LEVEL:-INFO}"
exec .venv/bin/python -m uvicorn app.main:app \
  --app-dir services/api \
  --host 127.0.0.1 \
  --port "${STUDYPILOT_PORT:-8765}" \
  --reload \
  --reload-dir services/api/app \
  --no-access-log
