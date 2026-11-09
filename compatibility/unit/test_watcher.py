# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

"""Exercise release discovery and interrupted dispatches without GitHub writes."""

import hashlib
import json
import subprocess
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from compatibility import npm, watcher
from compatibility.contracts import npm_bundle_digest
from compatibility.watcher_state import GitHub, GitLedger, RequestError

pytestmark = pytest.mark.unit
PYTHON_UPSTREAM = "mongodb/mongo-python-driver"
PROFILE = ("integration", "version", "documentdb_version")
RATE_LIMITED = RequestError("Rate limited", 429, True)


def metadata(version, yanked=False):
    wheel = f"pymongo-{version}-cp312-cp312-manylinux_2_17_x86_64.whl"
    digests = {"sha256": hashlib.sha256(version.encode()).hexdigest()}
    asset = {"filename": wheel, "packagetype": "bdist_wheel", "yanked": yanked, "digests": digests}
    info = {"name": "pymongo", "version": version, "requires_python": ">=3.9"}
    return {"info": info, "urls": [asset]}


def release(version, identifier=100, repository=PYTHON_UPSTREAM):
    url = f"https://github.com/{repository}/releases/tag/{version}"
    item = {"id": identifier, "tag_name": version, "draft": False, "prerelease": False}
    return {**item, "html_url": url, "published_at": "2026-10-01T00:00:00Z"}


def workflow_run(run_id, identifier, nonce, conclusion="success"):
    url = f"https://github.com/example/project/actions/runs/{run_id}"
    run = {"id": run_id, "head_branch": "main", "event": "workflow_dispatch", "html_url": url}
    title = watcher.dispatch_title(identifier, nonce)
    run.update(display_title=title, status="completed", conclusion=conclusion)
    return {**run, "path": ".github/workflows/compatibility.yml"}


class FakeAPI(GitHub):
    def __init__(self, releases):
        super().__init__("example/project", "unit-token")
        self.releases = releases
        self.dispatches = []
        self.runs = []
        self.failure = None
        self.persisted = None

    def request(self, method, path, payload=None):
        repository, _, rest = path.removeprefix("/repos/").partition("/releases")
        releases = self.releases.get(repository, [])
        if rest == "?per_page=30":
            return deepcopy(releases)
        return deepcopy(next(item for item in releases if f"/{item['id']}" == rest))

    def local(self, method, path, payload=None):
        if method == "POST":
            assert path == "/actions/workflows/compatibility.yml/dispatches"
            identifier, nonce = payload["inputs"]["detection_id"], payload["inputs"]["dispatch_id"]
            reserved = self.persisted["entries"][identifier]
            assert reserved["status"] == "dispatching"
            assert reserved["attempts"][-1]["id"] == nonce
            self.dispatches.append(deepcopy(payload))
            if self.failure:
                raise self.failure
            run = workflow_run(500 + len(self.runs), identifier, nonce)
            self.runs.append(run)
            api = f"https://api.github.com/repos/{self.repository}/actions/runs/{run['id']}"
            return {"workflow_run_id": run["id"], "html_url": run["html_url"], "run_url": api}
        if path.startswith("/actions/runs/"):
            return deepcopy(next(run for run in self.runs if f"/{run['id']}" == path[13:]))
        page = int(parse_qs(urlsplit(path).query)["page"][0])
        runs = deepcopy(self.runs[(page - 1) * 100 : page * 100])
        return {"total_count": len(self.runs), "workflow_runs": runs}


class MemoryLedger(GitLedger):
    def load(self):
        self.state = deepcopy(self.api.persisted)

    def save(self):
        self.validate()
        self.api.persisted = deepcopy(self.state)


@pytest.fixture
def context(monkeypatch):
    monkeypatch.setattr(watcher, "now", lambda: datetime(2026, 10, 7, tzinfo=timezone.utc))
    run_url = "https://github.com/example/project/actions/runs/42/attempts/1"
    return watcher.Context("example/project", "main", "a" * 40, run_url)


@pytest.fixture
def ledger(registry, monkeypatch):
    version = registry["integrations"]["pymongo"]["default_version"]
    monkeypatch.setattr(watcher, "package_metadata", lambda spec, selected: metadata(selected))
    return MemoryLedger(FakeAPI({PYTHON_UPSTREAM: [release(version)]}))


