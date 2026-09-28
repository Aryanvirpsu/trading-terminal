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
    # Yahoo reports genuine consolidated-tape volume for standard, liquid US equities/ETFs -- the same
    # property the fabhaus audit itself relied on when using Yahoo as the ground truth for its own
    # volume-coverage comparison (FABHAUS_AUDIT_REPORT.md sec 6). Not a universal claim about every ticker
    # Yahoo covers; a caller with reason to doubt it for a specific symbol should override per-instance.
    volume_trust = "ABSOLUTE"

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
            # yfinance labels an intraday bar by its START (e.g. a "5m" bar covering 09:30-09:35 is
            # indexed 09:30); the canonical bar schema is documented as bar CLOSE/period-end (schemas/
            # bars.py). Left unshifted, a clock sitting at 09:35 would see the 09:30-labelled bar as
            # "already closed" when only its first instant has actually elapsed -- a real, if small
            # (one-bar-width), lookahead risk. Shift intraday bars by one bar width so the label matches
            # when the bar's information was actually complete.
            if interval == "5m":
                df["timestamp"] = pd.to_datetime(df.index, utc=True) + pd.Timedelta(minutes=5)
            elif interval == "1d":
                # H5 finding: yfinance labels a daily bar with midnight UTC of its OWN date -- if left as
                # midnight, a clock sitting intraday on THAT SAME DATE (e.g. 12:05 ET) would see the bar as
                # already "visible" (timestamp <= clock.now), even though the day's own high/low/close
                # cannot actually be known until the session CLOSES that afternoon -- a real, same-day
                # lookahead leak the H2 guard is specifically meant to prevent. Daily bars are relabelled
                # to their own date's real 16:00 ET session close, converted to UTC per-row (DST-correct:
                # 20:00 UTC in EDT, 21:00 UTC in EST) rather than a fixed offset.
                from zoneinfo import ZoneInfo
                et = ZoneInfo("America/New_York")
                idx = pd.to_datetime(df.index)
                close_naive = idx.tz_localize(None) if idx.tz is not None else idx
                closes_et = pd.DatetimeIndex(close_naive.date).tz_localize(et) + pd.Timedelta(hours=16)
                df["timestamp"] = closes_et.tz_convert("UTC")
            else:
                df["timestamp"] = pd.to_datetime(df.index, utc=True)
            rows.append(df[["symbol", "timestamp", "open", "high", "low", "close", "volume"]])
        if not rows:
            return pd.DataFrame(columns=["symbol", "timestamp", "open", "high", "low", "close", "volume"])
        out = pd.concat(rows, ignore_index=True)
        out[["open", "high", "low", "close", "volume"]] = out[["open", "high", "low", "close", "volume"]].astype(float)
        return out.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
