#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=_lib.sh
source "$SCRIPT_DIR/_lib.sh"

require_command docker
require_command curl
docker info >/dev/null 2>&1 || {
    echo "Cannot reach the Docker daemon" >&2
    exit 1
}

if docker container inspect documentdb-local >/dev/null 2>&1; then
    existing_project="$(
        docker container inspect \
            --format '{{ index .Config.Labels "com.docker.compose.project" }}' \
            documentdb-local 2>/dev/null || true
    )"
    if [ "$existing_project" != "documentdb-telemetry" ]; then
        echo "A container named documentdb-local already exists outside this stack." >&2
        echo "Stop it with its owning playground before starting telemetry." >&2
        exit 1
    fi
fi

compose up -d

wait_for_url \
    "OpenTelemetry Collector" \
    "http://127.0.0.1:${OTEL_COLLECTOR_HEALTH_PORT}/" \
    120
wait_for_url \
    "Jaeger" \
    "http://127.0.0.1:${JAEGER_UI_PORT}/api/services" \
    120

elapsed=0
while [ "$(docker container inspect --format '{{.State.Health.Status}}' documentdb-local)" != "healthy" ]; do
    if [ "$elapsed" -ge 240 ]; then
        echo "DocumentDB did not become healthy within 240s" >&2
        compose logs --no-color documentdb
        exit 1
    fi
    sleep 2
    elapsed=$((elapsed + 2))
done

echo ""
echo "DocumentDB telemetry stack is ready."
echo "  Gateway:       mongodb://localhost:${DOCUMENTDB_PORT}"
echo "  OTLP/gRPC:     http://localhost:${OTEL_COLLECTOR_GRPC_PORT}"
echo "  OTLP/HTTP:     http://localhost:${OTEL_COLLECTOR_HTTP_PORT}"
echo "  Jaeger UI:     http://localhost:${JAEGER_UI_PORT}"
