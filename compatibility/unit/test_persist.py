# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

"""Exercise history publication against real disposable Git repositories."""

import json
import os
import shlex
import subprocess
from copy import deepcopy

import pytest

from compatibility.contracts import ROOT

pytestmark = pytest.mark.unit
SCRIPT = ROOT / "compatibility/persist.sh"
RUN_URL = "https://github.com/example/project/actions/runs/42/attempts/1"


def git(directory, *arguments):
    command = ["git", "-C", str(directory), *arguments]
    return subprocess.run(command, capture_output=True, text=True, check=True, timeout=30).stdout


def history(origin):
    names = git(origin, "ls-tree", "-r", "--name-only", "compatibility-data").split()
    return {name: json.loads(git(origin, "show", f"compatibility-data:{name}")) for name in names}


@pytest.fixture
def history_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    origin, source = tmp_path / "origin.git", tmp_path / "source"
    git(tmp_path, "init", "--bare", "--quiet", "--initial-branch=main", str(origin))
    git(tmp_path, "clone", "--quiet", str(origin), str(source))
    (source / "source-only").write_text("Must not be published with result data\n")
    git(source, "add", "source-only")
    identity = ["-c", "user.name=Test Fixture", "-c", "user.email=fixture@example.test"]
    git(source, *identity, "commit", "--quiet", "-m", "Create a test source revision")
    git(source, "push", "--quiet", "origin", "main")
    return source, origin


def persist(source, record, **environment):
    record["run_url"] = RUN_URL
    path = source.parent / f"{record['id']}.json"
    path.write_text(json.dumps(record))
    command = ["bash", str(SCRIPT), str(path), RUN_URL]
    options = {"capture_output": True, "text": True, "timeout": 60}
    return subprocess.run(command, cwd=source, env={**os.environ, **environment}, **options)


def test_first_publication_creates_only_result_data_and_cleans_up(history_repo, tmp_path, record):
    source, origin = history_repo
    state = [git(source, *command) for command in (["rev-parse", "HEAD"], ["worktree", "list"])]
    result = persist(source, record)
    assert result.returncode == 0, result.stderr
    assert history(origin) == {f"results/{record['id']}.json": record}
    assert [git(source, "rev-parse", "HEAD"), git(source, "worktree", "list")] == state
    assert git(source, "status", "--porcelain") == ""
    assert not list(tmp_path.glob("compat-history.*"))


def test_concurrent_writer_is_preserved_when_a_push_retries(history_repo, tmp_path, record):
    source, origin = history_repo
    competitor, marker = tmp_path / "competitor", shlex.quote(str(tmp_path / "competing-push"))
    git(tmp_path, "clone", "--quiet", str(origin), str(competitor))
    competing = {**deepcopy(record), "id": "c" * 32, "run_url": RUN_URL}
    (tmp_path / "competing.json").write_text(json.dumps(competing))
    command = shlex.join(["bash", str(SCRIPT), str(tmp_path / "competing.json"), RUN_URL])
    writer = tmp_path / "concurrent-writer.sh"
    writer.write_text(f"""git() {{
  if [[ "$1" == push && ! -e {marker} ]]; then
    touch {marker}
    (cd {shlex.quote(str(competitor))} && {command}) || return
  fi
  command git "$@"
}}
""")
    result = persist(source, record, BASH_ENV=str(writer))
    assert result.returncode == 0, result.stderr
    assert "retrying from the current remote history" in result.stderr
    assert history(origin) == {f"results/{r['id']}.json": r for r in (record, competing)}
    assert not list(tmp_path.glob("compat-history.*"))
