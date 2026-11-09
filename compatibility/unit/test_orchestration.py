# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

"""Registry-driven fan-out and combined reports with incomplete or conflicting evidence."""

import json
import sys
from copy import deepcopy

import pytest
import yaml
from packaging.version import Version

from compatibility import workflow
from compatibility.contracts import (
    RUNTIME_PREFIXES,
    expected_tests,
    npm_bundle_digest,
    suite_digest,
)

pytestmark = pytest.mark.unit
RUN_URL = "https://github.com/example/project/actions/runs/42/attempts/3"
PROVENANCE = ["--detection-id", "a" * 64, "--dispatch-id", "b" * 32]


@pytest.fixture
def runs(registry, selection):
    return workflow.select_runs(registry, "all", None, selection["documentdb_version"])


@pytest.fixture
def records(registry, record, runs):
    results = {}
    for index, run in enumerate(runs):
        name, version = run["integration"], run["version"]
        spec = registry["integrations"][name]
        parts = RUNTIME_PREFIXES[spec["runtime"]].rstrip(".").split(".")
        runtime = {"name": spec["runtime"], "version": ".".join(parts + ["0"] * (3 - len(parts)))}
        result = deepcopy(record)
        result.update(
            id=f"{index + 1:032x}",
            integration=name,
            suite_digest=suite_digest(name, spec),
            expected_tests=expected_tests(spec, False),
            **{key: spec[key] for key in ("repository", "owner", "profile")},
        )
        result["tests"] = [
            {"id": test, "outcome": "passed", "message": ""} for test in result["expected_tests"]
        ]
        result["upstream"].update(
            version=version,
            actual_version=version,
            runtime=runtime,
            dependencies=[{"name": spec["package"], "version": version, "sha256": "b" * 64}],
        )
        results[name] = result
    return results


def write_records(path, records):
    for integration, result in records.items():
        directory = path / integration / result["documentdb"]["version"]
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "result.json").write_text(json.dumps(result))
    return path


def read_report(output):
    report = json.loads((output / "summary.json").read_text())
    history = json.loads((output / "site/history.json").read_text())
    return report, {row["integration"]: row for row in report["integrations"]}, history


def run_cli(monkeypatch, *arguments):
    monkeypatch.setattr(sys, "argv", ["workflow", *arguments])
    return workflow.main()


def test_single_integration_version_override(registry, selection, other_version):
    runs = workflow.select_runs(
        registry, selection["integration"], other_version, selection["documentdb_version"]
    )
    assert runs == [{**selection, "version": other_version, "trigger": "manual"}]


def test_automatic_runs_select_each_enabled_default_and_reviewed_release(registry, selection):
    registry["integrations"][selection["integration"]]["enabled"] = False
    reviewed = registry["documentdb"][selection["documentdb_version"]]
    for release in ("0.9.0", "999.0.0"):
        registry["documentdb"][release] = deepcopy(reviewed)
    releases = sorted(registry["documentdb"], key=Version)
    enabled = sorted(name for name, spec in registry["integrations"].items() if spec["enabled"])
    every = workflow.select_runs(registry, "all", None, "all", trigger="scheduled")
    assert [tuple(run.values()) for run in every] == [
        (name, registry["integrations"][name]["default_version"], release, "scheduled")
        for name in enabled
        for release in releases
    ]
    one = workflow.select_runs(registry, "all", None, "0.9.0", trigger="documentdb_release")
    assert [(run["integration"], run["documentdb_version"]) for run in one] == [
        (name, "0.9.0") for name in enabled
    ]
    newest = workflow.select_runs(registry, enabled[-1], None, None)
    assert newest == [{**every[-1], "trigger": "manual"}]


@pytest.mark.parametrize(
    ("trigger", "integration", "version", "database", "error"),
    [
        ("manual", "unknown", None, None, "Unknown or disabled"),
        ("manual", None, "not-a-version; echo unsafe", None, "outside the policy"),
        ("manual", "all", None, "99.99.99", "Unknown DocumentDB"),
        ("scheduled", "all", None, None, "every reviewed DocumentDB release"),
        ("documentdb_release", "all", None, "all", "one reviewed release"),
        ("scheduled", None, None, "all", "every enabled integration"),
        ("push", "all", None, "all", "Unknown execution trigger"),
    ],
)
def test_invalid_or_narrowed_selection_is_rejected(
    registry, selection, trigger, integration, version, database, error
):
    with pytest.raises(ValueError, match=error):
        workflow.select_runs(
            registry,
            integration or selection["integration"],
            version,
            database or selection["documentdb_version"],
            trigger=trigger,
        )


