# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

"""Registry-driven fan-out and combined reports with incomplete or conflicting evidence."""

import json
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
import yaml
from packaging.version import Version

from compatibility import readme, workflow
from compatibility.contracts import (
    ROOT,
    RUNTIME_PREFIXES,
    expected_tests,
    npm_bundle_digest,
    suite_digest,
    timestamp,
)
from compatibility.results import append_result, dashboard_rows

pytestmark = pytest.mark.unit
RUN_URL = "https://github.com/example/project/actions/runs/42/attempts/3"


@pytest.fixture
def runs(registry, selection):
    return workflow.select_runs(registry, "all", None, selection["documentdb_version"])


@pytest.fixture
def records(registry, record, runs):
    results = {}
    for index, run in enumerate(runs):
        integration = run["integration"]
        spec = registry["integrations"][integration]
        result = deepcopy(record)
        result.update(
            id=f"{index + 1:032x}",
            integration=integration,
            suite_digest=suite_digest(integration, spec),
            **{key: spec[key] for key in ("repository", "owner", "profile")},
        )
        result["expected_tests"] = expected_tests(spec, False)
        result["tests"] = [
            {"id": name, "outcome": "passed", "message": ""} for name in result["expected_tests"]
        ]
        runtime = spec["runtime"]
        parts = RUNTIME_PREFIXES[runtime].rstrip(".").split(".")
        result["upstream"].update(
            version=run["version"],
            actual_version=run["version"],
            runtime={"name": runtime, "version": ".".join(parts + ["0"] * (3 - len(parts)))},
            dependencies=[{"name": spec["package"], "version": run["version"], "sha256": "b" * 64}],
        )
        results[integration] = result
    return results


def write_records(path, records, release=None):
    for integration, result in records.items():
        directory = path / integration / (release or result["documentdb"]["version"])
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "result.json").write_text(json.dumps(result))
    return path


def read_report(output):
    report = json.loads((output / "summary.json").read_text())
    history = json.loads((output / "site/history.json").read_text())
    return report, {row["integration"]: row for row in report["integrations"]}, history


def test_all_uses_each_enabled_default_and_accepts_new_registry_entries(registry, selection):
    extra = deepcopy(registry["integrations"][selection["integration"]])
    extra.update(default_version="99.1.2", version_pattern=r"^99\.1\.2$")
    registry["integrations"]["additional"] = extra
    registry["integrations"][selection["integration"]]["enabled"] = False
    runs = workflow.select_runs(registry, "all", None, selection["documentdb_version"], True)
    assert {run["integration"]: run["version"] for run in runs} == {
        name: spec["default_version"]
        for name, spec in registry["integrations"].items()
        if spec["enabled"]
    }
    assert all(
        run["documentdb_version"] == selection["documentdb_version"] and run["demonstration"]
        for run in runs
    )


@pytest.mark.parametrize("override", [False, True])
def test_single_integration_default_and_override(registry, selection, other_version, override):
    version = other_version if override else None
    runs = workflow.select_runs(
        registry, selection["integration"], version, selection["documentdb_version"]
    )
    assert runs == [
        {
            **selection,
            "version": version or selection["version"],
            "demonstration": False,
            "trigger": "manual",
        }
    ]


@pytest.mark.parametrize("integration", ["nodejs", "mongoose"])
def test_watch_ranges_do_not_broaden_manual_pins(registry, selection, integration):
    spec = registry["integrations"][integration]
    major, minor, _ = spec["default_version"].split(".")
    version = f"{major}.{int(minor) + 1}.0"
    with pytest.raises(ValueError, match="outside the policy"):
        workflow.select_runs(registry, integration, version, selection["documentdb_version"])
    runs = workflow.select_runs(
        registry, integration, version, selection["documentdb_version"], trigger="upstream_release"
    )
    assert runs[0]["version"] == version
    for rejected in (f"{int(major) + 1}.0.0", f"{major}.0.0", f"{version}-rc.1"):
        with pytest.raises(ValueError, match="outside the policy"):
            workflow.select_runs(
                registry,
                integration,
                rejected,
                selection["documentdb_version"],
                trigger="upstream_release",
            )


