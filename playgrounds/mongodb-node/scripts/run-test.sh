#!/usr/bin/env bash
# Run the MongoDB Node.js native driver compatibility suite against local DocumentDB.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ "${1:-}" == "--compatibility" ]]; then
    command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
    shift
    cd "$SCRIPT_DIR/../../.."
    exec python3 -m compatibility.runner "$@" --integration nodejs
fi

command -v node >/dev/null || { echo "node is required" >&2; exit 1; }
command -v npm >/dev/null || { echo "npm is required" >&2; exit 1; }

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