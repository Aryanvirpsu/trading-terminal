# DATA_PROVIDER_MATRIX.md

Which provider serves each data category, the fallback chain, keys, caching, and
the failure state shown when everything is exhausted. All providers are free-tier
or keyless. Every call is time-boxed, health-classified (`research._guard`), and
protected by a per-provider circuit breaker + stale-while-revalidate cache.

Legend — **State on total failure**: the typed state the endpoint returns instead
of a fabricated value (`ok|loading|empty|throttled|no_price|missing_key|unsupported|error|missing`).

---

## TradingView is DEMOTED (Prompt 5B)

TradingView is **no longer a primary data source**. It is an **optional
technical-signal confirmation** only, gated by `TRADINGVIEW_ENABLED` (default on,
but the terminal is fully functional with it **off**). The decision engine's base
technicals now lead with **Yahoo/yfinance** (`decision_engine._load_analysis`):

```
PRIMARY   Yahoo/yfinance technicals  -> data_state "fresh"
SECONDARY TradingView (only if enabled AND Yahoo down) -> "secondary-provider" (mild penalty, not blocking)
          otherwise                  -> "unavailable"
```

A TradingView outage therefore **cannot** lower a stock's data quality when Yahoo/
Finnhub have valid data. The authoritative category spec lives in
`lab/providers.py` (`CATEGORIES`) and is the source of this table:

| Category | Primary | Secondary | TV confirm | Timeout(s) | TTL(s) | Freshness limit | Required fields | Conf weight |
|---|---|---|---|---|---|---|---|---|
| price | finnhub | yahoo | optional | 6 | 8 | 900s | price | 1.0 |
| candles | yahoo | finnhub | optional | 8 | 300 | 6h | closes, highs, lows | 1.0 |
| fundamentals | finnhub | alphavantage | no | 8 | 6h | 3d | market_cap | 0.7 |
| options | yahoo | — | no | 8 | 60 | 1800s | bid, ask, open_interest | 0.6 |
| news | google-news | finnhub | no | 8 | 180 | 6h | headlines | 0.6 |
| analyst | finnhub | — | no | 8 | 6h | 7d | recommendation | 0.5 |
| filings | edgar | — | no | 8 | 7d | 30d | cik | 0.4 |
| macro | fred | — | no | 8 | 6h | 2d | series | 0.4 |
| sector | security-master | finnhub | no | 4 | 24h | 30d | sector | 0.4 |
| social | stocktwits | — | no | 6 | 180 | 6h | messages | 0.15 |

**Freshness limit** = the max SOURCE age at which a value may still drive a NEW
trade decision. Past it, the value is `display-only` (renders, but does not create
an actionable call) unless `DECISION_ALLOW_STALE=true`.

## Per-field consensus (`providers.consensus`)

Every price field can be resolved across providers into ONE record:
`{value, provider, source_timestamp, freshness, confidence, agreeing_providers,
conflicting_providers, state}`. Rules: prefer the **freshest valid** source; on
disagreement apply a **confidence penalty** and surface the split; never silently
overwrite; a failed provider is recorded but ignored for the value. `price_consensus`
queries Finnhub + Yahoo (+ optional TV confirm); a failed TV confirm does not lower
confidence when a primary is valid.

## Cache discipline (`lab/cache_policy.py`)