def test_newest_release_is_the_default_and_all_selects_every_release(registry, selection):
    reviewed = registry["documentdb"][selection["documentdb_version"]]
    for release in ("0.9.0", "999.0.0"):
        registry["documentdb"][release] = deepcopy(reviewed)
    enabled = sorted(name for name, spec in registry["integrations"].items() if spec["enabled"])
    newest = workflow.select_runs(registry, selection["integration"], None, None)
    assert [run["documentdb_version"] for run in newest] == ["999.0.0"]
    every = workflow.select_runs(registry, "all", None, "all", trigger="scheduled")
    assert [(run["integration"], run["documentdb_version"]) for run in every] == [
        (name, release)
        for name in enabled
        for release in sorted(registry["documentdb"], key=Version)
    ]
    assert all(run["trigger"] == "scheduled" for run in every)
    release = workflow.select_runs(registry, "all", None, "0.9.0", trigger="documentdb_release")
    assert [(run["integration"], run["documentdb_version"], run["trigger"]) for run in release] == [
        (name, "0.9.0", "documentdb_release") for name in enabled
    ]


@pytest.mark.parametrize(
    ("trigger", "integration", "version", "database", "demonstration", "error"),
    [
        ("scheduled", "all", None, None, False, "every reviewed DocumentDB release"),
        ("scheduled", "all", None, "selected", False, "every reviewed DocumentDB release"),
        ("documentdb_release", "all", None, None, False, "one reviewed release"),
        ("documentdb_release", "all", None, "all", False, "one reviewed release"),
        ("scheduled", "selected", None, "all", False, "every enabled integration"),
        ("documentdb_release", "all", "selected", "selected", False, "every enabled integration"),
        ("documentdb_release", "all", None, "selected", True, "every enabled integration"),
        ("push", "all", None, "selected", False, "Unknown execution trigger"),
    ],
)
def test_automatic_runs_cannot_narrow_the_reviewed_selection(
    registry, selection, trigger, integration, version, database, demonstration, error
):
    with pytest.raises(ValueError, match=error):
        workflow.select_runs(
            registry,
            selection["integration"] if integration == "selected" else integration,
            selection["version"] if version == "selected" else version,
            selection["documentdb_version"] if database == "selected" else database,
            demonstration,
            trigger=trigger,
        )


def test_changed_releases_are_added_or_re_pinned_entries(tmp_path, registry, selection):
    release = selection["documentdb_version"]
    previous = deepcopy(registry)
    for added in ("999.0.0", "0.9.0"):
        registry["documentdb"][added] = deepcopy(registry["documentdb"][release])
    source = tmp_path / "previous.yaml"
    source.write_text(yaml.safe_dump(previous))
    assert workflow.changed_releases(registry, source) == ["0.9.0", "999.0.0"]
    image = previous["documentdb"][release]["image"]
    previous["documentdb"][release]["image"] = image[:-1] + ("1" if image[-1] == "0" else "0")
    source.write_text(yaml.safe_dump(previous))
    assert workflow.changed_releases(registry, source) == ["0.9.0", release, "999.0.0"]
    previous["documentdb"] = deepcopy(registry["documentdb"])
    previous["integrations"][selection["integration"]]["default_version"] = "0.0.0"
    source.write_text(yaml.safe_dump(previous))
    assert workflow.changed_releases(registry, source) == []


@pytest.mark.parametrize(
    "contents", [None, "", "{", "- documentdb\n", "schema_version: 1\n", "documentdb: []\n"]
)
def test_unreadable_previous_registry_selects_every_release(tmp_path, registry, contents):
    source = tmp_path / "previous.yaml"
    if contents is not None:
        source.write_text(contents)
    assert workflow.changed_releases(registry, source) == sorted(
        registry["documentdb"], key=Version
    )


@pytest.mark.parametrize(
    ("integration", "version", "database", "error"),
    [
        ("all", "1.2.3", None, "single integration"),
        ("unknown", None, None, "Unknown or disabled"),
        (None, "not-a-version; echo unsafe", None, "outside the policy"),
        ("all", None, "99.99.99", "Unknown DocumentDB"),
    ],
)
def test_invalid_selection_is_rejected(registry, selection, integration, version, database, error):
    with pytest.raises(ValueError, match=error):
        workflow.select_runs(
            registry,
            integration or selection["integration"],
            version,
            database or selection["documentdb_version"],
        )


