"""Journal Lab — learn from the account's OWN trades.

Factors your real/paper trade history into the system: computes expectancy,
win-rate, profit factor, and breaks P&L down by setup_type, instrument, and
direction so the engine knows what's actually working for THIS account (not a
generic backtest). Feeds the self-evolving loop — segments that lose real money
get down-weighted; segments that win get promoted.

Headless: `python lab/journal_lab.py`  → prints report + writes journal_lab.json
"""
from __future__ import annotations
import json, os, sys
from collections import defaultdict
from statistics import mean

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from tradingview_mcp.core import portfolio

DATA_DIR = os.path.expanduser("~/.tradingview_mcp_data")
OUT = os.path.join(DATA_DIR, "journal_lab.json")
ACCOUNT = "strategy-500"


def _stats(pnls):
    if not pnls:
        return {"n": 0}
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_w = sum(wins); gross_l = abs(sum(losses))
    return {
        "n": len(pnls),
        "win_rate_pct": round(len(wins) / len(pnls) * 100, 1),
        "expectancy_usd": round(mean(pnls), 2),          # avg $ per trade — the number that matters
        "avg_win": round(mean(wins), 2) if wins else 0.0,
        "avg_loss": round(mean(losses), 2) if losses else 0.0,
        "profit_factor": round(gross_w / gross_l, 2) if gross_l else None,
        "total_pnl": round(sum(pnls), 2),
    }


def analyze(account: str = ACCOUNT) -> dict:
    j = portfolio.get_journal(account)
    closed = [e for e in j.get("entries", []) if e.get("status") == "closed"]
    by_setup, by_instr, by_opt = defaultdict(list), defaultdict(list), defaultdict(list)
    for e in closed:
        p = e.get("outcome_pnl") or 0.0
        by_setup[e.get("setup_type") or "?"].append(p)
        by_instr[e.get("instrument_type") or "?"].append(p)
        if e.get("instrument_type") == "OPTION":
            by_opt[e.get("option_type") or "?"].append(p)

    overall = _stats([e.get("outcome_pnl") or 0.0 for e in closed])
    seg = {k: _stats(v) for k, v in by_setup.items()}
    instr = {k: _stats(v) for k, v in by_instr.items()}
    opt = {k: _stats(v) for k, v in by_opt.items()}

    # What's working / what's not — ranked by expectancy (min 2 trades to matter)
    ranked = sorted(
        [(k, s) for k, s in {**{f"setup:{k}": v for k, v in seg.items()},
                             **{f"instr:{k}": v for k, v in instr.items()}}.items()
         if s.get("n", 0) >= 2],
        key=lambda kv: kv[1]["expectancy_usd"], reverse=True,
    )
    lessons = []
    for name, s in ranked:
        verdict = "KEEP/scale" if s["expectancy_usd"] > 0 else "cut/avoid"
        lessons.append(f"{name}: expectancy ${s['expectancy_usd']}/trade, "
                       f"win {s['win_rate_pct']}%, PF {s['profit_factor']} → {verdict}")

    return {
        "account": account,
        "closed_trades": len(closed),
        "overall": overall,
        "by_setup": seg,
        "by_instrument": instr,
        "by_option_type": opt,
        "lessons_ranked": lessons,
        "note": "Expectancy = avg $/trade; it's the single most important number. "
                "Positive-expectancy segments get promoted in the self-evolving loop.",
    }


def setup_edge(account: str = ACCOUNT) -> dict:
    """Map setup_type -> realized expectancy ($/trade) from closed trades, for
    setups with >=2 samples. Used to weight the live scanner toward what actually
    works for THIS account. Empty until enough trades exist."""
    r = analyze(account)
    return {k: s.get("expectancy_usd", 0.0)
            for k, s in r.get("by_setup", {}).items() if s.get("n", 0) >= 2}


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    r = analyze()
    os.makedirs(DATA_DIR, exist_ok=True)
    json.dump(r, open(OUT, "w", encoding="utf-8"), indent=2, default=str)
    o = r["overall"]
    print(f"=== JOURNAL LAB · {r['account']} · {r['closed_trades']} closed ===")
    print(f"Overall: expectancy ${o.get('expectancy_usd')}/trade | win {o.get('win_rate_pct')}% | "
          f"PF {o.get('profit_factor')} | total ${o.get('total_pnl')}")
    print("\nWhat's working (ranked by expectancy):")
    for l in r["lessons_ranked"]:
        print(f"  • {l}")
    print(f"\n[written] {OUT}")
