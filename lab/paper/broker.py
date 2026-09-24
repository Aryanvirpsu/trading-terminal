"""Paper broker — order lifecycle, positions, P&L.

The entry gate is the safety-critical part. A paper trade may be created ONLY when
every one of these holds (mission §2):

    decision == TRADEABLE
    every hard AND soft decision gate passed
    the data is decision-valid (source age within its category freshness limit)
    freshness state is acceptable (not stale / critically stale / fallback)
    provider provenance exists
    entry, stop, target and quantity are all valid
    expected value is positive after costs
    portfolio risk checks pass

MONITOR and REJECT are never executed. Each failed precondition is recorded, so a
refusal is itself evidence.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

from . import config as cfg
from . import db
from . import fills
from . import journal
from . import risk as risk_mod
from .fills import Quote

ACCEPTABLE_FRESHNESS = ("fresh", "ageing")


# ── Entry preconditions ───────────────────────────────────────────────────────

def check_preconditions(result: Dict[str, Any], quote: Optional[Quote] = None,
                        quantity: Optional[float] = None) -> Dict[str, Any]:
    """Evaluate every entry precondition. Returns allow + per-check detail."""
    checks: List[Dict[str, Any]] = []

    def _c(name: str, ok: bool, detail: str, value: Any = None):
        checks.append({"name": name, "passed": bool(ok),
                       "detail": "" if ok else detail, "value": value})

    decision = result.get("decision")
    _c("decision_tradeable", decision == "TRADEABLE",
       f"decision is {decision}, only TRADEABLE may execute", decision)

    gates = result.get("decision_gates") or []
    failed = [g["name"] for g in gates if not g.get("passed")]
    _c("all_gates_pass", bool(gates) and not failed,
       ("no decision gates present" if not gates
        else f"gates failed: {', '.join(failed)}"), failed)

    fresh = result.get("freshness") or {}
    state = fresh.get("state")
    _c("freshness_acceptable", state in ACCEPTABLE_FRESHNESS,
       f"freshness '{state}' is not acceptable for execution", state)

    # Decision-validity is judged on the SOURCE timestamp via the shared cache policy.
    dv, dv_detail = _decision_valid(result, quote)
    _c("data_decision_valid", dv, dv_detail, dv_detail)

    src = result.get("data_source")
    _c("provenance_exists", bool(src), "no provider provenance on the result", src)

    entry_range = result.get("entry_range") or []
    entry = entry_range[0] if entry_range else None
    stop, target = result.get("stop"), result.get("target")
    qty = quantity if quantity is not None else result.get("suggested_shares")
    levels_ok = all(v is not None and v > 0 for v in (entry, stop, target)) and \
        (target > entry > stop or target < entry < stop)
    _c("levels_valid", levels_ok, "entry/stop/target missing or mis-ordered",
       {"entry": entry, "stop": stop, "target": target})
    _c("quantity_valid", qty is not None and qty > 0,
       f"invalid quantity {qty}", qty)

    ev = (result.get("ev_breakdown") or {}).get("ev_per_share",
                                               result.get("expected_value_per_share"))
    _c("positive_ev_after_costs", ev is not None and ev > 0,
       f"EV/share {ev} is not positive after costs", ev)

    if quote is not None:
        _c("quote_available", quote.resolved().has_market,
           "no executable quote (cannot simulate a fill)", quote.provider)

    failed_checks = [c for c in checks if not c["passed"]]
    return {"allow": not failed_checks, "checks": checks,
            "reasons": [c["detail"] for c in failed_checks]}


def _decision_valid(result: Dict[str, Any], quote: Optional[Quote]) -> tuple:
    """Stale data must never create an order. Uses the shared cache policy, which
    judges on the SOURCE timestamp, not cache-insertion time."""
    try:
        import cache_policy as cp
    except Exception:
        try:
            from .. import cache_policy as cp     # type: ignore
        except Exception:
            return True, "cache policy unavailable"
    ts = quote.source_ts if quote else None
    if quote is not None:
        # A supplied quote must carry a real source time. An engine "fresh" label
        # cannot vouch for a price whose observation time is unknown.
        import math
        try:
            ok = ts is not None and math.isfinite(float(ts)) and float(ts) > 0
        except (TypeError, ValueError):
            ok = False
        if not ok:
            return False, "quote has no valid source timestamp"
    elif not ts:
        ts = None
    if ts is None:
        fresh = result.get("freshness") or {}
        if fresh.get("state") in ACCEPTABLE_FRESHNESS:
            return True, "no source ts; engine freshness acceptable"
        return False, f"no source timestamp and freshness '{fresh.get('state')}'"
    rec = cp.classify("price", ts)
    if not rec["decision_valid"]:
        return False, (f"price data is {rec['tier']} "
                       f"(source age {rec['age_seconds']}s > limit {rec['limit_s']}s)")
    return True, f"{rec['tier']} (age {rec['age_seconds']}s)"


# ── Order placement ───────────────────────────────────────────────────────────

def _next_seq(session_date: str) -> int:
    return (db.query_one("SELECT COUNT(*) n FROM orders WHERE session_date=?",
                         (session_date,)) or {"n": 0})["n"] + 1


def place_order(symbol: str, side: str, quantity: float, *, order_type: str = "MARKET",
                limit_price: Optional[float] = None, stop_price: Optional[float] = None,
                intent: str = "entry", strategy: str = "", signal_id: Optional[str] = None,
                session_date: Optional[str] = None, tif: str = "DAY") -> Dict[str, Any]:
    """Create a PENDING order with a deterministic id and an audit entry."""
    session_date = session_date or dt.date.today().isoformat()
    oid = fills.order_id(symbol, side, session_date, _next_seq(session_date), strategy)
    now = db.utcnow()
    db.execute("""INSERT INTO orders(order_id, signal_id, created_at, session_date, symbol,
                    side, order_type, quantity, limit_price, stop_price, tif, status,
                    intent, strategy, updated_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (oid, signal_id, now, session_date, symbol.upper(), side.upper(),
                order_type.upper(), quantity, limit_price, stop_price, tif, "pending",
                intent, strategy, now))
    db.audit("order", oid, "created",
             {"symbol": symbol, "side": side, "qty": quantity, "type": order_type,
              "limit": limit_price, "stop": stop_price, "intent": intent,
              "strategy": strategy, "signal_id": signal_id})
    return {"order_id": oid, "status": "pending", "symbol": symbol.upper(),
            "side": side.upper(), "quantity": quantity, "order_type": order_type.upper()}


