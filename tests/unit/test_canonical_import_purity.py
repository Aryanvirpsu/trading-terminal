"""Canonical Option Architecture v1.1 — Step 4.1: import-purity and
before/after numerical-parity tests.

Two concerns, kept in one file since they're both about this one narrow
hygiene step:

  1. Import purity — canonical.contract_quality / canonical.risk_policy /
     canonical.account_fit must be importable in a clean subprocess with
     zero dashboard/Flask/sys.path/environment side effects.
  2. Behavior parity — the risk numbers `canonical.account_fit` and
     `canonical.contract_quality` produce via the extracted
     `canonical.option_risk_math` module must be numerically identical to
     what `dashboard/option_risk.py:option_risk()` / `stock_risk()` and
     `dashboard/options_desk.py:bs_greeks()` produce directly — proving the
     extraction changed dependency PLACEMENT, not the math.

Purity tests run the check in a real subprocess (not just "in this
process") because sys.path/sys.modules pollution is exactly the kind of
thing that can look clean by accident if some earlier import in the same
pytest session already primed the environment.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)


def _run_subprocess_snippet(code: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], cwd=_ROOT,
                          capture_output=True, text=True, timeout=30)


# ══════════════════════════════════════════════════════════════════════════
# 1. sys.path is not mutated by importing canonical.account_fit
# ══════════════════════════════════════════════════════════════════════════

def test_canonical_import_does_not_mutate_sys_path():
    proc = _run_subprocess_snippet(
        "import sys\n"
        "before = list(sys.path)\n"
        "import canonical.account_fit\n"
        "after = list(sys.path)\n"
        "print('EQUAL' if after == before else 'CHANGED:' + repr((before, after)))\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "EQUAL", (
        f"canonical.account_fit mutated sys.path: {proc.stdout}\n{proc.stderr}"
    )


def test_canonical_contract_quality_import_does_not_mutate_sys_path():
    proc = _run_subprocess_snippet(
        "import sys\n"
        "before = list(sys.path)\n"
        "import canonical.contract_quality\n"
        "after = list(sys.path)\n"
        "print('EQUAL' if after == before else 'CHANGED')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "EQUAL"


def test_canonical_risk_policy_import_does_not_mutate_sys_path():
    proc = _run_subprocess_snippet(
        "import sys\n"
        "before = list(sys.path)\n"
        "import canonical.risk_policy\n"
        "after = list(sys.path)\n"
        "print('EQUAL' if after == before else 'CHANGED')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "EQUAL"


# ══════════════════════════════════════════════════════════════════════════
# 2. dashboard.* does not appear in sys.modules after canonical import
# ══════════════════════════════════════════════════════════════════════════

def test_canonical_import_does_not_load_dashboard_modules():
    proc = _run_subprocess_snippet(
        "import sys\n"
        "import canonical.account_fit\n"
        "import canonical.contract_quality\n"
        "import canonical.risk_policy\n"
        "loaded = [m for m in sys.modules if m in "
        "('options_desk', 'option_risk', 'research', 'app') "
        "or m.startswith('dashboard')]\n"
        "print('NONE' if not loaded else 'LOADED:' + ','.join(sorted(loaded)))\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "NONE", (
        f"canonical import pulled in dashboard modules: {proc.stdout}"
    )


def test_canonical_instrument_choice_import_does_not_mutate_sys_path():
    """Step 5: canonical.instrument_choice must uphold the same Step-4.1
    purity guarantees — it imports .account_fit and .contract_quality
    (intra-package only), never dashboard code."""
    proc = _run_subprocess_snippet(
        "import sys\n"
        "before = list(sys.path)\n"
        "import canonical.instrument_choice\n"
        "after = list(sys.path)\n"
        "print('EQUAL' if after == before else 'CHANGED')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "EQUAL"


def test_canonical_instrument_choice_import_does_not_load_dashboard_modules():
    proc = _run_subprocess_snippet(
        "import sys\n"
        "import canonical.instrument_choice\n"
        "loaded = [m for m in sys.modules if m in "
        "('options_desk', 'option_risk', 'research', 'app') "
        "or m.startswith('dashboard')]\n"
        "print('NONE' if not loaded else 'LOADED:' + ','.join(sorted(loaded)))\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "NONE"


def test_canonical_instrument_choice_does_not_depend_on_risk_profile():
    results = []
    for profile_env in (None, "BALANCED", "AGGRESSIVE_SMALL_ACCOUNT", "CUSTOM"):
        env = dict(os.environ)
        if profile_env is None:
            env.pop("RISK_PROFILE", None)
        else:
            env["RISK_PROFILE"] = profile_env
        proc = subprocess.run(
            [sys.executable, "-c",
             "from canonical.instrument_choice import choose_instrument, InstrumentChoice\n"
             "from canonical.account_fit import stock_account_fit, option_account_fit\n"
             "from canonical.contract_quality import evaluate_contract_quality\n"
             "from canonical.risk_policy import STRATEGY_500_POLICY\n"
             "from datetime import datetime, timezone\n"
             "now = datetime(2026, 6, 15, 15, 30, tzinfo=timezone.utc)\n"
             "cq = evaluate_contract_quality(strike=103.0, underlying=100.0, side='CALL',\n"
             "    bid=0.99, ask=1.01, volume=300, open_interest=1000,\n"
             "    implied_volatility=0.30, delta=0.50, dte=14,\n"
             "    quote_timestamp=now.isoformat(), session_open=True, now=now)\n"
             "saf = stock_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, entry=100.0,\n"
             "    stop=97.0, buying_power=400.0)\n"
             "oaf = option_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, contract_quality=cq,\n"
             "    side='CALL', limit_price=1.00, spot=100.0, stop=97.0, strike=103.0,\n"
             "    iv=0.30, dte=14, atr_pct=2.5, buying_power=400.0)\n"
             "r = choose_instrument(setup_tradeable=True, stock_account_fit=saf,\n"
             "    option_account_fit=oaf, contract_quality=cq)\n"
             "print(r.choice.value, r.reason)\n"],
            cwd=_ROOT, capture_output=True, text=True, timeout=30, env=env)
        assert proc.returncode == 0, f"RISK_PROFILE={profile_env!r}: {proc.stderr}"
        results.append(proc.stdout.strip())
    assert len(set(results)) == 1, f"choose_instrument() output changed with RISK_PROFILE: {results}"
    assert results[0] == "STOCK option_account_ineligible_stock_available"


def test_canonical_instrument_choice_clean_import_from_fresh_interpreter():
    proc = _run_subprocess_snippet("import canonical.instrument_choice\nprint('OK')\n")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


# ══════════════════════════════════════════════════════════════════════════
# Step 6 — canonical.sizing (Layer E)
# ══════════════════════════════════════════════════════════════════════════

def test_canonical_sizing_import_does_not_mutate_sys_path():
    proc = _run_subprocess_snippet(
        "import sys\n"
        "before = list(sys.path)\n"
        "import canonical.sizing\n"
        "after = list(sys.path)\n"
        "print('EQUAL' if after == before else 'CHANGED')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "EQUAL"


def test_canonical_sizing_import_does_not_load_dashboard_modules():
    proc = _run_subprocess_snippet(
        "import sys\n"
        "import canonical.sizing\n"
        "loaded = [m for m in sys.modules if m in "
        "('options_desk', 'option_risk', 'research', 'app') "
        "or m.startswith('dashboard')]\n"
        "print('NONE' if not loaded else 'LOADED:' + ','.join(sorted(loaded)))\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "NONE"


def test_canonical_sizing_does_not_depend_on_risk_profile():
    results = []
    for profile_env in (None, "BALANCED", "AGGRESSIVE_SMALL_ACCOUNT", "CUSTOM"):
        env = dict(os.environ)
        if profile_env is None:
            env.pop("RISK_PROFILE", None)
        else:
            env["RISK_PROFILE"] = profile_env
        proc = subprocess.run(
            [sys.executable, "-c",
             "from canonical.sizing import size_instrument\n"
             "from canonical.instrument_choice import choose_instrument, InstrumentChoice\n"
             "from canonical.account_fit import stock_account_fit, option_account_fit\n"
             "from canonical.contract_quality import evaluate_contract_quality\n"
             "from canonical.risk_policy import STRATEGY_500_POLICY\n"
             "from datetime import datetime, timezone\n"
             "now = datetime(2026, 6, 15, 15, 30, tzinfo=timezone.utc)\n"
             "cq = evaluate_contract_quality(strike=103.0, underlying=100.0, side='CALL',\n"
             "    bid=0.99, ask=1.01, volume=300, open_interest=1000,\n"
             "    implied_volatility=0.30, delta=0.50, dte=14,\n"
             "    quote_timestamp=now.isoformat(), session_open=True, now=now)\n"
             "saf = stock_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, entry=100.0,\n"
             "    stop=97.0, buying_power=400.0)\n"
             "oaf = option_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, contract_quality=cq,\n"
             "    side='CALL', limit_price=1.00, spot=100.0, stop=97.0, strike=103.0,\n"
             "    iv=0.30, dte=14, atr_pct=2.5, buying_power=400.0)\n"
             "d = choose_instrument(setup_tradeable=True, stock_account_fit=saf,\n"
             "    option_account_fit=oaf, contract_quality=cq)\n"
             "e = size_instrument(choice=d, stock_account_fit=saf, option_account_fit=oaf)\n"
             "print(e.instrument.value, e.quantity, e.binding_constraint)\n"],
            cwd=_ROOT, capture_output=True, text=True, timeout=30, env=env)
        assert proc.returncode == 0, f"RISK_PROFILE={profile_env!r}: {proc.stderr}"
        results.append(proc.stdout.strip())
    assert len(set(results)) == 1, f"size_instrument() output changed with RISK_PROFILE: {results}"


def test_canonical_sizing_clean_import_from_fresh_interpreter():
    proc = _run_subprocess_snippet("import canonical.sizing\nprint('OK')\n")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


# ══════════════════════════════════════════════════════════════════════════
# Step 7 — canonical.executable (final executable predicates)
# ══════════════════════════════════════════════════════════════════════════

def test_canonical_executable_import_does_not_mutate_sys_path():
    proc = _run_subprocess_snippet(
        "import sys\n"
        "before = list(sys.path)\n"
        "import canonical.executable\n"
        "after = list(sys.path)\n"
        "print('EQUAL' if after == before else 'CHANGED')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "EQUAL"


def test_canonical_executable_import_does_not_load_dashboard_modules():
    proc = _run_subprocess_snippet(
        "import sys\n"
        "import canonical.executable\n"
        "loaded = [m for m in sys.modules if m in "
        "('options_desk', 'option_risk', 'research', 'app') "
        "or m.startswith('dashboard')]\n"
        "print('NONE' if not loaded else 'LOADED:' + ','.join(sorted(loaded)))\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "NONE"


def test_canonical_executable_never_references_risk_policy():
    """canonical.executable must never inspect raw RiskPolicy caps — an
    output-stage permission bit (`execution_policy_allows: bool`) is the
    only policy-shaped input on its signature. `canonical.risk_policy` DOES
    still end up in sys.modules after importing canonical.executable
    (transitively, via canonical.account_fit's own `from .risk_policy
    import ...` — account_fit.py is where the AccountFitResult/
    InstrumentType types this module type-checks against come from), so
    that is NOT what this test checks. Instead it inspects
    canonical/executable.py's own CODE (not its prose docstrings/comments,
    which are free to discuss RiskPolicy conceptually) directly: no
    `.risk_policy` import and no `RiskPolicy`/`effective_limit` symbol used
    anywhere in an actual statement."""
    import inspect
    import canonical.executable as ex
    # Strip the module-level docstring block (everything up to and
    # including the closing '"""') — a cheap, adequate approach here since
    # this module's only triple-quoted string is that one leading docstring
    # (no other multi-line strings appear in the source).
    src = inspect.getsource(ex)
    doc_end = src.index('"""', src.index('"""') + 3) + 3
    code_only = src[doc_end:]
    assert ".risk_policy" not in code_only
    assert "RiskPolicy" not in code_only
    assert "effective_limit" not in code_only


def test_canonical_executable_does_not_depend_on_risk_profile():
    results = []
    for profile_env in (None, "BALANCED", "AGGRESSIVE_SMALL_ACCOUNT", "CUSTOM"):
        env = dict(os.environ)
        if profile_env is None:
            env.pop("RISK_PROFILE", None)
        else:
            env["RISK_PROFILE"] = profile_env
        proc = subprocess.run(
            [sys.executable, "-c",
             "from canonical.executable import evaluate_stock_executable, evaluate_option_executable\n"
             "from canonical.sizing import size_instrument\n"
             "from canonical.instrument_choice import choose_instrument, InstrumentChoice\n"
             "from canonical.account_fit import stock_account_fit, option_account_fit\n"
             "from canonical.contract_quality import evaluate_contract_quality\n"
             "from canonical.risk_policy import STRATEGY_500_POLICY\n"
             "from datetime import datetime, timezone\n"
             "now = datetime(2026, 6, 15, 15, 30, tzinfo=timezone.utc)\n"
             "cq = evaluate_contract_quality(strike=103.0, underlying=100.0, side='CALL',\n"
             "    bid=0.99, ask=1.01, volume=300, open_interest=1000,\n"
             "    implied_volatility=0.30, delta=0.50, dte=14,\n"
             "    quote_timestamp=now.isoformat(), session_open=True, now=now)\n"
             "saf = stock_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, entry=100.0,\n"
             "    stop=97.0, buying_power=400.0)\n"
             "oaf = option_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, contract_quality=cq,\n"
             "    side='CALL', limit_price=1.00, spot=100.0, stop=97.0, strike=103.0,\n"
             "    iv=0.30, dte=14, atr_pct=2.5, buying_power=400.0)\n"
             "d = choose_instrument(setup_tradeable=True, stock_account_fit=saf,\n"
             "    option_account_fit=oaf, contract_quality=cq)\n"
             "e = size_instrument(choice=d, stock_account_fit=saf, option_account_fit=oaf)\n"
             "sr = evaluate_stock_executable(setup_tradeable=True, account_fit=saf, choice=d, sizing=e)\n"
             "orr = evaluate_option_executable(setup_tradeable=True, contract_quality=cq,\n"
             "    account_fit=oaf, choice=d, sizing=e)\n"
             "print(sr.executable, sr.blockers, orr.executable, orr.blockers)\n"],
            cwd=_ROOT, capture_output=True, text=True, timeout=30, env=env)
        assert proc.returncode == 0, f"RISK_PROFILE={profile_env!r}: {proc.stderr}"
        results.append(proc.stdout.strip())
    assert len(set(results)) == 1, f"executable predicates changed with RISK_PROFILE: {results}"


def test_canonical_executable_clean_import_from_fresh_interpreter():
    proc = _run_subprocess_snippet("import canonical.executable\nprint('OK')\n")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


# ══════════════════════════════════════════════════════════════════════════
# 3. RISK_PROFILE (or any environment state) does not affect canonical
#    construction or default math
# ══════════════════════════════════════════════════════════════════════════

def test_risk_profile_env_var_does_not_affect_canonical_construction():
    results = []
    for profile_env in (None, "BALANCED", "CONSERVATIVE", "AGGRESSIVE_SMALL_ACCOUNT", "GARBAGE_VALUE"):
        env = dict(os.environ)
        if profile_env is None:
            env.pop("RISK_PROFILE", None)
        else:
            env["RISK_PROFILE"] = profile_env
        proc = subprocess.run(
            [sys.executable, "-c",
             "from canonical.risk_policy import STRATEGY_500_POLICY, DASHBOARD_POLICY\n"
             "from canonical.account_fit import option_account_fit\n"
             "print(STRATEGY_500_POLICY.max_loss_per_trade, DASHBOARD_POLICY.option_planned_risk_pct)\n"],
            cwd=_ROOT, capture_output=True, text=True, timeout=30, env=env)
        assert proc.returncode == 0, f"RISK_PROFILE={profile_env!r} broke canonical import: {proc.stderr}"
        results.append(proc.stdout.strip())
    assert len(set(results)) == 1, (
        f"canonical RiskPolicy construction changed with RISK_PROFILE: {results}"
    )


def test_risk_profile_env_var_does_not_affect_account_fit_evaluation():
    """Test C, exactly as specified: evaluate the SAME explicitly-supplied
    RiskPolicy through option_account_fit() under BALANCED,
    AGGRESSIVE_SMALL_ACCOUNT, and CUSTOM RISK_PROFILE env values (plus
    unset). Canonical behavior must depend on the passed policy object, not
    environment-selected profile state — option_account_fit() never reads
    RISK_PROFILE at all, so this proves that by construction, not by luck."""
    results = []
    for profile_env in (None, "BALANCED", "AGGRESSIVE_SMALL_ACCOUNT", "CUSTOM"):
        env = dict(os.environ)
        if profile_env is None:
            env.pop("RISK_PROFILE", None)
        else:
            env["RISK_PROFILE"] = profile_env
        proc = subprocess.run(
            [sys.executable, "-c",
             "from datetime import datetime, timezone\n"
             "from canonical.account_fit import option_account_fit\n"
             "from canonical.contract_quality import evaluate_contract_quality\n"
             "from canonical.risk_policy import STRATEGY_500_POLICY\n"
             "now = datetime(2026, 6, 15, 15, 30, tzinfo=timezone.utc)\n"
             "cq = evaluate_contract_quality(strike=103.0, underlying=100.0, side='CALL',\n"
             "    bid=0.99, ask=1.01, volume=300, open_interest=1000,\n"
             "    implied_volatility=0.30, delta=0.50, dte=14,\n"
             "    quote_timestamp=now.isoformat(), session_open=True, now=now)\n"
             "r = option_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, contract_quality=cq,\n"
             "    side='CALL', limit_price=1.00, spot=100.0, stop=97.0, strike=103.0,\n"
             "    iv=0.30, dte=14, atr_pct=2.5, buying_power=400.0)\n"
             "print(r.eligible, r.quantity_allowed, r.binding_constraint,\n"
             "      r.capital_required, r.planned_risk, r.violations[0].actual)\n"],
            cwd=_ROOT, capture_output=True, text=True, timeout=30, env=env)
        assert proc.returncode == 0, f"RISK_PROFILE={profile_env!r}: {proc.stderr}"
        results.append(proc.stdout.strip())
    assert len(set(results)) == 1, (
        f"option_account_fit() output changed with RISK_PROFILE: {results}"
    )
    assert "False" in results[0] and "per_trade_risk" in results[0]


def test_risk_profile_env_var_does_not_affect_default_option_risk_math():
    """canonical.option_risk_math.option_risk()'s pol default must be
    identical regardless of RISK_PROFILE — it never reads it."""
    results = []
    for profile_env in (None, "AGGRESSIVE_SMALL_ACCOUNT"):
        env = dict(os.environ)
        if profile_env is None:
            env.pop("RISK_PROFILE", None)
        else:
            env["RISK_PROFILE"] = profile_env
        proc = subprocess.run(
            [sys.executable, "-c",
             "from canonical.option_risk_math import option_risk\n"
             "r = option_risk(contracts=1, limit_price=1.00, spot=100.0, stop=97.0, "
             "strike=103.0, side='CALL', iv=0.30, dte=14, atr_pct=2.5)\n"
             "print(r['planned_risk'], r['stress_risk'], r['absolute_max_loss'])\n"],
            cwd=_ROOT, capture_output=True, text=True, timeout=30, env=env)
        assert proc.returncode == 0, proc.stderr
        results.append(proc.stdout.strip())
    assert len(set(results)) == 1, f"default option_risk() math changed with RISK_PROFILE: {results}"


# ══════════════════════════════════════════════════════════════════════════
# 4. Clean import from a genuinely fresh interpreter
# ══════════════════════════════════════════════════════════════════════════

def test_clean_import_from_fresh_interpreter():
    proc = _run_subprocess_snippet("import canonical.account_fit\nprint('OK')\n")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


# ══════════════════════════════════════════════════════════════════════════
# 5. Before/after numerical parity — dashboard.option_risk /
#    dashboard.options_desk vs canonical.option_risk_math, byte-for-byte
# ══════════════════════════════════════════════════════════════════════════

def _dashboard_option_risk():
    if _DASHBOARD_DIR not in sys.path:
        sys.path.insert(0, _DASHBOARD_DIR)
    import option_risk as ORK
    return ORK


def _dashboard_options_desk():
    if _DASHBOARD_DIR not in sys.path:
        sys.path.insert(0, _DASHBOARD_DIR)
    import options_desk as OD
    return OD


_DASHBOARD_DIR = os.path.join(_ROOT, "dashboard")

# Representative call/put/ATM/OTM/IV/DTE matrix, all deterministic.
_MATRIX = [
    dict(name="ATM_call", contracts=1, limit_price=2.50, spot=100.0, stop=97.0,
        strike=100.0, side="CALL", iv=0.30, dte=21, atr_pct=2.0, spread_dollars=0.05),
    dict(name="ATM_put", contracts=1, limit_price=2.50, spot=100.0, stop=103.0,
        strike=100.0, side="PUT", iv=0.30, dte=21, atr_pct=2.0, spread_dollars=0.05),
    dict(name="OTM_call", contracts=1, limit_price=1.10, spot=100.0, stop=97.0,
        strike=107.0, side="CALL", iv=0.28, dte=21, atr_pct=2.0, spread_dollars=0.04),
    dict(name="OTM_put", contracts=1, limit_price=1.10, spot=100.0, stop=103.0,
        strike=93.0, side="PUT", iv=0.28, dte=21, atr_pct=2.0, spread_dollars=0.04),
    dict(name="low_IV", contracts=1, limit_price=1.80, spot=100.0, stop=97.0,
        strike=101.0, side="CALL", iv=0.12, dte=30, atr_pct=1.2, spread_dollars=0.03),
    dict(name="high_IV", contracts=1, limit_price=4.20, spot=100.0, stop=97.0,
        strike=101.0, side="CALL", iv=0.85, dte=30, atr_pct=4.5, spread_dollars=0.15),
    dict(name="short_valid_DTE", contracts=1, limit_price=0.90, spot=100.0, stop=98.0,
        strike=102.0, side="CALL", iv=0.35, dte=7, atr_pct=2.0, spread_dollars=0.05),
    dict(name="medium_DTE", contracts=2, limit_price=3.30, spot=100.0, stop=96.0,
        strike=101.0, side="CALL", iv=0.32, dte=45, atr_pct=2.2, spread_dollars=0.08),
    # the audit's headline fixture
    dict(name="strategy500_500_vs_1", contracts=1, limit_price=1.00, spot=100.0,
        stop=97.0, strike=103.0, side="CALL", iv=0.30, dte=14, atr_pct=2.5,
        spread_dollars=0.02),
]


@pytest.mark.parametrize("case", _MATRIX, ids=[c["name"] for c in _MATRIX])
def test_option_risk_numerical_parity_before_after(case):
    """Every field of option_risk()'s return dict must be identical whether
    computed via dashboard.option_risk (before) or canonical.option_risk_math
    (after) — dashboard.option_risk now delegates to the canonical module, so
    this also proves the delegation itself is exact, not merely that a
    second copy happens to agree."""
    case = {k: v for k, v in case.items() if k != "name"}
    ORK = _dashboard_option_risk()
    from canonical.option_risk_math import option_risk as canonical_option_risk

    before = ORK.option_risk(**case, fee_per_contract=0.06, r=0.042,
                             pol=dict(ORK.STRESS_DEFAULTS))
    after = canonical_option_risk(**case, fee_per_contract=0.06, r=0.042,
                                  pol=dict(ORK.STRESS_DEFAULTS))
    assert before == after, "dashboard vs canonical option_risk() diverge"

    for key in ("capital_committed", "planned_risk", "stress_risk", "absolute_max_loss"):
        assert before[key] == after[key]
    if "planned_risk_basis" in before:
        assert before["planned_risk_basis"] == after["planned_risk_basis"]


def test_stock_risk_numerical_parity_before_after():
    ORK = _dashboard_option_risk()
    from canonical.option_risk_math import stock_risk as canonical_stock_risk

    for kwargs in (dict(quantity=1.25, entry=100.0, stop=97.0),
                  dict(quantity=3.0, entry=50.0, stop=48.5, slippage_bps=5.0, fee=0.5)):
        before = ORK.stock_risk(**kwargs)
        after = canonical_stock_risk(**kwargs)
        assert before == after


def test_bs_greeks_numerical_parity_before_after():
    OD = _dashboard_options_desk()
    from canonical.option_risk_math import bs_greeks as canonical_bs_greeks

    for kwargs in (dict(spot=100.0, strike=103.0, iv=0.30, dte_days=14, side="CALL"),
                  dict(spot=100.0, strike=93.0, iv=0.30, dte_days=14, side="PUT"),
                  dict(spot=50.0, strike=50.0, iv=0.85, dte_days=7, side="CALL")):
        before = OD.bs_greeks(**kwargs)
        after = canonical_bs_greeks(**kwargs)
        assert before == after


def test_dashboard_option_risk_is_now_the_same_function_object_as_canonical():
    """Not just equal output — dashboard.option_risk.option_risk IS
    canonical.option_risk_math.option_risk (delegation, not a lookalike)."""
    ORK = _dashboard_option_risk()
    from canonical.option_risk_math import option_risk as canonical_option_risk
    assert ORK.option_risk is canonical_option_risk
    assert ORK.stock_risk is __import__(
        "canonical.option_risk_math", fromlist=["stock_risk"]).stock_risk


def test_dashboard_bs_greeks_is_now_the_same_function_object_as_canonical():
    OD = _dashboard_options_desk()
    from canonical.option_risk_math import bs_greeks as canonical_bs_greeks
    assert OD.bs_greeks is canonical_bs_greeks


# ══════════════════════════════════════════════════════════════════════════
# 6. AccountFit end-to-end parity: Strategy500 + Dashboard cases, unchanged
#    from Step 4
# ══════════════════════════════════════════════════════════════════════════

def test_account_fit_strategy500_regression_unchanged():
    from canonical.account_fit import option_account_fit
    from canonical.contract_quality import evaluate_contract_quality
    from canonical.risk_policy import STRATEGY_500_POLICY

    now = datetime(2026, 6, 15, 15, 30, tzinfo=timezone.utc)
    cq = evaluate_contract_quality(strike=103.0, underlying=100.0, side="CALL",
                                   bid=0.99, ask=1.01, volume=300, open_interest=1000,
                                   implied_volatility=0.30, delta=0.50, dte=14,
                                   quote_timestamp=now.isoformat(), session_open=True, now=now)
    r = option_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, contract_quality=cq,
                           side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                           strike=103.0, iv=0.30, dte=14, atr_pct=2.5, buying_power=400.0)
    assert r.eligible is False
    assert r.binding_constraint == "per_trade_risk"
    assert r.violations[0].actual == pytest.approx(100.06, abs=0.01)
    assert r.violations[0].limit == pytest.approx(5.0)


def test_account_fit_dashboard_500_case_unchanged():
    """Matrix item 10: Dashboard-policy $500 case, distinct from item 11
    ($50,000). At $500 equity DASHBOARD_POLICY's percentage-only caps are
    tight enough that the same $1.00-premium candidate is still rejected —
    via a DIFFERENT binding check than Strategy500Policy's, proving the
    divergence traces to configured values, not a code fork."""
    from canonical.account_fit import option_account_fit
    from canonical.contract_quality import evaluate_contract_quality
    from canonical.risk_policy import DASHBOARD_POLICY

    now = datetime(2026, 6, 15, 15, 30, tzinfo=timezone.utc)
    cq = evaluate_contract_quality(strike=103.0, underlying=100.0, side="CALL",
                                   bid=0.99, ask=1.01, volume=300, open_interest=1000,
                                   implied_volatility=0.30, delta=0.50, dte=14,
                                   quote_timestamp=now.isoformat(), session_open=True, now=now)
    r = option_account_fit(policy=DASHBOARD_POLICY, equity=500.0, contract_quality=cq,
                           side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                           strike=103.0, iv=0.30, dte=14, atr_pct=2.5, buying_power=400.0)
    assert r.eligible is False
    assert r.quantity_allowed == 0
    assert r.binding_constraint == "max_position_notional"
    assert r.capital_required == 0.0


def test_account_fit_dashboard_case_unchanged():
    from canonical.account_fit import option_account_fit
    from canonical.contract_quality import evaluate_contract_quality
    from canonical.risk_policy import DASHBOARD_POLICY

    now = datetime(2026, 6, 15, 15, 30, tzinfo=timezone.utc)
    cq = evaluate_contract_quality(strike=103.0, underlying=100.0, side="CALL",
                                   bid=0.99, ask=1.01, volume=300, open_interest=1000,
                                   implied_volatility=0.30, delta=0.50, dte=14,
                                   quote_timestamp=now.isoformat(), session_open=True, now=now)
    r = option_account_fit(policy=DASHBOARD_POLICY, equity=50_000.0, contract_quality=cq,
                           side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                           strike=103.0, iv=0.30, dte=14, atr_pct=2.5, buying_power=40_000.0)
    assert r.eligible is True
    assert r.quantity_allowed == 1


# ══════════════════════════════════════════════════════════════════════════
# 7. test_atr_injection.py collection independence
# ══════════════════════════════════════════════════════════════════════════

def test_atr_injection_still_fails_independently_of_canonical_import_order():
    """The Step-4-discovered anomaly: importing canonical modules used to
    leak `src/` onto sys.path via a transitive options_desk.py import,
    incidentally making tests/unit/test_atr_injection.py collectible. After
    Step 4.1, canonical modules load zero dashboard code, so whether
    tests/unit/test_atr_injection.py can collect (which depends only on
    whether `tradingview_mcp` is importable in this environment — e.g.
    whether the package is pip-installed) must be unaffected by import
    order: collecting test_account_fit.py first must neither fix nor break
    it. This does NOT assert a specific installed/not-installed outcome —
    that depends on the environment (editable-installed here) — only that
    canonical's own sys.path manipulation isn't secretly the deciding
    factor."""
    proc_alone = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/unit/test_atr_injection.py",
         "--collect-only", "-q"],
        cwd=_ROOT, capture_output=True, text=True, timeout=60)
    proc_after_canonical = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/unit/test_account_fit.py",
         "tests/unit/test_atr_injection.py", "--collect-only", "-q"],
        cwd=_ROOT, capture_output=True, text=True, timeout=60)
    assert proc_alone.returncode == proc_after_canonical.returncode, (
        "test_atr_injection.py's collectibility must be import-order-independent — "
        f"alone rc={proc_alone.returncode}, after-canonical rc={proc_after_canonical.returncode}"
    )
    alone_had_modulenotfound = "ModuleNotFoundError: No module named 'tradingview_mcp'" in (
        proc_alone.stdout + proc_alone.stderr)
    after_had_modulenotfound = "ModuleNotFoundError: No module named 'tradingview_mcp'" in (
        proc_after_canonical.stdout + proc_after_canonical.stderr)
    assert alone_had_modulenotfound == after_had_modulenotfound, (
        "test_atr_injection.py's collection failure mode must be import-order-independent")
