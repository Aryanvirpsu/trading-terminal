# DATA_QUALITY_AFTER.md

Prompt 5B: TradingView is no longer a primary data source, data quality is measured
by **category coverage** instead of mean family confidence, and stale data can render
a page but can no longer create an actionable decision.

---

## 1. What was wrong

`decision_engine._load_analysis` called **TradingView first** and treated its own
free-Yahoo path as a *fallback*:

```python
# BEFORE
cand = ss._analyze_cached(symbol, exch, "1D")   # TradingView PRIMARY
if ok: return cand, f"tradingview:{exch}", "fresh"
fb = fallback_ta.analysis(symbol)               # Yahoo demoted to "fallback"
if ok: return fb, "yfinance-fallback", "fallback-provider"
```

Two consequences, both wrong:

1. **A TradingView throttle degraded every stock.** Yahoo data — which is complete,
   free and reliable — was labelled `fallback-provider`, which the shared freshness
   classifier maps to `fallback`, which **blocks TRADEABLE**. Perfect data was being
   treated as degraded because a *different* provider was busy.
2. **Data quality was the mean confidence of the 9 evidence families.** A couple of
   thin families (or one throttled provider) dragged the whole number under the 0.55
   floor even when price, candles, news, analyst and filings were all present. This is
   the "false low-quality score" the brief calls out.

Measured on AAPL at the start of this prompt:

```
decision: MONITOR
data_state: fallback-provider | freshness: fallback provider
failed_gates: ['data_quality', 'freshness']
data_quality gate: FAILED  (mean family confidence 0.54 < 0.55)
```

Neither failure was about AAPL. Both were artefacts of the provider layering.

---

## 2. What changed

### Provider abstraction (`lab/providers.py`)

One normalized spec per data category — primary, secondary, *optional* TradingView
confirmation, timeout, TTL, freshness limit, required fields, confidence weight (full
table in `DATA_PROVIDER_MATRIX.md`). **TradingView never appears as a primary or a
secondary** — only as `tv_confirm`, gated by `TRADINGVIEW_ENABLED`.

### Inverted technicals chain

```python
# AFTER
fb = fallback_ta.analysis(symbol)               # Yahoo PRIMARY
if ok: return fb, "yahoo", "fresh"              # <- genuinely fresh
if providers.tv_enabled():                      # TradingView OPTIONAL secondary
    cand = ss._analyze_cached(symbol, exch, "1D")
    if ok: return cand, f"tradingview:{exch}", "secondary-provider"   # ageing, not blocking
return None, "unavailable", "unavailable"
```

`secondary-provider` maps to `ageing` (a mild confidence penalty, **not** blocking);
`unavailable` maps to `unknown` — never to `fresh`.

### Per-field consensus (`providers.consensus` / `price_consensus`)

Every field resolves to
`{value, provider, source_timestamp, freshness, confidence, agreeing_providers,
conflicting_providers, state}`. The **freshest valid** source wins; disagreement
applies a confidence penalty and is surfaced (`state: "conflict"`); a losing provider
is reported, never silently overwritten; a failed provider is recorded but ignored for
the value.

| Case | Value | Confidence | State |
|---|---|---|---|
| Finnhub 100.0 (30 s) + Yahoo 100.3 (5 s) | 100.3 (yahoo) | **1.00** | ok, agreeing `[finnhub]` |
| Finnhub 100.0 + Yahoo 112.0 | 112.0 (yahoo) | **0.64** | **conflict**, conflicting `[finnhub]` |
| TradingView 429 + Yahoo 100.0 | 100.0 (yahoo) | **1.00** | ok — a failed TV confirm costs nothing |
| Finnhub no-key + Yahoo timeout | `None` | 0.00 | `unavailable` + `tried` list |
| Yahoo 100.0, source 2000 s old (limit 900 s) | 100.0 | **0.43** | past freshness limit |

### Coverage-based data quality (`lab/data_quality.py`)

Quality is now weighted **category coverage**, and which categories matter depends on
the **strategy profile**:

| Profile | Required | Optional | Ignored |
|---|---|---|---|
| `momentum` | price, candles | fundamentals, news, analyst, filings, sector, macro, **options** | social |
| `analysis` | price, candles, **fundamentals** | news, analyst, filings, sector, macro, options | social |
| `options` | price, candles, **options** | fundamentals, news, analyst, sector | filings, macro, social |

A missing **optional** category costs a little; a missing **required** category is a
blocking gap that caps the headline score at 0.5 and fails the gate. Per-category
coverage and the **exact missing fields** are always returned. A category sourced from
stale data counts as *degraded* coverage (×0.5), so stale data can't inflate quality.

The `data_quality` gate is deliberately **no longer** based on mean family confidence
— thin conviction is what the separate `conviction` gate is for.

### Cache discipline (`lab/cache_policy.py`)

Three tiers keyed off the **source timestamp**, not cache-insertion time:

| Tier | Meaning | May drive a NEW decision? |
|---|---|---|
| `valid-decision` | source age ≤ category freshness limit | **yes** |
| `display-only` | source age past the limit | **no** (renders only) |
| `unknown` | no source timestamp | only if `DECISION_ALLOW_STALE=true` |

A value re-inserted into the cache "now" whose *source* is old is still
`display-only` — verified by `test_cache_policy_uses_source_not_cache_time`. Every
served value is provenance-logged and exposed at `/api/providers` + `/api/health`.
SWR, request coalescing, circuit breakers, negative caching and provider health were
already in `research.py` and are unchanged.

### Options quality is separate (`_grade_option_chain`)

The engine now returns `stock_setup_quality` and an independent `option_quality`:

```json
{"chain_quality": 78.4, "best_option_quality": 78.4,
 "preference": "prefer-option", "gradeable": true, "missing": [], "stock_ok": true}
```

`preference ∈ prefer-stock | prefer-option | avoid-both | stock-only`. An option is
**only scored** when bid, ask, spread, OI, volume and DTE are all present; otherwise
`gradeable: false` with the exact missing fields, and the stock decision stands on its
own. **A weak or absent option can never reject the stock.**

---

## 3. Before / after — same symbol, same moment

### AAPL (live, TradingView throttled)

| | Before | After |
|---|---|---|
| `data_state` | `fallback-provider` | **`fresh`** |
| `data_source` | `yfinance-fallback` | **`yahoo`** (primary) |
| `freshness` | `fallback` (blocks TRADEABLE) | **`fresh`** |
| data quality | mean family conf **0.54** | **coverage 73.5 %** |
| `data_quality` gate | **FAILED** | **PASSED** |
| `freshness` gate | **FAILED** | **PASSED** |
| failed gates | `['data_quality', 'freshness']` | none from the data layer |

Per-category coverage now shown to the user, with the exact gaps:

```
price 0.95 · candles 0.925 · news 0.85 · sector 0.80 · filings 0.80
analyst 0.775 · macro 0.75 · fundamentals 0.00 · options 0.00
missing_fields: {"fundamentals": ["market_cap"],
                 "options": ["bid", "ask", "open_interest"]}
```

Both gaps are **optional** for the momentum profile, so they cost a little coverage
and are reported honestly — they do not fabricate a failure.

### Live AAPL with the flag flipped

```
TV=true   decision=REJECT  data_state=fresh  source=yahoo  coverage=73.5%  dq_gate=True
TV=false  decision=REJECT  data_state=fresh  source=yahoo  coverage=73.5%  dq_gate=True
```

**Byte-identical with TradingView enabled and disabled.** The REJECT is on genuine
merit (quality 29.0 < the 45 threshold — weak signals that day), not on data
availability.

---

## 4. Provider-outage matrix

