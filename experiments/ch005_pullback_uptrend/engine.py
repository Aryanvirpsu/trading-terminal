"""CH-005 engine: pullback-in-uptrend on daily bars, simulated under AVDI's $500 cash-account constraints.

Pure functions + one deterministic simulator.  Everything is causal: an indicator at session t uses only bars <= t; a signal formed at the
close of t is acted on at the OPEN of t+1.  The rules implemented here are exactly those frozen in PREREG.md (read that first).
No file or network access except `load_prices`.
"""
from __future__ import annotations

import gzip
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Params:
    # --- signal (PREREG section 3) ---
    sma_trend: int = 200
    bb_n: int = 15
    bb_k: float = 1.75
    rsi_n: int = 14
    rsi_max: float = 45.0
    below_high_pct: float = 0.03
    high_n: int = 60
    dv_n: int = 21
    dv_min: float = 5_000_000.0
    # --- exits ---
    atr_n: int = 14
    stop_atr: float = 2.0
    max_hold: int = 15
    # --- account (AVDI) ---
    start_cash: float = 500.0
    risk_per_trade: float = 5.0
    max_notional: float = 125.0
    max_positions: int = 3
    max_entries_per_day: int = 2
    reserve: float = 100.0
    daily_loss_halt: float = 10.0
    drawdown_halt: float = 50.0
    min_notional: float = 10.0
    # --- costs (per side, bps) ---
    cost_bps: float = 15.0
    gap_extra_bps: float = 20.0


# ───────────────────────────── data ─────────────────────────────

def load_prices(path) -> Dict[str, Dict[str, list]]:
    return json.loads(gzip.open(path).read())


def build_frame(bars: Dict[str, Dict[str, list]], cal: List[str]) -> Dict[str, Dict[str, np.ndarray]]:
    """Reindex every symbol onto the SPY session calendar (NaN where a bar is missing)."""
    out = {}
    for s, b in bars.items():
        idx = pd.Index(b["d"])
        d = {}
        for k in ("o", "h", "l", "c", "v"):
            d[k] = pd.Series(b[k], index=idx, dtype="float64").reindex(cal).to_numpy()
        out[s] = d
    return out


# ───────────────────────────── indicators (causal) ─────────────────────────────

def _first_valid(*arrs) -> int:
    ok = np.ones(len(arrs[0]), dtype=bool)
    for a in arrs:
        ok &= np.isfinite(a)
    idx = np.flatnonzero(ok)
    return int(idx[0]) if len(idx) else len(arrs[0])


def wilder_rsi(c: np.ndarray, n: int) -> np.ndarray:
    """Wilder RSI; seeded with the simple mean of the first n changes of the symbol's own history; NaN until then.
    Leading NaNs (before a listing) are skipped; an interior gap breaks the recursion (NaN afterwards, fail closed)."""
    f = _first_valid(c)
    if f:
        res = np.full(len(c), np.nan)
        res[f:] = wilder_rsi(c[f:], n)
        return res
    out = np.full(len(c), np.nan)
    if len(c) <= n:
        return out
    ch = np.diff(c)
    up, dn = np.where(ch > 0, ch, 0.0), np.where(ch < 0, -ch, 0.0)
    if np.isnan(ch[:n]).any():
        return out
    au, ad = up[:n].mean(), dn[:n].mean()
    out[n] = 100.0 if ad == 0 else 100 - 100 / (1 + au / ad)
    for i in range(n, len(ch)):
        if np.isnan(ch[i]):
            return out                                    # a gap breaks the recursion: stay NaN afterwards (fail closed)
        au = (au * (n - 1) + up[i]) / n
        ad = (ad * (n - 1) + dn[i]) / n
        out[i + 1] = 100.0 if ad == 0 else 100 - 100 / (1 + au / ad)
    return out


def wilder_atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int) -> np.ndarray:
    f = _first_valid(h, l, c)
    if f:
        res = np.full(len(c), np.nan)
        res[f:] = wilder_atr(h[f:], l[f:], c[f:], n)
        return res
    out = np.full(len(c), np.nan)
    if len(c) <= n:
        return out
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    if np.isnan(tr[:n]).any():
        return out
    a = tr[:n].mean()
    out[n] = a
    for i in range(n, len(tr)):
        if np.isnan(tr[i]):
            return out
        a = (a * (n - 1) + tr[i]) / n
        out[i + 1] = a
    return out


