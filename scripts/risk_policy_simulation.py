"""RISK POLICY SIMULATION — NOT HISTORICAL BACKTEST.

Compares what the OLD option risk policy (premium == planned risk, judged against a
stock-style 1%-of-equity budget) and the CURRENT instrument-aware policy each PERMIT,
and what that does to a simulated equity path.

WHY THIS IS NOT A BACKTEST
--------------------------
The repository stores no historical option chains. Options are read live from
Robinhood/Yahoo and the shadow ledger holds a handful of rows, so there is nothing to
replay. Every contract below is SYNTHETIC, drawn from fixed broad distributions and
priced with the same Black-Scholes model the engine uses.

Read the output as "how do these policies differ in what they allow, and how does that
shape the equity path" — never as "which policy would have made more money". The
outcome model assumes a win rate; a policy that permits more trades will therefore show
more of whatever that assumption implies, in either direction. No parameter here was
tuned against a result, and none was fitted to the SHOP/PLTR/NTRA cases.

Usage:
    python scripts/risk_policy_simulation.py            # profile table + simulation
    python scripts/risk_policy_simulation.py --profiles # profile table only
    EDGE_SHIFT=-0.12 python scripts/risk_policy_simulation.py   # adverse-edge stress
"""
from __future__ import annotations

import os
import random
import statistics
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "dashboard"))

import option_risk as ORK          # noqa: E402
import options_desk as OD          # noqa: E402

BANNER = "RISK POLICY SIMULATION — NOT HISTORICAL BACKTEST"
SEED = 20260807
DEFAULT_EQUITY = 447.94
TRADES_PER_RUN = 60
RUNS = 300
CFG = OD.config()
POLICIES = ("OLD", "CONSERVATIVE", "BALANCED", "CUSTOM", "AGGRESSIVE_SMALL_ACCOUNT")


def live_equity() -> tuple:
    """Prefer the real broker value; fall back to the last observed one and say so."""
    try:
        a = OD.account()
        v = a.get("portfolio_value") or a.get("buying_power")
        if v:
            return float(v), "live Robinhood account (read-only)"
    except Exception:                                          # noqa: BLE001
        pass
    return DEFAULT_EQUITY, f"fallback — last observed broker value ${DEFAULT_EQUITY}"


# ── synthetic population ─────────────────────────────────────────────────────

def draw(rng):
    spot = rng.uniform(12.0, 380.0)
    stop_pct = rng.uniform(3.0, 10.0)
    stop = spot * (1 - stop_pct / 100.0)
    rr = rng.uniform(1.8, 3.5)
    target = spot + (spot - stop) * rr
    score = rng.uniform(55.0, 95.0)
    atr_pct = rng.uniform(1.2, 5.0)
    dte = rng.choice([7, 14, 21, 30, 45])
    iv = rng.uniform(0.20, 0.80)
    otm = rng.uniform(-6.0, 7.0)
    strike = round(spot * (1 + otm / 100.0), 1)
    g = OD.bs_greeks(spot, strike, iv, dte, "CALL", CFG["risk_free_rate"])
    if not g or g["theoretical_price"] <= 0.05:
        return None
    mid = g["theoretical_price"]
    spread_pct = rng.uniform(1.0, 12.0)
    return dict(spot=spot, stop=stop, target=target, rr=rr, score=score,
                atr_pct=atr_pct, dte=dte, iv=iv, strike=strike,
                limit=round(mid * (1 + spread_pct / 200.0), 2),
                spread_pct=spread_pct,
                spread_dollars=round(mid * spread_pct / 100, 2))


def win_probability(score):
    """Modest and monotone in setup score. Deliberately unflattering — assuming a
    strong edge would make any permissive policy look good."""
    edge = float(os.environ.get("EDGE_SHIFT", "0.0"))
    return max(0.05, 0.35 + (score - 55.0) / 40.0 * 0.15 + edge)


def option_exit_value(d, underlying, days_used):
    v = ORK.reprice(underlying=underlying, strike=d["strike"], iv=d["iv"],
                    dte_remaining=max(0.0, d["dte"] - days_used), side="CALL",
                    r=CFG["risk_free_rate"])
    return max(0.0, v["value"] * 100 - (d["spread_dollars"] / 2.0) * 100)


def old_policy_option(d, equity):
    per = d["limit"] * 100 + CFG["fee_per_contract"]
    cap = min(CFG["max_premium_per_position"],
              equity * CFG["max_position_pct"] / 100.0,
              equity * CFG["max_total_exposure_pct"] / 100.0,
              equity * CFG["max_account_risk_pct"] / 100.0)
    return per <= cap, per, None


def new_policy_option(d, equity, profile, open_options):
    per = d["limit"] * 100 + CFG["fee_per_contract"]
    pol = ORK.policy(profile)
    risk = ORK.option_risk(contracts=1, limit_price=d["limit"], spot=d["spot"],
                           stop=d["stop"], strike=d["strike"], side="CALL",
                           iv=d["iv"], dte=d["dte"], atr_pct=d["atr_pct"],
                           spread_dollars=d["spread_dollars"],
                           fee_per_contract=CFG["fee_per_contract"],
                           r=CFG["risk_free_rate"], pol=pol)
    pchk = ORK.portfolio_check(equity=equity, open_options=open_options,
                               candidate_risk=risk, remaining_buying_power=equity,
                               pol=pol)
    elig = ORK.contract_eligibility(risk=risk, equity=equity, pol=pol,
                                    quality=ORK.quality_tier(d["score"]),
                                    portfolio=pchk)
    return elig["eligible"], per, risk


