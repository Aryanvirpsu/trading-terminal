"""H5 step 2: build the KNOWN_FORWARD_2026_09_25 dataset.

The primary fabhaus research corpus ends before September 2026 and must not be used here ("Do not
fabricate September 2026 HF data" -- the directive is explicit). This environment also has no SSH/backup
access to the Ubuntu production host, so the "data actually captured by Ubuntu" (shadow logger bars,
decision-time quote/bid/ask records) the directive asks to prefer is not reachable -- disclosed plainly
rather than worked around. The necessary, clearly documented supplement is real Yahoo data for the 12
symbols the forward session actually touched (`reference.REFERENCE_EVENTS`), fetched fresh for THIS exact
date, via the SAME `YahooBootstrapAdapter` H1's own acceptance sample already uses (`source="yahoo-bootstrap"`
in every manifest -- never confused with a Hugging Face import).

Two separately versioned datasets, never mixed with fabhaus or with each other's timeframe:
  * `KNOWN_FORWARD_2026_09_25_DAILY`  -- ~4 months of daily bars ending 2026-09-25, for decision-making
    (trend/RSI -- strategies._bars()'s own 55-bar floor).
  * `KNOWN_FORWARD_2026_09_25_5M`     -- 5-minute bars for 2026-09-25 itself, for execution (quote_for/
    _live_mark_src) -- H5 blocker #5's decision-vs-executable-price granularity split.

`volume_trust=ABSOLUTE` for both: this is genuine Yahoo consolidated-tape volume for large, liquid US
equities (the SAME source the fabhaus audit itself used as ground truth), not fabhaus's own RELATIVE_ONLY
figures -- a real property of this specific supplement, not a general claim about Yahoo everywhere.
"""
from __future__ import annotations

from typing import List

from ..datasets.yahoo_bootstrap import YahooBootstrapAdapter
from ..guards import guard_all
from ..manifest import DatasetManifest, save_manifest
from .reference import SESSION_DATE, all_symbols

DAILY_DATASET_ID = "KNOWN_FORWARD_2026_09_25_DAILY"
INTRADAY_DATASET_ID = "KNOWN_FORWARD_2026_09_25_5M"

DAILY_START = "2026-06-01"          # ~4 months of daily history, comfortably clears the 55-bar trend floor
DAILY_END = "2026-09-26"            # yfinance `end` is exclusive -> includes through 2026-09-25
INTRADAY_START = "2026-09-25"
INTRADAY_END = "2026-09-26"


def symbols() -> List[str]:
    return all_symbols()


def build(*, notes_suffix: str = "") -> dict:
    """Fetch and store both datasets. Returns {"daily": DatasetManifest, "intraday": DatasetManifest}.
    Idempotent: re-running re-fetches and re-validates (Yahoo's own history for a date this recent can
    still change slightly as late prints settle -- the manifest's content hash makes any such drift
    visible rather than silently overwritten)."""
    guard_all()
    syms = symbols()

    daily_notes = (f"H5 KNOWN_FORWARD_2026_09_25 (source=yahoo-bootstrap): daily bars for decision-making "
                  f"(trend/RSI), {DAILY_START}..{DAILY_END}. Real Yahoo data, fetched fresh for this exact "
                  f"session; NOT the fabhaus research corpus, NOT raw Ubuntu shadow-log data (unreachable "
                  f"from this environment). {notes_suffix}").strip()
    daily = YahooBootstrapAdapter(DAILY_DATASET_ID).import_and_store(
        syms, DAILY_START, DAILY_END, "1d", notes=daily_notes)

    intraday_notes = (f"H5 KNOWN_FORWARD_2026_09_25 (source=yahoo-bootstrap): 5-minute bars for the session "
                      f"itself ({SESSION_DATE}), for execution/quote data -- see H5 blocker #5 (decision vs. "
                      f"executable-price granularity). {notes_suffix}").strip()
    intraday = YahooBootstrapAdapter(INTRADAY_DATASET_ID).import_and_store(
        syms, INTRADAY_START, INTRADAY_END, "5m", notes=intraday_notes)

    return {"daily": daily, "intraday": intraday}


if __name__ == "__main__":
    result = build()
    for name, m in result.items():
        print(f"{name}: {m.rows} rows, symbols={m.symbols}, sha256={m.sha256[:12]}...")
