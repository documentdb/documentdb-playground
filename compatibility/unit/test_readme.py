# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

"""README views preserve execution identity and expose missing evidence."""

import json
import shutil
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

import pytest

from compatibility import readme
from compatibility.contracts import ROOT, read_registry, suite_digest, timestamp
from compatibility.results import append_result, validate_incoming_result

pytestmark = pytest.mark.unit
REPOSITORY = "example/project"


@pytest.fixture
def published_record(record):
    record["run_url"] = readme.run_url(REPOSITORY, 42, 1)
    return record


@pytest.fixture
def attempt(published_record):
    return {
        "schema_version": 1,
        "repository": REPOSITORY,
        "run_id": 42,
        "run_attempt": 1,
        "run_url": published_record["run_url"],
        "head_sha": "a" * 40,
        "started_at": (
            timestamp(published_record["started_at"]) - timedelta(seconds=10)
        ).isoformat(),
        "conclusion": "success",
        "profiles": [readme.profile_from_result(published_record)],
        "errors": [],
    }


@pytest.fixture
def source_run(attempt):
    return {
        "id": attempt["run_id"],
        "run_attempt": attempt["run_attempt"],
        "head_sha": attempt["head_sha"],
        "run_started_at": attempt["started_at"],
        "conclusion": attempt["conclusion"],
        "head_branch": "main",
        "repository": {"full_name": REPOSITORY},
        "head_repository": {"full_name": REPOSITORY},
        "event": "workflow_dispatch",
        "status": "completed",
        "workflow_id": 123,
        "display_title": "Ecosystem compatibility",
    }


def incoming_report(path, record, *, include_result=True):
    path.mkdir()
    profile = {**readme.profile_from_result(record), "demonstration": record["demonstration"]}
    (path / "summary.json").write_text(
        json.dumps({"schema_version": 1, "success": True, "integrations": [profile], "errors": []})
    )
    if include_result:
        append_result(path / "results", record)
    return path


def selected_row(view, record):
    return next(
        row
        for row in view["integrations"]
        if (
            row["integration"] == record["integration"]
            and row["upstream_version"] == record["upstream"]["version"]
            and row["profile"] == record["profile"]
        )
    )


def test_empty_dashboard_is_not_tested_and_has_no_execution_timestamp(tmp_path, registry):
    view = readme.snapshot(tmp_path, registry, REPOSITORY, datetime.now(timezone.utc))
    assert view["latest_workflow"] is None and view["preview"]
    assert all(
        row["state"] == "Not tested" and row["last_attempt"] is None for row in view["integrations"]
    )
    assert "No completed compatibility workflow" in readme.markdown(view)
    assert "Not tested" in "".join(ElementTree.fromstring(readme.svg(view)).itertext())


def test_real_profile_evidence_drives_svg_json_and_accessible_details(
    tmp_path, registry, published_record, attempt
):
    incoming = incoming_report(tmp_path / "incoming", published_record)
    data = tmp_path / "data"
    readme.ingest({"action": "publish", "attempt": attempt}, ROOT, incoming, data, REPOSITORY)
    readme.render(data, data / "site", REPOSITORY)
    view = json.loads((data / "site/status.json").read_text())
    row = selected_row(view, published_record)
    assert (row["state"], row["latest_attempt_state"]) == ("Working", "Working")
    assert row["last_attempt"] == published_record["finished_at"]
    assert row["conclusive_at"] == published_record["finished_at"]
    assert row["passed"] == row["expected"] == len(published_record["expected_tests"])
    text = "".join(ElementTree.fromstring((data / "site/status.svg").read_text()).itertext())
    assert readme.utc(published_record["finished_at"]) in text
    assert "FORK PREVIEW" not in text
    assert "Scoped scenario evidence" not in text
    details = (data / "site/README.md").read_text()
    assert published_record["run_url"] in details and "Working" in details
    assert "Fork preview" not in details
    assert "Results apply only" not in details


def test_missing_latest_artifact_does_not_inherit_an_earlier_pass(
    tmp_path, registry, published_record, attempt
):
    append_result(tmp_path / "results", published_record)
    later = deepcopy(attempt)
    later.update(
        run_id=43,
        run_url=readme.run_url(REPOSITORY, 43, 1),
        started_at=published_record["finished_at"],
        conclusion="failure",
    )
    missing = deepcopy(published_record)
    missing["run_url"] = later["run_url"]
    incoming = incoming_report(tmp_path / "incoming", missing, include_result=False)
    readme.ingest({"action": "publish", "attempt": later}, ROOT, incoming, tmp_path, REPOSITORY)
    view = readme.snapshot(tmp_path, registry, REPOSITORY, datetime.now(timezone.utc))
    row = selected_row(view, published_record)
    assert row["state"] == "Working"
    assert row["latest_attempt_state"] == "Not tested"
    assert row["last_attempt"] == later["started_at"]
    assert row["conclusive_at"] == published_record["finished_at"]
    assert row["run_url"] == later["run_url"] and view["latest_workflow"]["errors"]
    assert view["latest_workflow"]["conclusion"] == "failure"


