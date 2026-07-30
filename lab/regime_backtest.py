"""Regime-tagged backtest (#7) — WHERE does each strategy actually work?

A strategy's headline return hides that it may only work in one regime. This
classifies every historical day into a market regime (bull/bear/sideways x
high/low vol), runs the backtest, and attributes each trade to the regime it was
opened in — so you see "rsi_pullback wins in sideways/low-vol, bleeds in
bull/high-vol." That's what lets the engine pick strategies to fit the CURRENT
regime instead of trading a bull-market strategy into a crash.

Headless: `python lab/regime_backtest.py AAPL rsi_pullback`
"""
from __future__ import annotations
import os, sys
from collections import defaultdict
from statistics import mean, pstdev, median

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from tradingview_mcp.core.services import backtest_service as bt


def classify_regimes(candles: list) -> dict:
    """date -> 'bull/hivol' style label."""
    closes = [c["close"] for c in candles]
    dates = [c["date"] for c in candles]
    raw = {}
    for i in range(len(candles)):
        if i < 50:
            continue
        sma = mean(closes[i - 49:i + 1])
        sma_ref = mean(closes[i - 69:i - 19]) if i >= 69 else sma
        slope = sma - sma_ref
        px = closes[i]
        if px > sma and slope > 0: trend = "bull"
        elif px < sma and slope < 0: trend = "bear"
        else: trend = "sideways"
        rets = [(closes[j] - closes[j - 1]) / closes[j - 1] for j in range(i - 19, i + 1) if closes[j - 1]]
        raw[dates[i]] = (trend, pstdev(rets) if len(rets) > 1 else 0.0)
    if not raw:
        return {}
    med = median([v for _, v in raw.values()])
    return {d: f"{t}/{'hi' if v > med else 'lo'}vol" for d, (t, v) in raw.items()}


def regime_backtest(symbol: str, strategy: str, period: str = "2y") -> dict:
    res = bt.run_backtest(symbol, strategy, period=period, initial_capital=500.0,
                          include_trade_log=True)
    if isinstance(res, dict) and "error" in res:
        return {"symbol": symbol, "strategy": strategy, "error": res["error"]}
    try:
        candles = bt._fetch_ohlcv(symbol, period)
    except Exception as e:
        return {"symbol": symbol, "strategy": strategy, "error": f"ohlcv: {e}"}
    regimes = classify_regimes(candles)

    buckets = defaultdict(list)
    for t in res.get("trade_log", []):
        buckets[regimes.get(t.get("entry_date"), "unknown")].append(t.get("return_pct", 0.0))

    by_regime = {}
    for reg, rl in buckets.items():
        wins = [r for r in rl if r > 0]
        by_regime[reg] = {"trades": len(rl), "win_rate": round(len(wins) / len(rl) * 100, 1),
                          "avg_return_pct": round(mean(rl), 2), "total_return_pct": round(sum(rl), 2)}
    return {"symbol": symbol, "strategy": strategy, "period": period,
            "total_trades": len(res.get("trade_log", [])),
            "overall_return_pct": res.get("total_return_pct"),
            "by_regime": dict(sorted(by_regime.items(),
                                     key=lambda kv: kv[1]["total_return_pct"], reverse=True))}


def sweep(symbols=("SPY", "AAPL", "BAC"), strategies=("rsi_pullback", "bollinger", "donchian"),
          period="2y") -> dict:
    """Aggregate per-regime performance across names to see which strategy fits
    which regime (the actionable output)."""
    agg = defaultdict(lambda: defaultdict(list))   # strategy -> regime -> [returns]
    for strat in strategies:
        for sym in symbols:
            r = regime_backtest(sym, strat, period)
            for reg, s in r.get("by_regime", {}).items():
                agg[strat][reg].append(s["total_return_pct"])
    out = {}
    for strat, regs in agg.items():
        out[strat] = {reg: {"symbols": len(v), "avg_total_return_pct": round(mean(v), 2)}
                      for reg, v in sorted(regs.items(), key=lambda kv: mean(kv[1]), reverse=True)}
    return out


if __name__ == "__main__":
    import json
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if len(sys.argv) >= 3:
        print(json.dumps(regime_backtest(sys.argv[1], sys.argv[2]), indent=2, default=str))
    else:
        print("=== REGIME SWEEP: which strategy works in which regime ===")
        print(json.dumps(sweep(), indent=2, default=str))
