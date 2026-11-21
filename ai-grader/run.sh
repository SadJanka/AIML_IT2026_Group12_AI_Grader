#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -m pip install -q -r requirements.txt
.venv/bin/python -m pip install -q -r requirements-optional.txt || echo "Optional formats (.7z, .msg) are not available"
[ -f .env ] || cp .env.example .env
echo "AI Grader: http://localhost:8000"
exec .venv/bin/python -m uvicorn app.main:app --reload --port 8000
