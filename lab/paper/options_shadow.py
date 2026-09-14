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
    affordability, not edge: an unaffordable contract is not a trade at any quality.

    STATUS (Evidence & Graduation v1.2 audit): this function is NOT called by the
    live production path. `workflow.py::premarket()` calls `record()` below directly
    with `canonical_bridge.py`'s already-built option view — it never calls this
    function. Traced by grep + import/call-graph audit, not assumed from grep alone:
    no caller exists in workflow.py, canonical_bridge.py, broker.py, or any script
    under automation/. Its only callers are its own unit tests
    (tests/unit/test_paper_trading.py::test_ungradeable_option_is_honest,
    test_no_affordable_option_is_a_valid_result, test_short_and_multileg_options_not_simulated).

    It is NOT dead weight, though: `expected_move` here is computed from the
    contract's own IMPLIED volatility (`underlying * iv * sqrt(dte/365)`), and `edge`
    is a numeric distance from break-even to that expected move. The live path
    (`canonical_bridge.py`'s `option_display`) computes a DIFFERENT, ATR-based
    (historical-volatility-proxy) expected move and only a boolean
    `break_even_within_expected_move` — never a numeric edge. So this function's
    IV-based geometry is genuinely independent information, not a duplicate.

    What this function's OWN `ev_after_costs`/`preference` fields are NOT: they are a
    second, competing EV/preference model — geometry-based (edge x $100 - spread
    cost), distinct from the live `p_direction`-based `model_ev_per_contract` in
    canonical_bridge.py. Do not treat this function's output as "the" Model EV; the
    authoritative live figure is `canonical_bridge.py`'s `option_display`
    ("model_ev_per_contract"). `PAPER_GRADUATION_CHECKLIST.md` previously cited this
    function as the enforcement mechanism for the liquidity-floor graduation
    criterion — that was stale; the real enforcement is
    `canonical.contract_quality.evaluate_contract_quality()`'s hard-fail checks
    (see EVIDENCE_GRADUATION_AFTER.md for the correction and the recommendation:
    KEEP this function as-is, retitled in docs as a standalone IV-based contract-
    geometry reference implementation, not wired into the live pipeline)."""
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


# ══════════════════════════════════════════════════════════════════════════
# Evidence & Graduation (v1.2) — explicit gates
#
# graduation_readiness() above answers one question: "is there enough DATA to
# start evaluating the model?" It has never authorized execution and never
# will on its own — but a flat all-checks-pass boolean invites exactly that
# misreading. The six gates below make each question, and each gate's actual
# authority, explicit and separately inspectable. Reaching Gate 2 means "we
# can start asking whether the model's opinions track reality." It does NOT
# mean options may execute — only Gates 4 AND 5 together, both currently
# closed by explicit policy (not by sample size), authorize that.
#
# HONEST STATUS (adversarial review, 2026-09): Gate 2 cannot currently be
# reached by waiting. A shadow record's `outcome` column is set to 'open' at
# insert and NOTHING in this codebase ever transitions it to target_hit/
# stop_hit/expired — options_shadow has no equivalent of
# journal.update_excursions() (confirmed: zero `UPDATE options_shadow`
# statements anywhere in the repo). resolved_outcomes will read 0 forever
# until that resolver is built, regardless of elapsed time or record count.
# ══════════════════════════════════════════════════════════════════════════

GATE_NAMES = (
    "data_collection", "outcome_evidence", "predictive_validity",
    "economic_eligibility", "risk_authorization", "execution",
)


def execution_gate_state() -> Dict[str, Any]:
    """The six-gate state machine. Each gate reports its own status and what it
    does/does not authorize — no gate's status is inferred from another's."""
    ready = graduation_readiness()
    by_name = {c["name"]: c for c in ready["checks"]}
    resolved_n = next((c for c in ready["checks"] if c["name"] == "resolved_outcomes"), None)
    resolved_n = int(resolved_n["detail"].split()[0]) if resolved_n else 0

    gate1 = {
        "gate": "data_collection",
        "passed": by_name["sample_size"]["passed"] and by_name["gradeable_rate"]["passed"],
        "detail": f"{by_name['sample_size']['detail']}; {by_name['gradeable_rate']['detail']}",
        "authorizes": "nothing — only that enough observations exist to begin evaluating the model",
    }
    gate2 = {
        "gate": "outcome_evidence",
        "passed": by_name["resolved_outcomes"]["passed"],
        "detail": by_name["resolved_outcomes"]["detail"] + " — NOTE: no production mechanism "
                 "currently transitions a shadow record's outcome away from 'open' (no equivalent "
                 "of journal.update_excursions() exists for options_shadow; confirmed zero "
                 "'UPDATE options_shadow' statements anywhere in the codebase). resolved_outcomes "
                 "will stay at 0 regardless of how much time passes until that resolver is built — "
                 "this is not a 'wait for more data' situation.",
        "authorizes": "nothing — only that model scores can start being compared to realized outcomes",
    }
    # Gate 3 deliberately never returns PASS/FAIL — see model_ev_calibration_buckets().
    # A minimum-N floor for even attempting a verdict; MIN_RESOLVED_FOR_CALIBRATION_VERDICT
    # below documents why. Same "no resolver exists yet" caveat as gate2 applies here too.
    gate3_status = "INSUFFICIENT_EVIDENCE" if resolved_n < MIN_RESOLVED_FOR_CALIBRATION_VERDICT \
        else "SEE model_ev_calibration_buckets()"
    gate3 = {
        "gate": "predictive_validity",
        "status": gate3_status,
        "detail": f"{resolved_n} resolved shadow outcomes (need >= "
                 f"{MIN_RESOLVED_FOR_CALIBRATION_VERDICT} before any bucket is even attempted) — "
                 "this number cannot currently increase on its own; see gate2's detail",
        "authorizes": "nothing, ever, by itself — informs a human decision, never gates automatically",
    }
    gate4 = {
        "gate": "economic_eligibility",
        "status": "PER_CONTRACT",
        "detail": "answered per-contract by canonical.account_fit.option_account_fit() "
                 "(binding_constraint, most commonly 'per_trade_risk' on this account — "
                 "see canonical_audit_metadata()['option_binding_constraint'] for any given signal)",
        "authorizes": "nothing on its own — independent of model quality by construction",
    }
    gate5 = {
        "gate": "risk_authorization",
        "status": "NOT_AUTHORIZED",
        "detail": "STRATEGY_500_POLICY (canonical/risk_policy.py) has no option-specific "
                 "policy fields — they are explicitly None, not an unbounded/implicit grant. "
                 "This is a closed policy decision, not a missing configuration default.",
        "authorizes": "nothing — no option-specific execution policy exists to authorize against",
    }
    gate6 = {
        "gate": "execution",
        "status": "SHADOW_ONLY",
        "detail": "canonical_bridge.py hard-codes shadow_only=True; "
                 "canonical.executable.evaluate_option_executable() cannot return "
                 "executable=True while shadow_only=True, regardless of every other input",
        "authorizes": "nothing — structurally blocked, not merely unauthorized today",
    }

    gates = [gate1, gate2, gate3, gate4, gate5, gate6]
    return {
        "gates": gates,
        "execution_authorized": False,
        "note": ("Gates 1-2 measure DATA sufficiency. Gate 3 measures whether the model's "
                "opinions have been shown to track reality (never auto-authorizes). Gates 4-5 "
                "are independent economic/policy questions, not model-quality questions. Gate 6 "
                "is the only one that can ever flip to executable, and only a human policy "
                "decision — not a sample count — can open Gate 5 first."),
    }


# Below this many RESOLVED shadow outcomes, no bucket in
# model_ev_calibration_buckets() is even attempted — a handful of resolved
# trades split across 5 buckets would average single digits per bucket, which
# is not "a small sample", it's noise dressed as a bucket. 20 matches
# graduation_readiness()'s own "resolved_outcomes" floor (PAPER_GRADUATION_
# CHECKLIST.md) rather than inventing a second, different number.
MIN_RESOLVED_FOR_CALIBRATION_VERDICT = 20

MODEL_EV_BUCKETS = (
    ("< -25", None, -25.0),
    ("-25 to 0", -25.0, 0.0),
    ("0 to +10", 0.0, 10.0),
    ("+10 to +25", 10.0, 25.0),
    ("> +25", 25.0, None),
)


def model_ev_calibration_buckets() -> Dict[str, Any]:
    """Bucket RESOLVED shadow records by model_ev (== ev_after_costs, the persisted
    column) and report win rate / avg realized P&L per bucket — the plumbing for
    "do positive Model EV records outperform negative ones", never a verdict computed
    prematurely. An unresolved record ('outcome' == 'open') is never counted as a
    winner OR a loser; it is excluded, not defaulted. Returns INSUFFICIENT_SAMPLE
    instead of numbers until MIN_RESOLVED_FOR_CALIBRATION_VERDICT is met — this
    threshold is deliberately NOT tuned against whatever the live row count happens
    to be right now."""
    rows = db.query("""SELECT ev_after_costs, outcome, outcome_pnl FROM options_shadow
                        WHERE outcome IN ('target_hit', 'stop_hit', 'expired')
                        AND ev_after_costs IS NOT NULL""")
    if len(rows) < MIN_RESOLVED_FOR_CALIBRATION_VERDICT:
        return {"status": "INSUFFICIENT_SAMPLE",
                "resolved_n": len(rows), "required_n": MIN_RESOLVED_FOR_CALIBRATION_VERDICT,
                "buckets": None,
                "note": "no bucket is computed below the minimum — a ratio over a handful "
                        "of rows is not evidence, it's noise with a label"}

    buckets: Dict[str, Dict[str, Any]] = {}
    for label, lo, hi in MODEL_EV_BUCKETS:
        in_bucket = [r for r in rows
                    if (lo is None or r["ev_after_costs"] >= lo)
                    and (hi is None or r["ev_after_costs"] < hi)]
        wins = [r for r in in_bucket if r["outcome"] == "target_hit"]
        pnls = [r["outcome_pnl"] for r in in_bucket if r["outcome_pnl"] is not None]
        buckets[label] = {
            "n": len(in_bucket),
            "win_rate_pct": round(100 * len(wins) / len(in_bucket), 1) if in_bucket else None,
            "avg_realized_pnl": round(sum(pnls) / len(pnls), 2) if pnls else None,
        }
    return {"status": "OK", "resolved_n": len(rows), "required_n": MIN_RESOLVED_FOR_CALIBRATION_VERDICT,
            "buckets": buckets,
            "note": "monotonic win-rate/avg-P&L rise across buckets would support the model; "
                    "it is not claimed here — read the numbers, do not infer a verdict from this "
                    "function alone"}


def format_shadow_disclosure(option_display: Dict[str, Any]) -> str:
    """The user-facing block Evidence & Graduation v1.2 exists to make unavoidable —
    render a shadow option's model output next to its calibration/executability
    status so nobody reads a dollar figure as a demonstrated edge. Takes a
    canonical_bridge.py `option_display` dict (or any dict carrying the same keys)."""
    ev = option_display.get("model_ev_per_contract", option_display.get("ev_per_contract"))
    ev_str = f"{'+' if (ev or 0) >= 0 else ''}${ev:.2f} / contract" if ev is not None else "n/a"
    calib = option_display.get("calibration_status", "UNCALIBRATED")
    model = option_display.get("direction_model", "HEURISTIC")
    return (f"Model EV              {ev_str}\n"
            f"Calibration           {calib}\n"
            f"Direction model       {model}\n"
            f"Executable            NO — SHADOW ONLY")