def indicators(d: Dict[str, np.ndarray], p: Params) -> Dict[str, np.ndarray]:
    c = pd.Series(d["c"])
    ind = {
        "sma_trend": c.rolling(p.sma_trend).mean().to_numpy(),
        "sma_bb": c.rolling(p.bb_n).mean().to_numpy(),
        "sd_bb": c.rolling(p.bb_n).std(ddof=0).to_numpy(),
        "hi": c.rolling(p.high_n).max().to_numpy(),
        "dv": (c * pd.Series(d["v"])).rolling(p.dv_n).mean().to_numpy(),
        "rsi": wilder_rsi(d["c"], p.rsi_n),
        "atr": wilder_atr(d["h"], d["l"], d["c"], p.atr_n),
        "sma50": c.rolling(50).mean().to_numpy(),
    }
    return ind


def market_ok(spy: Dict[str, np.ndarray], p: Params) -> np.ndarray:
    sma = pd.Series(spy["c"]).rolling(p.sma_trend).mean().to_numpy()
    return np.where(np.isfinite(sma), spy["c"] > sma, False)


def signal_masks(d, ind, mkt_ok, p: Params):
    """(pullback signal, trend-only eligibility) for every session of one symbol; both strictly causal."""
    c = d["c"]
    with np.errstate(invalid="ignore"):
        base = (np.isfinite(ind["sma_trend"]) & np.isfinite(ind["dv"]) & (c > ind["sma_trend"]) & (ind["dv"] >= p.dv_min) & mkt_ok
                & np.isfinite(ind["atr"]) & (ind["atr"] > 0))
        pull = (base & np.isfinite(ind["sd_bb"]) & (ind["sd_bb"] > 0) & (c < ind["sma_bb"] - p.bb_k * ind["sd_bb"])
                & np.isfinite(ind["rsi"]) & (ind["rsi"] <= p.rsi_max) & np.isfinite(ind["hi"]) & (c <= (1 - p.below_high_pct) * ind["hi"]))
    return pull, base


# ───────────────────────────── simulator ─────────────────────────────

@dataclass
class Pos:
    sym: str
    sector: str
    shares: float
    entry_px: float
    stop: float
    entry_i: int
    risk: float
    notional: float
    flag: Optional[str] = None
    tags: dict = field(default_factory=dict)


