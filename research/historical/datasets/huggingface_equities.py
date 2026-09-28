"""H1 — generic Hugging Face equities adapter.

As of this writing there is no well-known, freely-licensed Hugging Face dataset providing intraday
(5-minute) US-equity OHLCV bars for arbitrary tickers that has ALSO passed this project's dataset-audit
gate (see `audits/FABHAUS_AUDIT_REPORT.md`: the primary candidate, `fabhaus/equities_5m_stockprices`, is
real and schema-complete, but its timestamps are mislabelled — see `source_tz` below). Rather than
hardcode a dataset id whose exact columns/quirks might change, this class is CONFIG-DRIVEN: point it at a
real `datasets.load_dataset` id (pinned to a commit revision, never a branch) plus a column-name mapping,
and it converts that source into the canonical bar schema through the exact same validated path as every
other adapter.

Today's tiny H1/H3 acceptance samples instead use `datasets.yahoo_bootstrap.YahooBootstrapAdapter` — same
adapter interface, clearly source-labelled in its own manifest (`source="yahoo-bootstrap"`), so nothing
downstream can mistake it for a real Hugging Face dataset.
"""
from __future__ import annotations

from typing import Dict, Optional, Sequence

import pandas as pd

from .base import DatasetAdapter

DEFAULT_COLUMN_MAP = {
    "symbol": "symbol", "timestamp": "timestamp", "open": "open", "high": "high", "low": "low",
    "close": "close", "volume": "volume",
}


class HuggingFaceEquitiesAdapter(DatasetAdapter):
    """Generic adapter: `datasets.load_dataset(hf_dataset_id, revision=revision)` -> rename columns via
    `column_map` -> filter to `symbols`/`start`/`end` -> canonical schema (UTC timestamps, upper symbols).
    `symbol_column`/`timestamp_column` name the SOURCE columns used for filtering before the rename.
    """

    source_name = "huggingface"
    adapter_version = "2026-09-28.1"

    def __init__(self, dataset_id: str, hf_dataset_id: str, *, revision: str = "main",
                 split: str = "train", column_map: Dict[str, str] = None,
                 symbol_column: str = "symbol", timestamp_column: str = "timestamp",
                 source_tz: Optional[str] = None, data_files: Optional[str] = None):
        """`revision` should almost always be a pinned commit SHA, never a branch name — an experiment must
        never point merely to "latest" (see the dataset-audit gate in README.md). `source_tz`: pass this
        when the source's own timestamp column is NOT actually UTC despite how it is labelled/documented
        (this is exactly what the audit found for `fabhaus/equities_5m_stockprices`: its `datetime` field
        carries America/New_York wall-clock time with a UTC "Z" suffix mistakenly appended). When set, the
        timestamp is localized to the TRUE zone and THEN converted to UTC, instead of being naively parsed
        as if it were already UTC. `data_files` restricts the HF load to one file/glob (e.g. a single
        monthly shard) instead of the whole dataset.
        """
        super().__init__(dataset_id)
        self.hf_dataset_id = hf_dataset_id
        self.revision = revision
        self.split = split
        self.column_map = column_map or DEFAULT_COLUMN_MAP
        self.symbol_column = symbol_column
        self.timestamp_column = timestamp_column
        self.source_tz = source_tz
        self.data_files = data_files
        self.selected_columns = sorted(set(self.column_map))
        self.upstream_files = [data_files] if isinstance(data_files, str) else list(data_files or [])

    def fetch(self, symbols: Sequence[str], start: str, end: str, timeframe: str) -> pd.DataFrame:
        import datasets as hf_datasets    # imported lazily: not a dependency of the rest of Historical Lab

        kw = {"revision": self.revision, "split": self.split}
        if self.data_files:
            kw["data_files"] = self.data_files
        ds = hf_datasets.load_dataset(self.hf_dataset_id, **kw)
        df = ds.to_pandas()
        wanted = {v: k for k, v in self.column_map.items()}   # source_col -> canonical_col
        df = df.rename(columns=wanted)
        symset = {s.upper() for s in symbols}
        df = df[df["symbol"].astype(str).str.upper().isin(symset)].copy()
        df["symbol"] = df["symbol"].astype(str).str.upper()
        if self.source_tz:
            naive = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
            df["timestamp"] = naive.dt.tz_localize(self.source_tz, ambiguous="infer").dt.tz_convert("UTC")
        else:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        start_ts, end_ts = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
        df = df[(df["timestamp"] >= start_ts) & (df["timestamp"] <= end_ts)]
        keep = [c for c in ("symbol", "timestamp", "open", "high", "low", "close", "volume",
                            "vwap", "trade_count", "bid", "ask") if c in df.columns]
        return df[keep].reset_index(drop=True)