def test_changed_releases_are_added_or_re_pinned_entries(tmp_path, registry, selection):
    release = selection["documentdb_version"]
    previous = deepcopy(registry)
    registry["documentdb"]["999.0.0"] = deepcopy(registry["documentdb"][release])
    image = previous["documentdb"][release]["image"]
    previous["documentdb"][release]["image"] = image[:-1] + ("1" if image[-1] == "0" else "0")
    previous["integrations"][selection["integration"]]["default_version"] = "0.0.0"
    source = tmp_path / "previous.yaml"
    source.write_text(yaml.safe_dump(previous))
    assert workflow.changed_releases(registry, source) == [release, "999.0.0"]
    for contents in ("{", "documentdb: []\n"):
        source.write_text(contents)
        assert workflow.changed_releases(registry, source) == sorted(
            registry["documentdb"], key=Version
        )


def test_matrix_cli_serializes_the_selected_runs(monkeypatch, capsys, selection, runs):
    assert (
        run_cli(monkeypatch, "matrix", "--documentdb-version", selection["documentdb_version"]) == 0
    )
    assert json.loads(capsys.readouterr().out) == {"include": runs}


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        (["--version", "1.2.3"], "single integration"),
        (["--trigger", "scheduled", *PROVENANCE], "cannot be combined"),
    ],
)
def test_matrix_cli_does_not_emit_a_plan_for_invalid_input(monkeypatch, capsys, arguments, error):
    monkeypatch.setattr(workflow, "validate_dispatch", lambda *_: {})
    assert run_cli(monkeypatch, "matrix", *arguments) == 2
    captured = capsys.readouterr()
    assert not captured.out and error in captured.err


@pytest.mark.parametrize("authorized", [False, True])
def test_upstream_matrix_uses_only_ledger_authorized_artifacts(
    monkeypatch, capsys, selection, authorized
):
    def authorize(registry, runs, identifier, nonce):
        assert runs == [{**selection, "trigger": "upstream_release"}]
        assert identifier == "a" * 64 and nonce == "b" * 32
        if not authorized:
            raise ValueError("Fixture ledger rejected this request")
        return {"artifact_sha256s": ["c" * 64]}

    monkeypatch.setattr(workflow, "validate_dispatch", authorize)
    arguments = [f"--{key.replace('_', '-')}={value}" for key, value in selection.items()]
    assert run_cli(monkeypatch, "matrix", *arguments, *PROVENANCE) == (0 if authorized else 2)
    captured = capsys.readouterr()
    plan = {**selection, "trigger": "upstream_release", "detection_id": "a" * 64}
    if authorized:
        assert json.loads(captured.out)["include"] == [{**plan, "artifact_sha256s": ["c" * 64]}]
    else:
        assert not captured.out and "ledger rejected" in captured.err


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
    assert report["success"] and report["errors"] == []
    assert {record["integration"]: record for record in history} == records
    for integration, row in rows.items():
        assert row["state"] == "Working" and row["run_url"] == RUN_URL
        assert row["passed"] == row["expected"] == len(records[integration]["expected_tests"])
    assert summary.read_text() == "Earlier step\n" + (output / "summary.md").read_text()
    assert "All selected profiles passed." in summary.read_text()
    with pytest.raises(ValueError, match="overwrite"):
        workflow.collect_report(incoming, output, registry, runs)
    assert read_report(output)[0] == report


@pytest.mark.parametrize("bundle_matches", [True, False])
def test_upstream_report_requires_the_planned_npm_bundle(
    tmp_path, registry, selection, records, npm_bundle, bundle_matches
):
    candidate = records["nodejs"]
    major, minor, _ = candidate["upstream"]["version"].split(".")
    version = f"{major}.{int(minor) + 1}.0"
    database = selection["documentdb_version"]
    with pytest.raises(ValueError, match="outside the policy"):
        workflow.select_runs(registry, "nodejs", version, database)
    runs = workflow.select_runs(registry, "nodejs", version, database, trigger="upstream_release")
    bundle = npm_bundle("nodejs", version)
    digest = npm_bundle_digest(bundle)
    candidate.update(trigger="upstream_release", detection_id="a" * 64, run_url=RUN_URL)
    candidate["upstream"].update(
        version=version, actual_version=version, npm_bundle=bundle, npm_bundle_sha256=digest
    )
    candidate["upstream"]["dependencies"][0]["version"] = version
    incoming = write_records(tmp_path / "incoming", {"nodejs": candidate})
    output = tmp_path / "report"
    success = workflow.collect_report(
        incoming,
        output,
        registry,
        runs,
        expected_run_url=RUN_URL,
        detection_id="a" * 64,
        expected_npm_bundle_sha256=digest if bundle_matches else "f" * 64,
    )
    assert success is bundle_matches
    report, rows, history = read_report(output)
    assert report["detection_id"] == "a" * 64
    assert rows["nodejs"]["state"] == ("Working" if bundle_matches else "Not tested")
    assert history == ([candidate] if bundle_matches else [])


