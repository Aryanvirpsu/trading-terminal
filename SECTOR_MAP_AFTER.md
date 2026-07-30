# SECTOR_MAP_AFTER.md

A new **Sector Map** page: a market-cap or equal-weighted treemap/heatmap of the 11
GICS sectors with industry drill-down, built entirely on Yahoo + the local security
master. **It does not use TradingView at all** (`tradingview_used: false` is asserted
in the payload and in tests).

---

## Design decision: ETF proxies, not a mass scan

The obvious implementation — aggregate thousands of constituents per sector — costs
thousands of provider calls per refresh and would re-introduce exactly the throttle
dependence this prompt is removing.

Instead each sector is represented by its **SPDR sector ETF**, whose own price series
*is* the authoritative cap-weighted sector return, plus a curated set of large, liquid
constituents used only for **breadth**, **advancers/decliners** and **top/bottom
movers**. SPY is the relative-strength benchmark.

Cost per full map refresh: **12 daily-history calls** (11 sector ETFs + SPY) and one
cached quote per constituent — all Yahoo, all SWR-cached, all concurrent.

The local security master supplies identity/search; the taxonomy (sector → industry
groups → constituents) lives in `dashboard/sector_map.py::SECTORS` so it is explicit
and auditable rather than guessed from a free-text industry string.

---

## Backend — `dashboard/sector_map.py`

| Function | Returns |
|---|---|
| `sector_map(weighting)` | all 11 tiles + SPY benchmark + worst-of freshness, SWR-cached (`swr_async`, TTL 300 s) |
| `sector_tile(key, benchmark_1m)` | one tile |
| `sector_detail(key)` | the drill-down |

**Metrics per tile** — computed from one cached 3-month daily history:
performance `1D / 5D / 1M`, **relative volume** (latest vs 20-day average),
**momentum** (% above the 50-DMA), **annualised volatility** (30-day stdev),
**relative strength vs SPY** (1M excess), **breadth** (advancers/decliners across
constituents), **opportunity count** (constituents up > 0.5 % while the ETF holds its
50-DMA — an honest cheap proxy, not a full scan), **top/bottom movers**, and a
**freshness** record from the shared classifier.

Endpoints: `GET /api/sectors?weighting=cap|equal` · `GET /api/sector/<key>` ·
`GET /api/providers` (matrix + provenance).

### Progressive loading

`sector_map` uses `swr_async`, so a cold call returns
`{"state": "loading"}` immediately rather than blocking; the frontend renders a
loading card and re-polls after 1.6 s. Warm calls are instant and a stale map is
served while it refreshes in the background.

### Freshness

Sector tiles are built from **daily bars**, so they use daily-bar thresholds through
the same shared classifier (`fresh < 30 h`, `ageing < 4 d`, `stale < 8 d`) — an
overnight gap must not read as "stale". The map header shows the **worst-of** tile
freshness so one lagging sector can't hide behind ten fresh ones.

### Degradation

A sector whose ETF history is unavailable renders with its typed state
(`throttled` / `empty`) and `perf_1d: null` — never a fabricated 0 %. Verified by
`test_sector_tile_degrades_when_etf_down`. An unknown sector key returns
`state: "unsupported"` (`test_sector_unknown_key`).

---

## Frontend — `dashboard/terminal.html`

New **Sectors** rail entry and `#view-sectors`, rendered by `renderSectors()`.

