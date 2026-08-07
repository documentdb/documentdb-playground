#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLAYGROUND_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
TELEMETRY_DIR="$PLAYGROUND_ROOT/shared/telemetry"
ENV_FILE="${TELEMETRY_ENV_FILE:-$TELEMETRY_DIR/.env}"

if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

"$TELEMETRY_DIR/scripts/up.sh"

export OTEL_TRACES_ENABLED=true
export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:${OTEL_COLLECTOR_GRPC_PORT:-4317}"
export OTEL_SERVICE_NAME="${OTEL_SERVICE_NAME:-documentdb-mongoose}"
export DOCUMENTDB_IMAGE="${DOCUMENTDB_IMAGE:-ghcr.io/documentdb/documentdb/documentdb-local:trace-4fbbfcb8}"

echo ""
echo "Application tracing is enabled."
echo "After the API starts, run this in another terminal:"
echo "  $SCRIPT_DIR/verify-telemetry.sh"
echo ""

exec "$SCRIPT_DIR/run-app.sh"
