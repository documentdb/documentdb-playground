#!/usr/bin/env bash
# Shared helpers for the Spring Data MongoDB local playground scripts.
#
# Everything runs on your machine: the DocumentDB local emulator runs in Docker
# and the app/test run as local JVM processes (via Maven) that connect to it
# directly.
set -euo pipefail

# Connection defaults. Override any of these via environment variables.
DOCUMENTDB_CONTAINER="${DOCUMENTDB_CONTAINER:-documentdb-local}"
DOCUMENTDB_IMAGE="${DOCUMENTDB_IMAGE:-ghcr.io/documentdb/documentdb/documentdb-local:latest}"
DOCUMENTDB_HOST="${DOCUMENTDB_HOST:-localhost}"
DOCUMENTDB_PORT="${DOCUMENTDB_PORT:-10260}"
# Note: the emulator rejects some reserved names (e.g. "documentdb"); use a
# distinct admin username.
DOCUMENTDB_USERNAME="${DOCUMENTDB_USERNAME:-docdbadmin}"
DOCUMENTDB_PASSWORD="${DOCUMENTDB_PASSWORD:-Documentdb!Local1}"

# Build the MongoDB connection string for the local emulator. The gateway only
# speaks TLS and advertises itself as a standalone server, so we request TLS,
# accept its self-signed cert, and use a direct connection.
#
# Note: this emits the same `tlsAllowInvalidCertificates=true` option the other
# playgrounds use. The MongoDB *Java* driver spells that option differently, so
# the application/test translate it to `tlsInsecure=true` at connect time (see
# MongoUriSupport.java).
build_uri() {
    echo "mongodb://${DOCUMENTDB_USERNAME}:${DOCUMENTDB_PASSWORD}@${DOCUMENTDB_HOST}:${DOCUMENTDB_PORT}/?tls=true&tlsAllowInvalidCertificates=true&directConnection=true"
}

require_docker() {
    command -v docker >/dev/null || {
        echo "docker is required (start Docker Desktop / the Docker daemon first)" >&2
        exit 1
    }
    docker info >/dev/null 2>&1 || {
        echo "Cannot reach the Docker daemon. Is Docker running?" >&2
        exit 1
    }
}

# Resolve a Maven command: prefer the project wrapper (./mvnw) when present,
# otherwise fall back to a system `mvn`. Echoes the command to use.
# Args: <app-dir>
resolve_maven() {
    local app_dir="$1"
    if [ -x "$app_dir/mvnw" ]; then
        echo "$app_dir/mvnw"
    elif command -v mvn >/dev/null; then
        echo "mvn"
    else
        echo "Maven is required: install 'mvn' or add the Maven wrapper (mvnw)." >&2
        exit 1
    fi
}

require_java() {
    command -v java >/dev/null || {
        echo "java (JDK 17+) is required" >&2
        exit 1
    }
}

container_running() {
    [ "$(docker inspect -f '{{.State.Running}}' "$DOCUMENTDB_CONTAINER" 2>/dev/null)" = "true" ]
}

container_exists() {
    docker inspect "$DOCUMENTDB_CONTAINER" >/dev/null 2>&1
}

ensure_documentdb() {
    require_docker

    if container_running; then
        echo "DocumentDB container '$DOCUMENTDB_CONTAINER' is already running."
    else
        if container_exists; then
            docker rm -f "$DOCUMENTDB_CONTAINER" >/dev/null 2>&1 || true
        fi

        echo "Starting DocumentDB container '$DOCUMENTDB_CONTAINER' on port ${DOCUMENTDB_PORT} ..."
        docker run -dt \
            -p "127.0.0.1:${DOCUMENTDB_PORT}:10260" \
            --name "$DOCUMENTDB_CONTAINER" \
            "$DOCUMENTDB_IMAGE" \
            --username "$DOCUMENTDB_USERNAME" \
            --password "$DOCUMENTDB_PASSWORD" >/dev/null
    fi

    wait_for_documentdb
}

wait_for_documentdb() {
    local uri
    uri="$(build_uri)"
    echo "Waiting for DocumentDB to accept connections ..."

    local i
    for i in $(seq 1 60); do
        if docker exec "$DOCUMENTDB_CONTAINER" mongosh "$uri" \
            --quiet --eval 'db.adminCommand({ ping: 1 })' >/dev/null 2>&1; then
            echo "DocumentDB is ready."
            return 0
        fi
        sleep 2
    done

    echo "DocumentDB did not become ready in time." >&2
    echo "Check logs with: docker logs $DOCUMENTDB_CONTAINER" >&2
    return 1
}

stop_documentdb() {
    require_docker
    if container_exists; then
        echo "Removing DocumentDB container '$DOCUMENTDB_CONTAINER' ..."
        docker rm -f "$DOCUMENTDB_CONTAINER" >/dev/null
        echo "Done."
    else
        echo "No DocumentDB container named '$DOCUMENTDB_CONTAINER' found."
    fi
}
