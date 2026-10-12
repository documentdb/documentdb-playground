#!/usr/bin/env bash
# Run the Mongoose CRUD/compatibility test suite end-to-end against a local
# DocumentDB container.
#
# Starts DocumentDB in Docker (if not already running), installs the Node
# dependencies, then runs the standalone test suite locally. This is the
# single command to verify the playground works end-to-end.
#
# Prerequisites: docker, node, npm.
#
# Set KEEP_DB=0 to remove the DocumentDB container when the tests finish
# (default keeps it running for fast re-runs).
set -euo pipefail

command -v node >/dev/null || { echo "node is required" >&2; exit 1; }
command -v npm >/dev/null || { echo "npm is required" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

APP_DIR="$SCRIPT_DIR/../app"
KEEP_DB="${KEEP_DB:-1}"

ensure_documentdb

if [ "$KEEP_DB" != "1" ]; then
    trap 'stop_documentdb' EXIT
fi

echo "Installing test dependencies (mongoose)..."
(cd "$APP_DIR" && npm install --omit=dev --no-audit --no-fund >/dev/null)

echo ""
MONGO_URI="$(build_uri)" node "$APP_DIR/mongoose-crud-test.js"
