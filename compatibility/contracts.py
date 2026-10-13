# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

"""Validated registry, immutable result, and coverage contracts."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from packaging.version import Version

ROOT = Path(__file__).resolve().parent.parent
DEMONSTRATION_TEST = "test_failure_demonstration"
RUNTIME_PREFIXES = {"python": "3.12.", "nodejs": "24."}
MAX_NPM_BUNDLE_BYTES = 64 * 1024
PYTHON_PLATFORMS = (
    "manylinux_2_28_x86_64",
    "manylinux_2_17_x86_64",
    "manylinux2014_x86_64",
    "manylinux1_x86_64",
)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def json_digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def npm_bundle_digest(bundle: dict[str, Any]) -> str:
    validate_schema(bundle, "npm-bundle")
    contents = canonical_bytes(bundle)
    if len(contents) > MAX_NPM_BUNDLE_BYTES:
        raise ValueError("npm candidate bundle exceeded its size limit")
    return hashlib.sha256(contents).hexdigest()


def version_allowed(spec: dict[str, Any], version: str, *, upstream: bool = False) -> bool:
    pattern = (
        spec.get("watch_version_pattern", spec["version_pattern"])
        if upstream
        else spec["version_pattern"]
    )
    return bool(
        len(version) <= 40
        and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
        and re.fullmatch(pattern, version)
        and (not upstream or Version(version) >= Version(spec["default_version"]))
    )


def validate_schema(value: Any, name: str) -> None:
    """Reject unknown fields and malformed values without echoing input secrets."""
    schema = json.loads((ROOT / "compatibility" / "schemas" / f"{name}.json").read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    error = next(validator.iter_errors(value), None)
    if error is not None:
        location = ".".join(map(str, error.absolute_path)) or "<root>"
        raise ValueError(f"Invalid {name} field {location}: {error.validator}")


def read_registry(
    path: Path = ROOT / "compatibility" / "registry.yaml", *, root: Path = ROOT
) -> dict[str, Any]:
    """Load the reviewed registry and validate adapter locations and defaults."""
    registry = yaml.safe_load(path.read_text())
    validate_schema(registry, "registry")
    for name, integration in registry["integrations"].items():
        if not re.fullmatch(integration["version_pattern"], integration["default_version"]):
            raise ValueError(f"Default version is outside the policy for {name}")
        if not version_allowed(integration, integration["default_version"], upstream=True):
            raise ValueError(f"Default version is outside the watcher policy for {name}")
        for key in ("test_file", "demonstration_file"):
            test_file = (root / integration[key]).resolve()
            allowed = (root / "compatibility" / "integrations" / name).resolve()
            if (
                not allowed.is_relative_to(root.resolve())
                or test_file.parent != allowed
                or not test_file.is_file()
                or test_file.suffix != (".py" if integration["runtime"] == "python" else ".cjs")
            ):
                raise ValueError(f"Invalid adapter location for {name}")
    return dict(registry)


def timestamp(value: str) -> datetime:
    """Parse an explicitly zoned timestamp into UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamps require an explicit timezone")
    return parsed.astimezone(timezone.utc)


def expected_tests(spec: dict[str, Any], demonstration: bool) -> list[str]:
    """Return the exact scenario contract, including an explicitly labeled demonstration."""
    return list(spec["expected_tests"]) + ([DEMONSTRATION_TEST] if demonstration else [])


def validate_result(result: dict[str, Any], spec: dict[str, Any] | None = None) -> None:
    """Validate raw records before either persistence or rendering."""
    validate_schema(result, "result")
    if "detection_id" in result and (
        result["trigger"] != "upstream_release" or result["demonstration"]
    ):
        raise ValueError("Detection provenance requires a normal upstream-release result")
    if timestamp(result["finished_at"]) < timestamp(result["started_at"]):
        raise ValueError("Result finishes before it starts")
    if timestamp(result["finished_at"]) > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("Result timestamp is implausibly far in the future")
    identifiers = [test["id"] for test in result["tests"]]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Duplicate test outcomes")
    if not set(identifiers).issubset(result["expected_tests"]):
        raise ValueError("Result contains an undeclared scenario")
    runtime = result_runtime(result)
    bundle = result["upstream"].get("npm_bundle")
    bundle_sha256 = result["upstream"].get("npm_bundle_sha256")
    npm_release = (
        runtime["name"] == "nodejs"
        and result["trigger"] == "upstream_release"
        and "detection_id" in result
    )
    if bundle is not None or bundle_sha256 is not None:
        if not npm_release:
            raise ValueError("npm candidate provenance requires a recorded upstream release")
        if bundle is not None and npm_bundle_digest(bundle) != bundle_sha256:
            raise ValueError("npm candidate bundle does not match its recorded identity")
    if npm_release and bundle_sha256 is None:
        raise ValueError("npm upstream results require a candidate bundle identity")
    if runtime["version"] is not None and not runtime["version"].startswith(
        RUNTIME_PREFIXES[runtime["name"]]
    ):
        raise ValueError("Result has an unsupported runtime version")
    if spec is not None:
        if runtime["name"] != spec["runtime"]:
            raise ValueError("Result runtime does not match the reviewed registry")
        if result["expected_tests"] != expected_tests(spec, result["demonstration"]):
            raise ValueError("Result coverage does not match the reviewed registry")
        for key in ("repository", "owner", "profile"):
            if result[key] != spec[key]:
                raise ValueError(f"Result {key} does not match the reviewed registry")


