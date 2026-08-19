"""Fallback technical analysis from yfinance history — kills the TradingView
single-point-of-failure. When the scanner is throttled, the decision engine calls
this to compute trend/momentum/RSI/ATR from free, reliable Yahoo OHLCV and returns
an analyze_coin-COMPATIBLE dict so the trend family works unchanged.
"""
from __future__ import annotations
from statistics import mean

try:
    import yfinance as yf
    _OK = True
except Exception:
    _OK = False


def _rsi(closes, n=14):
    if len(closes) <= n:
        return 50.0
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    ag, al = mean(gains[-n:]), mean(losses[-n:])
    if al == 0:
        return 100.0
    rs = ag / al
    return round(100 - 100 / (1 + rs), 1)


def _atr(highs, lows, closes, n=14):
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
           for i in range(1, len(closes))]
    return round(mean(trs[-n:]) if len(trs) >= n else (mean(trs) if trs else 0), 3)


def analysis(symbol: str) -> dict:
    if not _OK:
        return {"error": "yfinance missing"}
    try:
        h = yf.Ticker(symbol).history(period="6mo")
        closes = h["Close"].tolist()
        highs, lows = h["High"].tolist(), h["Low"].tolist()
        # Timestamp of the LAST BAR. Without it nothing downstream can tell a quote
        # taken seconds ago from one taken last Friday, and freshness collapses into
        # "which provider answered" — which is not freshness at all.
        as_of = h.index[-1].isoformat() if len(h.index) else None
    except Exception as e:
        return {"error": f"yf history: {e}"}
    if len(closes) < 55:
        return {"error": "insufficient history"}

    price = round(closes[-1], 2)
    sma20, sma50 = mean(closes[-20:]), mean(closes[-50:])
    rsi = _rsi(closes)
    atr = _atr(highs, lows, closes)
    chg = round((closes[-1] - closes[-2]) / closes[-2] * 100, 2) if closes[-2] else 0.0

    if price > sma20 > sma50: trend = "Strong Uptrend"
    elif price > sma50: trend = "Uptrend"
    elif price < sma20 < sma50: trend = "Strong Downtrend"
    elif price < sma50: trend = "Downtrend"
    else: trend = "Sideways"
    mom = "Bullish" if (price > sma20 and rsi > 50) else ("Bearish" if (price < sma20 and rsi < 50) else "Neutral")
    if "Uptrend" in trend and 50 <= rsi <= 72: sig = "BUY"
    elif "Downtrend" in trend and 28 <= rsi <= 50: sig = "SELL"
    else: sig = "NEUTRAL"
    # rough 0-100 score for cross-checks (NOT used as an additive signal)
    score = int(max(0, min(100, 50 + (price - sma50) / sma50 * 200 + (rsi - 50) * 0.4)))

    return {
        "symbol": symbol.upper(),
        "price_data": {"current_price": price, "change_percent": chg},
        "rsi": {"value": rsi},
        "atr": {"value": atr, "percent_of_price": round(atr / price * 100, 2)},
        "trend_state": trend,
        "market_sentiment": {"momentum": mom, "buy_sell_signal": sig},
        "stock_score": score, "grade": "fallback",
        "_source": "yfinance-fallback",
        "as_of": as_of,                     # last bar's own timestamp, not fetch time
    }


if __name__ == "__main__":
    import sys, json
    for s in (sys.argv[1:] or ["AAPL"]):
        print(json.dumps(analysis(s), indent=2, default=str))
