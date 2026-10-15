# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

"""Keep dispatched and scheduled runs read-only without turning failures into green jobs."""

import json
import os
import re
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


@pytest.fixture
def workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def test_workflow_is_dispatched_or_scheduled_and_read_only(workflow):
    # PyYAML's YAML 1.1 loader interprets the Actions "on" key as true.
    assert set(workflow[True]) == {"workflow_dispatch", "schedule"}
    assert [len(entry["cron"].split()) for entry in workflow[True]["schedule"]] == [5]
    inputs = workflow[True]["workflow_dispatch"]["inputs"]
    assert {
        "integration",
        "version",
        "documentdb_version",
        "demonstration",
        "trigger",
    } <= set(inputs)
    # The newest reviewed release comes from the registry rather than a workflow default.
    assert "default" not in inputs["documentdb_version"]
    assert inputs["documentdb_version"]["required"] is False
    assert inputs["trigger"]["options"] == ["manual", "documentdb_release"]
    assert inputs["trigger"]["default"] == "manual"
    assert workflow["permissions"] == {"contents": "read"}
    assert all(
        job.get("permissions", workflow["permissions"]) == {"contents": "read"}
        for job in workflow["jobs"].values()
    )
    assert "secrets." not in WORKFLOW.read_text()
    token_steps = [
        (name, step)
        for name, job in workflow["jobs"].items()
        for step in job["steps"]
        if "${{ github.token }}" in step.get("env", {}).values()
    ]
    assert [(name, step.get("id")) for name, step in token_steps] == [("plan", "select")]
    assert workflow["name"] == "Ecosystem compatibility"
    selector = workflow[True]["workflow_dispatch"]["inputs"]["integration"]
    assert selector["default"] == "all"
    assert {"all", *read_registry()["integrations"]} <= set(selector["options"])


@pytest.mark.parametrize("path", [WORKFLOW, DISPATCHER])
def test_actions_are_pinned_and_checkout_does_not_retain_credentials(path):
    workflow = yaml.safe_load(path.read_text())
    actions = [step for job in workflow["jobs"].values() for step in job["steps"] if "uses" in step]
    assert all(re.fullmatch(r"actions/[a-z-]+@[a-f0-9]{40}", step["uses"]) for step in actions)
    assert all(
        step["with"]["persist-credentials"] is False
        for step in actions
        if step["uses"].startswith("actions/checkout@")
    )