def test_disabled_or_empty_selection_is_rejected(registry, selection):
    for spec in registry["integrations"].values():
        spec["enabled"] = False
    with pytest.raises(ValueError, match="disabled"):
        workflow.select_runs(
            registry, selection["integration"], None, selection["documentdb_version"]
        )
    with pytest.raises(ValueError, match="No enabled"):
        workflow.select_runs(registry, "all", None, selection["documentdb_version"])


def test_matrix_cli_serializes_the_selected_runs(monkeypatch, capsys, selection, runs):
    monkeypatch.setattr(
        sys, "argv", ["workflow", "matrix", "--documentdb-version", selection["documentdb_version"]]
    )
    assert workflow.main() == 0
    assert json.loads(capsys.readouterr().out) == {"include": runs}


def test_matrix_cli_does_not_emit_a_plan_for_invalid_input(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["workflow", "matrix", "--version", "1.2.3"])
    assert workflow.main() == 2
    captured = capsys.readouterr()
    assert not captured.out and "single integration" in captured.err


@pytest.mark.parametrize("authorized", [False, True])
def test_upstream_matrix_uses_only_ledger_authorized_artifacts(
    monkeypatch, capsys, selection, authorized
):
    def authorize(registry, runs, identifier, nonce):
        assert runs == [{**selection, "demonstration": False, "trigger": "upstream_release"}]
        assert identifier == "a" * 64 and nonce == "b" * 32
        if not authorized:
            raise ValueError("Fixture ledger rejected this request")
        return {"artifact_sha256s": ["c" * 64]}

    monkeypatch.setattr(workflow, "validate_dispatch", authorize)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "workflow",
            "matrix",
            "--integration",
            selection["integration"],
            "--version",
            selection["version"],
            "--documentdb-version",
            selection["documentdb_version"],
            "--detection-id",
            "a" * 64,
            "--dispatch-id",
            "b" * 32,
        ],
    )
    assert workflow.main() == (0 if authorized else 2)
    captured = capsys.readouterr()
    if authorized:
        assert json.loads(captured.out)["include"] == [
            {
                **selection,
                "demonstration": False,
                "trigger": "upstream_release",
                "detection_id": "a" * 64,
                "artifact_sha256s": ["c" * 64],
            }
        ]
    else:
        assert not captured.out and "ledger rejected" in captured.err


@pytest.mark.parametrize("trigger", ["scheduled", "documentdb_release"])
def test_watcher_provenance_cannot_be_recorded_as_another_trigger(
    monkeypatch, capsys, selection, trigger
):
    monkeypatch.setattr(workflow, "validate_dispatch", lambda *arguments: {})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "workflow",
            "matrix",
            "--integration",
            selection["integration"],
            "--documentdb-version",
            selection["documentdb_version"],
            "--trigger",
            trigger,
            "--detection-id",
            "a" * 64,
            "--dispatch-id",
            "b" * 32,
        ],
    )
    assert workflow.main() == 2
    captured = capsys.readouterr()
    assert not captured.out and "cannot be combined" in captured.err


@pytest.mark.parametrize("matching", [False, True])
def test_upstream_report_preserves_only_matching_detection_provenance(
    tmp_path, registry, runs, records, matching, npm_bundle
):
    for record in records.values():
        record.update(trigger="upstream_release", detection_id="a" * 64, run_url=RUN_URL)
        if record["upstream"]["runtime"]["name"] == "nodejs":
            bundle = npm_bundle(record["integration"])
            record["upstream"].update(
                npm_bundle=bundle, npm_bundle_sha256=npm_bundle_digest(bundle)
            )
    incoming = write_records(tmp_path / "incoming", records)
    output = tmp_path / "report"
    assert (
        workflow.collect_report(
            incoming,
            output,
            registry,
            [{**run, "trigger": "upstream_release"} for run in runs],
            expected_run_url=RUN_URL,
            detection_id=("a" if matching else "b") * 64,
        )
        is matching
    )
    _, rows, history = read_report(output)
    if matching:
        assert {record["integration"]: record for record in history} == records
        assert all(row["detection_id"] == "a" * 64 for row in rows.values())
        assert all(row["trigger"] == "upstream_release" for row in rows.values())
        assert "upstream\\_release" in (output / "summary.md").read_text()
        assert "a" * 64 in (output / "site/index.html").read_text()
    else:
        assert history == [] and all(row["state"] == "Not tested" for row in rows.values())


