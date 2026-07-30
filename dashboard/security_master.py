"""Security-master service — a locally cached, searchable ticker universe.

WHY THIS EXISTS
    The old search hit a live price provider on every keystroke and only accepted
    an already-valid ticker *shape*. So "Apple", "Alphabet", "Google", "Nokia",
    "BRK B" and "NOKIA.HE" all failed, and each lookup cost 4-9 s. This module
    replaces that with a real security master:

      * A local SQLite table (``security_master.db``) of US stocks, ETFs, ADRs
        plus curated major foreign listings — symbol, company name, aliases,
        former names, exchange, MIC, instrument type, currency, country, CIK.
      * Built from THREE free sources, merged and de-duplicated:
          1. A curated in-repo SEED (always available, offline-safe) that also
             carries company aliases (Google->GOOGL, Facebook->META) and foreign
             listings (NOKIA.HE) the machine sources don't express well.
          2. SEC ``company_tickers.json`` — ~10k US names with CIK (no key).
          3. Finnhub ``/stock/symbol?exchange=US`` — adds ETFs/ADRs, instrument
             type and currency (uses the key already in .env; optional).
      * Search runs entirely IN MEMORY over a prebuilt index: exact/prefix/alias/
        token/substring/typo-tolerant fuzzy, ranked, popularity-boosted. Target
        < 150 ms warm (typically < 10 ms).
      * Refresh is incremental and BACKGROUND — search never blocks on a network
        call. First boot serves the seed instantly and back-fills the long tail.

Nothing here can raise into a request path: every public function is defensive
and returns a structured result, never an exception.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

# ── Paths ────────────────────────────────────────────────────────────────────
_DATA_DIR = os.path.expanduser("~/.tradingview_mcp_data")
_DB_PATH = os.path.join(_DATA_DIR, "security_master.db")
_SEC_CACHE = os.path.join(_DATA_DIR, "sec_company_tickers.json")

# Refresh cadence: a machine-source rebuild is considered fresh for 7 days.
_REFRESH_SECONDS = 7 * 24 * 3600

# ── Curated seed ─────────────────────────────────────────────────────────────
# (symbol, name, exchange, type, currency, country, popularity, aliases, yahoo?)
# popularity 0-100 is a hand-set prior used only to break ties toward names a
# human is most likely to mean. Machine sources fill everything else.
_SEED: List[tuple] = [
    # Mega-cap US common — with the aliases people actually type.
    ("AAPL", "Apple Inc.", "NASDAQ", "Common Stock", "USD", "US", 100, ["apple"], None),
    ("MSFT", "Microsoft Corporation", "NASDAQ", "Common Stock", "USD", "US", 100, ["microsoft"], None),
    ("GOOGL", "Alphabet Inc. (Class A)", "NASDAQ", "Common Stock", "USD", "US", 99, ["google", "alphabet", "google a"], None),
    ("GOOG", "Alphabet Inc. (Class C)", "NASDAQ", "Common Stock", "USD", "US", 95, ["google", "alphabet", "google c"], None),
    ("AMZN", "Amazon.com, Inc.", "NASDAQ", "Common Stock", "USD", "US", 99, ["amazon"], None),
    ("META", "Meta Platforms, Inc.", "NASDAQ", "Common Stock", "USD", "US", 98, ["facebook", "meta", "instagram", "whatsapp"], None),
    ("NVDA", "NVIDIA Corporation", "NASDAQ", "Common Stock", "USD", "US", 100, ["nvidia"], None),
    ("TSLA", "Tesla, Inc.", "NASDAQ", "Common Stock", "USD", "US", 99, ["tesla"], None),
    ("NFLX", "Netflix, Inc.", "NASDAQ", "Common Stock", "USD", "US", 92, ["netflix"], None),
    ("AMD", "Advanced Micro Devices, Inc.", "NASDAQ", "Common Stock", "USD", "US", 93, ["amd"], None),
    ("INTC", "Intel Corporation", "NASDAQ", "Common Stock", "USD", "US", 88, ["intel"], None),
    ("CRM", "Salesforce, Inc.", "NYSE", "Common Stock", "USD", "US", 84, ["salesforce"], None),
    ("ORCL", "Oracle Corporation", "NYSE", "Common Stock", "USD", "US", 84, ["oracle"], None),
    ("ADBE", "Adobe Inc.", "NASDAQ", "Common Stock", "USD", "US", 84, ["adobe"], None),
    ("PYPL", "PayPal Holdings, Inc.", "NASDAQ", "Common Stock", "USD", "US", 82, ["paypal"], None),
    ("UBER", "Uber Technologies, Inc.", "NYSE", "Common Stock", "USD", "US", 84, ["uber"], None),
    ("PLTR", "Palantir Technologies Inc.", "NASDAQ", "Common Stock", "USD", "US", 88, ["palantir"], None),
    ("SOFI", "SoFi Technologies, Inc.", "NASDAQ", "Common Stock", "USD", "US", 80, ["sofi"], None),
    ("COIN", "Coinbase Global, Inc.", "NASDAQ", "Common Stock", "USD", "US", 85, ["coinbase"], None),
    ("SHOP", "Shopify Inc.", "NYSE", "Common Stock", "USD", "US", 82, ["shopify"], None),
    ("DIS", "The Walt Disney Company", "NYSE", "Common Stock", "USD", "US", 88, ["disney", "walt disney"], None),
    ("BA", "The Boeing Company", "NYSE", "Common Stock", "USD", "US", 86, ["boeing"], None),
    ("JPM", "JPMorgan Chase & Co.", "NYSE", "Common Stock", "USD", "US", 90, ["jpmorgan", "jp morgan", "chase"], None),
    ("BAC", "Bank of America Corporation", "NYSE", "Common Stock", "USD", "US", 85, ["bank of america", "bofa"], None),
    ("WMT", "Walmart Inc.", "NYSE", "Common Stock", "USD", "US", 86, ["walmart"], None),
    ("KO", "The Coca-Cola Company", "NYSE", "Common Stock", "USD", "US", 82, ["coca cola", "coca-cola", "coke"], None),
    ("PEP", "PepsiCo, Inc.", "NASDAQ", "Common Stock", "USD", "US", 80, ["pepsi", "pepsico"], None),
    ("XOM", "Exxon Mobil Corporation", "NYSE", "Common Stock", "USD", "US", 82, ["exxon", "exxonmobil"], None),
    ("V", "Visa Inc.", "NYSE", "Common Stock", "USD", "US", 86, ["visa"], None),
    ("MA", "Mastercard Incorporated", "NYSE", "Common Stock", "USD", "US", 84, ["mastercard"], None),
    ("F", "Ford Motor Company", "NYSE", "Common Stock", "USD", "US", 80, ["ford"], None),
    ("GM", "General Motors Company", "NYSE", "Common Stock", "USD", "US", 78, ["general motors"], None),
    ("T", "AT&T Inc.", "NYSE", "Common Stock", "USD", "US", 78, ["at&t", "att"], None),
    ("MCD", "McDonald's Corporation", "NYSE", "Common Stock", "USD", "US", 80, ["mcdonalds", "mcdonald's"], None),
    ("NKE", "NIKE, Inc.", "NYSE", "Common Stock", "USD", "US", 80, ["nike"], None),
    ("SBUX", "Starbucks Corporation", "NASDAQ", "Common Stock", "USD", "US", 78, ["starbucks"], None),
    ("GME", "GameStop Corp.", "NYSE", "Common Stock", "USD", "US", 76, ["gamestop"], None),
    ("AMC", "AMC Entertainment Holdings, Inc.", "NYSE", "Common Stock", "USD", "US", 72, ["amc"], None),
    # Share classes — the canonical "BRK.B" plus its spellings.
    ("BRK.B", "Berkshire Hathaway Inc. (Class B)", "NYSE", "Common Stock", "USD", "US", 90, ["berkshire", "berkshire hathaway", "brk b", "brkb", "brk/b"], "BRK-B"),
    ("BRK.A", "Berkshire Hathaway Inc. (Class A)", "NYSE", "Common Stock", "USD", "US", 70, ["berkshire", "berkshire hathaway", "brk a", "brka"], "BRK-A"),
    ("BF.B", "Brown-Forman Corporation (Class B)", "NYSE", "Common Stock", "USD", "US", 40, ["brown forman", "brown-forman"], "BF-B"),
    # ADRs / foreign companies with a US listing.
    ("NOK", "Nokia Oyj (ADR)", "NYSE", "ADR", "USD", "US", 74, ["nokia"], None),
    ("BABA", "Alibaba Group Holding Limited (ADR)", "NYSE", "ADR", "USD", "US", 82, ["alibaba"], None),
    ("TSM", "Taiwan Semiconductor Manufacturing (ADR)", "NYSE", "ADR", "USD", "US", 84, ["tsmc", "taiwan semiconductor"], None),
    ("TM", "Toyota Motor Corporation (ADR)", "NYSE", "ADR", "USD", "US", 70, ["toyota"], None),
    ("SONY", "Sony Group Corporation (ADR)", "NYSE", "ADR", "USD", "US", 72, ["sony"], None),
    ("SHEL", "Shell plc (ADR)", "NYSE", "ADR", "USD", "US", 70, ["shell", "royal dutch shell"], None),
    ("NVO", "Novo Nordisk A/S (ADR)", "NYSE", "ADR", "USD", "US", 78, ["novo nordisk", "ozempic", "wegovy"], None),
    ("ASML", "ASML Holding N.V.", "NASDAQ", "ADR", "USD", "US", 82, ["asml"], None),
    ("SAP", "SAP SE (ADR)", "NYSE", "ADR", "USD", "US", 70, ["sap"], None),
    ("BP", "BP p.l.c. (ADR)", "NYSE", "ADR", "USD", "US", 68, ["bp", "british petroleum"], None),
    ("PDD", "PDD Holdings Inc. (ADR)", "NASDAQ", "ADR", "USD", "US", 72, ["pinduoduo", "temu", "pdd"], None),
    ("NIO", "NIO Inc. (ADR)", "NYSE", "ADR", "USD", "US", 70, ["nio"], None),
    # Major foreign listings (Yahoo-supported suffixes) — distinct from the US ADR.
    ("NOKIA.HE", "Nokia Oyj", "HEL", "Common Stock", "EUR", "FI", 60, ["nokia helsinki", "nokia oyj"], "NOKIA.HE"),
    ("ERICB.ST", "Telefonaktiebolaget LM Ericsson (Class B)", "STO", "Common Stock", "SEK", "SE", 45, ["ericsson"], "ERIC-B.ST"),
    ("VOD.L", "Vodafone Group Plc", "LON", "Common Stock", "GBP", "GB", 45, ["vodafone"], "VOD.L"),
    ("HSBA.L", "HSBC Holdings plc", "LON", "Common Stock", "GBP", "GB", 50, ["hsbc"], "HSBA.L"),
    ("SAP.DE", "SAP SE", "XETRA", "Common Stock", "EUR", "DE", 50, ["sap germany"], "SAP.DE"),
    ("AIR.PA", "Airbus SE", "PAR", "Common Stock", "EUR", "FR", 55, ["airbus"], "AIR.PA"),
    ("7203.T", "Toyota Motor Corporation", "TSE", "Common Stock", "JPY", "JP", 55, ["toyota tokyo"], "7203.T"),
    ("RELIANCE.NS", "Reliance Industries Limited", "NSE", "Common Stock", "INR", "IN", 60, ["reliance"], "RELIANCE.NS"),
    ("TCS.NS", "Tata Consultancy Services Limited", "NSE", "Common Stock", "INR", "IN", 55, ["tcs", "tata consultancy"], "TCS.NS"),
    ("SHOP.TO", "Shopify Inc.", "TSX", "Common Stock", "CAD", "CA", 45, ["shopify toronto"], "SHOP.TO"),
    # Popular ETFs (Finnhub name is often blank on the free tier — seed it).
    ("SPY", "SPDR S&P 500 ETF Trust", "ARCA", "ETP", "USD", "US", 100, ["s&p 500", "sp500", "spx etf", "spdr"], None),
    ("VOO", "Vanguard S&P 500 ETF", "ARCA", "ETP", "USD", "US", 90, ["vanguard s&p 500"], None),
    ("IVV", "iShares Core S&P 500 ETF", "ARCA", "ETP", "USD", "US", 80, ["ishares s&p 500"], None),
    ("QQQ", "Invesco QQQ Trust (Nasdaq-100)", "NASDAQ", "ETP", "USD", "US", 98, ["nasdaq 100", "qqq", "invesco qqq"], None),
    ("DIA", "SPDR Dow Jones Industrial Average ETF", "ARCA", "ETP", "USD", "US", 78, ["dow", "dow jones"], None),
    ("IWM", "iShares Russell 2000 ETF", "ARCA", "ETP", "USD", "US", 84, ["russell 2000", "small cap etf"], None),
    ("VTI", "Vanguard Total Stock Market ETF", "ARCA", "ETP", "USD", "US", 82, ["total market etf"], None),
    ("VXX", "iPath Series B S&P 500 VIX Short-Term Futures", "ARCA", "ETP", "USD", "US", 60, ["vix etf"], None),
    ("GLD", "SPDR Gold Shares", "ARCA", "ETP", "USD", "US", 82, ["gold etf"], None),
    ("SLV", "iShares Silver Trust", "ARCA", "ETP", "USD", "US", 68, ["silver etf"], None),
    ("USO", "United States Oil Fund", "ARCA", "ETP", "USD", "US", 66, ["oil etf"], None),
    ("TLT", "iShares 20+ Year Treasury Bond ETF", "NASDAQ", "ETP", "USD", "US", 78, ["treasury etf", "bonds etf"], None),
    ("HYG", "iShares iBoxx High Yield Corporate Bond ETF", "ARCA", "ETP", "USD", "US", 62, ["high yield etf", "junk bonds"], None),
    ("XLK", "Technology Select Sector SPDR Fund", "ARCA", "ETP", "USD", "US", 70, ["tech sector etf"], None),
    ("XLF", "Financial Select Sector SPDR Fund", "ARCA", "ETP", "USD", "US", 70, ["financials etf", "bank etf"], None),
    ("XLE", "Energy Select Sector SPDR Fund", "ARCA", "ETP", "USD", "US", 70, ["energy etf"], None),
    ("XLV", "Health Care Select Sector SPDR Fund", "ARCA", "ETP", "USD", "US", 68, ["healthcare etf"], None),
    ("XLY", "Consumer Discretionary Select Sector SPDR", "ARCA", "ETP", "USD", "US", 64, ["consumer etf"], None),
    ("XLI", "Industrial Select Sector SPDR Fund", "ARCA", "ETP", "USD", "US", 62, ["industrials etf"], None),
    ("SMH", "VanEck Semiconductor ETF", "NASDAQ", "ETP", "USD", "US", 74, ["semiconductor etf", "chip etf"], None),
    ("SOXL", "Direxion Daily Semiconductor Bull 3X", "ARCA", "ETP", "USD", "US", 72, ["semiconductor 3x"], None),
    ("ARKK", "ARK Innovation ETF", "ARCA", "ETP", "USD", "US", 72, ["ark", "cathie wood", "ark innovation"], None),
    ("TQQQ", "ProShares UltraPro QQQ (3x)", "NASDAQ", "ETP", "USD", "US", 80, ["3x nasdaq", "tqqq"], None),
    ("SQQQ", "ProShares UltraPro Short QQQ (-3x)", "NASDAQ", "ETP", "USD", "US", 66, ["short nasdaq"], None),
    ("BITO", "ProShares Bitcoin Strategy ETF", "ARCA", "ETP", "USD", "US", 64, ["bitcoin etf"], None),
    ("IBIT", "iShares Bitcoin Trust", "NASDAQ", "ETP", "USD", "US", 78, ["bitcoin etf", "ishares bitcoin"], None),
]

# Popular set (drives the "popular symbols" endpoint and a small rank boost).
POPULAR = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META", "SPY", "QQQ",
           "AMD", "PLTR", "COIN", "NFLX", "BRK.B", "JPM", "DIS", "BA", "SOFI",
           "IWM", "GLD"]

# Instrument-type rank prior (prefer real, liquid instruments over units/warrants).
_TYPE_RANK = {"common stock": 20, "adr": 16, "etp": 18, "etf": 18, "reit": 12,
              "": 6, "unit": 2, "warrant": 0, "right": 0, "preferred": 4}

# Yahoo suffixes that denote a FOREIGN EXCHANGE (keep the dot + suffix intact);
# a dot NOT followed by one of these is treated as a US share-class separator
# (BRK.B -> BRK-B).
_FOREIGN_SUFFIXES = {
    "HE", "ST", "L", "DE", "PA", "AS", "BR", "MC", "MI", "LS", "VI", "SW", "OL",
    "CO", "HE", "IC", "F", "TO", "V", "NE", "CN", "MX", "SA", "BA", "SN",
    "T", "HK", "SS", "SZ", "KS", "KQ", "TW", "TWO", "SI", "AX", "NZ", "JK",
    "BK", "KL", "NS", "BO", "TA", "IS", "CA", "QA", "SR", "AD", "DU", "WA",
    "PR", "ME", "IR", "AT", "HM", "SG", "BE", "MU", "DE",
}

# Friendly names for the MIC codes Finnhub returns, so the UI shows "NASDAQ"
# instead of "XNAS". Unknown codes pass through unchanged.
_MIC_NAME = {
    "XNAS": "NASDAQ", "XNGS": "NASDAQ", "XNMS": "NASDAQ", "XNCM": "NASDAQ",
    "XNYS": "NYSE", "XASE": "NYSE American", "ARCX": "NYSE Arca", "XASX": "NYSE American",
    "BATS": "Cboe BZX", "BATY": "Cboe BYX", "EDGX": "Cboe EDGX", "EDGA": "Cboe EDGA",
    "OOTC": "OTC", "OTCM": "OTC", "PSGM": "OTC", "PINX": "OTC Pink",
    "IEXG": "IEX", "XCBO": "Cboe", "MEMX": "MEMX", "XCHI": "NYSE Chicago",
    "HEL": "Helsinki", "STO": "Stockholm", "LON": "London", "XETRA": "Xetra",
    "PAR": "Paris", "TSE": "Tokyo", "NSE": "NSE India", "TSX": "Toronto", "ARCA": "NYSE Arca",
}

_INDEX_ALIAS = {  # common index shorthands -> Yahoo caret symbols
    "SPX": "^GSPC", "^SPX": "^GSPC", "NDX": "^NDX", "DJI": "^DJI", "DJIA": "^DJI",
    "VIX": "^VIX", "RUT": "^RUT",
}

# ── In-memory index ──────────────────────────────────────────────────────────
_INDEX: List[Dict[str, Any]] = []
_BY_SYMBOL: Dict[str, Dict[str, Any]] = {}
# Secondary indexes for sub-150 ms search over the full ~30k universe: we never
# score all 30k — we gather a narrow candidate set from these first.
_SYM_SORTED: List[str] = []                       # sorted lower-case symbols
_SYM_SORTED_RECS: List[Dict[str, Any]] = []       # parallel to _SYM_SORTED
_TOK_SORTED: List[Tuple[str, Dict[str, Any]]] = []  # sorted (name-token, rec)
_TOK_KEYS: List[str] = []                          # parallel sorted token keys
_ALIAS_ENTRIES: List[Tuple[str, Dict[str, Any]]] = []  # (alias, rec) — small
_FUZZ_SYM: Dict[str, List[Dict[str, Any]]] = {}   # first char -> recs (symbol fuzzy)
_FUZZ_TOK: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}  # first-2 -> (token0, rec)
_READY = threading.Event()
_LOAD_LOCK = threading.Lock()
_REFRESHING = {"on": False}
_LAST_BUILD = {"at": None, "sources": [], "count": 0}


def _norm(s: str) -> str:
    return (s or "").strip().upper().lstrip("$").replace(" ", "")


def canonical_query(q: str) -> str:
    """User-facing canonicalisation for a *symbol-shaped* query: upper, trim $,
    collapse whitespace, turn a space or slash between a root and a 1-char class
    into a dot (``BRK B``/``BRK/B`` -> ``BRK.B``)."""
    raw = (q or "").strip().upper().lstrip("$")
    m = re.fullmatch(r"([A-Z]{1,5})[ /.\-]([A-Z])", raw)
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    return raw.replace(" ", "")


def to_yahoo(symbol: str) -> str:
    """Map a canonical master symbol to the Yahoo Finance symbol.

    * Known override (seed ``yahoo`` column) wins.
    * Index shorthand -> caret symbol.
    * ``ROOT.SUFFIX`` where SUFFIX is a known foreign-exchange code is preserved
      verbatim (NOKIA.HE stays NOKIA.HE) — the old code wrongly turned it into
      NOKIA-HE and broke every foreign listing.
    * Otherwise a dot is a US share-class separator: BRK.B -> BRK-B.
    """
    s = _norm(symbol)
    if not s:
        return s
    rec = _BY_SYMBOL.get(s)
    if rec and rec.get("yahoo"):
        return rec["yahoo"]
    if s in _INDEX_ALIAS:
        return _INDEX_ALIAS[s]
    if s.startswith("^"):
        return s
    if "." in s:
        root, _, suf = s.rpartition(".")
        if suf in _FOREIGN_SUFFIXES:
            return s  # foreign listing — keep the exchange suffix
        return s.replace(".", "-")  # US share class
    return s


def _tokens(name: str) -> List[str]:
    return [t for t in re.split(r"[^A-Za-z0-9&]+", (name or "").lower()) if t]


def _mk_record(symbol: str, name: str, exchange: str = "", typ: str = "",
               currency: str = "USD", country: str = "US", cik: str = "",
               aliases: Optional[List[str]] = None, popularity: int = 0,
               yahoo: Optional[str] = None, source: str = "") -> Dict[str, Any]:
    symbol = _norm(symbol)
    name = (name or "").strip()
    return {
        "symbol": symbol, "name": name, "exchange": exchange or "",
        "type": typ or "", "currency": currency or "USD", "country": country or "",
        "cik": cik or "", "yahoo": yahoo, "source": source, "popularity": popularity,
        # precomputed lowercase search fields
        "_sym_l": symbol.lower(), "_name_l": name.lower(),
        "_tokens": _tokens(name), "_aliases": [a.lower() for a in (aliases or [])],
    }


# ── SQLite persistence ───────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    os.makedirs(_DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS securities (
            symbol   TEXT PRIMARY KEY,
            name     TEXT,
            exchange TEXT,
            type     TEXT,
            currency TEXT,
            country  TEXT,
            cik      TEXT,
            yahoo    TEXT,
            aliases  TEXT,
            popularity INTEGER DEFAULT 0,
            source   TEXT,
            updated_at REAL
        )""")
    # Indexes that matter for the cold-load path and any SQL-side prefix lookups.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sec_name ON securities(name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sec_type ON securities(type)")
    conn.execute("""CREATE TABLE IF NOT EXISTS master_meta (
            key TEXT PRIMARY KEY, value TEXT)""")
    conn.commit()


