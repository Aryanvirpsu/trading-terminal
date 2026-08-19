"""Pre-trade risk controls for the paper account.

Every limit is configurable (`paper.config.risk()`). A failure REJECTS the order and
records the exact reason — a blocked trade is evidence too, so it is journalled rather
than silently dropped.

Checks, in order (all are evaluated so the report shows every reason, not just the
first): equity/cash availability, per-trade risk cap, max entries per day, max open
positions, per-sector position cap, sector exposure cap, daily loss limit, drawdown
limit, cash reserve, consecutive-loss cooldown, correlated-position cap.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

from . import config as cfg
from . import db

# Coarse correlation groups — positions inside a group tend to move together, so we
# cap how many we hold at once. Deliberately simple and explicit rather than a
# statistical correlation matrix (which would need a price-history model we don't want
# to add during a build freeze).
CORRELATION_GROUPS: Dict[str, str] = {
    "AAPL": "megacap_tech", "MSFT": "megacap_tech", "GOOGL": "megacap_tech",
    "AMZN": "megacap_tech", "META": "megacap_tech", "NVDA": "semis",
    "AMD": "semis", "AVGO": "semis", "SMH": "semis", "MU": "semis", "INTC": "semis",
    "JPM": "big_banks", "BAC": "big_banks", "WFC": "big_banks", "C": "big_banks",
    "GS": "big_banks", "MS": "big_banks",
    "XOM": "energy", "CVX": "energy", "COP": "energy", "EOG": "energy", "SLB": "energy",
    "TSLA": "autos", "GM": "autos", "F": "autos", "RIVN": "autos",
    "UNH": "managed_care", "CVS": "managed_care", "CI": "managed_care",
}


def correlation_group(symbol: str) -> Optional[str]:
    return CORRELATION_GROUPS.get((symbol or "").upper())


# ── Account state ─────────────────────────────────────────────────────────────

def account_state(session_date: Optional[str] = None) -> Dict[str, Any]:
    """Current paper account: cash, open positions, equity, drawdown, day P&L."""
    r = cfg.risk()
    a = cfg.account()
    session_date = session_date or dt.date.today().isoformat()

    opens = db.query("SELECT * FROM positions WHERE status='open'")
    closed = db.query("SELECT * FROM positions WHERE status='closed'")

    realized = sum((p.get("realized_pnl") or 0.0) for p in closed)
    fees_paid = sum((p.get("fees") or 0.0) for p in closed) + \
                sum((p.get("fees") or 0.0) for p in opens)
    invested = sum((p["quantity"] * p["avg_entry"]) for p in opens)
    cash = a.initial_cash + realized - invested

    # Equity uses a live quote when reachable, else the last fill, else entry
    # (conservative) — see _live_mark. This is what the dashboard's Port tab reads.
    positions_value = 0.0
    for p in opens:
        mark = _live_mark(p["symbol"]) or p["avg_entry"]
        positions_value += p["quantity"] * mark
    equity = cash + positions_value

    curve = db.query("SELECT equity FROM equity ORDER BY session_date")
    peak = max([c["equity"] for c in curve] + [equity, a.initial_equity])
    drawdown = round((peak - equity) / peak * 100, 3) if peak else 0.0
    drawdown_usd = round(max(0.0, peak - equity), 2)      # absolute $ is what binds here

    day_closed = [p for p in closed if str(p.get("closed_at") or "")[:10] == session_date]
    day_pnl = sum((p.get("realized_pnl") or 0.0) for p in day_closed)

    entries_today = db.query_one(
        "SELECT COUNT(*) n FROM orders WHERE session_date=? AND intent='entry' "
        "AND status IN ('filled','partial')", (session_date,))["n"]

    sector_positions: Dict[str, int] = {}
    sector_value: Dict[str, float] = {}
    corr_positions: Dict[str, int] = {}
    for p in opens:
        s = p.get("sector") or "unknown"
        sector_positions[s] = sector_positions.get(s, 0) + 1
        mark = _live_mark(p["symbol"]) or p["avg_entry"]
        sector_value[s] = sector_value.get(s, 0.0) + p["quantity"] * mark
        g = correlation_group(p["symbol"])
        if g:
            corr_positions[g] = corr_positions.get(g, 0) + 1

    # A CASH account has no margin: buying power is settled cash minus the reserve,
    # never equity. This is the number every affordability check must use.
    available_cash = round(max(0.0, cash), 2)
    buying_power = round(max(0.0, available_cash - r.min_cash_reserve), 2)

    return {
        "session_date": session_date, "ledger": a.ledger,
        "starting_equity": a.initial_equity, "starting_cash": a.initial_cash,
        "cash": round(cash, 2), "positions_value": round(positions_value, 2),
        "available_cash": available_cash, "buying_power": buying_power,
        "margin_enabled": a.margin_enabled, "shorting_allowed": a.allow_shorting,
        "fractional_shares": a.fractional_shares,
        "capital_utilization_pct": round(positions_value / equity * 100, 2) if equity else 0.0,
        "equity": round(equity, 2), "peak_equity": round(peak, 2),
        "drawdown_pct": drawdown, "drawdown_usd": drawdown_usd,
        "realized_pnl": round(realized, 2), "fees_paid": round(fees_paid, 2),
        "unrealized_pnl": round(positions_value - invested, 2),
        "open_positions": len(opens), "entries_today": entries_today,
        "day_pnl": round(day_pnl, 2),
        "day_loss_used_pct": round(abs(min(0.0, day_pnl)) / equity * 100, 3) if equity else 0.0,
        "sector_positions": sector_positions,
        "sector_value": {k: round(v, 2) for k, v in sector_value.items()},
        "correlation_positions": corr_positions,
        "cooldown": cooldown_state(),
    }


def _last_mark(symbol: str) -> Optional[float]:
    row = db.query_one(
        "SELECT f.price FROM fills f JOIN orders o ON o.order_id=f.order_id "
        "WHERE o.symbol=? ORDER BY f.filled_at DESC LIMIT 1", (symbol,))
    return row["price"] if row else None


_MARK_CACHE: Dict[str, tuple] = {}   # symbol -> (price, fetched_at_monotonic)
_MARK_TTL_S = 15.0                   # dashboard polls faster than this; don't hammer providers


def _live_mark_src(symbol: str) -> tuple[Optional[float], str]:
    """Best-available mark for portfolio views (account_state, the dashboard's
    Port tab), plus where it came from. Tries a fresh Yahoo/Finnhub quote (no
    TradingView — this can run on every poll and must stay fast/cheap and outside
    the TV throttle), cached briefly so rapid polling doesn't hammer providers.
    Falls back to the last fill price, then (by the caller) avg_entry. A quote
    outage must never break the account view — worst case it's exactly as stale as
    before this existed."""
    import time as _time
    now = _time.monotonic()
    cached = _MARK_CACHE.get(symbol)
    if cached and (now - cached[1]) < _MARK_TTL_S:
        return cached[0], "live_quote_cached"
    try:
        import os, sys
        here = os.path.dirname(os.path.abspath(__file__))
        for rel in ("..", os.path.join("..", "..", "dashboard")):
            p = os.path.join(here, rel)
            if p not in sys.path:
                sys.path.insert(0, p)
        import providers as P
        pc = P.price_consensus(symbol, with_tv=False)
        v = pc.get("value")
        if v is not None:
            v = float(v)
            _MARK_CACHE[symbol] = (v, now)
            return v, "live_quote"
    except Exception:
        pass
    return _last_mark(symbol), "last_fill"


def _live_mark(symbol: str) -> Optional[float]:
    return _live_mark_src(symbol)[0]


def cooldown_state() -> Dict[str, Any]:
    """After N consecutive losing trades, stop opening new risk for a cooling period."""
    r = cfg.risk()
    closed = db.query(
        "SELECT realized_pnl, closed_at FROM positions WHERE status='closed' "
        "ORDER BY closed_at DESC LIMIT ?", (r.cooldown_losses,))
    if len(closed) < r.cooldown_losses:
        return {"active": False, "consecutive_losses": len(
            [c for c in closed if (c.get("realized_pnl") or 0) < 0]), "until": None}
    streak = 0
    for c in closed:
        if (c.get("realized_pnl") or 0) < 0:
            streak += 1
        else:
            break
    if streak < r.cooldown_losses:
        return {"active": False, "consecutive_losses": streak, "until": None}
    last_at = closed[0].get("closed_at")
    try:
        until = (dt.datetime.fromisoformat(str(last_at).replace("Z", "+00:00")).date()
                 + dt.timedelta(days=r.cooldown_days)).isoformat()
    except Exception:
        until = None
    active = True
    if until:
        active = dt.date.today().isoformat() <= until
    return {"active": active, "consecutive_losses": streak, "until": until}


# ── The pre-trade check ───────────────────────────────────────────────────────

def check_entry(symbol: str, sector: Optional[str], planned_risk: float,
                notional: float, session_date: Optional[str] = None,
                realistic_cost: Optional[float] = None,
                side: str = "BUY") -> Dict[str, Any]:
    """May we open this position? Returns allow + EVERY failing reason + the state
    that produced the verdict, so the journal records exactly why.

    `realistic_cost` is the cash actually required at the simulated fill (ask +
    slippage + fees), NOT the last price. On a $500 cash account that difference
    decides whether a trade is even possible."""
    r = cfg.risk()
    a = cfg.account()
    st = account_state(session_date)
    equity = st["equity"]
    cost = realistic_cost if realistic_cost is not None else notional
    reasons: List[str] = []
    checks: List[Dict[str, Any]] = []

    def _chk(name: str, ok: bool, detail: str, limit: Any, actual: Any):
        checks.append({"name": name, "passed": bool(ok), "limit": limit,
                       "actual": actual, "detail": "" if ok else detail})
        if not ok:
            reasons.append(detail)

    # ── Cash-account rules: no leverage, no borrowing, no shorting ────────────
    _chk("no_shorting", a.allow_shorting or side.upper() == "BUY",
         "shorting is disabled on this cash account", False, side.upper())
    _chk("no_margin", not a.margin_enabled or True, "", a.margin_enabled, a.margin_enabled)

    # THE binding constraint on a small account: can we actually pay for it?
    _chk("affordable", cost <= st["buying_power"] + 1e-9,
         f"realistic cost ${round(cost,2)} exceeds buying power ${st['buying_power']} "
         f"(cash ${st['available_cash']} − ${r.min_cash_reserve} reserve)",
         st["buying_power"], round(cost, 2))

    _chk("max_position_notional", cost <= r.max_position_notional + 1e-9,
         f"position cost ${round(cost,2)} exceeds the ${r.max_position_notional} "
         f"per-stock cap", r.max_position_notional, round(cost, 2))

    per_trade_cap = r.loss_cap(equity)
    _chk("per_trade_risk", planned_risk <= per_trade_cap + 1e-9,
         f"planned loss ${round(planned_risk,2)} exceeds the ${per_trade_cap} "
         f"max-loss-per-trade cap", per_trade_cap, round(planned_risk, 2))

    _chk("max_entries_per_day", st["entries_today"] < r.max_entries_per_day,
         f"already {st['entries_today']} entries today (max {r.max_entries_per_day})",
         r.max_entries_per_day, st["entries_today"])

    _chk("max_open_positions", st["open_positions"] < r.max_open_positions,
         f"already {st['open_positions']} open positions (max {r.max_open_positions})",
         r.max_open_positions, st["open_positions"])

    sec = sector or "unknown"
    held = st["sector_positions"].get(sec, 0)
    _chk("max_positions_per_sector", held < r.max_positions_per_sector,
         f"already {held} position(s) in {sec} (max {r.max_positions_per_sector})",
         r.max_positions_per_sector, held)

    sec_val = st["sector_value"].get(sec, 0.0) + cost
    sec_cap = r.sector_cap(equity)
    _chk("max_sector_exposure", sec_val <= sec_cap + 1e-9,
         f"{sec} exposure ${round(sec_val,2)} would exceed cap ${sec_cap}",
         sec_cap, round(sec_val, 2))

    _chk("daily_loss_limit", st["day_pnl"] > -r.max_daily_loss,
         f"daily loss ${round(st['day_pnl'],2)} hit the ${r.max_daily_loss} daily limit",
         round(-r.max_daily_loss, 2), st["day_pnl"])

    _chk("max_drawdown", st["drawdown_usd"] < r.max_drawdown,
         f"drawdown ${st['drawdown_usd']} at/over the ${r.max_drawdown} limit",
         r.max_drawdown, st["drawdown_usd"])

    cash_after = st["available_cash"] - cost
    _chk("min_cash_reserve", cash_after >= r.min_cash_reserve - 1e-9,
         f"cash after entry ${round(cash_after,2)} below the "
         f"${r.min_cash_reserve} minimum reserve",
         r.min_cash_reserve, round(cash_after, 2))

    cd = st["cooldown"]
    _chk("loss_cooldown", not cd["active"],
         f"cooldown active after {cd['consecutive_losses']} consecutive losses"
         + (f" until {cd['until']}" if cd.get("until") else ""),
         r.cooldown_losses, cd["consecutive_losses"])

    grp = correlation_group(symbol)
    grp_held = st["correlation_positions"].get(grp, 0) if grp else 0
    _chk("max_correlated", (not grp) or grp_held < r.max_correlated_positions,
         f"already {grp_held} position(s) in correlation group '{grp}' "
         f"(max {r.max_correlated_positions})", r.max_correlated_positions, grp_held)

    allow = not reasons
    out = {"allow": allow, "reasons": reasons, "checks": checks,
           "per_trade_cap": per_trade_cap, "sector": sec,
           "correlation_group": grp, "state": st,
           "config": r.as_dict()}
    db.audit("risk", symbol, "entry_allowed" if allow else "entry_blocked",
             {"reasons": reasons, "planned_risk": planned_risk, "notional": notional})
    return out


def position_size(equity: float, entry: float, stop: float,
                  risk_fraction: Optional[float] = None,
                  fill_price: Optional[float] = None,
                  buying_power: Optional[float] = None) -> Dict[str, Any]:
    """Shares for a $500 CASH account.

    Size is the smallest of four independent constraints, and we report which one
    actually bound — on a small account the answer is usually "affordability", and
    that is exactly the fact worth measuring:

        1. risk budget  ÷ stop distance      (max loss per trade)
        2. max position notional cap
        3. buying power at the REALISTIC fill price (no margin, no borrowing)
        4. whole-share rounding when fractional shares are unavailable
    """
    r = cfg.risk()
    a = cfg.account()
    px = fill_price if fill_price else entry
    per_share = abs(entry - stop)
    budget = round(min(r.max_loss_per_trade,
                       equity * (risk_fraction if risk_fraction is not None
                                 else r.risk_per_trade_pct)), 2)
    if per_share <= 0 or px <= 0:
        return {"quantity": 0.0, "risk_budget": budget, "risk_per_share": 0.0,
                "planned_risk": 0.0, "notional": 0.0, "affordable": False,
                "binding_constraint": "invalid levels", "reason": "zero stop distance"}

    bp = buying_power if buying_power is not None else max(
        0.0, account_state()["buying_power"])

    q_risk = budget / per_share
    q_notional = r.max_position_notional / px
    q_cash = bp / px
    qty = min(q_risk, q_notional, q_cash)
    binding = min((q_risk, "risk_budget"), (q_notional, "position_cap"),
                  (q_cash, "buying_power"), key=lambda t: t[0])[1]

    whole_share_only = not a.fractional_shares
    if whole_share_only:
        qty = float(int(qty))                      # always round DOWN — never overspend
        if qty < 1:
            return {"quantity": 0.0, "risk_budget": budget,
                    "risk_per_share": round(per_share, 4), "planned_risk": 0.0,
                    "notional": 0.0, "affordable": False,
                    "binding_constraint": "whole_share_minimum",
                    "share_price": round(px, 2),
                    "reason": f"cannot afford 1 whole share at ${round(px,2)} "
                              f"with ${round(bp,2)} buying power"}
    else:
        qty = round(qty, 6)
        if qty * px < a.fractional_min_notional:
            return {"quantity": 0.0, "risk_budget": budget,
                    "risk_per_share": round(per_share, 4), "planned_risk": 0.0,
                    "notional": 0.0, "affordable": False,
                    "binding_constraint": "fractional_minimum",
                    "reason": f"order ${round(qty*px,2)} below the "
                              f"${a.fractional_min_notional} fractional minimum"}

    notional = round(qty * px, 2)
    return {"quantity": qty, "risk_budget": budget,
            "risk_per_share": round(per_share, 4),
            "planned_risk": round(qty * per_share, 2),
            "notional": notional, "share_price": round(px, 2),
            "affordable": notional <= bp + 1e-9 and qty > 0,
            "binding_constraint": binding,
            "fractional": (not whole_share_only) and abs(qty - round(qty)) > 1e-9,
            "buying_power": round(bp, 2),
            "capital_required": notional}
