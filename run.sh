#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/python -m pip install -q -r requirements.txt
[ -f data/warehouse.duckdb ] || ./.venv/bin/python data/generate.py
exec ./.venv/bin/python -m uvicorn app.server:app --host 127.0.0.1 --port 8000 "$@"