def _meta_get(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM master_meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO master_meta(key, value) VALUES(?, ?)", (key, value))
    conn.commit()


def _upsert_many(conn: sqlite3.Connection, records: List[Dict[str, Any]]) -> int:
    now = time.time()
    rows = [(r["symbol"], r["name"], r["exchange"], r["type"], r["currency"],
             r["country"], r.get("cik", ""), r.get("yahoo"),
             json.dumps(r.get("aliases_raw") or []), r.get("popularity", 0),
             r.get("source", ""), now) for r in records]
    conn.executemany("""
        INSERT INTO securities(symbol,name,exchange,type,currency,country,cik,yahoo,aliases,popularity,source,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(symbol) DO UPDATE SET
            name=excluded.name, exchange=excluded.exchange, type=excluded.type,
            currency=excluded.currency, country=excluded.country,
            cik=CASE WHEN excluded.cik!='' THEN excluded.cik ELSE securities.cik END,
            yahoo=COALESCE(excluded.yahoo, securities.yahoo),
            aliases=excluded.aliases, popularity=MAX(excluded.popularity, securities.popularity),
            source=excluded.source, updated_at=excluded.updated_at
    """, rows)
    conn.commit()
    return len(rows)


# ── Index build (seed + DB) ──────────────────────────────────────────────────

def _seed_records() -> List[Dict[str, Any]]:
    out = []
    for sym, name, exch, typ, cur, ctry, pop, aliases, yh in _SEED:
        r = _mk_record(sym, name, exch, typ, cur, ctry, aliases=aliases,
                       popularity=pop, yahoo=yh, source="seed")
        r["aliases_raw"] = aliases or []
        out.append(r)
    return out


def _rebuild_index_from(records: List[Dict[str, Any]]) -> None:
    """Replace the in-memory index atomically. Seed aliases/popularity always win
    over machine data for the same symbol (they encode human intent)."""
    seed = {r["symbol"]: r for r in _seed_records()}
    merged: Dict[str, Dict[str, Any]] = {}
    for r in records:
        merged[r["symbol"]] = r
    for sym, sr in seed.items():
        if sym in merged:
            m = merged[sym]
            # Keep the better (usually longer / seed) name, always keep seed aliases,
            # popularity and yahoo override.
            if not m.get("name") or len(sr["name"]) > len(m.get("name", "")):
                m["name"] = sr["name"]; m["_name_l"] = sr["_name_l"]; m["_tokens"] = sr["_tokens"]
            m["_aliases"] = list(set(m.get("_aliases", []) + sr["_aliases"]))
            m["aliases_raw"] = list(set((m.get("aliases_raw") or []) + (sr.get("aliases_raw") or [])))
            m["popularity"] = max(m.get("popularity", 0), sr.get("popularity", 0))
            if sr.get("yahoo") and not m.get("yahoo"):
                m["yahoo"] = sr["yahoo"]
            if not m.get("currency"):
                m["currency"] = sr["currency"]
        else:
            merged[sym] = sr
    # Popularity boost for the curated popular list.
    for i, sym in enumerate(POPULAR):
        if sym in merged:
            merged[sym]["popularity"] = max(merged[sym]["popularity"], 100 - i)
    idx = list(merged.values())
    _build_secondary(idx)
    global _INDEX, _BY_SYMBOL
    _INDEX = idx
    _BY_SYMBOL = merged
    _READY.set()


def _build_secondary(idx: List[Dict[str, Any]]) -> None:
    """Build the bisect/bucket indexes that keep search fast on 30k rows."""
    global _SYM_SORTED, _SYM_SORTED_RECS, _TOK_SORTED, _TOK_KEYS, _ALIAS_ENTRIES, _FUZZ_SYM, _FUZZ_TOK
    pairs = sorted(((r["_sym_l"], r) for r in idx), key=lambda x: x[0])
    _SYM_SORTED = [p[0] for p in pairs]
    _SYM_SORTED_RECS = [p[1] for p in pairs]
    toks: List[Tuple[str, Dict[str, Any]]] = []
    aliases: List[Tuple[str, Dict[str, Any]]] = []
    fsym: Dict[str, List[Dict[str, Any]]] = {}
    ftok: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
    for r in idx:
        for t in r["_tokens"]:
            toks.append((t, r))
        for a in r["_aliases"]:
            aliases.append((a, r))
        sl = r["_sym_l"]
        if sl:
            fsym.setdefault(sl[0], []).append(r)
        t0 = r["_tokens"][0] if r["_tokens"] else ""
        if len(t0) >= 2:
            ftok.setdefault(t0[:2], []).append((t0, r))
    _TOK_SORTED = sorted(toks, key=lambda x: x[0])
    _TOK_KEYS = [t for t, _ in _TOK_SORTED]
    _ALIAS_ENTRIES = aliases
    _FUZZ_SYM = fsym
    _FUZZ_TOK = ftok


def _prefix_range(sorted_keys: List[str], prefix: str) -> Tuple[int, int]:
    """[lo, hi) index range of keys that start with prefix, via bisect."""
    import bisect
    lo = bisect.bisect_left(sorted_keys, prefix)
    hi = bisect.bisect_right(sorted_keys, prefix + "￿")
    return lo, hi


def _load_from_db() -> int:
    try:
        conn = _connect()
        _ensure_schema(conn)
        rows = conn.execute("SELECT * FROM securities").fetchall()
        conn.close()
    except Exception:
        rows = []
    records = []
    for row in rows:
        try:
            aliases = json.loads(row["aliases"] or "[]")
        except Exception:
            aliases = []
        r = _mk_record(row["symbol"], row["name"], row["exchange"], row["type"],
                       row["currency"], row["country"], row["cik"] or "",
                       aliases=aliases, popularity=row["popularity"] or 0,
                       yahoo=row["yahoo"], source=row["source"] or "db")
        r["aliases_raw"] = aliases
        records.append(r)
    _rebuild_index_from(records)
    return len(records)


def ensure_loaded() -> None:
    """Idempotent: make sure the in-memory index exists. Seeds instantly, then
    loads the DB, then (if stale) kicks a background machine-source refresh."""
    if _READY.is_set():
        return
    with _LOAD_LOCK:
        if _READY.is_set():
            return
        # 1) Seed immediately so search works within microseconds of first call.
        _rebuild_index_from([])
        # 2) Overlay whatever the DB already has (previous machine refresh).
        try:
            n = _load_from_db()
        except Exception:
            n = 0
        _LAST_BUILD["count"] = len(_INDEX)
    # 3) Background refresh if the DB is empty or older than the cadence.
    if _should_refresh():
        refresh_async()


def _should_refresh() -> bool:
    try:
        conn = _connect()
        _ensure_schema(conn)
        last = _meta_get(conn, "last_full_refresh")
        conn.close()
    except Exception:
        return False
    if not last:
        return True
    try:
        return (time.time() - float(last)) > _REFRESH_SECONDS
    except Exception:
        return True


# ── Machine sources (network — only ever run in the background) ───────────────

def _http_json(url: str, timeout: float = 20.0, headers: Optional[dict] = None) -> Any:
    hdr = {"User-Agent": "trading-terminal security-master (contact: local)"}
    if headers:
        hdr.update(headers)
    try:
        import requests
        r = requests.get(url, timeout=timeout, headers=hdr)
        r.raise_for_status()
        return r.json()
    except Exception:
        import urllib.request
        req = urllib.request.Request(url, headers=hdr)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)