def validate_client_report(value: Any, runtime_name: str = "python") -> None:
    """Validate the untrusted container's envelope before reading or retaining its fields."""
    result_schema = json.loads((ROOT / "compatibility" / "schemas" / "result.json").read_text())
    properties = {
        "version": {"type": "string", "pattern": r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?$"},
        "runtime": result_schema["$defs"]["runtime"],
        "artifact_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "exit_code": {"type": "integer", "minimum": 0, "maximum": 5},
        "tests": result_schema["properties"]["tests"],
        "junit": {"type": "string", "maxLength": 1024 * 1024},
        "dependencies": result_schema["properties"]["upstream"]["properties"]["dependencies"],
    }
    validator = Draft202012Validator(
        {
            "type": "object",
            "additionalProperties": False,
            "required": list(properties),
            "properties": properties,
        }
    )
    if next(validator.iter_errors(value), None) is not None:
        raise ValueError("Invalid isolated-client report")
    runtime = value["runtime"]
    if (
        runtime["name"] != runtime_name
        or runtime["version"] is None
        or not runtime["version"].startswith(RUNTIME_PREFIXES[runtime_name])
    ):
        raise ValueError("Isolated-client runtime does not match the selected profile")


def artifact_sha256(result: dict[str, Any]) -> str | None:
    """Read either historical wheel provenance or the runtime-neutral artifact hash."""
    key = "wheel_sha256" if result["schema_version"] == 1 else "artifact_sha256"
    value: str | None = result["upstream"][key]
    return value


def result_runtime(result: dict[str, Any]) -> dict[str, Any]:
    """Keep version-one history readable without rewriting immutable records."""
    if result["schema_version"] == 1:
        return {"name": "python", "version": result["upstream"]["python_version"]}
    return dict(result["upstream"]["runtime"])


def compatibility_state(result: dict[str, Any]) -> str:
    """A conclusive assertion failure wins; incomplete execution never becomes Working."""
    if any(test["outcome"] == "failed" for test in result["tests"]):
        return "Failing"
    if (
        result["execution_error"]
        or not result["documentdb"]["actual_extension_version"]
        or not result["documentdb"]["actual_postgres_version"]
        or result["upstream"]["actual_version"] is None
        or Version(result["upstream"]["actual_version"]) != Version(result["upstream"]["version"])
        or not artifact_sha256(result)
        or not result["upstream"]["client_image"]
        or not result_runtime(result)["version"]
        or not result["upstream"]["dependencies"]
        or (
            result["trigger"] == "upstream_release"
            and result_runtime(result)["name"] == "nodejs"
            and not result["upstream"].get("npm_bundle")
        )
    ):
        return "Not tested"
    outcomes = {test["id"]: test["outcome"] for test in result["tests"]}
    if all(outcomes.get(name) == "passed" for name in result["expected_tests"]):
        return "Working"
    return "Not tested"


def suite_files(integration: str, root: Path = ROOT) -> list[Path]:
    """Use the same explicit source set for provenance and the isolated client build."""
    adapter = root / "compatibility" / "integrations" / integration
    files = [
        root / "compatibility" / "pyproject.toml",
        root / "compatibility" / "requirements.txt",
        root / "compatibility" / "__init__.py",
        root / "compatibility" / "client.py",
        root / "compatibility" / "contracts.py",
        root / "compatibility" / "runner.py",
        root / "compatibility" / "npm.py",
        root / "compatibility" / "schemas" / "registry.json",
        root / "compatibility" / "schemas" / "result.json",
        adapter / "Dockerfile",
    ]
    for pattern in ("*.py", "*.cjs", "package*.json", "requirements.txt"):
        files.extend(adapter.glob(pattern))
    if any(path.suffix == ".cjs" for path in files):
        files.extend(root / "compatibility" / name for name in ("client.cjs", "report.cjs"))
    candidate_files = [root / "compatibility" / "schemas" / "npm-bundle.json"]
    if any(path.suffix == ".cjs" for path in files):
        candidate_files.append(root / "compatibility" / "resolve-npm.cjs")
    # Historical source revisions predate candidate preparation.
    files.extend(
        path for path in candidate_files if root == ROOT or path.exists() or path.is_symlink()
    )
    for path in files:
        if path.is_symlink():
            raise ValueError("Suite files must not be symbolic links")
    return sorted(files)


def suite_digest(integration: str, spec: dict[str, Any], root: Path = ROOT) -> str:
    """Hash execution inputs, including the runtime recipe and uncommitted local edits."""
    digest = hashlib.sha256()
    execution = {
        key: spec[key]
        for key in (
            "package",
            "runtime",
            "profile",
            "test_file",
            "demonstration_file",
            "timeout_seconds",
            "expected_tests",
        )
    }
    digest.update(json.dumps(execution, sort_keys=True, separators=(",", ":")).encode())
    for path in suite_files(integration, root):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
