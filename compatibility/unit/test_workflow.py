# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

"""Keep dispatched and scheduled runs read-only without turning failures into green jobs."""

import json
import os
import re
import shlex
import subprocess
import sys
from copy import deepcopy
from fnmatch import fnmatchcase

import pytest
import yaml
from packaging.version import Version

from compatibility.contracts import ROOT, read_registry

pytestmark = pytest.mark.unit
WORKFLOW = ROOT / ".github/workflows/compatibility.yml"
DISPATCHER = ROOT / ".github/workflows/compatibility-documentdb-release.yml"
WATCHER = ROOT / ".github/workflows/compatibility-watcher.yml"
REGISTRY = "compatibility/registry.yaml"
RUN_URL = "https://github.com/example/project/actions/runs/42/attempts/3"
INPUTS = ("INTEGRATION", "VERSION", "DOCUMENTDB_VERSION", "TRIGGER", "DETECTION_ID", "DISPATCH_ID")
HOSTILE = "; echo unsafe $(touch unexpected) `touch unexpected`"
# Synthetic values exercise argument transport, not the reviewed release policy.
PRELUDE = """
GITHUB_SERVER_URL=https://github.com GITHUB_REPOSITORY=example/project GITHUB_RUN_ID=42
GITHUB_RUN_ATTEMPT=3 GITHUB_OUTPUT=output GITHUB_STEP_SUMMARY=summary RUNNER_TEMP=.
python() {
  printf '%s\\0' "$@" > arguments
  if [ "$3" = matrix ]; then echo '{"include":[]}'; fi
  return "$EXIT_CODE"
}
"""


@pytest.fixture
def workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def steps(workflow):
    return [(name, step) for name, job in workflow["jobs"].items() for step in job["steps"]]


def step(job, marker):
    return next(step for step in job["steps"] if marker in step.get("uses", step.get("run", "")))


def bash(script, cwd, **environment):
    options = {"capture_output": True, "text": True, "timeout": 30}
    command = ["bash", "-euo", "pipefail", "-c", script]
    return subprocess.run(command, cwd=cwd, env={**os.environ, **environment}, **options)


def run_step(workflow, tmp_path, job, exit_code=0, **inputs):
    names = (*INPUTS, "EXPECTED_ARTIFACTS", "NPM_BUNDLE", "EXPECTED_NPM_BUNDLE_SHA256")
    script = PRELUDE + step(workflow["jobs"][job], "-m compatibility.")["run"]
    environment = {**dict.fromkeys(names, ""), **inputs, "EXIT_CODE": str(exit_code)}
    process = bash(script, tmp_path, **environment)
    assert process.returncode == exit_code, process.stderr
    assert process.stdout == "" and not (tmp_path / "unexpected").exists()
    return (tmp_path / "arguments").read_text().split("\0")[:-1]


@pytest.mark.parametrize("path", [WORKFLOW, DISPATCHER, WATCHER])
def test_actions_are_pinned_without_secrets_or_retained_credentials(path):
    actions = [step for _, step in steps(yaml.safe_load(path.read_text())) if "uses" in step]
    assert all(re.fullmatch(r"actions/[a-z-]+@[a-f0-9]{40}", step["uses"]) for step in actions)
    checkouts = [step["with"] for step in actions if "checkout@" in step["uses"]]
    assert checkouts and all(item["persist-credentials"] is False for item in checkouts)
    assert "secrets." not in path.read_text()


def test_workflows_are_narrowly_triggered_and_least_privileged(workflow):
    # PyYAML's YAML 1.1 loader interprets the Actions "on" key as true.
    assert set(workflow[True]) == {"workflow_dispatch", "schedule"}
    inputs = workflow[True]["workflow_dispatch"]["inputs"]
    assert "default" not in inputs["documentdb_version"]
    assert {"all", *read_registry()["integrations"]} <= set(inputs["integration"]["options"])
    assert workflow["permissions"] == {"contents": "read"}
    assert not any("permissions" in job for job in workflow["jobs"].values())
    tokens = [step.get("id") for _, step in steps(workflow) if "github.token" in str(step)]
    assert tokens == ["select"]
    dispatcher, watcher = (yaml.safe_load(path.read_text()) for path in (DISPATCHER, WATCHER))
    assert dispatcher[True] == {"push": {"branches": ["main"], "paths": [REGISTRY]}}
    assert dispatcher["jobs"]["dispatch"]["permissions"] == {"actions": "write", "contents": "read"}
    assert watcher["permissions"] == {}
    assert {name: job["permissions"] for name, job in watcher["jobs"].items()} == {
        "discover": {"contents": "write"},
        "dispatch": {"contents": "write", "actions": "write"},
    }
    assert "vars.COMPATIBILITY_WATCHER_ENABLED == 'true'" in watcher["jobs"]["discover"]["if"]


@pytest.mark.parametrize(
    ("job", "arguments"),
    [
        ("plan", []),
        ("plan", ["--documentdb-version", "0.999.0", "--trigger", "documentdb_release"]),
        ("report", ["--documentdb-version", "all", "--trigger", "scheduled"]),
        ("plan", ["--integration", HOSTILE, "--version", HOSTILE, "--trigger", HOSTILE]),
        ("plan", ["--detection-id", "a", "--dispatch-id", "b"]),
        ("report", ["--detection-id", "a", "--dispatch-id", "b"]),
    ],
)
def test_plan_and_report_pass_inputs_as_data(workflow, tmp_path, job, arguments):
    flags = (flag[2:].upper().replace("-", "_") for flag in arguments[::2])
    captured = run_step(workflow, tmp_path, job, **dict(zip(flags, arguments[1::2])))
    report = ["--input", "incoming", "--output", "report", "--summary", "summary"]
    command = ["matrix"] if job == "plan" else ["report", *report, "--expected-run-url", RUN_URL]
    assert captured == ["-m", "compatibility.workflow", *command, *arguments]
    if job == "plan":
        assert (tmp_path / "output").read_text() == 'matrix={"include":[]}\n'