# These synthetic values exercise argument transport, not the reviewed release policy.
@pytest.mark.parametrize(
    ("version", "database", "demonstration", "trigger", "extra", "exit_code", "watcher"),
    [
        ("", "", "", "", [], 0, False),
        (
            "",
            "0.999.0",
            "false",
            "documentdb_release",
            ["--documentdb-version", "0.999.0", "--trigger", "documentdb_release"],
            0,
            False,
        ),
        (
            "",
            "all",
            "",
            "scheduled",
            ["--documentdb-version", "all", "--trigger", "scheduled"],
            0,
            False,
        ),
        (
            "4.99.1",
            "0.999.0",
            "true",
            "manual",
            [
                "--documentdb-version",
                "0.999.0",
                "--version",
                "4.99.1",
                "--demonstration",
                "--trigger",
                "manual",
            ],
            1,
            False,
        ),
        (
            "4.99.0; echo unsafe",
            "0.999.0",
            "false",
            "manual",
            [
                "--documentdb-version",
                "0.999.0",
                "--version",
                "4.99.0; echo unsafe",
                "--trigger",
                "manual",
            ],
            0,
            False,
        ),
        (
            "",
            "0.999.0; echo unsafe",
            "false",
            "manual; echo unsafe",
            ["--documentdb-version", "0.999.0; echo unsafe", "--trigger", "manual; echo unsafe"],
            0,
            False,
        ),
        (
            "4.99.2",
            "0.999.0",
            "false",
            "manual",
            ["--documentdb-version", "0.999.0", "--version", "4.99.2", "--trigger", "manual"],
            0,
            True,
        ),
    ],
)
@pytest.mark.parametrize(
    "integration", ["", *read_registry()["integrations"], "pymongo; echo unsafe"]
)
@pytest.mark.parametrize("job", ["plan", "test", "report"])
def test_actual_workflow_script_handles_defaults_and_dispatch_inputs(
    workflow,
    tmp_path,
    version,
    database,
    demonstration,
    trigger,
    extra,
    exit_code,
    watcher,
    integration,
    job,
):
    step = next(
        step
        for step in workflow["jobs"][job]["steps"]
        if "compatibility.runner" in step.get("run", "")
        or "compatibility.workflow" in step.get("run", "")
    )
    capture = (
        'python() { printf "%s\\0" "$@" > "$CAPTURED_ARGUMENTS"; '
        """if [ "$3" = "matrix" ]; then printf '%s\\n' '{"include":[]}'; fi; """
        'return "$TEST_EXIT_CODE"; }\n'
    )
    if watcher and job == "test":
        # The planned matrix, not the dispatch input, carries the watcher trigger.
        trigger = "upstream_release"
    captured = tmp_path / "arguments"
    output = tmp_path / "job-output"
    summary = tmp_path / "summary"
    process = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", capture + step["run"]],
        cwd=tmp_path,
        env={
            **os.environ,
            "VERSION": version,
            "INTEGRATION": integration,
            "DOCUMENTDB_VERSION": database,
            "DEMONSTRATION": demonstration,
            "TRIGGER": trigger,
            "DETECTION_ID": "a" * 64 if watcher else "",
            "DISPATCH_ID": "b" * 32 if watcher else "",
            "EXPECTED_ARTIFACTS": ",".join(["c" * 64, "d" * 64]) if watcher else "",
            "NPM_BUNDLE": "",
            "EXPECTED_NPM_BUNDLE_SHA256": "",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "example/project",
            "GITHUB_RUN_ID": "42",
            "GITHUB_RUN_ATTEMPT": "3",
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
            "CAPTURED_ARGUMENTS": str(captured),
            "TEST_EXIT_CODE": str(exit_code),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert process.returncode == exit_code, process.stderr
    assert process.stdout == ""
    run_url = "https://github.com/example/project/actions/runs/42/attempts/3"
    if job == "test":
        expected = [
            "-m",
            "compatibility.runner",
            "--integration",
            integration,
            "--version",
            version,
            "--documentdb-version",
            database,
            "--output",
            f"results/{integration}/{database}/result.json",
            "--run-url",
            run_url,
            "--trigger",
            trigger,
            *(["--demonstration"] if demonstration == "true" else []),
        ]
    else:
        expected = ["-m", "compatibility.workflow", "matrix" if job == "plan" else "report"]
        if job == "report":
            expected += [
                "--input",
                "incoming",
                "--output",
                "report",
                "--summary",
                str(summary),
                "--expected-run-url",
                run_url,
            ]
        expected += [*(["--integration", integration] if integration else []), *extra]
    if watcher:
        if job == "test":
            expected += [
                "--detection-id",
                "a" * 64,
                "--expected-artifact-sha256",
                "c" * 64,
                "--expected-artifact-sha256",
                "d" * 64,
            ]
        else:
            expected += ["--detection-id", "a" * 64, "--dispatch-id", "b" * 32]
    assert captured.read_bytes().decode().split("\0")[:-1] == expected
    if job == "plan":
        if exit_code:
            assert not output.exists()
        else:
            assert output.read_text() == 'matrix={"include":[]}\n'


def test_failed_runs_keep_their_evidence_and_fail_the_job(workflow):
    job = workflow["jobs"]["test"]
    steps = job["steps"]
    suite = next(step for step in steps if step.get("id") == "suite")
    upload = next(
        step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert job.get("continue-on-error", False) is False
    assert suite.get("continue-on-error", False) is False
    assert job["strategy"]["fail-fast"] is False
    assert job["strategy"]["matrix"] == "${{ fromJSON(needs.plan.outputs.matrix) }}"
    assert job["needs"] == "plan"
    assert workflow["jobs"]["plan"]["outputs"]["matrix"] == "${{ steps.select.outputs.matrix }}"
    assert upload["if"] == "always()"
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["path"] == "results/"
    for variable in ("integration", "version", "documentdb_version", "demonstration", "trigger"):
        assert suite["env"][variable.upper()] == "${{ matrix." + variable + " }}"


def test_candidate_bundle_is_passed_as_data_without_shell_interpretation(workflow, tmp_path):
    suite = next(step for step in workflow["jobs"]["test"]["steps"] if step.get("id") == "suite")
    bundle = json.dumps({"lockfile": {"description": "$(touch unexpected) `touch unexpected`"}})
    captured = tmp_path / "arguments"
    capture = 'python() { printf "%s\\0" "$@" > "$CAPTURED_ARGUMENTS"; }\n'
    environment = {
        **os.environ,
        "INTEGRATION": "nodejs",
        "VERSION": "7.99.0",
        "DOCUMENTDB_VERSION": "0.117.0",
        "DEMONSTRATION": "false",
        "TRIGGER": "upstream_release",
        "DETECTION_ID": "a" * 64,
        "EXPECTED_ARTIFACTS": "b" * 64,
        "NPM_BUNDLE": bundle,
        "EXPECTED_NPM_BUNDLE_SHA256": "c" * 64,
        "RUNNER_TEMP": str(tmp_path),
        "CAPTURED_ARGUMENTS": str(captured),
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": "example/project",
        "GITHUB_RUN_ID": "42",
        "GITHUB_RUN_ATTEMPT": "1",
    }
    process = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", capture + suite["run"]],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert process.returncode == 0, process.stderr
    retained = tmp_path / "compatibility-npm-candidate.json"
    assert retained.read_text() == bundle and not (tmp_path / "unexpected").exists()
    arguments = captured.read_bytes().decode().split("\0")[:-1]
    assert arguments[-4:] == [
        "--npm-bundle",
        str(retained),
        "--expected-npm-bundle-sha256",
        "c" * 64,
    ]


def test_plan_and_report_resolve_the_same_selection(workflow):
    plan, report = (
        next(
            step
            for step in workflow["jobs"][job]["steps"]
            if "compatibility.workflow" in step.get("run", "")
        )
        for job in ("plan", "report")
    )
    selection = (
        "INTEGRATION",
        "VERSION",
        "DOCUMENTDB_VERSION",
        "DEMONSTRATION",
        "TRIGGER",
        "DETECTION_ID",
        "DISPATCH_ID",
    )
    assert {name: plan["env"][name] for name in selection} == {
        name: report["env"][name] for name in selection
    }
    assert "github.event_name == 'schedule' && 'all'" in plan["env"]["DOCUMENTDB_VERSION"]
    assert "github.event_name == 'schedule' && 'scheduled'" in plan["env"]["TRIGGER"]


def test_combined_reporting_runs_after_test_and_download_failures(workflow):
    job = workflow["jobs"]["report"]
    assert set(job["needs"]) == {"plan", "test"}
    assert job["if"] == "always() && needs.plan.result == 'success'"
    download = next(
        step
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/download-artifact@")
    )
    report = next(
        step for step in job["steps"] if "compatibility.workflow report" in step.get("run", "")
    )
    upload = next(
        step for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert report["if"] == upload["if"] == "always()"
    assert job["steps"].index(download) < job["steps"].index(report) < job["steps"].index(upload)
    assert all(step.get("continue-on-error", False) is False for step in job["steps"])
    assert job.get("continue-on-error", False) is False
    assert download["with"]["merge-multiple"] is True
    assert download["with"]["path"] == "incoming"
    assert upload["with"]["path"] == "report/"
    assert upload["with"]["if-no-files-found"] == "error"


def test_artifacts_are_distinct_per_integration_release_and_attempt(workflow, registry):
    uploads = {
        name: next(
            step["with"]
            for step in workflow["jobs"][name]["steps"]
            if step.get("uses", "").startswith("actions/upload-artifact@")
        )
        for name in ("test", "report")
    }
    pattern = next(
        step["with"]["pattern"]
        for step in workflow["jobs"]["report"]["steps"]
        if step.get("uses", "").startswith("actions/download-artifact@")
    )
    names = set()
    for attempt in (1, 2):
        current_pattern = pattern.replace("${{ github.run_attempt }}", str(attempt))
        for integration in registry["integrations"]:
            for release in [*registry["documentdb"], "0.999.0", "10.1.0"]:
                name = (
                    uploads["test"]["name"]
                    .replace("${{ matrix.integration }}", integration)
                    .replace("${{ matrix.documentdb_version }}", release)
                    .replace("${{ github.run_attempt }}", str(attempt))
                )
                assert name not in names
                assert fnmatchcase(name, current_pattern)
                assert not fnmatchcase(
                    name, pattern.replace("${{ github.run_attempt }}", str(attempt + 1))
                )
                names.add(name)
        combined = uploads["report"]["name"].replace("${{ github.run_attempt }}", str(attempt))
        assert combined not in names and not fnmatchcase(combined, current_pattern)
        names.add(combined)
    assert all(settings["retention-days"] == 30 for settings in uploads.values())


def test_release_dispatcher_runs_only_for_registry_changes_on_main():
    dispatcher = yaml.safe_load(DISPATCHER.read_text())
    assert dispatcher[True] == {
        "push": {"branches": ["main"], "paths": ["compatibility/registry.yaml"]}
    }
    assert dispatcher["permissions"] == {"contents": "read"}
    assert [job["permissions"] for job in dispatcher["jobs"].values()] == [
        {"actions": "write", "contents": "read"}
    ]
    assert "secrets." not in DISPATCHER.read_text()


@pytest.mark.parametrize(
    "change", ["unavailable", "unchanged", "added", "re-pinned", "integration-only"]
)
def test_release_dispatcher_starts_runs_for_added_or_re_pinned_releases(tmp_path, registry, change):
    (job,) = yaml.safe_load(DISPATCHER.read_text())["jobs"].values()
    step = next(step for step in job["steps"] if "gh workflow run" in step.get("run", ""))
    release = max(registry["documentdb"], key=Version)
    previous = deepcopy(registry)
    if change == "added":
        del previous["documentdb"][release]
    elif change == "re-pinned":
        image = previous["documentdb"][release]["image"]
        previous["documentdb"][release]["image"] = image[:-1] + ("1" if image[-1] == "0" else "0")
    elif change == "integration-only":
        previous["integrations"]["pymongo"]["default_version"] = "4.9.0"
    source = tmp_path / "source-registry.yaml"
    source.write_text(yaml.safe_dump(previous))
    stubs = (
        'git() { [ "$*" = "show $BEFORE:compatibility/registry.yaml" ] '
        '&& [ "$CHANGE" != unavailable ] && cat "$SOURCE"; }\n'
        'gh() { printf "%s\\n" "$*" >> "$DISPATCHED"; }\n'
        'python() { "$PYTHON" "$@"; }\n'
    )
    dispatched = tmp_path / "dispatched"
    process = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", stubs + step["run"]],
        cwd=ROOT,
        env={
            **os.environ,
            "BEFORE": "a" * 40,
            "CHANGE": change,
            "SOURCE": str(source),
            "DISPATCHED": str(dispatched),
            "PYTHON": sys.executable,
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_REF_NAME": "main",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    if change == "unavailable":
        expected = sorted(registry["documentdb"], key=Version)
    else:
        expected = [] if change in ("unchanged", "integration-only") else [release]
    assert (dispatched.read_text().splitlines() if dispatched.exists() else []) == [
        "workflow run compatibility.yml --ref main -f integration=all "
        f"-f documentdb_version={name} -f trigger=documentdb_release"
        for name in expected
    ]


def test_infrastructure_is_checked_without_starting_an_integration():
    workflow = yaml.safe_load((ROOT / ".github/workflows/compatibility-ci.yml").read_text())
    job = workflow["jobs"]["compatibility-unit-tests"]
    assert job["name"] == "Compatibility infrastructure"
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "compatibility/requirements-dev.txt" in commands
    assert "compatibility/unit" in commands
    assert "test_freshness.cjs" in commands
    assert "test_nodejs.cjs" in commands
    assert "compatibility.runner" not in commands


def test_compiled_watcher_limits_writes_and_keeps_dispatch_independent_of_ai_success():
    compiled = yaml.safe_load(
        (ROOT / ".github/workflows/compatibility-watcher.lock.yml").read_text()
    )
    assert set(compiled[True]) == {"schedule", "workflow_dispatch"}
    jobs = compiled["jobs"]
    assert compiled["permissions"] == {}
    assert all("issues" not in job.get("permissions", {}) for job in jobs.values())
    assert {
        name for name, job in jobs.items() if job.get("permissions", {}).get("contents") == "write"
    } == {"discover", "assess_releases", "dispatch"}
    assert {
        name for name, job in jobs.items() if job.get("permissions", {}).get("actions") == "write"
    } == {"dispatch"}
    assert jobs["agent"]["permissions"]["contents"] == "read"
    guard = jobs["activation"]["if"]
    assert "COMPATIBILITY_WATCHER_ENABLED == 'true'" in guard
    assert "COMPATIBILITY_WATCHER_MODEL != ''" in guard
    assert "github.event.repository.default_branch" in guard
    dispatch = jobs["dispatch"]
    assert set(dispatch["needs"]) == {"discover", "agent", "assess_releases"}
    assert "!cancelled()" in dispatch["if"]
    assert "needs.discover.result == 'success'" in dispatch["if"]
    assert "needs.agent.result" not in dispatch["if"]
