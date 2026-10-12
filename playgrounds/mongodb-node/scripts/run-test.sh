#!/usr/bin/env bash
# Run the MongoDB Node.js native driver compatibility suite against local DocumentDB.
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

echo "Installing test dependencies (mongodb)..."
(cd "$APP_DIR" && npm install --omit=dev --no-audit --no-fund >/dev/null)

echo ""
MONGO_URI="$(build_uri)" node "$APP_DIR/mongodb-crud-test.js"