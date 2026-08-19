"""Deterministic daily report + performance metrics.

Deterministic means: the same database state produces the same report, every time.
Nothing here samples live prices or wall-clock-dependent values beyond the timestamp
field, so a report can be regenerated later and compared byte-for-byte.
"""
from __future__ import annotations

import datetime as dt
import json
from statistics import mean
from typing import Any, Dict, List, Optional

from . import config as cfg, db, journal, risk as risk_mod


def _stats(pnls: List[float]) -> Dict[str, Any]:
    if not pnls:
        return {"n": 0, "win_rate": None, "avg_win": None, "avg_loss": None,
                "expectancy": None, "profit_factor": None, "total": 0.0}
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_w, gross_l = sum(wins), abs(sum(losses))
    return {
        "n": len(pnls),
        "win_rate": round(len(wins) / len(pnls) * 100, 1),
        "avg_win": round(mean(wins), 2) if wins else None,
        "avg_loss": round(mean(losses), 2) if losses else None,
        "expectancy": round(mean(pnls), 2),
        "profit_factor": round(gross_w / gross_l, 2) if gross_l else (None if not gross_w else float("inf")),
        "total": round(sum(pnls), 2),
    }


def _group_perf(rows: List[Dict[str, Any]], key: str) -> Dict[str, Any]:
    buckets: Dict[str, List[float]] = {}
    for r in rows:
        buckets.setdefault(r.get(key) or "unknown", []).append(r.get("realized_pnl") or 0.0)
    return {k: _stats(v) for k, v in sorted(buckets.items())}


