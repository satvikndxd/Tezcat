#!/usr/bin/env bash
# Build frontend (if needed) and start the Tezcat server.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d frontend/dist ]; then
  (cd frontend && npm install && npm run build)
fi
if [ ! -d .venv ]; then
  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
fi
exec .venv/bin/uvicorn tezcat.api.app:app --host 0.0.0.0 --port "${PORT:-8000}"
