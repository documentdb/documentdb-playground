# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

"""Immutable history, honest freshness, safe rendering, and issue-form links."""

import json
import sys
from copy import deepcopy
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from compatibility.contracts import DEMONSTRATION_TEST, ROOT, timestamp
from compatibility.publish import append_result, dashboard_rows, main, render

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


def test_failure_demonstrations_are_not_published_or_rendered(
    tmp_path, record, registry, monkeypatch
):
    demonstration = deepcopy(record)
    demonstration["id"] = "d" * 32
    demonstration["demonstration"] = True
    demonstration["expected_tests"].append(DEMONSTRATION_TEST)
    demonstration["tests"].append(
        {"id": DEMONSTRATION_TEST, "outcome": "failed", "message": "Deliberate mismatch"}
    )
    source = tmp_path / "demonstration.json"
    source.write_text(json.dumps(demonstration))
    store = tmp_path / "data"
    monkeypatch.setattr(sys, "argv", ["publish", "--result", str(source), "--store", str(store)])
    with pytest.raises(SystemExit, match="2"):
        main()
    assert not store.exists()
    append_result(store, record)
    append_result(store, demonstration)
    rows = render(store, tmp_path / "site", registry)
    assert not any(row["demonstration"] for row in rows)
    assert json.loads((tmp_path / "site" / "history.json").read_text()) == [record]
    assert DEMONSTRATION_TEST not in (tmp_path / "site" / "index.html").read_text()


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
