"""v1.0/v1.1 evidence boundary: journal rows must be labelled so results are never pooled."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for folder in ("src", "dashboard", "lab"):
    sys.path.insert(0, str(ROOT / folder))

from paper import db, journal


def test_signal_rows_carry_v11_engine_version_and_unchanged_config_hash(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_DATA_DIR", str(tmp_path))
    db.reset_for_tests(str(tmp_path))
    try:
        cfg_before = db.config_version()
        sid = journal.record_signal(
            {"symbol": "BAC", "decision": "TRADEABLE", "price": 40.0, "entry_range": [40.0, 40.1],
             "stop": 39.6, "target": 42.0, "decision_gates": [], "failed_gates": []},
            strategy="liquid_momentum", session_date="2026-09-25")
        row = db.query_one("SELECT engine_version FROM signals WHERE signal_id=?", (sid,))
        assert row["engine_version"] == "decision_engine/gates-v1.1"
        assert db.config_version() == cfg_before      # strategy fingerprint untouched
    finally:
        db.close()
