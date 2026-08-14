#!/usr/bin/env bash
# Run the Spring Data MongoDB CRUD/compatibility suite end-to-end against a local
# DocumentDB container.
#
# Starts DocumentDB in Docker (if not already running), then compiles and runs
# the standalone compatibility suite (CrudCompatibilityTest) with Maven.
#
# Prerequisites: docker, java (JDK 17+), and Maven (system `mvn` or ./mvnw).
#
# Set KEEP_DB=0 to remove the DocumentDB container when the tests finish
# (default keeps it running for fast re-runs).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

APP_DIR="$SCRIPT_DIR/../app"
KEEP_DB="${KEEP_DB:-1}"

require_java
MVN="$(resolve_maven "$APP_DIR")"

ensure_documentdb

if [ "$KEEP_DB" != "1" ]; then
    trap 'stop_documentdb' EXIT
fi

echo ""
MONGO_URI="$(build_uri)" \
MONGO_DB="${MONGO_DB:-springdata_test}" \
    "$MVN" -q -f "$APP_DIR/pom.xml" compile exec:java
