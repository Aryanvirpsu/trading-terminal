"""Shared option-contract grading — the SINGLE source of truth for contract
quality, used by BOTH the dashboard options endpoint (`/api/symbol/options`) and
the strategy scanner's inline option idea (`_pick_option_idea`).

Before this existed the scanner returned option ideas with `spread=None,
liquidity=None` (see BASELINE.md) while the dedicated endpoint graded them A-D.
Now there is ONE formula; nothing is duplicated.

`grade_contract` never raises and always returns a fully-populated dict: every
field is present (None where a datum is genuinely missing), a numeric
`liquidity_score` (0-100), a letter `grade` (A-F), a `tradeable` flag, and a
human `rejection` string when it should not be traded. Penalises/*rejects*:
zero bids, one-sided or stale quotes, missing Greeks, wide spreads, low volume,
low open interest, and excessive slippage.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def _f(x) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        return v if v == v else None  # drop NaN
    except (TypeError, ValueError):
        return None


def grade_contract(c: Dict[str, Any], underlying: Optional[float], side: str,
                   dte: Optional[int]) -> Dict[str, Any]:
    """Score one option contract on liquidity + structure quality (NOT direction).

    Args:
        c: raw contract dict (strike, bid, ask, last_price, volume, open_interest,
           implied_volatility, delta, ...).
        underlying: current underlying price (for moneyness / breakeven).
        side: "CALL" or "PUT".
        dte: days to expiry.
    Returns a merged dict: the original contract fields plus graded metrics.
    """
    side = (side or "CALL").upper()
    strike = _f(c.get("strike")) or 0.0
    bid = _f(c.get("bid")) or 0.0
    ask = _f(c.get("ask")) or 0.0
    last = _f(c.get("last_price")) or _f(c.get("last")) or 0.0
    vol = int(_f(c.get("volume")) or 0)
    oi = int(_f(c.get("open_interest")) or 0)
    iv = _f(c.get("implied_volatility"))
    delta = _f(c.get("delta"))

    two_sided = bid > 0 and ask > 0
    mid = round((bid + ask) / 2, 2) if two_sided else (round(last, 2) if last else None)
    spread_dollars = round(ask - bid, 2) if two_sided else None
    spread_pct = round((ask - bid) / mid * 100, 1) if (two_sided and mid) else None

    otm_pct = None
    if underlying:
        raw = (strike - underlying) / underlying * 100
        otm_pct = round(raw if side == "CALL" else -raw, 1)

    px = mid if mid else last
    breakeven = None
    if px:
        breakeven = round(strike + px, 2) if side == "CALL" else round(strike - px, 2)

    passes, fails = [], []
    score = 0.0

    # --- Open interest (depth) -------------------------------------------------
    if oi >= 1000:
        score += 30; passes.append(f"deep OI {oi:,}")
    elif oi >= 250:
        score += 18; passes.append(f"OK OI {oi:,}")
    elif oi >= 50:
        score += 6; passes.append(f"thin OI {oi:,}")
    else:
        fails.append(f"very low OI {oi:,}")

    # --- Volume (today's activity) --------------------------------------------
    if vol >= 200:
        score += 15; passes.append(f"active vol {vol:,}")
    elif vol >= 25:
        score += 8
    else:
        fails.append(f"low vol {vol}")

    # --- Spread (slippage) -----------------------------------------------------
    if not two_sided:
        fails.append("no two-sided quote")
    elif spread_pct is None:
        fails.append("unpriced spread")
    elif spread_pct <= 8:
        score += 30; passes.append(f"tight spread {spread_pct}%")
    elif spread_pct <= 20:
        score += 15; passes.append(f"fair spread {spread_pct}%")
    else:
        fails.append(f"wide spread {spread_pct}%")

    # --- Moneyness (near-money preferred for real delta) ----------------------
    if otm_pct is not None:
        if abs(otm_pct) <= 3:
            score += 20; passes.append("near the money")
        elif abs(otm_pct) <= 7:
            score += 12; passes.append(f"{abs(otm_pct)}% OTM")
        else:
            fails.append(f"far {abs(otm_pct)}% OTM (lottery)")

    # --- IV sanity + Greeks presence ------------------------------------------
    if iv is not None:
        ivp = round(iv * 100, 1) if iv < 5 else round(iv, 1)  # accept fraction or pct
        if ivp > 120:
            fails.append(f"very high IV {ivp}%")
        else:
            score += 5
    else:
        fails.append("missing IV")
    if delta is None:
        fails.append("missing delta (Greeks)")

    grade = ("A" if score >= 80 else "B" if score >= 60 else
             "C" if score >= 40 else "D" if score >= 20 else "F")

    # A contract is tradeable only if it clears a real liquidity bar AND has no
    # disqualifying structural fault.
    hard_fail = any(k in f for f in fails for k in
                    ("wide spread", "no two-sided", "very low OI", "unpriced"))
    tradeable = score >= 60 and not hard_fail
    rejection = None
    if not tradeable:
        if hard_fail:
            rejection = "; ".join(f for f in fails if any(
                k in f for k in ("wide spread", "no two-sided", "very low OI", "unpriced")))
        else:
            rejection = f"liquidity score {round(score)} < 60"

    return {
        **c,
        "bid": bid or None, "ask": ask or None, "mid": mid,
        "spread_dollars": spread_dollars, "spread_pct": spread_pct,
        "volume": vol, "open_interest": oi,
        "implied_volatility": iv, "delta": delta,
        "otm_pct": otm_pct, "breakeven": breakeven, "dte": dte,
        "liquidity_score": round(score), "grade": grade,
        "passes": passes, "fails": fails,
        "tradeable": tradeable, "rejection": rejection,
    }
