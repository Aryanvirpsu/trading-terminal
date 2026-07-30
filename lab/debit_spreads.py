"""Debit Spread finder — the CASH-ACCOUNT defined-risk spread.

Bull call spread: BUY a near-money call, SELL a higher-strike call against it.
You pay a net DEBIT (cheaper than a naked long call), and because you only ever
*buy* the package, it needs NO margin — it's legal and safe in a cash account.

    Max loss   = net debit * 100   (fully paid up front — this is ALL you can lose)
    Max profit = (width - debit) * 100   (capped: the short call is the trade-off)
    Breakeven  = long_strike + debit
    Reward:risk= max_profit / max_loss

Risk is managed by construction:
  * max loss can NEVER exceed the debit you paid (cash-secured by definition),
  * every candidate is sized so that debit <= MAX_COMMIT_PCT of the account,
  * we reject anything with reward:risk below MIN_RR (don't pay $80 to make $20),
  * both legs must be liquid (tight-ish fills).

Headless: `python lab/debit_spreads.py BAC 368`
"""
from __future__ import annotations
import os, sys, datetime as dt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from tradingview_mcp.core.services.options_service import get_options_chain

MAX_COMMIT_PCT = 0.25   # net debit (=max loss) <= 25% of account
MIN_RR = 1.0            # require at least 1:1 reward:risk
WIDTHS = [1.0, 2.0, 2.5, 5.0, 10.0]


def _mid(c) -> float:
    b, a, last = c.get("bid"), c.get("ask"), c.get("last_price")
    if b and a and a > 0:
        return round((b + a) / 2, 2)
    return round(last or 0.0, 2)


def _liquid(c) -> bool:
    return (c.get("open_interest") or 0) >= 50 or (c.get("volume") or 0) >= 20


def find_bull_call_spreads(symbol: str, balance: float, dte_min: int = 10,
                           dte_max: int = 45, min_rr: float = MIN_RR):
    chain = get_options_chain(symbol)
    if "error" in chain:
        return {"symbol": symbol, "error": chain["error"]}
    price = chain.get("underlying_price") or chain.get("price")
    if not price:
        return {"symbol": symbol, "error": "no underlying price"}
    today = dt.datetime.now(dt.timezone.utc).date()
    exp = None
    for e in chain.get("available_expiries") or []:
        try:
            d = (dt.datetime.strptime(e, "%Y-%m-%d").date() - today).days
        except ValueError:
            continue
        if dte_min <= d <= dte_max:
            exp, dte = e, d
            break
    if not exp:
        return {"symbol": symbol, "error": f"no expiry in {dte_min}-{dte_max} DTE window"}

    ec = get_options_chain(symbol, exp)
    calls = {c["strike"]: c for c in (ec.get("calls") or []) if c.get("strike")}
    cap = balance * MAX_COMMIT_PCT

    out, seen = [], set()
    for k_long, lc in calls.items():
        # Long leg must be ATM-to-slightly-ITM (high delta ~0.5-0.65) so the spread
        # has a REAL chance — buying an OTM long leg is a lottery, not managed risk.
        if not (price * 0.96 <= k_long <= price * 1.01):
            continue
        if not _liquid(lc):
            continue
        long_mid = _mid(lc)
        if long_mid <= 0:
            continue
        for width in WIDTHS:
            k_short = round(k_long + width, 2)
            sc = calls.get(k_short)
            if not sc or not _liquid(sc):
                continue
            debit = round(long_mid - _mid(sc), 2)
            if debit <= 0.02:
                continue
            # A balanced ATM spread costs 30-70% of its width. Cheaper than that =
            # far-OTM lottery (fake R:R); pricier = barely any leverage left.
            if not (0.30 <= debit / width <= 0.70):
                continue
            max_loss = round(debit * 100, 2)
            if max_loss > cap:
                continue
            max_profit = round((width - debit) * 100, 2)
            if max_profit <= 0:
                continue
            breakeven = round(k_long + debit, 2)
            # Breakeven must be achievable — within ~3% of spot, not a moonshot.
            if breakeven > price * 1.03:
                continue
            rr = round(max_profit / max_loss, 2)
            if rr < min_rr:
                continue
            key = (k_long, k_short)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "structure": "bull call spread (debit)",
                "expiry": exp, "dte": dte,
                "buy_call": k_long, "sell_call": k_short, "width": width,
                "net_debit": debit, "max_loss_usd": max_loss,
                "max_profit_usd": max_profit, "reward_risk": rr,
                "breakeven": round(k_long + debit, 2),
                "long_pct_otm": round(max(0.0, (k_long - price) / price) * 100, 1),
                "how_to_trade": (f"Buy {symbol} {k_long}C / Sell {symbol} {k_short}C, {exp} "
                                 f"(net debit ${debit}). Max loss ${max_loss} = all you can lose."),
                "management": (f"Take profit at ~+{round(max_profit*0.6)}$ (60% of max); "
                               f"cut at -{round(max_loss*0.5)}$ (50% of debit); "
                               f"exit before expiry — don't let it pin between strikes."),
            })
    out.sort(key=lambda s: (s["reward_risk"], s["max_profit_usd"]), reverse=True)
    return {"symbol": symbol, "underlying_price": price, "expiry": exp,
            "account_cap_usd": round(cap, 2), "candidates": out[:5]}


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sym = sys.argv[1] if len(sys.argv) > 1 else "BAC"
    bal = float(sys.argv[2]) if len(sys.argv) > 2 else 368.0
    r = find_bull_call_spreads(sym, bal)
    if "error" in r:
        print(f"{sym}: {r['error']}"); sys.exit(0)
    print(f"=== BULL CALL (DEBIT) SPREADS · {sym} ${r['underlying_price']} · exp {r['expiry']} "
          f"· max debit ${r['account_cap_usd']} ===")
    for s in r["candidates"]:
        print(f"  Buy {s['buy_call']}C / Sell {s['sell_call']}C  debit ${s['net_debit']} "
              f"| maxL ${s['max_loss_usd']} maxP ${s['max_profit_usd']} | R:R {s['reward_risk']} "
              f"| BE {s['breakeven']}")
    if not r["candidates"]:
        print("  (no spread met the cap/RR/liquidity filters — try another name or DTE)")