def detect(ledger, registry, context):
    added = watcher.discover(ledger, registry, context)
    assert len(added) == 1
    return added[0], ledger.state["entries"][added[0]]


def rerun(context, attempt):
    return replace(context, run_url=context.run_url.removesuffix("/1") + f"/{attempt}")


def serve(monkeypatch, ledger, context):
    monkeypatch.setattr(watcher.Context, "environment", lambda: context)
    monkeypatch.setattr(watcher, "GitHub", lambda *args: ledger.api)
    monkeypatch.setattr(watcher, "GitLedger", lambda *args: ledger)
    monkeypatch.setenv("GITHUB_TOKEN", "unit-token")


@pytest.mark.parametrize(("mutation", "decision"), [("yanked", "no eligible"), ("404", "awaiting")])
def test_ineligible_packages_are_audited_and_reconsidered(
    registry, ledger, context, monkeypatch, mutation, decision
):
    def package(spec, version):
        if mutation == "404":
            raise RequestError("Not published yet", 404)
        return metadata(version, yanked=True)

    monkeypatch.setattr(watcher, "package_metadata", package)
    assert watcher.discover(ledger, registry, context) == []
    assert decision in ledger.api.persisted["last_scan"]["observations"][0]["decision"]
    assert watcher.dispatch(ledger, registry, context)
    assert not ledger.api.dispatches
    monkeypatch.setattr(watcher, "package_metadata", lambda spec, version: metadata(version))
    assert len(watcher.discover(ledger, registry, context)) == 1


@pytest.mark.parametrize(("field", "value"), [("prerelease", True), ("html_url", "https://x.test")])
def test_release_policy_precedes_package_access(
    ledger, registry, context, monkeypatch, field, value
):
    ledger.api.releases[PYTHON_UPSTREAM][0][field] = value
    monkeypatch.setattr(watcher, "package_metadata", lambda *args: pytest.fail("Fetched package"))
    if field == "prerelease":
        assert watcher.discover(ledger, registry, context) == []
    else:
        with pytest.raises(ValueError, match="official source"):
            watcher.discover(ledger, registry, context)
        assert ledger.api.persisted["last_scan"]["error"]


def test_discovery_is_durable_idempotent_and_scoped_to_execution_identity(
    ledger, registry, context
):
    identifier, entry = detect(ledger, registry, context)
    assert entry == ledger.api.persisted["entries"][identifier]
    watcher.discover(ledger, registry, context)
    assert list(ledger.state["entries"]) == [identifier]
    registry["integrations"]["pymongo"]["timeout_seconds"] += 1
    watcher.discover(ledger, registry, context)
    assert len(ledger.state["entries"]) == 2
    assert ledger.state["entries"][identifier] == entry
    assert not ledger.api.dispatches


def test_eligible_release_dispatches_once_and_validates_only_that_attempt(
    ledger, registry, context, monkeypatch
):
    identifier, entry = detect(ledger, registry, context)
    assert watcher.dispatch(ledger, registry, context)
    saved = ledger.api.persisted["entries"][identifier]
    assert saved["status"] == "dispatched"
    assert ledger.api.dispatches[0]["inputs"] == {
        "integration": "pymongo",
        "version": saved["version"],
        "documentdb_version": saved["documentdb_version"],
        "detection_id": identifier,
        "dispatch_id": saved["attempts"][0]["id"],
    }
    assert watcher.dispatch(ledger, registry, context)
    assert len(ledger.api.dispatches) == 1
    assert ledger.api.persisted["entries"][identifier]["status"] == "completed"
    serve(monkeypatch, ledger, context)
    run = {key: entry[key] for key in PROFILE}
    assert watcher.validate_dispatch(registry, [run], identifier, entry["attempts"][-1]["id"]) == {
        "artifact_sha256s": [entry["artifacts"][0]["sha256"]]
    }
    with pytest.raises(ValueError, match="durable pending work"):
        watcher.validate_dispatch(registry, [run], identifier, "f" * 32)


