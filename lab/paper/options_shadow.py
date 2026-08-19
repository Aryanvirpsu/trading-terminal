"""Options shadow mode — record, never execute.

Options must not delay the stock paper launch, and nothing here writes into the paper
ledger. We record what we WOULD have done, with a conservative assumed fill, and score
the outcome later. Options graduate into the ledger only after chain-validity,
liquidity, fill and risk tests pass — see PAPER_GRADUATION_CHECKLIST.md.

An option is scored ONLY when the full microstructure is present (bid, ask, spread,
volume, open interest, DTE and Greeks). "No valid option" is an acceptable, expected,
and frequently correct answer.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any, Dict, List, Optional

from . import db

REQUIRED = ("bid", "ask", "open_interest", "volume", "days_to_expiry")
REQUIRED_GREEKS = ("delta",)


def _shadow_id(symbol: str, contract: str, created_at: str) -> str:
    return "shd_" + hashlib.sha1(f"{created_at}|{symbol}|{contract}".encode()).hexdigest()[:16]


def conservative_fill(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    """Assume we pay UP to buy: the ask, not the midpoint. If the spread is very wide
    we do not pretend a better fill exists."""
    if bid is None or ask is None or ask <= 0 or ask < bid:
        return None
    return round(ask, 4)


def affordability(assumed_fill: Optional[float], buying_power: Optional[float] = None,
                  fees: float = 0.0) -> Dict[str, Any]:
    """Can this contract actually be bought in a $500 cash account?

    Total premium = assumed fill (the ASK, i.e. we cross the spread) x 100 + fees.
    Max loss on a long option is the full premium — there is no stop that saves you.
    """
    from . import config as cfg, risk as risk_mod
    o = cfg.options()
    if assumed_fill is None or assumed_fill <= 0:
        return {"affordable": False, "reason": "no usable ask", "total_premium": None}
    total = round(assumed_fill * 100 + fees, 2)
    if buying_power is None:
        try:
            buying_power = risk_mod.account_state()["buying_power"]
        except Exception:
            buying_power = 0.0
    reasons = []
    if total > o.max_premium_per_trade:
        reasons.append(f"total premium ${total} exceeds the "
                       f"${o.max_premium_per_trade} per-trade allocation")
    if total > (buying_power or 0.0):
        reasons.append(f"total premium ${total} exceeds buying power "
                       f"${round(buying_power or 0.0, 2)}")
    return {"affordable": not reasons, "total_premium": total,
            "max_loss": total,                       # long option: the whole premium
            "allocation_cap": o.max_premium_per_trade,
            "buying_power": round(buying_power or 0.0, 2),
            "reason": "; ".join(reasons) if reasons else "fits cash and allocation"}


def evaluate_contract(opt: Dict[str, Any], underlying_price: Optional[float],
                      stock_quality: Optional[float] = None,
                      stock_ok: bool = True,
                      buying_power: Optional[float] = None) -> Dict[str, Any]:
    """Grade one contract. Returns preference + the exact missing fields when it can't
    be graded — never a fabricated score. On a $500 account the FIRST question is
    affordability, not edge: an unaffordable contract is not a trade at any quality."""
    from . import config as cfg
    ocfg = cfg.options()

    # Cash account: long single-leg only. Short/naked/multi-leg are not simulated.
    side = (opt.get("position") or "long").lower()
    if side != "long" and not cfg.account().allow_naked_options:
        return {"gradeable": False, "missing": [],
                "preference": "prefer-stock" if stock_ok else "avoid-both",
                "reason": "short/naked options are not enabled on this cash account"}
    if opt.get("legs") and len(opt.get("legs") or []) > 1 and not ocfg.allow_multi_leg:
        return {"gradeable": False, "missing": [],
                "preference": "prefer-stock" if stock_ok else "avoid-both",
                "reason": "multi-leg strategies are not enabled on this cash account"}

    missing = [k for k in REQUIRED if opt.get(k) in (None, 0)]
    greeks = opt.get("greeks") or {}
    missing += [f"greeks.{g}" for g in REQUIRED_GREEKS if greeks.get(g) is None]
    bid, ask = opt.get("bid"), opt.get("ask")
    fill = conservative_fill(bid, ask)
    spread_pct = (round((ask - bid) / ((ask + bid) / 2) * 100, 2)
                  if (bid and ask and (ask + bid) > 0) else None)

    if missing or fill is None:
        return {"gradeable": False, "missing": missing or ["unusable bid/ask"],
                "preference": "prefer-stock" if stock_ok else "avoid-both",
                "assumed_fill": fill, "spread_pct": spread_pct,
                "reason": "insufficient option microstructure to score honestly"}

    # Affordability BEFORE edge — an unaffordable contract is not a trade at any quality.
    aff = affordability(fill, buying_power)
    if not aff["affordable"]:
        return {"gradeable": True, "missing": [], "assumed_fill": fill,
                "spread_pct": spread_pct, "affordable": False,
                "total_premium": aff["total_premium"], "max_loss": aff["max_loss"],
                "allocation_cap": aff["allocation_cap"],
                "buying_power": aff["buying_power"],
                "preference": "prefer-stock" if stock_ok else "avoid-both",
                "reason": f"no affordable option: {aff['reason']}"}

    dte = opt.get("days_to_expiry")
    oi, vol = opt.get("open_interest") or 0, opt.get("volume") or 0
    liquid = ((spread_pct is not None and spread_pct <= ocfg.max_spread_pct)
              and oi >= ocfg.min_open_interest and vol >= ocfg.min_volume)
    strike = opt.get("strike")
    is_call = (opt.get("option_type") or "CALL").upper().startswith("C")
    break_even = (round(strike + fill, 4) if is_call else round(strike - fill, 4)) if strike else None
    max_loss = round(fill * 100, 2)                   # long option: premium at risk
    iv = greeks.get("iv") or opt.get("iv")
    expected_move = (round(underlying_price * (iv or 0) * ((dte or 0) / 365) ** 0.5, 4)
                     if (underlying_price and iv and dte) else None)

    # EV after costs: crossing the spread is a real, immediate cost.
    edge = 0.0
    if expected_move and break_even and underlying_price:
        room = (expected_move - abs(break_even - underlying_price)) if is_call else \
               (expected_move - abs(underlying_price - break_even))
        edge = round(room, 4)
    spread_cost = round((ask - bid) * 100, 2) if (ask and bid) else 0.0
    ev_after = round(edge * 100 - spread_cost, 2)

    if not liquid:
        pref = "prefer-stock" if stock_ok else "avoid-both"
    elif ev_after > 0 and stock_ok:
        pref = "prefer-option"
    elif stock_ok:
        pref = "prefer-stock"
    else:
        pref = "avoid-both"

    return {"gradeable": True, "missing": [], "assumed_fill": fill,
            "spread_pct": spread_pct, "liquid": liquid, "break_even": break_even,
            "max_loss": aff["max_loss"], "expected_move": expected_move,
            "ev_after_costs": ev_after, "preference": pref,
            "affordable": True, "total_premium": aff["total_premium"],
            "allocation_cap": aff["allocation_cap"],
            "buying_power": aff["buying_power"],
            "iv": iv, "dte": dte, "open_interest": oi, "volume": vol}


def record(result: Dict[str, Any], symbol: str, signal_id: Optional[str] = None,
           session_date: Optional[str] = None) -> Optional[str]:
    """Record the option view for a stock signal. Returns shadow_id, or None when the
    engine produced no option idea (a perfectly normal outcome)."""
    session_date = session_date or dt.date.today().isoformat()
    oq = result.get("option_quality") or {}
    opt = result.get("option") or {}
    if not opt and not oq:
        return None

    now = db.utcnow()
    contract = opt.get("contract") or oq.get("contract") or f"{symbol}-none"
    sid = _shadow_id(symbol, str(contract), now)
    greeks = opt.get("greeks") or {}

    db.execute("""INSERT OR REPLACE INTO options_shadow(
        shadow_id, created_at, session_date, signal_id, symbol, contract, expiry, strike,
        option_type, bid, ask, assumed_fill, spread_pct, volume, open_interest,
        delta, gamma, theta, vega, iv, iv_context, expected_move, break_even,
        max_loss, ev_after_costs, preference, gradeable, missing_json, outcome)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (sid, now, session_date, signal_id, symbol, str(contract),
         opt.get("expiry"), opt.get("strike"), opt.get("option_type"),
         opt.get("bid"), opt.get("ask"),
         conservative_fill(opt.get("bid"), opt.get("ask")),
         oq.get("spread_pct") or opt.get("spread_pct"),
         opt.get("volume"), opt.get("open_interest"),
         greeks.get("delta"), greeks.get("gamma"), greeks.get("theta"),
         greeks.get("vega"), greeks.get("iv"), None, None,
         opt.get("breakeven"),
         (round(opt["premium"] * 100, 2) if opt.get("premium") is not None else None),
         opt.get("ev_per_contract"), oq.get("preference", "prefer-stock"),
         1 if oq.get("gradeable") else 0,
         json.dumps(oq.get("missing") or [], default=str), "open"))
    db.audit("options_shadow", sid, "recorded",
             {"symbol": symbol, "preference": oq.get("preference"),
              "gradeable": oq.get("gradeable")})
    return sid


