#!/usr/bin/env bash
# Run the FastAPI backend for local development with reload.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -x .venv/bin/python ]]; then
  echo "No virtual environment found. Run: ./scripts/setup.sh" >&2
  exit 1
fi

# `.env.local` is gitignored and is where `neon env pull` writes connection
# strings and where API keys live. It is exported here, for this process only,
# rather than read by Settings itself: the test suite must never see it.
if [[ -f .env.local ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env.local
  set +a
fi

export STUDYPILOT_LOG_LEVEL="${STUDYPILOT_LOG_LEVEL:-INFO}"
exec .venv/bin/python -m uvicorn app.main:app \
  --app-dir services/api \
  --host 127.0.0.1 \
  --port "${STUDYPILOT_PORT:-8765}" \
  --reload \
  --reload-dir services/api/app \
  --no-access-log