def _fetch_sec() -> List[Dict[str, Any]]:
    """SEC company_tickers.json -> records with name + CIK (no key needed)."""
    data = None
    # Prefer a local cache written by an earlier run to avoid hammering SEC.
    if os.path.exists(_SEC_CACHE) and (time.time() - os.path.getmtime(_SEC_CACHE)) < _REFRESH_SECONDS:
        try:
            data = json.load(open(_SEC_CACHE, encoding="utf-8"))
        except Exception:
            data = None
    if data is None:
        data = _http_json("https://www.sec.gov/files/company_tickers.json", timeout=25.0)
        try:
            json.dump(data, open(_SEC_CACHE, "w", encoding="utf-8"))
        except Exception:
            pass
    out = []
    values = data.values() if isinstance(data, dict) else (data or [])
    for row in values:
        try:
            sym = _norm(row.get("ticker", ""))
            name = (row.get("title") or "").strip().title()
            cik = str(row.get("cik_str", "")).zfill(10)
            if sym and name:
                out.append(_mk_record(sym, name, cik=cik, typ="Common Stock",
                                      source="sec"))
        except Exception:
            continue
    return out


def _fetch_finnhub() -> List[Dict[str, Any]]:
    """Finnhub US symbol list -> adds ETFs/ADRs, instrument type + currency."""
    try:
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lab"))
        from _config import FINNHUB_KEY
    except Exception:
        FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")
    if not FINNHUB_KEY:
        return []
    data = _http_json(f"https://finnhub.io/api/v1/stock/symbol?exchange=US&token={FINNHUB_KEY}",
                      timeout=30.0)
    out = []
    for row in (data or []):
        try:
            sym = _norm(row.get("symbol", ""))
            name = (row.get("description") or "").strip().title()
            typ = (row.get("type") or "").strip()
            cur = (row.get("currency") or "USD").strip()
            mic = (row.get("mic") or "").strip()
            if sym and name and "." not in sym[1:]:  # skip odd multi-dot venue rows
                out.append(_mk_record(sym, name, exchange=mic, typ=typ or "Common Stock",
                                      currency=cur, country="US", source="finnhub"))
        except Exception:
            continue
    return out


