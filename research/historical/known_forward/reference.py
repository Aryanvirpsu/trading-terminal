"""H5 step 1+3: the FROZEN reference for the 2026-09-25 Ubuntu forward session.

Transcribed from `docs/UBUNTU_LIVE_ACCEPTANCE_01.md` (commit `f12a552`). This module is NOT used as input
to the historical decision engine anywhere in this package -- `replay.py` never imports it -- it exists
only for `compare.py` to diff the replay's own, independently produced output against, after the fact.
That separation is deliberate (see the H5 directive step 3): it prevents accidentally feeding known
forward decisions into the replay and then "discovering" them.

Granularity disclosed plainly: this is a SESSION/EVENT-level reference (12 symbol-events, their documented
classification transitions and final actions/reasons, the funnel-by-window table, and two FULLY detailed
order traces for the two real entries) -- not a fabricated cycle-by-cycle machine trace. The real per-cycle
shadow-log rows live in the Ubuntu host's shadow DB, which this environment has no access to; inventing
26 rows of observation/event IDs to look more complete would be worse than being explicit about what is
and is not available.
"""
from __future__ import annotations

import dataclasses
from typing import List, Optional, Tuple

SOURCE_DOC = "docs/UBUNTU_LIVE_ACCEPTANCE_01.md"
SOURCE_COMMIT = "f12a552"
SESSION_DATE = "2026-09-25"


@dataclasses.dataclass(frozen=True)
class SessionSummary:
    session_date: str
    engine_version_boundary_et: str            # wall-clock ET time the v1.1 evidence boundary was recorded
    boundary_equity: float
    boundary_cash: float
    boundary_open_positions: int
    boundary_entries_today: int
    discovery_cycles_expected: int
    discovery_cycles_actual: int
    premarket_prep_cycles: int
    raw_observations_total: int                # 133 (110 regular + 18 post_cutoff + 5 premarket_prep)
    independent_events_total: int               # 12
    journaled_signals_total: int                 # 21 (v1.1); 55 v1.0 rows also exist, excluded from v1.1 analysis
    paper_entries_total: int                    # 2 (DELL, META)
    entry_cutoff_note: str
    close_equity: float
    close_cash: float
    close_open_positions: int
    v11_pnl_unrealized: float


SESSION = SessionSummary(
    session_date=SESSION_DATE,
    engine_version_boundary_et="09:35:22",
    boundary_equity=504.66,
    boundary_cash=504.66,
    boundary_open_positions=0,
    boundary_entries_today=0,
    discovery_cycles_expected=26,
    discovery_cycles_actual=26,
    premarket_prep_cycles=1,                     # 09:15 observation scan (5 premarket_prep observations)
    raw_observations_total=133,
    independent_events_total=12,
    journaled_signals_total=21,
    paper_entries_total=2,
    entry_cutoff_note="regular entries allowed through 14:50 ET; 15:05-15:50 ET cycles are post_cutoff, observation-only",
    close_equity=504.49,
    close_cash=353.67,
    close_open_positions=2,
    v11_pnl_unrealized=-0.17,
)


@dataclasses.dataclass(frozen=True)
class EventReference:
    """One of the 12 independent events (one per symbol) the forward session's shadow-log event clustering
    collapsed 133 raw observations into. `sector` is the real dashboard.sector_map assignment (used by H5's
    multi-sector replay coverage), not itself sourced from the acceptance doc."""
    symbol: str
    sector: str
    observations: int
    first_seen_cycle_et: Optional[str]           # None where the doc doesn't give the exact first-seen time
    classification_progression: Tuple[str, ...]  # e.g. ("MONITOR", "TRADEABLE")
    final_action: str                            # "entered" | "not_entered_capacity" | "not_entered_cutoff" | "not_entered_generic"
    refusal_reason: Optional[str]
    notes: str


REFERENCE_EVENTS: Tuple[EventReference, ...] = (
    EventReference("DELL", "technology", 26, "09:15",
                  ("MONITOR", "TRADEABLE"), "entered", None,
                  "MONITOR 09:15-11:50 ET -> TRADEABLE at 12:05 ET -> entered; TRADEABLE for the rest of the day (held)."),
    EventReference("META", "communication", 20, "09:35",
                  ("REJECT", "MONITOR", "TRADEABLE"), "entered", None,
                  "REJECT/MONITOR flips (label flipped several times near threshold: 8 journal rows from 20 observations) -> TRADEABLE at 13:05 ET -> entered."),
    EventReference("AAPL", "technology", 23, "09:50",
                  ("MONITOR", "TRADEABLE"), "not_entered_capacity",
                  "technology sector slot held by DELL (shadow capacity_reason=sector; ledger reason generic: "
                  "'stock leg not canonically executable')",
                  "Reached TRADEABLE at 12:05 ET alongside DELL; only one technology slot available and the "
                  "Champion's scanner order picked DELL."),
    EventReference("TMO", "health_care", 6, "10:05",
                  ("MONITOR", "TRADEABLE"), "not_entered_capacity",
                  "daily entry cap reached (2/day, incl. persisted entries)",
                  "6 observations 14:20-15:50 ET; blocked once DELL+META had consumed the 2-per-day cap."),
    EventReference("MSFT", "technology", 1, "14:35",
                  ("TRADEABLE",), "not_entered_capacity",
                  "daily entry cap reached (2/day, incl. persisted entries)",
                  "Single observation at 14:35 ET, already past the point the daily cap was consumed."),
    EventReference("TSLA", "consumer_discretionary", 1, "15:05",
                  ("MONITOR",), "not_entered_cutoff",
                  "post-cutoff (15:05-15:50 ET window is observation-only; regular entries end at 14:50 ET)",
                  "Single observation at 15:05 ET -- arrived after the entry cutoff, observation-only by session design."),
    EventReference("AMD", "technology", 26, "09:35", ("REJECT", "MONITOR"), "not_entered_generic", None,
                  "Present in the very first 09:35 scan; never reached TRADEABLE per the funnel table."),
    EventReference("CRM", "technology", 25, "09:35", ("REJECT", "MONITOR"), "not_entered_generic", None,
                  "Present in the very first 09:35 scan; never reached TRADEABLE per the funnel table."),
    EventReference("NVDA", "technology", 2, "09:35", ("REJECT", "MONITOR"), "not_entered_generic", None,
                  "Only 2 observations all day; never reached TRADEABLE."),
    EventReference("FCX", "materials", 1, "15:05", ("MONITOR",), "not_entered_generic", None,
                  "First appeared at 15:05 ET, single observation."),
    EventReference("NEM", "materials", 1, "15:05", ("MONITOR",), "not_entered_generic", None,
                  "First appeared at 15:05 ET, single observation."),
    EventReference("VRTX", "health_care", 1, "10:05", ("MONITOR",), "not_entered_generic", None,
                  "First appeared at 10:05 ET, single observation."),
)


