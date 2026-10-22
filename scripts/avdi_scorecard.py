"""AVDI profit scorecard: the numbers that matter, from the paper ledger, read-only.

    python -I scripts/avdi_scorecard.py --ledger <robinhood_500_baseline.db | mirror dir> [--spy spy_daily.json] [--fetch-spy] [--out scorecard.json] [--md scorecard.md]

Reports money first: net P&L (realized and unrealized separately), profit factor, expectancy, payoff ratio, win rate, maximum
drawdown, trade frequency, capital efficiency and the benchmark, then attributes every closed trade (strategy, sector, exit
reason, holding time, R).  Everything is computed from the ledger's own rows with SQLite opened mode=ro&immutable=1; it imports
nothing from lab/, reads no signal/candidate OUTCOME column, and never writes to the ledger.

Versions are never pooled in the headline: the per-engine-version table is the one to read (PAPER_EVIDENCE_VERSIONS.md).  A pooled
line is printed only for reconciliation with the host's totals.  Costs are the MODELED fill costs already inside the recorded fill
prices; fills the ledger itself audited as phantom (exit_without_position) are excluded, because a sale that never happened has no
real slippage.  An honest "no edge shown yet" is a valid result: the scorecard states the sample size next to every ratio.
"""
import argparse, datetime as dt, json, math, sqlite3, statistics, sys, urllib.request
from pathlib import Path

INITIAL_CASH = 500.0
# Same definition as lab/paper/db.py PHANTOM_FILLS_SQL (fix branch); keep in sync.
PHANTOM_FILLS_SQL = """
SELECT f.fill_id FROM fills f JOIN orders o ON o.order_id = f.order_id
WHERE o.intent != 'entry'
  AND EXISTS (SELECT 1 FROM audit a WHERE a.entity='order' AND a.entity_id=f.order_id AND a.event='exit_without_position' AND a.at >= f.filled_at)
  AND (SELECT COUNT(*) FROM audit a WHERE a.entity='order' AND a.entity_id=f.order_id AND a.event='exit_without_position')
    = (SELECT COUNT(*) FROM fills f2 WHERE f2.order_id=f.order_id)
  AND NOT EXISTS (SELECT 1 FROM positions p WHERE p.symbol=o.symbol AND p.opened_at <= f.filled_at AND (p.closed_at IS NULL OR f.filled_at <= p.closed_at))"""


def ro(p):
    c = sqlite3.connect(f"file:{Path(p).as_posix()}?mode=ro&immutable=1", uri=True)
    c.row_factory = sqlite3.Row
    return c


def t(x):
    return dt.datetime.fromisoformat(x)


