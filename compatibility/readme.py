# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

"""Publish a repository-scoped README snapshot from validated execution evidence."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from itertools import groupby
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from compatibility.contracts import read_registry, timestamp, validate_schema
from compatibility.results import (
    RunKey,
    append_result,
    dashboard_rows,
    read_result,
    result_key,
    validate_incoming_result,
)

OFFICIAL_REPOSITORY = "documentdb/documentdb-playground"
WORKFLOW_PATH = ".github/workflows/compatibility.yml"
DEMONSTRATION_TITLE = "Ecosystem compatibility (demonstration)"
PROFILE_FIELDS = ("integration", "documentdb_version", "upstream_version", "profile")
COLORS = {
    "Working": "#176b2c",
    "Failing": "#b42318",
    "Not tested": "#667085",
    "Stale": "#9a6700",
}


def repository_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ValueError("Invalid publication repository")
    return value


def read_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("Publication input must be a bounded regular JSON file")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Publication input must be a JSON object")
    return value


def write_text(path: Path, value: str) -> None:
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError("Publication paths must not be symbolic links")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)


def write_object(path: Path, value: dict[str, Any]) -> None:
    write_text(path, json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def run_url(repository: str, identifier: int, attempt: int) -> str:
    return f"https://github.com/{repository}/actions/runs/{identifier}/attempts/{attempt}"


def validate_attempt(value: dict[str, Any], repository: str) -> None:
    repository_name(repository)
    validate_schema(value, "attempt")
    if value["repository"] != repository or value["run_url"] != run_url(
        repository, value["run_id"], value["run_attempt"]
    ):
        raise ValueError("Workflow attempt does not belong to this repository")
    if timestamp(value["started_at"]) > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("Workflow attempt starts in the future")


def source_attempt(
    run: dict[str, Any], workflow: dict[str, Any], repository: str, branch: str
) -> dict[str, Any]:
    """Accept only completed, same-repository default-branch integration runs."""
    repository_name(repository)
    if (
        run["repository"]["full_name"] != repository
        or run["head_repository"]["full_name"] != repository
        or run["head_branch"] != branch
        or run["event"] != "workflow_dispatch"
        or run["status"] != "completed"
        or workflow["path"] != WORKFLOW_PATH
        or workflow["id"] != run["workflow_id"]
    ):
        raise ValueError("Source is not a trusted completed compatibility workflow run")
    attempt = {
        "schema_version": 1,
        "repository": repository,
        "run_id": run["id"],
        "run_attempt": run["run_attempt"],
        "run_url": run_url(repository, run["id"], run["run_attempt"]),
        "head_sha": run["head_sha"],
        "started_at": run["run_started_at"],
        "conclusion": run["conclusion"],
        "profiles": [],
        "errors": [],
    }
    validate_attempt(attempt, repository)
    return attempt


def github_object(endpoint: str) -> dict[str, Any]:
    response = subprocess.run(
        ["gh", "api", endpoint],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    value = json.loads(response.stdout)
    if not isinstance(value, dict):
        raise ValueError("GitHub returned an unexpected response")
    return value


def prepare(
    repository: str, branch: str, event_name: str, event: dict[str, Any], identifier: str
) -> dict[str, Any]:
    repository_name(repository)
    expected_attempt = None
    if event_name == "workflow_run":
        identifier = str(event["workflow_run"]["id"])
        expected_attempt = event["workflow_run"]["run_attempt"]
    if not identifier:
        return {"action": "refresh"}
    if not re.fullmatch(r"[1-9][0-9]*", identifier):
        raise ValueError("A source run ID must be a positive integer")
    endpoint = f"repos/{repository}/actions/runs/{identifier}"
    if expected_attempt is not None:
        if type(expected_attempt) is not int or expected_attempt < 1:
            raise ValueError("A source attempt must be a positive integer")
        endpoint += f"/attempts/{expected_attempt}"
    run = github_object(endpoint)
    if run["id"] != int(identifier) or (
        expected_attempt is not None and run["run_attempt"] != expected_attempt
    ):
        raise ValueError("GitHub returned a different workflow attempt")
    if type(run["workflow_id"]) is not int or run["workflow_id"] < 1:
        raise ValueError("Invalid source workflow identity")
    workflow = github_object(f"repos/{repository}/actions/workflows/{run['workflow_id']}")
    attempt = source_attempt(run, workflow, repository, branch)
    if run["display_title"] == DEMONSTRATION_TITLE:
        return {"action": "ignore", "reason": "Demonstrations do not update README status"}
    return {"action": "publish", "attempt": attempt}


def profile_key(profile: dict[str, Any]) -> RunKey:
    return (
        profile["integration"],
        profile["documentdb_version"],
        profile["upstream_version"],
        profile["profile"],
        False,
    )


def profile_from_result(result: dict[str, Any]) -> dict[str, Any]:
    integration, database, upstream, profile, _ = result_key(result)
    return {
        "integration": integration,
        "documentdb_version": database,
        "upstream_version": upstream,
        "profile": profile,
    }


def selected_profiles(
    summary: dict[str, Any], registry: dict[str, Any]
) -> tuple[list[dict[str, Any]], bool]:
    if (
        summary.get("schema_version") != 1
        or type(summary.get("success")) is not bool
        or not isinstance(summary.get("integrations"), list)
        or not 0 < len(summary["integrations"]) <= 100
    ):
        raise ValueError("Invalid combined report envelope")
    profiles = []
    modes = set()
    for row in summary["integrations"]:
        if not isinstance(row, dict) or type(row.get("demonstration")) is not bool:
            raise ValueError("Invalid combined report profile")
        profile = {name: row[name] for name in PROFILE_FIELDS}
        spec = registry["integrations"][profile["integration"]]
        if (
            profile["profile"] != spec["profile"]
            or profile["documentdb_version"] not in registry["documentdb"]
            or not re.fullmatch(spec["version_pattern"], profile["upstream_version"])
            or profile in profiles
        ):
            raise ValueError("Combined report does not match the reviewed selection")
        profiles.append(profile)
        modes.add(row["demonstration"])
    if len(modes) != 1:
        raise ValueError("Combined report mixes demonstrations with compatibility evidence")
    return profiles, True in modes


def save_attempt(data: Path, attempt: dict[str, Any], repository: str) -> None:
    """Keep run identity fixed while allowing missing publication evidence to be recovered."""
    validate_attempt(attempt, repository)
    path = data / "attempts" / f"{attempt['run_id']}-{attempt['run_attempt']}.json"
    if path.exists() or path.is_symlink():
        previous = read_object(path)
        validate_attempt(previous, repository)
        for key in attempt.keys() - {"profiles", "errors"}:
            if previous[key] != attempt[key]:
                raise ValueError("Refusing to change an existing workflow attempt's identity")
        profiles = {profile_key(profile): profile for profile in previous["profiles"]}
        profiles.update({profile_key(profile): profile for profile in attempt["profiles"]})
        attempt = {**attempt, "profiles": [profiles[key] for key in sorted(profiles)]}
    write_object(path, attempt)


def ingest(
    plan: dict[str, Any],
    source_root: Path,
    incoming: Path,
    data: Path,
    repository: str,
) -> None:
    if plan["action"] == "refresh":
        return
    if plan["action"] != "publish":
        raise ValueError("Unexpected publication action")
    attempt = dict(plan["attempt"])
    validate_attempt(attempt, repository)
    registry = read_registry(source_root / "compatibility" / "registry.yaml", root=source_root)
    if data.is_symlink() or incoming.is_symlink():
        raise ValueError("Publication directories must not be symbolic links")
    errors = []
    profiles: list[dict[str, Any]] = []
    try:
        profiles, demonstration = selected_profiles(
            read_object(incoming / "summary.json"), registry
        )
        if demonstration:
            print("Demonstration report ignored; normal compatibility evidence is unchanged.")
            return
    except (OSError, ValueError, KeyError, TypeError) as error:
        errors.append(f"Combined report unavailable or rejected: {error}")
    results: list[dict[str, Any]] = []
    directory = incoming / "results"
    if directory.is_symlink():
        errors.append("Rejected symbolic-link result directory")
    elif directory.is_dir():
        paths = sorted(directory.iterdir())
        if len(paths) > 100:
            raise ValueError("Too many incoming result files")
        for path in paths:
            try:
                if not re.fullmatch(r"[a-f0-9]{32}\.json", path.name):
                    raise ValueError("Unexpected result file name")
                result = read_result(path)
                if path.stem != result["id"] or result["demonstration"]:
                    raise ValueError("Result identity or mode does not match publication")
                validate_incoming_result(result, registry, attempt["run_url"], root=source_root)
                profile = profile_from_result(result)
                if profiles and profile not in profiles:
                    raise ValueError("Result is outside the selected profile set")
                if any(result_key(prior) == result_key(result) for prior in results):
                    raise ValueError("More than one result for a selected profile")
                results.append(result)
            except (OSError, ValueError, KeyError, TypeError) as error:
                errors.append(f"Result evidence rejected: {error}")
    else:
        errors.append("No result evidence was retained by this workflow attempt")
    for result in results:
        append_result(data / "results", result)
    if not profiles:
        profiles = [profile_from_result(result) for result in results]
    available = {result_key(result) for result in results}
    for profile in profiles:
        if profile_key(profile) not in available:
            errors.append(f"Missing validated result for {profile['integration']}")
    attempt["profiles"] = profiles
    attempt["errors"] = [message[:1000] for message in errors]
    save_attempt(data, attempt, repository)
    for message in errors:
        print(message, file=sys.stderr)


def snapshot(
    data: Path, registry: dict[str, Any], repository: str, now: datetime
) -> dict[str, Any]:
    repository_name(repository)
    if any((data / name).is_symlink() for name in ("results", "attempts", "site")):
        raise ValueError("Publication directories must not be symbolic links")
    results = [read_result(path) for path in sorted((data / "results").glob("*.json"))]
    normal = [result for result in results if not result["demonstration"]]
    prefix = f"https://github.com/{repository}/actions/runs/"
    if any(not result["run_url"] or not result["run_url"].startswith(prefix) for result in normal):
        raise ValueError("README evidence must originate in this repository's Actions runs")
    attempts = []
    expected: dict[RunKey, str | None] = {
        (name, version, spec["default_version"], spec["profile"], False): None
        for name, spec in registry["integrations"].items()
        if spec["enabled"]
        for version in registry["documentdb"]
    }
    for path in sorted((data / "attempts").glob("*.json")):
        attempt = read_object(path)
        validate_attempt(attempt, repository)
        if path.stem != f"{attempt['run_id']}-{attempt['run_attempt']}":
            raise ValueError("Attempt file does not match its run identity")
        attempts.append(attempt)
        for profile in attempt["profiles"]:
            expected.setdefault(profile_key(profile), None)
    attempts.sort(
        key=lambda attempt: (
            timestamp(attempt["started_at"]),
            attempt["run_id"],
            attempt["run_attempt"],
        )
    )
    rows = dashboard_rows(normal, registry, now, expected_runs=expected)
    for row in rows:
        key = (
            row["integration"],
            row["documentdb_version"],
            row["upstream_version"],
            row["profile"],
            False,
        )
        for attempt in attempts:
            if key not in {profile_key(profile) for profile in attempt["profiles"]}:
                continue
            source_records = [
                result for result in row["history"] if result["run_url"] == attempt["run_url"]
            ]
            newer_record = any(
                timestamp(result["started_at"]) > timestamp(attempt["started_at"])
                and result["run_url"] != attempt["run_url"]
                for result in row["history"]
            )
            if not source_records and not newer_record:
                row.update(
                    last_attempt=attempt["started_at"],
                    latest_attempt_state="Not tested",
                    run_url=attempt["run_url"],
                    error="No validated profile result in this workflow attempt",
                )
        row.pop("history")
    return {
        "schema_version": 1,
        "repository": repository,
        "preview": repository != OFFICIAL_REPOSITORY,
        "latest_workflow": attempts[-1] if attempts else None,
        "integrations": rows,
    }


def utc(value: str | None) -> str:
    return timestamp(value).strftime("%Y-%m-%d %H:%M:%S UTC") if value else "Never"


def markdown_cell(value: str) -> str:
    """Keep evidence and diagnostics as text rather than executable Markdown or HTML."""
    escaped = html.escape(value).replace("\r", " ").replace("\n", " ")
    return re.sub(r"([\\`*_{}\[\]()|])", r"\\\1", escaped)


def markdown(view: dict[str, Any]) -> str:
    lines = ["# Compatibility status", ""]
    lines += [
        "The latest attempt is separate from the last conclusive compatibility evidence.",
        "",
    ]
    latest = view["latest_workflow"]
    if latest:
        lines += [
            f"Latest workflow attempt: [{latest['run_id']}, attempt {latest['run_attempt']}]"
            f"({latest['run_url']}), started **{utc(latest['started_at'])}**, "
            f"workflow conclusion **{latest['conclusion']}**.",
            "",
        ]
        if latest["errors"]:
            lines += ["**Publication/evidence needs attention:**", ""]
            lines += [f"- {markdown_cell(error)}" for error in latest["errors"]]
            lines.append("")
    else:
        lines += ["**No completed compatibility workflow has been published.**", ""]
    for integration, rows in groupby(view["integrations"], key=lambda row: row["integration"]):
        lines += [
            f"## {markdown_cell(integration)}",
            "",
            "| Client / DocumentDB | Profile | Evidence | Verified cases | Last conclusive | "
            "Latest attempt | Attempted at | Details |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for row in rows:
            fields = [
                f"{row['upstream_version']} / {row['documentdb_version']}",
                row["profile"],
                row["state"],
                f"{row['passed']}/{row['expected']}",
                utc(row["conclusive_at"]),
                row["latest_attempt_state"],
                utc(row["last_attempt"]),
            ]
            details = f"[Run]({row['run_url']})" if row["run_url"] else "No run"
            lines.append(
                "| " + " | ".join(markdown_cell(field) for field in fields) + f" | {details} |"
            )
        lines.append("")
    lines += [
        "Timestamps describe executions, not dashboard refreshes. Intentional-failure "
        "demonstrations are excluded. Freshness follows the registry and execution-input digests.",
        "",
        "The JSON snapshot is available in [status.json](status.json). "
        "Detailed reports and diagnostics are retained as artifacts of the linked runs.",
        "",
    ]
    return "\n".join(lines)


def svg(view: dict[str, Any]) -> str:
    rows = view["integrations"]
    height = 213 + 96 * max(1, len(rows))
    root = ElementTree.Element(
        "svg",
        {
            "xmlns": "http://www.w3.org/2000/svg",
            "width": "1240",
            "height": str(height),
            "viewBox": f"0 0 1240 {height}",
            "role": "img",
            "aria-labelledby": "title description",
        },
    )
    ElementTree.SubElement(root, "title", {"id": "title"}).text = "DocumentDB compatibility"
    ElementTree.SubElement(root, "desc", {"id": "description"}).text = (
        "Compatibility evidence, actual execution timestamps, and latest attempt outcomes. "
        "Open the accompanying status details for accessible tables and run links."
    )
    ElementTree.SubElement(
        root, "rect", {"width": "1240", "height": str(height), "rx": "12", "fill": "#ffffff"}
    )

    def text(x: int, y: int, value: str, size: int = 14, color: str = "#182230") -> None:
        ElementTree.SubElement(
            root,
            "text",
            {
                "x": str(x),
                "y": str(y),
                "font-family": "Arial, Helvetica, sans-serif",
                "font-size": str(size),
                "fill": color,
            },
        ).text = value

    text(24, 38, "DocumentDB compatibility", 25)
    text(24, 65, view["repository"], 14, "#475467")
    latest = view["latest_workflow"]
    if latest:
        text(
            24,
            91,
            f"Latest workflow started {utc(latest['started_at'])} | "
            f"Run {latest['run_id']}, attempt {latest['run_attempt']} | "
            f"Conclusion: {latest['conclusion']}",
        )
        if latest["errors"]:
            text(
                24,
                117,
                "Latest workflow has missing or rejected evidence; see status details.",
                color="#b42318",
            )
        else:
            text(24, 117, "Only verified profile results determine compatibility.", color="#475467")
    else:
        text(24, 91, "No completed compatibility workflow has been published.", color="#667085")
        text(24, 117, "No data is Not tested, never a compatibility pass.", color="#667085")
    for x, label in (
        (24, "Integration / profile"),
        (255, "Client"),
        (350, "DocumentDB"),
        (470, "Evidence"),
        (635, "Latest attempt"),
        (795, "Attempted at (UTC)"),
        (1080, "Verified cases"),
    ):
        text(x, 156, label, 13, "#475467")
    for index, row in enumerate(rows):
        top = 171 + index * 96
        ElementTree.SubElement(
            root,
            "rect",
            {
                "x": "16",
                "y": str(top),
                "width": "1208",
                "height": "88",
                "rx": "6",
                "fill": "#f2f4f7" if index % 2 == 0 else "#ffffff",
            },
        )
        text(24, top + 25, row["integration"], 18)
        text(24, top + 49, row["profile"], 11, "#475467")
        text(255, top + 25, row["upstream_version"])
        text(350, top + 25, row["documentdb_version"])
        text(470, top + 25, row["state"], 17, COLORS[row["state"]])
        text(470, top + 49, "Conclusive:", 10, "#475467")
        text(470, top + 66, utc(row["conclusive_at"]), 10, "#475467")
        text(635, top + 25, row["latest_attempt_state"], 17, COLORS[row["latest_attempt_state"]])
        text(795, top + 25, utc(row["last_attempt"]), 13)
        text(1080, top + 25, f"{row['passed']} / {row['expected']}", 17)
    text(
        24,
        height - 10,
        "Execution timestamps do not change when the dashboard is refreshed. "
        "Open status details for history and diagnostics.",
        11,
        "#475467",
    )
    return ElementTree.tostring(root, encoding="unicode") + "\n"


def badge_svg(view: dict[str, Any], integration: str) -> str:
    rows = [row for row in view["integrations"] if row["integration"] == integration]
    states = {row["state"] for row in rows}
    latest = view["latest_workflow"]
    unknown_selection = latest and latest["errors"] and not latest["profiles"]
    if (
        not rows
        or unknown_selection
        or any(row["latest_attempt_state"] == "Not tested" for row in rows)
    ):
        states.add("Not tested")
    state = next(
        value for value in ("Failing", "Not tested", "Stale", "Working") if value in states
    )
    left_width = 20 + 7 * len(integration)
    right_width = 20 + 7 * len(state)
    width = left_width + right_width
    root = ElementTree.Element(
        "svg",
        {
            "xmlns": "http://www.w3.org/2000/svg",
            "width": str(width),
            "height": "24",
            "viewBox": f"0 0 {width} 24",
            "role": "img",
            "aria-labelledby": "title description",
        },
    )
    ElementTree.SubElement(root, "title", {"id": "title"}).text = f"{integration}: {state}"
    details = [
        f"Client {row['upstream_version']}, DocumentDB {row['documentdb_version']}, "
        f"profile {row['profile']}: {row['state']}. "
        f"Latest attempt: {row['latest_attempt_state']} at {utc(row['last_attempt'])}."
        for row in rows
    ]
    if unknown_selection:
        details.append("The latest workflow has no validated profile selection or evidence.")
    ElementTree.SubElement(root, "desc", {"id": "description"}).text = " ".join(
        [*details, "Open the integration details for execution timestamps and run links."]
    )
    clip = ElementTree.SubElement(
        ElementTree.SubElement(root, "defs"), "clipPath", {"id": "rounded"}
    )
    ElementTree.SubElement(clip, "rect", {"width": str(width), "height": "24", "rx": "4"})
    background = ElementTree.SubElement(root, "g", {"clip-path": "url(#rounded)"})
    ElementTree.SubElement(
        background, "rect", {"width": str(width), "height": "24", "fill": "#475467"}
    )
    ElementTree.SubElement(
        background,
        "rect",
        {"x": str(left_width), "width": str(right_width), "height": "24", "fill": COLORS[state]},
    )
    for x, value in (
        (left_width / 2, integration),
        (left_width + right_width / 2, state),
    ):
        ElementTree.SubElement(
            root,
            "text",
            {
                "x": str(x),
                "y": "16",
                "fill": "#ffffff",
                "font-family": "Verdana, DejaVu Sans, sans-serif",
                "font-size": "12",
                "text-anchor": "middle",
                "textLength": str(7 * len(value)),
                "lengthAdjust": "spacingAndGlyphs",
            },
        ).text = value
    return ElementTree.tostring(root, encoding="unicode") + "\n"


def render(data: Path, site: Path, repository: str, *, now: datetime | None = None) -> None:
    view = snapshot(data, read_registry(), repository, now or datetime.now(timezone.utc))
    write_text(site / "status.svg", svg(view))
    for integration in sorted({row["integration"] for row in view["integrations"]}):
        write_text(site / "badges" / f"{integration}.svg", badge_svg(view, integration))
    write_text(site / "README.md", markdown(view))
    write_object(site / "status.json", view)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preparing = commands.add_parser("prepare")
    preparing.add_argument("--branch", required=True)
    preparing.add_argument(
        "--event-name",
        required=True,
        choices=["workflow_run", "workflow_dispatch", "push", "schedule"],
    )
    preparing.add_argument("--event-file", type=Path, required=True)
    preparing.add_argument("--run-id", default="")
    preparing.add_argument("--output", type=Path, required=True)
    ingesting = commands.add_parser("ingest")
    ingesting.add_argument("--source", type=Path, required=True)
    ingesting.add_argument("--source-root", type=Path, required=True)
    ingesting.add_argument("--incoming", type=Path, required=True)
    rendering = commands.add_parser("render")
    rendering.add_argument("--site", type=Path, required=True)
    for command in (preparing, ingesting, rendering):
        command.add_argument("--repository", required=True)
    for command in (ingesting, rendering):
        command.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            plan = prepare(
                args.repository,
                args.branch,
                args.event_name,
                read_object(args.event_file),
                args.run_id,
            )
            write_object(args.output, plan)
            outputs = {"action": plan["action"]}
            if plan["action"] == "publish":
                attempt = plan["attempt"]
                outputs.update(
                    run_id=str(attempt["run_id"]),
                    artifact=f"compatibility-report-{attempt['run_attempt']}",
                    source_sha=attempt["head_sha"],
                )
            if os.environ.get("GITHUB_OUTPUT"):
                with Path(os.environ["GITHUB_OUTPUT"]).open("a") as destination:
                    for key, value in outputs.items():
                        destination.write(f"{key}={value}\n")
            print(plan.get("reason", f"README publication action: {plan['action']}"))
        elif args.command == "ingest":
            ingest(
                read_object(args.source),
                args.source_root,
                args.incoming,
                args.data,
                args.repository,
            )
        else:
            render(args.data, args.site, args.repository)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"README publication failed: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
