"""Phases 7-9 — option chains, the shares-versus-option decision, and sizing.

READ-ONLY. Nothing in this module can place, preview, queue or simulate an order.
It calls Robinhood exclusively through `robinhood_mcp.call_tool`, which refuses
anything outside its read allowlist while `ROBINHOOD_TRADING_ENABLED` is false.

Provider hierarchy, and why it matters:

  * Robinhood (primary for CONTRACTS) returns bid/ask with sizes, mark, delta,
    gamma, theta, vega, rho, implied volatility, open interest, volume, the tick
    size, the contract multiplier, a broker-side `chance_of_profit_long`, and a
    real `updated_at` quote timestamp. If you are going to trade the contract at
    Robinhood, this is the book you will actually be filled against.
  * Yahoo (fallback) returns bid/ask/last/volume/open interest/IV and NO Greeks
    and NO quote timestamp. When the desk runs on Yahoo, Greeks are absent and
    every calculation that needs one is reported as unavailable — or, if the
    caller opts in, computed from Black-Scholes and labelled `model` with its
    inputs, never presented as an observed value.

Three separate provenance classes appear throughout and are never blended:
`observed` (a provider sent it), `calculated` (arithmetic on observed values) and
`model` (Black-Scholes or a scenario assumption).
"""
from __future__ import annotations

import math
import os
import sys
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in ("..", os.path.join("..", "lab"), os.path.join("..", "src")):
    sys.path.insert(0, os.path.join(_HERE, _p))
sys.path.insert(0, _HERE)

import research as _R
import option_risk as option_risk_mod


# ── Configuration ────────────────────────────────────────────────────────────

def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def config() -> Dict[str, Any]:
    return {
        # contract liquidity gates
        "max_spread_pct": _f("OPT_MAX_SPREAD_PCT", 12.0),
        "max_spread_dollars": _f("OPT_MAX_SPREAD_DOLLARS", 0.60),
        "min_open_interest": _f("OPT_MIN_OI", 250),
        "min_volume": _f("OPT_MIN_VOLUME", 10),
        "max_quote_age_hours": _f("OPT_MAX_QUOTE_AGE_H", 30.0),
        "max_otm_pct": _f("OPT_MAX_OTM_PCT", 7.0),
        "max_theta_pct_per_day": _f("OPT_MAX_THETA_PCT_DAY", 3.0),
        "min_dte": _f("OPT_MIN_DTE", 7),
        "max_dte": _f("OPT_MAX_DTE", 90),
        # costs
        "fee_per_contract": _f("OPT_FEE_PER_CONTRACT", 0.06),   # regulatory (ORF/OCC/TAF-ish)
        "equity_fee": _f("EQUITY_FEE", 0.0),
        "equity_slippage_bps": _f("EQUITY_SLIPPAGE_BPS", 3.0),
        # risk / sizing (Phase 9) — conservative defaults, all overridable
        "max_account_risk_pct": _f("SIZE_MAX_ACCOUNT_RISK_PCT", 1.0),
        "max_risk_dollars": _f("SIZE_MAX_RISK_DOLLARS", 0.0),   # 0 = no absolute cap
        "max_premium_per_position": _f("SIZE_MAX_PREMIUM", 300.0),
        "max_position_pct": _f("SIZE_MAX_POSITION_PCT", 20.0),
        "max_total_exposure_pct": _f("SIZE_MAX_TOTAL_EXPOSURE_PCT", 60.0),
        "max_concurrent_positions": int(_f("SIZE_MAX_POSITIONS", 5)),
        "max_positions_per_sector": int(_f("SIZE_MAX_PER_SECTOR", 2)),
        "max_daily_loss_pct": _f("SIZE_MAX_DAILY_LOSS_PCT", 3.0),
        "min_reward_risk": _f("SIZE_MIN_RR", 1.8),
        "fractional_shares": os.environ.get("SIZE_FRACTIONAL", "true").lower() == "true",
        # capability
        "spreads_allowed": os.environ.get("OPT_SPREADS_ALLOWED", "false").lower() == "true",
        "allow_model_greeks": os.environ.get("OPT_MODEL_GREEKS", "true").lower() == "true",
        "risk_free_rate": _f("OPT_RISK_FREE_RATE", 0.042),
    }


def _num(x) -> Optional[float]:
    """Robinhood sends money and Greeks as STRINGS."""
    if x is None:
        return None
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


# ── Black-Scholes (MODEL — only used when a provider gives no Greeks) ────────

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_greeks(spot: float, strike: float, iv: float, dte_days: float, side: str,
              r: float = 0.042) -> Optional[Dict[str, Any]]:
    """Black-Scholes Greeks. Returns None unless every input is usable. The result
    is tagged `provenance: model` by the caller and must never be shown as observed."""
    if not (spot and strike and iv and dte_days) or spot <= 0 or strike <= 0 or iv <= 0 or dte_days <= 0:
        return None
    T = dte_days / 365.0
    try:
        d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
        d2 = d1 - iv * math.sqrt(T)
    except (ValueError, ZeroDivisionError):
        return None
    disc = math.exp(-r * T)
    call = side.upper().startswith("C")
    delta = _norm_cdf(d1) if call else _norm_cdf(d1) - 1.0
    gamma = _norm_pdf(d1) / (spot * iv * math.sqrt(T))
    vega = spot * _norm_pdf(d1) * math.sqrt(T) / 100.0            # per 1 vol point
    theta_year = (-spot * _norm_pdf(d1) * iv / (2 * math.sqrt(T))
                  + (-r * strike * disc * _norm_cdf(d2) if call
                     else r * strike * disc * _norm_cdf(-d2)))
    price = (spot * _norm_cdf(d1) - strike * disc * _norm_cdf(d2)) if call else \
            (strike * disc * _norm_cdf(-d2) - spot * _norm_cdf(-d1))
    return {"delta": round(delta, 4), "gamma": round(gamma, 6),
            "theta": round(theta_year / 365.0, 4), "vega": round(vega, 4),
            "theoretical_price": round(price, 4),
            "provenance": "model", "model": "black_scholes",
            "inputs": {"spot": spot, "strike": strike, "iv": iv,
                       "dte_days": dte_days, "risk_free_rate": r}}


# ── Robinhood chain ──────────────────────────────────────────────────────────

