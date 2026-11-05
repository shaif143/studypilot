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
  # The same file holds the hosted deployment's database and storage. A local
  # dev server must not quietly run against production, so those are dropped
  # unless asked for; API keys are kept.
  if [[ "${STUDYPILOT_DEV_USE_HOSTED_SERVICES:-0}" != "1" ]]; then
    unset DATABASE_URL DATABASE_URL_UNPOOLED NEON_BRANCH NEON_AUTH_BASE_URL NEON_AUTH_JWKS_URL
    unset AWS_ENDPOINT_URL_S3 AWS_REGION AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY VERCEL_OIDC_TOKEN
  fi
fi

export STUDYPILOT_LOG_LEVEL="${STUDYPILOT_LOG_LEVEL:-INFO}"
exec .venv/bin/python -m uvicorn app.main:app \
  --app-dir services/api \
  --host 127.0.0.1 \
  --port "${STUDYPILOT_PORT:-8765}" \
  --reload \
  --reload-dir services/api/app \
  --no-access-log
