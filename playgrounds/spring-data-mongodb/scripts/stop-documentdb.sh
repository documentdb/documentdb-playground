#!/usr/bin/env bash
# Stop and remove the local DocumentDB container.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

stop_documentdb