def _unwrap(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    if raw.get("isError"):
        return {"__error": str((raw.get("_meta") or {}).get("rh_error_category") or "error")}
    sc = raw.get("structuredContent")
    if isinstance(sc, dict):
        return sc.get("data") if isinstance(sc.get("data"), dict) else sc
    return raw


def rh_available() -> Dict[str, Any]:
    try:
        import robinhood_mcp as rh
        st = rh.status()
        return {"connected": bool(st.get("connected")), "status": st.get("status"),
                "read_only": True, "trading_enabled": bool(st.get("trading_enabled")),
                "reason": st.get("reason")}
    except Exception as e:  # noqa: BLE001
        return {"connected": False, "status": "unavailable", "read_only": True,
                "trading_enabled": False, "reason": str(e)[:120]}


def rh_expirations(symbol: str) -> Dict[str, Any]:
    """Chain metadata: expirations, multiplier, tick sizes, tradability."""
    def _c():
        import robinhood_mcp as rh
        d = _unwrap(rh.call_tool("get_option_chains", {"underlying_symbol": symbol}))
        if d.get("__error"):
            return {"state": "error", "reason": d["__error"]}
        chains = d.get("chains") or []
        if not chains:
            return {"state": "unsupported", "reason": f"no option chain listed for {symbol}"}
        c = chains[0]
        return {"state": "ok", "chain_id": c.get("id"), "symbol": c.get("symbol"),
                "expirations": c.get("expiration_dates") or [],
                "multiplier": _num(c.get("trade_value_multiplier")) or 100.0,
                "min_ticks": c.get("min_ticks"),
                "can_open_position": bool(c.get("can_open_position")),
                "source": "robinhood"}
    try:
        return _R.cached(f"rhexp:{symbol}", 900, _c)
    except Exception as e:  # noqa: BLE001
        return {"state": "error", "reason": str(e)[:140]}


def rh_chain(symbol: str, expiry: str, side: str = "call",
             spot: Optional[float] = None, width_pct: float = 15.0) -> Dict[str, Any]:
    """Contracts for one expiry with full quotes, restricted to strikes within
    `width_pct` of spot (quoting the whole chain would be dozens of calls)."""
    side = "call" if side.upper().startswith("C") else "put"

    def _c():
        import robinhood_mcp as rh
        ins = _unwrap(rh.call_tool("get_option_instruments", {
            "chain_symbol": symbol, "expiration_dates": expiry,
            "type": side, "state": "active"}))
        if ins.get("__error"):
            return {"state": "error", "reason": ins["__error"]}
        rows = ins.get("instruments") or ins.get("results") or []
        if not rows:
            return {"state": "empty", "reason": f"no active {side}s for {expiry}"}
        mult = _num((rows[0] or {}).get("trade_value_multiplier")) or 100.0
        if spot:
            lo, hi = spot * (1 - width_pct / 100), spot * (1 + width_pct / 100)
            rows = [r for r in rows if lo <= (_num(r.get("strike_price")) or 0) <= hi] or rows
        rows = sorted(rows, key=lambda r: _num(r.get("strike_price")) or 0)[:40]
        by_id = {r["id"]: r for r in rows if r.get("id")}
        quotes: Dict[str, Any] = {}
        ids = list(by_id)
        for i in range(0, len(ids), 20):          # >20 drops `closes`; keep batches small
            q = _unwrap(rh.call_tool("get_option_quotes", {"instrument_ids": ids[i:i + 20]}))
            for row in (q.get("results") or []):
                qq = (row or {}).get("quote") or {}
                if qq.get("instrument_id"):
                    quotes[qq["instrument_id"]] = qq
        out = []
        for iid, inst in by_id.items():
            qq = quotes.get(iid) or {}
            out.append({
                "contract_id": iid,
                "symbol": symbol, "expiry": inst.get("expiration_date"),
                "strike": _num(inst.get("strike_price")),
                "side": (inst.get("type") or side).upper(),
                "tradability": inst.get("tradability"),
                "multiplier": _num(inst.get("trade_value_multiplier")) or mult,
                "min_ticks": inst.get("min_ticks"),
                "bid": _num(qq.get("bid_price")), "ask": _num(qq.get("ask_price")),
                "bid_size": qq.get("bid_size"), "ask_size": qq.get("ask_size"),
                "mark": _num(qq.get("adjusted_mark_price")) or _num(qq.get("mark_price")),
                "last": _num(qq.get("last_trade_price")),
                "previous_close": _num(qq.get("previous_close_price")),
                "volume": qq.get("volume"), "open_interest": qq.get("open_interest"),
                "implied_volatility": _num(qq.get("implied_volatility")),
                "delta": _num(qq.get("delta")), "gamma": _num(qq.get("gamma")),
                "theta": _num(qq.get("theta")), "vega": _num(qq.get("vega")),
                "rho": _num(qq.get("rho")),
                "broker_break_even": _num(qq.get("break_even_price")),
                "chance_of_profit_long": _num(qq.get("chance_of_profit_long")),
                "high_fill_rate_buy_price": _num(qq.get("high_fill_rate_buy_price")),
                "low_fill_rate_buy_price": _num(qq.get("low_fill_rate_buy_price")),
                "quote_timestamp": qq.get("updated_at"),
                "greeks_provenance": "observed" if qq.get("delta") is not None else None,
                "provider": "robinhood",
            })
        return {"state": "ok", "symbol": symbol, "expiry": expiry, "side": side.upper(),
                "contracts": out, "multiplier": mult, "provider": "robinhood",
                "fetched_at": datetime.now(timezone.utc).isoformat()}
    try:
        return _R.cached(f"rhchain:{symbol}:{expiry}:{side}", 120, _c)
    except Exception as e:  # noqa: BLE001
        return {"state": "error", "reason": str(e)[:140], "provider": "robinhood"}


def yahoo_chain(symbol: str, expiry: Optional[str], side: str = "call") -> Dict[str, Any]:
    """Fallback chain. NO Greeks and NO quote timestamp — both reported as missing."""
    try:
        from tradingview_mcp.core.services.options_service import get_options_chain
        ch = _R.cached(f"yopt:{symbol}:{expiry}", 120, lambda: get_options_chain(_R.to_yahoo(symbol), expiry))
    except Exception as e:  # noqa: BLE001
        return {"state": "error", "reason": str(e)[:140], "provider": "yahoo"}
    if not isinstance(ch, dict) or ch.get("error"):
        return {"state": "error", "reason": str((ch or {}).get("error"))[:140], "provider": "yahoo",
                "available_expiries": (ch or {}).get("available_expiries", [])}
    rows = ch.get("calls" if side.upper().startswith("C") else "puts") or []
    out = []
    for c in rows:
        out.append({
            "contract_id": c.get("contract_symbol"), "symbol": symbol,
            "expiry": c.get("expiration"), "strike": _num(c.get("strike")),
            "side": side.upper(), "multiplier": 100.0,
            "bid": _num(c.get("bid")), "ask": _num(c.get("ask")),
            "bid_size": None, "ask_size": None, "mark": None,
            "last": _num(c.get("last_price")),
            "volume": c.get("volume"), "open_interest": c.get("open_interest"),
            "implied_volatility": _num(c.get("implied_volatility")),
            "delta": None, "gamma": None, "theta": None, "vega": None, "rho": None,
            "chance_of_profit_long": None, "quote_timestamp": None,
            "greeks_provenance": None, "provider": "yahoo",
        })
    return {"state": "ok" if out else "empty", "symbol": symbol,
            "expiry": ch.get("requested_expiry"), "side": side.upper(),
            "contracts": out, "multiplier": 100.0, "provider": "yahoo",
            "available_expiries": ch.get("available_expiries", []),
            "underlying_price": ch.get("underlying_price"),
            "missing_capabilities": ["greeks", "quote_timestamp", "mark"],
            "fetched_at": datetime.now(timezone.utc).isoformat()}


# ── Per-contract analysis ────────────────────────────────────────────────────

def analyse_contract(c: Dict[str, Any], *, spot: float, cfg: Dict[str, Any],
                     atr_pct: Optional[float] = None,
                     target_price: Optional[float] = None,
                     stop_price: Optional[float] = None,
                     contracts: int = 1,
                     session_open: bool = True) -> Dict[str, Any]:
    """Every required calculation for one contract, each tagged with provenance."""
    out = dict(c)
    warn: List[str] = []
    missing: List[str] = []
    mult = c.get("multiplier") or 100.0
    bid, ask, strike = c.get("bid"), c.get("ask"), c.get("strike")
    call = str(c.get("side", "CALL")).upper().startswith("C")

    # --- quote sanity -------------------------------------------------------
    two_sided = bool(bid and ask and bid > 0 and ask > 0)
    out["two_sided"] = two_sided
    if not two_sided:
        warn.append("no two-sided quote" if not (bid or ask) else "zero bid — you may not be able to exit")
    mid = round((bid + ask) / 2, 4) if two_sided else None
    out["mid"] = mid
    out["spread_dollars"] = round(ask - bid, 4) if two_sided else None
    out["spread_pct"] = round((ask - bid) / mid * 100, 2) if (two_sided and mid) else None
    if mid is None:
        missing.append("mid")

    # --- DTE ----------------------------------------------------------------
    dte = None
    if c.get("expiry"):
        try:
            dte = (date.fromisoformat(str(c["expiry"])) - datetime.now(timezone.utc).date()).days
        except ValueError:
            pass
    out["dte"] = dte
    if dte is None:
        missing.append("dte")

    # --- moneyness ----------------------------------------------------------
    if strike and spot:
        raw = (strike - spot) / spot * 100
        out["otm_pct"] = round(raw if call else -raw, 2)
        out["moneyness"] = "ITM" if (out["otm_pct"] < 0) else ("ATM" if abs(out["otm_pct"]) < 1 else "OTM")
    else:
        out["otm_pct"] = None
        missing.append("moneyness")

    # --- Greeks: observed, else model, never invented ------------------------
    greeks_src = "observed" if c.get("delta") is not None else None
    if greeks_src is None:
        iv = c.get("implied_volatility")
        model = bs_greeks(spot, strike or 0, iv or 0, dte or 0,
                          "CALL" if call else "PUT", cfg["risk_free_rate"]) if cfg["allow_model_greeks"] else None
        if model:
            out.update({k: model[k] for k in ("delta", "gamma", "theta", "vega")})
            out["greeks_model"] = model
            greeks_src = "model"
        else:
            missing.append("greeks")
            warn.append("Greeks unavailable from the provider and not computable "
                        f"({'IV missing' if not c.get('implied_volatility') else 'inputs incomplete'})")
    out["greeks_provenance"] = greeks_src

    # --- realistic entry price ---------------------------------------------
    # Prefer the broker's own high-fill-rate buy price (an observed estimate of what
    # actually fills). Otherwise assume a limit at mid plus a quarter of the spread —
    # NOT the mid, which is the price nobody is obliged to give you.
    if c.get("high_fill_rate_buy_price"):
        limit = c["high_fill_rate_buy_price"]
        limit_basis = "Robinhood high-fill-rate buy price (observed)"
    elif two_sided:
        limit = round(mid + (ask - bid) * 0.25, 2)
        limit_basis = "mid + 25% of the spread (calculated)"
    elif ask:
        limit = ask
        limit_basis = "ask — one-sided quote, assume you pay the offer"
    else:
        limit = None
        limit_basis = None
        missing.append("entry_price")
    # Round the limit to the contract's actual TICK. Robinhood quotes a tick of
    # $0.01 below the cutoff price and $0.05 above it; an un-rounded limit like
    # 7.272 is not a price the exchange would accept, so a "realistic limit" that
    # ignores the tick is not realistic.
    ticks = c.get("min_ticks") or {}
    below, above = _num(ticks.get("below_tick")), _num(ticks.get("above_tick"))
    cutoff = _num(ticks.get("cutoff_price"))
    tick = None
    if limit is not None and below and above and cutoff is not None:
        tick = above if limit >= cutoff else below
    elif limit is not None and below:
        tick = below
    if limit is not None and tick and tick > 0:
        limit = round(math.ceil(limit / tick) * tick, 4)   # round UP: you are paying
        limit_basis = f"{limit_basis}, rounded up to the ${tick} tick"
    out["tick_size"] = tick
    out["limit_price"] = limit
    out["limit_basis"] = limit_basis

    if limit is None:
        out.update({"tradeable": False, "warnings": warn, "missing_fields": missing,
                    "rejection": "no usable price"})
        return out

    # --- costs, break-even, exposure ---------------------------------------
    fees = round(cfg["fee_per_contract"] * contracts, 2)
    debit = round(limit * mult * contracts, 2)
    out["entry_debit"] = debit
    out["fees_estimated"] = fees
    out["total_cost"] = round(debit + fees, 2)
    out["max_loss"] = round(debit + fees, 2)
    out["max_loss_basis"] = "long single-leg option — the entire premium plus fees is at risk"
    be = round(strike + limit, 4) if call else round(strike - limit, 4)
    out["break_even"] = be
    out["break_even_provenance"] = "calculated"
    out["broker_break_even"] = c.get("broker_break_even")
    if c.get("broker_break_even") and abs(c["broker_break_even"] - be) > max(0.05, be * 0.002):
        warn.append(f"break-even differs from the broker's ({c['broker_break_even']}) because the "
                    f"limit assumption differs from their mark")
    out["pct_move_to_break_even"] = round((be - spot) / spot * 100, 2) if spot else None

    if out.get("delta") is not None:
        out["delta_adjusted_exposure"] = round(out["delta"] * mult * contracts * spot, 2)
        out["delta_exposure_note"] = (f"{contracts} contract(s) behave like "
                                      f"{round(out['delta'] * mult * contracts, 1)} shares at entry")
    if out.get("theta") is not None:
        tpd = round(abs(out["theta"]) * mult * contracts, 2)
        out["theta_cost_per_day"] = tpd
        out["theta_pct_of_premium_per_day"] = round(tpd / debit * 100, 2) if debit else None

    # --- implied vs expected move ------------------------------------------
    iv = c.get("implied_volatility")
    if iv and dte and spot:
        im = spot * iv * math.sqrt(max(dte, 0) / 365.0)
        out["implied_move_dollars"] = round(im, 2)
        out["implied_move_pct"] = round(im / spot * 100, 2)
        out["implied_move_provenance"] = "model (IV x sqrt(time), from observed IV)"
    else:
        missing.append("implied_move")
    if atr_pct and dte:
        em = spot * (atr_pct / 100.0) * math.sqrt(max(dte, 0))
        out["underlying_expected_move_dollars"] = round(em, 2)
        out["underlying_expected_move_pct"] = round(em / spot * 100, 2)
        out["expected_move_provenance"] = "model (ATR x sqrt(days), from observed ATR)"
        if out.get("pct_move_to_break_even") is not None:
            out["break_even_within_expected_move"] = (
                abs(out["pct_move_to_break_even"]) <= out["underlying_expected_move_pct"])

    # --- slippage -----------------------------------------------------------
    if two_sided:
        out["estimated_slippage"] = round((ask - mid) * mult * contracts, 2)
        out["slippage_note"] = "half the spread per side; a round trip costs roughly twice this"

    # --- scenarios (expiry intrinsic is exact; pre-expiry is a delta estimate)
    scen = []
    for name, px in (("target_1", target_price), ("invalidation", stop_price),
                     ("unchanged", spot)):
        if px is None:
            continue
        intrinsic = max(0.0, (px - strike) if call else (strike - px))
        pl_exp = round((intrinsic - limit) * mult * contracts - fees, 2)
        row = {"scenario": name, "underlying": round(px, 2),
               "value_at_expiry": round(intrinsic * mult * contracts, 2),
               "pl_at_expiry": pl_exp,
               "return_on_risk_at_expiry": round(pl_exp / out["max_loss"] * 100, 1) if out["max_loss"] else None,
               "provenance": "calculated (intrinsic value at expiration)"}
        if out.get("delta") is not None:
            est = limit + out["delta"] * (px - spot)
            row["est_value_before_expiry"] = round(max(0.0, est) * mult * contracts, 2)
            row["est_pl_before_expiry"] = round((max(0.0, est) - limit) * mult * contracts - fees, 2)
            row["before_expiry_provenance"] = "model (delta-linear, ignores gamma/theta/vega)"
        scen.append(row)
    out["scenarios"] = scen

    # --- expected value: ONLY with a sourced probability ---------------------
    pop = c.get("chance_of_profit_long")
    out["probability_above_break_even"] = pop
    out["probability_source"] = ("Robinhood chance_of_profit_long (broker-supplied)"
                                 if pop is not None else None)
    # An EV is only published when the target is actually reachable inside this
    # contract's life. Pairing the broker's break-even probability with the payoff at
    # a swing target the option cannot plausibly reach before expiry produces a large,
    # confident-looking number that means nothing — an 8-day MSFT call scored +$1,818
    # against a target needing a 15% move when its implied move was 10%.
    target_move_pct = (abs(target_price - spot) / spot * 100) if (target_price and spot) else None
    implied_pct = out.get("implied_move_pct")
    reachable = (target_move_pct is not None and implied_pct
                 and target_move_pct <= implied_pct * 1.25)
    out["target_within_contract_life"] = bool(reachable) if (target_move_pct is not None and implied_pct) else None
    if pop is not None and target_price is not None and reachable:
        win = next((s for s in scen if s["scenario"] == "target_1"), None)
        if win and win["pl_at_expiry"] is not None:
            ev = round(pop * win["pl_at_expiry"] - (1 - pop) * out["max_loss"], 2)
            out["expected_value"] = ev
            out["expected_value_inputs"] = {
                "probability": pop, "probability_source": "Robinhood chance_of_profit_long (broker-supplied)",
                "win_amount": win["pl_at_expiry"], "loss_amount": -out["max_loss"],
                "note": ("OPTIMISTIC PAIRING, read with care: the probability is the broker's "
                         "chance of finishing above BREAK-EVEN at expiration, while the win amount "
                         "is the payoff at TARGET 1 — a larger, less likely outcome. The true "
                         "expected value is therefore no better than this figure, so a negative "
                         "number here is a strong negative signal. Use the scenario table for the "
                         "unblended outcomes.")}
    else:
        out["expected_value"] = None
        if pop is None:
            out["expected_value_unavailable_reason"] = (
                "no probability from a citable source — scenario analysis is shown instead of a "
                "number that would need an invented win rate")
        elif target_move_pct is not None and implied_pct and not reachable:
            out["expected_value_unavailable_reason"] = (
                f"target 1 needs a {target_move_pct:.1f}% move but this contract's implied move over "
                f"{dte} days is {implied_pct:.1f}% — an expected value built on that payoff would be "
                f"false precision. Use the scenario table.")
        else:
            out["expected_value_unavailable_reason"] = (
                "insufficient inputs to pair a sourced probability with a payoff")

    # --- liquidity gates ----------------------------------------------------
    fails: List[str] = []
    oi, vol = c.get("open_interest"), c.get("volume")
    if not two_sided:
        fails.append("no two-sided quote")
    if bid is not None and bid <= 0:
        fails.append("bid is zero — no one is bidding for this contract")
    if out.get("spread_pct") is not None and out["spread_pct"] > cfg["max_spread_pct"]:
        fails.append(f"spread {out['spread_pct']:.1f}% of mid exceeds {cfg['max_spread_pct']:.0f}%")
    # The absolute-dollar spread cap is a FLOOR for cheap contracts, not a second
    # gate on expensive ones. A flat $0.60 cap rejected every near-the-money MSFT
    # contract — a $3.20 spread on a $74 contract is 4%, which is tight, not wide.
    dollar_cap = max(cfg["max_spread_dollars"], (mid or 0) * cfg["max_spread_pct"] / 100.0)
    out["spread_dollar_cap_applied"] = round(dollar_cap, 2)
    if out.get("spread_dollars") is not None and out["spread_dollars"] > dollar_cap:
        fails.append(f"spread ${out['spread_dollars']:.2f} exceeds the ${dollar_cap:.2f} cap for a "
                     f"${mid:.2f} contract")
    if oi is None:
        fails.append("open interest not reported")
    elif oi < cfg["min_open_interest"]:
        fails.append(f"open interest {oi} below {cfg['min_open_interest']:.0f}")
    # Today's option volume is 0 for every contract before the opening bell, so
    # enforcing a volume minimum outside the session would reject the entire chain
    # for a reason that has nothing to do with the contract.
    if vol is None:
        warn.append("volume not reported")
    elif not session_open:
        out["volume_gate"] = "waived — market is not open, so today's contract volume is 0 by definition"
        warn.append(f"today's volume {vol} not meaningful outside the session; open interest "
                    f"({oi}) is carrying the liquidity check")
    elif vol < cfg["min_volume"]:
        fails.append(f"volume {vol} below {cfg['min_volume']:.0f}")
    if out.get("otm_pct") is not None and out["otm_pct"] > cfg["max_otm_pct"]:
        fails.append(f"{out['otm_pct']:.1f}% out of the money — beyond the {cfg['max_otm_pct']:.0f}% limit")
    if dte is not None and dte < cfg["min_dte"]:
        fails.append(f"{dte} days to expiry is inside the {cfg['min_dte']:.0f}-day minimum")
    if dte is not None and dte > cfg["max_dte"]:
        warn.append(f"{dte} days to expiry is beyond the {cfg['max_dte']:.0f}-day window")
    if out.get("theta_pct_of_premium_per_day") is not None and \
            out["theta_pct_of_premium_per_day"] > cfg["max_theta_pct_per_day"]:
        fails.append(f"theta burns {out['theta_pct_of_premium_per_day']:.1f}% of the premium per day, "
                     f"above the {cfg['max_theta_pct_per_day']:.0f}% limit")
    if "greeks" in missing:
        fails.append("Greeks unavailable — the position's risk cannot be characterised")

    import freshness as _fr
    age = _fr.bar_age_seconds(c.get("quote_timestamp")) if c.get("quote_timestamp") else None
    out["quote_age_hours"] = round(age / 3600, 2) if age is not None else None
    if c.get("quote_timestamp") is None:
        fails.append("contract quote carries no timestamp — freshness cannot be verified")
    elif age is not None and age > cfg["max_quote_age_hours"] * 3600:
        fails.append(f"contract quote {age/3600:.1f}h old")

    out["liquidity_failures"] = fails
    out["warnings"] = warn
    out["missing_fields"] = missing
    out["tradeable"] = not fails
    out["rejection"] = "; ".join(fails) if fails else None
    return out


def next_earnings(symbol: str) -> Dict[str, Any]:
    """The next scheduled earnings date, or a typed 'unknown'. An option held through
    earnings is a different trade from the one the chart describes — implied vol is
    bid up beforehand and collapses after, so this has to be checked, not assumed."""
    def _c():
        try:
            import finnhub_data as _fh
            rows = _fh.earnings_calendar(_R.to_finnhub(symbol)) or []
        except Exception as e:  # noqa: BLE001
            return {"state": "error", "reason": str(e)[:100]}
        today = datetime.now(timezone.utc).date()
        future = []
        for r in rows:
            d = r.get("date")
            try:
                dd = date.fromisoformat(str(d))
            except (TypeError, ValueError):
                continue
            if dd >= today:
                future.append((dd, r))
        if not future:
            return {"state": "unknown", "reason": "no scheduled earnings date within the next 90 days"}
        future.sort(key=lambda x: x[0])
        d, r = future[0]
        return {"state": "ok", "date": d.isoformat(), "days_away": (d - today).days,
                "hour": r.get("hour"), "eps_estimate": r.get("epsEstimate"),
                "source": "Finnhub earnings calendar"}
    try:
        return _R.cached(f"earndate:{symbol}", 3600, _c)
    except Exception as e:  # noqa: BLE001
        return {"state": "error", "reason": str(e)[:100]}


def apply_event_risk(contracts: List[Dict[str, Any]], earnings: Dict[str, Any]) -> None:
    """Flag every contract whose expiry sits on or after the next earnings date."""
    if earnings.get("state") != "ok":
        for c in contracts:
            c["event_before_expiry"] = None
            c["event_note"] = f"earnings date {earnings.get('state')} — cannot verify event risk"
        return
    edate = date.fromisoformat(earnings["date"])
    for c in contracts:
        try:
            exp = date.fromisoformat(str(c.get("expiry")))
        except (TypeError, ValueError):
            c["event_before_expiry"] = None
            continue
        holds = exp >= edate
        c["event_before_expiry"] = holds
        if holds:
            c["event_note"] = (f"earnings on {earnings['date']} ({earnings['days_away']}d away) falls "
                               f"before the {c.get('expiry')} expiry — implied volatility is inflated "
                               f"now and will collapse after the print")
            c.setdefault("warnings", []).append(c["event_note"])


# ── Account (read-only) ──────────────────────────────────────────────────────

def account(force: bool = False) -> Dict[str, Any]:
    """Buying power, positions and option positions — READ ONLY."""
    try:
        import robinhood_view as rv
        a = rv.accounts(force=force)
    except Exception as e:  # noqa: BLE001
        return {"state": "unavailable", "reason": str(e)[:140], "read_only": True,
                "buying_power": None, "positions": [], "option_positions": []}
    # `robinhood_view.accounts()` returns `cash` and `agentic` as single normalized
    # ACCOUNT OBJECTS (plus `other_accounts` as a list) — not lists of accounts.
    accts: List[Dict[str, Any]] = []
    if isinstance(a, dict):
        for k in ("cash", "agentic"):
            v = a.get(k)
            if isinstance(v, dict) and v.get("account_masked"):
                accts.append(v)
            elif isinstance(v, list):
                accts.extend([x for x in v if isinstance(x, dict)])
        if isinstance(a.get("other_accounts"), list):
            accts.extend([x for x in a["other_accounts"] if isinstance(x, dict)])
    primary = None
    for x in accts:
        if x.get("buying_power") is None:
            continue
        if primary is None or (x.get("is_default") and not primary.get("is_default")):
            primary = x
    if primary is None and accts:
        primary = accts[0]
    if primary is None:
        return {"state": "unavailable", "reason": (a.get("reason") if isinstance(a, dict) else None)
                or "no Robinhood account returned", "read_only": True,
                "buying_power": None, "positions": [], "option_positions": []}
    return {
        "state": "ok", "read_only": True,
        "account_masked": primary.get("account_masked"),
        "account_type": primary.get("account_type"),
        "buying_power": primary.get("buying_power"),
        "cash": primary.get("cash"),
        "portfolio_value": primary.get("portfolio_value"),
        "positions": primary.get("positions") or [],
        "position_count": primary.get("position_count") or 0,
        "option_positions": primary.get("option_positions") or [],
        "spreads_available": False,
        "spreads_note": ("Multi-leg/defined-risk spreads require a margin account and options "
                         "Level 3. Verify your own account's level before assuming otherwise."),
        "source": "Robinhood MCP (read-only allowlist)",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def watchlists(force: bool = False) -> Dict[str, Any]:
    """The account's watchlists and their members — READ ONLY.

    `get_watchlists` / `get_watchlist_items` / `get_option_watchlist` are on the read
    allowlist; every mutating watchlist tool (`add_to_watchlist`, `remove_from_watchlist`,
    `update_watchlist`, `unfollow_watchlist`) is NOT, and is refused before the network
    while trading is disabled. This function only reads.

    Members are typed: `instrument` (stocks/ETFs — scannable), `currency_pair` (crypto)
    and anything else are kept but flagged, because a crypto symbol like BTC is not a
    US equity and must never be fed to the equity scanner as if it were.
    """
    def _c():
        import robinhood_mcp as rh
        d = _unwrap(rh.call_tool("get_watchlists", {}))
        if d.get("__error"):
            return {"state": "error", "reason": d["__error"], "read_only": True}
        rows = d.get("watchlists") or d.get("results") or []
        lists: List[Dict[str, Any]] = []
        for r in rows:
            lid = r.get("id")
            items: List[Dict[str, Any]] = []
            if lid:
                try:
                    it = _unwrap(rh.call_tool("get_watchlist_items", {"list_id": lid}))
                    items = it.get("items") or it.get("results") or []
                except Exception as e:  # noqa: BLE001
                    items = []
                    r = {**r, "items_error": str(e)[:100]}
            equities = [i["symbol"] for i in items
                        if i.get("object_type") == "instrument" and i.get("symbol")]
            other = [{"symbol": i.get("symbol"), "type": i.get("object_type")}
                     for i in items if i.get("object_type") != "instrument"]
            lists.append({
                "id": lid, "name": r.get("display_name"), "owner_type": r.get("owner_type"),
                "icon": r.get("icon_emoji"),
                "item_count": r.get("item_count"),
                "symbols": equities, "equity_count": len(equities),
                "non_equity": other,
                "allowed_object_types": r.get("allowed_object_types"),
                "items_error": r.get("items_error"),
            })
        opt: Dict[str, Any] = {"items": [], "state": "ok"}
        try:
            ow = _unwrap(rh.call_tool("get_option_watchlist", {}))
            opt = {"state": "ok", "list_id": ow.get("list_id"), "items": ow.get("items") or []}
        except Exception as e:  # noqa: BLE001
            opt = {"state": "error", "reason": str(e)[:100], "items": []}
        all_eq = sorted({s for l in lists for s in l["symbols"]})
        return {"state": "ok", "read_only": True, "lists": lists, "count": len(lists),
                "option_watchlist": opt,
                "all_equity_symbols": all_eq,
                "source": "Robinhood MCP (read-only allowlist)",
                "fetched_at": datetime.now(timezone.utc).isoformat()}

    if force:
        _R.invalidate("desk:watchlists")
    try:
        return _R.cached("desk:watchlists", 300, _c)
    except Exception as e:  # noqa: BLE001
        return {"state": "unavailable", "reason": str(e)[:140], "read_only": True, "lists": []}


# ── Phase 9: position sizing ─────────────────────────────────────────────────

def size_position(*, entry: float, stop: float, buying_power: Optional[float],
                  cfg: Dict[str, Any], contract: Optional[Dict[str, Any]] = None,
                  open_positions: int = 0, sector_positions: int = 0,
                  existing_exposure: float = 0.0,
                  equity: Optional[float] = None, spot: Optional[float] = None,
                  atr_pct: Optional[float] = None, setup_score: Optional[float] = None,
                  portfolio: Optional[Dict[str, Any]] = None,
                  pol: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Share and contract sizing under the configured caps. Never assumes the whole
    account is available; reports the BINDING constraint."""
    out: Dict[str, Any] = {"limits": cfg, "blocked": [], "buying_power": buying_power}
    if open_positions >= cfg["max_concurrent_positions"]:
        out["blocked"].append(f"already at the {cfg['max_concurrent_positions']}-position limit")
    if sector_positions >= cfg["max_positions_per_sector"]:
        out["blocked"].append(f"already at the {cfg['max_positions_per_sector']}-per-sector limit")
    if buying_power is None:
        out["state"] = "unavailable"
        out["reason"] = "no buying power available from the account — sizing not computed"
        return out
    if entry <= 0 or stop <= 0 or stop >= entry:
        out["state"] = "unavailable"
        out["reason"] = "entry/stop do not define a positive risk per share"
        return out

    risk_per_share = entry - stop
    risk_budget = buying_power * cfg["max_account_risk_pct"] / 100.0
    if cfg["max_risk_dollars"] > 0:
        risk_budget = min(risk_budget, cfg["max_risk_dollars"])
    notional_cap = buying_power * cfg["max_position_pct"] / 100.0
    exposure_room = max(0.0, buying_power * cfg["max_total_exposure_pct"] / 100.0 - existing_exposure)

    by_risk = risk_budget / risk_per_share
    by_notional = notional_cap / entry
    by_exposure = exposure_room / entry
    raw = min(by_risk, by_notional, by_exposure)
    shares = round(raw, 4) if cfg["fractional_shares"] else math.floor(raw)
    binding = min((("risk budget", by_risk), ("position cap", by_notional),
                   ("total exposure cap", by_exposure)), key=lambda x: x[1])[0]

    out.update({
        "state": "ok",
        "shares": {
            "quantity": shares,
            "fractional": cfg["fractional_shares"],
            "notional": round(shares * entry, 2),
            "max_loss": round(shares * risk_per_share, 2),
            "pct_of_account_at_risk": round(shares * risk_per_share / buying_power * 100, 2) if buying_power else None,
            "remaining_buying_power": round(buying_power - shares * entry, 2),
            "binding_constraint": binding,
            "risk_per_share": round(risk_per_share, 4),
        },
    })
    # Stock leg under the instrument-aware model: capital, planned, stress and absolute
    # max loss are four different numbers and are reported as four different numbers.
    out["shares"]["risk"] = option_risk_mod.stock_risk(
        quantity=shares, entry=entry, stop=stop,
        slippage_bps=cfg["equity_slippage_bps"], fee=cfg["equity_fee"])

    if contract and contract.get("limit_price"):
        mult = contract.get("multiplier") or 100.0
        per = contract["limit_price"] * mult + cfg["fee_per_contract"]
        eq = equity if equity is not None else buying_power
        try:
            pol = pol or option_risk_mod.policy()
        except option_risk_mod.RiskConfigError as e:
            # Fail CLOSED and loudly. A misconfigured risk policy must not resolve to a
            # working default — nothing may be sized against limits nobody can name.
            out["risk_config_error"] = str(e)
            out["option"] = {
                "contracts": 0, "cost_per_contract": round(per, 2), "total_cost": 0.0,
                "affordable": False,
                "status": "NO CONTRACT FITS CURRENT RISK POLICY",
                "binding_constraint": "risk configuration",
                "config_error": str(e),
                "note": "Risk policy configuration is invalid — no option can be sized "
                        "until it is fixed. This is not a fallback to a default.",
            }
            return out
        # An option's PLANNED risk is what the position loses when the underlying
        # reaches its invalidation level — not the whole premium. Treating those as the
        # same number is what made every contract unreachable on a small account. The
        # premium is still reported, unchanged, as absolute_max_loss.
        orisk = option_risk_mod.option_risk(
            contracts=1, limit_price=contract["limit_price"],
            spot=spot if spot is not None else entry, stop=stop,
            strike=contract.get("strike") or 0.0, side=str(contract.get("side") or "CALL"),
            iv=contract.get("implied_volatility"), dte=contract.get("dte"),
            atr_pct=atr_pct, multiplier=mult, fee_per_contract=cfg["fee_per_contract"],
            spread_dollars=contract.get("spread_dollars"),
            r=cfg["risk_free_rate"], pol=pol)
        qual = option_risk_mod.quality_tier(setup_score)
        pchk = option_risk_mod.portfolio_check(
            equity=eq or 0.0, candidate_risk=orisk,
            remaining_buying_power=buying_power, pol=pol, **(portfolio or {}))
        elig = option_risk_mod.contract_eligibility(
            risk=orisk, equity=eq or 0.0, pol=pol, quality=qual, portfolio=pchk)
        out["option"] = {
            # Indivisible: one contract or none. A fractional contract count is a
            # rounding artifact, never a recommendation.
            "contracts": elig["contracts"],
            "cost_per_contract": round(per, 2),
            "total_cost": round(elig["contracts"] * per, 2),
            "max_loss": orisk["absolute_max_loss"],
            "capital_committed": orisk["capital_committed"],
            "planned_risk": orisk["planned_risk"],
            "stress_risk": orisk["stress_risk"],
            "absolute_max_loss": orisk["absolute_max_loss"],
            "pct_of_account_at_risk": (round(orisk["planned_risk"] / eq * 100, 2)
                                       if eq else None),
            "remaining_buying_power": (round(buying_power - elig["contracts"] * per, 2)
                                       if buying_power is not None else None),
            "affordable": elig["eligible"],
            "status": elig["status"],
            "binding_constraint": elig["binding_constraint"],
            "risk": orisk, "eligibility": elig, "portfolio": pchk, "quality_tier": qual,
            "policy_profile": pol["profile"], "policy_note": pol["profile_note"],
            "note": (elig["status"] + " under the " + pol["profile"] + " profile. "
                     "The full premium remains the absolute max loss; planned risk is "
                     "the modelled loss at the underlying's invalidation level."),
        }
    return out


# ── Phase 8: shares vs options ───────────────────────────────────────────────

VERDICTS = ("prefer-stock", "prefer-option", "prefer-defined-risk-spread", "watch-only", "reject")

# Which inputs a trade verdict genuinely depends on. Anything listed here is CRITICAL:
# if it is past its own freshness window during a live session, no verdict is issued.
CRITICAL_DECISION_INPUTS = ("underlying quote", "option chain", "account state",
                            "technical levels")


def _decision_freshness(candidate: Dict[str, Any], best_contract: Optional[Dict[str, Any]],
                        account_info: Dict[str, Any],
                        sizing: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Age every critical input against ITS OWN window, and say whether a verdict may
    be issued at all.

    On a closed exchange nothing can move, so age alone does not withhold a verdict —
    but the result is labelled MARKET CLOSED so it is never mistaken for live guidance.
    """
    import freshness as _fr
    try:
        import market_regime as _MRg
        market_state = _MRg.session_state()["state"]
    except Exception:
        market_state = None

    ind = candidate.get("indicators") or {}
    lv = candidate.get("levels") or {}
    checks: List[Dict[str, Any]] = []

    def add(name: str, kind: str, ts: Optional[str], present: bool = True) -> None:
        if not present:
            return
        rec = _fr.classify_typed(kind, _fr.bar_age_seconds(ts) if ts else None,
                                 market_state=market_state)
        checks.append({"input": name, "kind": kind, "source_timestamp": ts,
                       "age_seconds": rec["age_seconds"], "state": rec["state"],
                       "presentation": rec["presentation"],
                       "blocks": rec["blocks_tradeable"], "label": rec["label"]})

    # Prefer the REAL intraday quote timestamp over the daily bar's. A daily bar is
    # stamped at its own close, so `bar_age_seconds` reads it as age ~0 for the whole
    # session — it cannot detect an intraday-stale underlying. The candidate carries a
    # separate live quote; when it is present it is the honest thing to age.
    qts = ((candidate.get("quote") or {}).get("source_timestamp")
           or ind.get("source_timestamp") or ind.get("as_of"))
    add("underlying quote", "quote", qts)
    add("option chain", "option_chain", (best_contract or {}).get("quote_timestamp"),
        present=best_contract is not None)
    add("account state", "account", account_info.get("fetched_at"))
    add("technical levels", "levels", lv.get("computed_at") or ind.get("as_of"))

    stale = [f"{c['input']} is {c['label']}" for c in checks
             if c["blocks"] and c["input"] in CRITICAL_DECISION_INPUTS]
    return {"ok": not stale, "market_state": market_state, "checks": checks,
            "stale_inputs": stale,
            "status": ("DECISION STALE — REFRESH REQUIRED" if stale else
                       "MARKET CLOSED · LAST CLOSE"
                       if market_state not in ("open", "premarket", "afterhours")
                       and market_state is not None else "INPUTS CURRENT")}


def decide(*, candidate: Dict[str, Any], best_contract: Optional[Dict[str, Any]],
           account_info: Dict[str, Any], cfg: Dict[str, Any],
           sizing: Optional[Dict[str, Any]] = None,
           contracts_analysed: int = 0,
           rejected_sample: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """One verdict per candidate, with the reasons on both sides. The option that was
    evaluated is always reported, including when the answer is to buy shares."""
    reasons_for_stock: List[str] = []
    reasons_for_option: List[str] = []
    blockers: List[str] = []

    lv = candidate.get("levels") or {}
    ind = candidate.get("indicators") or {}
    setups = candidate.get("setups") or []
    horizon = setups[0].get("horizon") if setups else None
    score = (candidate.get("score") or {}).get("total")

    if lv.get("state") != "ok":
        return {"verdict": "reject", "reasons": ["no tradeable levels — a stop cannot be placed objectively"],
                "evaluated_option": best_contract, "horizon": horizon}
    if lv.get("rr_target_1") is not None and lv["rr_target_1"] < cfg["min_reward_risk"]:
        blockers.append(f"reward:risk {lv['rr_target_1']:.2f} to target 1 is below the "
                        f"{cfg['min_reward_risk']} minimum")

    # ---- the option case ---------------------------------------------------
    if best_contract is None:
        reasons_for_stock.append("no contract in the chain passed the liquidity gates")
    elif not best_contract.get("tradeable"):
        reasons_for_stock.append("best available contract fails: " + (best_contract.get("rejection") or "unknown"))
    else:
        sp = best_contract.get("spread_pct")
        if sp is not None and sp <= 5:
            reasons_for_option.append(f"contract spread is tight ({sp:.1f}% of mid)")
        elif sp is not None:
            reasons_for_stock.append(f"contract spread is {sp:.1f}% of mid — shares cost far less to get in and out of")
        oi = best_contract.get("open_interest") or 0
        if oi >= 1000:
            reasons_for_option.append(f"deep open interest ({oi:,})")
        elif oi < cfg["min_open_interest"]:
            reasons_for_stock.append(f"open interest {oi:,} is thin")
        tp = best_contract.get("theta_pct_of_premium_per_day")
        if tp is not None and tp > 1.5:
            reasons_for_stock.append(f"theta costs {tp:.1f}% of the premium per day — the thesis has to "
                                     f"work quickly, and shares do not decay")
        elif tp is not None:
            reasons_for_option.append(f"theta is modest at {tp:.1f}% of premium per day")
        bw = best_contract.get("break_even_within_expected_move")
        rq = best_contract.get("pct_move_to_break_even")
        if bw is False:
            reasons_for_stock.append(
                f"break-even needs {rq:.1f}% but the expected move over "
                f"{best_contract.get('dte')} days is only "
                f"{best_contract.get('underlying_expected_move_pct')}% — the required move is unrealistic")
        elif bw is True:
            reasons_for_option.append(
                f"break-even needs {rq:.1f}%, inside the {best_contract.get('underlying_expected_move_pct')}% "
                f"expected move")
        iv = best_contract.get("implied_volatility")
        # Prefer a proper close-to-close realized vol; the ATR proxy runs high
        # (ATR includes gaps and the full range), which made an earnings-inflated
        # IV look "not inflated" purely because the yardstick was inflated too.
        realized_annual = ind.get("realized_vol_20d")
        rv_basis = "20-day close-to-close realized volatility"
        if realized_annual is None and ind.get("atr_pct"):
            realized_annual = ind["atr_pct"] * math.sqrt(252)
            rv_basis = "ATR-based proxy (approximate, runs high)"
        if iv is not None and realized_annual:
            if iv * 100 > realized_annual * 1.35:
                reasons_for_stock.append(
                    f"implied volatility {iv*100:.0f}% is well above the {realized_annual:.0f}% the stock has "
                    f"actually been moving ({rv_basis}) — you would be paying up for volatility")
            else:
                reasons_for_option.append(f"implied volatility {iv*100:.0f}% is not inflated versus "
                                          f"{realized_annual:.0f}% realized ({rv_basis})")
        if horizon and "intraday" in str(horizon):
            reasons_for_option.append("an intraday horizon limits theta exposure")
        elif horizon and "week" in str(horizon):
            reasons_for_stock.append("a multi-week horizon gives theta time to work against a long option")
        if best_contract.get("event_before_expiry"):
            reasons_for_stock.append(best_contract.get("event_note") or
                                     "an earnings event falls before this expiry")
        if best_contract.get("greeks_provenance") == "model":
            reasons_for_stock.append("Greeks are modelled, not provider-supplied — less certainty about the option's risk")

    # ---- affordability / sizing -------------------------------------------
    # Affordability is a HARD constraint on the verdict, never one vote among many.
    # A contract the account cannot buy is not a candidate at any quality — the same
    # gate lab/paper/options_shadow.py already applies BEFORE it scores edge.
    # Without this, a technically strong contract (tight spread, deep OI, modest theta,
    # break-even inside the expected move) out-votes its own unaffordability on reason
    # count and returns "prefer-option" for a premium far above buying power and many
    # multiples of the risk budget. Contract quality and portfolio suitability are
    # different questions and are now answered separately.
    option_ineligible: Optional[str] = None
    option_eligibility: Optional[Dict[str, Any]] = None
    bp = account_info.get("buying_power")
    if bp is None:
        blockers.append("buying power unavailable — position size cannot be checked against the account")
    if sizing and sizing.get("state") == "ok":
        opt = sizing.get("option") or {}
        sh = sizing.get("shares") or {}
        option_eligibility = opt.get("eligibility")
        if opt and not opt.get("affordable"):
            option_ineligible = (f"{opt.get('status')} — "
                                 f"{(option_eligibility or {}).get('reason') or opt.get('binding_constraint')}")
            reasons_for_stock.append(option_ineligible)
        if sh.get("quantity") and cfg["fractional_shares"]:
            reasons_for_stock.append(
                f"fractional shares allow an exact {sh['quantity']} share position sized to the "
                f"{sh.get('binding_constraint')}")
    elif best_contract and best_contract.get("tradeable"):
        # Sizing unavailable means suitability was never established. Silence is not
        # a pass — a prefer-option verdict must not ride through on quality alone.
        option_ineligible = "position size could not be computed — suitability unverified"
    if sizing and (sizing.get("blocked") or []):
        blockers.extend(sizing["blocked"])
    # A broken risk policy blocks BOTH instruments, not just the option leg — the
    # configuration error is surfaced rather than absorbed into a default.
    if sizing and sizing.get("risk_config_error"):
        blockers.append("risk policy configuration error: " + sizing["risk_config_error"])

    # ---- instrument choice: quality and suitability answered separately ----
    # CONTRACT QUALITY  — "is this the right contract in this chain?"  (gates above)
    # PORTFOLIO SUITABILITY — "should THIS account trade it?"          (policy/sizing)
    # A contract can be the best in its chain and still fail suitability; the two are
    # reported side by side rather than collapsed into one green label.
    option_quality_ok = bool(best_contract and best_contract.get("tradeable"))
    stock_sizeable = bool(sizing and (sizing.get("shares") or {}).get("quantity"))
    instrument = option_risk_mod.instrument_choice(
        stock=((sizing.get("shares") or {}).get("risk") if sizing else None),
        option=((sizing.get("option") or {}).get("risk") if sizing else None),
        option_eligibility=option_eligibility,
        stock_sizeable=stock_sizeable,
        option_quality_ok=option_quality_ok and option_ineligible is None,
        quality_tilt=len(reasons_for_option) - len(reasons_for_stock),
        option_edge={
            "theta_pct_per_day": (best_contract or {}).get("theta_pct_of_premium_per_day"),
            "break_even_within_expected_move":
                (best_contract or {}).get("break_even_within_expected_move"),
        })

    # ---- spreads -----------------------------------------------------------
    spread_note = None
    if not cfg["spreads_allowed"] or not account_info.get("spreads_available", False):
        spread_note = ("Defined-risk debit spreads were NOT evaluated: they need a margin account "
                       "with options Level 3. Set OPT_SPREADS_ALLOWED=true only if your account "
                       "actually has that approval.")

    # ---- decision freshness: every critical input, checked by TYPE ----------
    # A verdict is only as current as the OLDEST input that produced it. Mixing a live
    # quote with an hour-old chain and yesterday's buying power yields a confident
    # recommendation about a market that has moved. Each input is aged against its own
    # window (a quote and an earnings date do not deserve the same tolerance), and if
    # any critical one is past it the verdict is withheld rather than restated.
    inputs_fresh = _decision_freshness(candidate, best_contract, account_info, sizing)
    if not inputs_fresh["ok"]:
        blockers.append("DECISION STALE — REFRESH REQUIRED: "
                        + "; ".join(inputs_fresh["stale_inputs"]))

    # ---- verdict -----------------------------------------------------------
    if blockers:
        verdict = "reject" if any("reward:risk" in b for b in blockers) else "watch-only"
    elif score is not None and score < 55:
        verdict = "watch-only"
        blockers.append(f"score {score} below the 55 threshold for an actionable setup")
    elif instrument["instrument"] == "OPTION PREFERRED":
        verdict = "prefer-option"
    elif instrument["instrument"] == "NO TRADE":
        verdict = "watch-only"
        blockers.append("neither instrument can be sized within policy — cash is the position")
    else:
        verdict = "prefer-stock"

    # A prefer-stock verdict is checked three ways before it stands: there must be a
    # concrete stock plan, the stock must be sizeable, and the option must actually
    # have been looked at.
    stock_checks: List[Dict[str, Any]] = []
    if verdict == "prefer-stock":
        stock_checks = [
            {"check": "levels are tradeable",
             "pass": lv.get("state") == "ok",
             "detail": f"entry {lv.get('entry_zone')}, stop {lv.get('invalidation')} "
                       f"({lv.get('invalidation_basis')}), targets {lv.get('target_1')}/{lv.get('target_2')}"},
            {"check": "position can be sized within the caps",
             "pass": bool(sizing and (sizing.get("shares") or {}).get("quantity")),
             "detail": ((f"{(sizing.get('shares') or {}).get('quantity')} shares, "
                         f"${(sizing.get('shares') or {}).get('notional')} notional, max loss "
                         f"${(sizing.get('shares') or {}).get('max_loss')}") if sizing else "not sized")},
            # "an option was evaluated" means the CHAIN was read and judged — not that
            # a contract survived. A chain where all 120 contracts were rejected is the
            # strongest possible evidence for preferring shares, so treating it as a
            # failed check wrongly downgraded the verdict to watch-only.
            {"check": "the option chain was actually evaluated before choosing shares",
             "pass": contracts_analysed > 0,
             "detail": (f"{contracts_analysed} contracts analysed; "
                        + (f"best was {best_contract.get('expiry')} ${best_contract.get('strike')} "
                           f"{best_contract.get('side')} at {best_contract.get('limit_price')}"
                           if best_contract else
                           "none passed the liquidity gates — e.g. " + "; ".join(
                               f"${r.get('strike')}: {r.get('reason')}" for r in (rejected_sample or [])[:2])))
                       if contracts_analysed else "NO chain retrieved"},
        ]
        if not all(c["pass"] for c in stock_checks):
            verdict = "watch-only"
            blockers.append("prefer-stock verdict failed its own verification: "
                            + "; ".join(c["check"] for c in stock_checks if not c["pass"]))

    return {
        "verdict": verdict,
        "reasons_for_option": reasons_for_option,
        "reasons_for_stock": reasons_for_stock,
        "blockers": blockers,
        "evaluated_option": best_contract,
        # Contract quality and portfolio suitability are reported separately: the best
        # contract in the chain is still shown when the account cannot trade it, marked
        # research-only rather than silently dropped or silently recommended.
        "option_eligible": option_ineligible is None,
        "option_ineligible_reason": option_ineligible,
        # The two questions, kept apart on purpose.
        "contract_quality": {
            "gates_passed": option_quality_ok,
            "reasons_for_option": reasons_for_option,
            "detail": "how good this contract is relative to others in its chain",
        },
        "portfolio_suitability": {
            "pass": option_ineligible is None and option_quality_ok,
            "status": ((sizing.get("option") or {}).get("status") if sizing else None),
            "eligibility": option_eligibility,
            "policy_profile": ((sizing.get("option") or {}).get("policy_profile")
                               if sizing else option_risk_mod.active_profile()),
            "detail": "whether THIS account should trade it, under the active risk policy",
        },
        "instrument": instrument["instrument"],
        "instrument_reasons": instrument,
        # Per-input ages behind this verdict, so a reader can see WHICH datum is old
        # rather than being handed one global age that describes none of them.
        "decision_freshness": inputs_fresh,
        # Mapped onto the engine's existing authoritative vocabulary — this module
        # chooses HOW to express a setup, it does not re-judge whether it is valid.
        "decision": option_risk_mod.to_decision(
            instrument["instrument"],
            setup_actionable=(verdict not in ("reject", "watch-only"))),
        "spread_note": spread_note,
        "horizon": horizon,
        "sizing": sizing,
        "verification": stock_checks,
        "decided_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": "Research only. No order is placed, previewed or queued by this system.",
    }


# ── Orchestration for one candidate ──────────────────────────────────────────

def evaluate_candidate(candidate: Dict[str, Any], *, account_info: Optional[Dict[str, Any]] = None,
                       cfg: Optional[Dict[str, Any]] = None,
                       max_expiries: int = 3) -> Dict[str, Any]:
    """Chain → contract analysis → best contract → verdict → sizing, for one name."""
    cfg = {**config(), **(cfg or {})}
    acct = account_info if account_info is not None else account()
    try:
        import market_regime as _MRg
        session_open = _MRg.session_state()["state"] == "open"
    except Exception:  # noqa: BLE001
        session_open = True
    sym = candidate["symbol"]
    ind = candidate.get("indicators") or {}
    lv = candidate.get("levels") or {}
    spot = ind.get("price")
    side = "call"

    prov = {"contracts": None, "notes": []}
    exp_info = rh_expirations(sym)
    chains: List[Dict[str, Any]] = []
    if exp_info.get("state") == "ok":
        prov["contracts"] = "robinhood"
        today = datetime.now(timezone.utc).date()
        usable = []
        for e in exp_info["expirations"]:
            try:
                d = (date.fromisoformat(e) - today).days
            except ValueError:
                continue
            if cfg["min_dte"] <= d <= cfg["max_dte"]:
                usable.append(e)
        if not usable:
            prov["notes"].append(f"no expiry between {cfg['min_dte']:.0f} and {cfg['max_dte']:.0f} days")
        for e in usable[:max_expiries]:
            ch = rh_chain(sym, e, side, spot=spot)
            if ch.get("state") == "ok":
                chains.append(ch)
            else:
                prov["notes"].append(f"{e}: {ch.get('state')} {ch.get('reason', '')}".strip())
    else:
        prov["contracts"] = "yahoo"
        prov["notes"].append(f"Robinhood chain unavailable ({exp_info.get('state')}: "
                             f"{exp_info.get('reason')}) — falling back to Yahoo, which supplies no Greeks")
        ch = yahoo_chain(sym, None, side)
        if ch.get("state") == "ok":
            chains.append(ch)
        else:
            prov["notes"].append(f"Yahoo chain {ch.get('state')}: {ch.get('reason', '')}")

    analysed: List[Dict[str, Any]] = []
    for ch in chains:
        for c in ch.get("contracts") or []:
            if spot is None or c.get("strike") is None:
                continue
            analysed.append(analyse_contract(
                c, spot=spot, cfg=cfg, atr_pct=ind.get("atr_pct"),
                target_price=lv.get("target_1"), stop_price=lv.get("invalidation"),
                session_open=session_open))

    earn = next_earnings(sym)
    apply_event_risk(analysed, earn)

    passing = [c for c in analysed if c.get("tradeable")]
    # Best = tightest spread among those closest to the money, then deepest OI.
    passing.sort(key=lambda c: (abs(c.get("otm_pct") or 99), c.get("spread_pct") or 99,
                                -(c.get("open_interest") or 0)))
    best = passing[0] if passing else None

    sizing = None
    if lv.get("state") == "ok":
        sizing = size_position(entry=lv["reference_price"], stop=lv["invalidation"],
                               buying_power=acct.get("buying_power"), cfg=cfg,
                               contract=best,
                               open_positions=len(acct.get("positions") or []),
                               sector_positions=0,
                               # Live broker values — never hardcoded. portfolio_value is
                               # the account's equity; buying power is the cash that can
                               # actually be deployed, and they are not the same number.
                               equity=acct.get("portfolio_value") or acct.get("buying_power"),
                               spot=spot, atr_pct=ind.get("atr_pct"),
                               setup_score=(candidate.get("score") or {}).get("total"),
                               portfolio={"open_options": acct.get("option_positions") or []})
    verdict = decide(candidate=candidate, best_contract=best, account_info=acct,
                     cfg=cfg, sizing=sizing, contracts_analysed=len(analysed),
                     rejected_sample=[{"strike": c.get("strike"), "reason": c.get("rejection")}
                                      for c in analysed if not c.get("tradeable")])

    return {
        "symbol": sym, "state": "ok",
        "underlying_price": spot,
        "provider": prov,
        "expirations_considered": [ch.get("expiry") for ch in chains],
        "contracts_analysed": len(analysed),
        "session_open": session_open,
        "earnings": earn,
        "contracts_passing": len(passing),
        "chain": analysed,
        "best_contract": best,
        "rejected_contracts": [{"strike": c.get("strike"), "expiry": c.get("expiry"),
                                "reason": c.get("rejection")} for c in analysed if not c.get("tradeable")],
        "decision": verdict,
        "account": {k: acct.get(k) for k in ("state", "buying_power", "account_masked",
                                             "read_only", "position_count")},
        "config": cfg,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
