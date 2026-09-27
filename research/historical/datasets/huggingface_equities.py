"""H1 — generic Hugging Face equities adapter.

As of this writing there is no well-known, freely-licensed Hugging Face dataset providing intraday
(5-minute) US-equity OHLCV bars for arbitrary tickers (AAPL/MSFT/META/NVDA/DELL) — searched the HF
datasets API directly (queries: "ohlcv", "us equities minute bars", "nasdaq intraday", "stock market
ohlc", "yahoo finance stock", "stooq", "polygon.io", among others); nothing matching was found. Rather
than hardcode a dataset id that may not exist or may not carry the columns this adapter assumes, this
class is CONFIG-DRIVEN: point it at a real `datasets.load_dataset` id plus a column-name mapping once a
suitable one is chosen (or once your own equities are uploaded to the Hub), and it converts that source
into the canonical bar schema through the exact same validated path as every other adapter.

Today's tiny H1 acceptance sample instead uses `datasets.yahoo_bootstrap.YahooBootstrapAdapter` — same
adapter interface, clearly source-labelled in its own manifest (`source="yahoo-bootstrap"`), so nothing
downstream can mistake it for a real Hugging Face dataset.
"""
from __future__ import annotations

from typing import Dict, Sequence

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

    def __init__(self, dataset_id: str, hf_dataset_id: str, *, revision: str = "main",
                 split: str = "train", column_map: Dict[str, str] = None,
                 symbol_column: str = "symbol", timestamp_column: str = "timestamp"):
        super().__init__(dataset_id)
        self.hf_dataset_id = hf_dataset_id
        self.revision = revision
        self.split = split
        self.column_map = column_map or DEFAULT_COLUMN_MAP
        self.symbol_column = symbol_column
        self.timestamp_column = timestamp_column

    def fetch(self, symbols: Sequence[str], start: str, end: str, timeframe: str) -> pd.DataFrame:
        import datasets as hf_datasets    # imported lazily: not a dependency of the rest of Historical Lab

        ds = hf_datasets.load_dataset(self.hf_dataset_id, revision=self.revision, split=self.split)
        df = ds.to_pandas()
        wanted = {v: k for k, v in self.column_map.items()}   # source_col -> canonical_col
        df = df.rename(columns=wanted)
        symset = {s.upper() for s in symbols}
        df = df[df["symbol"].astype(str).str.upper().isin(symset)].copy()
        df["symbol"] = df["symbol"].astype(str).str.upper()
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        start_ts, end_ts = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
        df = df[(df["timestamp"] >= start_ts) & (df["timestamp"] <= end_ts)]
        keep = [c for c in ("symbol", "timestamp", "open", "high", "low", "close", "volume",
                            "vwap", "trade_count", "bid", "ask") if c in df.columns]
        return df[keep].reset_index(drop=True)
