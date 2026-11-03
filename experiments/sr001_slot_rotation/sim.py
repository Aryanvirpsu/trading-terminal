"""SR-001 paired-account mechanics — DRY-RUN PROTOTYPE (not frozen, no outcome data).

Pure functions of the inputs it is handed: no I/O, no clock, no network. It exists so the SR-001 mechanics can be
tested on synthetic fixtures before any real evidence is collected. Both arms use the same Account class; the only
difference is `rotate=True` on the challenger.

Interpretations of SR001_PROTOCOL.md made here (flagged for the owner, see DRYRUN_REPORT.md):
  I1 "completed at least 10 regular sessions after the fill session"  -> session_index(now) - session_index(fill) >= 11
  I2 reserved-entry sizing: the reserved entry must be fundable at its FULL planned size from settled cash (otherwise
     it waits); ordinary entries (both arms) may be cash-limited, as in production sizing.
  I3 T+1: proceeds of a sale become settled cash at the open of the next regular session.
  I4 the exit cutoff is a per-session timestamp supplied by the calendar.
"""
from __future__ import annotations

import dataclasses as dc
import datetime as dt
from typing import Dict, List, Optional, Tuple

EPS = 1e-9


@dc.dataclass(frozen=True)
class Cfg:
    slots: int = 3
    reserve: float = 100.0
    max_notional: float = 125.0
    max_loss: float = 5.0
    cost_bps: float = 15.0              # adverse, per side
    max_entries_day: int = 2
    min_age_sessions: int = 10
    quote_fresh_s: float = 60.0
    min_notional: float = 1.0


@dc.dataclass(frozen=True)
class Session:
    date: dt.date
    open: dt.datetime
    entry_cutoff: dt.datetime
    exit_cutoff: dt.datetime
    close: dt.datetime


@dc.dataclass(frozen=True)
class Quote:
    bid: float
    ask: float
    received: dt.datetime
    executable: bool = True


@dc.dataclass(frozen=True)
class Cand:
    symbol: str
    rank: int
    eligible: bool          # TRADEABLE and every entry gate except slot limit / cash
    stop: float


@dc.dataclass(frozen=True)
class Cycle:
    ts: dt.datetime
    session: int
    candidates: Tuple[Cand, ...] = ()
    quotes: Dict[str, Quote] = dc.field(default_factory=dict)
    ordinary_exits: frozenset = frozenset()


