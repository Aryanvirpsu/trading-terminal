"""CH-003 replay engine — pure functions, no I/O.  Implements exactly what PREREG.md fixes.

One engine, many arms: an arm is just `Params(time_stop_n=...)`; CHAMPION is `time_stop_n=None`. Every other rule is identical
across arms, so a difference between arms can only come from the time stop.

Conventions (all from PREREG.md): buy at ask·(1+slip); sizing qty = min(risk$/(fill − stop·(1−slip)), notional$/fill); exits evaluated on
5-minute bars that BEGIN at/after the entry cycle; same-bar stop+target ⇒ stop first (flagged); stop gap (bar opens ≤ stop) exits at
open·(1−gap); target is a limit (no slippage); slot freed at the END of the exit bar and usable by the NEXT cycle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

BAR = timedelta(minutes=5)


@dataclass(frozen=True)
class Bar:
    start: datetime
    o: float
    h: float
    l: float
    c: float

    @property
    def end(self) -> datetime:
        return self.start + BAR

    @property
    def day(self):
        return self.start.date()


@dataclass(frozen=True)
class Cand:
    """One TRADEABLE observation, decision-time facts only."""
    scan_ts: datetime
    session_date: str
    symbol: str
    sector: str
    event_id: str
    rank: int
    stop: float
    target: float
    ask: float


@dataclass(frozen=True)
class Params:
    slip_bps: float = 5.0
    gap_bps: float = 25.0
    time_stop_n: Optional[int] = None
    max_open: int = 3
    per_sector: int = 1
    max_per_day: int = 2
    sector_exposure: float = 0.30
    notional: float = 125.0
    risk_usd: float = 5.0
    reserve: float = 100.0
    equity0: float = 504.66
    cooldown_sessions: int = 10

    @property
    def slip(self) -> float:
        return self.slip_bps / 1e4

    @property
    def gap(self) -> float:
        return self.gap_bps / 1e4


@dataclass
class Pos:
    symbol: str
    sector: str
    event_id: str
    entry_ts: datetime
    entry_day: str
    entry_sess: int
    fill: float
    stop: float
    target: float
    qty: float
    r_price: float                      # fill - stop : price risk per share at fill
    level1r: float
    i: int                              # next bar index to process
    touched1r: bool = False
    exit_ts: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None   # stop | gap_stop | target | time_stop | None (still open)
    ambiguous: bool = False
    exit_sess: Optional[int] = None
    path: List[Tuple[datetime, float]] = field(default_factory=list)   # (bar end, mark-to-market $ while open)

    @property
    def notional(self) -> float:
        return self.qty * self.fill


def sizing(ask: float, stop: float, p: Params) -> Tuple[float, float]:
    """(fill, qty). Post risk-fix formula: the $ risk must survive the executable (slipped) prices."""
    fill = ask * (1 + p.slip)
    risk_exec = fill - stop * (1 - p.slip)
    if risk_exec <= 0:
        return fill, 0.0
    return fill, min(p.risk_usd / risk_exec, p.notional / fill)


def first_bar_at_or_after(bars: Sequence[Bar], t: datetime) -> int:
    lo, hi = 0, len(bars)
    while lo < hi:
        mid = (lo + hi) // 2
        if bars[mid].start >= t:
            hi = mid
        else:
            lo = mid + 1
    return lo


def advance(pos: Pos, bars: Sequence[Bar], upto: Optional[datetime], sess: Dict, p: Params) -> None:
    """Process this position's bars that have COMPLETED by `upto` (None = all). Sets exit_* when it exits."""
    while pos.exit_ts is None and pos.i < len(bars) and (upto is None or bars[pos.i].end <= upto):
        b = bars[pos.i]
        prev = bars[pos.i - 1] if pos.i > 0 else None
        # session boundary: the time stop is decided at the CLOSE of session entry+N and executes at the next session's open
        if p.time_stop_n is not None and prev is not None and prev.day != b.day and not pos.touched1r:
            if sess[prev.day] - pos.entry_sess >= p.time_stop_n:
                _exit(pos, b.end, b.o * (1 - p.slip), "time_stop", sess)
                return
        if b.o <= pos.stop:
            _exit(pos, b.end, b.o * (1 - p.gap), "gap_stop", sess)
            return
        if b.l <= pos.stop:
            pos.ambiguous = b.h >= pos.target                       # both inside one bar: unknowable order -> stop first
            _exit(pos, b.end, pos.stop * (1 - p.slip), "stop", sess)
            return
        if b.h >= pos.target:
            _exit(pos, b.end, pos.target, "target", sess)
            return
        if b.h >= pos.level1r:
            pos.touched1r = True
        pos.path.append((b.end, pos.qty * (b.c - pos.fill)))
        pos.i += 1