@pytest.mark.parametrize("integration", ["nodejs", "mongoose"])
@pytest.mark.parametrize("outcome", ["passed", "failed", "missing", "wrong-bundle"])
def test_candidate_evidence_flows_through_reporting_and_readme_publication(
    tmp_path, registry, records, npm_bundle, integration, outcome
):
    baseline = records[integration]
    baseline["run_url"] = "https://github.com/example/project/actions/runs/41/attempts/1"
    data = tmp_path / "data"
    append_result(data / "results", baseline)
    spec = registry["integrations"][integration]
    major, minor, _ = spec["default_version"].split(".")
    version = f"{major}.{int(minor) + 1}.0"
    bundle = npm_bundle(integration, version)
    digest = npm_bundle_digest(bundle)
    candidate = deepcopy(baseline)
    candidate.update(
        id="c" * 32, trigger="upstream_release", detection_id="a" * 64, run_url=RUN_URL
    )
    candidate["upstream"].update(
        version=version, actual_version=version, npm_bundle=bundle, npm_bundle_sha256=digest
    )
    candidate["upstream"]["dependencies"][0]["version"] = version
    if outcome == "failed":
        candidate["tests"][0].update(outcome="failed", message="Synthetic compatibility failure")
    incoming = write_records(
        tmp_path / "incoming", {} if outcome == "missing" else {integration: candidate}
    )
    report = tmp_path / "report"
    runs = workflow.select_runs(
        registry,
        integration,
        version,
        baseline["documentdb"]["version"],
        trigger="upstream_release",
    )
    success = workflow.collect_report(
        incoming,
        report,
        registry,
        runs,
        expected_run_url=RUN_URL,
        detection_id="a" * 64,
        expected_npm_bundle_sha256="f" * 64 if outcome == "wrong-bundle" else digest,
    )
    assert success is (outcome == "passed")
    attempt = {
        "schema_version": 1,
        "repository": "example/project",
        "run_id": 42,
        "run_attempt": 3,
        "run_url": RUN_URL,
        "head_sha": "a" * 40,
        "started_at": (timestamp(candidate["started_at"]) - timedelta(seconds=10)).isoformat(),
        "conclusion": "success" if success else "failure",
        "profiles": [],
        "errors": [],
    }
    readme.ingest({"action": "publish", "attempt": attempt}, ROOT, report, data, "example/project")
    view = readme.snapshot(data, registry, "example/project", datetime.now(timezone.utc))
    expected = {"passed": "Working", "failed": "Failing"}.get(outcome, "Not tested")
    row = next(
        row
        for row in view["integrations"]
        if row["integration"] == integration and row["upstream_version"] == version
    )
    assert row["state"] == expected
    assert f"{integration}: {expected}" in readme.badge_svg(view, integration)
    if outcome in {"passed", "failed"}:
        saved = json.loads((data / "results" / f"{candidate['id']}.json").read_text())
        assert saved["upstream"]["npm_bundle"] == bundle
        assert saved["upstream"]["npm_bundle_sha256"] == digest
    else:
        assert not (data / "results" / f"{candidate['id']}.json").exists()


