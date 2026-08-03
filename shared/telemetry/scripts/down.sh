#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=_lib.sh
source "$SCRIPT_DIR/_lib.sh"

args=(down --remove-orphans)
if [ "${1:-}" = "--volumes" ]; then
    args+=(--volumes)
fi

compose "${args[@]}"
