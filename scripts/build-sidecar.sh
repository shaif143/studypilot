#!/usr/bin/env bash
# Build the backend as a self-contained ARM64 executable and place it where
# Tauri expects to find a sidecar.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "$(uname -m)" != "arm64" ]]; then
  echo "This build targets Apple Silicon only. Detected $(uname -m)." >&2
  exit 1
fi

TRIPLE="aarch64-apple-darwin"
OUT="apps/desktop/src-tauri/binaries"

# The app's Google OAuth client (and nothing else) from .env.local, so the
# build can ship it and no user has to type one. Never echoed.
if [[ -f .env.local ]]; then
  while IFS= read -r line; do
    case "$line" in
      GOOGLE_OAUTH_CLIENT_ID=*|GOOGLE_OAUTH_CLIENT_SECRET=*) export "${line%%#*}" ;;
    esac
  done < .env.local
fi
if [[ -n "${GOOGLE_OAUTH_CLIENT_ID:-}" && -n "${GOOGLE_OAUTH_CLIENT_SECRET:-}" ]]; then
  echo "==> A Google OAuth client is set; it will be bundled"
else
  echo "==> No GOOGLE_OAUTH_CLIENT_ID/SECRET in .env.local; Drive will ask for one in Settings"
fi

echo "==> Building the backend with PyInstaller"
rm -rf services/api/build services/api/dist
(cd services/api && ../../.venv/bin/python -m PyInstaller --noconfirm --clean sidecar.spec)

echo "==> Staging the sidecar for Tauri"
rm -rf "$OUT"
mkdir -p "$OUT"
# Tauri resolves `binaries/studypilot-api` from tauri.conf.json to the file
# suffixed with the target triple, and copies it into Contents/MacOS.
cp services/api/dist/studypilot-api "$OUT/studypilot-api-$TRIPLE"
chmod +x "$OUT/studypilot-api-$TRIPLE"

echo "==> Verifying the executable"
file "$OUT/studypilot-api-$TRIPLE"
if ! file "$OUT/studypilot-api-$TRIPLE" | grep -q 'arm64'; then
  echo "ERROR: the sidecar is not an arm64 binary." >&2
  exit 1
fi

echo "==> Smoke-testing the bundled backend"
SMOKE_DIR="$(mktemp -d)"
trap 'rm -rf "$SMOKE_DIR"' EXIT
STUDYPILOT_DATA_DIR="$SMOKE_DIR" \
STUDYPILOT_PORT=8799 \
STUDYPILOT_API_TOKEN=smoke-test \
  "$OUT/studypilot-api-$TRIPLE" &
SMOKE_PID=$!
trap 'kill $SMOKE_PID 2>/dev/null || true; rm -rf "$SMOKE_DIR"' EXIT

# A one-file bundle unpacks ~180 MB and imports PyMuPDF, the speech model
# and the vector store before it listens; a cold start on a busy machine
# can take over a minute, which is not a failure.
for _ in $(seq 1 180); do
  if curl -fsS http://127.0.0.1:8799/healthz >/dev/null 2>&1; then
    echo "    liveness OK"
    break
  fi
  sleep 1
done

if ! curl -fsS http://127.0.0.1:8799/healthz >/dev/null 2>&1; then
  echo "ERROR: the bundled backend did not start." >&2
  exit 1
fi

curl -fsS -H 'x-studypilot-token: smoke-test' http://127.0.0.1:8799/api/health \
  | python3 -c 'import json,sys; d=json.load(sys.stdin)["runtime"]; print("    vector:", d["vector_backend"], "| sparse:", d["sparse_backend"], "| embeddings:", d["embedding_model"])'

echo "==> Sidecar ready: $OUT/studypilot-api-$TRIPLE"
