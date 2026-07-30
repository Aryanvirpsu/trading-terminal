"""Strategy Lab — the self-evolving core.

Runs every candidate strategy over a small-account universe through
walk_forward_backtest (train/test splits), keeps ONLY those that survive
out-of-sample with a robustness score (test/train) above threshold, ranks the
survivors, and writes champions.json. Re-running it = evolution: each generation
re-validates on fresh data, promotes newly-robust strategies, and demotes ones
that have decayed. Overfitting is the enemy, so the gate is OUT-OF-SAMPLE
robustness, never in-sample return.

Headless: `python lab/strategy_lab.py [--quick]`  → writes champions.json
"""
from __future__ import annotations
import json, os, sys, datetime as dt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from tradingview_mcp.core.services import backtest_service as bt

DATA_DIR = os.path.expanduser("~/.tradingview_mcp_data")
CHAMPIONS = os.path.join(DATA_DIR, "champions.json")

# Small-account liquid universe: index ETFs + affordable optionable names.
UNIVERSE = ["SPY", "QQQ", "AAPL", "BAC", "F", "SOFI", "INTC", "T", "NVDA", "AMD"]
# Walk-forward-safe strategies (exclude SMA200-warmup ones the WF engine rejects).
STRATEGIES = ["rsi", "bollinger", "macd", "ema_cross", "supertrend",
              "donchian", "rsi_pullback", "keltner_breakout"]

# Guardrails
MIN_ROBUSTNESS = 0.5   # test/train >= 0.5 (MODERATE or better) — else likely overfit
MIN_TRADES = 8         # need enough trades for the result to mean anything


def _extract(res: dict) -> dict:
    """Pull the fields we care about from walk_forward_backtest output."""
    def num(k, default=0.0):
        v = res.get(k)
        return v if isinstance(v, (int, float)) else default
    return {
        "robustness": num("robustness_score", None),
        "oos_return": num("oos_total_return_pct", num("avg_test_return_pct", 0.0)),
        "trades": int(num("oos_total_trades", 0)),
        "label": str(res.get("verdict") or ""),
    }


def evolve(quick: bool = False) -> dict:
    universe = UNIVERSE[:3] if quick else UNIVERSE
    strategies = STRATEGIES[:3] if quick else STRATEGIES
    results = []
    for sym in universe:
        for strat in strategies:
            try:
                res = bt.walk_forward_backtest(sym, strat, period="2y", n_splits=3,
                                               initial_capital=500.0)
            except Exception as e:
                results.append({"symbol": sym, "strategy": strat, "error": str(e)[:80]})
                continue
            if isinstance(res, dict) and "error" in res:
                results.append({"symbol": sym, "strategy": strat, "error": res["error"][:80]})
                continue
            m = _extract(res)
            rob = m["robustness"] if m["robustness"] is not None else 0.0
            champion = (rob >= MIN_ROBUSTNESS and m["oos_return"] > 0 and m["trades"] >= MIN_TRADES)
            results.append({"symbol": sym, "strategy": strat, "champion": champion,
                            "score": round(m["oos_return"] * max(rob, 0), 3), **m})

    champs = sorted([r for r in results if r.get("champion")],
                    key=lambda r: r["score"], reverse=True)

    # Evolution diff vs last generation
    prev: set = set()
    gen = 1
    if os.path.exists(CHAMPIONS):
        try:
            old = json.load(open(CHAMPIONS, encoding="utf-8"))
            prev = {f"{c['symbol']}:{c['strategy']}" for c in old.get("champions", [])}
            gen = old.get("generation", 0) + 1
        except Exception:
            pass
    now = {f"{c['symbol']}:{c['strategy']}" for c in champs}
    payload = {
        "generation": gen,
        "evolved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "tested": len(results),
        "champions_count": len(champs),
        "promoted": sorted(now - prev),
        "demoted": sorted(prev - now),
        "champions": champs,
        "guardrails": {"min_robustness": MIN_ROBUSTNESS, "min_trades": MIN_TRADES,
                       "gate": "out-of-sample only — never in-sample return"},
    }
    return payload


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    quick = "--quick" in sys.argv
    r = evolve(quick=quick)
    os.makedirs(DATA_DIR, exist_ok=True)
    json.dump(r, open(CHAMPIONS, "w", encoding="utf-8"), indent=2, default=str)
    print(f"=== STRATEGY LAB · generation {r['generation']} · tested {r['tested']} "
          f"({'quick' if quick else 'full'}) ===")
    print(f"Champions: {r['champions_count']} | promoted {r['promoted']} | demoted {r['demoted']}")
    for c in r["champions"][:12]:
        print(f"  {c['symbol']:5} {c['strategy']:16} robustness {c['robustness']} "
              f"OOS {c['oos_return']}% trades {c['trades']} → score {c['score']}")
    if not r["champions"]:
        print("  (no strategy survived out-of-sample guardrails this generation — "
              "that's the system refusing to trade overfit noise)")
    print(f"\n[written] {CHAMPIONS}")