class Account:
    def __init__(self, cfg: Cfg, sessions: List[Session], cash: float = 500.0, rotate: bool = False):
        self.cfg, self.sessions, self.rotate = cfg, sessions, rotate
        self.cash = float(cash)                       # settled cash only
        self.recv: List[Tuple[dt.datetime, float]] = []  # (available_at, amount)
        self.pos: Dict[str, dict] = {}
        self.entries: Dict[int, int] = {}
        self.attempts: Dict[int, int] = {}
        self.pending_exit: Optional[dict] = None
        self.reservation: Optional[dict] = None
        self.events: List[tuple] = []
        self.exit_failures = 0

    # ---- helpers
    def _c(self) -> float:
        return self.cfg.cost_bps / 1e4

    def _log(self, kind: str, ts: dt.datetime, **kw) -> None:
        self.events.append((kind, ts.isoformat(), tuple(sorted(kw.items()))))

    def seed(self, symbol: str, qty: float, fill: float, session: int, ts: dt.datetime) -> None:
        self.pos[symbol] = {"qty": qty, "fill": fill, "session": session, "ts": ts}

    def _fresh(self, q: Optional[Quote], ts: dt.datetime) -> bool:
        return bool(q and q.executable and 0 <= (ts - q.received).total_seconds() <= self.cfg.quote_fresh_s)

    def _plan(self, cand: Cand, q: Quote) -> Optional[Tuple[float, float, float]]:
        """(fill, full_qty, cost) at the frozen sizing, ignoring cash; None if the plan is unusable."""
        fill = q.ask * (1 + self._c())
        risk = fill - cand.stop
        if risk <= 0:
            return None
        qty = min(self.cfg.max_loss / risk, self.cfg.max_notional / fill)
        return fill, qty, qty * fill

    def _settle(self, ts: dt.datetime) -> None:
        keep = []
        for avail, amt in self.recv:
            if avail <= ts:
                self.cash += amt
            else:
                keep.append((avail, amt))
        self.recv = keep

    def _sell(self, sym: str, q: Quote, ts: dt.datetime, session: int, why: str) -> None:
        p = self.pos.pop(sym)
        px = q.bid * (1 - self._c())
        amt = p["qty"] * px
        nxt = session + 1
        avail = self.sessions[nxt].open if nxt < len(self.sessions) else dt.datetime.max.replace(tzinfo=ts.tzinfo)
        self.recv.append((avail, amt))
        self._log("sell", ts, symbol=sym, why=why, px=round(px, 6), pnl=round(amt - p["qty"] * p["fill"], 6))

    def _buy(self, cand: Cand, q: Quote, ts: dt.datetime, session: int, full_size_required: bool) -> str:
        if self.entries.get(session, 0) >= self.cfg.max_entries_day:
            return "daily_limit"
        plan = self._plan(cand, q)
        if plan is None:
            return "bad_plan"
        fill, full_qty, _ = plan
        qty = min(full_qty, max(0.0, self.cash - self.cfg.reserve) / fill)
        if full_size_required and qty < full_qty - EPS:
            return "awaiting_settled_cash"
        if qty * fill < self.cfg.min_notional - EPS:
            return "cash_short"
        self.cash -= qty * fill
        self.pos[cand.symbol] = {"qty": qty, "fill": fill, "session": session, "ts": ts}
        self.entries[session] = self.entries.get(session, 0) + 1
        self._log("buy", ts, symbol=cand.symbol, qty=round(qty, 6), px=round(fill, 6))
        return "ok"

    # ---- one decision cycle
    def step(self, cy: Cycle) -> None:
        s = self.sessions[cy.session]
        self._settle(cy.ts)
        self._expire(cy)
        for sym in sorted(cy.ordinary_exits):
            q = cy.quotes.get(sym)
            if sym in self.pos and q and q.executable:
                self._sell(sym, q, cy.ts, cy.session, "ordinary")
        if self.pending_exit and cy.ts > self.pending_exit["ts"] and cy.ts <= s.exit_cutoff:
            self._try_rotation_exit(cy)
        if not (s.open <= cy.ts <= s.entry_cutoff):
            return
        self._reserved_entry(cy)
        self._entries(cy)
        if self.rotate:
            self._rotation(cy)

    def _expire(self, cy: Cycle) -> None:
        pe = self.pending_exit
        if pe and (cy.session > pe["session"] or cy.ts > self.sessions[pe["session"]].exit_cutoff):
            self.pending_exit = None
            self.exit_failures += 1
            self._log("rotation_exit_cancelled", cy.ts, symbol=pe["symbol"])
            if self.reservation:
                self.reservation = None
        rs = self.reservation
        if rs and cy.session > rs["expires"]:
            if rs["exit_done"]:
                self._log("orphan_exit", cy.ts, symbol=rs["exit_symbol"], reserved=rs["symbol"])
            self.reservation = None

    def _try_rotation_exit(self, cy: Cycle) -> None:
        sym = self.pending_exit["symbol"]
        q = cy.quotes.get(sym)
        if not self._fresh(q, cy.ts):
            return
        self._sell(sym, q, cy.ts, cy.session, "rotation")
        self.pending_exit = None
        self.reservation["exit_done"] = True
        self.reservation["exit_ts"] = cy.ts

    def _reserved_entry(self, cy: Cycle) -> None:
        rs = self.reservation
        if not (rs and rs["exit_done"]) or len(self.pos) >= self.cfg.slots or cy.ts <= rs["exit_ts"]:
            return
        cand = next((c for c in cy.candidates if c.symbol == rs["symbol"]), None)
        q = cy.quotes.get(rs["symbol"])
        if not (cand and cand.eligible and self._fresh(q, cy.ts)):
            self._log("reservation_revalidation_failed", cy.ts, symbol=rs["symbol"])
            return
        res = self._buy(cand, q, cy.ts, cy.session, full_size_required=True)
        if res == "ok":
            self.reservation = None
        else:
            self._log("reservation_wait", cy.ts, symbol=rs["symbol"], why=res)

    def _entries(self, cy: Cycle) -> None:
        for c in sorted(cy.candidates, key=lambda c: (c.rank, c.symbol)):
            free = self.cfg.slots - len(self.pos) - (1 if self.reservation else 0)
            if free <= 0:
                return
            q = cy.quotes.get(c.symbol)
            if c.eligible and c.symbol not in self.pos and self._fresh(q, cy.ts):
                if self.reservation and c.symbol == self.reservation["symbol"]:
                    continue
                self._buy(c, q, cy.ts, cy.session, full_size_required=False)

    def _rotation(self, cy: Cycle) -> None:
        if (len(self.pos) < self.cfg.slots or self.reservation or self.pending_exit
                or self.attempts.get(cy.session, 0) >= 1):
            return
        cand = next((c for c in sorted(cy.candidates, key=lambda c: (c.rank, c.symbol))
                     if c.eligible and c.symbol not in self.pos), None)
        if cand is None or self.entries.get(cy.session, 0) >= self.cfg.max_entries_day:
            return
        cq = cy.quotes.get(cand.symbol)
        if not self._fresh(cq, cy.ts):
            return
        elig = []
        for sym, p in self.pos.items():
            q = cy.quotes.get(sym)
            if (cy.session - p["session"] >= self.cfg.min_age_sessions + 1 and sym not in cy.ordinary_exits
                    and self._fresh(q, cy.ts) and q.bid <= p["fill"] + EPS):
                elig.append((p["ts"], sym))
        if not elig:
            return
        _, inc = min(elig)
        plan = self._plan(cand, cq)
        if plan is None:
            return
        proceeds = self.pos[inc]["qty"] * cy.quotes[inc].bid * (1 - self._c())
        if self.cash + proceeds - plan[2] < self.cfg.reserve - EPS:
            self._log("rotation_cash_short", cy.ts, candidate=cand.symbol, incumbent=inc)
            return
        self.attempts[cy.session] = 1
        self.pending_exit = {"symbol": inc, "ts": cy.ts, "session": cy.session}
        self.reservation = {"symbol": cand.symbol, "expires": cy.session + 1, "exit_done": False, "exit_symbol": inc}
        self._log("rotation_submitted", cy.ts, candidate=cand.symbol, incumbent=inc)

    # ---- valuation
    def equity(self, quotes: Dict[str, Quote]) -> float:
        held = sum(p["qty"] * quotes[s].bid * (1 - self._c()) for s, p in self.pos.items())
        return round(self.cash + sum(a for _, a in self.recv) + held, 6)


def run(cycles: List[Cycle], cfg: Cfg, sessions: List[Session], rotate: bool, cash: float = 500.0,
        seed: Optional[List[tuple]] = None) -> Account:
    acct = Account(cfg, sessions, cash=cash, rotate=rotate)
    for s in seed or []:
        acct.seed(*s)
    for cy in cycles:
        acct.step(cy)
    return acct
