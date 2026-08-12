#!/usr/bin/env bash
# Shared helpers for the Mongoose + DocumentDB playground scripts.
#
# Everything here targets a DocumentDB instance running LOCALLY in a Docker
# container (the official `documentdb-local` image). No Kubernetes required.
set -euo pipefail

# --- Configuration (override via environment variables) ----------------------
DOCUMENTDB_IMAGE="${DOCUMENTDB_IMAGE:-ghcr.io/documentdb/documentdb/documentdb-local:latest}"
DOCUMENTDB_CONTAINER="${DOCUMENTDB_CONTAINER:-documentdb-local}"
DOCUMENTDB_PORT="${DOCUMENTDB_PORT:-10260}"
DOCUMENTDB_CONTAINER_PORT=10260
DOCUMENTDB_USERNAME="${DOCUMENTDB_USERNAME:-docdbadmin}"
DOCUMENTDB_PASSWORD="${DOCUMENTDB_PASSWORD:-Documentdb!Local1}"

# Local MongoDB connection string that Mongoose/the driver uses. The gateway
# only accepts TLS and advertises itself as a standalone server, so we request
# a direct connection and accept the container's self-signed certificate.
build_uri() {
    echo "mongodb://${DOCUMENTDB_USERNAME}:${DOCUMENTDB_PASSWORD}@localhost:${DOCUMENTDB_PORT}/?tls=true&tlsAllowInvalidCertificates=true&directConnection=true"
}

build_container_uri() {
    echo "mongodb://${DOCUMENTDB_USERNAME}:${DOCUMENTDB_PASSWORD}@localhost:${DOCUMENTDB_CONTAINER_PORT}/?tls=true&tlsAllowInvalidCertificates=true&directConnection=true"
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

container_running() {
    [ "$(docker container inspect -f '{{.State.Running}}' "$DOCUMENTDB_CONTAINER" 2>/dev/null)" = "true" ]
}

container_exists() {
    docker container inspect "$DOCUMENTDB_CONTAINER" >/dev/null 2>&1
}

# Start the DocumentDB container if it is not already running, then wait until
# the gateway accepts connections. Idempotent: safe to call from every script.
ensure_documentdb() {
    require_docker

    if container_running; then
        echo "DocumentDB container '$DOCUMENTDB_CONTAINER' is already running."
    else
        # Remove a stopped/leftover container with the same name so `run` succeeds.
        if container_exists; then
            docker rm -f "$DOCUMENTDB_CONTAINER" >/dev/null 2>&1 || true
        fi

        echo "Starting DocumentDB container '$DOCUMENTDB_CONTAINER' on port ${DOCUMENTDB_PORT} ..."
        # Bind to 127.0.0.1 explicitly: keeps the database off external interfaces
        # and avoids a Docker Desktop/WSL2 quirk where the IPv4 loopback mapping is
        # otherwise not reachable from host processes.
        docker run -dt \
            -p "127.0.0.1:${DOCUMENTDB_PORT}:10260" \
            --name "$DOCUMENTDB_CONTAINER" \
            "$DOCUMENTDB_IMAGE" \
            --username "$DOCUMENTDB_USERNAME" \
            --password "$DOCUMENTDB_PASSWORD" >/dev/null
    fi

    wait_for_documentdb
}

# Poll the gateway until it responds to a MongoDB ping (or time out).
wait_for_documentdb() {
    local uri
    uri="$(build_container_uri)"
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
