"""Canonical Option Architecture v1.1 — Step 10: Pipeline 3 (Strategy-500)
canonical bridge.

Builds the authoritative A/B/D/E/executable result for one Strategy-500
candidate, using the REAL paper account state (never a neutral default when
real state exists) and the canonical `STRATEGY_500_POLICY`. Layer C
(direction/gates) is computed by `decision_engine.evaluate(evaluate_option=
False)` — this module never touches gate semantics; it starts from that
result and adds everything downstream.

Deliberately a separate module rather than inlined in workflow.py/broker.py:
the same canonical-attachment shape (build A from the candidate, B(stock)/
B(option) from real account state, D, E, executable, then a legacy-shaped
compatibility block for existing consumers) already exists twice
(dashboard/research.py:_attach_canonical_pipeline1 for Pipeline 1,
dashboard/options_desk.py:decide() for Pipeline 2); keeping Pipeline 3's copy
in one file makes the parallel structure — and the one place Strategy-500's
shadow_only=True is hardcoded — easy to find and audit.

ADDITIVE to the existing Pipeline-3 modules: nothing here writes to the
ledger, journal schema, or options_shadow schema directly. Callers
(workflow.py) decide what to do with the result.

Evidence & Graduation (v1.2) note on `option_display["ev_per_contract"]`:
this number is a MODEL OPINION, not a measured expectation. Its probability
input (`p_direction`/`p_trade`, from decision_engine's indicator-agreement
heuristic) has no calibration evidence behind it — see
EVIDENCE_GRADUATION_AFTER.md. `option_display` now also carries
`model_ev_per_contract` (identical value, explicit name),
`calibration_status`, and `direction_model` so any consumer can render the
disclosure without re-deriving it. `ev_per_contract` is kept unchanged for
backward compatibility (tests and other pipelines key off that exact name —
see test_canonical_v11_invariants.py's field-provenance check).
"""
from __future__ import annotations

