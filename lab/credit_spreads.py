"""Credit Spread finder — defined-risk premium selling for a small account.

Focuses on the PUT CREDIT SPREAD (a.k.a. bull put spread): sell a higher-strike
OTM put, buy a lower-strike put for protection. You collect a net credit; you
keep it all if the stock stays above the short strike at expiry. Max loss is
capped at (width - credit), so a $500 account can trade it safely — unlike a
naked put.

    Max profit = net credit
    Max loss   = (width - credit) * 100
    Breakeven  = short_strike - credit
    Prob. profit ~ how far OTM the short strike is (further = safer, less credit)

This finds a spread that (a) is meaningfully OTM (higher win probability),
(b) collects a credit worth >= min_credit_pct of the width, and (c) whose max
loss fits the account's per-trade cap. Live Yahoo chains, no LLM.

Headless: `python lab/credit_spreads.py SPY 368`  → prints candidate spreads
"""
from __future__ import annotations
import os, sys, datetime as dt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from tradingview_mcp.core.services.options_service import get_options_chain

MAX_COMMIT_PCT = 0.25   # max loss per spread <= 25% of account


def _mid(c) -> float:
    b, a, last = c.get("bid"), c.get("ask"), c.get("last_price")
    if b and a and a > 0:
        return round((b + a) / 2, 2)
    return round(last or 0.0, 2)


def find_put_credit_spreads(symbol: str, balance: float, width: float = 1.0,
                            min_credit_pct: float = 0.20, otm_min: float = 0.02,
                            otm_max: float = 0.08, dte_min: int = 14, dte_max: int = 45):
    """Return ranked put-credit-spread candidates for `symbol` sized to `balance`."""
    chain = get_options_chain(symbol)
    if "error" in chain:
        return {"symbol": symbol, "error": chain["error"]}
    price = chain.get("underlying_price") or chain.get("price")
    expiries = chain.get("available_expiries") or []
    today = dt.datetime.now(dt.timezone.utc).date()
    exp = None
    for e in expiries:
        try:
            d = (dt.datetime.strptime(e, "%Y-%m-%d").date() - today).days
        except ValueError:
            continue
        if dte_min <= d <= dte_max:
            exp, dte = e, d
            break
    if not exp:
        return {"symbol": symbol, "error": "no expiry in the 14-45 DTE window"}

    ec = get_options_chain(symbol, exp)
    puts = {p["strike"]: p for p in (ec.get("puts") or []) if p.get("strike")}
    max_loss_cap = balance * MAX_COMMIT_PCT

    out = []
    for k_short, sp in puts.items():
        otm = (price - k_short) / price
        if not (otm_min <= otm <= otm_max):      # short strike: modestly OTM
            continue
        if (sp.get("open_interest") or 0) < 50 and (sp.get("volume") or 0) < 20:
            continue
        k_long = round(k_short - width, 2)
        lp = puts.get(k_long)
        if not lp:
            continue
        credit = round(_mid(sp) - _mid(lp), 2)
        if credit <= 0:
            continue
        max_loss = round((width - credit) * 100, 2)
        if max_loss <= 0 or max_loss > max_loss_cap:
            continue
        if credit / width < min_credit_pct:       # not paid enough for the risk
            continue
        breakeven = round(k_short - credit, 2)
        out.append({
            "structure": "put credit spread (bull put)",
            "expiry": exp, "dte": dte,
            "sell_put": k_short, "buy_put": k_long, "width": width,
            "net_credit": credit, "max_profit_usd": round(credit * 100, 2),
            "max_loss_usd": max_loss,
            "breakeven": breakeven,
            "return_on_risk_pct": round(credit / (width - credit) * 100, 1),
            "pct_otm": round(otm * 100, 1),
            "approx_pop_pct": round(min(95, 50 + otm * 100 * 5), 0),  # rough: further OTM = higher POP
            "how_to_trade": (f"Sell {symbol} {k_short}P / Buy {symbol} {k_long}P, {exp} "
                             f"(net credit ${credit}). Defined risk ${max_loss}."),
        })
    out.sort(key=lambda s: s["return_on_risk_pct"], reverse=True)
    return {"symbol": symbol, "underlying_price": price, "expiry": exp,
            "max_loss_cap": round(max_loss_cap, 2), "candidates": out[:5]}


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sym = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    bal = float(sys.argv[2]) if len(sys.argv) > 2 else 368.0
    w = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
    r = find_put_credit_spreads(sym, bal, width=w)
    if "error" in r:
        print(f"{sym}: {r['error']}"); sys.exit(0)
    print(f"=== PUT CREDIT SPREADS · {sym} ${r['underlying_price']} · exp {r['expiry']} "
          f"· max-loss cap ${r['max_loss_cap']} ===")
    for s in r["candidates"]:
        print(f"  Sell {s['sell_put']}P / Buy {s['buy_put']}P  credit ${s['net_credit']} "
              f"| maxL ${s['max_loss_usd']} | ROR {s['return_on_risk_pct']}% "
              f"| ~POP {s['approx_pop_pct']}% | BE {s['breakeven']} ({s['pct_otm']}% OTM)")
    if not r["candidates"]:
        print("  (no spread fit the width/credit/cap filters — try a wider width or different name)")