@pytest.mark.parametrize(
    ("field", "value", "diagnostic"),
    [
        (("upstream", "version"), "99.1.2", "selected integration"),
        (("owner",), "@example/other", "owner"),
        (("documentdb", "actual_extension_version"), "99.1-2", "installed extension"),
        (
            ("documentdb", "image"),
            "ghcr.io/documentdb/documentdb/documentdb-local@sha256:" + "0" * 64,
            "reviewed database artifact",
        ),
        (("run_url",), RUN_URL[:-1] + "2", "workflow run"),
    ],
)
def test_unmatched_or_unreviewed_evidence_is_rejected_independently(
    tmp_path, registry, selection, runs, records, field, value, diagnostic
):
    integration = selection["integration"]
    for record in records.values():
        record["run_url"] = RUN_URL
    target = records[integration]
    for part in field[:-1]:
        target = target[part]
    target[field[-1]] = value
    incoming = write_records(tmp_path / "incoming", records)
    output = tmp_path / "report"
    assert not workflow.collect_report(incoming, output, registry, runs, expected_run_url=RUN_URL)
    _, rows, history = read_report(output)
    assert rows[integration]["state"] == "Not tested"
    assert diagnostic in rows[integration]["error"]
    assert rows[integration]["passed"] == 0 and rows[integration]["last_attempt"] is None
    assert all(row["state"] == "Working" for name, row in rows.items() if name != integration)
    assert {record["integration"] for record in history} == set(records) - {integration}


@pytest.mark.parametrize("linked", [False, True])
def test_missing_linked_or_unexpected_artifacts_still_produce_a_combined_report(
    tmp_path, registry, selection, runs, records, linked
):
    integration = selection["integration"]
    incoming = write_records(tmp_path / "incoming", records)
    (incoming / "unexpected").mkdir()
    path = incoming / integration / selection["documentdb_version"] / "result.json"
    if linked:
        # A valid record behind a linked integration directory is still rejected.
        target = tmp_path / "linked-integration"
        path.parent.parent.rename(target)
        path.parent.parent.symlink_to(target, target_is_directory=True)
    else:
        path.unlink()
    output = tmp_path / "report"
    assert not workflow.collect_report(incoming, output, registry, runs)
    report, rows, history = read_report(output)
    assert report["errors"] and rows[integration]["state"] == "Not tested"
    assert rows[integration]["error"].startswith("Result unavailable:")
    assert all(row["state"] == "Working" for name, row in rows.items() if name != integration)
    assert {record["integration"] for record in history} == set(records) - {integration}
    assert "Reporting errors" in (output / "summary.md").read_text()


def test_report_cli_fails_without_letting_diagnostics_inject_summary_rows(
    tmp_path, monkeypatch, selection, runs, records
):
    records[selection["integration"]]["tests"][0].update(
        outcome="failed", message="<script>bad</script>\n| Working | [click](https://example.com)\r"
    )
    incoming = write_records(tmp_path / "incoming", records)
    output = tmp_path / "report"
    code = run_cli(
        monkeypatch,
        "report",
        f"--input={incoming}",
        f"--output={output}",
        f"--documentdb-version={selection['documentdb_version']}",
    )
    assert code == 1
    report, rows, _ = read_report(output)
    assert not report["success"] and rows[selection["integration"]]["state"] == "Failing"
    summary = (output / "summary.md").read_text()
    assert "<script>" not in summary and "&lt;script&gt;" in summary
    assert "\\| Working \\|" in summary and "\\[click\\]\\(https://example.com\\)" in summary
    assert len([line for line in summary.splitlines() if line.startswith("|")]) == len(runs) + 2
