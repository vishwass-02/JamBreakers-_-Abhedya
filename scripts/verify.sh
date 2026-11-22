#!/usr/bin/env bash
# ABHEDYA one-command check (macOS / Linux): install, lint, every test, pre-flight; then start the server.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
[ -x .venv/bin/python ] || "$PY" -m venv .venv
echo "[1/4] Installing packages ..."; .venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements-dev.txt
echo "[2/4] Lint (ruff) ...";          .venv/bin/python -m ruff check .
echo "[3/4] Tests ...";                .venv/bin/python -m pytest -q -p no:cacheprovider
echo "[4/4] Pre-flight check ...";     .venv/bin/python scripts/preflight.py
echo; echo " ALL CHECKS PASSED. Starting ABHEDYA on http://localhost:${AEGIS_PORT:-8000}/intro/"; echo
exec .venv/bin/python -m uvicorn backend.app:app --host 0.0.0.0 --port "${AEGIS_PORT:-8000}"
