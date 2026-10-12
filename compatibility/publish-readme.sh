#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

set -euo pipefail

if [[ $# -ne 3 || -z "${GITHUB_REPOSITORY:-}" ]]; then
    printf 'Usage: GITHUB_REPOSITORY=owner/repo bash compatibility/publish-readme.sh SOURCE_JSON SOURCE_CHECKOUT REPORT_DIRECTORY\n' >&2
    exit 2
fi

source_json=$(realpath --no-symlinks "$1")
source_root=$(realpath --no-symlinks --canonicalize-missing "$2")
incoming=$(realpath --no-symlinks --canonicalize-missing "$3")
repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"
worktree=""
scratch=""
temporary_branch=""

cleanup() {
    if [[ -n "$worktree" ]]; then
        git worktree remove --force "$worktree"
        worktree=""
        scratch=""
    elif [[ -n "$scratch" ]]; then
        rmdir "$scratch"
        scratch=""
    fi
    if [[ -n "$temporary_branch" ]] && git show-ref --verify --quiet "refs/heads/$temporary_branch"; then
        git branch -D "$temporary_branch"
    fi
    temporary_branch=""
}
trap cleanup EXIT

for attempt in 1 2 3 4 5; do
    lookup=0
    git ls-remote --exit-code --heads origin refs/heads/compatibility-data >/dev/null || lookup=$?
    if [[ $lookup != 0 && $lookup != 2 ]]; then
        printf 'Could not look up the compatibility data branch.\n' >&2
        exit "$lookup"
    fi
    if [[ $lookup == 0 ]]; then
        git fetch --no-tags origin refs/heads/compatibility-data
    fi
    scratch=$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/compat-dashboard.XXXXXX")
    if [[ $lookup == 0 ]]; then
        git worktree add --detach "$scratch" FETCH_HEAD
    else
        git worktree add --detach "$scratch" HEAD
    fi
    worktree=$scratch
    if [[ $lookup == 2 ]]; then
        temporary_branch="dashboard-$(basename "$worktree")"
        git -C "$worktree" switch --orphan "$temporary_branch"
    fi

    python -m compatibility.readme ingest --source "$source_json" \
        --source-root "$source_root" --incoming "$incoming" \
        --data "$worktree" --repository "$GITHUB_REPOSITORY"
    python -m compatibility.readme render --data "$worktree" \
        --site "$worktree/site" --repository "$GITHUB_REPOSITORY"
    git -C "$worktree" add -- site
    for directory in results attempts; do
        if [[ -d "$worktree/$directory" ]]; then
            git -C "$worktree" add -- "$directory"
        fi
    done
    if git -C "$worktree" diff --cached --quiet; then
        printf 'README status and execution evidence are unchanged.\n'
        exit 0
    fi
    git -C "$worktree" \
        -c user.name='github-actions[bot]' \
        -c user.email='41898282+github-actions[bot]@users.noreply.github.com' \
        commit -s -m "Update compatibility evidence and README status"
    revision=$(git -C "$worktree" rev-parse HEAD)
    if git push origin "$revision:refs/heads/compatibility-data"; then
        exit 0
    fi
    cleanup
    printf 'Dashboard push failed; retrying from current history (%s/5).\n' "$attempt" >&2
    sleep "$attempt"
done

printf 'Could not publish the README dashboard after five conflict-safe attempts.\n' >&2
exit 1