def simulate(frame, ind, pull, base, spy_i: Dict[str, np.ndarray], sector: Dict[str, str], cal: List[str], p: Params,
             start: str, end: str, selector: str = "rank", seed: int = 0) -> dict:
    """Run one account over signal dates in [start, end] (entries stop after `end`; open trades run to their exit or the data end)."""
    n = len(cal)
    syms = [s for s in frame if s != "SPY"]
    i0 = next(i for i, x in enumerate(cal) if x >= start)
    i1 = max(i for i, x in enumerate(cal) if x <= end)                # last signal session
    rng = np.random.default_rng(seed)
    c_ = p.cost_bps / 1e4
    g_ = (p.cost_bps + p.gap_extra_bps) / 1e4
    settled, unsettled = p.start_cash, 0.0
    pos: List[Pos] = []
    trades: List[dict] = []
    eq_curve: List[tuple] = []
    peak = p.start_cash
    last_eq = p.start_cash
    skipped = {"small": 0, "no_bar": 0, "risk_nonpositive": 0}

    def close_trade(ps: Pos, i: int, px: float, reason: str):
        nonlocal unsettled
        pnl = ps.shares * (px - ps.entry_px)
        unsettled += ps.shares * px
        trades.append({"sym": ps.sym, "sector": ps.sector, "entry": cal[ps.entry_i], "exit": cal[i], "entry_px": round(ps.entry_px, 4), "exit_px": round(px, 4),
                       "shares": round(ps.shares, 6), "notional": round(ps.notional, 2), "stop": round(ps.stop, 4), "risk": round(ps.risk, 4),
                       "sessions": i - ps.entry_i + 1, "reason": reason, "pnl": pnl, "R": pnl / ps.risk if ps.risk else None, **ps.tags})
        return pnl

    for i in range(max(i0, 1), n):
        settled += unsettled
        unsettled = 0.0
        o, h, l, c = ({s: frame[s][k][i] for s in syms} for k in ("o", "h", "l", "c"))
        realized_today = 0.0
        # 1. planned exits (flagged at the previous close) fill at today's open
        for ps in list(pos):
            if ps.flag and np.isfinite(o[ps.sym]):
                px = o[ps.sym] * (1 - (g_ if o[ps.sym] <= ps.stop else c_))
                realized_today += close_trade(ps, i, px, ps.flag)
                pos.remove(ps)
        # 2. entries from yesterday's signals
        t = i - 1
        if i0 <= t <= i1:
            halted = realized_today <= -p.daily_loss_halt or (peak - last_eq) >= p.drawdown_halt
            if not halted:
                cands = [s for s in syms if (pull[s][t] if selector == "rank" else base[s][t])]
                if selector == "rank":
                    cands.sort(key=lambda s: (-(ind[s]["sma_bb"][t] - frame[s]["c"][t]) / ind[s]["sd_bb"][t], ind[s]["rsi"][t], s))
                else:
                    cands.sort()
                    rng.shuffle(cands)
                entries_today = 0
                for s in cands:
                    if len(pos) >= p.max_positions or entries_today >= p.max_entries_per_day:
                        break
                    if any(q.sym == s or q.sector == sector[s] for q in pos):
                        continue
                    if not np.isfinite(o[s]):
                        skipped["no_bar"] += 1
                        continue
                    entry = o[s] * (1 + c_)
                    stop = entry - p.stop_atr * ind[s]["atr"][t]
                    rps = entry - stop * (1 - c_)
                    if stop <= 0 or rps <= 0:
                        skipped["risk_nonpositive"] += 1
                        continue
                    shares = min(p.risk_per_trade / rps, p.max_notional / entry, max(0.0, settled - p.reserve) / entry)
                    if shares * entry < p.min_notional:
                        skipped["small"] += 1
                        continue
                    settled -= shares * entry
                    spy_t = spy_i["vol_bucket_src"][t]
                    pos.append(Pos(s, sector[s], shares, entry, stop, i, shares * rps, shares * entry,
                                   tags={"spy_vol20": float(spy_t) if np.isfinite(spy_t) else None, "spy_above_sma50": bool(spy_i["above50"][t]),
                                         "z": float((ind[s]["sma_bb"][t] - frame[s]["c"][t]) / ind[s]["sd_bb"][t]) if ind[s]["sd_bb"][t] > 0 else None}))
                    entries_today += 1
        # 3. protective stops (stop-first; includes the entry session)
        for ps in list(pos):
            lo = l[ps.sym]
            if np.isfinite(lo) and lo <= ps.stop:
                px = o[ps.sym] * (1 - g_) if (ps.entry_i < i and o[ps.sym] <= ps.stop) else ps.stop * (1 - c_)
                realized_today += close_trade(ps, i, px, "stop")
                pos.remove(ps)
        # 4. close: mark, then flag next-open exits
        mv = 0.0
        for ps in pos:
            cc = c[ps.sym]
            mv += ps.shares * (cc if np.isfinite(cc) else ps.entry_px)
        last_eq = settled + unsettled + mv
        peak = max(peak, last_eq)
        eq_curve.append((cal[i], last_eq))
        for ps in pos:
            sym = ps.sym
            if not np.isfinite(c[sym]):
                continue
            held = i - ps.entry_i + 1
            if c[sym] < ind[sym]["sma_trend"][i]:
                ps.flag = "trend_invalid"
            elif c[sym] >= ind[sym]["sma_bb"][i]:
                ps.flag = "recovery"
            elif held >= p.max_hold:
                ps.flag = "time"
        if t >= i1 and not pos:
            break
    # data ends with positions open: liquidate at the last close with the exit cost (reported separately)
    for ps in list(pos):
        cc = frame[ps.sym]["c"][n - 1]
        cc = cc if np.isfinite(cc) else ps.entry_px
        close_trade(ps, n - 1, cc * (1 - c_), "open_at_end")
        pos.remove(ps)
    return {"trades": trades, "equity": eq_curve, "skipped": skipped, "final_cash": settled + unsettled}