def test_dispatch_is_bounded_across_reentry_of_the_same_invocation(ledger, registry, context):
    major, minor, patch = registry["integrations"]["pymongo"]["default_version"].split(".")
    versions = [f"{major}.{minor}.{int(patch) + n}" for n in range(4)]
    ledger.api.releases[PYTHON_UPSTREAM] = [release(v, 100 + n) for n, v in enumerate(versions)]
    assert len(watcher.discover(ledger, registry, context)) == 3
    watcher.discover(ledger, registry, context)
    assert len(ledger.state["entries"]) == 4
    assert watcher.dispatch(ledger, registry, context)
    assert watcher.dispatch(ledger, registry, context)
    assert len(ledger.api.dispatches) == 3
    assert watcher.dispatch(ledger, registry, rerun(context, 2))
    assert len(ledger.api.dispatches) == 4


def test_unknown_dispatch_outcome_blocks_until_reconciliation_finds_its_run(
    ledger, registry, context
):
    identifier, entry = detect(ledger, registry, context)
    ledger.api.failure = RequestError("Connection failed")
    assert not watcher.dispatch(ledger, registry, context)
    assert not watcher.dispatch(ledger, registry, context)
    assert ledger.api.persisted["entries"][identifier]["status"] == "blocked"
    ledger.api.runs = [{"id": 1000 + n, "display_title": "Unrelated"} for n in range(100)]
    ledger.api.runs.append(workflow_run(999, identifier, entry["attempts"][-1]["id"], "failure"))
    assert watcher.dispatch(ledger, registry, context)
    resumed = ledger.api.persisted["entries"][identifier]
    assert resumed["status"] == "completed"
    assert resumed["attempts"][-1]["run_id"] == 999
    assert resumed["attempts"][-1]["conclusion"] == "failure"
    assert len(ledger.api.dispatches) == 1


@pytest.mark.parametrize("mutation", ["inputs", "artifacts"])
def test_changed_policy_or_artifacts_cannot_use_old_authorization(
    ledger, registry, context, monkeypatch, mutation
):
    identifier, _ = detect(ledger, registry, context)
    if mutation == "inputs":
        registry["integrations"]["pymongo"]["timeout_seconds"] += 1
    else:
        value = metadata(registry["integrations"]["pymongo"]["default_version"])
        value["urls"][0]["digests"]["sha256"] = "e" * 64
        monkeypatch.setattr(watcher, "package_metadata", lambda spec, version: value)
    assert watcher.dispatch(ledger, registry, context)
    assert ledger.api.persisted["entries"][identifier]["status"] == "superseded"
    assert not ledger.api.dispatches


@pytest.mark.parametrize(
    ("variable", "value"),
    [(None, None), ("COMPATIBILITY_WATCHER_ENABLED", "false"), ("GITHUB_REF", "refs/heads/x")],
)
def test_execution_context_requires_explicit_trusted_activation(
    tmp_path, monkeypatch, variable, value
):
    event = tmp_path / "event.json"
    repository = {"full_name": "example/project", "default_branch": "main"}
    event.write_text(json.dumps({"repository": repository}))
    environment = {"COMPATIBILITY_WATCHER_ENABLED": "true", "GITHUB_EVENT_PATH": str(event)}
    environment.update(GITHUB_REPOSITORY="example/project", GITHUB_REF="refs/heads/main")
    environment.update(GITHUB_EVENT_NAME="schedule", GITHUB_SERVER_URL="https://github.com")
    environment.update(GITHUB_SHA="a" * 40, GITHUB_RUN_ID="42", GITHUB_RUN_ATTEMPT="1")
    if variable is not None:
        environment[variable] = value
    for key, configured in environment.items():
        monkeypatch.setenv(key, configured)
    if variable is None:
        assert watcher.Context.environment().run_url.endswith("/42/attempts/1")
    else:
        with pytest.raises(ValueError):
            watcher.Context.environment()


def npm_metadata(spec, version, bundle):
    selected = bundle["lockfile"]["packages"][f"node_modules/{spec['package']}"]
    dist = {"tarball": selected["resolved"], "integrity": selected["integrity"]}
    repository = {"url": f"git+{spec['repository']}.git"}
    return {"name": spec["package"], "version": version, "repository": repository, "dist": dist}


@pytest.fixture
def npm_ledger(ledger, registry, monkeypatch, npm_bundle, npm_archive):
    spec = registry["integrations"]["nodejs"]
    major, minor, _ = spec["default_version"].split(".")
    version = f"{major}.{int(minor) + 1}.0"
    repository = watcher.upstream_repository(spec)
    ledger.api.releases = {repository: [release(version, repository=repository)]}
    bundle = npm_bundle("nodejs", version)
    value = npm_metadata(spec, version, bundle)
    digest = hashlib.sha256(npm_archive).hexdigest()
    archive = {"filename": f"mongodb-{version}.tgz", "sha256": digest}
    resolved = (bundle, [archive])
    monkeypatch.setattr(watcher, "package_metadata", lambda *args: deepcopy(value))
    monkeypatch.setattr(watcher, "prepare_npm_candidate", lambda *args: deepcopy(resolved))
    return ledger


