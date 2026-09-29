"""H5.5 — historical macro replay (the `_fam_macro` family), rebuilt for real ALFRED vintage semantics.

================================================================================================
PART 1 — the REAL production `_fam_macro` specification, inspected from `lab/fred.py` and
`lab/decision_engine.py:_fam_macro()` directly (not summarized from memory), before writing any
replay logic, per the H5.5 directive. Every fact below is cited to the exact line/behavior in that file.
================================================================================================

**Series used** (`lab/fred.py:SERIES`): exactly four FRED daily series --
    DGS10     "10-Year Treasury Constant Maturity Rate"   -> internal name "10y"
    DGS2      "2-Year Treasury Constant Maturity Rate"    -> internal name "2y"
    VIXCLS    "CBOE Volatility Index (VIX), Close"        -> internal name "vix"
    FEDFUNDS  "Effective Federal Funds Rate"              -> internal name "fedfunds"

**Fetch** (`_latest()`): for each series, `GET /fred/series/observations?series_id=...&limit=2&sort_order=desc`
-- the two MOST RECENT observations, filtering out `"."` (FRED's missing-value marker); the first valid one
is used. This is NOT a lookback window or a moving average -- there is no smoothing, no multi-day average,
just "the single latest available print," with one extra day fetched purely as a fallback in case the very
latest calendar day is a holiday/missing.

**Transformation** (`macro_regime()`):
    yield_curve_10y_2y = DGS10 - DGS2                          (raw spread, percentage points)
    tilt = clamp(yield_curve_10y_2y / 1.0, -1.0, 1.0)          (linear: +/-100bp spread -> +/-1.0 tilt)
    if VIXCLS > 25: tilt -= 0.3                                 (flat penalty, not scaled by how far over 25)
    tilt = clamp(tilt, -1.0, 1.0)                               (reclamped after the VIX adjustment)
    read = "risk-off" if tilt < -0.2 else "risk-on" if tilt > 0.2 else "neutral"

**FEDFUNDS is fetched and reported but NEVER used in the tilt/signal arithmetic at all** -- dead weight in
the current production formula. Disclosed here rather than silently dropped or silently "fixed": historical
replay reproduces the REAL formula, bugs and all, not an improved one.

**Confidence** (`macro_signal()`): a FIXED `0.5` whenever `macro_regime()["available"]` is True (i.e. a
`FRED_API_KEY`/`FRED_KEY` is configured at all) -- NOT data-driven. Confidence does not vary with how many
of the four series actually resolved, how stale the latest print is, or anything else; only the presence of
an API key gates it between `0.5` and `0.0`.

**Direction**: `dir = round(tilt * (1 if direction == "LONG" else -1), 2)`.

**Fallback behavior** (never raises, never blocks):
    - no FRED key at all               -> `{"dir": 0.0, "conf": 0.0, "detail": "no FRED key"}`
    - a series fetch raises            -> that series' value is `None`; `spread` becomes `None` if EITHER
                                          10y or 2y is missing (tilt stays 0.0 from the curve term, VIX
                                          penalty still applies if VIX itself resolved); never a hard error
    - VIX missing                      -> no VIX penalty applied, curve term alone still stands
    - live-only cache: 6 hours -- irrelevant for a point-in-time historical lookup, which always asks for
      the value as of a specific date, never "now".

Historical replay reproduces this EXACT arithmetic (`historical_macro_signal()` below) against a
vintage-aware point-in-time lookup instead of a live, cached call -- never a simplified or "improved"
historical macro score.

================================================================================================
PART 2 — vintage/point-in-time correctness (ALFRED semantics)
================================================================================================

The question a historical replay must answer is never "what does FRED say DGS10 was on date D" (today's
current, possibly-revised value) but "what value would `_latest('DGS10')` have returned had this code
actually run on date T" -- i.e. the value as published in whichever vintage was CURRENT on T, per FRED's
own real-time period model (`realtime_start`/`realtime_end` on every observation; ALFRED is the same
underlying data accessed with `output_type=2`, which returns every historical vintage of a series, not
just the current one).

**Per-series integrity, checked before trusting any of them for point-in-time replay** (see
`tests/historical/test_h55_macro.py::test_series_integrity_notes` for the recorded verdict per series):
DGS10/DGS2/VIXCLS/FEDFUNDS are daily MARKET-OBSERVATION series (not survey- or estimate-based aggregates
like GDP/CPI) with a short (~1 business day) publication lag and are, as a matter of established practice
for the St. Louis Fed's own daily market-rate series, NOT subject to subsequent revision the way estimated
aggregates are. This module does NOT assume that from prior knowledge alone: `fetch_fred_vintages()` always
requests `output_type=2` (every vintage) and `MacroHistory` is built to handle a genuinely revised series
correctly regardless -- if a future fetch against the real API ever DOES show more than one
`realtime_start` for the same `observation_date` on one of these four series, that is data, not an
assumption, and this module's own point-in-time logic already handles it (see the revision test below). A
series that DOES turn out to need special handling this module doesn't yet have gets marked `UNAVAILABLE`
for macro replay rather than silently treated as if it were fine (`SeriesIntegrityError`).

Every stored observation carries: `series_id`, `observation_date` (what the value is ABOUT),
`realtime_start`/`realtime_end` (the vintage window during which this exact value was FRED's current
answer), `value`, `retrieval_source`, and `api_version` -- the directive's own required minimum. The API key
is NEVER part of any observation, manifest, or log line (see `fetch_fred_vintages()`: the key is passed only
as a URL query parameter to `urllib`, never returned, stored, or printed).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .guards import assert_not_production_path, guard_all, historical_data_root

SERIES = {"DGS10": "10y", "DGS2": "2y", "VIXCLS": "vix", "FEDFUNDS": "fedfunds"}
API_VERSION = "fred/series/observations?output_type=2"      # ALFRED-style: every vintage, not just current
ADAPTER_VERSION = "2026-09-28.1"

# Per-series integrity verdict (directive's "Macro-series integrity checks"). ACCEPTED means: daily,
# un-revised-in-practice market observation series, short fixed publication lag, safe for the point-in-time
# model this module implements. A series without an ACCEPTED verdict is refused by `historical_macro_signal`
# (fails closed) rather than silently treated as safe.
SERIES_INTEGRITY: Dict[str, Dict[str, str]] = {
    "DGS10": {"verdict": "ACCEPTED", "release_frequency": "daily (business days)",
             "revision_frequency": "none observed/expected (daily market close rate)",
             "publication_lag": "~1 business day", "timezone_semantics": "US business date, no intraday time"},
    "DGS2": {"verdict": "ACCEPTED", "release_frequency": "daily (business days)",
            "revision_frequency": "none observed/expected (daily market close rate)",
            "publication_lag": "~1 business day", "timezone_semantics": "US business date, no intraday time"},
    "VIXCLS": {"verdict": "ACCEPTED", "release_frequency": "daily (business days)",
              "revision_frequency": "none observed/expected (daily index close)",
              "publication_lag": "~1 business day", "timezone_semantics": "US business date, no intraday time"},
    # Corrected against a real fetch (see H55_MACRO_REPLAY.md): FEDFUNDS is MONTHLY, not daily -- an
    # initial, unverified assumption here said "daily," which a real ~2-month window's fetch disproved (1
    # row, dated the 1st of the month, not ~40). Kept ACCEPTED because it is never used in the _fam_macro
    # arithmetic at all (see Part 1) -- its release cadence cannot affect a signal that never reads it -- but
    # the frequency claim itself is now the verified one, not the original guess.
    "FEDFUNDS": {"verdict": "ACCEPTED", "release_frequency": "monthly (verified against a real fetch; "
                "irrelevant to _fam_macro's own output since this series is unused there)",
                "revision_frequency": "none observed in a real ~2-month fetch",
                "publication_lag": "observed ~1 month after the reference month", "timezone_semantics": "US calendar month, no intraday time"},
}


class SeriesIntegrityError(RuntimeError):
    """Raised when a series without an ACCEPTED integrity verdict is asked for -- fail closed rather than
    silently treat an unreviewed series as historically safe."""


class MacroCoverageError(RuntimeError):
    """Raised by `assert_macro_coverage` when a MacroHistory does not have real data covering the full
    requested range -- see that function's docstring. Distinct from SeriesIntegrityError (an unreviewed
    series) -- this is a reviewed series with an actual date-coverage gap in this specific dataset."""


@dataclasses.dataclass(frozen=True)
class VintageObservation:
    series_id: str
    observation_date: str      # ISO date this value is ABOUT
    realtime_start: str        # ISO date this exact value FIRST became FRED's current answer
    realtime_end: str          # ISO date this exact value LAST was FRED's current answer (inclusive)
    value: float
    retrieval_source: str = "FRED"
    api_version: str = API_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


class MacroHistory:
    """A vintage-aware table: possibly MULTIPLE rows per (series_id, observation_date) if that value was
    ever revised, each tagged with the real-time window it was FRED's current answer for. `latest_value_as_of`
    answers "what would `_latest(series_id)` have returned if queried on this date" -- the value from
    whichever vintage was active on that date, for the most recent observation_date already published by
    then. This is the exact point-in-time question the directive requires; a later revision is never
    visible before its own `realtime_start`."""

    def __init__(self, observations: List[VintageObservation]):
        self._by_series: Dict[str, List[VintageObservation]] = {}
        for o in observations:
            self._by_series.setdefault(o.series_id, []).append(o)

    def latest_value_as_of(self, series_id: str, as_of: dt.date) -> Optional[Tuple[str, float]]:
        """Returns (observation_date, value) for the most recent observation_date whose ACTIVE vintage on
        `as_of` has `realtime_start <= as_of <= realtime_end` -- i.e. exactly what a live call would have
        returned on that date. None if the series has no data knowable by `as_of` at all."""
        rows = self._by_series.get(series_id, [])
        as_of_s = as_of.isoformat()
        visible = [o for o in rows if o.realtime_start <= as_of_s <= o.realtime_end]
        if not visible:
            return None
        best = max(visible, key=lambda o: o.observation_date)
        return best.observation_date, best.value

    def all_observations(self) -> List[VintageObservation]:
        return [o for rows in self._by_series.values() for o in rows]

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_series.values())

    def coverage_gaps(self, start: dt.date, end: dt.date, *,
                      required_series: Tuple[str, ...] = ("DGS10", "DGS2", "VIXCLS")) -> Dict[str, List[str]]:
        """For each of `required_series` (default: the three series `_fam_macro`'s own arithmetic actually
        reads -- FEDFUNDS is excluded here since it is never used in that arithmetic, see Part 1 of this
        module's docstring), which calendar dates in [start, end] have NO value knowable by that date at
        all. A gap here means a historical run covering this range would silently fall back to
        conf=0/dir=0 for that date-series, indistinguishable from "no FRED key" -- exactly what
        `assert_macro_coverage` exists to catch before a run starts, not after."""
        gaps: Dict[str, List[str]] = {sid: [] for sid in required_series}
        d = start
        while d <= end:
            for sid in required_series:
                if self.latest_value_as_of(sid, d) is None:
                    gaps[sid].append(d.isoformat())
            d += dt.timedelta(days=1)
        return {sid: dates for sid, dates in gaps.items() if dates}


def historical_macro_signal(history: MacroHistory, as_of_date: dt.date, direction: str = "LONG") -> Dict[str, Any]:
    """Reproduces `lab/fred.py`'s EXACT arithmetic (see Part 1 above), against `MacroHistory`'s
    vintage-aware point-in-time lookup instead of a live, cached FRED call. Returns the same shape
    `_fam_macro()` does (`family`/`dir`/`conf`/`detail`)."""
    for sid in SERIES:
        if SERIES_INTEGRITY.get(sid, {}).get("verdict") != "ACCEPTED":
            raise SeriesIntegrityError(
                f"{sid} has no ACCEPTED integrity verdict -- refusing to replay _fam_macro rather than "
                f"silently treat an unreviewed series as historically safe")

    ten = history.latest_value_as_of("DGS10", as_of_date)
    two = history.latest_value_as_of("DGS2", as_of_date)
    vix = history.latest_value_as_of("VIXCLS", as_of_date)
    # FEDFUNDS is deliberately looked up too (for parity/disclosure with the real function fetching it) but
    # -- exactly like production -- never used in the arithmetic below.
    history.latest_value_as_of("FEDFUNDS", as_of_date)

    if ten is None and two is None and vix is None:
        return {"family": "macro-rates", "dir": 0.0, "conf": 0.0, "detail": "no macro data as of this date"}

    ten_v = ten[1] if ten else None
    two_v = two[1] if two else None
    vix_v = vix[1] if vix else None
    spread = (ten_v - two_v) if (ten_v is not None and two_v is not None) else None
    tilt = 0.0
    if spread is not None:
        tilt = max(-1.0, min(1.0, spread / 1.0))
    if vix_v is not None and vix_v > 25:
        tilt -= 0.3
    tilt = max(-1.0, min(1.0, tilt))
    d = tilt * (1 if direction == "LONG" else -1)
    read = "risk-off" if tilt < -0.2 else ("risk-on" if tilt > 0.2 else "neutral")
    return {"family": "macro-rates", "dir": round(d, 2), "conf": 0.5,
           "detail": f"as of {as_of_date.isoformat()}: curve {round(spread, 2) if spread is not None else None} "
                     f"VIX {vix_v} -> {read}"}


def assert_macro_coverage(macro_history: Optional[MacroHistory], start: dt.date, end: dt.date) -> None:
    """A HIST-001 (or any) run labelled `PRICE_TREND_MACRO_V1` must not start if its macro store is absent
    or incomplete over the run's own [start, end] -- the directive's own explicit requirement, so a run can
    never silently degrade to `PRICE_TREND_ONLY_V1`'s behavior while still claiming the macro tag. Raises
    `MacroCoverageError` (absent history) or reports the exact gap dates found (incomplete). A caller that
    genuinely wants `PRICE_TREND_ONLY_V1` simply never calls this -- it is not invoked implicitly."""
    if macro_history is None:
        raise MacroCoverageError(
            f"PRICE_TREND_MACRO_V1 run requires a macro_history covering {start}..{end}, but none was "
            f"given -- pass a real MacroHistory (built via build_macro_history/load_macro_history) or use "
            f"PRICE_TREND_ONLY_V1 explicitly instead of silently running without macro")
    gaps = macro_history.coverage_gaps(start, end)
    if gaps:
        total_gap_days = sum(len(v) for v in gaps.values())
        sample = {sid: dates[:3] for sid, dates in gaps.items()}
        raise MacroCoverageError(
            f"PRICE_TREND_MACRO_V1 run's macro_history has {total_gap_days} date-series gap(s) over "
            f"{start}..{end} (sample: {sample}) -- refusing to start rather than silently fall back to "
            f"conf=0/dir=0 for the missing dates while still claiming the macro tag. Fetch a wider "
            f"date range (build_macro_history) before retrying.")


# ── Real fetch (needs a real FRED_API_KEY; never exercised by the default test suite) ─────────────────────

def fetch_fred_vintages(series_id: str, start: str, end: str, *, api_key: str) -> List[Dict[str, Any]]:
    """ALFRED-style: every historical vintage of `series_id` between `start`/`end`, so a genuine revision
    (should one exist for these series) is captured rather than silently collapsed to today's current
    value. The key is used ONLY as a URL query parameter here -- never returned, logged, or stored by this
    function or any caller in this module.

    Real API shape (`output_type=2`, confirmed against the live endpoint): each observation row has a
    `"date"` (the observation_date) plus one column PER VINTAGE DATE within the query's own
    `realtime_start..realtime_end` window, named `f"{series_id}_{vintage_date}"` -- e.g.
    `{"date": "2026-09-01", "DGS10_20260902": "4.79", "DGS10_20260903": "4.79", ...}`. A vintage column's
    date is when THAT value became FRED's current answer; if the value never changes across consecutive
    vintage columns (true for DGS10/DGS2/VIXCLS/FEDFUNDS in every sample checked), they collapse into one
    run. `realtime_start` is set a few days before `start` (these series publish ~1 business day after
    their own observation date, per SERIES_INTEGRITY) so the TRUE first vintage for the earliest requested
    observation is never cut off; `realtime_end` is today (the retrieval date) -- the query's own real-time
    window, not a claim about how long any of these series stays unrevised. FRED caps the number of
    distinct vintage dates it will return per call (~2000), so this is only safe for a bounded date range,
    not decades of history in one call -- exactly the historical-replay use case this module is for."""
    if not api_key:
        raise ValueError("fetch_fred_vintages requires a real FRED_API_KEY -- none was given")
    rt_start = (dt.date.fromisoformat(start) - dt.timedelta(days=7)).isoformat()
    rt_end = dt.datetime.now(dt.timezone.utc).date().isoformat()
    url = (f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}"
          f"&api_key={api_key}&file_type=json&output_type=2"
          f"&realtime_start={rt_start}&realtime_end={rt_end}"
          f"&observation_start={start}&observation_end={end}")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "avdi-historical-lab"}),
                                timeout=30) as resp:
        data = json.load(resp)

    def _iso(vintage_raw: str) -> str:
        # column suffixes come back as raw "YYYYMMDD" (no separators) -- normalize to ISO so
        # MacroHistory's plain string comparisons (realtime_start <= as_of <= realtime_end) are correct.
        return f"{vintage_raw[0:4]}-{vintage_raw[4:6]}-{vintage_raw[6:8]}"

    prefix = f"{series_id}_"
    out: List[Dict[str, Any]] = []
    for row in data.get("observations", []):
        obs_date = row["date"]
        pairs = sorted(((_iso(k[len(prefix):]), v) for k, v in row.items() if k.startswith(prefix)),
                       key=lambda kv: kv[0])
        pairs = [(vd, v) for vd, v in pairs if v not in (".", None)]
        if not pairs:
            continue
        run_start, run_val = pairs[0]
        for i in range(1, len(pairs)):
            vd, v = pairs[i]
            if v != run_val:
                out.append({"observation_date": obs_date, "realtime_start": run_start,
                           "realtime_end": pairs[i - 1][0], "value": float(run_val)})
                run_start, run_val = vd, v
        out.append({"observation_date": obs_date, "realtime_start": run_start,
                   "realtime_end": "9999-12-31", "value": float(run_val)})
    return out


def build_macro_history(*, start: str, end: str, api_key: str) -> MacroHistory:
    """Fetches every ACCEPTED series' full vintage history for [start, end] and assembles one MacroHistory.
    Real network calls; not exercised by the default test suite (requires a real FRED_API_KEY)."""
    observations: List[VintageObservation] = []
    for series_id, verdict in SERIES_INTEGRITY.items():
        if verdict["verdict"] != "ACCEPTED":
            continue
        for row in fetch_fred_vintages(series_id, start, end, api_key=api_key):
            observations.append(VintageObservation(
                series_id=series_id, observation_date=row["observation_date"],
                realtime_start=row["realtime_start"], realtime_end=row["realtime_end"], value=row["value"]))
    return MacroHistory(observations)


# ── Local versioned store: Parquet + manifest, under the guarded historical data root ──────────────────────
# (kept under historical_data_root()/macro/ -- the ONE directory Historical Lab code may write under,
# per guards.py -- rather than the directive's illustrative `research/historical/data/macro/`, so this
# obeys the SAME single-source-of-truth guard every other Historical Lab dataset already does.)

@dataclasses.dataclass(frozen=True)
class MacroManifest:
    dataset_id: str
    source: str                          # "FRED" (ALFRED-style vintage query against the same API)
    series_ids: List[str]
    retrieval_date: str                  # UTC date this fetch was performed -- never the API key
    api_version: str
    date_range: Tuple[str, str]
    row_count: int
    vintage_semantics: str
    local_sha256: str
    adapter_version: str
    parquet_path: str
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


def _macro_root() -> Path:
    root = historical_data_root() / "macro"
    assert_not_production_path(root)
    return root


def save_macro_history(history: MacroHistory, dataset_id: str, *, date_range: Tuple[str, str],
                       notes: str = "") -> Path:
    """Persists a MacroHistory as Parquet plus its own manifest, so a real fetch (once a key is available)
    only needs to happen once per date range. The manifest records provenance exactly per the directive
    (source, series ids, retrieval date, API version, date range, row count, vintage semantics, local
    sha256, adapter version) and NEVER the API key."""
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    guard_all()
    root = _macro_root()
    root.mkdir(parents=True, exist_ok=True)
    rows = [o.to_dict() for o in history.all_observations()]
    df = pd.DataFrame(rows, columns=["series_id", "observation_date", "realtime_start", "realtime_end",
                                     "value", "retrieval_source", "api_version"])
    df = df.sort_values(["series_id", "observation_date", "realtime_start"]).reset_index(drop=True)
    parquet_rel = f"{dataset_id}.parquet"
    parquet_path = root / parquet_rel
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), parquet_path)

    h = hashlib.sha256()
    with open(parquet_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)

    manifest = MacroManifest(
        dataset_id=dataset_id, source="FRED", series_ids=sorted(SERIES.keys()),
        retrieval_date=dt.datetime.now(dt.timezone.utc).date().isoformat(), api_version=API_VERSION,
        date_range=date_range, row_count=len(df),
        vintage_semantics=("output_type=2 (every historical vintage per observation_date); "
                          "latest_value_as_of(T) selects the most recent observation_date whose active "
                          "vintage window [realtime_start, realtime_end] contains T"),
        local_sha256=h.hexdigest(), adapter_version=ADAPTER_VERSION, parquet_path=parquet_rel, notes=notes)
    manifest_path = root / f"{dataset_id}.manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest.to_dict(), fh, indent=1)
    return manifest_path


def load_macro_history(dataset_id: str) -> Tuple[MacroHistory, MacroManifest]:
    import pandas as pd

    root = _macro_root()
    manifest_path = root / f"{dataset_id}.manifest.json"
    assert_not_production_path(manifest_path)
    with open(manifest_path, encoding="utf-8") as fh:
        m = MacroManifest(**json.load(fh))
    df = pd.read_parquet(root / m.parquet_path)
    obs = [VintageObservation(**row) for row in df.to_dict("records")]
    return MacroHistory(obs), m


def verify_macro_manifest(dataset_id: str) -> Dict[str, Any]:
    root = _macro_root()
    manifest_path = root / f"{dataset_id}.manifest.json"
    with open(manifest_path, encoding="utf-8") as fh:
        m = json.load(fh)
    parquet_path = root / m["parquet_path"]
    if not parquet_path.exists():
        return {"ok": False, "problem": f"parquet file missing: {parquet_path}"}
    h = hashlib.sha256()
    with open(parquet_path, "rb") as fh2:
        for chunk in iter(lambda: fh2.read(1 << 20), b""):
            h.update(chunk)
    actual = h.hexdigest()
    ok = actual == m["local_sha256"]
    return {"ok": ok, "expected": m["local_sha256"], "actual": actual}
