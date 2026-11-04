#!/usr/bin/env python3
"""Write the backend's OpenAPI document to fixtures/openapi.json.

The TypeScript types in packages/shared are hand-written so they can carry
meaning the generated ones could not. This snapshot is what stops them drifting
silently: a frontend test asserts that every endpoint the client calls still
exists here, and a backend test asserts this file still matches the live app.

Run after changing any route:  npm run api:schema
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

# Build the schema without touching the user's real data directory. Hosted
# mode is used because it exposes the *complete* surface: the account routes
# are not mounted on the desktop, and the frontend's contract test needs them.
os.environ.setdefault("STUDYPILOT_DATA_DIR", str(ROOT / ".tmp-openapi"))
os.environ.setdefault("STUDYPILOT_DEPLOYMENT_MODE", "hosted")
os.environ.setdefault("STUDYPILOT_JWT_SECRET", "x" * 48)

from app.main import create_app

TARGET = ROOT / "fixtures" / "openapi.json"


def build() -> dict:
    schema = create_app().openapi()
    # Drop the version so a release bump is not a schema change.
    schema.get("info", {}).pop("version", None)
    return schema


if __name__ == "__main__":
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(
        json.dumps(build(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    paths = len(json.loads(TARGET.read_text())["paths"])
    print(f"wrote {TARGET} ({paths} paths)")
