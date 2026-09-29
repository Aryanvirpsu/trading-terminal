"""HIST-001 Medium stage: combine the six independently-fetched, independently-filtered monthly shards
(`fetch_medium_shards.py`'s output) into two canonical-schema datasets spanning 2024-01-01..2024-06-30 --
5-minute (execution) and daily (decision, aggregated from the 5-minute bars, same disclosed methodology as
Smoke) -- then run the corporate-action detection pass.

Directive sec 8: the six monthly batches are combined by concatenating each month's already-parsed
DataFrame and then sorting the WHOLE result by (symbol, timestamp) -- the combination is a pure sort, not an
accumulation that depends on which order the months were read in, so the result is provably invariant to
batch order (see `tests/historical/test_hist001_medium_batching.py`, which combines the same six months in
several different orders and asserts byte-identical output).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Sequence

import pandas as pd

from ..corporate_actions import detect_splits, is_confirmed
from ..datasets.base import DatasetAdapter

HERE = Path(__file__).resolve().parent
MONTHS = ["2024-01", "2024-02", "2024-03", "2024-04", "2024-05", "2024-06"]

INTRADAY_DATASET_ID = "HIST001_MEDIUM_2024_H1_5M"
DAILY_DATASET_ID = "HIST001_MEDIUM_2024_H1_DAILY"


def _load_one_month(month: str, data_dir: Path = HERE) -> pd.DataFrame:
    path = data_dir / f"medium_{month}_filtered.jsonl"
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    if not rows:
        return pd.DataFrame(columns=["symbol", "timestamp", "open", "high", "low", "close", "volume",
                                     "trade_count"])
    df = pd.DataFrame(rows)
    df["symbol"] = df["symbol"].str.upper()
    df["timestamp"] = pd.to_datetime(df["datetime"], utc=True)
    for c in ("open", "high", "low", "close", "volume", "trade_count"):
        df[c] = df[c].astype(float)
    return df[["symbol", "timestamp", "open", "high", "low", "close", "volume", "trade_count"]]


def load_filtered(months: Sequence[str] = MONTHS, data_dir: Path = HERE) -> pd.DataFrame:
    """Loads and combines every requested month. Order of `months` does NOT affect the result: each month's
    rows are independent (no cross-month accumulation happens here), and the final frame is always sorted by
    (symbol, timestamp) regardless of concatenation order."""
    frames = [_load_one_month(m, data_dir) for m in months]
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return combined.sort_values(["symbol", "timestamp"]).reset_index(drop=True)


def _aggregate_daily(intraday: pd.DataFrame) -> pd.DataFrame:
    """Same OHLC-rollup convention as build_smoke_dataset.py's `_aggregate_daily`, applied across the full
    six-month combined frame."""
    dates = intraday["timestamp"].dt.date
    g = intraday.assign(_date=dates).groupby(["symbol", "_date"], sort=True)
    daily = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                 volume=("volume", "sum"), trade_count=("trade_count", "sum")).reset_index()
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    closes_et = pd.to_datetime(daily["_date"]).dt.tz_localize(et) + pd.Timedelta(hours=16)
    daily["timestamp"] = closes_et.dt.tz_convert("UTC")
    return daily[["symbol", "timestamp", "open", "high", "low", "close", "volume", "trade_count"]].sort_values(
        ["symbol", "timestamp"]).reset_index(drop=True)


def _load_fetch_manifests(months: Sequence[str] = MONTHS) -> List[dict]:
    out = []
    for m in months:
        with open(HERE / f"medium_{m}_fetch_manifest.json", encoding="utf-8") as fh:
            out.append(json.load(fh))
    return out


class _PrefetchedAdapter(DatasetAdapter):
    """Wraps the already-fetched, already-filtered, already-combined six-month frame with real provenance
    from all six monthly fetch manifests -- never re-hits the network."""
    source_name = "huggingface"
    adapter_version = "hist001-medium-2024h1.1"
    volume_trust = "RELATIVE_ONLY"

    def __init__(self, dataset_id: str, df: pd.DataFrame, fetch_manifests: List[dict]):
        super().__init__(dataset_id)
        self._df = df
        self.hf_dataset_id = fetch_manifests[0]["dataset_id"]
        self.revision = fetch_manifests[0]["revision"]
        self.upstream_files = [m["shard"] for m in fetch_manifests]
        self.upstream_sha256 = {m["shard"]: (m.get("upstream_etag") or "").strip('"') for m in fetch_manifests}
        self.selected_columns = fetch_manifests[0]["selected_columns"]

    def fetch(self, symbols: Sequence[str], start: str, end: str, timeframe: str) -> pd.DataFrame:
        return self._df


def build(*, months: Sequence[str] = MONTHS, notes_suffix: str = "") -> dict:
    fetch_manifests = _load_fetch_manifests(months)
    universe = sorted(fetch_manifests[0]["symbols_requested"])

    intraday_df = load_filtered(months)
    daily_df = _aggregate_daily(intraday_df)

    intraday_notes = (f"HIST-001 Medium stage: fabhaus/equities_5m_stockprices @ "
                      f"{fetch_manifests[0]['revision']}, shards {[m['shard'] for m in fetch_manifests]}, "
                      f"streamed+filtered to the full 90-symbol dashboard/sector_map.py universe, combined "
                      f"in a batch-order-invariant way (see build_medium_dataset.py). {notes_suffix}").strip()
    intraday_manifest = _PrefetchedAdapter(INTRADAY_DATASET_ID, intraday_df, fetch_manifests).import_and_store(
        universe, "2024-01-01", "2024-07-01", "5m", notes=intraday_notes)

    daily_notes = (f"HIST-001 Medium stage: DAILY bars AGGREGATED from the same fabhaus 5-minute source "
                  f"(no separate daily table exists in fabhaus). {notes_suffix}").strip()
    daily_manifest = _PrefetchedAdapter(DAILY_DATASET_ID, daily_df, fetch_manifests).import_and_store(
        universe, "2024-01-01", "2024-07-01", "1d", notes=daily_notes)

    split_events = detect_splits(daily_df)
    confirmed = [e for e in split_events if is_confirmed(e)]
    corp_actions_report = {
        "suspected_events": [e.to_dict() for e in split_events],
        "confirmed_events": [e.to_dict() for e in confirmed],
        "policy": "raw bars immutable; split_adjusted view available on demand via corporate_actions.py",
    }

    missing_symbols = sorted(set(universe) - set(daily_df["symbol"].unique()))

    return {"intraday_manifest": intraday_manifest, "daily_manifest": daily_manifest,
           "corporate_actions": corp_actions_report, "symbols_with_no_data": missing_symbols,
           "universe_requested": universe}


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(HERE.parents[2]))
    result = build()
    print("intraday rows:", result["intraday_manifest"].rows, "symbols:", result["intraday_manifest"].symbols)
    print("daily rows:", result["daily_manifest"].rows)
    print("corporate actions:", result["corporate_actions"])
    print("symbols with no data:", result["symbols_with_no_data"])
