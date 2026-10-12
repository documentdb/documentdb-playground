# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

"""Playground entrypoints delegate strict results to the canonical runner."""

import json
import os
import shutil
import subprocess
import sys

import pytest

from compatibility.contracts import ROOT


@pytest.mark.parametrize("integration,playground", [("pymongo", "pymongo")])
@pytest.mark.parametrize("exit_code", [0, 1])
def test_compatibility_entrypoint(tmp_path, registry, integration, playground, exit_code):
    module = tmp_path / "stub" / "compatibility"
    module.mkdir(parents=True)
    (module / "__init__.py").write_text("")
    (module / "runner.py").write_text(
        "import json, os, sys\n"
        "print(json.dumps({'cwd': os.getcwd(), 'args': sys.argv[1:]}))\n"
        f"sys.exit({exit_code})\n"
    )
    (tmp_path / "python3").symlink_to(sys.executable)
    dirname = shutil.which("dirname")
    bash = shutil.which("bash")
    assert dirname is not None and bash is not None
    (tmp_path / "dirname").symlink_to(dirname)
    args = [
        "--output",
        "compatibility/.test-results/entrypoint/result.json",
        "--version",
        registry["integrations"][integration]["default_version"],
    ]
    process = subprocess.run(
        [
            bash,
            str(ROOT / "playgrounds" / playground / "scripts" / "run-test.sh"),
            "--compatibility",
            *args,
        ],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": str(tmp_path),
            "PYTHONPATH": str(module.parent),
            "PYTHONSAFEPATH": "1",
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert process.returncode == exit_code, process.stderr
    assert json.loads(process.stdout) == {
        "cwd": str(ROOT),
        "args": [*args, "--integration", integration],
    }
