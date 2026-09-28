"""H5.5 — historical macro replay (the `_fam_macro` family), the cheapest lever identified from H5's
`data_quality` ceiling finding.

**Correction to the original H5.5 framing**: `lab/data_quality.py`'s `"fundamentals"` category (weight 0.35,
required field `market_cap`) is NEVER populated by `lab/decision_engine.py` at all -- not historically, not
in live production either. There is no `_fam_fundamentals` function; `evaluate()`'s own `coverages` dict
never includes a `"fundamentals"` key, so `assess()`'s fallback silently defaults it to zero coverage for
every decision the live forward Champion has ever made too. Wiring it would mean INVENTING a new production
data path, not replaying an existing one -- out of scope for Historical Lab, which exists to replay how
AVDI already decides, never to add a decision path it doesn't have. Recorded here so this isn't
rediscovered the hard way later.

What the user's "fundamentals/filings" and "macro" instincts actually map onto, precisely, in the real
code: `_fam_filings` (SEC EDGAR, `lab/edgar.py:dilution_penalty()`, data_quality weight 0.4 base / 0.2
effective under the momentum profile) and `_fam_macro` (FRED, `lab/fred.py:macro_signal()`, weight 0.4 base
/ 0.2 effective) -- BOTH already real, live-wired `_fam_*` families in `FAMILIES_WITHOUT_HISTORICAL_REPLAY`,
simply stubbed for historical replay today, exactly as H3 disclosed.

**Quantified**: under `PRICE_TREND_ONLY_V1`, `data_quality.overall = 0.536` (effective weights: price 1.0,
candles 1.0, sector 0.16 of 0.2, everything else 0 of {fundamentals 0.35 (dead category, see above), news
0.3, analyst 0.25, filings 0.2, macro 0.2, options 0.3} against a fixed denominator of 3.8). Replaying
EITHER filings OR macro alone (full coverage, weight 0.2 each) raises `overall` to `(2.035+0.2)/3.8 = 0.588`,
comfortably clearing the unchanged 0.55 floor -- see `tests/historical/test_h55_macro.py` for the exact
arithmetic, checked directly against `lab/data_quality.py`.

**Why macro first, not filings, despite equal weight**: `_fam_macro` needs exactly ONE shared time series
per date (yield curve + VIX), read once and reused for every symbol; `_fam_filings` needs a real, dated,
per-symbol SEC filing history (CIK resolution + filing-availability timestamps) to avoid inventing
"knowable" information. Macro is the cheaper implementation for the same structural gain -- matching the
user's own "historical-data reliability versus implementation cost" ranking criterion, just landing macro
ahead of filings on the "cost" half of it.

**A genuine, disclosed blocker**: `lab/fred.py` requires `FRED_API_KEY` (St. Louis Fed's free, public API);
this environment has none configured. This module is built and tested against synthetic/fixture data so the
point-in-time logic is proven correct now; `fetch_fred_history()` is the one function that needs a real key
to actually pull real historical observations, and is not exercised by anything in the default test run.

**Point-in-time correctness**: DGS10 (10y Treasury), DGS2 (2y Treasury), VIXCLS (VIX close) and FEDFUNDS
(effective fed funds rate) are all daily MARKET-OBSERVATION series with a short (~1 business day)
publication lag and are NEVER revised after publication (unlike GDP/CPI, which get restated) -- so, unlike
the user's own more general ALFRED-vintage concern, no vintage/revision API is needed for these specific
four series: "the value known as of date T" is simply the latest observation dated `<= T - lag`, using
FRED's ordinary (not real-time-vintage) `/observations` endpoint. This is stated as a property of THESE
series, not a general claim about FRED data.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from .guards import assert_not_production_path, guard_all, historical_data_root

SERIES = {"DGS10": "10y", "DGS2": "2y", "VIXCLS": "vix", "FEDFUNDS": "fedfunds"}
PUBLICATION_LAG_DAYS = 1          # conservative: never assume same-day availability


@dataclasses.dataclass(frozen=True)
class MacroObservation:
    date: str               # ISO date this value is DATED (not when it became knowable)
    values: Dict[str, Optional[float]]     # e.g. {"10y": 4.21, "2y": 4.05, "vix": 18.3, "fedfunds": 5.25}


class MacroHistory:
    """A small, date-sorted table of daily macro observations, with a lookahead-safe `.as_of()` lookup
    (returns the latest observation whose DATE, plus the publication lag, is not after the requested
    instant) -- the exact same "never serve the future" discipline `HistoricalMarketProvider` enforces for
    price bars, applied here to macro data instead of re-implemented ad hoc."""

    def __init__(self, observations: List[MacroObservation]):
        self._obs = sorted(observations, key=lambda o: o.date)

    def as_of(self, as_of_date: dt.date) -> Optional[MacroObservation]:
        cutoff = as_of_date - dt.timedelta(days=PUBLICATION_LAG_DAYS)
        candidates = [o for o in self._obs if dt.date.fromisoformat(o.date) <= cutoff]
        return candidates[-1] if candidates else None

    def __len__(self) -> int:
        return len(self._obs)


def historical_macro_signal(history: MacroHistory, as_of_date: dt.date, direction: str = "LONG") -> Dict[str, Any]:
    """Reimplements `lab/fred.py:macro_signal()`'s own arithmetic (yield-curve tilt + VIX penalty),
    against a point-in-time `MacroHistory` lookup instead of a live, cached FRED call. Returns the SAME
    shape `_fam_macro()` does (`dir`/`conf`/`detail`), so it can be patched straight in."""
    obs = history.as_of(as_of_date)
    if obs is None:
        return {"family": "macro-rates", "dir": 0.0, "conf": 0.0, "detail": "no macro data as of this date"}
    v = obs.values
    ten, two, vix = v.get("10y"), v.get("2y"), v.get("vix")
    spread = (ten - two) if (ten is not None and two is not None) else None
    tilt = 0.0
    if spread is not None:
        tilt = max(-1.0, min(1.0, spread / 1.0))
    if vix is not None and vix > 25:
        tilt -= 0.3
    tilt = max(-1.0, min(1.0, tilt))
    d = tilt * (1 if direction == "LONG" else -1)
    read = "risk-off" if tilt < -0.2 else ("risk-on" if tilt > 0.2 else "neutral")
    return {"family": "macro-rates", "dir": round(d, 2), "conf": 0.5,
           "detail": f"as of {obs.date}: curve {round(spread, 2) if spread is not None else None} "
                     f"VIX {vix} -> {read}"}


def fetch_fred_history(series_id: str, start: str, end: str, *, api_key: str) -> List[Dict[str, Any]]:
    """Real fetch, for when FRED_API_KEY is available. Returns raw {date, value} rows for ONE series,
    ordinary (not real-time-vintage) observations -- correct for the un-revised daily series this module
    is built for (see module docstring); a series that DOES get revised would need ALFRED's
    `realtime_start`/`realtime_end` parameters instead, which this function deliberately does not add
    speculatively."""
    if not api_key:
        raise ValueError("fetch_fred_history requires a real FRED_API_KEY -- none was given")
    url = (f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}"
          f"&api_key={api_key}&file_type=json&observation_start={start}&observation_end={end}")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "avdi-historical-lab"}),
                                timeout=30) as resp:
        data = json.load(resp)
    out = []
    for o in data.get("observations", []):
        if o.get("value") in (".", None):
            continue
        out.append({"date": o["date"], "value": float(o["value"])})
    return out


def build_macro_history(*, start: str, end: str, api_key: str) -> MacroHistory:
    """Fetches all four SERIES for [start, end] and assembles one date-aligned MacroHistory. Real network
    calls; not exercised by the default test suite (requires a real FRED_API_KEY)."""
    per_series: Dict[str, Dict[str, float]] = {}
    for series_id, short_name in SERIES.items():
        rows = fetch_fred_history(series_id, start, end, api_key=api_key)
        per_series[short_name] = {r["date"]: r["value"] for r in rows}
    all_dates = sorted(set().union(*(d.keys() for d in per_series.values())) if per_series else set())
    observations = [MacroObservation(date=d, values={name: per_series[name].get(d) for name in SERIES.values()})
                    for d in all_dates]
    return MacroHistory(observations)


def save_macro_history(history: MacroHistory, name: str) -> Path:
    """Persists a MacroHistory as small JSON under the guarded historical data root, so a real fetch (once
    a key is available) only needs to happen once per date range."""
    guard_all()
    root = historical_data_root() / "macro"
    assert_not_production_path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump([dataclasses.asdict(o) for o in history._obs], fh, indent=1)
    return path


def load_macro_history(name: str) -> MacroHistory:
    root = historical_data_root() / "macro"
    path = root / f"{name}.json"
    assert_not_production_path(path)
    with open(path, encoding="utf-8") as fh:
        rows = json.load(fh)
    return MacroHistory([MacroObservation(date=r["date"], values=r["values"]) for r in rows])