def _exit(pos: Pos, ts: datetime, price: float, reason: str, sess: Dict) -> None:
    pos.exit_ts, pos.exit_price, pos.exit_reason = ts, price, reason
    pos.exit_sess = sess[(ts - BAR).date()]
    pos.path.append((ts, pos.qty * (price - pos.fill)))


def hold_outcome(symbol_bars: Sequence[Bar], entry_ts: datetime, stop: float, target: float, fill: float, sess: Dict, p: Params) -> Dict:
    """What the SAME entry would do with NO time stop (Champion exits) — used for time-stop regret."""
    q = Params(**{**p.__dict__, "time_stop_n": None})
    r = fill - stop
    pos = Pos("", "", "", entry_ts, str(entry_ts.date()), sess[entry_ts.date()], fill, stop, target, 1.0, r, fill + r,
              first_bar_at_or_after(symbol_bars, entry_ts))
    advance(pos, symbol_bars, None, sess, q)
    if pos.exit_ts is not None:
        return {"state": pos.exit_reason, "R": (pos.exit_price - fill) / r, "closed": True}
    last = symbol_bars[-1].c
    return {"state": "open", "R": (last - fill) / r, "closed": False}


def run_arm(cycles: Sequence[Tuple[datetime, str, List[Cand]]], bars: Dict[str, List[Bar]], sess: Dict, p: Params) -> Dict:
    """Replay every regular cycle in time order. `cycles` = [(scan_ts, session_date, [TRADEABLE candidates])]."""
    open_pos: List[Pos] = []
    done: List[Pos] = []
    entries_by_day: Dict[str, int] = {}
    entered_today: Dict[str, set] = {}
    cooldown: Dict[str, int] = {}          # symbol -> first session index at which it may be re-entered
    skipped: List[Dict] = []

    for scan_ts, sdate, cands in sorted(cycles, key=lambda c: c[0]):
        for ps in list(open_pos):          # exits that COMPLETED before this cycle free their slot for it
            advance(ps, bars[ps.symbol], scan_ts, sess, p)
            if ps.exit_ts is not None:
                open_pos.remove(ps)
                done.append(ps)
                if ps.exit_reason == "time_stop":
                    cooldown[ps.symbol] = (ps.exit_sess or 0) + p.cooldown_sessions
        today_idx = sess[datetime.fromisoformat(sdate).date()]
        for c in sorted(cands, key=lambda c: (c.rank, c.symbol)):
            why = None
            held = {x.symbol for x in open_pos}
            if c.symbol in held or c.symbol in entered_today.get(sdate, set()):
                why = "symbol already held or entered this session"
            elif cooldown.get(c.symbol, -1) > today_idx:
                why = "time-stop cooldown"
            elif entries_by_day.get(sdate, 0) >= p.max_per_day:
                why = "daily order cap"
            elif len(open_pos) >= p.max_open:
                why = "max_open_positions"
            elif sum(x.sector == c.sector for x in open_pos) >= p.per_sector:
                why = "max_positions_per_sector"
            else:
                fill, qty = sizing(c.ask, c.stop, p)
                invested = sum(x.notional for x in open_pos)
                sector_notional = sum(x.notional for x in open_pos if x.sector == c.sector)
                if qty <= 0 or fill <= c.stop:
                    why = "invalid levels"
                elif (sector_notional + qty * fill) / p.equity0 > p.sector_exposure:
                    why = "max_sector_exposure"
                elif p.equity0 - invested - qty * fill < p.reserve:
                    why = "cash reserve"
            if why:
                skipped.append({"ts": scan_ts.isoformat(), "symbol": c.symbol, "why": why})
                continue
            sb = bars[c.symbol]
            i0 = first_bar_at_or_after(sb, scan_ts)
            if i0 >= len(sb):
                skipped.append({"ts": scan_ts.isoformat(), "symbol": c.symbol, "why": "no bars after decision"})
                continue
            r = fill - c.stop
            ps = Pos(c.symbol, c.sector, c.event_id, scan_ts, sdate, today_idx, fill, c.stop, c.target, qty, r, fill + r, i0)
            open_pos.append(ps)
            entries_by_day[sdate] = entries_by_day.get(sdate, 0) + 1
            entered_today.setdefault(sdate, set()).add(c.symbol)

    for ps in open_pos:                     # run everything to the end of the data
        advance(ps, bars[ps.symbol], None, sess, p)
    all_pos = done + open_pos
    return {"positions": all_pos, "skipped": skipped}


