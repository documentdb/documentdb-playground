# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

"""Immutable history, honest freshness, safe rendering, and issue-form links."""

import json
import sys
from copy import deepcopy
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from compatibility.contracts import DEMONSTRATION_TEST, ROOT, timestamp
from compatibility.publish import append_result, dashboard_rows, main, render

pytestmark = pytest.mark.unit


def test_later_failure_keeps_history_and_escapes_its_message(tmp_path, record, registry):
    failed = deepcopy(record)
    failed.update(id="d" * 32, started_at=record["finished_at"])
    failed["tests"][0].update(outcome="failed", message="<script>alert('test')</script>")
    for result in (record, failed):
        append_result(tmp_path / "data", result)
    rows = render(tmp_path / "data", tmp_path / "site", registry)
    row = next(row for row in rows if row["history"])
    html = (tmp_path / "site" / "index.html").read_text()
    assert (row["state"], len(row["history"])) == ("Failing", 2)
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    assert json.loads((tmp_path / "site" / "history.json").read_text()) == [record, failed]


def test_issue_links_prefill_fields_defined_by_the_issue_form(record, registry):
    rows = dashboard_rows([record], registry, timestamp(record["finished_at"]), "owner/repo")
    fields = parse_qs(urlparse(next(row for row in rows if row["history"])["issue_url"]).query)
    assert fields["upstream_version"] == [record["upstream"]["version"]]
    form = yaml.safe_load((ROOT / ".github/ISSUE_TEMPLATE" / fields["template"][0]).read_text())
    assert fields.keys() - {"template", "title"} <= {item.get("id") for item in form["body"]}


def test_empty_dashboard_does_not_claim_compatibility(tmp_path, registry):
    rows = render(tmp_path / "absent", tmp_path / "site", registry)
    assert rows and all((row["state"], row["last_attempt"]) == ("Not tested", None) for row in rows)


def test_failure_demonstrations_are_never_published(tmp_path, record, registry, monkeypatch):
    record.update(id="d" * 32, demonstration=True)
    record["expected_tests"].append(DEMONSTRATION_TEST)
    record["tests"].append({"id": DEMONSTRATION_TEST, "outcome": "failed", "message": "Mismatch"})
    source, store = tmp_path / "demonstration.json", tmp_path / "data"
    source.write_text(json.dumps(record))
    monkeypatch.setattr(sys, "argv", ["publish", "--result", str(source), "--store", str(store)])
    with pytest.raises(SystemExit, match="2"):
        main()
    assert not store.exists()
    append_result(store, record)
    render(store, tmp_path / "site", registry)
    assert json.loads((tmp_path / "site" / "history.json").read_text()) == []
    assert DEMONSTRATION_TEST not in (tmp_path / "site" / "index.html").read_text()
