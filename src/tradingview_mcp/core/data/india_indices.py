"""India (NSE/BSE) index definitions.

Unlike egx_indices.py (which hardcodes a fixed constituent list), Indian index
membership here is resolved LIVE against the TradingView `india` scanner market
by market-cap-ranked sector/industry filters. This avoids shipping a static
50-ticker list that silently drifts out of date after the next semi-annual
NSE index reconstitution — the filters below approximate official membership
closely (verified against live scanner output) and self-correct every call.

INDIA_INDICES values are filter *specs*, not symbol lists. The service layer
(india_service.py) turns a spec into a live query via _resolve_index_constituents().

The benchmark indices themselves (NIFTY, BANKNIFTY, SENSEX, ...) ARE traded,
quotable TradingView symbols in their own right — those are listed separately
in INDEX_QUOTE_SYMBOL and are NOT derived.
"""
from __future__ import annotations
from typing import Dict, List, Optional, TypedDict


class IndexFilterSpec(TypedDict, total=False):
    exchange: str                    # 'NSE' or 'BSE'
    sector_in: Optional[List[str]]   # restrict to these `sector` values
    industry_in: Optional[List[str]]  # restrict to these `industry` values
    approx_count: int                # roughly how many constituents this index has


# Canonical TradingView symbol for the index level itself (quotable, has its
# own OHLCV) — verified live via Query(markets='india', types=['index']).
INDEX_QUOTE_SYMBOL: Dict[str, str] = {
    "NIFTY50": "NSE:NIFTY",
    "BANKNIFTY": "NSE:BANKNIFTY",
    "SENSEX": "BSE:SENSEX",
    "NIFTYIT": "NSE:CNXIT",
    "NIFTYNEXT50": "NSE:NIFTYJR",
    "NIFTY500": "NSE:CNX500",
    "NIFTYMIDCAP": "NSE:CNXMIDCAP",
    "NIFTYSMALLCAP": "NSE:CNXSMALLCAP",
    "NIFTYENERGY": "NSE:CNXENERGY",
    "NIFTYFINANCE": "NSE:CNXFINANCE",
    "INDIAVIX": "NSE:INDIAVIX",
}

# Filter specs used to derive an approximate constituent list on demand.
INDIA_INDICES: Dict[str, IndexFilterSpec] = {
    "NIFTY50": {
        "exchange": "NSE",
        "sector_in": None,
        "industry_in": None,
        "approx_count": 50,
    },
    "BANKNIFTY": {
        "exchange": "NSE",
        "sector_in": ["Finance"],
        "industry_in": ["Major Banks", "Regional Banks"],
        "approx_count": 12,
    },
    "NIFTYIT": {
        "exchange": "NSE",
        "sector_in": ["Technology Services", "Electronic Technology"],
        "industry_in": None,
        "approx_count": 10,
    },
    "NIFTYFINSERVICE": {
        "exchange": "NSE",
        "sector_in": ["Finance"],
        "industry_in": None,
        "approx_count": 20,
    },
    "NIFTYPHARMA": {
        "exchange": "NSE",
        "sector_in": ["Health Technology", "Health Services"],
        "industry_in": None,
        "approx_count": 20,
    },
    "NIFTYAUTO": {
        "exchange": "NSE",
        "sector_in": ["Consumer Durables"],
        "industry_in": ["Motor Vehicles", "Trucks/Construction/Farm Machinery", "Auto Parts:OEM"],
        "approx_count": 15,
    },
    "NIFTYFMCG": {
        "exchange": "NSE",
        "sector_in": ["Consumer Non-Durables"],
        "industry_in": None,
        "approx_count": 15,
    },
    "NIFTYMETAL": {
        "exchange": "NSE",
        "sector_in": None,
        "industry_in": ["Steel", "Aluminum", "Other Metals/Minerals", "Precious Metals", "Metal Mining"],
        "approx_count": 15,
    },
    "NIFTYREALTY": {
        "exchange": "NSE",
        "sector_in": ["Finance"],
        "industry_in": ["Real Estate Development"],
        "approx_count": 10,
    },
    "SENSEX30": {
        "exchange": "BSE",
        "sector_in": None,
        "industry_in": None,
        "approx_count": 30,
    },
}

INDEX_DESCRIPTIONS: Dict[str, str] = {
    "NIFTY50": "Top 50 NSE large-caps by free-float market cap — India's headline benchmark",
    "BANKNIFTY": "12 most liquid NSE-listed banking stocks",
    "NIFTYIT": "Leading NSE-listed IT/technology services companies",
    "NIFTYFINSERVICE": "Broader financial services universe (banks, NBFCs, insurance, AMCs)",
    "NIFTYPHARMA": "NSE-listed pharmaceutical and healthcare companies",
    "NIFTYAUTO": "NSE-listed automobile manufacturers and auto-parts makers",
    "NIFTYFMCG": "Fast-moving consumer goods companies",
    "NIFTYMETAL": "Metals and mining companies",
    "NIFTYREALTY": "Real-estate development companies",
    "SENSEX30": "Top 30 BSE large-caps by free-float market cap — BSE's headline benchmark",
}


def get_index_names() -> List[str]:
    """Return list of available India index keys (derived indices only)."""
    return list(INDIA_INDICES.keys())


def get_quote_symbol(index_key: str) -> Optional[str]:
    """Return the quotable TradingView symbol for an index name, if known."""
    return INDEX_QUOTE_SYMBOL.get(index_key.strip().upper())