Same symbol, same signals, only the provider availability varies (portfolio risk gate
neutralised so the data layer is what's under test):

| Scenario | Decision | `data_state` | Source | Freshness | Coverage | Sufficient | dq gate | Failed gates |
|---|---|---|---|---|---|---|---|---|
| All providers healthy | **TRADEABLE** | fresh | yahoo | fresh | 74.8 % | yes | pass | — |
| **TradingView offline (throttled)** | **TRADEABLE** | fresh | yahoo | fresh | 74.8 % | yes | pass | — |
| **TradingView disabled (flag off)** | **TRADEABLE** | fresh | yahoo | fresh | 74.8 % | yes | pass | — |
| Yahoo unavailable → TV secondary | **TRADEABLE** | secondary-provider | tradingview | ageing | 74.8 % | yes | pass | — |
| Yahoo **and** TradingView down | REJECT | unavailable | unavailable | unknown | 0 % | no | — | `symbol_resolution` |
| Missing social data | **TRADEABLE** | fresh | yahoo | fresh | 74.8 % | yes | pass | — |
| Missing options (**options** profile) | MONITOR | fresh | yahoo | fresh | 50 % | no | fail | `data_quality` |

Reading the table against the acceptance targets:

* Rows 2–3: a TradingView outage — throttled *or* switched off entirely — produces
  **exactly the healthy result**. TradingView contributes at most one optional
  confirmation.
* Row 4: losing the *primary* degrades gracefully to the optional secondary with a
  mild `ageing` penalty rather than a hard failure.
* Row 5: only when **every** provider is down does the symbol reject — and it does so
  through the `symbol_resolution` gate with a real reason, not a silent blank.
* Row 6: missing StockTwits is nearly free (coverage unchanged at 74.8 %).
* Row 7: no option chain fails only the **options** strategy, and only to **MONITOR**
  — the stock is *not* rejected for having weak options.

---

## 5. Tests

`tests/unit/test_providers.py` — **27 tests**, fully offline:

| Required scenario | Test |
|---|---|
| TradingView completely offline / disabled | `test_tv_flag_default_and_off`, `test_evaluate_works_with_tv_disabled`, `test_load_analysis_leads_with_yahoo` |
| Yahoo unavailable | `test_price_consensus_yahoo_down`, `test_load_analysis_tv_secondary_when_yahoo_down` |
| Conflicting prices | `test_consensus_conflict` |
| Stale cache | `test_cache_policy_stale_display_only`, `test_cache_policy_uses_source_not_cache_time`, `test_cache_policy_stale_override` |
| Missing options | `test_momentum_ok_without_options`, `test_options_profile_requires_option_chain`, `test_option_ungradeable_keeps_stock` |
| Missing social data | `test_missing_social_little_effect` |
| Multiple providers agreeing | `test_consensus_agree`, `test_tv_failure_does_not_lower_quality_when_others_valid` |
| Sector classification missing | `test_sector_unknown_key` |
| Sector-map rendering | `test_sector_map_shape`, `test_sector_tile_degrades_when_etf_down` |

Plus a bug found and fixed while building the outage matrix: the engine's early exits
(no data / no price / halted) returned a bare `{"decision": "REJECT"}` with **no
`decision_gates`**, violating the gate contract from the previous prompt. They now
return the single HARD gate that failed (`_early_reject`), and `unavailable` data is
classified `unknown` freshness rather than `fresh`.

---

## 6. Acceptance targets

| Target | Status | Evidence |
|---|---|---|
| Stock pages and scans work with TradingView disabled | met | live AAPL identical TV on/off; outage matrix rows 2–3 |
| TradingView contributes ≤ one optional signal family | met | never primary/secondary in `CATEGORIES`; `tv_confirm` only |
| Valid Yahoo/Finnhub/Robinhood data prevents false low-quality scores | met | AAPL dq gate FAILED → PASSED; coverage 73.5 % |
| Stale display data cannot create TRADEABLE | met | `display-only` tier; `decision_valid: false`; stale coverage ×0.5 |
| Stocks not rejected merely because options are weak | met | `stock-only` / `prefer-stock`; options profile → MONITOR not REJECT |
| Sector map loads progressively and shows freshness | met | see `SECTOR_MAP_AFTER.md` |
| Full existing test suite remains green | met | see the suite result at the end of this prompt |
