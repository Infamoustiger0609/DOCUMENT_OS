#!/usr/bin/env bash
# Starts the backend (uvicorn) and frontend (npm run dev) together, streaming
# both logs into this terminal, prefixed by service. Stop both with Ctrl+C.
#
# Usage (from the repo root):
#   ./dev.sh

set -e
trap 'kill 0' EXIT INT TERM

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Backend:  http://localhost:8000"
echo "Frontend: http://localhost:3000"
echo "Press Ctrl+C to stop both."
echo ""

(
  cd "$ROOT_DIR/backend"
  PYTHON=python
  [ -f venv/bin/python ] && PYTHON=venv/bin/python
  "$PYTHON" -m uvicorn main:app --reload --port 8000 2>&1 | sed -u "s/^/[backend]  /"
) &

(
  cd "$ROOT_DIR/frontend"
  npm run dev 2>&1 | sed -u "s/^/[frontend] /"
) &

wait
