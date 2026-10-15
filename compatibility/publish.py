# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

"""Append immutable results and render a credential-free static compatibility dashboard."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from compatibility.contracts import ROOT, read_registry
from compatibility.results import (
    RunKey,
    append_result,
)
from compatibility.results import dashboard_rows as result_rows
from compatibility.results import read_result, validate_incoming_result


def reporting_link(repository: str, row: dict[str, Any]) -> str:
    """Use the issue form's stable field IDs and encode every prefilled value."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repository):
        raise ValueError("Invalid issue repository")
    query = urlencode(
        {
            "template": "compatibility-report.yml",
            "title": f"[Compatibility] {row['integration']} {row['upstream_version']}",
            "integration": row["integration"],
            "documentdb_version": row["documentdb_version"],
            "upstream_version": row["upstream_version"],
            "environment": row["profile"],
            "dashboard_run": row["run_url"] or "No published run; include your dashboard/run link",
        }
    )
    return f"https://github.com/{repository}/issues/new?{query}"


def dashboard_rows(
    results: list[dict[str, Any]],
    registry: dict[str, Any],
    now: datetime,
    issue_repository: str,
    *,
    expected_runs: dict[RunKey, str | None] | None = None,
) -> list[dict[str, Any]]:
    """Add issue-report routes to the shared compatibility evidence projection."""
    rows = result_rows(results, registry, now, expected_runs=expected_runs)
    for row in rows:
        row["issue_url"] = reporting_link(issue_repository, row)
    return rows


def render(
    store: Path,
    site: Path,
    registry: dict[str, Any],
    *,
    issue_repository: str = "documentdb/documentdb-playground",
    preview: bool = False,
    now: datetime | None = None,
    expected_runs: dict[RunKey, str | None] | None = None,
) -> list[dict[str, Any]]:
    """Publish static HTML and JSON from the same validated history."""
    now = now or datetime.now(timezone.utc)
    if store.is_symlink():
        raise ValueError("The result store must not be a symbolic link")
    results = [read_result(path) for path in sorted(store.glob("*.json"))]
    rows = dashboard_rows(results, registry, now, issue_repository, expected_runs=expected_runs)
    if preview:
        for row in rows:
            row["issue_url"] = None
    site.mkdir(parents=True, exist_ok=True)
    environment = Environment(
        loader=FileSystemLoader(ROOT / "compatibility" / "dashboard"),
        autoescape=True,
        undefined=StrictUndefined,
    )
    generated = now.isoformat()
    page = environment.get_template("index.html").render(
        rows=rows, generated_at=generated, preview=preview
    )
    (site / "index.html").write_text(page)
    (site / "freshness.js").write_text(
        (ROOT / "compatibility" / "dashboard" / "freshness.js").read_text()
    )
    (site / "history.json").write_text(json.dumps(results, indent=2, allow_nan=False) + "\n")
    current = [{key: value for key, value in row.items() if key != "history"} for row in rows]
    (site / "current.json").write_text(
        json.dumps({"generated_at": generated, "integrations": current}, indent=2, allow_nan=False)
        + "\n"
    )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--site", type=Path)
    parser.add_argument("--issue-repository", default="documentdb/documentdb-playground")
    parser.add_argument("--expected-run-url")
    parser.add_argument(
        "--preview", action="store_true", help="Label a demo site; hide issue links"
    )
    args = parser.parse_args()
    if args.result is None and args.site is None:
        parser.error("Provide a result to append or a site to render")
    registry = read_registry()
    if args.result:
        result = read_result(args.result)
        validate_incoming_result(result, registry, args.expected_run_url)
        append_result(args.store, result)
    if args.site:
        render(
            args.store,
            args.site,
            registry,
            issue_repository=args.issue_repository,
            preview=args.preview,
        )
        print(f"Dashboard rendered at {args.site / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
