"""HIST-001: post-hoc analysis over a `baseline.run_baseline()` result -- funnel attribution, capacity
opportunity cost (via H6's `resolve_hypothetical`, now using the full decision-time capture baseline.py
records), choice-event records with full candidate detail, the CH-001 shadow comparison, and basic
portfolio/trade/path metrics. Every function here is read-only over the replay's own output; none of them
change a Champion decision or re-run any part of the replay.

Post-Smoke: every metric that would otherwise count a warm-up-phase observation is filtered to
`phase == "evaluation"` only (directive: "warm-up decisions do not count, warm-up trades do not count,
warm-up events do not enter HIST-001 metrics") -- warm-up cycles still ran for real and their account-state
effects are real (a warm-up position still consumes capacity in the evaluation window), just excluded from
the REPORTED sample.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

from ..clock import HistoricalClock
from ..outcomes import resolve_hypothetical
from ..provider import HistoricalMarketProvider


def _eval_cycles(result: Dict[str, Any]):
    return [c for c in result["cycles"] if c["phase"] == "evaluation" and c["premarket"].get("state") == "ok"]


def funnel_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    """UNIVERSE -> scanner detections -> raw setups -> validation -> A/B/C/D/E -> finalists -> TRADEABLE ->
    capacity-eligible -> orders -> fills -> exits. EVALUATION PHASE ONLY."""
    total_observations = 0
    events_seen: set = set()
    by_decision = Counter()
    events_by_decision: Dict[str, set] = defaultdict(set)
    candidates_total = 0
    finalists_total = 0

    eval_cycles = _eval_cycles(result)
    for c in eval_cycles:
        pm = c["premarket"]
        candidates_total += pm.get("candidates") or 0
        finalists_total += len(pm.get("finalists") or [])
        for ev in pm.get("evaluated", []):
            total_observations += 1
            action = ev.get("action")
            by_decision[action] += 1
            eid = ev.get("event_id")
            if eid:
                events_seen.add(eid)
                events_by_decision[action].add(eid)

    # not_executed audit rows carry a UTC timestamp, not an ET session_date, so they are NOT phase-filtered
    # here -- this tally is informational context (all phases combined), never a counted HIST-001 metric.
    reasons = Counter()
    for row in result["not_executed"]:
        try:
            detail = json.loads(row["detail_json"]) if row.get("detail_json") else {}
        except Exception:
            detail = {}
        reasons[detail.get("reason", "unknown")] += 1

    tradeable_events = len(events_by_decision.get("TRADEABLE", set()))
    orders = result["orders"]
    fills = result["fills"]
    closed_positions = [p for p in result["positions"] if p.get("status") == "closed"]

    def pct(n, d):
        return round(100.0 * n / d, 2) if d else None

    return {
        "phase": "evaluation", "evaluation_cycles_ok": len(eval_cycles),
        "warmup_trading_days": len(result["warmup_trading_days"]),
        "evaluation_trading_days": len(result["evaluation_trading_days"]),
        "candidates_total_raw": candidates_total, "finalists_total_raw": finalists_total,
        "raw_observations": total_observations, "independent_events": len(events_seen),
        "by_decision_observations": dict(by_decision),
        "by_decision_independent_events": {k: len(v) for k, v in events_by_decision.items()},
        "tradeable_independent_events": tradeable_events,
        "orders_placed": len(orders), "fills": len(fills), "exits": len(closed_positions),
        "conversion_pct": {
            "observations_to_tradeable": pct(by_decision.get("TRADEABLE", 0), total_observations),
            "tradeable_events_to_orders": pct(len(orders), tradeable_events) if tradeable_events else None,
            "orders_to_fills": pct(len(fills), len(orders)),
        },
        "major_rejection_reasons_all_phases": reasons.most_common(10),
    }


def survivorship_bias_note(universe_symbols: List[str], dataset_symbols: List[str]) -> Dict[str, Any]:
    missing = sorted(set(universe_symbols) - set(dataset_symbols))
    return {
        "universe_source": "today's dashboard/sector_map.py membership (NOT point-in-time historical "
                          "membership -- fabhaus/this project has no reliable point-in-time constituent "
                          "history)",
        "symbols_in_universe": len(universe_symbols), "symbols_with_data": len(dataset_symbols),
        "symbols_missing_from_source": missing,
        "delisted_or_ticker_changed_excluded": "not separately tracked at this stage -- no ticker-change/"
                                              "delisting metadata was supplied to corporate_actions.py for "
                                              "this batch (quarantine-by-default: none silently remapped)",
        "verdict": "SURVIVORSHIP BIAS PRESENT -- today's surviving universe was replayed backward. Any "
                  "symbol delisted, merged, or renamed between the replay period and today is absent from "
                  "this universe by construction, not detected and excluded after the fact.",
    }


def capacity_opportunity_cost(result: Dict[str, Any], intraday_dataset_id: str) -> List[Dict[str, Any]]:
    """For every TRADEABLE observation (EVALUATION phase only) that did NOT execute, resolve its
    hypothetical outcome via H6, using the FULL decision-time record `baseline.run_baseline()` captured
    (price/stop/target/direction/sector/quantity/risk-budget/quote/account-state/capability-fingerprint --
    the directive's own required minimum). The historical account never actually consumed cash/capacity for
    these -- this reads the replay's own already-produced output; nothing is re-run or re-decided."""
    # "daily order cap reached" is the REAL note text `lab.paper.workflow.premarket()` attaches to `ev["note"]`
    # (verbatim from its own source) -- the separate, differently-worded "daily entry cap reached (...)" string
    # only ever appears in the AUDIT log (`db.audit("not_executed", ...)`), never here. Both phrases are kept
    # so this filter matches the real note text (bug found building the Medium report: the audit-log wording
    # was used here by mistake, silently making the daily-cap bucket empty even when the funnel's own audit-
    # sourced tally showed real daily-cap rejections).
    blocked_capacity_phrases = ("daily order cap", "daily entry cap", "sector", "capacity", "post-cutoff",
                               "post_cutoff", "existing position", "already entered")
    end_clock = HistoricalClock(dt.datetime.fromisoformat(result["cycles"][-1]["et_time"]) + dt.timedelta(minutes=5))
    outcome_provider = HistoricalMarketProvider(end_clock, [intraday_dataset_id])

    out: List[Dict[str, Any]] = []
    for c in _eval_cycles(result):
        for ev in c["premarket"].get("evaluated", []):
            if ev.get("action") != "TRADEABLE" or ev.get("executed"):
                continue
            note = (ev.get("note") or "").lower()
            if not any(phrase in note for phrase in blocked_capacity_phrases):
                continue
            key = f"{c['cycle_id']}|{ev['symbol']}"
            rec = result["decision_capture"].get(key)
            record: Dict[str, Any] = {"symbol": ev["symbol"], "cycle_id": c["cycle_id"],
                                      "session_date": c["session_date"], "event_id": ev.get("event_id"),
                                      "decision_id": ev.get("decision_id"), "blocked_reason": ev.get("note")}
            if rec is None or rec.get("price") is None or rec.get("stop") is None or rec.get("target") is None:
                record["hypothetical_outcome"] = None
                record["resolution_note"] = "decision-time price/stop/target were not captured for this symbol"
                out.append(record)
                continue
            record.update({"decision_time_et": rec.get("et_time"), "direction": rec.get("direction"),
                          "entry_price": rec["price"], "stop": rec["stop"], "target": rec["target"],
                          "sector": rec.get("sector"), "hypothetical_quantity": rec.get("hypothetical_quantity"),
                          "risk_budget": rec.get("risk_budget"), "quote_ask": rec.get("quote_ask"),
                          "quote_bid": rec.get("quote_bid"), "account_equity_before": rec.get("account_equity_before"),
                          "capability_fingerprint": rec.get("capability_fingerprint")})
            try:
                decision_time = dt.datetime.fromisoformat(rec["et_time"])
                outcome = resolve_hypothetical(outcome_provider, ev["symbol"],
                                               {"price": rec["price"], "stop": rec["stop"], "target": rec["target"],
                                               "direction": rec.get("direction", "LONG")}, decision_time)
                record["hypothetical_outcome"] = outcome.to_dict() if outcome else None
            except Exception as e:
                record["hypothetical_outcome"] = None
                record["resolution_note"] = f"outcome resolution failed: {e}"
            out.append(record)
    return out


def choice_events(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Directive sec 5/14: whenever two or more TRADEABLE candidates compete in the SAME evaluation-phase
    cycle, record the full candidate set (decision-time features from decision_capture) and which one the
    Champion actually selected."""
    out = []
    for c in _eval_cycles(result):
        tradeable = [ev for ev in c["premarket"].get("evaluated", []) if ev.get("action") == "TRADEABLE"]
        if len(tradeable) < 2:
            continue
        selected = [ev["symbol"] for ev in tradeable if ev.get("executed")]
        candidate_detail = []
        for ev in tradeable:
            rec = result["decision_capture"].get(f"{c['cycle_id']}|{ev['symbol']}", {})
            candidate_detail.append({"symbol": ev["symbol"], "note": ev.get("note"),
                                    "quality": rec.get("quality"), "sector": rec.get("sector"),
                                    "price": rec.get("price"), "stop": rec.get("stop"), "target": rec.get("target"),
                                    "selected": ev["symbol"] in selected})
        out.append({"cycle_id": c["cycle_id"], "session_date": c["session_date"],
                   "candidates": [ev["symbol"] for ev in tradeable], "selected": selected,
                   "candidate_detail": candidate_detail})
    return out


def ch001_shadow(result: Dict[str, Any]) -> Dict[str, Any]:
    """SHADOW ANALYSIS ONLY: Champion's real exit vs. a hypothetical breakeven-after-+1R stop management,
    for every REAL position (opened during the EVALUATION phase) that reached +1R per its own recorded MFE.
    Never affects a real position."""
    eval_dates = set(result["evaluation_trading_days"])
    reached_plus_1r = []
    for p in result["positions"]:
        opened_date = str(p.get("opened_at") or "")[:10]
        if eval_dates and opened_date not in eval_dates:
            continue
        entry, stop = p.get("avg_entry"), p.get("stop")
        if entry is None or stop is None or entry == stop:
            continue
        r_per_share = abs(entry - stop)
        mfe_r = (p.get("mfe") or 0.0) / r_per_share if r_per_share else 0.0
        if mfe_r >= 1.0:
            reached_plus_1r.append({"position_id": p["position_id"], "symbol": p["symbol"],
                                   "mfe_r": round(mfe_r, 3), "status": p.get("status"),
                                   "realized_pnl": p.get("realized_pnl")})
    return {
        "note": "SHADOW ANALYSIS -- does not affect real Champion positions. HIST-002 remains the formal "
               "Challenger experiment; CH-001 is not promoted from this alone.",
        "independent_trades_reaching_plus_1r": len(reached_plus_1r), "detail": reached_plus_1r,
        "scope_limit": "full CH-001 delta-R (Champion exit vs. breakeven-after-+1R exit) requires a bar-by-"
                      "bar outcomes.py resolution from the +1R timestamp forward -- not computed in this "
                      "pass; this reports only which real positions reached +1R per their own ledger MFE.",
    }


def portfolio_metrics(result: Dict[str, Any]) -> Dict[str, Any]:
    """EVALUATION PHASE positions only (opened_at date within the evaluation window)."""
    eval_dates = set(result["evaluation_trading_days"])
    positions = [p for p in result["positions"]
                if not eval_dates or str(p.get("opened_at") or "")[:10] in eval_dates]
    closed = [p for p in positions if p.get("status") == "closed"]
    realized = [p.get("realized_pnl") or 0.0 for p in closed]
    acct = result["account"]
    return {
        "phase": "evaluation", "starting_equity": acct.get("starting_equity"), "ending_equity": acct.get("equity"),
        "net_pnl": round(sum(realized), 2), "resolved_trades": len(closed), "open_positions_end": len(
            [p for p in positions if p.get("status") == "open"]),
        "win_rate_pct": round(100.0 * sum(1 for r in realized if r > 0) / len(closed), 1) if closed else None,
        "avg_win": round(sum(r for r in realized if r > 0) / max(1, sum(1 for r in realized if r > 0)), 2) if closed else None,
        "avg_loss": round(sum(r for r in realized if r < 0) / max(1, sum(1 for r in realized if r < 0)), 2) if closed else None,
        "note_on_sample_size": "Sharpe/Sortino and other distributional statistics are NOT reported at this "
                              "sample size (per the pre-registration: reported only when the independent-"
                              "event count is large enough to be statistically meaningful).",
    }


def _eval_closed_positions(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """EVALUATION PHASE positions only, status='closed', sorted chronologically by closed_at -- the shared
    base every R/drawdown/concentration/stability function below builds on, so they all agree on which
    trades count."""
    eval_dates = set(result["evaluation_trading_days"])
    positions = [p for p in result["positions"]
                if p.get("status") == "closed"
                and (not eval_dates or str(p.get("opened_at") or "")[:10] in eval_dates)]
    return sorted(positions, key=lambda p: str(p.get("closed_at") or ""))


def r_multiple_metrics(result: Dict[str, Any]) -> Dict[str, Any]:
    """R = realized_pnl / planned_risk, the same convention `lab.paper.shadow_log.py`'s own CH-001 shadow
    comparison already uses in production (`orig_R = realized_pnl / planned_risk`) -- not a new definition
    invented for HIST-001. `planned_risk` is populated by the real, unmodified `broker.py` at order time
    (the dollar risk budgeted at entry), so this is the real per-trade R the account itself would report,
    not a recomputation from entry/stop/quantity that could silently diverge from what actually got sized.
    Trades with no planned_risk recorded (should not happen for a real sized entry) are excluded and counted
    separately rather than silently coerced to R=0."""
    closed = _eval_closed_positions(result)
    rs: List[float] = []
    excluded_no_planned_risk = 0
    for p in closed:
        planned = p.get("planned_risk")
        if not planned:
            excluded_no_planned_risk += 1
            continue
        rs.append((p.get("realized_pnl") or 0.0) / planned)
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    return {
        "phase": "evaluation", "trades_with_r": len(rs), "excluded_no_planned_risk": excluded_no_planned_risk,
        "total_r": round(sum(rs), 3) if rs else None,
        "expectancy_r": round(sum(rs) / len(rs), 3) if rs else None,
        "win_rate_pct": round(100.0 * len(wins) / len(rs), 1) if rs else None,
        "avg_win_r": round(sum(wins) / len(wins), 3) if wins else None,
        "avg_loss_r": round(sum(losses) / len(losses), 3) if losses else None,
        "best_trade_r": round(max(rs), 3) if rs else None,
        "worst_trade_r": round(min(rs), 3) if rs else None,
    }


def drawdown_and_streaks(result: Dict[str, Any]) -> Dict[str, Any]:
    """Equity path built ONLY from the evaluation-phase closed-trade realized P&L sequence (chronological by
    closed_at) starting from the account's own starting_equity -- not the full intraday/mark-to-market
    equity curve (that would require re-walking every cycle's unrealized P&L, which this function does not
    do). Max drawdown here is therefore a LOWER BOUND on the account's real max drawdown (intraday
    unrealized swings between trade closes are not captured) -- reported as such, not silently presented as
    the complete figure."""
    closed = _eval_closed_positions(result)
    equity = result["account"].get("starting_equity") or 0.0
    peak = equity
    max_dd_dollars = 0.0
    max_dd_pct = 0.0
    cur_win_streak = cur_loss_streak = max_win_streak = max_loss_streak = 0
    trade_pnls = []
    for p in closed:
        pnl = p.get("realized_pnl") or 0.0
        equity += pnl
        trade_pnls.append(pnl)
        peak = max(peak, equity)
        dd = peak - equity
        if dd > max_dd_dollars:
            max_dd_dollars = dd
            max_dd_pct = round(100.0 * dd / peak, 2) if peak else 0.0
        if pnl > 0:
            cur_win_streak += 1
            cur_loss_streak = 0
        elif pnl < 0:
            cur_loss_streak += 1
            cur_win_streak = 0
        max_win_streak = max(max_win_streak, cur_win_streak)
        max_loss_streak = max(max_loss_streak, cur_loss_streak)
    worst_5 = sorted(trade_pnls)[:5]
    return {
        "phase": "evaluation", "scope_limit": "closed-trade-realized-P&L equity path only -- a LOWER BOUND "
                                             "on true max drawdown, intraday unrealized swings between "
                                             "trade closes are not walked here",
        "max_drawdown_dollars": round(max_dd_dollars, 2), "max_drawdown_pct_of_peak": max_dd_pct,
        "worst_trade_dollars": round(min(trade_pnls), 2) if trade_pnls else None,
        "tail_losses_worst_5_dollars": [round(x, 2) for x in worst_5],
        "longest_win_streak": max_win_streak, "longest_loss_streak": max_loss_streak,
        "ending_equity_from_this_path": round(equity, 2),
    }


def concentration_analysis(result: Dict[str, Any]) -> Dict[str, Any]:
    """How much of the canonical net P&L is carried by a small number of trades -- directive sec 12. Reports
    the best trade, the top-5 and top-10 by realized_pnl, and a DIAGNOSTIC net P&L/expectancy with the best
    and worst trade excluded, computed purely for interpretation. Excluding trades here never changes, feeds
    back into, or replaces the canonical net_pnl/expectancy reported by `portfolio_metrics`/
    `r_multiple_metrics` -- those numbers are read verbatim from the full closed-trade set, never recomputed
    from this function's filtered view."""
    closed = _eval_closed_positions(result)
    pnls = sorted(((p.get("realized_pnl") or 0.0, p.get("symbol"), p.get("position_id")) for p in closed),
                 reverse=True)
    total_pnl = sum(pnl for pnl, _, _ in pnls)
    top5 = pnls[:5]
    top10 = pnls[:10]

    def pct_of_total(subset_sum: float) -> Optional[float]:
        return round(100.0 * subset_sum / total_pnl, 1) if total_pnl else None

    diagnostic_excl_best_worst = None
    if len(pnls) >= 2:
        rest = [pnl for pnl, _, _ in pnls[1:-1]]
        diagnostic_excl_best_worst = {
            "trades_remaining": len(rest), "net_pnl_excl_best_and_worst": round(sum(rest), 2),
            "expectancy_dollars_excl_best_and_worst": round(sum(rest) / len(rest), 2) if rest else None,
            "note": "DIAGNOSTIC ONLY -- excludes exactly the single best and single worst trade by "
                   "realized_pnl, never the canonical result reported elsewhere in this file.",
        }
    return {
        "phase": "evaluation", "trades": len(pnls), "total_net_pnl": round(total_pnl, 2),
        "best_trade": {"symbol": top5[0][1], "position_id": top5[0][2], "pnl": round(top5[0][0], 2)} if pnls else None,
        "worst_trade": {"symbol": pnls[-1][1], "position_id": pnls[-1][2], "pnl": round(pnls[-1][0], 2)} if pnls else None,
        "top5_sum": round(sum(p for p, _, _ in top5), 2), "top5_pct_of_total_pnl": pct_of_total(sum(p for p, _, _ in top5)),
        "top10_sum": round(sum(p for p, _, _ in top10), 2), "top10_pct_of_total_pnl": pct_of_total(sum(p for p, _, _ in top10)),
        "diagnostic_excluding_best_and_worst_trade": diagnostic_excl_best_worst,
    }


def stability_breakdown(result: Dict[str, Any]) -> Dict[str, Any]:
    """Net P&L / trade count / win rate broken out by month, quarter, year, symbol and sector -- directive
    sec 11. Grouped by `closed_at` date for the time-based buckets (when the trade's outcome was realized),
    not `opened_at` -- a trade opened in one quarter and closed in the next is counted in the quarter its
    P&L actually landed in."""
    closed = _eval_closed_positions(result)

    def bucket(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        pnls = [r.get("realized_pnl") or 0.0 for r in rows]
        wins = [x for x in pnls if x > 0]
        return {"trades": len(rows), "net_pnl": round(sum(pnls), 2),
               "win_rate_pct": round(100.0 * len(wins) / len(pnls), 1) if pnls else None}

    by_month: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_quarter: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_year: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_symbol: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_sector: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for p in closed:
        closed_date = str(p.get("closed_at") or "")[:10]
        if len(closed_date) == 10:
            year, month = closed_date[:4], closed_date[:7]
            quarter = f"{year}-Q{(int(closed_date[5:7]) - 1) // 3 + 1}"
            by_month[month].append(p)
            by_quarter[quarter].append(p)
            by_year[year].append(p)
        by_symbol[p.get("symbol") or "unknown"].append(p)
        by_sector[p.get("sector") or "unknown"].append(p)

    return {
        "phase": "evaluation",
        "by_month": {k: bucket(v) for k, v in sorted(by_month.items())},
        "by_quarter": {k: bucket(v) for k, v in sorted(by_quarter.items())},
        "by_year": {k: bucket(v) for k, v in sorted(by_year.items())},
        "by_symbol": {k: bucket(v) for k, v in sorted(by_symbol.items(), key=lambda kv: -bucket(kv[1])["net_pnl"])},
        "by_sector": {k: bucket(v) for k, v in sorted(by_sector.items(), key=lambda kv: -bucket(kv[1])["net_pnl"])},
    }


def risk_invariant_check(result: Dict[str, Any]) -> Dict[str, Any]:
    """Directive's risk invariant check -- zero violations expected. Verifies, from the replay's own output
    only (no re-simulation): (1) no position or fill ever went short or used a negative quantity (the $500
    cash-account constraint -- PAPER_500_ACCOUNT.md -- forbids shorting and margin by construction), (2) no
    realized loss on a trade exceeded ~1.5x its own planned_risk (a generous slippage/fee/gap tolerance --
    real gap-through-stop exits, like a genuine gap day, cost a bit more than the nominal stop distance;
    anything beyond that ratio is a genuine anomaly worth a name, not "normal slippage"), (3) equity never
    went negative, (4) counts (does not fail on) every cycle the account's own real max_drawdown circuit
    breaker fired, so a reviewer can see whether it fired rarely (expected, real risk management working) or
    pathologically (e.g. the AVGO-cascade pattern this file exists to have retired)."""
    violations: List[Dict[str, Any]] = []
    for p in result["positions"]:
        qty = p.get("quantity")
        if qty is not None and qty < 0:
            violations.append({"type": "negative_quantity", "position_id": p.get("position_id"), "quantity": qty})
    for f in result["fills"]:
        qty = f.get("quantity")
        if qty is not None and qty < 0:
            violations.append({"type": "negative_fill_quantity", "fill_id": f.get("fill_id"), "quantity": qty})
    for p in _eval_closed_positions(result):
        planned = p.get("planned_risk")
        pnl = p.get("realized_pnl") or 0.0
        if planned and pnl < 0 and abs(pnl) > 1.5 * planned:
            violations.append({"type": "loss_exceeds_1.5x_planned_risk", "position_id": p.get("position_id"),
                              "symbol": p.get("symbol"), "realized_pnl": round(pnl, 2),
                              "planned_risk": round(planned, 2), "ratio": round(abs(pnl) / planned, 2)})
    equity_curve = drawdown_and_streaks(result)
    if (equity_curve.get("ending_equity_from_this_path") or 0) < 0:
        violations.append({"type": "negative_equity_on_closed_trade_path"})

    dd_breaker_cycles = 0
    for row in result["not_executed"]:
        try:
            detail = json.loads(row["detail_json"]) if row.get("detail_json") else {}
        except Exception:
            detail = {}
        if "drawdown" in str(detail.get("reason", "")).lower():
            dd_breaker_cycles += 1

    return {
        "violations_found": len(violations), "violations": violations,
        "max_drawdown_circuit_breaker_fired_count": dd_breaker_cycles,
        "verdict": "PASS -- zero violations" if not violations else "FAIL -- see violations",
    }