Three tiers, keyed off the **source timestamp** (not cache-insertion time):
`valid-decision` (within freshness limit) · `display-only` (stale — renders but
can't drive a decision) · `unknown`. Provenance of every served value is logged
(`/api/providers`, `/api/health`).

---

## Provider matrix

| Data category | Primary | Fallback(s) | Key | Cache TTL (SWR) | State on failure |
|---|---|---|---|---|---|
| **Ticker universe / search** | Local security master (SQLite, in-memory) | Finnhub `/stock/symbol` rebuild; curated seed always present | Finnhub (build only) | index in-mem; DB refresh 7d | `no_match` |
| **Symbol resolve (identity)** | Security master | Finnhub profile → Yahoo → TradingView | — | resolve 24h (valid) / 120s (invalid) | `unsupported` / `no_price` |
| **Live quote / price** | Finnhub `quote` | Yahoo `get_price` → TradingView screener | Finnhub | quote 4s | `throttled` / `unsupported` |
| **Foreign-listing price** | Yahoo (suffix preserved, e.g. `.HE`,`.L`,`.NS`) | TradingView | — | quote 4s | `no_price` |
| **Price history / charts** | Yahoo `yfinance` daily | — | — | chart 300s | `empty` |
| **Technicals / indicators** | TradingView screener | `fallback_ta` (yfinance) — same schema | — | technicals 20s / ta 300s | `throttled` (degraded flag when on fallback) |
| **Fundamentals / metrics** | Finnhub `stock/metric` + `profile2` | — | Finnhub | fundamentals 6h | `error` / `missing_key` |
| **Options chain** | Yahoo options service | — | — | options 20–120s | `unsupported` / `empty` |
| **Company news** | Google News RSS | Finnhub `company-news` | Finnhub (2nd) | news/catalysts 180s–30m | `empty` / `throttled` |
| **Earnings calendar** | Finnhub `calendar/earnings` | — | Finnhub | earnings 30m | `empty` |
| **News sentiment (aggregate)** | Model registry (lexical → FinBERT) over Google+Finnhub headlines | keyword lean | optional HF | sentiment 180s | `missing` (never fake-neutral) |
| **Social sentiment** | StockTwits | — | — | sentiment 180s | `empty` / `thin` |
| **Analyst signal** | Finnhub `recommendation` | — | Finnhub | sentiment 180s | `empty` |
| **Sector proxy** | Sector-ETF technicals (XLK/XLF/…) | — | — | technicals 20s | `unknown` / `throttled` |
| **Market regime** | `strategy_service.market_regime` (indices/VIX/breadth) | — | — | regime 30s | `error` |
| **Macro sensitivity** | FRED series | — | FRED | fundamentals 6h | `error` / `missing_key` |
| **Comparison (2–8)** | Composes history + fundamentals + technicals concurrently | per-symbol independent degrade | as above | chart 300s (whole result) | per-symbol `no_price` + `data unavailable` |
| **Cash account (read-only)** | SnapTrade | — | SnapTrade | 30s | `configuration_required` |
| **CIK / filings id** | SEC EDGAR (`company_tickers.json`) | cached `sec_cik_map.json` | — (UA required) | 7d | absent (optional enrichment) |

---

## Cross-provider symbol mapping

The same security has different symbols per provider; the master normalises them:

| Concept | Canonical (master) | Yahoo | Finnhub |
|---|---|---|---|
| Share class | `BRK.B` | `BRK-B` | `BRK.B` |
| Foreign listing | `NOKIA.HE` | `NOKIA.HE` (suffix kept) | n/a (US only) |
| Index | `SPX` | `^GSPC` | n/a |
| Plain US | `AAPL` | `AAPL` | `AAPL` |

`research.to_yahoo()` / `research.to_finnhub()` delegate to
`security_master.to_yahoo()`, which distinguishes a US share-class dot from a
foreign-exchange suffix (the old blanket `.`→`-` broke every foreign listing).

---

## Reliability mechanics (shared by all categories)

* **Timeout + retry-free time-boxing** (`research._box`) — one slow provider can't
  freeze an endpoint; the abandoned worker is left running, the request moves on.
* **Circuit breaker** (`_trip`/`_tripped`) — a throttled provider is skipped for a
  cooldown instead of paying its full timeout on every ticker.
* **Stale-while-revalidate** (`swr`/`swr_async`) — warm reads are instant; a stale
  value is served while a background refresh runs; cold misses on expensive paths
  return a `loading` placeholder and never block.
* **In-flight coalescing** — N identical concurrent requests collapse to one call.
* **Health classification** (`_guard`) — every call is tagged
  `ok|throttled|missing_key|error`; surfaced at `/api/health`.
* **News de-duplication** — canonical title + title-Jaccard similarity; the most
  reliable source wins.
* **No silent placeholders** — a category that can't be served returns a typed
  state the UI renders as a labelled empty/loading/error card, never a fake number.

---

## Keys (all optional, all free-tier)

| Env var | Enables | Without it |
|---|---|---|
| `FINNHUB_API_KEY` | quotes, fundamentals, analyst, company news, earnings, **universe build** | falls back to Yahoo/TradingView; seed-only universe |
| `FRED_API_KEY` | macro-sensitivity signal | macro state = `missing_key` |
| `ALPHAVANTAGE_API_KEY` | supplementary fundamentals | Finnhub-only fundamentals |
| `SNAPTRADE_CLIENT_ID` / `SNAPTRADE_CONSUMER_KEY` | read-only Cash account panel | panel shows `not connected` |
| `SENTIMENT_MODEL_ENABLED` (+`transformers`,`torch`) | FinBERT sentiment | lexical sentiment (default) |
| *(none)* | Yahoo, TradingView, StockTwits, Google News, SEC EDGAR | always available |

Secrets are read from `.env` server-side only — never logged, never sent to the
frontend, never placed in URLs.