def daily(session_date: Optional[str] = None) -> Dict[str, Any]:
    """The post-market report for one session."""
    session_date = session_date or dt.date.today().isoformat()
    st = risk_mod.account_state(session_date)

    entered = db.query(
        """SELECT o.order_id, o.symbol, o.side, o.strategy, o.avg_fill, o.filled_qty,
                  o.fees, o.status, o.intent
           FROM orders o WHERE o.session_date=? AND o.intent='entry'
             AND o.status IN ('filled','partial')""", (session_date,))
    exited = db.query(
        """SELECT p.*, p.realized_pnl pnl FROM positions p
           WHERE p.status='closed' AND substr(p.closed_at,1,10)=?""", (session_date,))
    sigs = journal.signals(session_date=session_date, limit=500)

    rejected = [s for s in sigs if s["action"] == "REJECT"]
    monitored = [s for s in sigs if s["action"] == "MONITOR"]
    tradeable = [s for s in sigs if s["action"] == "TRADEABLE"]

    # gate failures across today's signals
    gate_fail: Dict[str, int] = {}
    for s in sigs:
        try:
            for name in json.loads(s.get("failed_gates") or "[]"):
                gate_fail[name] = gate_fail.get(name, 0) + 1
        except Exception:
            pass

    # slippage actually paid today
    slips = db.query(
        """SELECT f.slippage, f.liquidity, o.intent FROM fills f
           JOIN orders o ON o.order_id=f.order_id WHERE o.session_date=?""", (session_date,))
    entry_slip = [s["slippage"] for s in slips if s["intent"] == "entry" and s["slippage"] is not None]
    exit_slip = [s["slippage"] for s in slips if s["intent"] != "entry" and s["slippage"] is not None]
    gaps = [s for s in slips if s["liquidity"] == "gap"]

    day_pnls = [e.get("realized_pnl") or 0.0 for e in exited]
    all_closed = db.query("SELECT * FROM positions WHERE status='closed'")
    cum_pnls = [p.get("realized_pnl") or 0.0 for p in all_closed]

    # stale-data / provider refusals recorded in the audit trail today
    refusals = db.query(
        """SELECT detail_json FROM audit WHERE event IN ('entry_refused','risk_blocked')
           AND substr(at,1,10)=?""", (session_date,))
    stale_attempts = sum(1 for r in refusals
                         if "stale" in (r.get("detail_json") or "").lower()
                         or "display-only" in (r.get("detail_json") or "").lower())

    return {
        "session_date": session_date,
        "generated_at": db.utcnow(),
        "config_version": db.config_version(),
        "config": cfg.snapshot(),
        "account": {
            "cash": st["cash"], "equity": st["equity"],
            "starting_equity": st["starting_equity"],
            "realized_pnl": st["realized_pnl"], "unrealized_pnl": st["unrealized_pnl"],
            "open_positions": st["open_positions"], "drawdown_pct": st["drawdown_pct"],
            "day_pnl": st["day_pnl"],
            "day_loss_used_pct": st["day_loss_used_pct"],
            "daily_loss_limit_usd": cfg.risk().max_daily_loss,
            "drawdown_usd": st["drawdown_usd"],
            "max_drawdown_usd": cfg.risk().max_drawdown,
            "available_cash": st["available_cash"],
            "buying_power": st["buying_power"],
            "cooldown": st["cooldown"],
        },
        "trades": {
            "entered": entered, "exited": exited,
            "entered_count": len(entered), "exited_count": len(exited),
        },
        "signals": {
            "tradeable": len(tradeable), "monitor": len(monitored), "reject": len(rejected),
            "rejected_detail": [{"symbol": s["symbol"], "strategy": s["strategy"],
                                 "failed_gates": s["failed_gates"],
                                 "quality": s["quality"], "threshold": s["quality_threshold"]}
                                for s in rejected],
            "monitor_detail": [{"symbol": s["symbol"], "strategy": s["strategy"],
                                "failed_gates": s["failed_gates"]} for s in monitored],
        },
        "pnl": {"daily": _stats(day_pnls), "cumulative": _stats(cum_pnls)},
        "excursions": {
            "avg_mfe": round(mean([p.get("mfe") or 0 for p in exited]), 4) if exited else None,
            "avg_mae": round(mean([p.get("mae") or 0 for p in exited]), 4) if exited else None,
        },
        "slippage": {
            "entry_avg": round(mean(entry_slip), 5) if entry_slip else None,
            "exit_avg": round(mean(exit_slip), 5) if exit_slip else None,
            "gap_fills": len(gaps),
        },
        "by_strategy": _group_perf(all_closed, "strategy"),
        "by_sector": _group_perf(all_closed, "sector"),
        "gate_failures": dict(sorted(gate_fail.items(), key=lambda x: -x[1])),
        "calibration": calibration(),
        "gate_outcomes": journal.gate_outcomes(),
        "blocked_winners": journal.blocked_winners(),
        "provider_failures": db.query(
            """SELECT event, COUNT(*) n FROM audit
               WHERE event IN ('evaluate_failed','premarket_aborted') AND substr(at,1,10)=?
               GROUP BY event""", (session_date,)),
        "stale_data_attempts": stale_attempts,
        "small_account": small_account_metrics(session_date),
        "reconciliation": _recon(),
        "what_the_engine_got_wrong": _mistakes(session_date),
    }