def _do_refresh() -> Dict[str, Any]:
    sources, records_by_sym = [], {}

    def _merge(recs: List[Dict[str, Any]], label: str) -> None:
        if not recs:
            return
        sources.append(f"{label}:{len(recs)}")
        for r in recs:
            cur = records_by_sym.get(r["symbol"])
            if cur is None:
                records_by_sym[r["symbol"]] = r
            else:
                # Enrich: fill blanks, prefer a real instrument type + currency.
                if not cur.get("name") and r.get("name"):
                    cur["name"] = r["name"]
                if not cur.get("cik") and r.get("cik"):
                    cur["cik"] = r["cik"]
                if r.get("type") and r["type"].lower() != "common stock":
                    cur["type"] = r["type"]
                if r.get("exchange") and not cur.get("exchange"):
                    cur["exchange"] = r["exchange"]
                if r.get("currency") and r["currency"] != "USD":
                    cur["currency"] = r["currency"]
    try:
        _merge(_fetch_sec(), "sec")
    except Exception as e:  # noqa: BLE001
        sources.append(f"sec:err:{str(e)[:40]}")
    try:
        _merge(_fetch_finnhub(), "finnhub")
    except Exception as e:  # noqa: BLE001
        sources.append(f"finnhub:err:{str(e)[:40]}")

    records = list(records_by_sym.values())
    if records:
        try:
            conn = _connect()
            _ensure_schema(conn)
            for r in records:
                r["aliases_raw"] = r.get("aliases_raw") or []
            _upsert_many(conn, records)
            _meta_set(conn, "last_full_refresh", str(time.time()))
            conn.close()
        except Exception as e:  # noqa: BLE001
            sources.append(f"db:err:{str(e)[:40]}")
        # Reload the merged view (DB + seed) into memory.
        _load_from_db()
    _LAST_BUILD.update(at=time.time(), sources=sources, count=len(_INDEX))
    return {"count": len(_INDEX), "sources": sources}


