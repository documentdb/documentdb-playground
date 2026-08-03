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

APP_SERVICE_NAME="${OTEL_SERVICE_NAME:-documentdb-mongoose}"
GATEWAY_SERVICE_NAME="${GATEWAY_SERVICE_NAME:-documentdb_gateway}"
API_URL="${API_URL:-http://localhost:${PORT:-3000}}"
JAEGER_URL="${JAEGER_URL:-http://localhost:${JAEGER_UI_PORT:-16686}}"
CURL_TIMEOUT_SECONDS="${CURL_TIMEOUT_SECONDS:-10}"
TRACE_WAIT_TIMEOUT_SECONDS="${TRACE_WAIT_TIMEOUT_SECONDS:-60}"
unique_id="$(date +%s)"

command -v curl >/dev/null || {
    echo "curl is required" >&2
    exit 1
}
command -v python3 >/dev/null || {
    echo "python3 is required" >&2
    exit 1
}

start_time_us="$(python3 -c 'import time; print(time.time_ns() // 1000)')"
curl_args=(--connect-timeout 2 --max-time "$CURL_TIMEOUT_SECONDS" -fsS)

curl "${curl_args[@]}" "$API_URL/health" >/dev/null
curl "${curl_args[@]}" -X POST "$API_URL/books" \
    -H 'Content-Type: application/json' \
    -d "{\"title\":\"Tracing Demo $unique_id\",\"author\":\"DocumentDB\",\"genres\":[\"telemetry\"],\"pages\":1}" \
    >/dev/null
curl "${curl_args[@]}" "$API_URL/books?author=DocumentDB" >/dev/null
curl "${curl_args[@]}" "$API_URL/stats/genres" >/dev/null

trace_file="$(mktemp)"
cleanup() {
    rm -f -- "$trace_file"
}
trap cleanup EXIT

deadline=$((SECONDS + TRACE_WAIT_TIMEOUT_SECONDS))
while [ "$SECONDS" -lt "$deadline" ]; do
    if curl "${curl_args[@]}" -G \
        "$JAEGER_URL/api/traces" \
        --data-urlencode "service=$APP_SERVICE_NAME" \
        --data-urlencode "limit=100" \
        --data-urlencode "lookback=1h" \
        >"$trace_file" &&
        trace_id="$(
            python3 - \
                "$trace_file" \
                "$start_time_us" \
                "$APP_SERVICE_NAME" \
                "$GATEWAY_SERVICE_NAME" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)

start_time_us = int(sys.argv[2])
app_service_name = sys.argv[3]
gateway_service_name = sys.argv[4]

for trace in payload.get("data", []):
    processes = trace.get("processes", {})
    service_names = {
        process.get("serviceName")
        for process in processes.values()
        if process.get("serviceName")
    }
    operation_names = {
        span.get("operationName")
        for span in trace.get("spans", [])
        if span.get("operationName")
    }
    is_current_run = any(
        int(span.get("startTime", 0)) >= start_time_us
        for span in trace.get("spans", [])
    )
    if (
        is_current_run
        and app_service_name in service_names
        and gateway_service_name in service_names
        and "gateway.request" in operation_names
    ):
        print(trace.get("traceID", ""))
        break
PY
        )" &&
        [ -n "$trace_id" ]; then
        echo "Verified a connected Mongoose and gateway trace."
        echo "$JAEGER_URL/trace/$trace_id"
        exit 0
    fi
    sleep 2
done

echo "No connected Mongoose and gateway trace appeared in Jaeger within ${TRACE_WAIT_TIMEOUT_SECONDS}s." >&2
exit 1
