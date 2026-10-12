# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

"""Exercise README publication against real disposable Git repositories."""

import json
import os
import shlex
import subprocess
from copy import deepcopy
from datetime import timedelta

import pytest

from compatibility import readme
from compatibility.contracts import ROOT, timestamp
from compatibility.results import append_result

pytestmark = pytest.mark.unit
REPOSITORY = "example/project"
SCRIPT = ROOT / "compatibility/publish-readme.sh"


def git(directory, *arguments, check=True):
    return subprocess.run(
        ["git", "-C", str(directory), *arguments],
        check=check,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.fixture
def dashboard_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    origin = tmp_path / "origin.git"
    source = tmp_path / "checkout"
    git(tmp_path, "init", "--bare", "--quiet", "--initial-branch=main", str(origin))
    git(tmp_path, "clone", "--quiet", str(origin), str(source))
    (source / "source-only").write_text("This must never appear on the data branch.\n")
    git(source, "add", "source-only")
    git(
        source,
        "-c",
        "user.name=Test Fixture",
        "-c",
        "user.email=fixture@example.test",
        "commit",
        "--quiet",
        "-s",
        "-m",
        "Create test source",
    )
    git(source, "push", "--quiet", "origin", "main")
    return source, origin


def invoke(source, plan, report, *, environment=None):
    return subprocess.run(
        ["bash", str(SCRIPT), str(plan), str(ROOT), str(report)],
        cwd=source,
        env=environment or os.environ,
        capture_output=True,
        text=True,
        timeout=90,
    )


def evidence(directory, record, identifier=42):
    directory.mkdir()
    record["run_url"] = readme.run_url(REPOSITORY, identifier, 1)
    profile = readme.profile_from_result(record)
    attempt = {
        "schema_version": 1,
        "repository": REPOSITORY,
        "run_id": identifier,
        "run_attempt": 1,
        "run_url": record["run_url"],
        "head_sha": "a" * 40,
        "started_at": (timestamp(record["started_at"]) - timedelta(seconds=10)).isoformat(),
        "conclusion": "success",
        "profiles": [profile],
        "errors": [],
    }
    plan = directory / "source.json"
    plan.write_text(json.dumps({"action": "publish", "attempt": attempt}))
    report = directory / "report"
    append_result(report / "results", record)
    (report / "summary.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "success": True,
                "errors": [],
                "integrations": [{**profile, "demonstration": False}],
            }
        )
    )
    return plan, report


def test_refresh_initializes_only_status_assets_and_is_idempotent(dashboard_repo, tmp_path):
    source, origin = dashboard_repo
    plan = tmp_path / "refresh.json"
    plan.write_text('{"action":"refresh"}')
    worktrees = git(source, "worktree", "list", "--porcelain").stdout
    original = git(source, "rev-parse", "HEAD").stdout
    result = invoke(source, plan, tmp_path / "missing")
    assert result.returncode == 0, result.stderr
    files = git(origin, "ls-tree", "-r", "--name-only", "compatibility-data").stdout.split()
    assert files == ["site/README.md", "site/status.json", "site/status.svg"]
    view = json.loads(git(origin, "show", "compatibility-data:site/status.json").stdout)
    assert view["latest_workflow"] is None
    assert all(row["state"] == "Not tested" for row in view["integrations"])
    revision = git(origin, "rev-parse", "compatibility-data").stdout
    result = invoke(source, plan, tmp_path / "missing")
    assert result.returncode == 0, result.stderr
    assert git(origin, "rev-parse", "compatibility-data").stdout == revision
    assert git(source, "rev-parse", "HEAD").stdout == original
    assert git(source, "status", "--porcelain").stdout == ""
    assert git(source, "worktree", "list", "--porcelain").stdout == worktrees
    assert not list(tmp_path.glob("compat-dashboard.*"))
    assert not git(source, "branch", "--list", "dashboard-*").stdout.strip()


def test_evidence_and_snapshot_are_published_together_without_rewriting_records(
    dashboard_repo, tmp_path, record
):
    source, origin = dashboard_repo
    plan, report = evidence(tmp_path / "evidence", record)
    result = invoke(source, plan, report)
    assert result.returncode == 0, result.stderr
    saved = json.loads(
        git(origin, "show", f"compatibility-data:results/{record['id']}.json").stdout
    )
    view = json.loads(git(origin, "show", "compatibility-data:site/status.json").stdout)
    assert saved == record and view["latest_workflow"]["run_id"] == 42
    assert (
        next(row for row in view["integrations"] if (row["integration"] == record["integration"]))[
            "state"
        ]
        == "Working"
    )
    revision = git(origin, "rev-parse", "compatibility-data").stdout
    record["tests"][0]["outcome"] = "failed"
    (report / "results" / f"{record['id']}.json").write_text(json.dumps(record))
    result = invoke(source, plan, report)
    assert result.returncode != 0 and "overwrite" in result.stderr
    assert git(origin, "rev-parse", "compatibility-data").stdout == revision
    assert not list(tmp_path.glob("compat-dashboard.*"))


def test_concurrent_newer_failure_survives_a_retry_of_an_older_success(
    dashboard_repo, tmp_path, record
):
    source, origin = dashboard_repo
    plan, report = evidence(tmp_path / "first", record)
    other = deepcopy(record)
    other["id"] = "b" * 32
    other["started_at"] = (timestamp(record["started_at"]) + timedelta(seconds=10)).isoformat()
    other["finished_at"] = (timestamp(record["finished_at"]) + timedelta(seconds=10)).isoformat()
    other["tests"][0]["outcome"] = "failed"
    other_plan, other_report = evidence(tmp_path / "second", other, 43)
    competitor = tmp_path / "competitor"
    git(tmp_path, "clone", "--quiet", str(origin), str(competitor))
    marker = tmp_path / "competing-push"
    injected = tmp_path / "inject.sh"
    injected.write_text(
        "git() {\n"
        f'  if [[ "$1" == push && ! -e {shlex.quote(str(marker))} ]]; then\n'
        f"    touch {shlex.quote(str(marker))}\n"
        f"    (cd {shlex.quote(str(competitor))} && bash {shlex.quote(str(SCRIPT))} "
        f"{shlex.quote(str(other_plan))} {shlex.quote(str(ROOT))} "
        f"{shlex.quote(str(other_report))}) || return\n"
        "  fi\n"
        '  command git "$@"\n'
        "}\n"
    )
    result = invoke(source, plan, report, environment={**os.environ, "BASH_ENV": str(injected)})
    assert result.returncode == 0, result.stderr
    assert "retrying from current history" in result.stderr
    files = git(origin, "ls-tree", "-r", "--name-only", "compatibility-data").stdout.split()
    assert f"results/{record['id']}.json" in files and f"results/{other['id']}.json" in files
    assert "attempts/42-1.json" in files and "attempts/43-1.json" in files
    view = json.loads(git(origin, "show", "compatibility-data:site/status.json").stdout)
    assert view["latest_workflow"]["run_id"] == 43
    assert (
        next(row for row in view["integrations"] if (row["integration"] == record["integration"]))[
            "state"
        ]
        == "Failing"
    )
    assert not list(tmp_path.glob("compat-dashboard.*"))


def test_remote_failure_is_not_treated_as_empty_history(dashboard_repo, tmp_path):
    source, _ = dashboard_repo
    git(source, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    plan = tmp_path / "refresh.json"
    plan.write_text('{"action":"refresh"}')
    result = invoke(source, plan, tmp_path / "missing")
    assert result.returncode != 0 and "Could not look up" in result.stderr
    assert not list(tmp_path.glob("compat-dashboard.*"))