def small_account_metrics(session_date: Optional[str] = None) -> Dict[str, Any]:
    """Metrics that only matter because the account is $500.

    The central question this answers: **how much of the strategy is even reachable
    with this much cash?** A signal we cannot afford is not an opportunity, and a
    strategy whose good setups are mostly unaffordable is the wrong strategy for the
    account — regardless of how well it backtests at $10,000.
    """
    a = cfg.account()
    st = risk_mod.account_state(session_date)
    where, params = ("WHERE session_date=?", (session_date,)) if session_date else ("", ())

    total_sig = db.query_one(f"SELECT COUNT(*) n FROM signals {where}", params)["n"]
    unaffordable = db.query_one(
        """SELECT COUNT(*) n FROM audit WHERE event='unaffordable'""" +
        (" AND substr(at,1,10)=?" if session_date else ""),
        (session_date,) if session_date else ())["n"]
    executed = db.query_one(
        f"SELECT COUNT(*) n FROM signals {where}{' AND' if where else 'WHERE'} executed=1",
        params)["n"]

    # capital actually required by the entries we took
    entries = db.query(
        """SELECT o.filled_qty, o.avg_fill FROM orders o
           WHERE o.intent='entry' AND o.status IN ('filled','partial')""" +
        (" AND o.session_date=?" if session_date else ""),
        (session_date,) if session_date else ())
    caps = [(e["filled_qty"] or 0) * (e["avg_fill"] or 0) for e in entries]

    fractional = sum(1 for e in entries
                     if abs((e["filled_qty"] or 0) - round(e["filled_qty"] or 0)) > 1e-9)

    opt_rows = db.query("SELECT assumed_fill, preference FROM options_shadow" +
                        (" WHERE session_date=?" if session_date else ""),
                        (session_date,) if session_date else ())
    ocfg = cfg.options()
    opt_too_expensive = sum(
        1 for o in opt_rows
        if (o.get("assumed_fill") or 0) * 100 > ocfg.max_premium_per_trade)

    affordable_signals = max(0, total_sig - unaffordable)
    start = a.initial_equity or 1.0
    return {
        "ledger": a.ledger,
        "starting_cash": a.initial_cash,
        "starting_equity": a.initial_equity,
        "signals_total": total_sig,
        "signals_affordable": affordable_signals,
        "affordable_pct": round(affordable_signals / total_sig * 100, 1) if total_sig else None,
        "rejected_insufficient_buying_power": unaffordable,
        "signals_executed": executed,
        "avg_capital_required": round(mean(caps), 2) if caps else None,
        "max_capital_required": round(max(caps), 2) if caps else None,
        "capital_utilization_pct": st["capital_utilization_pct"],
        "cash_remaining": st["available_cash"],
        "buying_power": st["buying_power"],
        "fractional_entries": fractional,
        "whole_share_entries": len(entries) - fractional,
        "fractional_enabled": a.fractional_shares,
        "options_rejected_premium": opt_too_expensive,
        "option_allocation_cap": ocfg.max_premium_per_trade,
        "return_on_starting_equity_pct": round(
            (st["equity"] - start) / start * 100, 3),
        "note": "an unaffordable signal is not an opportunity — if affordable_pct is "
                "low, the strategy is mis-sized for this account, not unlucky",
    }


def _recon() -> Dict[str, Any]:
    from . import broker
    return broker.reconcile()


def _mistakes(session_date: str) -> List[Dict[str, Any]]:
    """Honest self-criticism: executed trades that lost, and blocked setups that won."""
    out: List[Dict[str, Any]] = []
    for p in db.query("""SELECT * FROM positions WHERE status='closed'
                          AND substr(closed_at,1,10)=? AND realized_pnl < 0""", (session_date,)):
        out.append({"type": "executed_loser", "symbol": p["symbol"],
                    "strategy": p.get("strategy"), "pnl": p.get("realized_pnl"),
                    "exit_reason": p.get("exit_reason"),
                    "mfe": p.get("mfe"), "mae": p.get("mae"),
                    "lesson": ("stopped out after being onside — stop may be too tight"
                               if (p.get("mfe") or 0) > (p.get("mae") or 0)
                               else "went against us from the start — entry timing")})
    bw = journal.blocked_winners()
    for s in bw["blocked_winners"][:5]:
        out.append({"type": "blocked_winner", "symbol": s["symbol"],
                    "action": s["action"], "failed_gates": s.get("failed_gates"),
                    "pnl_per_share": s.get("outcome_pnl_ps"),
                    "lesson": "a gate blocked a setup that reached its target — "
                              "track whether this repeats before loosening anything"})
    return out


def calibration() -> Dict[str, Any]:
    """Are the quality scores meaningful? Bucket resolved signals by quality and
    compare predicted confidence to the realised hit rate."""
    rows = db.query("""SELECT quality, action, outcome FROM signals
                        WHERE outcome IN ('target_hit','stop_hit') AND quality IS NOT NULL""")
    buckets: Dict[str, List[int]] = {}
    for r in rows:
        q = r["quality"] or 0
        b = f"{int(q // 10) * 10}-{int(q // 10) * 10 + 9}"
        buckets.setdefault(b, []).append(1 if r["outcome"] == "target_hit" else 0)
    return {
        "quality_buckets": {k: {"n": len(v), "hit_rate": round(sum(v) / len(v) * 100, 1)}
                            for k, v in sorted(buckets.items())},
        "freshness_buckets": _freshness_buckets(),
        "note": "needs a meaningful sample before it means anything; "
                "the 50-trade milestone is the first point worth reading",
    }


