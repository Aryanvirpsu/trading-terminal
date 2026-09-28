# Dataset audit: `fabhaus/equities_5m_stockprices` (H4 primary bulk-data candidate)

**Verdict: does NOT yet clear the dataset-acceptance gate. One material, upstream data-labelling defect
was found and is fixable at the ingestion adapter (not a reason by itself to replace the dataset); a
second, systematic discrepancy (volume) and the documented lack of corporate-action adjustment both need
an explicit decision before any H4 execution uses this source.** Reproducible from the pinned revision
below; nothing in this audit relied on the Hub's dataset viewer or search (both are unreliable for this
dataset, per the user's note — every fact here comes from the raw shard files or the `datasets-server`
`/first-rows` endpoint, both queried directly).

* **Repository:** `fabhaus/equities_5m_stockprices`
* **Revision audited (pinned commit, never "main"):** `f17c0b0c3cf6a455994f93d6a85e76274172ab03`
* **Scripts/outputs:** `research/historical/audits/fabhaus_equities_5m_audit.py` (fetch), this report
  (findings), `fabhaus_sample/` (the tiny extracted sample + `fetch_manifest.json` with per-shard upstream
  hashes and the extracted sample's own SHA-256 — **not** committed in full if large; see note at the end).
* **Method:** direct HTTP Range GETs against `resolve/<revision>/<shard>.jsonl` (each shard is 7-23 GB;
  never downloaded in full), stream-filtered for 8 target symbols only. 4 bounded samples, ~2024-01
  (file start, ~260 MB — one intraday sequence), 2024-01 (file end, ~20 MB — a different, later day within
  the same month), 2025-01 (file start, ~20 MB), 2026-02 (file start, ~20 MB) — 3 distinct, non-consecutive
  calendar months plus a second day within the first month. Total transient download ≈ 320 MB out of a
  ~279 GB dataset (27 shards); **1,135 rows extracted and persisted**, nothing else.

## 1. Existence, schema, revision (PASS)

Confirmed via the Hub API (`GET /api/datasets/...`) and `datasets-server`'s `/first-rows` endpoint (the
Hub's own dataset **viewer**, not this project's tools, is what the user flagged as broken — `is-valid`
returns `"viewer": false, "filter": false`; `/first-rows` still works and matches what the raw shard bytes
actually contain, cross-checked directly). **53 columns**, exactly as documented: bar identifiers
(`symbol`, `datetime`, `date`, `unix_timestamp`), OHLCV (`open/high/low/close/volume/trade_count/vwap`),
10 `ti_*`, 10 `macro_*`×2 (value + observation date), 16 `cf_*`, 6 `cv_*`. All 8 target symbols
(AAPL/MSFT/META/NVDA/DELL/AMD/SPY/QQQ) are present in every one of the 4 sampled months.

**Per H1 v1 scope, only the raw market fields are ingested — never `ti_*`/`macro_*`/`cf_*`/`cv_*`:**
`symbol, datetime, date, unix_timestamp, open, high, low, close, volume, trade_count`. This is enforced at
the adapter (`HuggingFaceEquitiesAdapter.column_map`), not merely a convention.

Canonical-bar-schema validation (`schemas.bars.validate_bars`) on the full 1,135-row extracted sample:
**`{"ok": true, "failures": {}}`** — no OHLC-ordering violation, no non-positive price, no negative volume,
no duplicate (symbol, timestamp), monotonic per-symbol timestamps, every bar aligned to a `:00/:05/.../:55`
minute boundary.

## 2. Timestamp validation — **FAIL as documented; fixable at the adapter**

The README states: *"All timestamps are UTC"* and the `datetime` column is formatted
`YYYY-MM-DDTHH:MM:SSZ`. **This is not true of the values themselves.** Decisive evidence, from the raw
extracted rows (2024-01-02, SPY):

| Labelled time | Volume (5-min bar) |
|---|---|
| `09:00:00Z` (session "open") | 33,350 |
| `12:35–12:50Z` (midday) | 7,718 – 86,035 |
| `16:00:00Z` | 710,420 |
| `16:05:00Z` | 576,489 |
| `16:10:00Z` | 714,697 |
| `16:15:00Z` | 635,746 |
| `16:20:00Z` | **1,011,402** |
| `16:25:00Z` (last bar) | 549,812 |

