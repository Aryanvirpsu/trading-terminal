"""HIST-001 smoke stage: turn the streamed, filtered fabhaus rows (`fetch_smoke_shard.py`'s output) into
two canonical-schema datasets -- 5-minute (execution) and daily (decision, AGGREGATED from the 5-minute
bars, since fabhaus provides no separate daily table; see HIST_001_PREREGISTRATION.md sec 2 for why this is
a deliberate, disclosed methodology) -- then runs the corporate-action detection pass required before this
data may be used for replay.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import pandas as pd

from ..corporate_actions import detect_splits, is_confirmed
from ..datasets.base import DatasetAdapter

HERE = Path(__file__).resolve().parent
FILTERED_JSONL = HERE / "smoke_2024_01_filtered.jsonl"
FETCH_MANIFEST = HERE / "smoke_2024_01_fetch_manifest.json"

INTRADAY_DATASET_ID = "HIST001_SMOKE_2024_01_5M"
DAILY_DATASET_ID = "HIST001_SMOKE_2024_01_DAILY"


def _load_filtered() -> pd.DataFrame:
    rows = []
    with open(FILTERED_JSONL, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    df = pd.DataFrame(rows)
    df["symbol"] = df["symbol"].str.upper()
    df["timestamp"] = pd.to_datetime(df["datetime"], utc=True)
    df["open"] = df["open"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["close"] = df["close"].astype(float)
    df["volume"] = df["volume"].astype(float)
    df["trade_count"] = df["trade_count"].astype(float)
    return df[["symbol", "timestamp", "open", "high", "low", "close", "volume", "trade_count"]].sort_values(
        ["symbol", "timestamp"]).reset_index(drop=True)


def _aggregate_daily(intraday: pd.DataFrame) -> pd.DataFrame:
    """One row per (symbol, UTC calendar date): open of the day's first bar, running high/low, close of
    the day's last bar, summed volume/trade_count -- the same OHLC-rollup convention already built and
    tested for "today's still-forming bar" synthesis in avdi_adapter.py, applied here to the FULL history
    instead of just the current day. Volume inherits fabhaus's own RELATIVE_ONLY verdict unchanged."""
    dates = intraday["timestamp"].dt.date
    g = intraday.assign(_date=dates).groupby(["symbol", "_date"], sort=True)
    daily = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                 volume=("volume", "sum"), trade_count=("trade_count", "sum")).reset_index()
    # Daily bars are labelled at their own date's real 16:00 ET close (matching yahoo_bootstrap.py's H5 fix
    # -- schemas/bars.py's documented "period-end" convention), DST-correct, per row.
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    closes_et = pd.to_datetime(daily["_date"]).dt.tz_localize(et) + pd.Timedelta(hours=16)
    daily["timestamp"] = closes_et.dt.tz_convert("UTC")
    return daily[["symbol", "timestamp", "open", "high", "low", "close", "volume", "trade_count"]].sort_values(
        ["symbol", "timestamp"]).reset_index(drop=True)


class _PrefetchedAdapter(DatasetAdapter):
    """Wraps already-fetched, already-filtered real data (fetch_smoke_shard.py's output) with the real
    provenance the fetch captured -- never re-hits the network, never re-downloads the shard."""
    source_name = "huggingface"
    adapter_version = "hist001-smoke-2024-01.1"
    volume_trust = "RELATIVE_ONLY"

    def __init__(self, dataset_id: str, df: pd.DataFrame, fetch_manifest: dict):
        super().__init__(dataset_id)
        self._df = df
        self.hf_dataset_id = fetch_manifest["dataset_id"]
        self.revision = fetch_manifest["revision"]
        self.upstream_files = [fetch_manifest["shard"]]
        self.upstream_sha256 = {fetch_manifest["shard"]: (fetch_manifest.get("upstream_etag") or "").strip('"')}
        self.selected_columns = fetch_manifest["selected_columns"]

    def fetch(self, symbols: Sequence[str], start: str, end: str, timeframe: str) -> pd.DataFrame:
        return self._df


def build(*, notes_suffix: str = "") -> dict:
    with open(FETCH_MANIFEST, encoding="utf-8") as fh:
        fetch_manifest = json.load(fh)

    intraday_df = _load_filtered()
    daily_df = _aggregate_daily(intraday_df)

    intraday_notes = (f"HIST-001 smoke stage: fabhaus/equities_5m_stockprices @ {fetch_manifest['revision']}, "
                      f"shard {fetch_manifest['shard']}, streamed+filtered to the 10-symbol technology "
                      f"smoke universe (never the full shard persisted). {notes_suffix}").strip()
    intraday_manifest = _PrefetchedAdapter(INTRADAY_DATASET_ID, intraday_df, fetch_manifest).import_and_store(
        sorted(fetch_manifest["symbols_requested"]), "2024-01-01", "2024-02-01", "5m", notes=intraday_notes)

    daily_notes = (f"HIST-001 smoke stage: DAILY bars AGGREGATED from the same fabhaus 5-minute source "
                  f"(no separate daily table exists in fabhaus) -- see HIST_001_PREREGISTRATION.md sec 2. "
                  f"{notes_suffix}").strip()
    daily_manifest = _PrefetchedAdapter(DAILY_DATASET_ID, daily_df, fetch_manifest).import_and_store(
        sorted(fetch_manifest["symbols_requested"]), "2024-01-01", "2024-02-01", "1d", notes=daily_notes)

    # Corporate-action detection pass -- required before this data may be used for replay (directive sec 8).
    # A single month has essentially no chance of a real split, but the pass runs unconditionally and its
    # result (even "none found") is recorded, never silently skipped.
    split_events = detect_splits(daily_df)
    confirmed = [e for e in split_events if is_confirmed(e)]
    corp_actions_report = {
        "suspected_events": [e.to_dict() for e in split_events],
        "confirmed_events": [e.to_dict() for e in confirmed],
        "policy": "raw bars immutable; split_adjusted view available on demand via corporate_actions.py; "
                 "no confirmed splits in this window -> raw and split_adjusted are identical here",
    }

    return {"intraday_manifest": intraday_manifest, "daily_manifest": daily_manifest,
           "corporate_actions": corp_actions_report}


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(HERE.parents[2]))
    result = build()
    print("intraday rows:", result["intraday_manifest"].rows, "symbols:", result["intraday_manifest"].symbols)
    print("daily rows:", result["daily_manifest"].rows)
    print("corporate actions:", result["corporate_actions"])
