#!/usr/bin/env bash
# One-time development setup on an Apple Silicon Mac.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Checking prerequisites"
command -v node >/dev/null || { echo "Node.js 20+ is required." >&2; exit 1; }
command -v uv   >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/" >&2; exit 1; }
command -v cargo >/dev/null || {
  echo "Rust is required for the desktop app: https://rustup.rs" >&2
  echo "(The website and backend work without it.)" >&2
}

if [[ "$(uname -m)" != "arm64" ]]; then
  echo "WARNING: this project targets Apple Silicon. Detected $(uname -m)." >&2
fi

echo "==> Creating the Python environment (3.13)"
uv venv --python 3.13 .venv
VIRTUAL_ENV="$PWD/.venv" uv pip install -e "services/api[dev]"

echo "==> Installing JavaScript dependencies"
npm install

echo "==> Generating test fixtures"
.venv/bin/python scripts/make_fixtures.py

cat <<'MSG'

Setup complete.

  Terminal 1:  npm run dev:api     # the backend on 127.0.0.1:8765
  Terminal 2:  npm run dev         # the website on http://127.0.0.1:5173

  Desktop app: npm run dev:desktop  (needs Rust; builds the sidecar on release)
  Checks:      npm run check

MSG