def reject_order(order_id: str, reason: str) -> Dict[str, Any]:
    db.execute("UPDATE orders SET status='rejected', reject_reason=?, updated_at=? "
               "WHERE order_id=?", (reason, db.utcnow(), order_id))
    db.audit("order", order_id, "rejected", {"reason": reason})
    return {"order_id": order_id, "status": "rejected", "reason": reason}


def expire_order(order_id: str, reason: str = "DAY order expired") -> Dict[str, Any]:
    db.execute("UPDATE orders SET status='expired', reject_reason=?, updated_at=? "
               "WHERE order_id=?", (reason, db.utcnow(), order_id))
    db.audit("order", order_id, "expired", {"reason": reason})
    return {"order_id": order_id, "status": "expired", "reason": reason}


def process_order(order_id: str, quote: Quote) -> Dict[str, Any]:
    """Attempt to fill a pending/partial order against a market snapshot."""
    o = db.query_one("SELECT * FROM orders WHERE order_id=?", (order_id,))
    if not o:
        return {"error": "unknown order"}
    if o["status"] in ("filled", "cancelled", "rejected", "expired"):
        return {"order_id": order_id, "status": o["status"], "note": "terminal state"}

    remaining = o["quantity"] - (o["filled_qty"] or 0)
    if remaining <= 0:
        return {"order_id": order_id, "status": "filled"}

    res = fills.simulate(o["order_type"], o["side"], remaining, quote,
                         o.get("limit_price"), o.get("stop_price"))

    if res.status == "rejected":
        return reject_order(order_id, res.reason)
    if res.status == "pending":
        db.audit("order", order_id, "no_fill", {"reason": res.reason})
        return {"order_id": order_id, "status": "pending", "reason": res.reason}

    seq = (db.query_one("SELECT COUNT(*) n FROM fills WHERE order_id=?", (order_id,))
           or {"n": 0})["n"] + 1
    fid = fills.fill_id(order_id, seq)
    db.execute("""INSERT INTO fills(fill_id, order_id, filled_at, quantity, price, fees,
                    slippage, reference, liquidity, note)
                  VALUES(?,?,?,?,?,?,?,?,?,?)""",
               (fid, order_id, db.utcnow(), res.quantity, res.price, res.fees,
                res.slippage, res.reference, res.liquidity, res.note))

    filled = (o["filled_qty"] or 0) + res.quantity
    all_fills = db.query("SELECT quantity, price, fees FROM fills WHERE order_id=?", (order_id,))
    notional = sum(f["quantity"] * f["price"] for f in all_fills)
    total_fees = sum(f["fees"] or 0 for f in all_fills)
    avg = round(notional / filled, 4) if filled else None
    status = "filled" if filled >= o["quantity"] - 1e-9 else "partial"
    db.execute("""UPDATE orders SET status=?, filled_qty=?, avg_fill=?, fees=?, updated_at=?
                  WHERE order_id=?""",
               (status, round(filled, 6), avg, round(total_fees, 4), db.utcnow(), order_id))
    db.audit("order", order_id, f"fill_{res.liquidity}",
             {"fill_id": fid, "qty": res.quantity, "price": res.price,
              "slippage": res.slippage, "status": status})

    _apply_fill_to_position(o, res, avg)
    return {"order_id": order_id, "status": status, "fill": res.as_dict(),
            "filled_qty": round(filled, 6), "avg_fill": avg, "fill_id": fid}


