"""C0 G5-B — a fully synthetic, network-free paper-trading scenario, run identically on
the host reference and inside the container, to compare decisions/DB rows/reports/
config_version at the REAL CLI/integration level (not just unit-test fixtures, which
G5-A already covers via the full pytest suite).

Deliberately does NOT reuse tests/unit/test_paper_trading.py's `fresh_db` fixture: that
fixture monkeypatches ~15 PAPER_* env vars, which would change `config_version()` away
from the K3 baseline (`cfg-a0eede144e`) — the whole point here is to run under the
*default* config, exactly like Case 1/Case 2 actually do. Only PAPER_DATA_DIR (a
location, not a hashed knob) is set, matching the profile under test.

Three synthetic signals for one symbol/strategy, structurally identical to the audited
`_result()`/`_quote()` shapes in tests/unit/test_paper_trading.py: one MONITOR, one
REJECT, one TRADEABLE (which fills, then is closed at its target on a second call).
Zero network calls — `broker.submit_entry` and `broker.manage_open_positions` only
touch the DB and the `Quote` object passed in.

Usage:  python g5_fixture_scenario.py <label>
Output: prints one JSON object to stdout: {signals, account, report, config_version,
        schema_version, audit_count}. Volatile fields (timestamps, signal_id, order_id,
        position_id — all time-derived) are NORMALIZED to fixed placeholders, not
        dropped, so the comparison is over everything economically meaningful:
        symbol, strategy, action, gates, entry/stop/target/quantity, quality,
        confidence, config_version, schema_version, P&L, reconciliation.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("lab", "dashboard", "src"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

from paper import broker, db, journal, report  # noqa: E402
from paper.fills import Quote  # noqa: E402


def _result(decision="TRADEABLE", *, gates_ok=True, symbol="AAPL"):
    entry = 40.0
    gates = [{"name": n, "passed": gates_ok, "blocking": True,
              "reason": "" if gates_ok else "failed"}
             for n in ("quality_threshold", "positive_ev", "liquidity")]
    return {"symbol": symbol, "decision": decision, "direction": "LONG", "price": entry,
            "entry_range": [entry, entry * 1.003], "stop": entry * 0.97,
            "target": entry * 1.06, "suggested_shares": 10,
            "decision_gates": gates,
            "failed_gates": [] if gates_ok else ["quality_threshold"],
            "freshness": {"state": "fresh", "label": "fresh"},
            "data_source": "yahoo", "data_state": "fresh",
            "ev_breakdown": {"ev_per_share": 0.8, "expected_r": 0.3},
            "confidence_quality": 70, "quality_threshold": 45}


def _quote(symbol="AAPL", last=40.0, **kw):
    d = dict(bid=last - 0.05, ask=last + 0.05, open=last, high=last * 1.01,
              low=last * 0.99, volume=5_000_000, source_ts=0.0, provider="yahoo")
    d.update(kw)
    return Quote(symbol, last=last, **d)


TIME_FIELDS = {"created_at", "updated_at", "closed_at", "filled_at", "outcome_at",
               "recorded_at", "generated_at", "at", "tracked_until"}
ID_FIELDS = {"signal_id", "order_id", "position_id", "fill_id", "audit_id"}


def normalize(obj):
    """Replace time-derived fields with fixed placeholders (recursively). Keeps every
    other field — including all decision/economic content — untouched."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in TIME_FIELDS and isinstance(v, str):
                out[k] = "<TIME>"
            elif k in ID_FIELDS and isinstance(v, str):
                out[k] = "<ID>"
            else:
                out[k] = normalize(v)
        return out
    if isinstance(obj, list):
        return [normalize(x) for x in obj]
    if isinstance(obj, str):
        # ISO timestamps embedded in free text (e.g. audit detail_json blobs).
        return re.sub(r"\d{4}-\d{2}-\d{2}T[\d:.]+(\+00:00|Z)?", "<TIME>", obj)
    return obj


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "run"
    session_date = dt.date.today().isoformat()

    r1 = broker.submit_entry(_result(decision="MONITOR"), _quote(),
                              strategy="g5fixture", sector="technology",
                              session_date=session_date)
    r2 = broker.submit_entry(_result(decision="REJECT", gates_ok=False), _quote(),
                              strategy="g5fixture", sector="technology",
                              session_date=session_date)
    r3 = broker.submit_entry(_result(decision="TRADEABLE"), _quote(),
                              strategy="g5fixture", sector="technology",
                              session_date=session_date)
    # Close the TRADEABLE position cleanly at its target (high=42.4 clears target
    # 40*1.06=42.4; low=39.5 stays above stop 40*0.97=38.8, so no stop/target
    # ambiguity — deterministic target_hit, no same-bar collision policy invoked).
    exits = broker.manage_open_positions(
        {"AAPL": _quote("AAPL", last=42.4, high=42.5, low=39.5)},
        session_date=session_date)

    sigs = journal.signals(session_date=session_date)
    acct = broker.account(session_date=session_date)
    rep = report.daily(session_date)
    audit_rows = db.query("SELECT event FROM audit ORDER BY audit_id")

    out = {
        "label": label,
        "session_date": session_date,
        "entries": {"monitor": r1, "reject": r2, "tradeable": r3, "exits": exits},
        "signals": sigs,
        "account": acct,
        "report": rep,
        "config_version": db.config_version(),
        "schema_version": db.schema_version(),
        "audit_event_counts": sorted(r["event"] for r in audit_rows),
    }
    print(json.dumps(normalize(out), indent=2, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
