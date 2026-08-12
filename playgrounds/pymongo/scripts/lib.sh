#!/usr/bin/env bash
# Shared helpers for the PyMongo local playground scripts.
#
# Everything runs on your machine: the DocumentDB local emulator runs in Docker
# and the app/test run as local Python processes that connect to it directly.
set -euo pipefail

# Connection defaults. Override any of these via environment variables.
DOCUMENTDB_CONTAINER="${DOCUMENTDB_CONTAINER:-documentdb-local}"
DOCUMENTDB_IMAGE="${DOCUMENTDB_IMAGE:-ghcr.io/documentdb/documentdb/documentdb-local:latest}"
DOCUMENTDB_HOST="${DOCUMENTDB_HOST:-localhost}"
DOCUMENTDB_PORT="${DOCUMENTDB_PORT:-10260}"
DOCUMENTDB_CONTAINER_PORT=10260
# Note: the emulator rejects some reserved names (e.g. "documentdb"); use a
# distinct admin username.
DOCUMENTDB_USERNAME="${DOCUMENTDB_USERNAME:-docdbadmin}"
DOCUMENTDB_PASSWORD="${DOCUMENTDB_PASSWORD:-Documentdb!Local1}"

# Build the MongoDB connection string for the local emulator. The gateway only
# speaks TLS and advertises itself as a standalone server, so we request TLS,
# accept its self-signed cert, and use a direct connection.
build_uri() {
    echo "mongodb://${DOCUMENTDB_USERNAME}:${DOCUMENTDB_PASSWORD}@${DOCUMENTDB_HOST}:${DOCUMENTDB_PORT}/?tls=true&tlsAllowInvalidCertificates=true&directConnection=true"
}

build_container_uri() {
    echo "mongodb://${DOCUMENTDB_USERNAME}:${DOCUMENTDB_PASSWORD}@localhost:${DOCUMENTDB_CONTAINER_PORT}/?tls=true&tlsAllowInvalidCertificates=true&directConnection=true"
}

# Wait until a TCP port accepts connections.
# Args: <host> <port> [tries]
wait_for_port() {
    local host="$1" port="$2" tries="${3:-60}" i
    for i in $(seq 1 "$tries"); do
        if (exec 3<>"/dev/tcp/${host}/${port}") 2>/dev/null; then
            exec 3>&- 3<&- 2>/dev/null || true
            return 0
        fi
        sleep 1
    done
    return 1
}

# Create (once) a Python virtualenv in the app directory and install
# requirements. Echoes the path to the venv's python interpreter.
# Args: <app-dir>
setup_venv() {
    local app_dir="$1"
    local venv_dir="$app_dir/.venv"

    if [ ! -d "$venv_dir" ]; then
        echo "Creating Python virtualenv in $venv_dir ..." >&2
        python3 -m venv "$venv_dir"
    fi
    "$venv_dir/bin/pip" install --quiet --disable-pip-version-check \
        -r "$app_dir/requirements.txt" >&2
    echo "$venv_dir/bin/python"
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
