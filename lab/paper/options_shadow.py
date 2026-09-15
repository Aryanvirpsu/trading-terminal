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
           session_date: Optional[str] = None, strategy: Optional[str] = None) -> Optional[str]:
    """Record the option view for a stock signal. Returns shadow_id, or None when the
    engine produced no option idea (a perfectly normal outcome).

    `strategy` is the one evidence field this function cannot source from `result`
    itself — it's a workflow-loop concept (the scanner strategy name), passed through
    by the caller exactly as it already is to journal.record_signal()/
    broker.submit_entry(). Every other new field below is read straight off
    canonical_bridge.py's option_display dict (`opt`) — the exact values already used
    to compute ev_opt/model_ev_per_contract, never recomputed here."""
    session_date = session_date or dt.date.today().isoformat()
    oq = result.get("option_quality") or {}
    opt = result.get("option") or {}
    if not opt and not oq:
        return None

    now = db.utcnow()
    contract = opt.get("contract") or oq.get("contract") or f"{symbol}-none"
    sid = _shadow_id(symbol, str(contract), now)
    greeks = opt.get("greeks") or {}
    bewm = opt.get("break_even_within_expected_move")

    db.execute("""INSERT OR REPLACE INTO options_shadow(
        shadow_id, created_at, session_date, signal_id, symbol, contract, expiry, strike,
        option_type, bid, ask, assumed_fill, spread_pct, volume, open_interest,
        delta, gamma, theta, vega, iv, iv_context, expected_move, break_even,
        max_loss, ev_after_costs, preference, gradeable, missing_json, outcome,
        direction, p_direction, theta_drag, p_trade, dte_used_in_model,
        underlying_price, stock_stop, stock_target, expected_move_pct, move_to_be_pct,
        break_even_within_expected_move, contract_quality_score, contract_quality_grade,
        quality_eligible, quality_rejection_reason, risk_rejection_reason, strategy)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
               ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
         json.dumps(oq.get("missing") or [], default=str), "open",
         opt.get("direction"), opt.get("p_direction"), opt.get("theta_drag"), opt.get("p_trade"),
         opt.get("dte_used_in_model"), opt.get("underlying_price"), opt.get("stock_stop"),
         opt.get("stock_target"), opt.get("expected_move_pct"), opt.get("move_to_be_pct"),
         (None if bewm is None else (1 if bewm else 0)),
         opt.get("contract_quality_score"), opt.get("contract_quality_grade"),
         (None if opt.get("quality_eligible") is None else (1 if opt.get("quality_eligible") else 0)),
         opt.get("quality_rejection_reason"), opt.get("risk_rejection_reason"), strategy))
    db.audit("options_shadow", sid, "recorded",
             {"symbol": symbol, "preference": oq.get("preference"),
              "gradeable": oq.get("gradeable")})
    return sid


# ══════════════════════════════════════════════════════════════════════════
# Evidence & Graduation (v1.2 phase 2) — the missing outcome resolver
#
# WHAT THIS GRADES: the UNDERLYING STOCK THESIS that generated the option
# candidate — did the same entry/stop/target the stock setup itself used go
# on to hit stop or target? This is deliberately NOT the option contract's
# own P&L. This repo has no historical option-chain pricing source (the
# chain is only ever fetched live, for the current moment, via
# strategy_service._pick_option_idea() — there is no historical bid/ask/
# settlement lookup anywhere in the codebase), so faking an option exit price
# from the underlying's move (delta, Black-Scholes, whatever) would
# contaminate the exact evidence this exists to collect honestly. Every
# resolved row gets `option_outcome = 'unavailable'` for that reason —
# stated, not silently omitted.
#
# Why the underlying thesis still matters even without option pricing: the
# live Model EV (canonical_bridge.py) is built directly from the stock
# setup's own risk/reward geometry (risk_ps/reward_ps feed opt_profit) — so
# "did the underlying thesis play out" is real, relevant evidence about the
# geometry half of the model, even though it says nothing about theta/IV/
# spread realism.
#
# SAME-BAR AMBIGUITY: reuses the exact policy already established for stock
# signals (journal.update_excursions()) and positions (broker.
# manage_open_positions()) — if one day's bar touches both stop and target,
# the STOP is assumed to have hit first. This is NOT "ambiguous" by choice:
# the project already has a real, tested, deliberately conservative answer
# to this question ("never flatter the record"), and reusing it is more
# honest than inventing a third state this codebase has never used anywhere
# else.
#
# IDEMPOTENT: only rows with outcome='open' are ever touched; a resolved row
# is never re-graded. Never places, modifies, or cancels an order — reads
# `options_shadow` and the same `quotes` dict market_hours() already
# fetched, writes only to `options_shadow`.
# ══════════════════════════════════════════════════════════════════════════

def resolve_outcomes(quotes: Dict[str, Any], session_date: Optional[str] = None) -> Dict[str, Any]:
    """Grade open shadow observations against today's quotes. Shadow-only: issues
    zero broker/order calls, touches only the options_shadow table. Call this from
    the same lifecycle stage that updates stock excursions (workflow.market_hours()),
    with the same `quotes` dict already fetched there — no new data fetch here."""
    session_date = session_date or dt.date.today().isoformat()
    rows = db.query("SELECT * FROM options_shadow WHERE outcome='open'")
    checked = expired = resolved = skipped = 0
    for r in rows:
        checked += 1
        expiry = r.get("expiry")
        if expiry and expiry < session_date:
            db.execute("""UPDATE options_shadow SET outcome=?, outcome_at=?, option_outcome=?
                          WHERE shadow_id=?""",
                       ("expired", db.utcnow(), "unavailable", r["shadow_id"]))
            expired += 1
            continue

        direction = r.get("direction")
        entry, stop, target = r.get("underlying_price"), r.get("stock_stop"), r.get("stock_target")
        q = quotes.get(r["symbol"])
        if direction is None or entry is None or stop is None or target is None or q is None:
            # Missing required evidence (historical pre-migration row, or no
            # quote available today) — stays open honestly, never guessed.
            skipped += 1
            continue

        hi = q.high if q.high is not None else q.last
        lo = q.low if q.low is not None else q.last
        if hi is None or lo is None:
            skipped += 1
            continue

        is_long = direction.upper() == "LONG"
        # Stop checked before target — same conservative tie-break as
        # journal.update_excursions()/broker.manage_open_positions().
        hit_stop = (lo <= stop) if is_long else (hi >= stop)
        hit_target = (hi >= target) if is_long else (lo <= target)
        if hit_stop:
            outcome = "stop_hit"
            pnl_ps = round((stop - entry) if is_long else (entry - stop), 4)
        elif hit_target:
            outcome = "target_hit"
            pnl_ps = round((target - entry) if is_long else (entry - target), 4)
        else:
            continue  # still open, no change

        db.execute("""UPDATE options_shadow SET outcome=?, outcome_at=?, outcome_pnl=?,
                      option_outcome=? WHERE shadow_id=?""",
                   (outcome, db.utcnow(), pnl_ps, "unavailable", r["shadow_id"]))
        db.audit("options_shadow", r["shadow_id"], "resolved",
                 {"symbol": r["symbol"], "outcome": outcome, "outcome_pnl": pnl_ps})
        resolved += 1

    return {"checked": checked, "resolved": resolved, "expired": expired,
            "skipped_missing_data": skipped}


def backfill_recoverable_evidence(dry_run: bool = True) -> Dict[str, Any]:
    """One-time backfill for rows recorded before Evidence & Graduation v1.2 phase 2
    (i.e. missing `underlying_price`) — NOT a general migration step, never called
    automatically. Only fills fields DETERMINISTICALLY recoverable from durable,
    already-written data:

    - contract_quality_score / contract_quality_grade / quality_eligible /
      risk_rejection_reason: from the immutable `audit` table's own
      'canonical_evaluated' event for this exact shadow_id (entity_id == shadow_id,
      an EXACT match, not approximate) — this was written at the moment of the
      original evaluation and never modified since.
    - underlying_price / stock_stop / stock_target / strategy: from the `signals`
      table, joined on (symbol, session_date) — safe ONLY because workflow.py's
      per-symbol same-day dedup guarantees at most one evaluation per (symbol,
      session_date), so a row is backfilled ONLY when EXACTLY ONE signals row
      matches; zero or multiple matches are skipped, never guessed.
    - direction: derived from the recovered target vs. entry (target > entry =>
      LONG, the same relationship decision_engine.evaluate() itself would have
      produced) — arithmetic on recovered data, not a new independent guess.

    NEVER recovers p_direction, theta_drag, p_trade, expected_move_pct,
    move_to_be_pct, break_even_within_expected_move, or dte_used_in_model — none of
    these were ever persisted anywhere (schema v1 or the audit trail), so for rows
    written before this phase they are genuinely lost and stay NULL, honestly,
    forever. `dry_run=True` (default) reports what WOULD change without writing;
    pass `dry_run=False` to actually apply it."""
    rows = db.query("SELECT * FROM options_shadow WHERE underlying_price IS NULL")
    plan: List[Dict[str, Any]] = []
    for r in rows:
        update: Dict[str, Any] = {}
        audit_row = db.query(
            """SELECT detail_json FROM audit WHERE entity='options_shadow_canonical'
               AND entity_id=? AND event='canonical_evaluated'""", (r["shadow_id"],))
        if len(audit_row) == 1:
            meta = json.loads(audit_row[0]["detail_json"])
            if meta.get("quality_score") is not None:
                update["contract_quality_score"] = meta["quality_score"]
            if meta.get("quality_grade") is not None:
                update["contract_quality_grade"] = meta["quality_grade"]
            if meta.get("quality_pass") is not None:
                update["quality_eligible"] = 1 if meta["quality_pass"] else 0
            if meta.get("option_eligible") is False and meta.get("option_binding_constraint"):
                update["risk_rejection_reason"] = meta["option_binding_constraint"]

        sig_rows = db.query(
            "SELECT entry, stop, target, strategy FROM signals WHERE symbol=? AND session_date=?",
            (r["symbol"], r["session_date"]))
        if len(sig_rows) == 1:
            s = sig_rows[0]
            if s["entry"] is not None:
                update["underlying_price"] = s["entry"]
            if s["stop"] is not None:
                update["stock_stop"] = s["stop"]
            if s["target"] is not None:
                update["stock_target"] = s["target"]
            if s["strategy"] is not None:
                update["strategy"] = s["strategy"]
            if s["entry"] is not None and s["target"] is not None:
                update["direction"] = "LONG" if s["target"] > s["entry"] else "SHORT"

        if update:
            plan.append({"shadow_id": r["shadow_id"], "symbol": r["symbol"],
                        "session_date": r["session_date"], "fields": update})
            if not dry_run:
                sets = ", ".join(f"{k}=?" for k in update)
                db.execute(f"UPDATE options_shadow SET {sets} WHERE shadow_id=?",
                          (*update.values(), r["shadow_id"]))

    if not dry_run and plan:
        db.audit("options_shadow", "backfill", "backfill_recoverable_evidence",
                 {"rows_updated": len(plan)})
    return {"dry_run": dry_run, "candidates": len(rows), "backfilled": len(plan), "plan": plan}


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
# STATUS (Evidence & Graduation v1.2 phase 2): Gate 2 is now reachable —
# resolve_outcomes() (this module) is wired into workflow.market_hours(),
# grading the UNDERLYING STOCK THESIS (not the option contract's own P&L —
# no historical option-chain pricing source exists in this repo) daily
# against the persisted stock_stop/stock_target. Historical rows recorded
# before this phase have NULL stock_stop/stock_target/underlying_price and
# can never resolve — that's honest, not a bug (see EVIDENCE_GRADUATION_
# AFTER.md's backfill table). Reaching Gate 2/3 still authorizes NOTHING;
# see Gates 4-6.
# ══════════════════════════════════════════════════════════════════════════

GATE_NAMES = (
    "data_collection", "outcome_evidence", "predictive_validity",
    "economic_eligibility", "risk_authorization", "execution",
)

# Gate 3 thresholds. 20 matches graduation_readiness()'s own resolved_outcomes
# floor (not a second, invented number). 50 matches the overall shadow
# sample-size floor for the same reason. Neither threshold means "calibrated"
# — they only widen what kind of DESCRIPTIVE read is honest to attempt.
MIN_RESOLVED_FOR_DESCRIPTIVE = 20
MIN_RESOLVED_FOR_VALIDATION_READY = 50


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
        "detail": by_name["resolved_outcomes"]["detail"] + " — grades the underlying stock "
                 "thesis only (resolve_outcomes(), run daily from market_hours()); "
                 "option_outcome is always 'unavailable' — no historical option-chain pricing "
                 "source exists in this repo to grade the contract's own P&L honestly.",
        "authorizes": "nothing — only that model scores can start being compared to realized outcomes",
    }
    # Gate 3 deliberately never returns PASS/FAIL, and never CALIBRATED at any N.
    if resolved_n < MIN_RESOLVED_FOR_DESCRIPTIVE:
        gate3_status = "INSUFFICIENT_EVIDENCE"
    elif resolved_n < MIN_RESOLVED_FOR_VALIDATION_READY:
        gate3_status = "DESCRIPTIVE_ONLY"
    else:
        gate3_status = "READY_FOR_VALIDATION"
    gate3 = {
        "gate": "predictive_validity",
        "status": gate3_status,
        "detail": f"{resolved_n} resolved shadow outcomes (underlying-thesis only). "
                 f"<{MIN_RESOLVED_FOR_DESCRIPTIVE}: not enough to look. "
                 f"{MIN_RESOLVED_FOR_DESCRIPTIVE}-{MIN_RESOLVED_FOR_VALIDATION_READY-1}: "
                 f"descriptive buckets only (model_ev_calibration_buckets()), no verdict. "
                 f">={MIN_RESOLVED_FOR_VALIDATION_READY}: enough to consider a real validation "
                 "study — still not itself calibration.",
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


MODEL_EV_BUCKETS = (
    ("< -25", None, -25.0),
    ("-25 to 0", -25.0, 0.0),
    ("0 to +10", 0.0, 10.0),
    ("+10 to +25", 10.0, 25.0),
    ("> +25", 25.0, None),
)

# p_direction is a [0.15, 0.85]-clamped heuristic (lab/decision_engine.py),
# not a probability — bucket edges chosen to match that clamp's natural
# quintile-ish spread, not fitted to any observed distribution.
P_DIRECTION_BUCKETS = (
    ("0.15-0.30", 0.15, 0.30),
    ("0.30-0.45", 0.30, 0.45),
    ("0.45-0.60", 0.45, 0.60),
    ("0.60-0.70", 0.60, 0.70),
    ("0.70-0.85", 0.70, 0.86),  # 0.86 so the real upper clamp (0.85) is inclusive
)


def _dedupe_by_observation(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Observation identity is (session_date, symbol, contract) — workflow.py's
    per-symbol same-day dedup already prevents same-day duplicates in normal
    operation, but this is a defensive second line, not a trust exercise: if two
    rows ever share an observation identity (e.g. a retried/rerun premarket
    before that guard applied), only the most recently created one counts. A
    symbol's contract evaluated again on a LATER session_date is a genuinely
    separate observation and is never collapsed."""
    best: Dict[tuple, Dict[str, Any]] = {}
    for r in rows:
        key = (r.get("session_date"), r.get("symbol"), r.get("contract"))
        prior = best.get(key)
        if prior is None or (r.get("created_at") or "") > (prior.get("created_at") or ""):
            best[key] = r
    return list(best.values())


def model_ev_calibration_buckets() -> Dict[str, Any]:
    """Bucket RESOLVED shadow records by model_ev (== ev_after_costs, the persisted
    column) and report win rate / avg realized P&L per bucket — the plumbing for
    "do positive Model EV records outperform negative ones", never a verdict computed
    prematurely. An unresolved record ('outcome' == 'open') is never counted as a
    winner OR a loser; it is excluded, not defaulted. Returns INSUFFICIENT_SAMPLE
    instead of numbers until MIN_RESOLVED_FOR_DESCRIPTIVE is met — this threshold is
    deliberately NOT tuned against whatever the live row count happens to be right
    now. Grades the underlying-thesis outcome only (see resolve_outcomes())."""
    rows = db.query("""SELECT session_date, symbol, contract, created_at, ev_after_costs,
                              outcome, outcome_pnl FROM options_shadow
                        WHERE outcome IN ('target_hit', 'stop_hit', 'expired')
                        AND ev_after_costs IS NOT NULL""")
    rows = _dedupe_by_observation(rows)
    if len(rows) < MIN_RESOLVED_FOR_DESCRIPTIVE:
        return {"status": "INSUFFICIENT_SAMPLE",
                "resolved_n": len(rows), "required_n": MIN_RESOLVED_FOR_DESCRIPTIVE,
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
    return {"status": "OK", "resolved_n": len(rows), "required_n": MIN_RESOLVED_FOR_DESCRIPTIVE,
            "buckets": buckets,
            "note": "monotonic win-rate/avg-P&L rise across buckets would support the model; "
                    "it is not claimed here — read the numbers, do not infer a verdict from this "
                    "function alone"}


def p_direction_calibration_buckets() -> Dict[str, Any]:
    """Bucket RESOLVED shadow records by the p_direction heuristic that fed their
    Model EV, and report the realized directional hit-rate per bucket — asking
    whether a HIGHER heuristic score corresponds to a HIGHER realized success
    frequency. This is descriptive-only plumbing, not a calibration claim: never
    read a bucket's realized hit-rate as proof p_direction=0.70 means a real 70%
    probability — that would be exactly the conflation this whole pass exists to
    stop. Only possible for rows recorded after p_direction started being
    persisted (Evidence & Graduation v1.2 phase 2) — historical rows with
    p_direction IS NULL are excluded, never treated as 0."""
    rows = db.query("""SELECT session_date, symbol, contract, created_at, p_direction,
                              outcome, outcome_pnl FROM options_shadow
                        WHERE outcome IN ('target_hit', 'stop_hit', 'expired')
                        AND p_direction IS NOT NULL""")
    rows = _dedupe_by_observation(rows)
    if len(rows) < MIN_RESOLVED_FOR_DESCRIPTIVE:
        return {"status": "INSUFFICIENT_SAMPLE",
                "resolved_n": len(rows), "required_n": MIN_RESOLVED_FOR_DESCRIPTIVE,
                "buckets": None,
                "note": "excludes historical rows with p_direction IS NULL (recorded before "
                        "this field was persisted) — they are absent from resolved_n, not "
                        "counted as 0"}

    buckets: Dict[str, Dict[str, Any]] = {}
    for label, lo, hi in P_DIRECTION_BUCKETS:
        in_bucket = [r for r in rows if lo <= r["p_direction"] < hi]
        hits = [r for r in in_bucket if r["outcome"] == "target_hit"]
        buckets[label] = {
            "n": len(in_bucket),
            "realized_directional_hit_rate_pct":
                round(100 * len(hits) / len(in_bucket), 1) if in_bucket else None,
        }
    return {"status": "OK", "resolved_n": len(rows), "required_n": MIN_RESOLVED_FOR_DESCRIPTIVE,
            "buckets": buckets,
            "note": "a realized hit-rate is NOT a probability — this only asks whether higher "
                    "p_direction correlates with better outcomes, never that p_direction IS a "
                    "probability. Do not present p_direction=0.70 as '70% win probability'."}


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