def test_failed_plan_fails_the_step_without_a_matrix(workflow, tmp_path):
    run_step(workflow, tmp_path, "plan", exit_code=1)
    assert not (tmp_path / "output").exists()


def test_runner_receives_inputs_and_candidate_bundle_as_data(workflow, tmp_path):
    inputs = {"INTEGRATION": HOSTILE, "VERSION": HOSTILE, "DOCUMENTDB_VERSION": "1", "TRIGGER": "t"}
    manual = [
        *("-m", "compatibility.runner", "--integration", HOSTILE, "--version", HOSTILE),
        *("--documentdb-version", "1", "--output", f"results/{HOSTILE}/1/result.json"),
        *("--run-url", RUN_URL, "--trigger", "t"),
    ]
    assert run_step(workflow, tmp_path, "test", **inputs) == manual
    bundle, candidate = json.dumps({"lockfile": HOSTILE}), "./compatibility-npm-candidate.json"
    inputs.update(DETECTION_ID="a", EXPECTED_ARTIFACTS="b,c", NPM_BUNDLE=bundle)
    assert run_step(workflow, tmp_path, "test", **inputs, EXPECTED_NPM_BUNDLE_SHA256="d") == [
        *(*manual, "--detection-id", "a"),
        *("--expected-artifact-sha256", "b", "--expected-artifact-sha256", "c"),
        *("--npm-bundle", candidate, "--expected-npm-bundle-sha256", "d"),
    ]
    assert (tmp_path / candidate).read_text() == bundle


def test_plan_and_report_resolve_the_same_selection(workflow):
    plan, report = (
        step(workflow["jobs"][job], "-m compatibility.")["env"] for job in ("plan", "report")
    )
    assert {name: plan[name] for name in INPUTS} == {name: report[name] for name in INPUTS}
    assert "github.event_name == 'schedule' && 'all'" in plan["DOCUMENTDB_VERSION"]
    assert "github.event_name == 'schedule' && 'scheduled'" in plan["TRIGGER"]


def test_failures_keep_their_evidence_and_still_fail_the_run(workflow):
    jobs = workflow["jobs"]
    assert jobs["test"]["strategy"]["fail-fast"] is False
    assert jobs["report"]["if"] == "always() && needs.plan.result == 'success'"
    assert step(jobs["report"], "-m compatibility.")["if"] == "always()"
    for job in (jobs["test"], jobs["report"]):
        assert not any(item.get("continue-on-error") for item in [job, *job["steps"]])
        upload = step(job, "upload-artifact@")
        assert (upload["if"], upload["with"]["if-no-files-found"]) == ("always()", "error")


def expand(template, **values):
    return re.sub(r"\$\{\{ \w+\.(\w+) \}\}", lambda match: values[match[1]], template)


def test_report_collects_only_this_attempts_integration_artifacts(workflow):
    jobs = workflow["jobs"]
    test, report = (step(jobs[j], "upload-artifact@")["with"]["name"] for j in ("test", "report"))
    pattern = step(jobs["report"], "download-artifact@")["with"]["pattern"]
    pairs = [("pymongo", "0.999.0"), ("pymongo", "10.1.0"), ("nodejs", "0.999.0")]
    names = {expand(test, integration=i, documentdb_version=v, run_attempt="1") for i, v in pairs}
    assert len(names) == 3 and all(fnmatchcase(n, expand(pattern, run_attempt="1")) for n in names)
    assert not any(fnmatchcase(n, expand(pattern, run_attempt="2")) for n in names)
    assert not fnmatchcase(expand(report, run_attempt="1"), expand(pattern, run_attempt="1"))


@pytest.mark.parametrize("change", ["unavailable", "re-pinned", "integration-only"])
def test_release_dispatcher_starts_runs_for_changed_releases(tmp_path, registry, change):
    release = max(registry["documentdb"], key=Version)
    previous = deepcopy(registry)
    if change == "re-pinned":
        image = previous["documentdb"][release]["image"]
        previous["documentdb"][release]["image"] = image[:-1] + ("1" if image[-1] == "0" else "0")
    elif change == "integration-only":
        previous["integrations"]["pymongo"]["default_version"] = "4.9.0"
    if change != "unavailable":
        (tmp_path / "old.yaml").write_text(yaml.safe_dump(previous))
    stubs = f"""
BEFORE=before GITHUB_REF_NAME=main RUNNER_TEMP={shlex.quote(str(tmp_path))}
git() {{ [ "$*" = "show before:{REGISTRY}" ] && cat "$RUNNER_TEMP/old.yaml"; }}
gh() {{ echo "$*" >> "$RUNNER_TEMP/dispatched"; }}
python() {{ {shlex.quote(sys.executable)} "$@"; }}
"""
    job = yaml.safe_load(DISPATCHER.read_text())["jobs"]["dispatch"]
    process = bash(stubs + step(job, "gh workflow")["run"], ROOT)
    assert process.returncode == 0, process.stderr
    expected = {"unavailable": sorted(registry["documentdb"], key=Version), "re-pinned": [release]}
    dispatched = tmp_path / "dispatched"
    assert (dispatched.read_text().splitlines() if dispatched.exists() else []) == [
        "workflow run compatibility.yml --ref main -f integration=all "
        f"-f documentdb_version={name} -f trigger=documentdb_release"
        for name in expected.get(change, [])
    ]