# ───────────────────────────── metrics ─────────────────────────────

def metrics(res: dict, p: Params, start: str, cal: List[str]) -> dict:
    tr = res["trades"]
    n = len(tr)
    out = {"trades": n}
    if not n:
        return out
    pnl = np.array([t["pnl"] for t in tr])
    R = np.array([t["R"] for t in tr])
    w, lo = pnl[pnl > 0], pnl[pnl < 0]
    out.update({"net_pnl": float(pnl.sum()), "wins": int((pnl > 0).sum()), "losses": int((pnl < 0).sum()), "win_rate": float((pnl > 0).mean()),
                "avg_win": float(w.mean()) if len(w) else None, "avg_loss": float(lo.mean()) if len(lo) else None,
                "profit_factor": float(w.sum() / -lo.sum()) if len(lo) else None, "expectancy_usd": float(pnl.mean()), "expectancy_R": float(R.mean()),
                "worst_trade": float(pnl.min()), "best_trade": float(pnl.max()), "open_at_end": sum(1 for t in tr if t["reason"] == "open_at_end"),
                "by_reason": {r: {"n": sum(1 for t in tr if t["reason"] == r), "pnl": float(sum(t["pnl"] for t in tr if t["reason"] == r))} for r in sorted({t["reason"] for t in tr})}})
    eq = np.array([e for _, e in res["equity"]])
    if len(eq):
        run = np.maximum.accumulate(np.concatenate([[p.start_cash], eq]))
        dd = run[1:] - eq
        out["max_drawdown_usd"] = float(dd.max())
        out["max_drawdown_pct"] = float((dd / run[1:]).max() * 100)
        out["final_equity"] = float(eq[-1])
    slot_days = int(sum(t["sessions"] for t in tr))
    cap_days = float(sum(t["notional"] * t["sessions"] for t in tr))
    sess = max(1, len(res["equity"]))
    out["slot_days"] = slot_days
    out["pnl_per_slot_day"] = float(pnl.sum() / slot_days)
    out["avg_capital_deployed"] = cap_days / sess
    out["return_on_capital_used_pct"] = float(pnl.sum() / (cap_days / sess) * 100) if cap_days else None
    out["avg_hold_sessions"] = float(np.mean([t["sessions"] for t in tr]))
    out["skipped"] = res["skipped"]
    return out


def bootstrap_lower(R: np.ndarray, q: float = 10.0, draws: int = 10_000, seed: int = 20261022) -> float:
    rng = np.random.default_rng(seed)
    m = rng.choice(R, size=(draws, len(R)), replace=True).mean(axis=1)
    return float(np.percentile(m, q))


def prepare(frame, p: Params):
    """Indicators, signal masks and SPY context for a whole frame (all strictly causal)."""
    ind = {s: indicators(d, p) for s, d in frame.items()}
    mk = market_ok(frame["SPY"], p)
    pull, base = {}, {}
    for s, d in frame.items():
        if s == "SPY":
            continue
        pull[s], base[s] = signal_masks(d, ind[s], mk, p)
    return ind, pull, base, spy_context(frame["SPY"], p), mk


def spy_context(spy: Dict[str, np.ndarray], p: Params) -> Dict[str, np.ndarray]:
    c = pd.Series(spy["c"])
    r = np.log(c).diff()
    vol20 = (r.rolling(20).std(ddof=0) * math.sqrt(252)).to_numpy()
    sma50 = c.rolling(50).mean().to_numpy()
    with np.errstate(invalid="ignore"):
        above50 = np.where(np.isfinite(sma50), spy["c"] > sma50, False)
    return {"vol_bucket_src": vol20, "above50": above50}