The closing-auction volume surge (5-10×+ normal, concentrated in the final minutes) is one of the most
reliable, mechanical facts about US equity trading — it happens at the REAL exchange close, 16:00
**America/New_York**, never at 16:00 UTC (which is 11:00 AM ET, mid-morning, with no reason for a closing
surge). The labelled `16:20Z` bar carrying the day's largest volume, immediately before the sequence ends
at `16:30Z`, is decisive: **the `datetime` field actually contains America/New_York wall-clock time, with
a UTC `"Z"` suffix mistakenly appended — not true UTC.** (Sanity-consistent with the session span itself:
`09:00–16:30` "local" reads exactly as NYSE regular hours ± a 30-minute pad, whereas `09:00–16:30` UTC
would be `04:00–11:30 ET`, an odd, asymmetric window with no market-structure explanation.)

**This is fixable, not disqualifying by itself.** `HuggingFaceEquitiesAdapter` now takes a `source_tz`
parameter: when set, the timestamp is localized to the TRUE zone and then converted to UTC, instead of
being naively parsed as if it were already UTC (`datasets/huggingface_equities.py`, tested in
`tests/historical/test_hf_source_tz_fix.py`). For this dataset specifically, ingestion must use
`source_tz="America/New_York"`. **Any use of this dataset's raw `datetime`/`date` field as true UTC — as
its own documentation instructs — will silently misalign every session, gate and clock boundary by 4-5
hours.** This is exactly the class of defect H2's lookahead guard cannot catch (the clock and the data
would both be "wrong" together, consistently) — it can only be caught by exactly this kind of independent,
market-structure-based cross-check, which is why it's recorded here rather than only in code.

## 3. Regular-session membership / premarket / after-hours

Once re-interpreted as America/New_York: the DENSE block for liquid names (AAPL, SPY, QQQ, AMD, NVDA) runs
`09:00–16:30` ET — 30 minutes of pre-market pad, the full `09:30–16:00` regular session, and 30 minutes of
post-close pad (likely closing-auction prints settling). **Sparse, near-24-hour bars also exist for at
least one heavily-traded name** (AAPL showed additional isolated bars around `00:00` and `21:25–23:55` on
an adjacent date in the same byte range) — consistent with modern overnight/ATS trading venues (e.g. Blue
Ocean) that a handful of providers now report for the most liquid tickers. Not fully characterized by this
tiny sample; worth a wider check before relying on "is this symbol in its regular session" logic near the
open/close boundary.

## 4. Duplicate / missing bars — real, symbol-dependent gaps found

No duplicate (symbol, timestamp) rows in 1,135 extracted rows. **Missing bars are real and material**,
measured against the dense expected 5-minute cadence for 2024-01-02 (session span per symbol):

| Symbol | Bars present | Expected (dense, same span) | Missing |
|---|---|---|---|
| SPY | 90 | 90 | 0 |
| AAPL | 90 | 91 | 1 |
| MSFT | 74 | 90 | 16 (18%) |
| META | 73 | 89 | 16 (18%) |
| DELL | 26 | 36 (and session starts at **13:30**, not 09:00) | 10 within-window, **+54 bars entirely missing before 13:30** |

