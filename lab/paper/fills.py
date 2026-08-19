"""Realistic fill simulation — the part that decides whether paper results mean anything.

Rules (non-negotiable, from the mission):
  * A market BUY fills at the ASK plus slippage. A market SELL fills at the BID minus
    slippage. **Never at the midpoint** — midpoint fills are the single most common way
    a paper backtest flatters itself into a strategy that loses real money.
  * A LIMIT order fills only when price actually TRADES THROUGH the limit, not when it
    merely touches. Touch-fills manufacture entries that would have been missed.
  * A STOP can GAP: if the bar opens beyond the stop, the fill is at the OPEN (worse),
    not at the stop price. Stops are not guarantees.
  * Orders larger than a share of the bar's volume fill PARTIALLY.
  * Fees are configurable and always deducted.
  * Orders can be REJECTED (no quote, non-positive qty, crossed book) or EXPIRE (DAY).

A `Quote` is the market snapshot a fill is evaluated against. When only a last price is
available we synthesise a spread from config rather than pretending the spread is zero.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, Optional

from . import config as cfg


@dataclass(frozen=True)
class Quote:
    """A market snapshot. `bid`/`ask` are preferred; if absent they are derived from
    `last` using the configured default spread (never a zero spread)."""
    symbol: str
    last: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    volume: Optional[float] = None
    source_ts: Optional[float] = None
    provider: Optional[str] = None

    def resolved(self) -> "Quote":
        if self.bid is not None and self.ask is not None and self.ask >= self.bid:
            return self
        if self.last is None:
            return self
        half = self.last * (cfg.execution().spread_bps_default / 10000.0) / 2.0
        return Quote(self.symbol, self.last, round(self.last - half, 4),
                     round(self.last + half, 4), self.open, self.high, self.low,
                     self.volume, self.source_ts, self.provider)

    @property
    def has_market(self) -> bool:
        q = self.resolved()
        return q.bid is not None and q.ask is not None and q.ask > 0 and q.bid > 0


@dataclass
class FillResult:
    status: str                       # filled | partial | rejected | expired | pending
    quantity: float = 0.0
    price: Optional[float] = None
    fees: float = 0.0
    slippage: Optional[float] = None  # per share, vs the reference price
    reference: Optional[float] = None
    liquidity: str = "normal"         # normal | gap | partial
    reason: str = ""
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "quantity": self.quantity, "price": self.price,
                "fees": self.fees, "slippage": self.slippage, "reference": self.reference,
                "liquidity": self.liquidity, "reason": self.reason, "note": self.note}


def fee_for(quantity: float, price: float) -> float:
    e = cfg.execution()
    fee = quantity * e.fee_per_share + (quantity * price) * (e.fee_pct / 100.0)
    if fee > 0 or e.fee_min > 0:
        fee = max(fee, e.fee_min)
    return round(fee, 4)


def _slip(price: float, bps: float) -> float:
    return price * (bps / 10000.0)


def _partial(quantity: float, q: Quote) -> tuple:
    """Return (fillable_qty, is_partial). An order that is large relative to the bar's
    volume cannot realistically fill in full."""
    e = cfg.execution()
    if not e.allow_partial_fills or not q.volume or q.volume <= 0:
        return quantity, False
    max_qty = q.volume * e.partial_fill_threshold
    if quantity > max_qty:
        return round(max(1.0, max_qty * e.partial_fill_ratio + 0.0), 6), True
    return quantity, False


def simulate_market(side: str, quantity: float, q: Quote) -> FillResult:
    """Market order: BUY at ask+slippage, SELL at bid-slippage. Never midpoint."""
    e = cfg.execution()
    if quantity <= 0:
        return FillResult("rejected", reason="non-positive quantity")
    qr = q.resolved()
    if not qr.has_market:
        return FillResult("rejected", reason="no quote available (cannot fill)")
    if qr.ask < qr.bid:
        return FillResult("rejected", reason="crossed book (ask < bid)")

    if side.upper() == "BUY":
        ref = qr.ask
        price = ref + _slip(ref, e.slippage_bps)
    else:
        ref = qr.bid
        price = ref - _slip(ref, e.slippage_bps)
    price = round(max(price, 0.0001), 4)

    qty, is_partial = _partial(quantity, qr)
    return FillResult(
        status="partial" if is_partial else "filled",
        quantity=qty, price=price, fees=fee_for(qty, price),
        slippage=round(abs(price - ref), 6), reference=ref,
        liquidity="partial" if is_partial else "normal",
        note=f"market {side.upper()} vs {'ask' if side.upper()=='BUY' else 'bid'} {ref}")


def simulate_limit(side: str, quantity: float, limit_price: float, q: Quote) -> FillResult:
    """Limit order. Fills ONLY when the bar trades through the limit (strictly better),
    at the limit price. A mere touch does not fill."""
    e = cfg.execution()
    if quantity <= 0:
        return FillResult("rejected", reason="non-positive quantity")
    if limit_price is None or limit_price <= 0:
        return FillResult("rejected", reason="invalid limit price")
    qr = q.resolved()
    lo, hi = qr.low, qr.high
    if lo is None or hi is None:
        # Without a bar range, fall back to the quote: only marketable limits fill.
        if not qr.has_market:
            return FillResult("pending", reason="no quote or bar to evaluate limit")
        through = (qr.ask < limit_price) if side.upper() == "BUY" else (qr.bid > limit_price)
        if not through:
            return FillResult("pending", reason="limit not marketable")
        qty, is_partial = _partial(quantity, qr)
        price = round(limit_price, 4)
        return FillResult("partial" if is_partial else "filled", qty, price,
                          fee_for(qty, price), 0.0, limit_price,
                          "partial" if is_partial else "normal",
                          note="limit marketable against quote")

    if side.upper() == "BUY":
        traded_through = (lo < limit_price) if e.limit_needs_trade_through else (lo <= limit_price)
    else:
        traded_through = (hi > limit_price) if e.limit_needs_trade_through else (hi >= limit_price)

    if not traded_through:
        return FillResult("pending", reason=f"price never traded through {limit_price}")

    qty, is_partial = _partial(quantity, qr)
    price = round(limit_price, 4)
    return FillResult("partial" if is_partial else "filled", qty, price,
                      fee_for(qty, price), 0.0, limit_price,
                      "partial" if is_partial else "normal",
                      note="limit filled on trade-through")


def simulate_stop(side: str, quantity: float, stop_price: float, q: Quote) -> FillResult:
    """Stop order, including GAPS.

    If the bar OPENS beyond the stop, the realistic fill is the OPEN (plus gap
    slippage) — materially worse than the stop. Otherwise, if the bar trades through
    the stop, it becomes a market order at the stop plus normal slippage.
    """
    e = cfg.execution()
    if quantity <= 0:
        return FillResult("rejected", reason="non-positive quantity")
    if stop_price is None or stop_price <= 0:
        return FillResult("rejected", reason="invalid stop price")
    qr = q.resolved()
    o, lo, hi = qr.open, qr.low, qr.high
    sell = side.upper() == "SELL"

    # 1) GAP — the bar opened through the stop; you get the open, not the stop.
    #    This is the whole point: a stop is a trigger, not a guaranteed price.
    if o is not None:
        gapped = (o <= stop_price) if sell else (o >= stop_price)
        if gapped:
            slip = _slip(o, e.gap_slippage_bps)
            price = round(max((o - slip) if sell else (o + slip), 0.0001), 4)
            qty, is_partial = _partial(quantity, qr)
            return FillResult("partial" if is_partial else "filled", qty, price,
                              fee_for(qty, price),
                              slippage=round(abs(price - stop_price), 6),
                              reference=stop_price, liquidity="gap",
                              note=f"STOP GAPPED: opened {o} through stop {stop_price}")

    # 2) Triggered intrabar — market fill at the stop plus normal slippage.
    triggered = (lo is not None and lo <= stop_price) if sell else (hi is not None and hi >= stop_price)
    if not triggered:
        return FillResult("pending", reason=f"stop {stop_price} not triggered")
    slip = _slip(stop_price, e.slippage_bps)
    price = round(max((stop_price - slip) if sell else (stop_price + slip), 0.0001), 4)
    qty, is_partial = _partial(quantity, qr)
    return FillResult("partial" if is_partial else "filled", qty, price,
                      fee_for(qty, price), round(abs(price - stop_price), 6),
                      stop_price, "partial" if is_partial else "normal",
                      note="stop triggered intrabar")


def simulate(order_type: str, side: str, quantity: float, q: Quote,
             limit_price: Optional[float] = None,
             stop_price: Optional[float] = None) -> FillResult:
    t = (order_type or "MARKET").upper()
    if t == "MARKET":
        return simulate_market(side, quantity, q)
    if t == "LIMIT":
        return simulate_limit(side, quantity, limit_price, q)
    if t == "STOP":
        return simulate_stop(side, quantity, stop_price, q)
    return FillResult("rejected", reason=f"unsupported order type {order_type}")


# ── Deterministic identifiers ────────────────────────────────────────────────

def order_id(symbol: str, side: str, session_date: str, seq: int,
             strategy: str = "") -> str:
    """Deterministic, collision-resistant, and reproducible from its inputs — so an
    order can be traced back to exactly what produced it."""
    raw = f"{session_date}|{symbol.upper()}|{side.upper()}|{strategy}|{seq}"
    return "ord_" + hashlib.sha1(raw.encode()).hexdigest()[:16]


def fill_id(oid: str, seq: int) -> str:
    return "fil_" + hashlib.sha1(f"{oid}|{seq}".encode()).hexdigest()[:16]


def signal_id(symbol: str, strategy: str, created_at: str) -> str:
    raw = f"{created_at}|{symbol.upper()}|{strategy}"
    return "sig_" + hashlib.sha1(raw.encode()).hexdigest()[:16]


def position_id(symbol: str, opened_at: str) -> str:
    return "pos_" + hashlib.sha1(f"{symbol.upper()}|{opened_at}".encode()).hexdigest()[:16]


# ── Corporate actions (applied where data exists) ────────────────────────────

def apply_split(quantity: float, avg_entry: float, ratio: float) -> tuple:
    """A 2:1 split -> ratio 2.0: double the shares, halve the basis. Applied only when
    a provider actually reports a split; we never guess."""
    if not ratio or ratio <= 0:
        return quantity, avg_entry
    return round(quantity * ratio, 6), round(avg_entry / ratio, 6)


def apply_cash_dividend(quantity: float, dividend_per_share: float) -> float:
    """Cash credited to the paper account on the ex-date (position size unchanged)."""
    if not dividend_per_share or dividend_per_share <= 0:
        return 0.0
    return round(quantity * dividend_per_share, 4)