@pytest.mark.parametrize("latest_candidate", [False, True])
def test_incomplete_runs_cannot_reuse_a_different_dependency_tree(
    registry, records, npm_bundle, latest_candidate
):
    baseline = records["nodejs"]
    candidate = deepcopy(baseline)
    bundle = npm_bundle()
    candidate.update(id="b" * 32, trigger="upstream_release", detection_id="a" * 64)
    candidate["upstream"].update(npm_bundle=bundle, npm_bundle_sha256=npm_bundle_digest(bundle))
    previous, latest = (baseline, candidate) if latest_candidate else (candidate, baseline)
    latest["started_at"] = (timestamp(previous["finished_at"]) + timedelta(seconds=1)).isoformat()
    latest["finished_at"] = (timestamp(latest["started_at"]) + timedelta(seconds=1)).isoformat()
    latest["tests"] = []
    latest["execution_error"] = "Candidate preparation unavailable"
    rows = dashboard_rows([previous, latest], registry, datetime.now(timezone.utc))
    row = next(row for row in rows if row["integration"] == "nodejs")
    assert row["state"] == "Stale" and row["latest_attempt_state"] == "Not tested"


def test_combined_passes_preserve_envelopes_and_share_dashboard_data(
    tmp_path, registry, runs, records
):
    for record in records.values():
        record["run_url"] = RUN_URL
    incoming = write_records(tmp_path / "incoming", records)
    output = tmp_path / "report"
    summary = tmp_path / "step-summary"
    summary.write_text("Earlier step\n")
    assert workflow.collect_report(
        incoming, output, registry, runs, expected_run_url=RUN_URL, summary=summary
    )
    report, rows, history = read_report(output)
    assert report["schema_version"] == 1 and report["success"] and report["errors"] == []
    assert set(rows) == set(records)
    assert {record["integration"]: record for record in history} == records
    assert {path.stem for path in (output / "results").glob("*.json")} == {
        record["id"] for record in records.values()
    }
    for integration, row in rows.items():
        assert row["state"] == "Working"
        assert row["passed"] == row["expected"] == len(records[integration]["expected_tests"])
        assert row["run_url"] == RUN_URL and row["issue_url"] is None
    assert (
        json.loads((output / "site/current.json").read_text())["integrations"]
        == report["integrations"]
    )
    assert summary.read_text() == "Earlier step\n" + (output / "summary.md").read_text()
    assert "All selected profiles passed." in summary.read_text()


@pytest.mark.parametrize("outcome", ["failed", "error", "skipped", "infrastructure", "incomplete"])
def test_one_nonpassing_profile_does_not_erase_other_results(
    tmp_path, registry, selection, runs, records, outcome
):
    integration = selection["integration"]
    record = records[integration]
    if outcome == "infrastructure":
        record["tests"] = []
        record["execution_error"] = "Fixture unavailable"
    elif outcome == "incomplete":
        record["tests"].pop()
    else:
        record["tests"][0].update(outcome=outcome, message="Scenario diagnostic")
    incoming = write_records(tmp_path / "incoming", records)
    output = tmp_path / "report"
    assert not workflow.collect_report(incoming, output, registry, runs)
    report, rows, history = read_report(output)
    assert not report["success"]
    assert rows[integration]["state"] == ("Failing" if outcome == "failed" else "Not tested")
    assert all(row["state"] == "Working" for name, row in rows.items() if name != integration)
    assert {record["integration"]: record for record in history} == records