A historical execution runner (H4) reading 5-minute bars for quotes/fills must have an explicit stale/
missing-bar policy (matching the live system's own "no stale/fallback data may create an order" rule) —
this is not yet built and is now a documented H4 requirement, not a reason to reject the dataset (SPY, the
name most similar to what a liquid-momentum scan would select, was perfectly dense).

## 5. Zero/negative prices, OHLC invariants, volume validity — PASS

Zero across the sample: no `open/high/low/close` ≤ 0, no `volume` < 0, no `high < low`, no
`high < open|close`, no `low > open|close`. Types are consistently `float`/`int` (`datasets-server`'s own
type report agrees: `float64`/`int64`, no strings-as-numbers).

## 6. Cross-check against Yahoo (2024-01-02, daily aggregate)

Yahoo's public 5-minute history does not reach back to 2024 (~60-day limit), so the cross-check aggregates
fabhaus's `09:00–16:30`-ET-window bars into a synthetic daily OHLCV and compares against Yahoo's real daily
bar for the same date (`auto_adjust=False`):

| Symbol | fabhaus close | Yahoo close | Close diff | fabhaus volume | Yahoo volume | Volume ratio |
|---|---|---|---|---|---|---|
| SPY | 472.33 | 472.65 | −0.07% | 28.1M | 123.6M | **0.23×** |
| QQQ | 403.56 | 402.59 | +0.24% | 24.0M | 58.0M | **0.41×** |
| AAPL | 186.97 | 185.64 | +0.72% | 30.8M | 82.5M | **0.37×** |
| AMD | 139.81 | 138.58 | +0.89% | 30.0M | 64.9M | **0.46×** |
| MSFT | 369.97 | 370.87 | −0.24% | 9.1M | 25.3M | **0.36×** |
| META | 344.02 | 346.29 | −0.66% | 8.1M | 19.0M | **0.42×** |
| DELL | 74.68 | 74.79 | −0.15% | 0.58M | 2.96M | **0.20×** |
| **NVDA** | **481.16** | **48.17** | **+899%** | 20.3M | 411.3M | 0.05×† |

**Price agreement is good** (closes within 0.07-0.9% for 7 of 8 symbols — the residual is plausibly the
extended-hours padding on fabhaus's window vs. Yahoo's regular-session-only daily bar; low values matched
Yahoo's daily low EXACTLY for DELL and META, which is reassuring for raw price fidelity).

**Volume disagrees systematically and materially: fabhaus's aggregated volume is 20-46% of Yahoo's
consolidated daily volume for every single symbol checked**, not explainable by the extended-hours window
alone (that would ADD volume, not remove the majority of it). This means fabhaus's underlying bars source
does not report full consolidated-tape volume — **any AVDI gate that reads volume for a liquidity
decision (e.g. `score_liquid_momentum`'s `dollar_volume < 5e6` floor) would be comparing against a
different, lower baseline than the forward Ubuntu runtime's own Yahoo-sourced volume**, and must not be
used uncalibrated. Flagged; not yet resolved.

## 7. Corporate actions — CONFIRMS the risk, blocks H4 until a policy exists

† NVDA's ~899% "discrepancy" is not a data error — it is direct, concrete proof of the README's own
disclosure ("corporate-action adjustments are not pre-applied"): NVIDIA's real 10-for-1 split was on
2024-06-07. fabhaus's 2024-01-02 close ($481.16, pre-split, RAW) is internally consistent with NVDA's real
pre-split trading range (~$490-500 that week); Yahoo's `auto_adjust=False` daily "Close" ($48.17) is
**itself split-back-adjusted by Yahoo's current pricing pipeline regardless of the flag** — a real, and
separately worth knowing, quirk of `yfinance`'s OWN behavior for very old dates. **Both are legitimate
representations of different conventions; mixing them without a stated policy would silently corrupt any
comparison, or any strategy backtest, that spans a split date.**

### Corporate-action policy for the historical runner (required before H4; not yet built)

1. **Splits/reverse splits:** the historical runner operates on **raw, unadjusted** prices at all times —
   the SAME convention the forward Ubuntu runtime uses (AVDI never trades adjusted prices; a real fill
   happens at a real, unadjusted price). A split is therefore not "corrected"; it is a **legitimate, large,
   single-bar price/share-count discontinuity** that must be **detected and flagged**, never silently
   smoothed: a day-over-day close ratio outside `[0.4, 2.5]` (catches 2:1 and larger splits/reverse-splits
   either direction) with volume moving inversely by a comparable factor triggers a `SPLIT_SUSPECTED` event
   on that (symbol, date), and any open historical position or pending signal spanning that boundary is
   marked `CORPORATE_ACTION_BOUNDARY` in its own event record — mirroring the real `apply_split()` machinery
   already in `lab/paper/fills.py`, reused rather than reinvented, once H4 needs it.
2. **Ticker changes / re-listings:** the historical universe is fixed per walk-forward fold from a
   **point-in-time symbol list** (never "today's" ticker list applied retroactively); a symbol that
   disappears mid-fold is treated as **delisted from that date forward** (see #3), never silently remapped
   to a successor ticker without an explicit, dated mapping table checked into the manifest.
3. **Delistings/mergers:** a symbol with no further bars after some date is NOT survivorship-bias-filtered
   away — any open historical position in it is closed at its LAST available raw price with an explicit
   `event_reason="delisted_or_merged_no_further_data"`, and reported separately from ordinary exits so
   performance numbers are never quietly inflated by ignoring a name that went to zero or was acquired.
4. **Never silently adjust history.** No back-adjustment, no forward-adjustment, no interpolation across a
   detected corporate-action boundary. The runner FAILS CLOSED (flags, does not trade through) an
   unresolved `SPLIT_SUSPECTED`/`CORPORATE_ACTION_BOUNDARY` event by default; a user may explicitly opt an
   experiment into a documented handling mode later, but the default is always the safest one.

This policy is a design decision now on record — it is not implemented in code yet (no H4 code exists);
implementing and testing it is part of H4, not this audit.

## 8. Reproducibility / provenance

* Revision pinned: `f17c0b0c3cf6a455994f93d6a85e76274172ab03` (never `main`).
* Per-shard upstream integrity: the Hub serves each LFS-backed shard with its **git-lfs SHA-256 as the
  HTTP `ETag`** (verified format: 64 lowercase hex chars) — recorded per sampled shard in
  `fabhaus_sample/fetch_manifest.json` without downloading the full file to compute it independently.
* The extracted 1,135-row sample itself is content-hashed (`fetch_manifest.json`'s
  `extracted_sample_sha256`) and would be byte-identical on a re-run against the same pinned revision and
  byte ranges (the audit script is deterministic; HF's CDN honours the requested Range exactly).
* `research/historical/manifest.py`'s `DatasetManifest` now carries `hf_repository`, `hf_revision`,
  `upstream_files`, `upstream_sha256`, `selected_columns`, `adapter_version` — an experiment manifest built
  from this source will never point merely at "latest".

## 9. Verdict against the 7-point dataset-acceptance gate

| # | Criterion | Result |
|---|---|---|
| 1 | Schema validation | **PASS** |
| 2 | Timestamp validation | **FAIL as documented** (mislabelled UTC) — **fixable**: ingest with `source_tz="America/New_York"` |
| 3 | Causal-access validation | N/A to the raw dataset (a property of the runner); the mislabelling above would corrupt it if not fixed first |
| 4 | Spot-check vs. an independent source | **PARTIAL PASS** — prices agree well; volume is systematically 0.2-0.46× Yahoo's (unresolved) |
| 5 | Corporate-action policy | **NOT YET ESTABLISHED IN CODE** — policy drafted above (§7); implementation is H4 scope |
| 6 | Reproducible manifest/hash | **PASS** |
| 7 | Repeatable local import | **PASS** (deterministic given the pinned revision) |

**Recommendation:** the primary candidate is **conditionally viable**, not yet accepted. Before any H4
bulk ingestion: (a) always ingest with `source_tz="America/New_York"` (now supported, tested); (b) decide
how volume will be normalized/calibrated for any liquidity gate, or accept it as directional-only; (c)
implement the corporate-action policy above; (d) audit a broader sample (more symbols, more months) before
trusting anything beyond the 8-symbol, 4-month scope checked here. `GGLabYale/MTBench_finance_stock` is
recorded as a secondary candidate (below) but not evaluated to this depth — its value is 2013-2023
coverage for later regime testing, not a replacement for this audit's findings.

## Secondary candidate (recorded, not integrated): `GGLabYale/MTBench_finance_stock`

* Repository exists, real revision `0c1a656a9611846e76c35a96ec85404829763022`, Parquet format (not JSONL),
  ~148 shards of ~55-65 MB each, `size_categories: 1K<n<10K` (likely 1K-10K distinct series/examples, not
  raw bar rows, given the shard count and size), associated with arXiv paper `2503.16858`. Not schema-
  audited, not cross-checked, not ingested. Its coverage (documented as 2013-2023, per the task) is
  materially longer than fabhaus's 2024-2026, which is why it is worth returning to for regime-diversity
  testing once the primary source's open items are resolved.
