"""Paper-trading scheduler — the thing you actually run each day.

Usage:
    python automation/paper_scheduler.py premarket     # scan, analyse finalists, place <=3
    python automation/paper_scheduler.py hours         # process fills, stops, targets, tracking
    python automation/paper_scheduler.py postmarket    # expire, reconcile, write the report
    python automation/paper_scheduler.py all           # the whole day in one shot
    python automation/paper_scheduler.py status        # account + milestone progress
    python automation/paper_scheduler.py report [date] # regenerate a report (deterministic)
    python automation/paper_scheduler.py performance   # cumulative metrics

`--dry-run` on premarket journals every signal WITHOUT placing orders — use it to
watch the funnel for a few days before committing the paper account to it.

Safety: this scheduler can only ever touch the paper ledger. It never imports a broker
client, and `ROBINHOOD_TRADING_ENABLED` / `BROKER_PROVIDER` are irrelevant to it.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("lab", "dashboard", "src"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from paper import broker, db, options_shadow, report, workflow  # noqa: E402


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _write_daily_markdown(rep: dict, path: str = None) -> str:
    """Render the day's report to PAPER_DAILY_REPORT.md (overwritten each session;
    the database remains the durable record)."""
    a = rep["account"]
    lines = [
        f"# PAPER_DAILY_REPORT.md — {rep['session_date']}", "",
        f"_Generated {rep['generated_at']} · config `{rep['config_version']}`_", "",
        "## Account", "",
        f"| Metric | Value |", "|---|---|",
        f"| Equity | ${a['equity']:,.2f} |",
        f"| Cash | ${a['cash']:,.2f} |",
        f"| Realized P&L | ${a['realized_pnl']:,.2f} |",
        f"| Unrealized P&L | ${a['unrealized_pnl']:,.2f} |",
        f"| Day P&L | ${a['day_pnl']:,.2f} |",
        f"| Day P&L vs limit | ${a['day_pnl']:,.2f} of -${a['daily_loss_limit_usd']:,.2f} |",
        f"| Drawdown | ${a['drawdown_usd']:,.2f} of ${a['max_drawdown_usd']:,.2f} max |",
        f"| Buying power | ${a['buying_power']:,.2f} |",
        f"| Open positions | {a['open_positions']} |",
        f"| Cooldown | {'ACTIVE' if a['cooldown']['active'] else 'no'} |", "",
        "## Signals", "",
        f"- TRADEABLE: **{rep['signals']['tradeable']}**",
        f"- MONITOR: {rep['signals']['monitor']}",
        f"- REJECT: {rep['signals']['reject']}",
        f"- Entered: {rep['trades']['entered_count']} · Exited: {rep['trades']['exited_count']}",
        "",
    ]
    if rep["gate_failures"]:
        lines += ["## Gate failures today", "", "| Gate | Count |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in rep["gate_failures"].items()]
        lines += [""]
    sl = rep["slippage"]
    lines += ["## Execution quality", "",
              f"- Entry slippage avg: {sl['entry_avg']}",
              f"- Exit slippage avg: {sl['exit_avg']}",
              f"- Gap fills: {sl['gap_fills']}",
              f"- Stale-data attempts blocked: {rep['stale_data_attempts']}", ""]
    rec = rep["reconciliation"]
    lines += ["## Integrity", "",
              f"- P&L reconciled: **{'YES' if rec['reconciled'] else 'NO — INVESTIGATE'}** "
              f"(delta {rec['delta']})", ""]
    if rep["what_the_engine_got_wrong"]:
        lines += ["## What the engine got wrong", ""]
        for m in rep["what_the_engine_got_wrong"]:
            lines.append(f"- **{m['type']}** {m.get('symbol','')}: {m['lesson']}")
        lines += [""]
    bw = rep["blocked_winners"]
    lines += ["## Gate cost (blocked setups that would have won)", "",
              f"- Blocked winners: **{bw['count']}** of {bw['resolved_blocked']} resolved "
              f"blocked setups (win rate {bw['blocked_win_rate']}%)",
              "- If this stays high across many sessions, the gates are too tight — "
              "that is the ONLY evidence that justifies loosening them.", ""]
    path = path or os.path.join(_ROOT, "PAPER_DAILY_REPORT.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    cmd = (args[0] if args else "status").lower()
    date = args[1] if len(args) > 1 else None

    if cmd == "premarket":
        _print(workflow.premarket(date, dry_run=dry))
    elif cmd in ("hours", "market", "market_hours"):
        _print(workflow.market_hours(date))
    elif cmd == "postmarket":
        out = workflow.postmarket(date)
        path = _write_daily_markdown(out["report"])
        out["markdown"] = path
        _print({k: v for k, v in out.items() if k != "report"})
        print(f"\nwrote {path}")
    elif cmd == "all":
        out = workflow.run_all(date, dry_run=dry)
        _write_daily_markdown(out["postmarket"]["report"])
        _print({"premarket": {k: out["premarket"].get(k) for k in
                              ("state", "candidates", "evaluated", "orders_placed")},
                "market_hours": out["market_hours"],
                "postmarket": {k: v for k, v in out["postmarket"].items() if k != "report"}})
    elif cmd == "report":
        rep = report.daily(date or dt.date.today().isoformat())
        path = _write_daily_markdown(rep)
        _print(rep)
        print(f"\nwrote {path}")
    elif cmd == "performance":
        _print(report.performance())
    elif cmd == "options":
        _print({"summary": options_shadow.summary(),
                "graduation": options_shadow.graduation_readiness()})
    elif cmd == "status":
        acct = broker.account()
        perf = report.performance()
        _print({"account": {k: acct[k] for k in
                            ("equity", "cash", "open_positions", "drawdown_pct",
                             "day_pnl", "realized_pnl")},
                "milestone": perf["milestone"],
                "reconciliation": broker.reconcile(),
                "schema_version": db.schema_version(),
                "config_version": db.config_version()})
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