def test_failure_before_a_report_exists_is_visible(tmp_path, registry, attempt):
    attempt["conclusion"] = "failure"
    attempt["profiles"] = []
    readme.ingest(
        {"action": "publish", "attempt": attempt},
        ROOT,
        tmp_path / "missing",
        tmp_path / "data",
        REPOSITORY,
    )
    view = readme.snapshot(tmp_path / "data", registry, REPOSITORY, datetime.now(timezone.utc))
    assert view["latest_workflow"]["errors"]
    assert all(row["state"] == "Not tested" for row in view["integrations"])
    assert "missing or rejected evidence" in readme.svg(view)


def test_republication_can_recover_missing_artifacts_without_rewriting_run_identity(
    tmp_path, registry, published_record, attempt
):
    incoming = incoming_report(tmp_path / "incoming", published_record, include_result=False)
    data = tmp_path / "data"
    plan = {"action": "publish", "attempt": attempt}
    readme.ingest(plan, ROOT, incoming, data, REPOSITORY)
    before = readme.snapshot(data, registry, REPOSITORY, datetime.now(timezone.utc))
    append_result(incoming / "results", published_record)
    readme.ingest(plan, ROOT, incoming, data, REPOSITORY)
    after = readme.snapshot(data, registry, REPOSITORY, datetime.now(timezone.utc))
    assert selected_row(before, published_record)["latest_attempt_state"] == "Not tested"
    assert selected_row(after, published_record)["state"] == "Working"
    assert before["latest_workflow"]["started_at"] == after["latest_workflow"]["started_at"]
    assert not after["latest_workflow"]["errors"]
    changed = {**attempt, "head_sha": "b" * 40}
    with pytest.raises(ValueError, match="identity"):
        readme.save_attempt(data, changed, REPOSITORY)


def test_refresh_ages_evidence_without_inventing_a_new_execution(
    tmp_path, registry, published_record, attempt
):
    append_result(tmp_path / "results", published_record)
    readme.save_attempt(tmp_path, attempt, REPOSITORY)
    original = (tmp_path / f"results/{published_record['id']}.json").read_bytes()
    first = readme.snapshot(tmp_path, registry, REPOSITORY, datetime.now(timezone.utc))
    days = registry["integrations"][published_record["integration"]]["freshness_days"]
    later = readme.snapshot(
        tmp_path,
        registry,
        REPOSITORY,
        timestamp(published_record["finished_at"]) + timedelta(days=days, seconds=1),
    )
    assert selected_row(first, published_record)["state"] == "Working"
    assert selected_row(later, published_record)["state"] == "Stale"
    assert first["latest_workflow"] == later["latest_workflow"]
    assert selected_row(first, published_record)["last_attempt"] == (
        selected_row(later, published_record)["last_attempt"]
    )
    assert (tmp_path / f"results/{published_record['id']}.json").read_bytes() == original


def test_demonstrations_do_not_change_normal_readme_state(
    tmp_path, registry, published_record, attempt
):
    append_result(tmp_path / "results", published_record)
    readme.save_attempt(tmp_path, attempt, REPOSITORY)
    original = readme.snapshot(tmp_path, registry, REPOSITORY, datetime.now(timezone.utc))
    demo = deepcopy(published_record)
    demo["id"] = "b" * 32
    demo["demonstration"] = True
    demo["expected_tests"].append("test_failure_demonstration")
    demo["tests"].append(
        {"id": "test_failure_demonstration", "outcome": "failed", "message": "Deliberate failure"}
    )
    incoming = incoming_report(tmp_path / "incoming", demo)
    readme.ingest({"action": "publish", "attempt": attempt}, ROOT, incoming, tmp_path, REPOSITORY)
    assert readme.snapshot(tmp_path, registry, REPOSITORY, datetime.now(timezone.utc)) == original
    assert not (tmp_path / f"results/{demo['id']}.json").exists()