# ── evaluation ──────────────────────────────────────────────────────────────────────────────────────────────────
def position_result(ps: Pos, bars: Sequence[Bar], sess: Dict) -> Dict:
    closed = ps.exit_ts is not None
    px = ps.exit_price if closed else bars[-1].c
    r = (px - ps.fill) / ps.r_price
    last_sess = ps.exit_sess if closed else sess[bars[-1].day]
    return {"symbol": ps.symbol, "sector": ps.sector, "event_id": ps.event_id, "entry_ts": ps.entry_ts.isoformat(), "entry_session": ps.entry_day,
            "fill": round(ps.fill, 4), "stop": ps.stop, "target": ps.target, "qty": round(ps.qty, 6), "r_price": round(ps.r_price, 4),
            "state": ps.exit_reason or "open", "exit_ts": ps.exit_ts.isoformat() if closed else None,
            "exit_price": round(ps.exit_price, 4) if closed else None, "mark_price": None if closed else round(px, 4),
            "R": round(r, 4), "pnl_usd": round(ps.qty * (px - ps.fill), 4), "sessions_held": last_sess - ps.entry_sess,
            "touched_1r": ps.touched1r, "ambiguous": ps.ambiguous, "notional": round(ps.notional, 2)}


def equity_stats(positions: Sequence[Pos], timeline: Sequence[datetime], p: Params) -> Dict:
    """Bar-level mark-to-market equity: equity0 + realized + open P&L; max drawdown ($) and mean capital utilisation."""
    peak = eq0 = p.equity0
    max_dd = 0.0
    util_sum = 0.0
    n = 0
    for t in timeline:
        total = 0.0
        notional = 0.0
        for ps in positions:
            if ps.entry_ts >= t:
                continue
            if ps.exit_ts is not None and ps.exit_ts <= t:
                total += ps.qty * (ps.exit_price - ps.fill)
                continue
            m = None
            for ts, v in ps.path:
                if ts <= t:
                    m = v
                else:
                    break
            total += m or 0.0
            notional += ps.notional
        eq = eq0 + total
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
        util_sum += notional / p.equity0
        n += 1
    return {"max_drawdown_usd": round(max_dd, 4), "mean_utilisation": round(util_sum / n, 4) if n else 0.0}


def longest_losing_streak(results: Sequence[Dict]) -> int:
    best = cur = 0
    for r in sorted((x for x in results if x["state"] != "open"), key=lambda x: x["exit_ts"]):
        cur = cur + 1 if r["R"] < 0 else 0
        best = max(best, cur)
    return best
