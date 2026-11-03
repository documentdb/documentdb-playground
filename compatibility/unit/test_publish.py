# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

"""Immutable history, honest freshness, safe rendering, and issue-form links."""

import json
from copy import deepcopy
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from compatibility.contracts import ROOT, timestamp
from compatibility.publish import append_result, dashboard_rows, render

pytestmark = pytest.mark.unit


def row_for(rows, record):
    return next(row for row in rows if any(r["id"] == record["id"] for r in row["history"]))


def test_result_history_remains_after_later_failure(tmp_path, record, registry):
    append_result(tmp_path / "data", record)
    failed = deepcopy(record)
    failed["id"] = "d" * 32
    failed["started_at"] = record["finished_at"]
    failed["tests"][0]["outcome"] = "failed"
    append_result(tmp_path / "data", failed)
    rows = render(tmp_path / "data", tmp_path / "site", registry)
    row = row_for(rows, record)
    assert (row["state"], len(row["history"])) == ("Failing", 2)


def test_html_escapes_failure_text_and_preserves_machine_readable_history(
    tmp_path, record, registry
):
    record["tests"][0].update(outcome="failed", message="<script>alert('test')</script>")
    append_result(tmp_path / "data", record)
    render(tmp_path / "data", tmp_path / "site", registry)
    html = (tmp_path / "site" / "index.html").read_text()
    history = json.loads((tmp_path / "site" / "history.json").read_text())
    assert "<script>alert" not in html and "&lt;script&gt;" in html and history == [record]


def test_issue_form_fields_are_prefilled(record, registry):
    rows = dashboard_rows(
        [record], registry, timestamp(record["finished_at"]), "documentdb/documentdb"
    )
    fields = parse_qs(urlparse(row_for(rows, record)["issue_url"]).query)
    assert (
        fields["integration"],
        fields["documentdb_version"],
        fields["upstream_version"],
        fields["template"],
    ) == (
        [record["integration"]],
        [record["documentdb"]["version"]],
        [record["upstream"]["version"]],
        ["compatibility-report.yml"],
    )
    form = yaml.safe_load((ROOT / ".github" / "ISSUE_TEMPLATE" / fields["template"][0]).read_text())
    field_ids = {item["id"] for item in form["body"] if "id" in item}
    assert fields.keys() - {"template", "title"} <= field_ids


def test_empty_dashboard_does_not_claim_compatibility(tmp_path, registry):
    rows = render(tmp_path / "absent", tmp_path / "site", registry)
    expected_pairs = {
        (integration, version)
        for integration, spec in registry["integrations"].items()
        if spec["enabled"]
        for version in registry["documentdb"]
    }
    assert {(row["integration"], row["documentdb_version"]) for row in rows} == expected_pairs
    assert all((row["state"], row["last_attempt"]) == ("Not tested", None) for row in rows)
    assert all(
        urlparse(row["issue_url"]).path == "/documentdb/documentdb-playground/issues/new"
        for row in rows
    )


def test_preview_is_labeled_and_does_not_offer_unprovisioned_issue_reporting(
    tmp_path, record, registry
):
    append_result(tmp_path / "data", record)
    rows = render(tmp_path / "data", tmp_path / "site", registry, preview=True)
    html = (tmp_path / "site" / "index.html").read_text()
    current = json.loads((tmp_path / "site" / "current.json").read_text())
    assert "Review prototype." in html and "not an official compatibility support matrix" in html
    assert "Report a compatibility problem" not in html
    assert all(row["issue_url"] is None for row in rows)
    assert all(row["issue_url"] is None for row in current["integrations"])
    assert row_for(rows, record)["state"] == "Working"


def test_old_failure_details_remain_visible_after_a_success(tmp_path, record, registry):
    failed = deepcopy(record)
    failed["id"] = "d" * 32
    failed["started_at"] = (timestamp(record["started_at"]) - timedelta(minutes=1)).isoformat()
    failed["finished_at"] = record["started_at"]
    failed["tests"][0].update(outcome="failed", message="Earlier assertion mismatch")
    append_result(tmp_path / "data", failed)
    append_result(tmp_path / "data", record)
    rows = render(tmp_path / "data", tmp_path / "site", registry)
    assert (
        row_for(rows, record)["state"] == "Working"
        and "Earlier assertion mismatch" in (tmp_path / "site" / "index.html").read_text()
    )
