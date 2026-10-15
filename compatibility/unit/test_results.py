# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

"""Shared immutable history and compatibility/freshness decisions."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta

import pytest

from compatibility.contracts import DEMONSTRATION_TEST, timestamp
from compatibility.results import append_result, dashboard_rows, read_result

pytestmark = pytest.mark.unit


def row_for(rows, record):
    return next(row for row in rows if any(r["id"] == record["id"] for r in row["history"]))


def test_append_is_idempotent(tmp_path, record):
    path = append_result(tmp_path, record)
    append_result(tmp_path, record)
    assert read_result(path) == record


def test_existing_result_cannot_be_overwritten(tmp_path, record):
    append_result(tmp_path, record)
    changed = deepcopy(record)
    changed["tests"][0]["outcome"] = "failed"
    with pytest.raises(ValueError, match="overwrite"):
        append_result(tmp_path, changed)


def test_symlink_result_rejected(tmp_path, record):
    original = append_result(tmp_path / "original", record)
    link = tmp_path / "linked.json"
    link.symlink_to(original)
    with pytest.raises(ValueError, match="regular JSON"):
        read_result(link)


def test_arrival_order_does_not_overwrite_a_newer_failure(record, registry):
    failed = deepcopy(record)
    failed["id"] = "d" * 32
    failed["started_at"] = record["finished_at"]
    failed["tests"][0]["outcome"] = "failed"
    rows = dashboard_rows([failed, record], registry, timestamp(record["finished_at"]))
    assert row_for(rows, record)["state"] == "Failing"


def test_infrastructure_failure_keeps_conclusive_result_and_attempt_error(record, registry):
    unavailable = deepcopy(record)
    unavailable["id"] = "d" * 32
    unavailable["started_at"] = record["finished_at"]
    unavailable["tests"] = []
    unavailable["execution_error"] = "Artifact download unavailable"
    rows = dashboard_rows([record, unavailable], registry, timestamp(record["finished_at"]))
    row = row_for(rows, record)
    assert (row["state"], row["latest_attempt_state"], row["error"]) == (
        "Working",
        "Not tested",
        "Artifact download unavailable",
    )


def test_configured_freshness_boundary(record, registry):
    days = registry["integrations"][record["integration"]]["freshness_days"]
    boundary = timestamp(record["finished_at"]) + timedelta(days=days)
    fresh = dashboard_rows([record], registry, boundary)
    stale = dashboard_rows([record], registry, boundary + timedelta(milliseconds=1))
    assert (row_for(fresh, record)["state"], row_for(stale, record)["state"]) == (
        "Working",
        "Stale",
    )


def test_changed_suite_is_stale(record, registry):
    record["suite_digest"] = "0" * 64
    rows = dashboard_rows([record], registry, timestamp(record["finished_at"]))
    assert row_for(rows, record)["state"] == "Stale"


def test_new_version_does_not_inherit_an_old_pass(record, registry, other_version):
    newer = deepcopy(record)
    newer["id"] = "d" * 32
    newer["upstream"]["version"] = other_version
    newer["upstream"]["actual_version"] = None
    newer["tests"] = []
    newer["execution_error"] = "Not installed"
    rows = dashboard_rows([record, newer], registry, timestamp(record["finished_at"]))
    assert row_for(rows, record)["state"] == "Working"
    assert row_for(rows, newer)["state"] == "Not tested"


def test_synthetic_failure_does_not_poison_real_compatibility(record, registry):
    demonstration = deepcopy(record)
    demonstration["id"] = "d" * 32
    demonstration["demonstration"] = True
    demonstration["expected_tests"].append(DEMONSTRATION_TEST)
    demonstration["tests"].append(
        {"id": DEMONSTRATION_TEST, "outcome": "failed", "message": "Deliberate mismatch"}
    )
    rows = dashboard_rows([record, demonstration], registry, timestamp(record["finished_at"]))
    assert row_for(rows, record)["state"] == "Working"
    assert row_for(rows, demonstration)["state"] == "Failing"


def test_concurrent_appends_preserve_both_results(tmp_path, record):
    other = deepcopy(record)
    other["id"] = "d" * 32
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda result: append_result(tmp_path, result), [record, other]))
    assert {path.stem for path in tmp_path.glob("*.json")} == {record["id"], other["id"]}


def test_store_directory_symlinks_are_rejected(tmp_path, record):
    store = tmp_path / "store"
    store.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(store, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"):
        append_result(link, record)
