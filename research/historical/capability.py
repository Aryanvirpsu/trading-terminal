"""H4 blocker #4 — the historical capability fingerprint.

H3's `avdi_adapter.py` is explicit that most of AVDI's evidence families
(`FAMILIES_WITHOUT_HISTORICAL_REPLAY`: catalyst headlines, short interest, SEC filings, options flow,
social sentiment, analyst ratings, macro) have no historical replay and are neutralised to the same
zero-confidence stand-in the live system falls back to. A historical run is therefore NEVER a full replay
of the forward Champion -- it replays only the price/technical-analysis-driven slice of it.

This module names that slice with one short, explicit tag, stamped onto every historical manifest,
report, and ledger this project produces, so nobody (including a future session of this same agent) can
later describe a historical result as "a backtest of the full forward Champion" -- the exact regression
the standing rule in `research/historical/__init__.py` forbids.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Tuple

# The only fingerprint defined today. Adding a new one requires actually wiring a new family's historical
# replay in avdi_adapter.py first -- the fingerprint describes capability that exists, it does not aspire
# to it.
PRICE_TREND_ONLY_V1 = "PRICE_TREND_ONLY_V1"

_KNOWN_FINGERPRINTS = (PRICE_TREND_ONLY_V1,)


@dataclasses.dataclass(frozen=True)
class CapabilityFingerprint:
    tag: str
    live_families: Tuple[str, ...]          # decision-engine families actually driven by historical data
    stubbed_families: Tuple[str, ...]       # families neutralised to decision_engine._fam_stub
    volume_status: str                      # e.g. "RELATIVE_ONLY" -- see FABHAUS_AUDIT_REPORT.md §6
    notes: str

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


def price_trend_only_v1() -> CapabilityFingerprint:
    """The fingerprint for every H3/H4 historical run against `HistoricalAVDIContext` as it exists today:
    real price/TA-driven scoring and gating, a fixed neutral sector input (no historical sector
    breadth/rotation), and every fundamentals/catalyst/flow/sentiment family stubbed to zero confidence."""
    from .avdi_adapter import FAMILIES_WITHOUT_HISTORICAL_REPLAY
    return CapabilityFingerprint(
        tag=PRICE_TREND_ONLY_V1,
        live_families=("price_trend", "momentum", "rsi", "atr", "liquid_momentum_scoring",
                       "risk_sizing", "capacity_and_sector_caps", "fill_simulation"),
        stubbed_families=tuple(FAMILIES_WITHOUT_HISTORICAL_REPLAY) + ("sector_breadth_rotation", "halts"),
        volume_status="RELATIVE_ONLY",
        notes=("No historical replay for catalyst headlines, short interest, SEC filings, options flow, "
               "social sentiment, analyst ratings, macro series, sector breadth/rotation, or trading "
               "halts -- all neutralised to decision_engine._fam_stub or an equivalent fixed neutral "
               "input. Volume is RELATIVE_ONLY (see FABHAUS_AUDIT_REPORT.md section 6): no absolute-"
               "volume rule may be evaluated against historical data. A result produced under this "
               "fingerprint characterizes the price/TA-driven slice of AVDI's decision logic only, and "
               "must never be described as a backtest of the full forward Champion."))


def known_fingerprints() -> List[str]:
    return list(_KNOWN_FINGERPRINTS)


def validate_fingerprint(tag: str) -> None:
    if tag not in _KNOWN_FINGERPRINTS:
        raise ValueError(f"unknown historical capability fingerprint {tag!r}; known: {_KNOWN_FINGERPRINTS!r}. "
                         "A new fingerprint must be added here only once the corresponding family actually "
                         "has a historical replay wired in avdi_adapter.py -- never speculatively.")