* **Treemap** — tile width is a 12-column span derived from the weighting
  (`cap` uses the sector's market-cap weight, `equal` uses 1/n), so Technology
  occupies visibly more area than Utilities under cap weighting.
* **Heat colour** — `heatColor()` maps the selected metric to a green/red alpha ramp
  clamped at ±3 %, so one outlier can't wash out the map.
* **Colour-by selector** — `1D · 5D · 1M · Mom · RelVol`, re-paints without refetching.
* **Per tile** — sector name, ETF symbol, the selected metric large, `1D / 1M / RS`,
  advancers ▲ / decliners ▼, an opportunity badge, a freshness badge, and the top/
  bottom movers.
* **Drill-down** — clicking a tile opens the detail panel inline: ETF proxy stats
  (price, 1D/5D/1M, momentum, volatility, relative volume, RS vs SPY), a
  trend badge (`UP/DOWN/SIDEWAYS`), **industry groups** with per-group performance and
  colour-coded members, **ranked stocks**, **sector catalysts** (news + sentiment lean
  via the existing FinBERT/lexical pipeline), and **options availability**. Every
  symbol is clickable through to the stock workspace.

### Rendered verification

The user's dev server holds the previous backend in memory, so the two views were
verified by injecting representative payloads into the live page and reading back the
rendered DOM.

Heatmap (6 tiles incl. one throttled):

```
Technology XLK +1.80%  1D +1.80% · 1M +5.50% · RS +3.5  ▲8 ▼2  4 opp  fresh  ▲ AAA BBB · ▼ YYY ZZZ
Financials XLF +0.60%  1D +0.60% · 1M +2.10% · RS +0.1  ▲6 ▼3  2 opp  fresh
Utilities  XLU +0.20%  1D +0.20% · 1M -0.50% · RS -2.5  ▲2 ▼3         fresh
Health Care XLV -0.40% 1D -0.40% · 1M -1.20% · RS -3.2  ▲4 ▼5         fresh
Energy     XLE -1.90%  1D -1.90% · 1M +3.00% · RS +1    ▲3 ▼5  1 opp  fresh
Real Estate XLRE  throttled            <- typed state, no fake number
```

Drill-down:

```
TECHNOLOGY  XLK proxy  UP  fresh
Price $245.10 · 1D +1.80% · 5D +2.70% · 1M +5.50% · Momentum +4.40%
Volatility 18.5% · Rel volume 1.2× · RS vs SPY (1M) +3.5
INDUSTRY GROUPS
  Semiconductors +2.40%   NVDA +3.10%   AVGO +1.90%
  Software       +1.10%   MSFT +1.40%   ORCL +0.60%
RANKED STOCKS  1. NVDA +3.10%  2. AVGO +1.90%  3. MSFT +1.40%
SECTOR CATALYSTS BULLISH — "Chip demand accelerates into Q3"
Options: NVDA, MSFT, AAPL, AVGO
```

### Live backend check (TradingView disabled)

```
TILE technology: Technology XLK state=ok perf_1d=-1.84 perf_1m=-5.53 rs=-7.53
  breadth={'advancers': 7, 'decliners': 3, 'counted': 10}  top=['ADBE','CRM','MSFT']
DETAIL energy: state=ok trend=up rs=+5.30
  groups=[('Integrated Oil & Gas', -1.20), ('E&P', -1.19), ('Equipment & Services', -2.90)]
  tv_used=False
```

---

## Coverage of the brief

| Requirement | Status |
|---|---|
| Treemap/heatmap from security master + market data | tiles sized by weight, coloured by metric |
| Sector / industry | 11 sectors, 2–3 industry groups each, in the drill-down |
| Market-cap **and** equal weighting | `?weighting=cap\|equal`, toggle in the header |
| 1D / 5D / 1M performance | per tile and in the detail panel |
| Relative volume · momentum · volatility | computed from the ETF history |
| News sentiment | sector catalysts + lean in the drill-down |
| Scanner opportunity count | `opportunity_count` badge per tile |
| Tile: name, performance, breadth, adv/decl, freshness, top & bottom | all present |
| Click → ETF proxy, industry groups, ranked stocks, trend, RS vs SPY, catalysts, options | all present |
| No TradingView required | `tradingview_used: false`; verified with `TRADINGVIEW_ENABLED=false` |
| Loads progressively, shows freshness | `swr_async` loading state + re-poll; worst-of freshness badge |

## Tests

`test_sector_map_shape` (all 11 tiles, required keys, RS computed, `tradingview_used`
false), `test_sector_tile_degrades_when_etf_down` (typed state, no fabricated number),
`test_sector_unknown_key` (missing sector classification → `unsupported`).

## Known limits

* The constituent lists are curated large-caps, so breadth is a **sample** (~8–10
  names per sector), not the full sector membership. It is labelled `counted` in the
  payload so the UI never implies full coverage.
* `opportunity_count` is a momentum proxy, not a run of the real scanner — wiring the
  scanner per sector would re-introduce the cost this design avoids.
* Sector/industry classification is the curated taxonomy in `SECTORS`; the security
  master has no sector column (see `DATA_PROVIDER_MATRIX.md`, `sector` category:
  primary `security-master`, secondary `finnhub`).