def refresh_async() -> None:
    if _REFRESHING["on"]:
        return
    _REFRESHING["on"] = True

    def _run():
        try:
            _do_refresh()
        finally:
            _REFRESHING["on"] = False
    threading.Thread(target=_run, name="secmaster-refresh", daemon=True).start()


def refresh_sync() -> Dict[str, Any]:
    """Blocking rebuild — for the scheduler / CLI, never a request path."""
    ensure_loaded()
    _REFRESHING["on"] = True
    try:
        return _do_refresh()
    finally:
        _REFRESHING["on"] = False


# ── Fuzzy matching (bounded Levenshtein, stdlib only) ─────────────────────────

def _lev(a: str, b: str, maxd: int = 2) -> int:
    """Levenshtein distance with an early exit once it exceeds maxd."""
    la, lb = len(a), len(b)
    if abs(la - lb) > maxd:
        return maxd + 1
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        best = cur[0]
        ca = a[i - 1]
        for j in range(1, lb + 1):
            cost = 0 if ca == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if cur[j] < best:
                best = cur[j]
        if best > maxd:
            return maxd + 1
        prev = cur
    return prev[lb]


# ── Ranked search ────────────────────────────────────────────────────────────

def _score(rec: Dict[str, Any], q: str, q_tokens: List[str], symbolish: bool) -> Tuple[float, str]:
    """Return (score, match_reason). Higher is better; 0 means no match."""
    sym_l = rec["_sym_l"]
    name_l = rec["_name_l"]
    aliases = rec["_aliases"]
    pop = rec.get("popularity", 0)
    type_boost = _TYPE_RANK.get((rec.get("type") or "").lower(), 6)
    base = pop * 1.0 + type_boost

    # Symbol matches (strongest signal when the query looks like a ticker).
    if sym_l == q:
        return 1000 + base, "exact symbol"
    if aliases and q in aliases:
        return 940 + base, "alias"
    if sym_l.startswith(q):
        return 780 + base - (len(sym_l) - len(q)) * 4, "symbol prefix"

    # Name matches.
    if name_l == q:
        return 720 + base, "exact name"
    if name_l.startswith(q):
        return 640 + base, "name prefix"
    # Token prefix: every query token is a prefix of some name token, in order-ish.
    toks = rec["_tokens"]
    if q_tokens and all(any(t.startswith(qt) for t in toks) for qt in q_tokens):
        # first token match earns more
        lead = 1 if (toks and toks[0].startswith(q_tokens[0])) else 0
        return 560 + base + lead * 30, "name tokens"
    # Alias prefix.
    for a in aliases:
        if a.startswith(q):
            return 600 + base, "alias prefix"
    # Substring in name or alias.
    if q in name_l:
        return 380 + base - name_l.index(q), "name contains"
    for a in aliases:
        if q in a:
            return 360 + base, "alias contains"

    # Typo tolerance (only worth it for symbol-ish queries of length >= 3).
    if symbolish and 3 <= len(q) <= 6:
        d = _lev(q, sym_l, 2)
        if d <= 2:
            return 300 + base - d * 60, f"symbol ~{d}"
    # Fuzzy on the leading name token for short name queries.
    if not symbolish and q_tokens and toks:
        d = _lev(q_tokens[0], toks[0], 2)
        if d <= 1 and len(q_tokens[0]) >= 4:
            return 260 + base - d * 60, f"name ~{d}"
    return 0, ""


