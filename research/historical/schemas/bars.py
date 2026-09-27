"""The canonical historical bar schema (H1). Every dataset adapter must convert its source into exactly
this shape before it is written to Parquet — the rest of Historical Lab (clock, provider, replay) reads
only this schema and never a source-specific one.

Columns:
    symbol      str       upper-cased ticker, as AVDI's own `canonical()` normalizer would produce
    timestamp   datetime  tz-aware, UTC (bar CLOSE/period-end convention, matching lab/paper's own bars)
    open, high, low, close   float, > 0
    volume      int/float, >= 0
    vwap        float, optional
    trade_count int, optional
    bid, ask    float, optional (only when the source actually has quotes, e.g. NBBO-derived bars)
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import List

import pandas as pd
import pandera.pandas as pa
from pandera.pandas import Column, Check, DataFrameSchema

REQUIRED_COLUMNS = ("symbol", "timestamp", "open", "high", "low", "close", "volume")
OPTIONAL_COLUMNS = ("vwap", "trade_count", "bid", "ask")

BAR_SCHEMA = DataFrameSchema(
    {
        "symbol": Column(str, Check.str_length(min_value=1, max_value=10), nullable=False),
        "timestamp": Column("datetime64[ns, UTC]", nullable=False),
        "open": Column(float, Check.gt(0), nullable=False),
        "high": Column(float, Check.gt(0), nullable=False),
        "low": Column(float, Check.gt(0), nullable=False),
        "close": Column(float, Check.gt(0), nullable=False),
        "volume": Column(float, Check.ge(0), nullable=False),
        "vwap": Column(float, Check.gt(0), nullable=True, required=False),
        "trade_count": Column(float, Check.ge(0), nullable=True, required=False),
        "bid": Column(float, Check.gt(0), nullable=True, required=False),
        "ask": Column(float, Check.gt(0), nullable=True, required=False),
    },
    strict=False,        # extra source-specific columns (e.g. adjclose) may ride along, unvalidated
    coerce=True,
)


@dataclass
class ValidationReport:
    rows: int
    symbols: List[str]
    ok: bool
    failures: dict          # check name -> count of offending rows (0 = passed)

    def to_dict(self) -> dict:
        return {"rows": self.rows, "symbols": self.symbols, "ok": self.ok, "failures": self.failures}


class BarValidationError(ValueError):
    def __init__(self, report: ValidationReport):
        self.report = report
        super().__init__(f"bar validation failed: {report.failures}")


def validate_bars(df: pd.DataFrame, *, raise_on_failure: bool = True) -> ValidationReport:
    """Pandera schema checks PLUS the cross-row/cross-column checks pandera can't express as a single
    column Check: high >= open/close/low, low <= open/close, increasing timestamps per symbol, and no
    duplicate (symbol, timestamp) rows. Returns a report either way; raises BarValidationError only if
    `raise_on_failure` and something actually failed."""
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        report = ValidationReport(rows=len(df), symbols=[], ok=False, failures={"missing_columns": missing})
        if raise_on_failure:
            raise BarValidationError(report)
        return report

    failures: dict = {}
    try:
        BAR_SCHEMA.validate(df, lazy=True)
    except pa.errors.SchemaErrors as e:
        for name, grp in e.failure_cases.groupby("check"):
            failures[str(name)] = int(len(grp))

    bad_hl = df["high"] < df["low"]
    bad_ho = df["high"] < df["open"]
    bad_hc = df["high"] < df["close"]
    bad_lo = df["low"] > df["open"]
    bad_lc = df["low"] > df["close"]
    for name, mask in (("high_ge_low", bad_hl), ("high_ge_open", bad_ho), ("high_ge_close", bad_hc),
                       ("low_le_open", bad_lo), ("low_le_close", bad_lc)):
        n = int(mask.sum())
        if n:
            failures[name] = n

    dupes = df.duplicated(subset=["symbol", "timestamp"]).sum()
    if dupes:
        failures["duplicate_symbol_timestamp"] = int(dupes)

    non_monotonic = 0
    for _sym, grp in df.groupby("symbol", sort=False):
        ts = grp["timestamp"].reset_index(drop=True)
        if not ts.is_monotonic_increasing:
            non_monotonic += 1
    if non_monotonic:
        failures["timestamps_not_increasing_per_symbol"] = non_monotonic

    report = ValidationReport(rows=len(df), symbols=sorted(df["symbol"].unique().tolist()),
                              ok=not failures, failures=failures)
    if raise_on_failure and not report.ok:
        raise BarValidationError(report)
    return report


def dataframe_sha256(df: pd.DataFrame) -> str:
    """Deterministic content hash (column order + dtype-independent) for dataset-manifest provenance."""
    ordered = df[list(REQUIRED_COLUMNS) + [c for c in OPTIONAL_COLUMNS if c in df.columns]]
    ordered = ordered.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    return hashlib.sha256(pd.util.hash_pandas_object(ordered, index=False).values.tobytes()).hexdigest()
