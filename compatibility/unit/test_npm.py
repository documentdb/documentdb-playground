# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

"""Lockfile provenance and offline npm preparation without executing package code."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from compatibility import npm
from compatibility.contracts import ROOT, suite_digest, suite_files

pytestmark = pytest.mark.unit


@pytest.fixture
def reviewed(tmp_path, monkeypatch, registry, npm_bundle, npm_archive):
    spec = registry["integrations"]["nodejs"]
    bundle = npm_bundle()
    adapter = tmp_path / "source" / Path(spec["test_file"]).parent
    adapter.mkdir(parents=True)
    (adapter / "package.json").write_text(json.dumps(bundle["manifest"]))
    (adapter / "package-lock.json").write_text(json.dumps(bundle["lockfile"]))
    monkeypatch.setattr(npm, "ROOT", tmp_path / "source")
    monkeypatch.setattr(npm, "suite_files", lambda integration: list(adapter.glob("*.json")))
    monkeypatch.setattr(npm, "registry_bytes", lambda address, limit: npm_archive)
    return spec


def prepare(tmp_path, spec, name="context", version=None, **options):
    context = tmp_path / name
    context.mkdir()
    version = version or spec["default_version"]
    return context, npm.prepare_node_client(context, spec, version, **options)


def test_locked_archive_is_verified_recorded_and_cached(
    tmp_path, reviewed, npm_archive, monkeypatch
):
    cache = tmp_path / "cache"
    context, digest = prepare(tmp_path, reviewed, package_cache=cache)
    assert digest == hashlib.sha256(npm_archive).hexdigest()
    assert json.loads((context / "install-report.json").read_text())["install"] == [
        {
            "path": f"node_modules/{reviewed['package']}",
            "name": reviewed["package"],
            "version": reviewed["default_version"],
            "sha256": digest,
        }
    ]
    assert (context / "npm-packages" / f"{digest}.tgz").read_bytes() == npm_archive
    monkeypatch.setattr(npm, "registry_bytes", lambda *args: pytest.fail("Cache used the network"))
    assert prepare(tmp_path, reviewed, "offline", package_cache=cache)[1] == digest


def test_downloaded_archive_is_verified_before_caching(tmp_path, reviewed, monkeypatch):
    monkeypatch.setattr(npm, "registry_bytes", lambda *args: b"corrupted")
    cache = tmp_path / "cache"
    with pytest.raises(ValueError, match="integrity"):
        prepare(tmp_path, reviewed, package_cache=cache)
    assert not list(cache.iterdir())


@pytest.mark.parametrize("integration", ["nodejs", "mongoose"])
def test_reviewed_node_lockfiles_pin_the_registry_default(registry, integration):
    spec = registry["integrations"][integration]
    adapter = ROOT / Path(spec["test_file"]).parent
    manifest, lock = (
        json.loads((adapter / name).read_text()) for name in ("package.json", "package-lock.json")
    )
    npm.validate_lock(manifest, lock, spec, spec["default_version"])


def test_node_lockfile_is_part_of_provenance(tmp_path, registry):
    spec = registry["integrations"]["nodejs"]
    for source in suite_files("nodejs"):
        destination = tmp_path / source.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    original = suite_digest("nodejs", spec, tmp_path)
    lock = tmp_path / Path(spec["test_file"]).parent / "package-lock.json"
    lock.write_text(lock.read_text() + "\n")
    assert suite_digest("nodejs", spec, tmp_path) != original


def test_candidate_bundle_uses_its_exact_lock_and_preserves_reviewed_sources(
    tmp_path, registry, monkeypatch, npm_bundle, npm_archive
):
    spec = registry["integrations"]["nodejs"]
    major, minor, _ = spec["default_version"].split(".")
    version = f"{major}.{int(minor) + 1}.0"
    bundle = npm_bundle("nodejs", version)
    monkeypatch.setattr(npm, "registry_bytes", lambda *args: npm_archive)
    context, digest = prepare(tmp_path, spec, version=version, npm_bundle=bundle)
    assert digest == hashlib.sha256(npm_archive).hexdigest()
    assert json.loads((context / "package-lock.json").read_text()) == bundle["lockfile"]
    assert suite_digest("nodejs", spec, context) == suite_digest("nodejs", spec)
    with pytest.raises(ValueError, match="lockfile"):
        prepare(tmp_path, spec, "manual", version)


@pytest.mark.parametrize(
    ("location", "field", "value", "message"),
    [
        (None, "resolved", "https://example.test/driver.tgz", "registry"),
        (None, "integrity", "sha1-unreviewed", "SHA-512"),
        ("node_modules/../../outside", "optional", True, "location"),
    ],
)
def test_candidate_lock_is_validated_before_any_archive_access(
    tmp_path, registry, monkeypatch, npm_bundle, location, field, value, message
):
    spec = registry["integrations"]["nodejs"]
    bundle = npm_bundle()
    packages = bundle["lockfile"]["packages"]
    selected = f"node_modules/{spec['package']}"
    packages[location or selected] = {**packages[selected], field: value}
    monkeypatch.setattr(npm, "registry_bytes", lambda *args: pytest.fail("Fetched an archive"))
    with pytest.raises(ValueError, match=message):
        prepare(tmp_path, spec, npm_bundle=bundle)