def _gather(q: str, q_sym: str, q_tokens: List[str], symbolish: bool) -> List[Dict[str, Any]]:
    """Collect a NARROW candidate set from the secondary indexes so scoring never
    touches all ~30k rows. Returns a de-duplicated list of records."""
    cand: Dict[str, Dict[str, Any]] = {}

    def add(rec):
        if rec:
            cand[rec["symbol"]] = rec

    # 1) Exact symbol (both raw and canonicalised, e.g. "brk b" -> "BRK.B").
    for k in {q.upper(), q_sym.upper()}:
        add(_BY_SYMBOL.get(k))
    # 2) Symbol prefix via bisect (bounded).
    for key in {q, q_sym}:
        if key:
            lo, hi = _prefix_range(_SYM_SORTED, key)
            for rec in _SYM_SORTED_RECS[lo:min(hi, lo + 300)]:
                add(rec)
    # 3) Alias exact / prefix / substring (alias set is small — a full scan is fine).
    for a, rec in _ALIAS_ENTRIES:
        if a == q or a.startswith(q) or (len(q) >= 3 and q in a):
            add(rec)
    # 4) Name-token prefix for the FIRST query token (bisect); scoring verifies the rest.
    if q_tokens:
        lo, hi = _prefix_range(_TOK_KEYS, q_tokens[0])
        for _t, rec in _TOK_SORTED[lo:min(hi, lo + 600)]:
            add(rec)
    # 5) Typo tolerance — only over a small first-char / first-2 bucket.
    if symbolish and q:
        for rec in _FUZZ_SYM.get(q[0], []):
            if _lev(q, rec["_sym_l"], 2) <= 2:
                add(rec)
        if q_sym and q_sym[0] != q[0]:
            for rec in _FUZZ_SYM.get(q_sym[0], []):
                if _lev(q_sym, rec["_sym_l"], 2) <= 2:
                    add(rec)
    elif q_tokens and len(q_tokens[0]) >= 4:
        for t0, rec in _FUZZ_TOK.get(q_tokens[0][:2], []):
            if _lev(q_tokens[0], t0, 1) <= 1:
                add(rec)
    return list(cand.values())


