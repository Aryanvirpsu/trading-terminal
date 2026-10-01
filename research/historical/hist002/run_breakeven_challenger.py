"""HIST-002: apply the breakeven-after-+1R Challenger to every evaluation-phase closed position in
EXP-DD-001's complete, uncapped discovery baseline (exp_dd_001_no_drawdown_v2_complete) that reached +1R
per its own ledger MFE. For each such position, resolve the real bar-by-bar counterfactual path; for every
other position (never reached +1R), the variant is IDENTICAL to Champion's real outcome by construction
(the breakeven rule never engages). Builds a "shadow result" with realized_pnl replaced by the variant's
dollar P&L wherever it differs, then reuses analysis.py's own portfolio/R/drawdown/stability functions
for an apples-to-apples report against the real baseline.

Result (2026-10-01): COMPLETE -- NOT PROMOTED. See research/historical/reports/HIST_002_RESULTS.md.
The pickled EXP-DD-001 result this script reads is a large (~100MB+) run artifact, intentionally not
committed to the repo (same convention as the Full-stage dataset parquet files) -- pass its path via
HIST002_INPUT_PKL, or regenerate it with research/historical/hist001/baseline.py's
run_baseline(disable_drawdown_gate=True) over 2024-01-01..2026-03-10.
"""
import copy
import datetime as dt
import json
import os
import pickle
import sys

REPO_ROOT = os.environ["REPO_ROOT"]
sys.path.insert(0, REPO_ROOT)

from research.historical.clock import HistoricalClock
from research.historical.hist001.analysis import (
    concentration_analysis, drawdown_and_streaks, portfolio_metrics, r_multiple_metrics, stability_breakdown,
)
from research.historical.hist001.build_full_dataset import INTRADAY_DATASET_ID
from research.historical.hist002.breakeven_resolver import resolve_breakeven_after_plus1r
from research.historical.provider import HistoricalMarketProvider

RESULT_PATH = os.environ.get("HIST002_INPUT_PKL") or os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..", "exp_dd_001_v2_result.pkl")
with open(RESULT_PATH, "rb") as fh:
    baseline = pickle.load(fh)

eval_dates = set(baseline["evaluation_trading_days"])
end_clock = HistoricalClock(dt.datetime.fromisoformat(baseline["cycles"][-1]["et_time"]) + dt.timedelta(minutes=5))
provider = HistoricalMarketProvider(end_clock, [INTRADAY_DATASET_ID])

resolved_count = unresolved_count = never_reached_1r = 0
ambiguous_count = 0
detail = []
shadow_positions = copy.deepcopy(baseline["positions"])
by_id = {p["position_id"]: p for p in shadow_positions}

for p in baseline["positions"]:
    if p.get("status") != "closed":
        continue
    opened_date = str(p.get("opened_at") or "")[:10]
    if eval_dates and opened_date not in eval_dates:
        continue
    entry, stop = p.get("avg_entry"), p.get("stop")
    if entry is None or stop is None or entry == stop:
        continue
    r_per_share = abs(entry - stop)
    mfe_r = (p.get("mfe") or 0.0) / r_per_share if r_per_share else 0.0
    if mfe_r < 1.0:
        never_reached_1r += 1
        continue

    direction = "LONG" if (p.get("quantity") or 0) > 0 else "SHORT"   # this account never shorts; defensive only
    entry_time = dt.datetime.fromisoformat(p["opened_at"])
    target = p.get("target")
    if target is None:
        unresolved_count += 1
        continue

    r = resolve_breakeven_after_plus1r(provider, p["symbol"], direction, entry, stop, target, entry_time)
    if not r.resolved:
        unresolved_count += 1
        detail.append({"symbol": p["symbol"], "position_id": p["position_id"], "real_pnl": p["realized_pnl"],
                       "variant_exit_reason": r.exit_reason, "note": "unresolved -- real Champion P&L kept"})
        continue

    resolved_count += 1
    if r.ambiguous:
        ambiguous_count += 1

    variant_quantity = abs(p["quantity"])
    variant_dollar_pnl = round(r.variant_net_r * variant_quantity * r_per_share, 4)
    by_id[p["position_id"]]["realized_pnl"] = variant_dollar_pnl
    detail.append({"symbol": p["symbol"], "position_id": p["position_id"], "real_pnl": p["realized_pnl"],
                  "variant_pnl": variant_dollar_pnl, "variant_exit_reason": r.exit_reason,
                  "variant_net_r": r.variant_net_r, "ambiguous": r.ambiguous,
                  "ambiguous_note": r.ambiguous_note, "plus_1r_time": r.plus_1r_time})

print(f"closed evaluation-phase positions reaching +1R: {resolved_count + unresolved_count}", file=sys.stderr)
print(f"  resolved: {resolved_count} (ambiguous: {ambiguous_count})", file=sys.stderr)
print(f"  unresolved (kept real P&L): {unresolved_count}", file=sys.stderr)
print(f"  never reached +1R (unchanged by construction): {never_reached_1r}", file=sys.stderr)

shadow_result = dict(baseline)
shadow_result["positions"] = shadow_positions
shadow_result["account"] = dict(baseline["account"])
# portfolio_metrics() reads "ending_equity" directly from the account snapshot (not derived from net_pnl),
# which still reflects the REAL baseline's own ending equity unless corrected here -- would otherwise pair a
# stale ending_equity with the variant's own, different net_pnl in the printed report. drawdown_and_streaks()
# is unaffected (it walks starting_equity + the closed-trade P&L sequence directly, already self-consistent).
_variant_eval_closed = [p for p in shadow_positions if p.get("status") == "closed"
                       and str(p.get("opened_at") or "")[:10] in eval_dates]
_variant_net_pnl = sum((p.get("realized_pnl") or 0.0) for p in _variant_eval_closed)
shadow_result["account"]["equity"] = round(baseline["account"]["starting_equity"] + _variant_net_pnl, 2)

print("\n=== BASELINE (real Champion, uncapped) ===", file=sys.stderr)
print(json.dumps(portfolio_metrics(baseline), indent=2))
print(json.dumps(r_multiple_metrics(baseline), indent=2))
print(json.dumps(drawdown_and_streaks(baseline), indent=2))

print("\n=== HIST-002 VARIANT (breakeven after +1R) ===", file=sys.stderr)
print(json.dumps(portfolio_metrics(shadow_result), indent=2))
print(json.dumps(r_multiple_metrics(shadow_result), indent=2))
print(json.dumps(drawdown_and_streaks(shadow_result), indent=2))
print(json.dumps(stability_breakdown(shadow_result)["by_year"], indent=2))

OUT_PATH = os.path.join(os.path.dirname(__file__), "results", "hist002_breakeven_result.json")
with open(OUT_PATH, "w", encoding="utf-8") as fh:
    json.dump({"detail": detail, "resolved_count": resolved_count, "unresolved_count": unresolved_count,
              "never_reached_1r": never_reached_1r, "ambiguous_count": ambiguous_count,
              "baseline_portfolio": portfolio_metrics(baseline), "variant_portfolio": portfolio_metrics(shadow_result),
              "baseline_drawdown": drawdown_and_streaks(baseline), "variant_drawdown": drawdown_and_streaks(shadow_result),
              "baseline_r": r_multiple_metrics(baseline), "variant_r": r_multiple_metrics(shadow_result)},
             fh, indent=2, default=str)
print(f"\nfull detail written to {OUT_PATH}", file=sys.stderr)
print("HIST002_BREAKEVEN_DONE_FINAL", file=sys.stderr)
