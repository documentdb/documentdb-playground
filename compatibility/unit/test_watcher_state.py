# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

"""Exercise metadata boundaries and the ledger's Git object protocol locally."""

import base64
import io
import subprocess
import urllib.error

import pytest

from compatibility import watcher_state as state

pytestmark = pytest.mark.unit
REFERENCE = f"refs/heads/{state.STATE_BRANCH}"
GIT = ["git", "-c", f"user.name={state.BOT['name']}", "-c", f"user.email={state.BOT['email']}"]
GITHUB_URL = "https://api.github.com/repos/example/project"
PYPI_URL = "https://pypi.org/pypi/pymongo/4.18.0/json"


class LocalGitAPI(state.GitHub):
    def __init__(self, path):
        super().__init__("example/project", "unit-token")
        self.path = path
        subprocess.run(["git", "init", "--bare", "--quiet", str(path)], check=True)

    def git(self, *arguments, data=None):
        command = [*GIT, "--git-dir", str(self.path), *arguments]
        result = subprocess.run(command, input=data, capture_output=True)
        return None if result.returncode else result.stdout.decode().strip()

    def local(self, method, path, payload=None):
        name = path.rsplit("/", 1)[1]
        if method == "GET" and path.startswith("/git/ref/"):
            head = self.git("rev-parse", "--verify", "--quiet", REFERENCE)
            if head is None:
                raise state.RequestError("Reference missing", 404)
            return {"object": {"sha": head}}
        if method == "GET" and path.startswith("/git/commits/"):
            return {"tree": {"sha": self.git("rev-parse", f"{name}^{{tree}}")}}
        if method == "GET" and path.startswith("/git/trees/"):
            files = []
            for line in self.git("ls-tree", "--long", name).splitlines():
                mode, kind, sha, size, filename = line.split(maxsplit=4)
                files.append(dict(mode=mode, type=kind, sha=sha, size=int(size), path=filename))
            return {"truncated": False, "tree": files}
        if method == "GET" and path.startswith("/git/blobs/"):
            content = base64.b64encode(self.git("cat-file", "blob", name).encode()).decode()
            return {"encoding": "base64", "content": content}
        if path == "/git/trees":
            item = payload["tree"][0]
            sha = self.git("hash-object", "-w", "--stdin", data=item["content"].encode())
            listing = f"{item['mode']} blob {sha}\t{item['path']}\n"
            return {"sha": self.git("mktree", data=listing.encode())}
        if path == "/git/commits":
            parents = [argument for parent in payload["parents"] for argument in ("-p", parent)]
            message = payload["message"].encode()
            return {"sha": self.git("commit-tree", payload["tree"], *parents, data=message)}
        if method == "PATCH":
            assert payload["force"] is False
            current = self.git("rev-parse", REFERENCE)
            if self.git("merge-base", "--is-ancestor", current, payload["sha"]) is None:
                raise state.RequestError("Non-fast-forward update", 422)
        self.git("update-ref", REFERENCE, payload["sha"])
        return {"object": {"sha": payload["sha"]}}


class Transport:
    def __init__(self, response):
        self.response, self.handlers, self.requests = response, [], []

    def __call__(self, handler):
        self.handlers.append(handler)
        return self

    def open(self, request, timeout):
        self.requests.append(request)
        if isinstance(self.response, int):
            raise urllib.error.HTTPError(request.full_url, self.response, "unit", {}, None)
        return io.BytesIO(self.response)


def scanned(run):
    url = f"https://github.com/example/project/actions/runs/{run}/attempts/1"
    return {"run_url": url, "at": "2026-10-01T00:00:00Z", "observations": [], "error": None}


def test_git_ledger_round_trips_and_preserves_a_concurrent_writers_evidence(tmp_path):
    api = LocalGitAPI(tmp_path / "state.git")
    initial = state.GitLedger(api)
    initial.load()
    initial.save()
    first, second = state.GitLedger(api), state.GitLedger(api)
    first.load()
    second.load()
    first.state["last_scan"] = scanned(1)
    second.state["last_scan"] = scanned(2)
    first.save()
    with pytest.raises(state.RequestError) as error:
        second.save()
    assert error.value.status == 422 and second.head == initial.head
    reloaded = state.GitLedger(api)
    reloaded.load()
    assert reloaded.head == first.head and reloaded.state == first.state
    commits = api.git("rev-list", "--parents", REFERENCE).splitlines()
    assert len(commits) == 2 and len(commits[-1].split()) == 1
    assert "Signed-off-by: github-actions[bot]" in api.git("show", "-s", "--format=%B", first.head)


def test_state_load_rejects_a_symlink_instead_of_following_it(tmp_path):
    api = LocalGitAPI(tmp_path / "state.git")
    link = {"path": "ledger.json", "mode": "120000", "content": "../outside.json"}
    tree = api.local("POST", "/git/trees", {"tree": [link]})["sha"]
    commit = api.local("POST", "/git/commits", {"tree": tree, "parents": [], "message": "Link"})
    api.local("POST", "/git/refs", {"ref": REFERENCE, "sha": commit["sha"]})
    with pytest.raises(ValueError, match="regular ledger"):
        state.GitLedger(api).load()


def test_credentials_reach_only_reviewed_sources_without_redirects(monkeypatch):
    transport = Transport(b'{"ok": true}')
    monkeypatch.setattr(state.urllib.request, "build_opener", transport)
    for url in (PYPI_URL, GITHUB_URL.replace(".com", ".com.x.test")):
        with pytest.raises(ValueError, match="reviewed public sources"):
            state.request_json(url, token="unit-token")
    assert not transport.requests
    assert state.request_json(GITHUB_URL, token="unit-token") == {"ok": True}
    state.request_json(PYPI_URL)
    github, pypi = transport.requests
    assert github.get_header("Authorization") == "Bearer unit-token"
    assert pypi.get_header("Authorization") is None
    assert transport.handlers[0].redirect_request(None, None, 302, "", {}, "https://x.test") is None


@pytest.mark.parametrize(("response", "retryable"), [(429, True), (403, False), (b"{", False)])
def test_only_definite_rate_limits_are_retryable(monkeypatch, response, retryable):
    monkeypatch.setattr(state.urllib.request, "build_opener", Transport(response))
    with pytest.raises(state.RequestError) as error:
        state.request_json(GITHUB_URL, token="unit-token")
    assert error.value.status == (response if isinstance(response, int) else None)
    assert error.value.retryable is retryable