import os
import sys
from dataclasses import asdict
from typing import Any, Dict, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in (_ROOT, os.path.join(_ROOT, "src"), os.path.join(_ROOT, "lab"),
          os.path.join(_ROOT, "dashboard")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from canonical.contract_quality import (          # noqa: E402
    ContractQualityResult, evaluate_contract_quality,
)
from canonical.risk_policy import STRATEGY_500_POLICY   # noqa: E402
from canonical.account_fit import (                # noqa: E402
    AccountFitResult, stock_account_fit, option_account_fit,
)
from canonical.instrument_choice import (           # noqa: E402
    InstrumentChoice, OptionEdge, choose_instrument,
)
from canonical.sizing import size_instrument         # noqa: E402
from canonical.executable import (                   # noqa: E402
    evaluate_stock_executable, evaluate_option_executable,
)

from . import risk as risk_mod    # noqa: E402

_INSTRUMENT_LABEL = {
    InstrumentChoice.OPTION: "OPTION PREFERRED",
    InstrumentChoice.STOCK: "STOCK PREFERRED",
    InstrumentChoice.NO_TRADE: "NO TRADE",
}
# Legacy _grade_option_chain() vocabulary — preserved so
# options_shadow.record() and any reader of result["option_quality"] keep
# the values they've always seen, now canonically sourced.
_LEGACY_PREFERENCE = {
    InstrumentChoice.OPTION: "prefer-option",
    InstrumentChoice.STOCK: "prefer-stock",
    InstrumentChoice.NO_TRADE: "avoid-both",
}


def _executable_buy_price(quote: Any) -> Optional[float]:
    """Per-share cost of a simulated market BUY (ask + slippage) — the SAME price
    the paper broker will fill at — so sizing can never approve a quantity the
    fill simulator/risk gate will then refuse. None when there is no quote
    (sizing falls back to the reference entry; execution is refused anyway)."""
    if quote is None:
        return None
    from . import config as cfg
    qr = quote.resolved()
    if not qr.has_market:
        return None
    return round(qr.ask * (1 + cfg.execution().slippage_bps / 10000.0), 4)


def evaluate_canonical(result: Dict[str, Any], *, symbol: str, direction: str,
                       sector: Optional[str] = None,
                       session_date: Optional[str] = None,
                       quote: Any = None) -> Dict[str, Any]:
    """The authoritative A/B/D/E/executable evaluation for one candidate.

    `result` is decision_engine.evaluate(..., evaluate_option=False)'s own
    output — Layer C, untouched. Returns a dict (never raises on a missing/
    partial `result` — mirrors decision_engine's own early-reject dicts,
    which lack `price`/`stop`) with:

        setup_tradeable, account_state, contract_quality (or None),
        stock_account_fit (or None), option_account_fit (or None),
        instrument_choice, sizing, stock_executable, option_executable,
        stock_planned_risk, option_display, option_quality_display

    `option_display`/`option_quality_display` are legacy-shaped compatibility
    dicts (decision_engine's own former `option`/`option_quality` output
    shape) so options_shadow.record() and any other reader of those two
    result keys keeps working unchanged — see the module docstring.
    """
    setup_tradeable = result.get("decision") == "TRADEABLE"
    st = risk_mod.account_state(session_date)

    entry_range = result.get("entry_range") or []
    entry = entry_range[0] if entry_range else None
    stop = result.get("stop")

    canon_stock_fit: Optional[AccountFitResult] = None
    if entry is not None and stop is not None:
        sec = sector or "unknown"
        canon_stock_fit = stock_account_fit(
            policy=STRATEGY_500_POLICY, equity=st["equity"], entry=entry, stop=stop,
            fill_price=_executable_buy_price(quote),
            # RAW spendable cash: stock_account_fit nets out the policy reserve
            # itself, so st["buying_power"] (already net) would subtract it twice.
            buying_power=st["available_cash"], open_positions=st["open_positions"],
            sector=sec, sector_open_positions=st["sector_positions"].get(sec, 0),
            sector_exposure=st["sector_value"].get(sec, 0.0),
            # day_pnl is signed (negative = a loss); check_entry()'s own
            # daily-loss gate is symmetric with this — current_daily_loss is
            # documented as a positive "loss used so far" figure.
            current_daily_loss=max(0.0, -st["day_pnl"]),
            current_drawdown=st["drawdown_usd"])

    # ---- option candidate generation (Pipeline-3-specific, unchanged) -----
    # strategy_service._pick_option_idea() is the SAME candidate generator
    # decision_engine.evaluate(evaluate_option=True) used to call internally
    # — called here instead, once, since evaluate_option=False means C never
    # calls it. Its own ranking/filtering (closest-to-money, capital-fitting,
    # liquid) is untouched; only structural PASS/FAIL authority moves to A.
    from tradingview_mcp.core.services import strategy_service as ss
    opt = None
    if entry is not None:
        try:
            opt = ss._pick_option_idea(symbol, round(entry, 2), st["equity"], direction)
        except Exception:
            opt = None

    canon_quality: Optional[ContractQualityResult] = None
    canon_option_fit: Optional[AccountFitResult] = None
    option_display: Optional[Dict[str, Any]] = None
    edge = OptionEdge()
    if opt:
        try:
            import market_regime as _MRg
            session_open = _MRg.session_state()["state"] == "open"
        except Exception:
            session_open = True
        # quote_timestamp: whatever strategy_service._pick_option_idea()
        # actually returned (Step 9.1 propagation — None if the underlying
        # options-chain provider offered no trustworthy one; never invented
        # here). Same canonical A as Pipelines 1/2, same fail-closed rule.
        canon_quality = evaluate_contract_quality(
            strike=opt.get("strike"), underlying=entry, side=opt.get("option_type") or "CALL",
            bid=opt.get("bid"), ask=opt.get("ask"), volume=opt.get("volume"),
            open_interest=opt.get("open_interest"), implied_volatility=opt.get("implied_volatility"),
            delta=opt.get("delta"), dte=opt.get("days_to_expiry"),
            quote_timestamp=opt.get("quote_timestamp"), session_open=session_open)

        if canon_quality is not None:
            canon_option_fit = option_account_fit(
                policy=STRATEGY_500_POLICY, equity=st["equity"], contract_quality=canon_quality,
                side=opt.get("option_type") or "CALL", limit_price=opt.get("premium") or 0.0,
                spot=entry, stop=stop or 0.0, strike=opt.get("strike") or 0.0,
                iv=opt.get("implied_volatility"), dte=opt.get("days_to_expiry"),
                spread_dollars=opt.get("spread_dollars"), buying_power=st["buying_power"])

        # ---- genuine D-owned preference signals only (Phase 9) ------------
        # Reuses decision_engine's OWN pure EV/liquidity helpers — the SAME
        # formula evaluate_option=True would have used internally — never a
        # new model. No A/B criteria (OI/spread/DTE/freshness/eligibility)
        # enter this calculation.
        import decision_engine as de
        dte = opt.get("days_to_expiry") or 7
        liq_opt = de._gate_liquidity(entry, opt)
        spr = (liq_opt.get("spread_pct") or 0) / 100
        theta_drag = min(.25, (7.0 / max(dte, 1)) * .10)
        p_direction = result.get("p_direction") or 0.5
        risk_ps = abs(entry - (stop if stop is not None else entry))
        reward_ps = abs((result.get("target") or entry) - entry)
        p_trade = round(max(.1, p_direction - spr * .5 - theta_drag), 3)
        prem = opt.get("premium") or 0.0
        opt_profit = prem * (reward_ps / max(risk_ps, .01)) * .5
        ev_opt = de._expected_value(p_trade, opt_profit, prem, prem * spr)

        # theta_pct_per_day intentionally NOT mapped — theta_drag is a
        # probability-discount fraction over the trade's whole life, not a
        # "% of premium per day" figure (same reasoning as Pipeline 1's
        # research.py — a differently-scaled quantity, never force-mapped).
        atr_pct = ((result.get("gates") or {}).get("volatility") or {}).get("atr_pct")
        expected_move_pct = (atr_pct * (dte ** 0.5)) if atr_pct else None
        breakeven = opt.get("breakeven")
        move_to_be_pct = (abs(breakeven - entry) / entry * 100) if (breakeven and entry) else None
        break_even_within_move = (
            (move_to_be_pct <= expected_move_pct)
            if (move_to_be_pct is not None and expected_move_pct is not None) else None)

        edge = OptionEdge(theta_pct_per_day=None,
                          break_even_within_expected_move=break_even_within_move,
                          ev_positive=(ev_opt > 0))

        _model_ev = round(ev_opt * 100, 2)
        option_display = {
            "contract": opt.get("label"), "premium": prem, "pct_otm": opt.get("pct_otm"),
            "spread_pct": liq_opt.get("spread_pct"), "theta_drag": round(theta_drag, 2),
            "ev_per_contract": _model_ev,
            # Explicit semantic aliases (Evidence & Graduation v1.2) — same
            # number as ev_per_contract, named so a consumer never has to
            # infer "is this calibrated?" from the bare figure. See the
            # module docstring and EVIDENCE_GRADUATION_AFTER.md.
            "model_ev_per_contract": _model_ev,
            "calibration_status": "UNCALIBRATED",
            "direction_model": "HEURISTIC",
            # Exact model inputs (Evidence & Graduation v1.2 phase 2) — the
            # same local variables used two lines above to compute ev_opt,
            # exposed so options_shadow.record() can persist them without a
            # second computation. Never recompute these downstream.
            "direction": direction,
            "p_direction": p_direction,
            "p_trade": p_trade,
            "dte_used_in_model": dte,
            "underlying_price": entry,
            "stock_stop": stop,
            "stock_target": result.get("target"),
            "expected_move_pct": expected_move_pct,
            "move_to_be_pct": move_to_be_pct,
            "break_even_within_expected_move": break_even_within_move,
            # Layer A/B results, already computed above — read, never re-derived.
            "contract_quality_score": canon_quality.score if canon_quality else None,
            "contract_quality_grade": canon_quality.grade if canon_quality else None,
            "quality_eligible": bool(canon_quality.quality_pass) if canon_quality else None,
            "quality_rejection_reason": ("; ".join(canon_quality.hard_failures)
                                        if (canon_quality and canon_quality.hard_failures) else None),
            "risk_rejection_reason": (canon_option_fit.binding_constraint
                                     if (canon_option_fit and not canon_option_fit.eligible) else None),
            "verdict": "structure OK" if ev_opt > 0 else "AVOID option — take the stock",
            "bid": opt.get("bid"), "ask": opt.get("ask"),
            "strike": opt.get("strike"), "expiry": opt.get("expiry"),
            "option_type": opt.get("option_type"), "days_to_expiry": opt.get("days_to_expiry"),
            "volume": opt.get("volume"), "open_interest": opt.get("open_interest"),
            "breakeven": breakeven,
            "greeks": {"delta": opt.get("delta"), "iv": opt.get("implied_volatility")},
            # Provenance (Step 9.1) — exposed for debugging, never authoritative
            # by itself (canon_quality.freshness_status/quote_age_hours are).
            "quote_timestamp": opt.get("quote_timestamp"),
            "quote_timestamp_source": opt.get("quote_timestamp_source"),
        }

    # ---- D: only after C, B(stock), A, B(option) are all known ------------
    # quality_tilt=0 (Phase 9's preferred fallback): Strategy-500 has no
    # clean, non-duplicated D-only preference tally (unlike Pipeline 2's
    # reasons_for_option/reasons_for_stock text) — honest rather than
    # reconstructing legacy mixed scoring.
    canon_choice = choose_instrument(
        setup_tradeable=setup_tradeable, stock_account_fit=canon_stock_fit,
        option_account_fit=canon_option_fit, contract_quality=canon_quality,
        option_edge=edge, quality_tilt=0)

    # ---- E: legacy conviction-scaled shares as a clamped TARGET only ------
    # result["suggested_shares"] is decision_engine's genuine uncertainty-
    # scaled conviction size — computed identically whether evaluate_option
    # is True or False (it never depends on the option overlay) — passed
    # only when D actually chose STOCK (meaningless as a contracts count).
    target_quantity = (result.get("suggested_shares")
                       if canon_choice.choice == InstrumentChoice.STOCK else None)
    canon_sizing = size_instrument(
        choice=canon_choice, stock_account_fit=canon_stock_fit, option_account_fit=canon_option_fit,
        target_quantity=target_quantity)

    stock_planned_risk = 0.0
    if (canon_choice.choice == InstrumentChoice.STOCK and entry is not None and stop is not None
            and canon_sizing.quantity):
        stock_planned_risk = round(canon_sizing.quantity * abs(entry - stop), 2)

    canon_stock_exec = evaluate_stock_executable(
        setup_tradeable=setup_tradeable, account_fit=canon_stock_fit, choice=canon_choice,
        sizing=canon_sizing, execution_policy_allows=True)
    # shadow_only=True is HARD-CODED for Strategy-500 — not a config knob,
    # not conditional on anything computed above. This is the one line in
    # the whole canonical stack that keeps every Strategy-500 option
    # unconditionally non-executable, regardless of how clean A/B/D/E are.
    canon_option_exec = evaluate_option_executable(
        setup_tradeable=setup_tradeable, contract_quality=canon_quality, account_fit=canon_option_fit,
        choice=canon_choice, sizing=canon_sizing, execution_policy_allows=True, shadow_only=True)

    option_quality_display = None
    if opt:
        option_quality_display = {
            "chain_quality": canon_quality.score if canon_quality else None,
            "best_option_quality": canon_quality.score if canon_quality else None,
            "preference": _LEGACY_PREFERENCE[canon_choice.choice],
            "gradeable": bool(canon_quality and canon_quality.quality_pass),
            "missing": list(canon_quality.hard_failures) if canon_quality else ["no option idea"],
            "stock_ok": bool(canon_stock_fit and canon_stock_fit.eligible),
        }

    return {
        "setup_tradeable": setup_tradeable,
        "account_state": st,
        "contract_quality": canon_quality,
        "stock_account_fit": canon_stock_fit,
        "option_account_fit": canon_option_fit,
        "instrument_choice": canon_choice,
        "instrument_label": _INSTRUMENT_LABEL[canon_choice.choice],
        "sizing": canon_sizing,
        "stock_planned_risk": stock_planned_risk,
        "stock_executable": canon_stock_exec,
        "option_executable": canon_option_exec,
        "option_display": option_display,
        "option_quality_display": option_quality_display,
    }


def augmented_result_for_shadow(result: Dict[str, Any], canon: Dict[str, Any]) -> Dict[str, Any]:
    """A shallow copy of `result` with `option`/`option_quality` populated
    from the canonical evaluation — the exact shape options_shadow.record()
    already expects (see its own REQUIRED/REQUIRED_GREEKS reads), now
    canonically sourced instead of decision_engine's internal
    _grade_option_chain(). Never mutates the original `result` (still used
    for journal.record_signal(), which reads several of its OTHER fields)."""
    out = dict(result)
    out["option"] = canon["option_display"]
    out["option_quality"] = canon["option_quality_display"]
    return out


def canonical_audit_metadata(canon: Dict[str, Any]) -> Dict[str, Any]:
    """JSON-safe canonical metadata for db.audit() — Phase 15/17's requested
    fields (quality_pass, A score/grade, account eligibility, binding
    constraint, instrument choice, canonical quantity, executables,
    shadow_only, quote provenance), without any options_shadow/signals
    schema change."""
    cq, sf, of = canon["contract_quality"], canon["stock_account_fit"], canon["option_account_fit"]
    return {
        "setup_tradeable": canon["setup_tradeable"],
        "quality_pass": bool(cq and cq.quality_pass),
        "quality_score": cq.score if cq else None,
        "quality_grade": cq.grade if cq else None,
        "quote_timestamp": (canon["option_display"] or {}).get("quote_timestamp") if canon["option_display"] else None,
        "quote_timestamp_source": (canon["option_display"] or {}).get("quote_timestamp_source") if canon["option_display"] else None,
        "freshness_status": cq.freshness_status.value if cq else None,
        "stock_eligible": bool(sf and sf.eligible),
        "stock_binding_constraint": sf.binding_constraint if sf else None,
        "option_eligible": bool(of and of.eligible),
        "option_binding_constraint": of.binding_constraint if of else None,
        "instrument_choice": canon["instrument_label"],
        "sizing_instrument": canon["sizing"].instrument.value,
        "sizing_quantity": canon["sizing"].quantity,
        "stock_planned_risk": canon["stock_planned_risk"],
        "stock_executable": canon["stock_executable"].executable,
        "option_executable": canon["option_executable"].executable,
        "shadow_only": True,
    }
