"""Run Lab — headless orchestrator. Runs unattended (scheduled task), NO Claude.

  1. journal_lab   — learn from our own closed trades (what's actually working)
  2. catalyst_scan — rules-based news/event screen for the watchlist
  3. strategy_lab  — self-evolving walk-forward validation → champions.json

Writes lab_report.json (one combined artifact for the dashboard) and appends a
one-line summary to lab.log. Wire into automation/daily_runner.py or its own
scheduled task so the engine improves itself on a cadence without any LLM call.
"""
from __future__ import annotations
import json, os, sys, traceback, datetime as dt

sys.path.insert(0, os.path.dirname(__file__))
DATA_DIR = os.path.expanduser("~/.tradingview_mcp_data")
REPORT = os.path.join(DATA_DIR, "lab_report.json")
LOG = os.path.join(DATA_DIR, "lab.log")


def main(quick: bool = False) -> int:
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    out = {"ran_at": stamp, "mode": "quick" if quick else "full"}
    try:
        import journal_lab, catalyst_scan, strategy_lab, decision_engine, calibration
        out["journal"] = journal_lab.analyze()
        out["catalysts"] = catalyst_scan.scan()
        out["strategies"] = strategy_lab.evolve(quick=quick)

        # Decision engine sweep — evaluates a universe, auto-logs every prediction
        # (feeds calibration), surfaces only TRADEABLE ones. This is the autonomous
        # loop: scan -> predict -> log -> (trades resolve) -> calibrate.
        universe = ["AAPL", "BAC", "NVDA", "AMD", "SOFI"] if quick else \
                   ["AAPL", "BAC", "NVDA", "AMD", "SOFI", "F", "PLTR", "ABNB", "AXP", "CRWD"]
        sweep_res = decision_engine.sweep(universe)      # parallel (thread pool)
        evals = [{"symbol": sym,
                  "decision": (sweep_res.get(sym) or {}).get("decision"),
                  "quality": (sweep_res.get(sym) or {}).get("confidence_quality"),
                  "p_direction": (sweep_res.get(sym) or {}).get("p_direction"),
                  "ev_per_share": (sweep_res.get(sym) or {}).get("expected_value_per_share")}
                 for sym in universe]
        out["engine"] = {"scanned": len(evals),
                         "tradeable": [e for e in evals if e["decision"] == "TRADEABLE"],
                         "all": evals}
        out["calibration"] = calibration.calibrate()   # recompute after fresh predictions

        os.makedirs(DATA_DIR, exist_ok=True)
        json.dump(out, open(REPORT, "w", encoding="utf-8"), indent=2, default=str)
        line = (f"{stamp} | OK | journal_expectancy=${out['journal']['overall'].get('expectancy_usd')} "
                f"| catalysts={out['catalysts']['catalysts_found']} "
                f"| champions=gen{out['strategies']['generation']}:{out['strategies']['champions_count']} "
                f"| engine_tradeable={len(out['engine']['tradeable'])}/{out['engine']['scanned']} "
                f"| calib={out['calibration']['matched_samples']}/{out['calibration']['min_samples_for_trust']}")
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        print(line)
        return 0
    except Exception as e:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{stamp} | ERROR | {type(e).__name__}: {e}\n{traceback.format_exc()}\n")
        print(f"run_lab failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main(quick="--quick" in sys.argv))
