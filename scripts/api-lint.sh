#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m ruff check services/api
.venv/bin/python -m ruff format --check services/api