def search(query: str, limit: int = 10) -> Dict[str, Any]:
    """Ranked local search over the security master. Never raises."""
    ensure_loaded()
    raw = (query or "").strip()
    if not raw:
        return {"query": query, "count": 0, "results": [], "state": "empty"}
    q = raw.lower().lstrip("$").strip()
    q_canon = canonical_query(raw)
    q_sym = q_canon.lower()
    q_tokens = [t for t in re.split(r"[^a-z0-9&]+", q) if t]
    # "Symbol-ish": short, few tokens, mostly alnum — decides whether to weight
    # symbol matching and typo tolerance.
    symbolish = len(q_tokens) <= 1 and len(q.replace(".", "").replace("-", "")) <= 6

    scored: List[Tuple[float, str, Dict[str, Any]]] = []
    for rec in _gather(q, q_sym, q_tokens, symbolish):
        s, why = _score(rec, q, q_tokens, symbolish)
        if q_sym != q:
            s2, why2 = _score(rec, q_sym, [q_sym], True)
            if s2 > s:
                s, why = s2, why2
        if s > 0:
            scored.append((s, why, rec))
    scored.sort(key=lambda x: (x[0], -len(x[2]["_sym_l"])), reverse=True)

    results, seen = [], set()
    for s, why, rec in scored:
        if rec["symbol"] in seen:
            continue
        seen.add(rec["symbol"])
        results.append(_public(rec, round(s, 1), why))
        if len(results) >= limit:
            break
    return {"query": query, "canonical": q_canon, "count": len(results),
            "results": results, "state": "ok" if results else "no_match",
            "index_size": len(_INDEX)}


