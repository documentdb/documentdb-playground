#!/usr/bin/env bash
# Run the Spring Data MongoDB demo API locally against a local DocumentDB
# container.
#
# Starts DocumentDB in Docker (if not already running), then builds and runs the
# Spring Boot app with Maven.
#
# Prerequisites: docker, java (JDK 17+), and Maven (system `mvn` or ./mvnw).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

APP_DIR="$SCRIPT_DIR/../app"
PORT="${PORT:-3000}"

require_java
MVN="$(resolve_maven "$APP_DIR")"

ensure_documentdb

echo ""
echo "=== Spring Data MongoDB demo API running locally ==="
echo "API:    http://localhost:${PORT}"
echo "Health: curl http://localhost:${PORT}/health"
echo "Create: curl -X POST http://localhost:${PORT}/books -H 'Content-Type: application/json' \\"
echo "          -d '{\"title\":\"Dune\",\"author\":\"Herbert\",\"genres\":[\"sci-fi\"],\"pages\":412}'"
echo "Press Ctrl-C to stop (DocumentDB keeps running; stop it with ./scripts/stop-documentdb.sh)."
echo ""

MONGO_URI="$(build_uri)" \
MONGO_DB="${MONGO_DB:-springdata_demo}" \
PORT="$PORT" \
    "$MVN" -q -f "$APP_DIR/pom.xml" spring-boot:run