def _apply_fill_to_position(order: Dict[str, Any], res: fills.FillResult,
                            avg: Optional[float]) -> None:
    """Open, add to, or close a position from a fill."""
    sym = order["symbol"]
    pos = db.query_one("SELECT * FROM positions WHERE symbol=? AND status='open'", (sym,))
    now = db.utcnow()

    if order["intent"] == "entry":
        if pos:
            new_qty = pos["quantity"] + res.quantity
            new_avg = round((pos["quantity"] * pos["avg_entry"] + res.quantity * res.price)
                            / new_qty, 6)
            db.execute("UPDATE positions SET quantity=?, avg_entry=?, fees=? WHERE position_id=?",
                       (new_qty, new_avg, (pos["fees"] or 0) + res.fees, pos["position_id"]))
            db.audit("position", pos["position_id"], "added", {"qty": res.quantity})
        else:
            sig = db.query_one("SELECT stop, target, sector, planned_risk FROM signals "
                               "WHERE signal_id=?", (order.get("signal_id"),)) or {}
            pid = fills.position_id(sym, now)
            db.execute("""INSERT INTO positions(position_id, signal_id, symbol, strategy,
                            sector, opened_at, quantity, avg_entry, stop, target, status,
                            fees, planned_risk, mfe, mae)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (pid, order.get("signal_id"), sym, order.get("strategy"),
                        sig.get("sector"), now, res.quantity, res.price,
                        sig.get("stop"), sig.get("target"), "open", res.fees,
                        sig.get("planned_risk"), 0.0, 0.0))
            db.audit("position", pid, "opened",
                     {"symbol": sym, "qty": res.quantity, "entry": res.price})
    else:
        if not pos:
            db.audit("order", order["order_id"], "exit_without_position", {"symbol": sym})
            return
        closing = min(res.quantity, pos["quantity"])
        gross = (res.price - pos["avg_entry"]) * closing
        fees_total = (pos["fees"] or 0) + res.fees
        remaining = round(pos["quantity"] - closing, 6)
        if remaining <= 1e-9:
            db.execute("""UPDATE positions SET status='closed', closed_at=?, avg_exit=?,
                            realized_pnl=?, fees=?, exit_reason=?, quantity=?
                          WHERE position_id=?""",
                       (now, res.price, round(gross - fees_total, 4), round(fees_total, 4),
                        order.get("intent"), pos["quantity"], pos["position_id"]))
            db.audit("position", pos["position_id"], "closed",
                     {"exit": res.price, "pnl": round(gross - fees_total, 4),
                      "reason": order.get("intent")})
        else:
            realized = (pos.get("realized_pnl") or 0) + gross
            db.execute("""UPDATE positions SET quantity=?, realized_pnl=?, fees=?
                          WHERE position_id=?""",
                       (remaining, round(realized, 4), round(fees_total, 4), pos["position_id"]))
            db.audit("position", pos["position_id"], "reduced",
                     {"qty": closing, "remaining": remaining})


# ── The one entry point that may create a paper trade ─────────────────────────

def submit_entry(result: Dict[str, Any], quote: Quote, *, strategy: str,
                 sector: Optional[str] = None, industry: Optional[str] = None,
                 market_regime: Optional[str] = None, scanner_rank: Optional[int] = None,
                 session_date: Optional[str] = None,
                 canonical_quantity: Optional[float] = None,
                 canonical_planned_risk: Optional[float] = None,
                 stock_executable: Optional[bool] = None) -> Dict[str, Any]:
    """Journal the signal, then execute ONLY if every precondition and risk check
    passes. Returns what happened and why — a refusal is a first-class outcome.

    Canonical Option Architecture v1.1, Step 10: `canonical_quantity` (and
    optionally `canonical_planned_risk`) let a caller that has already run
    the canonical A/B/D/E/executable stack (lab/paper/canonical_bridge.py)
    hand this function the AUTHORITATIVE final quantity directly — this
    function then uses it EXACTLY as given, never recomputing or resizing it
    via risk_mod.position_size(). `risk_mod.check_entry()` still runs
    unconditionally below as pure defense-in-depth: it may BLOCK the
    canonical quantity (a real, useful catch on state canonical B's snapshot
    couldn't see — e.g. a position opened by another process between B's
    read and this call) but it can never increase or rescale it — every
    check_entry() call in this module receives numbers already fixed by
    this point, and check_entry() itself only ever returns allow/block, it
    has no resize path of its own (see lab/paper/risk.py — its signature
    takes planned_risk/notional as INPUT, never computes them).

    `stock_executable`, when `canonical_quantity` is given, must be True —
    this is the hard "only stock_executable=True may reach the broker" gate
    (Phase 13), enforced here even if a future caller forgets to check it
    upstream.

    Backward compatible: when `canonical_quantity` is None (every EXISTING
    caller/test), behavior is 100% unchanged — this function still computes
    its own quantity via risk_mod.position_size() below, exactly as before
    Step 10.
    """
    session_date = session_date or dt.date.today().isoformat()
    entry_range = result.get("entry_range") or []
    entry = entry_range[0] if entry_range else None
    stop = result.get("stop")

    st = risk_mod.account_state(session_date)
    # Size against the price we would ACTUALLY pay (ask + slippage), not the last
    # print. On a $500 cash account this is the difference between a trade being
    # affordable and being rejected, so it must not be approximated.
    fill_px = _expected_fill_price(result, quote)

    if canonical_quantity is not None:
        if not stock_executable:
            sid = journal.record_signal(
                result, strategy=strategy, sector=sector, industry=industry,
                market_regime=market_regime, scanner_rank=scanner_rank,
                quantity=canonical_quantity, planned_risk=canonical_planned_risk,
                session_date=session_date)
            db.audit("signal", sid, "not_executable",
                     {"reason": "canonical stock_executable is not True",
                      "canonical_quantity": canonical_quantity})
            return {"executed": False, "signal_id": sid, "stage": "not_executable",
                    "reasons": ["canonical stock_executable is not True — no broker call"],
                    "sizing": {"quantity": canonical_quantity, "affordable": False,
                              "binding_constraint": "not_executable"}}
        qty = canonical_quantity
        sizing = {"quantity": qty, "planned_risk": canonical_planned_risk or 0.0,
                 "notional": round(qty * (fill_px or 0.0), 2), "affordable": qty > 0,
                 "binding_constraint": "canonical_e", "share_price": fill_px,
                 "capital_required": round(qty * (fill_px or 0.0), 2),
                 "role": "canonical_authoritative"}
    else:
        sizing = (risk_mod.position_size(st["equity"], entry, stop, fill_price=fill_px,
                                         buying_power=st["buying_power"])
                  if (entry and stop) else {"quantity": 0.0, "planned_risk": 0.0,
                                            "notional": 0.0, "affordable": False,
                                            "binding_constraint": "invalid levels"})
        qty = sizing.get("quantity", 0.0)

    sid = journal.record_signal(
        result, strategy=strategy, sector=sector, industry=industry,
        market_regime=market_regime, scanner_rank=scanner_rank,
        quantity=qty, planned_risk=sizing.get("planned_risk"), session_date=session_date)

    # Unaffordable is a first-class, EXPECTED outcome on a $500 account — recorded as
    # its own stage so we can measure what fraction of good signals we simply can't buy.
    if not sizing.get("affordable", False) or qty <= 0:
        reason = sizing.get("reason") or (
            f"not affordable: {sizing.get('binding_constraint')} "
            f"(buying power ${st['buying_power']}, share ${sizing.get('share_price')})")
        db.audit("signal", sid, "unaffordable",
                 {"reason": reason, "binding": sizing.get("binding_constraint"),
                  "buying_power": st["buying_power"]})
        return {"executed": False, "signal_id": sid, "stage": "affordability",
                "reasons": [reason], "sizing": sizing}

    pre = check_preconditions(result, quote, qty)
    if not pre["allow"]:
        db.audit("signal", sid, "entry_refused", {"reasons": pre["reasons"]})
        return {"executed": False, "signal_id": sid, "stage": "preconditions",
                "reasons": pre["reasons"], "checks": pre["checks"]}

    side = "BUY" if (result.get("direction", "LONG").upper() == "LONG") else "SELL"
    est_fees = fills.fee_for(qty, fill_px or 0.0)
    realistic_cost = round(qty * (fill_px or 0.0) + est_fees, 2)
    rk = risk_mod.check_entry(result.get("symbol", "?"), sector,
                              sizing.get("planned_risk", 0.0),
                              sizing.get("notional", 0.0), session_date,
                              realistic_cost=realistic_cost, side=side)
    if not rk["allow"]:
        db.audit("signal", sid, "risk_blocked", {"reasons": rk["reasons"]})
        return {"executed": False, "signal_id": sid, "stage": "risk",
                "reasons": rk["reasons"], "checks": rk["checks"], "sizing": sizing}

    order = place_order(result["symbol"], side, qty, order_type="MARKET",
                        intent="entry", strategy=strategy, signal_id=sid,
                        session_date=session_date)
    filled = process_order(order["order_id"], quote)
    if filled.get("status") in ("filled", "partial"):
        journal.mark_executed(sid, order["order_id"])
    return {"executed": filled.get("status") in ("filled", "partial"),
            "signal_id": sid, "order": order, "fill": filled,
            "sizing": sizing, "realistic_cost": realistic_cost, "risk": {"allow": True}}


def _expected_fill_price(result: Dict[str, Any], quote: Optional[Quote]) -> Optional[float]:
    """What a market BUY would actually cost per share: ask + slippage. Falls back to
    the engine's entry only when no quote exists (in which case the trade will be
    refused anyway)."""
    if quote is not None:
        qr = quote.resolved()
        if qr.has_market:
            e = cfg.execution()
            return round(qr.ask * (1 + e.slippage_bps / 10000.0), 4)
    er = result.get("entry_range") or []
    return er[0] if er else result.get("price")


# ── Position management ───────────────────────────────────────────────────────

def manage_open_positions(quotes: Dict[str, Quote],
                          session_date: Optional[str] = None) -> Dict[str, Any]:
    """Check stops and targets on open positions and update MFE/MAE. Stops are checked
    BEFORE targets: if a bar hits both, we assume the stop filled first."""
    session_date = session_date or dt.date.today().isoformat()
    actions: List[Dict[str, Any]] = []
    for pos in db.query("SELECT * FROM positions WHERE status='open'"):
        q = quotes.get(pos["symbol"])
        if not q:
            continue
        hi = q.high if q.high is not None else q.last
        lo = q.low if q.low is not None else q.last
        if hi is None or lo is None:
            continue
        is_long = pos["quantity"] > 0
        mfe = max(pos.get("mfe") or 0.0, round((hi - pos["avg_entry"]) if is_long
                                               else (pos["avg_entry"] - lo), 4))
        mae = max(pos.get("mae") or 0.0, round((pos["avg_entry"] - lo) if is_long
                                               else (hi - pos["avg_entry"]), 4))
        db.execute("UPDATE positions SET mfe=?, mae=? WHERE position_id=?",
                   (mfe, mae, pos["position_id"]))

        stop, target = pos.get("stop"), pos.get("target")
        exit_intent = None
        if stop is not None and ((lo <= stop) if is_long else (hi >= stop)):
            exit_intent, ref = "exit_stop", stop
        elif target is not None and ((hi >= target) if is_long else (lo <= target)):
            exit_intent, ref = "exit_target", target
        if not exit_intent:
            continue

        side = "SELL" if is_long else "BUY"
        otype = "STOP" if exit_intent == "exit_stop" else "LIMIT"
        o = place_order(pos["symbol"], side, pos["quantity"], order_type=otype,
                        limit_price=(ref if otype == "LIMIT" else None),
                        stop_price=(ref if otype == "STOP" else None),
                        intent=exit_intent, strategy=pos.get("strategy"),
                        signal_id=pos.get("signal_id"), session_date=session_date)
        r = process_order(o["order_id"], q)
        actions.append({"symbol": pos["symbol"], "intent": exit_intent,
                        "order_id": o["order_id"], "result": r})
    return {"session_date": session_date, "actions": actions, "count": len(actions)}


def expire_day_orders(session_date: Optional[str] = None) -> int:
    """DAY orders that never filled expire at the close."""
    session_date = session_date or dt.date.today().isoformat()
    rows = db.query("SELECT order_id FROM orders WHERE tif='DAY' AND session_date < ? "
                    "AND status IN ('pending','partial')", (session_date,))
    for r in rows:
        expire_order(r["order_id"])
    return len(rows)


# ── Account views + reconciliation ────────────────────────────────────────────

def account(session_date: Optional[str] = None) -> Dict[str, Any]:
    st = risk_mod.account_state(session_date)
    opens = db.query("SELECT * FROM positions WHERE status='open'")
    for p in opens:
        mark, src = risk_mod._live_mark_src(p["symbol"])
        qty, entry = p["quantity"], p["avg_entry"]
        if mark is None:
            mark, src = entry, "entry_fallback"
        p["current_price"] = round(mark, 4)
        p["market_value"] = round(qty * mark, 2)
        p["unrealized_pnl"] = round(qty * (mark - entry), 2)
        p["unrealized_pnl_pct"] = round((mark - entry) / entry * 100, 2) if entry else 0.0
        p["mark_source"] = src
    st["open"] = opens
    st["pending_orders"] = db.query(
        "SELECT * FROM orders WHERE status IN ('pending','partial') ORDER BY created_at DESC")
    st["closed"] = db.query(
        "SELECT * FROM positions WHERE status='closed' ORDER BY closed_at DESC LIMIT 50")
    return st


def snapshot_equity(session_date: Optional[str] = None) -> Dict[str, Any]:
    session_date = session_date or dt.date.today().isoformat()
    st = risk_mod.account_state(session_date)
    db.execute("""INSERT OR REPLACE INTO equity(session_date, recorded_at, cash,
                    positions_value, equity, realized_pnl, unrealized_pnl,
                    peak_equity, drawdown_pct)
                  VALUES(?,?,?,?,?,?,?,?,?)""",
               (session_date, db.utcnow(), st["cash"], st["positions_value"], st["equity"],
                st["realized_pnl"], st["unrealized_pnl"], st["peak_equity"],
                st["drawdown_pct"]))
    return st


def reconcile() -> Dict[str, Any]:
    """Independent P&L check: rebuild cash from fills and compare to the ledger.
    A mismatch is a data-integrity violation and must fail loudly."""
    cash = cfg.account().initial_cash
    for f in db.query("""SELECT f.quantity, f.price, f.fees, o.side
                         FROM fills f JOIN orders o ON o.order_id=f.order_id
                         ORDER BY f.filled_at"""):
        notional = f["quantity"] * f["price"]
        cash += (-notional if f["side"] == "BUY" else notional) - (f["fees"] or 0)
    st = risk_mod.account_state()
    # Compare at cent precision on BOTH sides: account_state rounds its cash for
    # display, so comparing a rounded value against an unrounded sum leaves a
    # sub-cent artefact that makes a clean reconciliation look almost-clean.
    cash_r = round(cash, 2)
    delta = round(cash_r - st["cash"], 4)
    ok = abs(delta) < 0.01
    return {"reconciled": ok, "cash_from_fills": cash_r,
            "cash_from_state": st["cash"], "delta": delta,
            "equity": st["equity"],
            "note": "ok" if ok else "MISMATCH — investigate before trusting P&L"}
