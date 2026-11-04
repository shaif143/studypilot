#!/usr/bin/env bash
# Point the deployed website at your API.
#
# The frontend and the API must be served from the same origin: that is what
# lets the session cookie be SameSite=Lax, which removes the need for separate
# CSRF tokens and for any CORS configuration. Vercel achieves it with a
# rewrite, which this inserts into vercel.json.
#
#   ./scripts/set-api-host.sh api.example.up.railway.app
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${1:-}"
if [[ -z "$HOST" ]]; then
  echo "Usage: $0 <api-host>            e.g. studypilot-api.up.railway.app" >&2
  echo "       $0 --clear               remove the rewrite again" >&2
  exit 1
fi

python3 - "$HOST" <<'PY'
import json
import sys
from pathlib import Path

host = sys.argv[1].strip().removeprefix("https://").removeprefix("http://").rstrip("/")
config = Path("vercel.json")
data = json.loads(config.read_text())

# Keep only the SPA fallback, then put the API rewrites in front of it: Vercel
# applies rewrites in order, and the fallback deliberately matches everything
# that is not an API path.
fallback = [r for r in data.get("rewrites", []) if r["destination"] == "/index.html"]

if host == "--clear":
    data["rewrites"] = fallback
    print("Removed the API rewrite.")
else:
    data["rewrites"] = [
        {"source": "/api/:path*", "destination": f"https://{host}/api/:path*"},
        {"source": "/healthz", "destination": f"https://{host}/healthz"},
        *fallback,
    ]
    print(f"Website will proxy /api/* to https://{host}")

config.write_text(json.dumps(data, indent=2) + "\n")
PY

echo
echo "Commit and redeploy:"
echo "  git commit -am 'Point the website at the API' && git push"