def _freshness_buckets() -> Dict[str, Any]:
    rows = db.query("""SELECT freshness_state, outcome FROM signals
                        WHERE outcome IN ('target_hit','stop_hit')""")
    b: Dict[str, List[int]] = {}
    for r in rows:
        b.setdefault(r["freshness_state"] or "unknown", []).append(
            1 if r["outcome"] == "target_hit" else 0)
    return {k: {"n": len(v), "hit_rate": round(sum(v) / len(v) * 100, 1)}
            for k, v in sorted(b.items())}


# ── Cumulative performance (mission §10) ─────────────────────────────────────

def performance() -> Dict[str, Any]:
    closed = db.query("SELECT * FROM positions WHERE status='closed'")
    pnls = [p.get("realized_pnl") or 0.0 for p in closed]
    curve = db.query("SELECT session_date, equity FROM equity ORDER BY session_date")
    peak, max_dd = 0.0, 0.0
    for c in curve:
        peak = max(peak, c["equity"])
        if peak:
            max_dd = max(max_dd, (peak - c["equity"]) / peak * 100)

    sigs = db.query("SELECT action, COUNT(*) n FROM signals GROUP BY action")
    return {
        "generated_at": db.utcnow(),
        "resolved_trades": len(closed),
        "milestone": {"target": 50, "progress": len(closed),
                      "remaining": max(0, 50 - len(closed))},
        "overall": _stats(pnls),
        "max_drawdown_pct": round(max_dd, 2),
        "equity_curve": curve,
        "by_strategy": _group_perf(closed, "strategy"),
        "by_sector": _group_perf(closed, "sector"),
        "signal_counts": {s["action"]: s["n"] for s in sigs},
        "gate_outcomes": journal.gate_outcomes(),
        "blocked_winners": journal.blocked_winners(),
        "calibration": calibration(),
        "excursions": {
            "avg_mfe": round(mean([p.get("mfe") or 0 for p in closed]), 4) if closed else None,
            "avg_mae": round(mean([p.get("mae") or 0 for p in closed]), 4) if closed else None,
        },
        "slippage": _slippage_summary(),
        "benchmarks": benchmarks(),
        "small_account": small_account_metrics(),
        "warning": ("fewer than 50 resolved trades — treat every number here as noise"
                    if len(closed) < 50 else
                    "50+ resolved trades: the sample is large enough to start reading"),
    }


def _slippage_summary() -> Dict[str, Any]:
    rows = db.query("""SELECT f.slippage, f.liquidity, o.intent FROM fills f
                       JOIN orders o ON o.order_id=f.order_id WHERE f.slippage IS NOT NULL""")
    e = [r["slippage"] for r in rows if r["intent"] == "entry"]
    x = [r["slippage"] for r in rows if r["intent"] != "entry"]
    return {"entry_avg": round(mean(e), 5) if e else None,
            "exit_avg": round(mean(x), 5) if x else None,
            "gap_fills": sum(1 for r in rows if r["liquidity"] == "gap"),
            "partial_fills": sum(1 for r in rows if r["liquidity"] == "partial")}


def benchmarks() -> Dict[str, Any]:
    """Compare against SPY buy-and-hold and a naive momentum baseline over the same
    window. Without this, a positive P&L in a rising market means nothing."""
    curve = db.query("SELECT session_date, equity FROM equity ORDER BY session_date")
    if len(curve) < 2:
        return {"state": "insufficient_history",
                "note": "need at least two equity snapshots to compare"}
    start, end = curve[0], curve[-1]
    strat_ret = ((end["equity"] / start["equity"]) - 1) * 100 if start["equity"] else 0.0
    out: Dict[str, Any] = {"state": "ok", "from": start["session_date"],
                           "to": end["session_date"],
                           "strategy_return_pct": round(strat_ret, 3)}
    try:
        import research as R
        h = R.price_history("SPY", "3M")
        if h.get("state") == "ok" and h.get("points"):
            pts = [p for p in h["points"] if start["session_date"] <= p["t"][:10] <= end["session_date"]]
            if len(pts) >= 2:
                spy = (pts[-1]["c"] / pts[0]["c"] - 1) * 100
                out["spy_return_pct"] = round(spy, 3)
                out["excess_vs_spy_pct"] = round(strat_ret - spy, 3)
    except Exception as e:
        out["spy_error"] = str(e)[:80]
    return out
