"""H1 — bootstrap-only adapter used ONLY to produce the tiny acceptance sample while no suitable free
Hugging Face intraday-equities dataset exists (see huggingface_equities.py's module docstring). Clearly
labelled `source="yahoo-bootstrap"` in the manifest so it is never confused with a real HF import. Not
intended for large-scale historical research — Yahoo's public 5-minute history is short and rate-limited;
swap in a real HF/vendor dataset for anything beyond a handful of days.
"""
from __future__ import annotations

from typing import Sequence

import pandas as pd

from .base import DatasetAdapter


class YahooBootstrapAdapter(DatasetAdapter):
    source_name = "yahoo-bootstrap"
    revision = "n/a"

    def fetch(self, symbols: Sequence[str], start: str, end: str, timeframe: str) -> pd.DataFrame:
        import yfinance as yf

        interval = {"5m": "5m", "1d": "1d"}.get(timeframe, timeframe)
        raw = yf.download(list(symbols), start=start, end=end, interval=interval, auto_adjust=False,
                          group_by="ticker", progress=False)
        rows = []
        for sym in symbols:
            try:
                df = raw[sym].dropna(how="all")
            except KeyError:
                continue
            df = df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close",
                                    "Volume": "volume"})
            df = df.dropna(subset=["open", "high", "low", "close"])
            df["symbol"] = sym.upper()
            df["timestamp"] = pd.to_datetime(df.index, utc=True)
            rows.append(df[["symbol", "timestamp", "open", "high", "low", "close", "volume"]])
        if not rows:
            return pd.DataFrame(columns=["symbol", "timestamp", "open", "high", "low", "close", "volume"])
        out = pd.concat(rows, ignore_index=True)
        out[["open", "high", "low", "close", "volume"]] = out[["open", "high", "low", "close", "volume"]].astype(float)
        return out.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
