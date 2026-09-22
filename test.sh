#!/usr/bin/env bash
# Runs the full local test suite: backend pytest (against a disposable Postgres
# test container, started automatically if it isn't already) and the frontend
# Playwright e2e suite. See CLAUDE.md's "Testing" section.
#
# Usage (from the repo root):
#   ./test.sh

set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTAINER_NAME="documentos-test-pg"
TEST_DB_PORT="${TEST_DB_PORT:-15432}"

if ! docker inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  echo "Starting a disposable Postgres test container ($CONTAINER_NAME) on port $TEST_DB_PORT..."
  docker run -d --name "$CONTAINER_NAME" \
    -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=documentos_test \
    -p "$TEST_DB_PORT:5432" postgres:16-alpine >/dev/null
  sleep 3
elif [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME")" != "true" ]; then
  echo "Starting existing test Postgres container ($CONTAINER_NAME)..."
  docker start "$CONTAINER_NAME" >/dev/null
  sleep 2
fi

echo ""
echo "=== Backend tests (pytest) ==="
(
  cd "$ROOT_DIR/backend"
  PYTHON=python
  [ -f venv/Scripts/python.exe ] && PYTHON=venv/Scripts/python.exe
  [ -f venv/bin/python ] && PYTHON=venv/bin/python
  TEST_DATABASE_URL="postgresql://postgres:postgres@localhost:$TEST_DB_PORT/documentos_test" "$PYTHON" -m pytest
)

echo ""
echo "=== Frontend e2e tests (Playwright) ==="
(
  cd "$ROOT_DIR/frontend"
  npx playwright test
)

echo ""
echo "All tests passed."