def test_svg_and_markdown_keep_metadata_as_text(tmp_path, registry):
    view = readme.snapshot(tmp_path, registry, REPOSITORY, datetime.now(timezone.utc))
    view["integrations"][0]["profile"] = '<script>alert("test")</script>'
    view["integrations"][0]["integration"] = "[unsafe](javascript:alert(1))|bad"
    svg = ElementTree.fromstring(readme.svg(view))
    assert not list(svg.iter("{http://www.w3.org/2000/svg}script"))
    assert "<script>" in "".join(svg.itertext())
    assert "[unsafe](javascript" not in readme.markdown(view)


@pytest.mark.parametrize(
    "invalid", ["branch", "repository", "head_repository", "event", "status", "workflow", "future"]
)
def test_untrusted_source_runs_are_rejected(source_run, invalid):
    workflow = {"path": readme.WORKFLOW_PATH, "id": source_run["workflow_id"]}
    if invalid == "branch":
        source_run["head_branch"] = "unreviewed"
    elif invalid in ("repository", "head_repository"):
        source_run[invalid]["full_name"] = "another/project"
    elif invalid == "event":
        source_run["event"] = "pull_request"
    elif invalid == "status":
        source_run["status"] = "in_progress"
    elif invalid == "workflow":
        workflow["path"] = ".github/workflows/unrelated.yml"
    else:
        source_run["run_started_at"] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError):
        readme.source_attempt(source_run, workflow, REPOSITORY, "main")


def test_prepare_keeps_failed_runs_and_ignores_demonstrations(source_run, monkeypatch):
    workflow = {"path": readme.WORKFLOW_PATH, "id": source_run["workflow_id"]}
    monkeypatch.setattr(
        readme, "github_object", lambda path: source_run if "/runs/" in path else workflow
    )
    source_run["conclusion"] = "failure"
    plan = readme.prepare(REPOSITORY, "main", "workflow_dispatch", {}, "42")
    assert plan["action"] == "publish" and plan["attempt"]["conclusion"] == "failure"
    source_run["display_title"] = readme.DEMONSTRATION_TITLE
    assert readme.prepare(REPOSITORY, "main", "workflow_dispatch", {}, "42")["action"] == "ignore"


def test_callback_uses_its_exact_attempt_instead_of_a_newer_rerun(source_run, monkeypatch):
    queried = []

    def request(path):
        queried.append(path)
        return (
            source_run
            if "/runs/" in path
            else {"path": readme.WORKFLOW_PATH, "id": source_run["workflow_id"]}
        )

    monkeypatch.setattr(readme, "github_object", request)
    event = {"workflow_run": {"id": source_run["id"], "run_attempt": 1}}
    readme.prepare(REPOSITORY, "main", "workflow_run", event, "")
    assert queried[0].endswith("/runs/42/attempts/1")
    source_run["run_attempt"] = 2
    with pytest.raises(ValueError, match="different workflow attempt"):
        readme.prepare(REPOSITORY, "main", "workflow_run", event, "")


def test_input_digest_validation_reads_the_source_revision(tmp_path, registry, published_record):
    source = tmp_path / "source"
    shutil.copytree(
        ROOT / "compatibility",
        source / "compatibility",
        ignore=shutil.ignore_patterns(
            "__pycache__", ".pytest_cache", ".mypy_cache", ".test-results"
        ),
    )
    adapter = source / registry["integrations"][published_record["integration"]]["test_file"]
    with adapter.open("a") as stream:
        stream.write("\n# changed source revision\n")
    source_registry = read_registry(source / "compatibility/registry.yaml", root=source)
    spec = source_registry["integrations"][published_record["integration"]]
    published_record["suite_digest"] = suite_digest(published_record["integration"], spec, source)
    validate_incoming_result(
        published_record, source_registry, published_record["run_url"], root=source
    )
    with pytest.raises(ValueError, match="different suite revision"):
        validate_incoming_result(published_record, registry, published_record["run_url"])


def test_output_symlinks_and_foreign_evidence_are_rejected(tmp_path, registry, published_record):
    output = tmp_path / "output"
    output.mkdir()
    outside = tmp_path / "outside.svg"
    outside.write_text("unchanged")
    (output / "status.svg").symlink_to(outside)
    with pytest.raises(ValueError, match="symbolic links"):
        readme.render(tmp_path / "data", output, REPOSITORY)
    assert outside.read_text() == "unchanged"
    published_record["run_url"] = "https://github.com/another/project/actions/runs/42/attempts/1"
    append_result(tmp_path / "data/results", published_record)
    with pytest.raises(ValueError, match="originate in this repository"):
        readme.snapshot(tmp_path / "data", registry, REPOSITORY, datetime.now(timezone.utc))