@dataclasses.dataclass(frozen=True)
class OrderTrace:
    """A fully detailed, real end-to-end entry trace -- the doc gives every field needed for a genuine
    apples-to-apples comparison against the replay's own order for the same symbol."""
    symbol: str
    decision_time_et: str
    quality: float
    expected_r: float
    classification: str
    sector: str
    capacity_note: str
    quote_provider: str
    quote_ask: float
    spread_pct: float
    executable_price: float
    quantity: float
    planned_risk: float
    notional: float
    order_id: str
    fill_price: float
    slippage_dollars: float
    fees: float
    stop: float
    target: float
    mfe_r_at_close: float
    mae_r_at_close: float


REFERENCE_ORDERS: Tuple[OrderTrace, ...] = (
    OrderTrace(
        symbol="DELL", decision_time_et="12:05:23", quality=67.0, expected_r=1.007, classification="TRADEABLE",
        sector="technology", capacity_note="two eligible (DELL, AAPL), one technology slot; scanner order picked DELL",
        quote_provider="yahoo", quote_ask=565.4026, spread_pct=0.1, executable_price=565.6853,
        quantity=0.109577, planned_risk=5.00, notional=61.99, order_id="ord_6b76c171eb2fcd10",
        fill_price=565.6853, slippage_dollars=0.2827, fees=0.0, stop=517.79, target=659.78,
        mfe_r_at_close=0.17, mae_r_at_close=0.51,
    ),
    OrderTrace(
        symbol="META", decision_time_et="13:05:00", quality=62.1, expected_r=0.924, classification="TRADEABLE",
        sector="communication", capacity_note="no sector conflict",
        quote_provider="tradingview", quote_ask=750.1249, spread_pct=None, executable_price=750.5,
        quantity=0.118596, planned_risk=5.00, notional=None, order_id="ord_811f32a092e7b76e",
        fill_price=750.5, slippage_dollars=0.3751, fees=0.0, stop=706.2, target=839.43,
        mfe_r_at_close=0.43, mae_r_at_close=0.09,
    ),
)


# Funnel-by-window table (doc's own aggregation, kept verbatim for the comparison report's context --
# not itself diffed cycle-by-cycle against the replay, since the replay does not group cycles this way).
FUNNEL_BY_WINDOW: Tuple[dict, ...] = (
    {"window": "09:15-11:50 (12 cycles)", "tradeable": "0", "monitor": "1-3", "reject": "2-4",
     "capacity_eligible": 0, "entries": 0},
    {"window": "12:05", "tradeable": "2", "monitor": "1", "reject": "2", "capacity_eligible": 2, "entries": 1,
     "entries_note": "DELL"},
    {"window": "12:20-12:50", "tradeable": "1", "monitor": "2", "reject": "2", "capacity_eligible": 1, "entries": 0,
     "entries_note": "AAPL sector-blocked; DELL held"},
    {"window": "13:05", "tradeable": "2", "monitor": "1", "reject": "2", "capacity_eligible": 2, "entries": 1,
     "entries_note": "META"},
    {"window": "13:20-14:50", "tradeable": "2-3", "monitor": "0-1", "reject": "2", "capacity_eligible": "2-3",
     "entries": 0, "entries_note": "daily cap reached; existing positions"},
    {"window": "15:05-15:50 (observation only)", "tradeable": "1-2", "monitor": "1", "reject": "1-2",
     "capacity_eligible": "1-2", "entries": 0, "entries_note": "after the entry cutoff"},
)


def event_by_symbol(symbol: str) -> Optional[EventReference]:
    for e in REFERENCE_EVENTS:
        if e.symbol == symbol.upper():
            return e
    return None


def order_by_symbol(symbol: str) -> Optional[OrderTrace]:
    for o in REFERENCE_ORDERS:
        if o.symbol == symbol.upper():
            return o
    return None


def all_symbols() -> List[str]:
    return [e.symbol for e in REFERENCE_EVENTS]
