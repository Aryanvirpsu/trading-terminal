"""Headless daily runner for the $500 strategy account.

Invoked by the pre-market scheduler (scheduler_run.py). Split into two stages so a
slow/throttled data scan can NEVER take down the protective exit management:

  --stage protect  (default-critical): record an equity snapshot, manage every open
      position against its stops/targets, AUTO-CLOSE anything that hit its stop AND
      AUTO-SETTLE any expired option at intrinsic (execute_stops=True), and write
      last_daily_run.json. Fast (<60s) and must always run.

  --stage scans   (best-effort): the heavy both-direction candidate report, the
      journal-learning + catalyst lab, and the paper auto-executor. These hit
      TradingView and can be throttled/slow — the scheduler runs them with their own
      timeout and treats failure as non-fatal, so they can't block the protective run.

  --stage all     (default): both, in order — for manual runs.

It intentionally NEVER opens new positions from the protective stage — per the
strategy doc, a human commits new capital with a documented thesis.

Outputs (in ~/.tradingview_mcp_data/):
  * last_daily_run.json  — full result of the most recent protective run
  * daily_run.log        — one appended summary line per stage
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timezone

# Make the package importable without relying on a pip install in the task's
# environment (falls back to the repo's src/ layout).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))  # so `import scan_both` works under Task Scheduler

DATA_DIR = os.path.expanduser("~/.tradingview_mcp_data")
LAST_RUN = os.path.join(DATA_DIR, "last_daily_run.json")
LOG_FILE = os.path.join(DATA_DIR, "daily_run.log")


def _log_line(text: str) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(text + "\n")


def run_protective(stamp: str) -> int:
    """The must-run part: snapshot equity + manage/close/settle open positions.

    Deliberately does NOT call find_trades — surfacing fresh candidates hits the
    throttle-prone screener and is not capital-preservation. Bundling it here is
    what let a slow candidate scan delay the critical exit work; candidates belong
    to the best-effort scans stage. This path only marks positions to market and
    acts on stops/expiries, so it stays fast (a couple of price fetches) even when
    TradingView is throttling.
    """
    import datetime as _dt
    from tradingview_mcp.core import portfolio
    from tradingview_mcp.core.services import strategy_service as ss

    status = ss.account_status()
    portfolio.record_equity_snapshot(ss.STRATEGY_ACCOUNT, status["total_equity"])
    management = ss.manage_positions(execute_stops=True)

    result = {
        "as_of": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "stage": "protect",
        "equity": status["total_equity"],
        "total_return_pct": status["total_return_pct"],
        "open_positions": len(status["stock_positions"]) + len(status["option_positions"]),
        "management": management,
        # candidates are produced by the scans stage (scan_both) / the live dashboard,
        # so the critical exit path never waits on the screener.
        "new_candidates": {"candidates_found": None, "note": "candidates run in the scans stage"},
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LAST_RUN, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str)

    mg = management or {}
    summary = (
        f"{stamp} | OK protect | equity={result['equity']} "
        f"| positions={result['open_positions']} "
        f"| reviewed={mg.get('open_positions_reviewed')} "
        f"| exits_auto_executed={mg.get('stops_auto_executed')} "
        f"| actions={mg.get('summary')}"
    )
    _log_line(summary)
    print(summary)
    return 0


def run_scans(stamp: str) -> int:
    """Best-effort heavy scans — each isolated so one failure can't sink the rest."""
    # Both-direction candidate report (puts + longs, with news + simulated returns).
    # Review-only — it NEVER opens a position. Writes last_scan_both.json.
    both_summary = "both=skipped"
    try:
        from scan_both import scan_both  # automation/ is on sys.path via __file__
        both = scan_both()
        c = both.get("counts", {})
        th = (both.get("scan_health") or {}).get("throttled")
        both_summary = ("both=throttled" if th
                        else f"both_longs={c.get('longs')} both_shorts={c.get('shorts')}")
    except Exception as be:
        both_summary = f"both=ERROR:{type(be).__name__}"
        _log_line(f"{stamp} | scan_both error | {be}")

    # Lab (headless, NO LLM): learn-from-journal + rules-based catalyst screen.
    lab_summary = "lab=skipped"
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lab"))
        import journal_lab, catalyst_scan
        jl = journal_lab.analyze()
        cat = catalyst_scan.scan()
        with open(os.path.join(DATA_DIR, "journal_lab.json"), "w", encoding="utf-8") as f:
            json.dump(jl, f, indent=2, default=str)
        with open(os.path.join(DATA_DIR, "catalysts.json"), "w", encoding="utf-8") as f:
            json.dump(cat, f, indent=2, default=str)
        lab_summary = (f"expectancy=${jl['overall'].get('expectancy_usd')} "
                       f"catalysts={cat['catalysts_found']}")
    except Exception as le:
        lab_summary = f"lab=ERROR:{type(le).__name__}"
        _log_line(f"{stamp} | lab error | {le}")

    # DISABLED 2026-08-12: auto_paper.run() opened new positions in the legacy
    # `strategy-500` account (ACCOUNT = "strategy-500" in lab/auto_paper.py). That
    # account was supposed to be retired 2026-08-01 in favor of lab/paper's
    # robinhood_500_baseline ledger (see PAPER_500_ACCOUNT.md / build freeze in
    # CLAUDE.md), but this scheduled call kept opening fresh trades in it
    # unnoticed (last: SOFI/PLTR/BAC on 2026-08-06) because it lives in
    # automation/daily_runner.py + lab/auto_paper.py, outside the freeze's scope
    # (lab/paper/, lab/decision_engine.py, automation/paper_scheduler.py) so
    # nothing flagged it. The protect stage above still manages/closes strategy-500's
    # existing open positions (SOFI, PLTR, BAC) to a clean exit — it just won't open
    # new ones. Re-enable only if the legacy account is intentionally revived.
    auto_summary = "auto=disabled (legacy strategy-500 retired 2026-08-01, see daily_runner.py comment)"

    summary = f"{stamp} | OK scans | {both_summary} | {lab_summary} | {auto_summary}"
    _log_line(summary)
    print(summary)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["protect", "scans", "all"], default="all")
    args = ap.parse_args()

    stamp = datetime.now(timezone.utc).isoformat()
    try:
        if args.stage in ("protect", "all"):
            run_protective(stamp)
        if args.stage in ("scans", "all"):
            run_scans(stamp)
        return 0
    except Exception as e:  # noqa: BLE001 — a scheduled job must never crash silently
        _log_line(f"{stamp} | ERROR ({args.stage}) | {type(e).__name__}: {e}")
        _log_line(traceback.format_exc())
        print(f"daily_runner ({args.stage}) failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
