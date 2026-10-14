"""dashboard/research_api.py — the read-only experiments / watchlist / docs endpoints.

The Blueprint is mounted on a bare Flask app (not dashboard/app.py, whose import starts
background pre-warm threads) with EXPERIMENTS / DOCS pointed at a temp tree. What matters:
the summaries are parsed correctly, and no request can read outside those two folders.
"""
from __future__ import annotations

import json
import os
import sys

import pytest
from flask import Flask

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "dashboard"))

import research_api  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    exp, docs = tmp_path / "experiments", tmp_path / "docs"
    ch = exp / "ch007_example_rule"
    ch.mkdir(parents=True)
    (ch / "PREREG.md").write_text("# CH-007 example — pre-registration\n\nRegistered before the run. Status: **provisional**.\n", encoding="utf-8")
    (ch / "RESULTS.md").write_text("# CH-007 example — results\n\n## Verdict: NOT SUPPORTED. Nothing to change.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n", encoding="utf-8")
    (ch / "results.json").write_text(json.dumps({"total_r": 1.5, "arms": [1, 2]}), encoding="utf-8")
    (ch / "run.py").write_text("print('not served')\n", encoding="utf-8")
    (ch / "big.json").write_text(json.dumps({"x": "y" * (research_api._MAX_JSON + 10)}), encoding="utf-8")
    (exp / "profit_mode_baseline_v1").mkdir()
    (exp / "profit_mode_baseline_v1" / "PROVENANCE.md").write_text("# Provenance\n", encoding="utf-8")
    (exp / "diagnosis_example_20261013").mkdir()
    (exp / "diagnosis_example_20261013" / "DIAGNOSIS.md").write_text("# A diagnosis" + chr(10), encoding="utf-8")
    wl = exp / "strategy_watchlist"
    wl.mkdir()
    (wl / "registry.json").write_text(json.dumps({"sources": [{"id": "a", "name": "A"}]}), encoding="utf-8")
    (wl / "README.md").write_text("# Watchlist\n", encoding="utf-8")
    docs.mkdir()
    (docs / "REPORT.md").write_text("# The report\n\nbody\n", encoding="utf-8")
    (tmp_path / "secret.md").write_text("# secret outside the roots\n", encoding="utf-8")
    monkeypatch.setattr(research_api, "EXPERIMENTS", exp)
    monkeypatch.setattr(research_api, "DOCS", docs)
    app = Flask(__name__)
    app.register_blueprint(research_api.bp)
    return app.test_client()


def test_index_classifies_and_parses_experiments(client):
    d = client.get("/api/research/index").get_json()
    by = {e["folder"]: e for e in d["experiments"]}
    ch = by["ch007_example_rule"]
    assert (ch["kind"], ch["id"], ch["status"]) == ("challenger", "CH-007", "complete")
    assert ch["verdict"].startswith("NOT SUPPORTED")
    assert ch["prereg_status"] == "provisional"
    assert ch["title"].startswith("CH-007 example")
    assert by["profit_mode_baseline_v1"]["kind"] == "baseline"
    assert by["strategy_watchlist"]["kind"] == "watchlist"
    assert by["diagnosis_example_20261013"]["kind"] == "diagnosis"
    assert [x["name"] for x in d["docs"]] == ["REPORT"]


def test_experiment_returns_text_and_json_but_never_code(client):
    d = client.get("/api/research/experiment/ch007_example_rule").get_json()
    assert set(d["documents"]) == {"PREREG.md", "RESULTS.md"}
    assert d["json"]["results.json"]["total_r"] == 1.5
    skipped = {s["name"]: s["reason"] for s in d["skipped"]}
    assert skipped["run.py"].endswith("not shown")        # code is listed, never served
    assert skipped["big.json"] == "too large"             # size cap is enforced
    assert "print('not served')" not in json.dumps(d)


@pytest.mark.parametrize("bad", [
    "..", ".git", "..%2Ftests", "%2e%2e", "ch007_example_rule%2F..%2F..", "does_not_exist", "a%00b", ".hidden",
])
def test_experiment_rejects_anything_outside_the_whitelist(client, bad):
    assert client.get(f"/api/research/experiment/{bad}").status_code == 404


@pytest.mark.parametrize("bad", ["..%2Fsecret", "..", "secret", "REPORT.md", "REPORT%2F..%2F..%2Fsecret", "nope"])
def test_doc_rejects_anything_but_a_doc_name(client, bad):
    assert client.get(f"/api/research/doc/{bad}").status_code == 404


def test_doc_and_watchlist_happy_path(client):
    doc = client.get("/api/research/doc/REPORT").get_json()
    assert doc["title"] == "The report" and "body" in doc["markdown"]
    wl = client.get("/api/research/watchlist").get_json()
    assert wl["state"] == "ok" and wl["registry"]["sources"][0]["id"] == "a" and wl["readme"].startswith("# Watchlist")


def test_missing_folders_degrade_to_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(research_api, "EXPERIMENTS", tmp_path / "nope")
    monkeypatch.setattr(research_api, "DOCS", tmp_path / "nada")
    app = Flask(__name__)
    app.register_blueprint(research_api.bp)
    c = app.test_client()
    assert c.get("/api/research/index").get_json() == {"experiments": [], "docs": []}
    assert c.get("/api/research/watchlist").get_json()["state"] == "unavailable"
