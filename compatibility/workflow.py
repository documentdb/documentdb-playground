# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

"""Select registry-backed jobs and combine their evidence without fabricating missing runs."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from packaging.version import Version

from compatibility.contracts import default_documentdb_version, read_registry
from compatibility.publish import render
from compatibility.readme import markdown_cell
from compatibility.results import (
    RunKey,
    append_result,
    read_result,
    result_key,
    validate_incoming_result,
)

TRIGGERS = ("manual", "scheduled", "documentdb_release")


def documentdb_releases(registry: dict[str, Any], documentdb_version: str | None) -> list[str]:
    """Expand one reviewed release, every reviewed release, or the newest when unspecified."""
    if documentdb_version is None:
        return [default_documentdb_version(registry)]
    if documentdb_version == "all":
        return sorted(registry["documentdb"], key=Version)
    if documentdb_version not in registry["documentdb"]:
        raise ValueError("Unknown DocumentDB release")
    return [documentdb_version]


def changed_releases(registry: dict[str, Any], previous: Path) -> list[str]:
    """List reviewed releases that are new or re-pinned relative to an earlier registry."""
    try:
        earlier = yaml.safe_load(previous.read_text())["documentdb"]
    except (OSError, yaml.YAMLError, KeyError, TypeError):
        earlier = {}
    if not isinstance(earlier, dict):
        earlier = {}
    return sorted(
        (name for name, release in registry["documentdb"].items() if earlier.get(name) != release),
        key=Version,
    )


def select_runs(
    registry: dict[str, Any],
    integration: str,
    version: str | None,
    documentdb_version: str | None,
    demonstration: bool = False,
    *,
    trigger: str = "manual",
) -> list[dict[str, Any]]:
    """Resolve each enabled integration's own default before expanding the matrix."""
    if trigger not in TRIGGERS:
        raise ValueError("Unknown execution trigger")
    if trigger != "manual" and (integration != "all" or version or demonstration):
        raise ValueError("Automatic runs select every enabled integration at its default version")
    if trigger == "scheduled" and documentdb_version != "all":
        raise ValueError("Scheduled runs select every reviewed DocumentDB release")
    if trigger == "documentdb_release" and documentdb_version in (None, "all"):
        raise ValueError("DocumentDB release runs select one reviewed release")
    releases = documentdb_releases(registry, documentdb_version)
    if integration == "all":
        if version:
            raise ValueError("A version override requires a single integration, not all")
        names = [name for name, spec in registry["integrations"].items() if spec["enabled"]]
    else:
        spec = registry["integrations"].get(integration)
        if spec is None or not spec["enabled"]:
            raise ValueError("Unknown or disabled integration")
        names = [integration]
    if not names:
        raise ValueError("No enabled integrations were selected")
    runs: list[dict[str, Any]] = []
    for name in sorted(names):
        spec = registry["integrations"][name]
        selected = version or spec["default_version"]
        if len(selected) > 40 or not re.fullmatch(spec["version_pattern"], selected):
            raise ValueError(f"Requested version is outside the policy for {name}")
        runs.extend(
            {
                "integration": name,
                "version": selected,
                "documentdb_version": release,
                "demonstration": demonstration,
                "trigger": trigger,
            }
            for release in releases
        )
    return runs


