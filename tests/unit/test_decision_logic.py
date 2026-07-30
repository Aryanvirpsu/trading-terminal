"""Decision-logic overhaul tests (Prompt 6) — auditable TRADEABLE / MONITOR / REJECT.

Deterministic and OFFLINE: the base-TA load, regime and all nine evidence families
are mocked, so these exercise the GATE-derived action label, the shared freshness
classifier, quantified EV / max-loss / disagreement, and the mutually-exclusive
scenario probabilities — without hitting any provider.

Every required check from the prompt is covered and cross-referenced in-line:
  1. fallback != fresh                     -> test_fallback_is_not_fresh
  2. low data quality blocks TRADEABLE     -> test_low_data_quality_blocks_tradeable
  3. stale global/panel consistency        -> test_freshness_is_one_shared_system / _header_*
  4. quality vs threshold are separate     -> test_quality_and_threshold_are_separate
  5. REJECT hides the setup                -> test_reject_hides_setup_and_lists_failed_gates
  6. MONITOR shows confirmations           -> test_monitor_shows_pending_confirmations
  7. scenarios total 100%                  -> test_scenarios_sum_to_100
  8. max-loss matches quantity             -> test_max_loss_matches_quantity
  9. EV includes slippage                  -> test_ev_includes_slippage
 10. high disagreement penalty/block       -> test_high_disagreement_blocks_tradeable*
 11. action exactly matches gates          -> test_action_is_derived_only_from_gates

Run:  pytest tests/unit/test_decision_logic.py -q
"""
from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("src", "dashboard", "lab"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import decision_engine as de   # noqa: E402
import freshness as fr         # noqa: E402


# ── Deterministic harness ────────────────────────────────────────────────────

def _mk(fam, d, c, detail="x"):
    return {"family": fam, "dir": d, "conf": c, "detail": detail}


_DEEP = {  # func name -> family label
    "_fam_catalyst": "catalyst", "_fam_short": "short-interest",
    "_fam_filings": "filings/insider", "_fam_options_flow": "options-flow",
    "_fam_social": "social-sentiment", "_fam_analyst": "analyst-ratings",
    "_fam_macro": "macro-rates",
}


def _patch(monkeypatch, *, trend=(0.6, 0.8), regime=(0.3, 0.6), families=None,
           data_state="fresh", a_source="tradingview:NASDAQ", price=100.0, atrp=2.5,
           portfolio_blocks=False):
    """Fully control the 9-family set + provider state so a scenario is reproducible.
    `families` overrides individual deep families as {family_label: (dir, conf)}."""
    families = families or {}
    a = {"price_data": {"current_price": price}, "trend_state": "uptrend",
         "atr": {"value": price * atrp / 100.0, "percent_of_price": atrp},
         "market_sentiment": {"momentum": "Bullish", "buy_sell_signal": "BUY"},
         "rsi": {"value": 60}}
    monkeypatch.setattr(de, "_load_analysis", lambda s, e: (a, a_source, data_state))
    monkeypatch.setattr(de, "_safe_regime", lambda: {"risk_appetite_score": 65})
    monkeypatch.setattr(de, "_fam_trend", lambda _a: _mk("trend/momentum", *trend))
    monkeypatch.setattr(de, "_fam_regime", lambda _r, _d: _mk("regime", *regime))
    for fn, fam in _DEEP.items():
        d, c = families.get(fam, (0.25, 0.6))          # default: mildly aligned, healthy conf
        monkeypatch.setattr(de, fn, (lambda fam=fam, d=d, c=c: (lambda *a, **k: _mk(fam, d, c)))())
    # keep options + halts + calibration out of the way
    monkeypatch.setattr(de.ss, "_pick_option_idea", lambda *a, **k: None)
    try:
        import halts
        monkeypatch.setattr(halts, "is_halted", lambda s: False)
    except Exception:
        pass
    # Neutralise the PORTFOLIO risk gate. It reads the LIVE paper ledger (cash
    # reserve / drawdown / suspension), so leaving it live makes every test here
    # depend on mutable account state — they'd pass or fail depending on what the
    # ledger happened to hold. The gate itself is covered explicitly by
    # `test_risk_limit_gate_blocks_when_portfolio_refuses`.
    if not portfolio_blocks:
        try:
            import risk_engine
            monkeypatch.setattr(risk_engine, "check_new_trade", lambda sym, risk, spec: {
                "allow": True, "reasons": [], "size_cap_usd": 1e6,
                "portfolio": {"suspended": False, "drawdown_pct": 0.0}})
        except Exception:
            pass
    else:
        import risk_engine
        monkeypatch.setattr(risk_engine, "check_new_trade", lambda sym, risk, spec: {
            "allow": False, "reasons": ["would breach 20% cash reserve"],
            "size_cap_usd": 0.0,
            "portfolio": {"suspended": False, "drawdown_pct": 12.1}})


def _run(monkeypatch, *, profile="momentum", **kw):
    _patch(monkeypatch, **kw)
    return de.evaluate("TEST", "NASDAQ", "LONG", 500, evaluate_option=False, profile=profile)


def _gate(r, name):
    return next(g for g in r["decision_gates"] if g["name"] == name)


# ── 11. The action is derived ONLY from the gates ────────────────────────────

def test_action_is_derived_only_from_gates(monkeypatch):
    r = _run(monkeypatch)
    hard_fail = [g for g in r["decision_gates"] if g["blocking"] and not g["passed"]]
    soft_fail = [g for g in r["decision_gates"] if not g["blocking"] and not g["passed"]]
    expected = "REJECT" if hard_fail else ("MONITOR" if soft_fail else "TRADEABLE")
    assert r["decision"] == expected


def test_risk_limit_gate_blocks_when_portfolio_refuses(monkeypatch):
    # The portfolio gate is a HARD gate: when the risk engine refuses (cash reserve,
    # drawdown, suspension) the verdict must be REJECT no matter how good the signals.
    r = _run(monkeypatch, portfolio_blocks=True, trend=(0.7, 0.85), regime=(0.4, 0.7),
             families={f: (0.3, 0.7) for f in _DEEP.values()})
    g = _gate(r, "risk_limit")
    assert g["passed"] is False and g["blocking"] is True
    assert "cash reserve" in g["reason"]
    assert r["decision"] == "REJECT"


def test_clean_setup_is_tradeable(monkeypatch):
    # Strong aligned trend, fresh data, healthy confidence, no conflict -> every gate passes.
    r = _run(monkeypatch, trend=(0.7, 0.85), regime=(0.4, 0.7),
             families={f: (0.3, 0.7) for f in _DEEP.values()})
    assert all(g["passed"] for g in r["decision_gates"]), \
        [g["name"] for g in r["decision_gates"] if not g["passed"]]
    assert r["decision"] == "TRADEABLE"


# ── 2. Low data quality must NOT become TRADEABLE ────────────────────────────

def test_low_data_quality_blocks_tradeable(monkeypatch):
    # Coverage-based: the OPTIONS strategy REQUIRES a live option chain. With none
    # available the data_quality (soft) gate fails on a blocking gap -> MONITOR,
    # never TRADEABLE — even though the STOCK signals are strong.
    r = _run(monkeypatch, profile="options", trend=(0.7, 0.85), regime=(0.4, 0.7),
             families={f: (0.3, 0.7) for f in _DEEP.values()})
    dqg = _gate(r, "data_quality")
    assert dqg["passed"] is False
    assert "options" in dqg["value"]["blocking_gaps"]
    assert r["decision"] != "TRADEABLE"          # must be MONITOR (or REJECT), never TRADEABLE
    assert r["decision"] == "MONITOR"


def test_valid_coverage_is_not_falsely_low(monkeypatch):
    # Valid Yahoo/Finnhub coverage (momentum profile, no options needed) must NOT be
    # blocked by the data_quality gate — the "false low score" this prompt removes.
    r = _run(monkeypatch, trend=(0.7, 0.85), regime=(0.4, 0.7),
             families={f: (0.3, 0.7) for f in _DEEP.values()})
    assert _gate(r, "data_quality")["passed"] is True
    assert r["data_quality"]["sufficient"] is True
    assert r["data_quality"]["blocking_gaps"] == []


# ── 1 & 3. Fallback is never fresh; ONE shared freshness system ──────────────

def test_fallback_is_not_fresh(monkeypatch):
    r = _run(monkeypatch, data_state="fallback-provider", a_source="yfinance-fallback")
    assert r["freshness"]["state"] == "fallback"
    assert r["freshness"]["state"] != "fresh"
    assert _gate(r, "freshness")["passed"] is False
    assert r["decision"] != "TRADEABLE"          # fallback data can't be TRADEABLE by default


def test_freshness_is_one_shared_system():
    # The engine's freshness must come from the SAME classifier the header/panels use.
    assert de._engine_freshness("fresh")["state"] == fr.classify(0)["state"] == "fresh"
    assert de._engine_freshness("fallback-provider")["state"] == \
        fr.classify(None, is_fallback=True)["state"] == "fallback"
    assert fr.blocks_tradeable("stale") and fr.blocks_tradeable("fallback")
    assert not fr.blocks_tradeable("fresh") and not fr.blocks_tradeable("ageing")


def test_header_uses_shared_classifier_not_scheduler_age(monkeypatch):
    # The global 'Data' badge must classify the market-DATA artifact age through the
    # shared classifier (scan thresholds) — NOT the scheduler task's idle age.
    import app
    monkeypatch.setattr(app, "_fresh", lambda name: {"age_seconds": 4 * 3600, "at": "x"})
    st = app._data_state("last_scan_both.json")
    assert st["state"] == fr.classify(4 * 3600, thresholds=app._SCAN_THRESHOLDS)["state"]
    assert st["scope"] == "batch-scan"           # explicitly not the live engine read
    # a 4h-old daily scan is 'fresh' on scan thresholds, though it would be
    # 'critically_stale' on the live-quote thresholds — proving the age is real,
    # source-based, and interpreted per-scope by the one classifier.
    assert st["state"] == "fresh"


def test_stale_global_and_panel_agree(monkeypatch):
    # Regression for "header says stale 18h while engine says fresh": both sides run
    # the same classifier, so for the SAME source age they produce the SAME state.
    import app
    age = 30 * 3600
    monkeypatch.setattr(app, "_fresh", lambda name: {"age_seconds": age, "at": "x"})
    header = app._data_state("last_scan_both.json")["state"]
    panel = fr.classify(age, thresholds=app._SCAN_THRESHOLDS)["state"]
    assert header == panel                       # never contradict each other


# ── 4. Quality SCORE and THRESHOLD are separate values ───────────────────────

def test_quality_and_threshold_are_separate(monkeypatch):
    r = _run(monkeypatch)
    assert r["quality_max"] == 100
    assert r["quality_threshold"] == r["quality_bar"]          # score is out of 100
    assert isinstance(r["confidence_quality"], (int, float))
    assert r["confidence_quality"] != r["quality_threshold"]   # they are NOT the same number
    # the threshold is the gate's requirement, quality is the value
    q = _gate(r, "quality_threshold")
    assert q["value"] == r["confidence_quality"]
    assert str(r["quality_threshold"]) in q["requirement"]


# ── 5. REJECT: NO TRADE, list failed gates, setup is only hypothetical ───────

def test_reject_hides_setup_and_lists_failed_gates(monkeypatch):
    # Signals cancel out -> quality below threshold + alignment fails (HARD) -> REJECT.
    r = _run(monkeypatch, trend=(-0.6, 0.8), regime=(-0.4, 0.6),
             families={f: (-0.3, 0.6) for f in _DEEP.values()})
    assert r["decision"] == "REJECT"
    hard_fail = [g for g in r["decision_gates"] if g["blocking"] and not g["passed"]]
    assert hard_fail                                   # at least one mandatory gate failed
    assert r["reject_reasons"]                         # exact failed-gate reasons surfaced
    assert r["failed_gates"]                           # names of failed gates
    assert r["upgrade_conditions"]                     # "what must change"
    # the setup levels still exist in the payload (so the UI can put them under a
    # collapsed 'hypothetical setup'), but the action is unambiguously NO-TRADE.
    assert r["entry_range"] and r["stop"] and r["target"]


# ── 6. MONITOR: pending confirmations, not an active trade ───────────────────

def test_monitor_shows_pending_confirmations(monkeypatch):
    # All HARD gates pass but a SOFT gate (freshness on fallback data) fails -> MONITOR.
    r = _run(monkeypatch, data_state="fallback-provider", a_source="yfinance-fallback",
             trend=(0.7, 0.85), regime=(0.4, 0.7),
             families={f: (0.3, 0.7) for f in _DEEP.values()})
    assert r["decision"] == "MONITOR"
    assert not [g for g in r["decision_gates"] if g["blocking"] and not g["passed"]]
    soft_fail = [g for g in r["decision_gates"] if not g["blocking"] and not g["passed"]]
    assert soft_fail                                   # pending confirmation conditions
    assert r["upgrade_conditions"]                     # what would upgrade it to TRADEABLE
    assert r["avoid_if"]                               # what would downgrade it


# ── 7. Scenario probabilities are mutually exclusive and sum to 100% ─────────

def test_scenarios_sum_to_100(monkeypatch):
    r = _run(monkeypatch)
    sp = r["scenario_probabilities"]
    total = sp["bull"] + sp["base"] + sp["bear"]
    assert abs(total - 100.0) <= 0.1                   # tolerance test
    assert "method" in sp and "P(dir)" not in sp["method"] or "not P(dir)" in sp["method"]


@pytest.mark.parametrize("seed", range(1, 60))
def test_scenarios_sum_to_100_property(seed):
    import random
    random.seed(seed)
    sp = de._scenario_probabilities(random.uniform(-1, 1), random.uniform(0.2, 5),
                                    random.uniform(0.2, 10), random.uniform(0.1, 3),
                                    random.random(), random.random())
    assert abs((sp["bull"] + sp["base"] + sp["bear"]) - 100.0) <= 0.1


def test_scenarios_not_derived_from_p_dir():
    # Same P(dir) inputs but different geometry -> different scenario split, proving
    # the scenarios are NOT a direct function of P(dir).
    tight = de._scenario_probabilities(0.5, 1.0, 1.0, 1.0, 0.6, 0.6)
    wide = de._scenario_probabilities(0.5, 1.0, 5.0, 1.0, 0.6, 0.6)
    assert (tight["bull"], tight["bear"]) != (wide["bull"], wide["bear"])


# ── 8. Max Loss is always tied to a quantity ─────────────────────────────────

def test_max_loss_matches_quantity(monkeypatch):
    r = _run(monkeypatch)
    m = r["max_loss_breakdown"]
    assert m["quantity"] == r["suggested_shares"]
    expected = round(m["quantity"] * (m["stop_distance_per_share"]
                                      + m["slippage_gap_allowance_per_share"]), 2)
    assert m["total_planned_loss"] == expected
    assert r["max_loss_usd"] == m["total_planned_loss"]
    # never present a max loss without the quantity it assumes
    assert "quantity" in m and m["quantity"] is not None


# ── 9. EV is explained and net of spread + slippage ──────────────────────────

def test_ev_includes_slippage(monkeypatch):
    r = _run(monkeypatch)
    e = r["ev_breakdown"]
    for k in ("ev_per_share", "expected_return_pct", "expected_r",
              "ev_after_slippage", "ev_before_costs", "inputs", "formula"):
        assert k in e
    assert e["ev_after_slippage"] <= e["ev_before_costs"]     # costs reduce EV
    assert e["inputs"]["cost_per_share"] > 0                  # a real cost was applied
    assert r["expected_value_per_share"] == e["ev_per_share"]


# ── 10. High disagreement penalises confidence and blocks TRADEABLE ──────────

def test_high_disagreement_blocks_tradeable(monkeypatch):
    # Strong trend up, but options-flow + analyst strongly bearish -> HIGH disagreement.
    r = _run(monkeypatch, trend=(0.7, 0.85), regime=(0.4, 0.7),
             families={"options-flow": (-0.8, 0.8), "analyst-ratings": (-0.7, 0.8),
                       "catalyst": (-0.6, 0.7)})
    assert r["disagreement"]["severity"] == "high"
    assert r["disagreement"]["confidence_penalty"] > 0
    assert _gate(r, "signal_disagreement")["passed"] is False
    assert r["decision"] != "TRADEABLE"
    # the penalty visibly lowers overall confidence
    assert r["overall_confidence"] <= r["data_confidence"]


def test_high_disagreement_override_allows(monkeypatch):
    # The ONLY way high disagreement can pass is an explicit configured override.
    monkeypatch.setenv("DECISION_ALLOW_HIGH_DISAGREEMENT", "true")
    r = _run(monkeypatch, trend=(0.7, 0.85), regime=(0.4, 0.7),
             families={"options-flow": (-0.8, 0.8), "analyst-ratings": (-0.7, 0.8),
                       "catalyst": (-0.6, 0.7)})
    assert r["disagreement"]["severity"] == "high"
    assert _gate(r, "signal_disagreement")["passed"] is True   # override lets it through


def test_fallback_override_allows(monkeypatch):
    # Fallback data can only be TRADEABLE if the operator explicitly opts in.
    monkeypatch.setenv("DECISION_ALLOW_FALLBACK_TRADEABLE", "true")
    r = _run(monkeypatch, trend=(0.7, 0.85), regime=(0.4, 0.7),
             families={f: (0.3, 0.7) for f in _DEEP.values()},
             data_state="fallback-provider", a_source="yfinance-fallback")
    assert r["freshness"]["state"] == "fallback"
    assert _gate(r, "freshness")["passed"] is True             # override permits it


# ── Disagreement math sanity ─────────────────────────────────────────────────

def test_disagreement_low_when_aligned():
    active = [_mk("trend/momentum", 0.6, 0.8), _mk("regime", 0.4, 0.6),
              _mk("analyst-ratings", 0.3, 0.6)]
    d = de._disagreement(active, 1)
    assert d["severity"] == "low"
    assert d["conflicting_family_count"] == 0
    assert d["confidence_penalty"] == 0.0