@pytest.mark.parametrize(
    ("field", "value", "diagnostic"),
    [
        (("integration",), "other", "selected integration"),
        (("upstream", "version"), "99.1.2", "selected integration"),
        (("documentdb", "version"), "99.1.2", "selected integration"),
        (("profile",), "other-profile", "selected integration"),
        (("demonstration",), True, "selected integration"),
        (("trigger",), "scheduled", "selected integration"),
        (("repository",), "https://github.com/example/other", "repository"),
        (("owner",), "@example/other", "owner"),
        (("suite_digest",), "0" * 64, "suite revision"),
        (("documentdb", "actual_extension_version"), "99.1-2", "installed extension"),
        (
            ("documentdb", "image"),
            "ghcr.io/documentdb/documentdb/documentdb-local@sha256:" + "0" * 64,
            "reviewed database artifact",
        ),
        (("upstream", "runtime", "name"), "nodejs", "runtime"),
        (("expected_tests",), ["test_other"], "undeclared scenario"),
    ],
)
def test_unmatched_or_unreviewed_evidence_is_rejected_independently(
    tmp_path, registry, selection, runs, records, field, value, diagnostic
):
    integration = selection["integration"]
    target = records[integration]
    for part in field[:-1]:
        target = target[part]
    target[field[-1]] = value
    incoming = write_records(tmp_path / "incoming", records, selection["documentdb_version"])
    output = tmp_path / "report"
    assert not workflow.collect_report(incoming, output, registry, runs)
    _, rows, history = read_report(output)
    assert rows[integration]["state"] == "Not tested"
    assert diagnostic in rows[integration]["error"]
    assert rows[integration]["passed"] == 0 and rows[integration]["last_attempt"] is None
    assert all(row["state"] == "Working" for name, row in rows.items() if name != integration)
    assert {record["integration"] for record in history} == set(records) - {integration}


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "https://github.com/example/project/actions/runs/42",
        "https://github.com/example/project/actions/runs/42/attempts/2",
        "https://github.com/example/other/actions/runs/42/attempts/3",
        "https://github.com/example/project/actions/runs/43/attempts/3",
    ],
)
def test_other_runs_and_attempts_never_fill_current_evidence(
    tmp_path, registry, selection, runs, records, origin
):
    for record in records.values():
        record["run_url"] = RUN_URL
    records[selection["integration"]]["run_url"] = origin
    incoming = write_records(tmp_path / "incoming", records)
    output = tmp_path / "report"
    assert not workflow.collect_report(incoming, output, registry, runs, expected_run_url=RUN_URL)
    _, rows, history = read_report(output)
    assert rows[selection["integration"]]["state"] == "Not tested"
    assert "workflow run" in rows[selection["integration"]]["error"]
    assert {record["integration"] for record in history} == set(records) - {
        selection["integration"]
    }


@pytest.mark.parametrize(
    "kind",
    [
        "absent",
        "json",
        "schema",
        "encoding",
        "oversized",
        "directory",
        "file-link",
        "directory-link",
        "integration-link",
    ],
)
def test_missing_or_malformed_artifacts_still_produce_a_combined_report(
    tmp_path, registry, selection, runs, records, kind
):
    integration = selection["integration"]
    incoming = write_records(tmp_path / "incoming", records)
    path = incoming / integration / selection["documentdb_version"] / "result.json"
    path.unlink()
    if kind == "json":
        path.write_text("{")
    elif kind == "schema":
        path.write_text("{}")
    elif kind == "encoding":
        path.write_bytes(b"\xff")
    elif kind == "oversized":
        path.write_bytes(b" " * (1024 * 1024 + 1))
    elif kind == "directory":
        path.mkdir()
    elif kind == "file-link":
        path.symlink_to(tmp_path / "absent.json")
    elif kind == "directory-link":
        path.parent.rmdir()
        path.parent.symlink_to(tmp_path / "absent-directory", target_is_directory=True)
    elif kind == "integration-link":
        # A valid record behind a linked integration directory is still rejected.
        target = tmp_path / "linked-integration"
        path.parent.parent.rename(target)
        (target / path.parent.name / "result.json").write_text(json.dumps(records[integration]))
        path.parent.parent.symlink_to(target, target_is_directory=True)
    output = tmp_path / "report"
    assert not workflow.collect_report(incoming, output, registry, runs)
    report, rows, history = read_report(output)
    assert not report["success"] and rows[integration]["state"] == "Not tested"
    assert rows[integration]["error"].startswith("Result unavailable:")
    assert all(row["state"] == "Working" for name, row in rows.items() if name != integration)
    assert {record["integration"] for record in history} == set(records) - {integration}
    assert "Result unavailable:" in (output / "summary.md").read_text()