def _public(rec: Dict[str, Any], score: Optional[float] = None, why: str = "") -> Dict[str, Any]:
    exch = rec.get("exchange") or ""
    out = {
        "symbol": rec["symbol"], "name": rec["name"],
        "exchange": _MIC_NAME.get(exch.upper(), exch) or None,
        "mic": exch or None,
        "type": rec.get("type") or None,
        "currency": rec.get("currency") or None,
        "country": rec.get("country") or None,
        "cik": rec.get("cik") or None,
        "yahoo": to_yahoo(rec["symbol"]),
    }
    if score is not None:
        out["score"] = score
        out["match"] = why
    return out


def lookup(symbol: str) -> Optional[Dict[str, Any]]:
    """Exact canonical-symbol lookup (returns the master record or None)."""
    ensure_loaded()
    rec = _BY_SYMBOL.get(canonical_query(symbol))
    if rec is None:
        rec = _BY_SYMBOL.get(_norm(symbol))
    return _public(rec) if rec else None


def popular(limit: int = 12) -> List[Dict[str, Any]]:
    ensure_loaded()
    out = []
    for sym in POPULAR[:limit]:
        rec = _BY_SYMBOL.get(sym)
        if rec:
            out.append(_public(rec))
    return out


def stats() -> Dict[str, Any]:
    ensure_loaded()
    by_type: Dict[str, int] = {}
    for r in _INDEX:
        t = r.get("type") or "unknown"
        by_type[t] = by_type.get(t, 0) + 1
    return {"index_size": len(_INDEX), "by_type": by_type,
            "refreshing": _REFRESHING["on"], "last_build": _LAST_BUILD,
            "db_path": _DB_PATH}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "refresh":
        print("Refreshing security master (SEC + Finnhub)…")
        print(json.dumps(refresh_sync(), indent=2))
    else:
        ensure_loaded()
        for q in (sys.argv[1:] or ["Apple", "Google", "Nokia", "BRK B", "NOKIA.HE", "spy", "microsft"]):
            r = search(q, 5)
            print(f"\n{q!r} -> {r['count']} hits (index {r['index_size']})")
            for x in r["results"]:
                print(f"   {x['symbol']:<12} {x['name'][:40]:<40} {x['type'] or '':<14} {x['exchange'] or '':<8} score={x.get('score')} [{x.get('match')}]")