def summary(session_date: Optional[str] = None) -> Dict[str, Any]:
    where, params = ("WHERE session_date=?", (session_date,)) if session_date else ("", ())
    rows = db.query(f"SELECT * FROM options_shadow {where} ORDER BY created_at DESC", params)
    prefs: Dict[str, int] = {}
    for r in rows:
        prefs[r.get("preference") or "unknown"] = prefs.get(r.get("preference") or "unknown", 0) + 1
    gradeable = [r for r in rows if r.get("gradeable")]
    return {"count": len(rows), "gradeable": len(gradeable),
            "ungradeable": len(rows) - len(gradeable),
            "preferences": prefs, "rows": rows[:50],
            "in_ledger": False,
            "note": "shadow mode only — no option has ever been written to the paper ledger"}


def graduation_readiness() -> Dict[str, Any]:
    """Are options ready to enter the ledger? Every check must pass, and there must be
    a real sample behind it."""
    rows = db.query("SELECT * FROM options_shadow")
    gradeable = [r for r in rows if r.get("gradeable")]
    resolved = [r for r in rows if r.get("outcome") in ("target_hit", "stop_hit", "expired")]
    checks = [
        {"name": "sample_size", "passed": len(rows) >= 50,
         "detail": f"{len(rows)} shadow records (need 50)"},
        {"name": "gradeable_rate", "passed": bool(rows) and len(gradeable) / max(len(rows), 1) >= 0.5,
         "detail": f"{len(gradeable)}/{len(rows)} had full microstructure"},
        {"name": "resolved_outcomes", "passed": len(resolved) >= 20,
         "detail": f"{len(resolved)} resolved (need 20)"},
        {"name": "stock_launch_stable", "passed": _stock_stable(),
         "detail": "stock paper trading reconciles and has >= 50 resolved trades"},
    ]
    return {"ready": all(c["passed"] for c in checks), "checks": checks,
            "note": "options stay in shadow mode until every check passes"}


def _stock_stable() -> bool:
    try:
        from . import broker
        n = db.query_one("SELECT COUNT(*) n FROM positions WHERE status='closed'")["n"]
        return broker.reconcile()["reconciled"] and n >= 50
    except Exception:
        return False