@pytest.mark.parametrize("missing", [False, True])
def test_override_has_only_the_selected_integration_and_version(
    tmp_path, registry, selection, other_version, records, missing
):
    runs = workflow.select_runs(
        registry, selection["integration"], other_version, selection["documentdb_version"]
    )
    incoming = tmp_path / "incoming"
    record = records[selection["integration"]]
    record["upstream"].update(version=other_version, actual_version=other_version)
    record["upstream"]["dependencies"][0]["version"] = other_version
    if not missing:
        write_records(incoming, {selection["integration"]: record})
    output = tmp_path / "report"
    assert workflow.collect_report(incoming, output, registry, runs) is not missing
    _, rows, history = read_report(output)
    assert set(rows) == {selection["integration"]}
    assert history == ([] if missing else [record])
    row = rows[selection["integration"]]
    assert (row["upstream_version"], row["state"], row["passed"]) == (
        other_version,
        "Not tested" if missing else "Working",
        0 if missing else len(record["expected_tests"]),
    )
    if missing:
        assert not list((output / "results").glob("*.json"))


@pytest.mark.parametrize("missing", [False, True])
def test_demonstrations_have_separate_rows_and_complete_expected_coverage(
    tmp_path, registry, selection, records, missing
):
    runs = workflow.select_runs(registry, "all", None, selection["documentdb_version"], True)
    for integration, record in records.items():
        record["demonstration"] = True
        record["expected_tests"] = expected_tests(registry["integrations"][integration], True)
        record["tests"].append(
            {
                "id": record["expected_tests"][-1],
                "outcome": "failed",
                "message": "Deliberate mismatch",
            }
        )
    incoming = tmp_path / "incoming"
    if not missing:
        write_records(incoming, records)
    output = tmp_path / "report"
    assert not workflow.collect_report(incoming, output, registry, runs)
    _, rows, history = read_report(output)
    assert set(rows) == set(records)
    for integration, row in rows.items():
        assert row["demonstration"]
        assert row["scenarios"] == expected_tests(registry["integrations"][integration], True)
        assert row["expected"] == len(row["scenarios"])
        assert row["state"] == ("Not tested" if missing else "Failing")
    assert all(record["demonstration"] for record in history)
    assert "Demonstration" in (output / "summary.md").read_text()


def test_conflicting_ids_do_not_overwrite_other_integration_evidence(
    tmp_path, registry, selection, record, monkeypatch
):
    first = deepcopy(record)
    second = {**deepcopy(record), "integration": "additional"}
    registry["integrations"]["additional"] = deepcopy(
        registry["integrations"][first["integration"]]
    )
    # Exercise cross-integration collisions without requiring a second real adapter.
    monkeypatch.setattr("compatibility.results.suite_digest", lambda *_: first["suite_digest"])
    runs = [
        {**selection, "demonstration": False, "trigger": "manual"},
        {**selection, "integration": "additional", "demonstration": False, "trigger": "manual"},
    ]
    records = {first["integration"]: first, second["integration"]: second}
    incoming = write_records(tmp_path / "incoming", records)
    output = tmp_path / "report"
    assert not workflow.collect_report(incoming, output, registry, runs)
    _, rows, history = read_report(output)
    assert rows[first["integration"]]["state"] == "Working"
    assert rows[second["integration"]]["state"] == "Not tested"
    assert "same run ID" in rows[second["integration"]]["error"]
    assert first in history and second not in history


@pytest.mark.parametrize("level", ["integration", "release"])
def test_unexpected_artifacts_are_reported_without_erasing_valid_results(
    tmp_path, registry, selection, runs, records, level
):
    incoming = write_records(tmp_path / "incoming", records)
    parent = incoming if level == "integration" else incoming / selection["integration"]
    (parent / "unexpected").mkdir()
    output = tmp_path / "report"
    assert not workflow.collect_report(incoming, output, registry, runs)
    report, rows, history = read_report(output)
    assert report["errors"] and not report["success"]
    assert all(row["state"] == "Working" for row in rows.values())
    assert {record["integration"]: record for record in history} == records
    assert "Reporting errors" in (output / "summary.md").read_text()


