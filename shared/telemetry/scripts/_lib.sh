#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEMETRY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$TELEMETRY_DIR/compose.yaml"
ENV_FILE="${TELEMETRY_ENV_FILE:-$TELEMETRY_DIR/.env}"

load_telemetry_env() {
    if [ -f "$ENV_FILE" ]; then
        set -a
        # shellcheck disable=SC1090
        source "$ENV_FILE"
        set +a
    fi

    export DOCUMENTDB_IMAGE="${DOCUMENTDB_IMAGE:-ghcr.io/documentdb/documentdb/documentdb-local:trace-4fbbfcb8}"
    export DOCUMENTDB_USERNAME="${DOCUMENTDB_USERNAME:-docdbadmin}"
    export DOCUMENTDB_PASSWORD="${DOCUMENTDB_PASSWORD:-Documentdb!Local1}"
    export DOCUMENTDB_PORT="${DOCUMENTDB_PORT:-10260}"
    export DOCUMENTDB_SERVICE_VERSION="${DOCUMENTDB_SERVICE_VERSION:-local}"
    export OTEL_COLLECTOR_GRPC_PORT="${OTEL_COLLECTOR_GRPC_PORT:-4317}"
    export OTEL_COLLECTOR_HTTP_PORT="${OTEL_COLLECTOR_HTTP_PORT:-4318}"
    export OTEL_COLLECTOR_HEALTH_PORT="${OTEL_COLLECTOR_HEALTH_PORT:-13133}"
    export OTEL_TRACES_SAMPLER_ARG="${OTEL_TRACES_SAMPLER_ARG:-1.0}"
    export DOCUMENTDB_SQL_COMMENTER_ENABLED="${DOCUMENTDB_SQL_COMMENTER_ENABLED:-false}"
    export JAEGER_UI_PORT="${JAEGER_UI_PORT:-16686}"
}

compose() {
    local args=(
        --project-directory "$TELEMETRY_DIR"
        -f "$COMPOSE_FILE"
    )
    if [ -f "$ENV_FILE" ]; then
        args+=(--env-file "$ENV_FILE")
    fi
    docker compose "${args[@]}" "$@"
}

require_command() {
    local command_name="$1"
    command -v "$command_name" >/dev/null || {
        echo "$command_name is required" >&2
        exit 1
    }
}

wait_for_url() {
    local name="$1"
    local url="$2"
    local timeout_seconds="${3:-120}"
    local deadline=$((SECONDS + timeout_seconds))

    while true; do
        if curl --connect-timeout 2 --max-time 5 -fsS "$url" >/dev/null 2>&1; then
            return 0
        fi
        if [ "$SECONDS" -ge "$deadline" ]; then
            echo "$name did not become ready within ${timeout_seconds}s: $url" >&2
            return 1
        fi
        sleep 2
    done
}

load_telemetry_env