def test_rate_limited_retries_reuse_the_frozen_tree_up_to_a_durable_ceiling(
    npm_ledger, registry, context, monkeypatch
):
    identifier, entry = detect(npm_ledger, registry, context)
    bundle = deepcopy(entry["npm_bundle"])
    monkeypatch.setattr(
        watcher, "prepare_npm_candidate", lambda *args: pytest.fail("Resolved the tree again")
    )
    npm_ledger.api.failure = RATE_LIMITED
    for attempt in range(1, 4):
        assert watcher.discover(npm_ledger, registry, rerun(context, attempt)) == []
        assert not watcher.dispatch(npm_ledger, registry, rerun(context, attempt))
        assert len(npm_ledger.api.dispatches) == attempt
    saved = npm_ledger.state["entries"]
    assert list(saved) == [identifier]
    assert saved[identifier]["status"] == "blocked" and saved[identifier]["npm_bundle"] == bundle
    assert not watcher.dispatch(npm_ledger, registry, rerun(context, 4))
    assert len(npm_ledger.api.dispatches) == 3


def test_npm_dispatch_transfers_only_the_recorded_bundle(
    npm_ledger, registry, context, monkeypatch
):
    identifier, entry = detect(npm_ledger, registry, context)
    assert watcher.dispatch(npm_ledger, registry, context)
    serve(monkeypatch, npm_ledger, context)
    run = {key: entry[key] for key in PROFILE}
    nonce = entry["attempts"][-1]["id"]
    prepared = watcher.validate_dispatch(registry, [run], identifier, nonce)
    assert json.loads(prepared["npm_bundle"]) == entry["npm_bundle"]
    assert prepared["npm_bundle_sha256"] == npm_bundle_digest(entry["npm_bundle"])
    npm_ledger.state["entries"][identifier]["npm_bundle"]["lockfile"]["requires"] = True
    npm_ledger.save()
    with pytest.raises(ValueError, match="durable pending work"):
        watcher.validate_dispatch(registry, [run], identifier, nonce)


def test_npm_resolution_is_isolated_and_verifies_the_published_archive(
    registry, monkeypatch, npm_bundle, npm_archive
):
    spec = registry["integrations"]["nodejs"]
    version = spec["default_version"]
    bundle = npm_bundle()
    published = watcher.npm_release(npm_metadata(spec, version, bundle), spec, version)
    calls = []

    def command(arguments, **kwargs):
        calls.append(arguments)
        payload = {key: bundle[key] for key in ("manifest", "lockfile")}
        return subprocess.CompletedProcess(arguments, 0, json.dumps(payload), "")

    monkeypatch.setattr(watcher, "command", command)
    monkeypatch.setattr(watcher, "cleanup", lambda *args: [])
    monkeypatch.setattr(npm, "registry_bytes", lambda *args: npm_archive)
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-token-not-forwarded")
    prepared, artifacts = watcher.prepare_npm_candidate(spec, version, published)
    assert prepared == bundle
    assert artifacts[0]["sha256"] == hashlib.sha256(npm_archive).hexdigest()
    docker = calls[0][: calls[0].index(bundle["resolver_image"])]
    assert "--read-only" in docker and "--cap-drop=ALL" in docker
    assert not {"--mount", "-v", "--env", "--env-file", "-e"}.intersection(docker)
    assert "synthetic-token-not-forwarded" not in calls[0]


def test_npm_release_accepts_only_the_official_repository(registry, npm_bundle):
    spec = registry["integrations"]["nodejs"]
    version = spec["default_version"]
    value = npm_metadata(spec, version, npm_bundle())
    repository = spec["repository"].removeprefix("https://")
    value["repository"]["url"] = f"git+ssh://git@{repository}.git"
    assert watcher.npm_release(value, spec, version)
    value["repository"]["url"] = spec["repository"].replace("github.com", "github.com.x.test")
    with pytest.raises(ValueError, match="official selected"):
        watcher.npm_release(value, spec, version)
