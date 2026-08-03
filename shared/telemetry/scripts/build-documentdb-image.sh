#!/usr/bin/env bash

set -euo pipefail

SOURCE_INPUT="${1:-${DOCUMENTDB_SOURCE_DIR:-$HOME/repos/documentdb}}"
IMAGE_TAG="${2:-${DOCUMENTDB_IMAGE:-documentdb-local:gateway-tracing}}"
POSTGRES_VERSION="${POSTGRES_VERSION:-17}"
PACKAGE_OS="${PACKAGE_OS:-deb13}"
BASE_IMAGE="${BASE_IMAGE:-debian:trixie-slim}"

if [ -d "$SOURCE_INPUT/oss/packaging" ]; then
    SOURCE_INPUT="$SOURCE_INPUT/oss"
fi

if [ ! -d "$SOURCE_INPUT" ]; then
    echo "DocumentDB source directory does not exist: $SOURCE_INPUT" >&2
    exit 1
fi

SOURCE_DIR="$(cd "$SOURCE_INPUT" && pwd -P)"
for required_path in \
    packaging/build_packages.sh \
    packaging/gateway/docker/Dockerfile_documentdb_local \
    pg_documentdb_gw; do
    if [ ! -e "$SOURCE_DIR/$required_path" ]; then
        echo "DocumentDB source is missing $required_path: $SOURCE_DIR" >&2
        exit 1
    fi
done

command -v docker >/dev/null || {
    echo "docker is required" >&2
    exit 1
}
docker info >/dev/null 2>&1 || {
    echo "Cannot reach the Docker daemon" >&2
    exit 1
}

BUILD_DIR="$(mktemp -d "$SOURCE_DIR/.playground-image-build.XXXXXX")"
cleanup() {
    rm -rf -- "$BUILD_DIR"
}
trap cleanup EXIT

OUTPUT_REL="${BUILD_DIR#"$SOURCE_DIR"/}"
SOURCE_REVISION="$(git -C "$SOURCE_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
if [ -n "$(git -C "$SOURCE_DIR" status --porcelain 2>/dev/null || true)" ]; then
    SOURCE_REVISION="${SOURCE_REVISION}-dirty"
fi

echo "Building DocumentDB image"
echo "  Source:     $SOURCE_DIR ($SOURCE_REVISION)"
echo "  PostgreSQL: $POSTGRES_VERSION"
echo "  Image:      $IMAGE_TAG"

(
    cd "$SOURCE_DIR"
    ./packaging/build_packages.sh \
        --os "$PACKAGE_OS" \
        --pg "$POSTGRES_VERSION" \
        --output-dir "$OUTPUT_REL"
)

packages=()
for package in "$BUILD_DIR"/*.deb; do
    [ -f "$package" ] || continue
    case "$package" in
        *dbgsym*) continue ;;
    esac
    packages+=("$package")
done
if [ "${#packages[@]}" -ne 1 ]; then
    echo "Expected exactly one non-debug Debian package, found ${#packages[@]}" >&2
    printf '  %s\n' "${packages[@]}" >&2
    exit 1
fi

PACKAGE_REL="${packages[0]#"$SOURCE_DIR"/}"
(
    cd "$SOURCE_DIR"
    docker build \
        --build-arg BASE_IMAGE="$BASE_IMAGE" \
        --build-arg POSTGRES_VERSION="$POSTGRES_VERSION" \
        --build-arg DEB_PACKAGE_REL_PATH="$PACKAGE_REL" \
        --label "com.documentdb.playground.source-revision=$SOURCE_REVISION" \
        -t "$IMAGE_TAG" \
        -f packaging/gateway/docker/Dockerfile_documentdb_local .
)

docker image inspect "$IMAGE_TAG" \
    --format 'Built {{index .RepoTags 0}} ({{.Architecture}}, {{.Size}} bytes)'