@pytest.mark.parametrize("missing", [False, True])
def test_scheduled_reports_keep_each_release_independent(
    tmp_path, registry, selection, records, missing
):
    release = selection["documentdb_version"]
    registry["documentdb"]["999.0.0"] = deepcopy(registry["documentdb"][release])
    runs = workflow.select_runs(registry, "all", None, "all", trigger="scheduled")
    incoming = tmp_path / "incoming"
    expected = []
    for index, database in enumerate(sorted(registry["documentdb"], key=Version)):
        current = deepcopy(records)
        for offset, record in enumerate(current.values()):
            record.update(id=f"{index + 1:016x}{offset + 1:016x}", trigger="scheduled")
            record["documentdb"]["version"] = database
            expected.append(record)
        if not (missing and database == "999.0.0"):
            write_records(incoming, current)
    output = tmp_path / "report"
    assert workflow.collect_report(incoming, output, registry, runs) is not missing
    report, _, history = read_report(output)
    states = {
        (row["integration"], row["documentdb_version"]): row["state"]
        for row in report["integrations"]
    }
    assert states == {
        (run["integration"], run["documentdb_version"]): (
            "Not tested" if missing and run["documentdb_version"] == "999.0.0" else "Working"
        )
        for run in runs
    }
    assert sorted(history, key=lambda record: record["id"]) == [
        record
        for record in expected
        if not (missing and record["documentdb"]["version"] == "999.0.0")
    ]


def test_nested_report_is_not_mistaken_for_an_incoming_artifact(tmp_path, registry, runs, records):
    incoming = write_records(tmp_path / "incoming", records)
    output = incoming / "report"
    assert workflow.collect_report(incoming, output, registry, runs)
    report, _, history = read_report(output)
    assert report["errors"] == []
    assert {record["integration"]: record for record in history} == records


def test_existing_report_and_symbolic_input_root_are_rejected(tmp_path, registry, runs, records):
    incoming = write_records(tmp_path / "incoming", records)
    output = tmp_path / "report"
    output.mkdir()
    sentinel = output / "preserved"
    sentinel.write_text("Original report")
    with pytest.raises(ValueError, match="overwrite"):
        workflow.collect_report(incoming, output, registry, runs)
    assert sentinel.read_text() == "Original report"
    link = tmp_path / "linked"
    link.symlink_to(incoming, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"):
        workflow.collect_report(link, tmp_path / "another-report", registry, runs)


def test_expired_success_is_not_reported_as_a_current_pass(
    tmp_path, registry, selection, runs, records
):
    record = records[selection["integration"]]
    age = timedelta(days=registry["integrations"][selection["integration"]]["freshness_days"] + 1)
    for key in ("started_at", "finished_at"):
        record[key] = (timestamp(record[key]) - age).isoformat()
    incoming = write_records(tmp_path / "incoming", records)
    output = tmp_path / "report"
    assert not workflow.collect_report(incoming, output, registry, runs)
    _, rows, _ = read_report(output)
    assert rows[selection["integration"]]["state"] == "Stale"


def test_summary_diagnostics_cannot_inject_markup_or_extra_rows(
    tmp_path, registry, selection, runs, records
):
    records[selection["integration"]]["tests"][0].update(
        outcome="failed", message="<script>bad</script>\n| Working | [click](https://example.com)\r"
    )
    incoming = write_records(tmp_path / "incoming", records)
    output = tmp_path / "report"
    assert not workflow.collect_report(incoming, output, registry, runs)
    summary = (output / "summary.md").read_text()
    assert "<script>" not in summary and "&lt;script&gt;" in summary
    assert "\\| Working \\|" in summary
    assert "\\[click\\]\\(https://example.com\\)" in summary
    assert len([line for line in summary.splitlines() if line.startswith("|")]) == len(runs) + 2


@pytest.mark.parametrize("outcome", ["pass", "fail", "missing"])
def test_report_cli_returns_failure_without_losing_the_summary(
    tmp_path, monkeypatch, registry, selection, records, outcome
):
    incoming = tmp_path / "incoming"
    if outcome == "fail":
        records[selection["integration"]]["tests"][0]["outcome"] = "failed"
    if outcome != "missing":
        write_records(incoming, records)
    output = tmp_path / "report"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "workflow",
            "report",
            "--input",
            str(incoming),
            "--output",
            str(output),
            "--documentdb-version",
            selection["documentdb_version"],
        ],
    )
    assert workflow.main() == (0 if outcome == "pass" else 1)
    report, _, _ = read_report(output)
    assert report["success"] == (outcome == "pass")