def collect_report(
    incoming: Path,
    output: Path,
    registry: dict[str, Any],
    runs: list[dict[str, Any]],
    *,
    expected_run_url: str | None = None,
    summary: Path | None = None,
) -> bool:
    """Retain valid envelopes and expose rejected or absent evidence for every selected job."""
    if output.exists() or output.is_symlink():
        raise ValueError("Refusing to overwrite an existing combined report")
    if incoming.is_symlink():
        raise ValueError("The incoming artifact directory must not be a symbolic link")
    store = output / "results"
    expected: dict[RunKey, str | None] = {}
    errors = []
    selected: dict[str, set[str]] = {}
    for run in runs:
        selected.setdefault(run["integration"], set()).add(run["documentdb_version"])
    if incoming.exists() and any(
        path.name not in selected
        or (
            path.is_dir()
            and not path.is_symlink()
            and any(release.name not in selected[path.name] for release in path.iterdir())
        )
        for path in incoming.iterdir()
    ):
        errors.append(
            "Unexpected files, integrations, or releases were found in the downloaded artifacts"
        )
    output.mkdir(parents=True)
    for run in runs:
        integration = run["integration"]
        spec = registry["integrations"][integration]
        key = (
            integration,
            run["documentdb_version"],
            run["version"],
            spec["profile"],
            run["demonstration"],
        )
        expected[key] = None
        directory = incoming / integration / run["documentdb_version"]
        path = directory / "result.json"
        try:
            if directory.parent.is_symlink() or directory.is_symlink():
                raise ValueError("Integration artifacts must not use a directory symbolic link")
            if not path.exists() and not path.is_symlink():
                raise ValueError(
                    "Missing result artifact for this integration, release, and attempt"
                )
            result = read_result(path)
            if result_key(result) != key or result["trigger"] != run["trigger"]:
                raise ValueError("Result does not match the selected integration run")
            validate_incoming_result(result, registry, expected_run_url)
            append_result(store, result)
        except (OSError, ValueError, KeyError) as error:
            expected[key] = f"Result unavailable: {error}"
    rows = render(store, output / "site", registry, preview=True, expected_runs=expected)
    success = not errors and all(row["state"] == "Working" for row in rows)
    report = {
        "schema_version": 1,
        "success": success,
        "errors": errors,
        "integrations": [
            {key: value for key, value in row.items() if key != "history"} for row in rows
        ],
    }
    (output / "summary.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    lines = [
        "## Ecosystem compatibility",
        "",
        (
            "All selected profiles passed."
            if success
            else "One or more profiles or artifacts need attention."
        ),
        "",
        "| Integration | DocumentDB | Upstream | Profile | Mode | Result | Passed | Diagnostic |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        cells = [
            row["integration"],
            row["documentdb_version"],
            row["upstream_version"],
            row["profile"],
            "Demonstration" if row["demonstration"] else "Compatibility",
            row["state"],
            f"{row['passed']}/{row['expected']}",
            row["error"] or "",
        ]
        lines.append("| " + " | ".join(markdown_cell(cell) for cell in cells) + " |")
    if errors:
        lines.extend(["", "### Reporting errors", ""])
        lines.extend(f"- {markdown_cell(error)}" for error in errors)
    contents = "\n".join(lines) + "\n"
    (output / "summary.md").write_text(contents)
    if summary is not None:
        with summary.open("a") as file:
            file.write(contents)
    return success


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    changed = commands.add_parser("changed-releases")
    changed.add_argument("--previous", type=Path, required=True)
    for name in ("matrix", "report"):
        command = commands.add_parser(name)
        command.add_argument("--integration", default="all")
        command.add_argument("--version")
        command.add_argument(
            "--documentdb-version",
            help="A reviewed registry release, or all; defaults to the newest",
        )
        command.add_argument("--demonstration", action="store_true")
        command.add_argument("--trigger", choices=TRIGGERS, default="manual")
        if name == "report":
            command.add_argument("--input", type=Path, required=True)
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--expected-run-url")
            command.add_argument("--summary", type=Path)
    args = parser.parse_args()
    try:
        registry = read_registry()
        if args.command == "changed-releases":
            for release in changed_releases(registry, args.previous):
                print(release)
            return 0
        runs = select_runs(
            registry,
            args.integration,
            args.version,
            args.documentdb_version,
            args.demonstration,
            trigger=args.trigger,
        )
        if args.command == "matrix":
            print(json.dumps({"include": runs}, separators=(",", ":")))
            return 0
        success = collect_report(
            args.input,
            args.output,
            registry,
            runs,
            expected_run_url=args.expected_run_url,
            summary=args.summary,
        )
    except (OSError, ValueError) as error:
        print(f"Invalid compatibility workflow request: {error}", file=sys.stderr)
        return 2
    print(f"Combined report: {args.output / 'summary.md'}")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