def stock_leg(d, equity):
    rps = d["spot"] - d["stop"]
    qty = min(equity * CFG["max_account_risk_pct"] / 100.0 / rps,
              equity * CFG["max_position_pct"] / 100.0 / d["spot"])
    return round(qty, 4), round(qty * d["spot"], 2), round(qty * rps, 2)


def run_one(policy_name, rng, start_equity):
    equity = peak = start_equity
    max_dd = 0.0
    stock_trades = option_trades = 0
    capitals, planned, realized, pnls, premiums = [], [], [], [], []
    open_options = []

    for _ in range(TRADES_PER_RUN):
        d = None
        while d is None:
            d = draw(rng)
        if equity < 20:
            break
        if policy_name == "OLD":
            ok, per, orisk = old_policy_option(d, equity)
        else:
            ok, per, orisk = new_policy_option(d, equity, policy_name, open_options)

        won = rng.random() < win_probability(d["score"])
        exit_px = d["target"] if won else d["stop"]
        t = ORK.days_to_invalidation(spot=d["spot"], stop=exit_px,
                                     atr_pct=d["atr_pct"], dte=d["dte"])
        days_used = t["days"] or d["dte"]

        if ok:
            option_trades += 1
            cost = d["limit"] * 100 + CFG["fee_per_contract"]
            pnl = option_exit_value(d, exit_px, days_used) - cost
            capitals.append(cost)
            premiums.append(cost)
            planned.append(orisk["planned_risk"] if orisk else cost)
            open_options.append({"capital_committed": cost,
                                 "stress_risk": orisk["stress_risk"] if orisk else cost,
                                 "absolute_max_loss": cost})
            if len(open_options) > 2:
                open_options.pop(0)
        else:
            qty, notional, plan = stock_leg(d, equity)
            if qty <= 0:
                continue
            stock_trades += 1
            pnl = qty * (exit_px - d["spot"])
            capitals.append(notional)
            planned.append(plan)

        equity += pnl
        pnls.append(pnl)
        if pnl < 0:
            realized.append(-pnl)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100 if peak else 0.0)

    mean = statistics.fmean
    return {"stock_trades": stock_trades, "option_trades": option_trades,
            "avg_capital": mean(capitals) if capitals else 0.0,
            "avg_planned_loss": mean(planned) if planned else 0.0,
            "avg_realized_loss": mean(realized) if realized else 0.0,
            "worst_loss": max(realized) if realized else 0.0,
            "max_drawdown_pct": max_dd,
            "expectancy": mean(pnls) if pnls else 0.0,
            "total_return_pct": (equity - start_equity) / start_equity * 100,
            "avg_premium": mean(premiums) if premiums else 0.0,
            "peak_premium": max(premiums) if premiums else 0.0,
            "ruined": equity < start_equity * 0.5}


def aggregate(name, start_equity):
    rng = random.Random(SEED)                 # identical population for every policy
    rows = [run_one(name, rng, start_equity) for _ in range(RUNS)]
    avg = lambda k: statistics.fmean(r[k] for r in rows)        # noqa: E731
    return {"policy": name,
            "stock_trades": avg("stock_trades"), "option_trades": avg("option_trades"),
            "avg_capital": avg("avg_capital"),
            "avg_planned_loss": avg("avg_planned_loss"),
            "avg_realized_loss": avg("avg_realized_loss"),
            "worst_loss": max(r["worst_loss"] for r in rows),
            "max_drawdown_pct": avg("max_drawdown_pct"),
            "median_total_return_pct": statistics.median(r["total_return_pct"] for r in rows),
            "expectancy": avg("expectancy"), "avg_premium": avg("avg_premium"),
            "peak_premium": max(r["peak_premium"] for r in rows),
            "ruin_rate_pct": sum(r["ruined"] for r in rows) / len(rows) * 100}


COLUMNS = (("policy", "policy", 24), ("stock_trades", "stock tr", 10),
           ("option_trades", "opt tr", 9), ("avg_capital", "avg cap", 10),
           ("avg_planned_loss", "avg planL", 11), ("avg_realized_loss", "avg realL", 11),
           ("worst_loss", "worst L", 10), ("max_drawdown_pct", "avg maxDD%", 11),
           ("median_total_return_pct", "med ret%", 10), ("expectancy", "expectancy", 11),
           ("avg_premium", "avg prem", 10), ("peak_premium", "peak prem", 11),
           ("ruin_rate_pct", "ruin%", 8))


def main() -> int:
    equity, source = live_equity()
    print("=" * 100)
    print(BANNER)
    print("=" * 100)
    print(ORK.render_profile_table(equity))
    print(f"\nequity source: {source}")
    if "--profiles" in sys.argv:
        return 0

    print("\n" + "=" * 100)
    print(f"{BANNER}\n{RUNS} runs x {TRADES_PER_RUN} trades | seed {SEED} | "
          f"start ${equity:,.2f} | identical synthetic population per policy")
    shift = os.environ.get("EDGE_SHIFT")
    if shift:
        print(f"ADVERSE-EDGE STRESS: win rate shifted by {float(shift):+.0%}")
    print("=" * 100)
    print("".join(f"{lbl:>{w}s}" for _, lbl, w in COLUMNS))
    print("-" * sum(w for _, _, w in COLUMNS))
    for name in POLICIES:
        r = aggregate(name, equity)
        print("".join(f"{r[k]:>{w}s}" if isinstance(r[k], str) else f"{r[k]:>{w}.2f}"
                      for k, _, w in COLUMNS))
    print("\nReminder: option-trade COUNTS and LOSS magnitudes are what this measures.")
    print("Returns reflect the assumed win rate, not evidence of edge — a policy that")
    print("permits more trades amplifies that assumption in BOTH directions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
