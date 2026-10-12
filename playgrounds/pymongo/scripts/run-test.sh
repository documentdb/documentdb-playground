#!/usr/bin/env bash
# Run the PyMongo CRUD/compatibility test suite end-to-end against a local
# DocumentDB container.
#
# Starts DocumentDB in Docker (if not already running), then sets up the app
# virtualenv/dependencies and runs the standalone test suite locally.
#
# Prerequisites: docker, python3.
#
# Set KEEP_DB=0 to remove the DocumentDB container when the tests finish
# (default keeps it running for fast re-runs).
set -euo pipefail

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

APP_DIR="$SCRIPT_DIR/../app"
KEEP_DB="${KEEP_DB:-1}"

ensure_documentdb

if [ "$KEEP_DB" != "1" ]; then
    trap 'stop_documentdb' EXIT
fi

echo "Setting up Python virtualenv and installing dependencies..."
VENV_PY=$(setup_venv "$APP_DIR")

echo ""
cd "$APP_DIR"
MONGO_URI="$(build_uri)" "$VENV_PY" pymongo_crud_test.py