def fetch_spy(path, start="2026-09-08", end="2026-10-30"):
    p1 = int(dt.datetime.fromisoformat(start).replace(tzinfo=dt.timezone.utc).timestamp()); p2 = int(dt.datetime.fromisoformat(end).replace(tzinfo=dt.timezone.utc).timestamp())
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/SPY?period1={p1}&period2={p2}&interval=1d"
    r = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20))["chart"]["result"][0]
    et = dt.timezone(dt.timedelta(hours=-4)); q = r["indicators"]["quote"][0]
    rows = [{"date": dt.datetime.fromtimestamp(ts, dt.timezone.utc).astimezone(et).strftime("%Y-%m-%d"), "close": c} for ts, c in zip(r["timestamp"], q["close"]) if c]
    Path(path).write_text(json.dumps({"source": "Yahoo chart API, unadjusted daily close", "fetched_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "rows": rows}, indent=1) + "\n", newline="\n")


def block(trades):
    """trades: closed positions with pnl (net, $) and r."""
    n = len(trades)
    if not n:
        return {"closed_trades": 0}
    p = [x["pnl"] for x in trades]; w = [x for x in p if x > 0]; l = [x for x in p if x < 0]
    gp, gl = sum(w), -sum(l)
    rs = [x["r"] for x in trades if x["r"] is not None]
    return {"closed_trades": n, "wins": len(w), "losses": len(l), "win_rate": round(len(w) / n, 3),
            "net_realized_pnl": round(sum(p), 2), "gross_profit": round(gp, 2), "gross_loss": round(gl, 2),
            "profit_factor": round(gp / gl, 3) if gl else None,
            "expectancy_usd_per_trade": round(sum(p) / n, 3), "expectancy_R_per_trade": round(sum(rs) / len(rs), 3) if rs else None,
            "avg_win": round(gp / len(w), 2) if w else None, "avg_loss": round(-gl / len(l), 2) if l else None,
            "payoff_ratio": round((gp / len(w)) / (gl / len(l)), 2) if w and l else None,
            "worst_trade": round(min(p), 2), "best_trade": round(max(p), 2),
            "breakeven_win_rate_at_this_payoff": round(1 / (1 + (gp / len(w)) / (gl / len(l))), 3) if w and l else None,
            "sample_note": "n=%d: far below the 50-trade review checkpoint; ratios are descriptive, not estimates of an edge" % n}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")      # Windows consoles default to cp1252
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", required=True); ap.add_argument("--spy"); ap.add_argument("--fetch-spy", action="store_true")
    ap.add_argument("--out"); ap.add_argument("--md")
    a = ap.parse_args()
    lp = Path(a.ledger).expanduser()
    mir = lp if lp.is_dir() else lp.parent
    db = lp / "robinhood_500_baseline.db" if lp.is_dir() else lp
    man = json.loads((mir / "MANIFEST.json").read_text()) if (mir / "MANIFEST.json").exists() else {}
    L = ro(db)
    pos = [dict(r) for r in L.execute("SELECT p.*, s.engine_version ev FROM positions p LEFT JOIN signals s ON s.signal_id=p.signal_id ORDER BY p.opened_at")]
    eq = [dict(r) for r in L.execute("SELECT * FROM equity ORDER BY recorded_at")]
    phantom = {r[0] for r in L.execute(PHANTOM_FILLS_SQL)}
    fills = [dict(r) for r in L.execute("SELECT f.*, o.symbol, o.intent, o.signal_id, o.side FROM fills f JOIN orders o ON o.order_id=f.order_id ORDER BY f.filled_at")]
    audit_cnt = {r["event"]: r["n"] for r in L.execute("SELECT event, COUNT(*) n FROM audit WHERE event IN ('entries_disabled','risk_blocked','entry_blocked','not_executed') GROUP BY event")}
    disabled_by_day = {r["d"]: r["n"] for r in L.execute("SELECT substr(at,1,10) d, COUNT(*) n FROM audit WHERE event='entries_disabled' GROUP BY 1")}
    last = eq[-1]

    closed = []
    for p in pos:
        if p["status"] != "closed":
            continue
        days = (t(p["closed_at"]) - t(p["opened_at"])).total_seconds() / 86400
        closed.append({"symbol": p["symbol"], "strategy": p["strategy"], "sector": p["sector"], "engine": p["ev"], "exit_reason": p["exit_reason"],
                       "opened": p["opened_at"][:16], "closed": p["closed_at"][:16], "hold_days": round(days, 1), "pnl": round(p["realized_pnl"], 2),
                       "r": round(p["realized_pnl"] / p["planned_risk"], 2) if p["planned_risk"] else None,
                       "notional": round(p["quantity"] * p["avg_entry"], 2)})
    open_pos = [p for p in pos if p["status"] == "open"]

    # ---- money ----
    realized = round(sum(c["pnl"] for c in closed), 2)
    out = {"ledger": str(db.name), "backup": mir.name, "backup_created_utc": man.get("created_iso"), "runtime_version": man.get("runtime_version"),
           "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "evidence_through_session": last["session_date"]}
    out["money"] = {"start_equity": INITIAL_CASH, "equity": last["equity"], "net_total_pnl": round(last["equity"] - INITIAL_CASH, 2),
                    "realized_pnl": realized, "unrealized_pnl_snapshot": last["unrealized_pnl"],
                    "return_pct": round((last["equity"] / INITIAL_CASH - 1) * 100, 3), "open_positions": [(p["symbol"], p["ev"]) for p in open_pos]}
    out["by_engine_version"] = {ev: block([c for c in closed if c["engine"] == ev]) for ev in sorted({c["engine"] for c in closed})}
    out["pooled_for_reconciliation_only"] = block(closed)
    for k in ("strategy", "sector", "exit_reason"):
        out["attribution_by_" + k] = {key: {"trades": len(g), "net_pnl": round(sum(x["pnl"] for x in g), 2), "wins": sum(1 for x in g if x["pnl"] > 0)}
                                      for key in sorted({c[k] or "?" for c in closed}) for g in [[c for c in closed if (c[k] or "?") == key]]}
    out["closed_trades"] = closed

    # ---- drawdown, frequency, capital efficiency (one snapshot per session) ----
    curve = [(e["session_date"], e["equity"]) for e in eq]
    peak, mdd, mdd_usd, w = -1e18, 0.0, 0.0, None
    for d, e in curve:
        peak = max(peak, e)
        if (peak - e) / peak * 100 > mdd:
            mdd, mdd_usd, w = (peak - e) / peak * 100, peak - e, d
    sessions = len(eq) - 1
    entries = [f for f in fills if f["intent"] == "entry"]
    avg_inv = statistics.mean(e["positions_value"] for e in eq)
    exposure_days = sum(e["positions_value"] for e in eq[1:])
    out["risk_and_speed"] = {"max_drawdown_pct": round(mdd, 3), "max_drawdown_usd": round(mdd_usd, 2), "trough_session": w,
                             "drawdown_note": "peak-to-trough on one snapshot per session; intraday drawdown is not recorded",
                             "sessions": sessions, "entries": len(entries), "entries_per_session": round(len(entries) / sessions, 3) if sessions else None,
                             "avg_capital_deployed_usd": round(avg_inv, 2), "avg_capital_deployed_pct": round(avg_inv / statistics.mean(e["equity"] for e in eq) * 100, 1),
                             "net_pnl_per_$100_deployed_session": round((last["equity"] - INITIAL_CASH) / exposure_days * 100, 4) if exposure_days else None,
                             "entries_disabled_events": audit_cnt.get("entries_disabled", 0), "entries_disabled_by_day": disabled_by_day,
                             "risk_blocked_events": audit_cnt.get("risk_blocked", 0)}

    # ---- costs (modeled, inside the fill prices) ----
    def cost(fs):
        return {"fills": len(fs), "slippage_usd": round(sum((f["slippage"] or 0) * f["quantity"] for f in fs), 3),
                "fees_usd": round(sum(f["fees"] or 0 for f in fs), 3),
                "avg_slippage_bps": round(statistics.mean((f["slippage"] or 0) / f["reference"] * 1e4 for f in fs if f["reference"]), 2) if fs else None}
    real = [f for f in fills if f["fill_id"] not in phantom]
    out["costs_modeled"] = {"entries": cost([f for f in real if f["intent"] == "entry"]), "exits": cost([f for f in real if f["intent"] != "entry"]),
                            "phantom_fills_excluded": len(phantom),
                            "note": "modeled spread/slippage, not observed vendor books; Robinhood commission $0; SEC/TAF fees not modeled"}

    # ---- benchmark ----
    spy_path = a.spy
    if a.fetch_spy:
        spy_path = spy_path or str(Path(a.out).with_name("spy_daily.json") if a.out else "spy_daily.json")
        fetch_spy(spy_path)
    if spy_path and Path(spy_path).exists():
        spy = {r["date"]: r["close"] for r in json.loads(Path(spy_path).read_text())["rows"]}
        s0, s1 = eq[0]["session_date"], last["session_date"]
        if s0 in spy and s1 in spy:
            sr = (spy[s1] / spy[s0] - 1) * 100
            out["benchmark"] = {"window": [s0, s1], "spy_return_pct": round(sr, 3), "strategy_return_pct": out["money"]["return_pct"],
                                "excess_pct_points": round(out["money"]["return_pct"] - sr, 3),
                                "caveat": "not exposure-matched; the account averaged %.0f%% invested" % out["risk_and_speed"]["avg_capital_deployed_pct"]}
        else:
            out["benchmark"] = {"state": "unavailable", "reason": "SPY file lacks %s/%s" % (s0, s1)}

    # ---- daily tracker ----
    daily, prev = [], INITIAL_CASH
    for e in eq:
        ex = [c for c in closed if c["closed"][:10] <= e["session_date"] and c["closed"][:10] > (daily[-1]["session"] if daily else "0")]
        daily.append({"session": e["session_date"], "equity": e["equity"], "day_pnl": round(e["equity"] - prev, 2), "cash": e["cash"],
                      "positions_value": e["positions_value"], "realized_cum": e["realized_pnl"], "exits_today": [(c["symbol"], c["pnl"]) for c in ex]})
        prev = e["equity"]
    out["daily"] = daily
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + "\n", newline="\n")
    if a.md:
        Path(a.md).write_text(render(out), encoding="utf-8", newline="\n")
    print(render(out))


def render(o):
    m, r = o["money"], o["risk_and_speed"]
    s = [f"# AVDI scorecard — evidence through {o['evidence_through_session']} (backup {o['backup']}, generated {o['generated_utc'][:16]}Z)", "",
         f"**Net P&L ${m['net_total_pnl']:+.2f}** ({m['return_pct']:+.2f}% on ${m['start_equity']:.0f}): realized ${m['realized_pnl']:+.2f}, unrealized ${m['unrealized_pnl_snapshot']:+.2f}. "
         f"Max drawdown ${r['max_drawdown_usd']:.2f} ({r['max_drawdown_pct']:.2f}%). Open: {m['open_positions']}", ""]
    if "benchmark" in o and "spy_return_pct" in o["benchmark"]:
        b = o["benchmark"]; s.append(f"**vs SPY {b['window'][0]}→{b['window'][1]}:** strategy {b['strategy_return_pct']:+.2f}% vs SPY {b['spy_return_pct']:+.2f}% (excess {b['excess_pct_points']:+.2f} pts; {b['caveat']})\n")
    s += ["| engine | trades | win rate | net $ | profit factor | expectancy $/trade | expectancy R | avg win | avg loss | payoff |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for ev, b in list(o["by_engine_version"].items()) + [("POOLED (reconcile only)", o["pooled_for_reconciliation_only"])]:
        if b.get("closed_trades"):
            s.append(f"| {ev} | {b['closed_trades']} | {b['win_rate']:.0%} | {b['net_realized_pnl']:+.2f} | {b['profit_factor']} | {b['expectancy_usd_per_trade']:+.2f} | {b['expectancy_R_per_trade']} | {b['avg_win']} | {b['avg_loss']} | {b['payoff_ratio']} |")
    s += ["", f"Frequency: {r['entries']} entries in {r['sessions']} sessions ({r['entries_per_session']}/session); capital deployed on average ${r['avg_capital_deployed_usd']} ({r['avg_capital_deployed_pct']}%); "
          f"entries disabled by the ledger check {r['entries_disabled_events']}× ({r['entries_disabled_by_day']}).",
          f"Modeled costs: entries {o['costs_modeled']['entries']['avg_slippage_bps']} bps, exits {o['costs_modeled']['exits']['avg_slippage_bps']} bps, fees $0; {o['costs_modeled']['phantom_fills_excluded']} phantom fills excluded.", "",
          "| closed trade | strategy | sector | exit | hold d | net $ | R |", "|---|---|---|---|---:|---:|---:|"]
    for c in o["closed_trades"]:
        s.append(f"| {c['symbol']} ({c['engine'].split('-')[-1]}) | {c['strategy']} | {c['sector']} | {c['exit_reason']} | {c['hold_days']} | {c['pnl']:+.2f} | {c['r']} |")
    return "\n".join(s) + "\n"


if __name__ == "__main__":
    main()
