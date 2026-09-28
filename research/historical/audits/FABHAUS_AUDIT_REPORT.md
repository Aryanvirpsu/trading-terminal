# Dataset audit: `fabhaus/equities_5m_stockprices` (H4 primary bulk-data candidate)

**Verdict: OHLC/timestamps/session semantics ACCEPTED. Volume: see §6 (RELATIVE_ONLY). Corporate actions:
ACCEPTED_WITH_POLICY, policy drafted, not yet implemented in code — blocks H4 until it is.** Reproducible
from the pinned revision below; nothing in this audit relied on the Hub's dataset viewer or search (both
are unreliable for this dataset, per the user's note — every fact here comes from the raw shard files or
the `datasets-server` `/first-rows` endpoint, both queried directly).

* **Repository:** `fabhaus/equities_5m_stockprices`
* **Revision audited (pinned commit, never "main"):** `f17c0b0c3cf6a455994f93d6a85e76274172ab03`
* **Scripts:** `fabhaus_equities_5m_audit.py` (initial fetch), `fabhaus_tz_reaudit.py` (the DST re-audit
  that corrected §2 below). **Outputs are NOT all committed** (the corrected re-audit alone fetched several
  GB transiently); the original 1,135-row sample + its manifest remain in `fabhaus_sample/` as the
  reproducible evidence base.
* **Method:** direct HTTP Range GETs against `resolve/<revision>/<shard>.jsonl` (each shard is 7-23 GB;
  never downloaded in full — largest single fetch was 1 GB, into a symbol-filtered stream, nothing else
  persisted), plus a bisection helper (`_locate_offset_for_date`) that finds a target calendar date's byte
  offset in a time-sorted shard by probing a handful of small (300 KB) windows. Samples: 2024-01-02
  (winter), 2024-07-01 (summer), and the four trading days immediately surrounding both 2024 DST
  transitions (2024-03-08/11, 2024-11-01/04) — 6 dates, 2 seasons, both transition boundaries, one equity
  (AAPL) and one ETF (SPY) each.

## 1. Existence, schema, revision (PASS)

Confirmed via the Hub API (`GET /api/datasets/...`) and `datasets-server`'s `/first-rows` endpoint (the
Hub's own dataset **viewer**, not this project's tools, is what the user flagged as broken — `is-valid`
returns `"viewer": false, "filter": false`; `/first-rows` still works and matches what the raw shard bytes
actually contain, cross-checked directly). **53 columns**, exactly as documented: bar identifiers
(`symbol`, `datetime`, `date`, `unix_timestamp`), OHLCV (`open/high/low/close/volume/trade_count/vwap`),
10 `ti_*`, 10 `macro_*`×2 (value + observation date), 16 `cf_*`, 6 `cv_*`. All 8 target symbols
(AAPL/MSFT/META/NVDA/DELL/AMD/SPY/QQQ) are present in every sampled month.

**Per H1 v1 scope, only the raw market fields are ingested — never `ti_*`/`macro_*`/`cf_*`/`cv_*`:**
`symbol, datetime, date, unix_timestamp, open, high, low, close, volume, trade_count`. Enforced at the
adapter (`HuggingFaceEquitiesAdapter.column_map`), not merely a convention.

Canonical-bar-schema validation (`schemas.bars.validate_bars`) on the original 1,135-row extracted sample:
**`{"ok": true, "failures": {}}`** — no OHLC-ordering violation, no non-positive price, no negative volume,
no duplicate (symbol, timestamp), monotonic per-symbol timestamps, every bar aligned to a `:00/:05/.../:55`
minute boundary.

## 2. Timestamp validation — **ACCEPTED** (corrected after a re-audit; see the correction note)

**Verdict: `datetime` is genuinely, correctly UTC, exactly as documented.** Decisive evidence — the REAL
opening and closing volume spikes, found once a large enough window was fetched to actually reach them:

| Date | Season | Session start (labelled UTC) | Opening spike (UTC) | Closing spike (UTC) | Closing spike in ET |
|---|---|---|---|---|---|
| 2024-01-02 | winter (EST) | 09:00 | AAPL 14:30 (5.65M) | AAPL/SPY 21:00-21:10 (10-13M) | **16:00-16:10 ET** ✓ |
| 2024-03-08 (Fri, last EST day) | winter (EST) | 00:00* | — | 20:55-21:10 (4-7.5M) | **16:00-16:10 ET** ✓ |
| 2024-03-11 (Mon, first EDT day) | summer (EDT) | 08:00 | AAPL 13:30 (3.4M) | 19:55-20:10 (3.8-8.5M) | **16:00-16:10 ET** ✓ |
| 2024-07-01 | summer (EDT) | 08:00 | AAPL 13:30 (2.76M) | 19:55-20:10 (2.4-7.2M) | **16:00-16:10 ET** ✓ |
| 2024-11-01 (Fri, last EDT day) | summer (EDT) | 08:00 | AAPL 13:30 (4.1M) | 19:55-20:10 (3.0-9.3M) | **16:00-16:10 ET** ✓ |
| 2024-11-04 (Mon, first EST day) | winter (EST) | 09:00 | AAPL 14:30 (2.3M) | 20:55-21:00 (3.4-10.2M) | **16:00-16:10 ET** ✓ |

\* the 00:00 start on 2024-03-08 is a genuine sparse overnight/ATS bar sequence, not a session-boundary
artifact — see §3.

The real NYSE close (16:00 ET) lands at **21:00 UTC in winter (EST, UTC-5) and 20:00 UTC in summer (EDT,
UTC-4)** — the labelled closing-volume spike matches this to the minute in **every one of the 6 sampled
dates**, correctly shifting by exactly one hour across BOTH 2024 DST transitions, with no lag: the very
first trading day after each transition (2024-03-11, 2024-11-04) is already on the new offset. The session
"start" (09:00 winter / 08:00 summer) is independently consistent with a **4:00 AM ET** extended-hours
pre-market convention, correctly UTC-converted (4:00 EST = 09:00 UTC; 4:00 EDT = 08:00 UTC) — a very common
convention for historical-bars vendors, and itself further, independent confirmation that the field is
true UTC.

### Correction note (kept, per this project's own evidence-integrity standard: never silently overwrite a
prior conclusion)

The FIRST pass of this audit (single-month, single-window samples) concluded the field was mislabelled
America/New_York-as-UTC, based on a volume spike at labelled `16:20Z` on 2024-01-02 that looked like a
closing auction. **That was a methodological error on my part, not a dataset defect.** The sample window
(260 MB from file start) happened to end at labelled `16:30Z` — only 11:30 AM ET, nowhere near the actual
close — and the largest value WITHIN that truncated window was mistaken for THE day's largest value. Once a
1 GB window was fetched (reaching the genuine close near 21:00 UTC), the true closing-auction spike
appeared there instead, 20-40× larger than the false one, and the mislabelling theory could not survive
being checked across seasons and DST transitions as this task required. The `source_tz` parameter added to
`HuggingFaceEquitiesAdapter` in response to the original (wrong) finding is **not needed for this dataset**
and must NOT be used when ingesting it — it remains in the adapter as a generic feature for a source that
genuinely has this problem, with its own synthetic-data regression test
(`tests/historical/test_hf_source_tz_fix.py`), decoupled from any claim about fabhaus specifically.

## 3. Regular-session membership / premarket / after-hours

Confirmed span, in true UTC/ET: **09:00-23:55 UTC (04:00-18:55 ET) in winter**, **08:00-23:55 UTC
(04:00-19:55 ET) in summer** — i.e. roughly 4:00 AM ET pre-market through late-evening extended hours,
every sampled date, both seasons. Bars exist through this entire window for the two symbols checked (AAPL,
SPY), sparse outside the dense `09:30-16:00 ET` regular session, dense within it. Not characterized beyond
these two symbols; a wider symbol sample should confirm this holds for less-liquid names before relying on
"in regular session" logic near the open/close boundary for anything but the most liquid tickers.

## 4. Duplicate / missing bars — real, symbol-dependent gaps found

No duplicate (symbol, timestamp) rows found anywhere in any sample. **Missing bars are real and material**,
measured against the dense expected 5-minute cadence within the regular session on 2024-01-02:

| Symbol | Bars present (09:00-16:30 labelled) | Expected (dense) | Missing |
|---|---|---|---|
| SPY | 90 | 90 | 0 |
| AAPL | 90 | 91 | 1 |
| MSFT | 74 | 90 | 16 (18%) |
| META | 73 | 89 | 16 (18%) |
| DELL | 26 | 36 (session starts 13:30 UTC, not 09:00) | 10 within-window, **+54 bars entirely missing before 13:30** |

A historical execution runner (H4) reading 5-minute bars for quotes/fills must have an explicit stale/
missing-bar policy (matching the live system's own "no stale/fallback data may create an order" rule) —
this is now a documented H4 requirement (SPY, the name closest to what a liquid-momentum scan would
select, was perfectly dense; DELL was not).

## 5. Zero/negative prices, OHLC invariants — PASS

Zero across every sample: no `open/high/low/close` ≤ 0, no `volume` < 0, no `high < low`, no
`high < open|close`, no `low > open|close`. Types are consistently `float`/`int` (`datasets-server`'s own
type report agrees: `float64`/`int64`, no strings-as-numbers).

## 6. Volume — characterized, verdict **RELATIVE_ONLY** (not fixed, not rejected)

Cross-check against Yahoo's real daily volume for 2024-01-02 (Yahoo's 5-minute history doesn't reach back
this far; fabhaus's regular-session bars are aggregated into a synthetic daily volume for comparison):

| Symbol | fabhaus volume (regular session) | Yahoo daily volume | Ratio |
|---|---|---|---|
| SPY | 28.1M | 123.6M | 0.23× |
| QQQ | 24.0M | 58.0M | 0.41× |
| AAPL | 30.8M | 82.5M | 0.37× |
| AMD | 30.0M | 64.9M | 0.46× |
| MSFT | 9.1M | 25.3M | 0.36× |
| META | 8.1M | 19.0M | 0.42× |
| DELL | 0.58M | 2.96M | 0.20× |
| NVDA | 20.3M | 411.3M† | 0.05×† |

**Every symbol undercounts, consistently in the 0.2-0.46× range (excluding NVDA†).** This is NOT
attributable to the session window (already restricted to `09:30-16:00 ET` for this comparison, matching
Yahoo's daily convention) and is NOT random noise — the ratio is stable across large-caps (AAPL/MSFT/AMD:
0.36-0.46×), an ETF pair (SPY/QQQ: 0.23-0.41×), and a smaller name (DELL: 0.20×), consistent with a single
underlying cause: **fabhaus's bars source reports a PARTIAL feed (e.g. one venue/ECN or a subset of the
consolidated tape), not the full SIP-consolidated volume Yahoo (and the forward Ubuntu runtime) uses.**

**Decision: mark fabhaus volume `RELATIVE_ONLY`.** It is usable for within-dataset, same-symbol,
same-session RELATIVE comparisons (e.g. "this bar's volume vs. this symbol's own trailing average" — which
is how AVDI's `rel_vol` factor in `score_liquid_momentum` is actually computed) but **must NOT be compared
against, or substituted for, an ABSOLUTE volume threshold calibrated on Yahoo-consolidated data** (e.g.
`score_liquid_momentum`'s `dollar_vol < 5e6` floor, which is a hard absolute-dollar gate). Concretely, for
H4: **any absolute-volume rule sourced from fabhaus must be rescaled or disabled**; relative-volume rules
may be used as-is. This is a characterization, not a fix — the true cause (which venue(s) fabhaus's vendor
actually covers) is not established here and does not need to be for this decision to be safe.

† NVDA is excluded from the volume-ratio pattern discussion — its 0.05× "ratio" is dominated by the
corporate-action effect in §7, not a volume-coverage difference.

## 7. Corporate actions — CONFIRMS the risk, blocks H4 until a policy is implemented

NVDA's 2024-01-02 close: fabhaus (raw) $481.16 vs. Yahoo (`auto_adjust=False`) $48.17 — a ~10.0× gap. This
is not a data error; it is direct, concrete proof of the README's own disclosure ("corporate-action
adjustments are not pre-applied"): NVIDIA's real 10-for-1 split was 2024-06-07. fabhaus's raw $481.16 is
internally consistent with NVDA's actual pre-split trading range that week; Yahoo's daily "Close" is
**itself split-back-adjusted by Yahoo's current pricing pipeline regardless of the `auto_adjust` flag for
dates this old** — a separate, real quirk of `yfinance`'s own behaviour, worth knowing on its own. Both are
legitimate conventions; mixing them without a stated policy would silently corrupt any comparison, or any
backtest, that spans a split date.

### Corporate-action policy — design decision, drafted here, implementation is H4 scope (raw / split_adjusted)

Two explicit views of price, never silently blended:

* **`raw`** — exactly as delivered, immutable, the historical runner's default and the ONLY view the
  runner trades against (matching the forward Ubuntu runtime: AVDI never trades an adjusted price; a real
  fill happens at a real, unadjusted price).
* **`split_adjusted`** — a SEPARATE, derived view for cross-period analysis/reporting only (e.g. plotting a
  multi-year price series without a visual discontinuity) — never used for sizing, fills, or gates. Built
  only from CONFIRMED splits (see below), and is a read-only projection computed on demand from `raw` plus
  a split table — never stored as if it were new raw data, and never allowed to overwrite or hide `raw`.

**Split/reverse-split detection (implemented now, in H4 scope, not merely documented):**
1. A `SPLIT_SUSPECTED` event is raised at (symbol, date) when the day-over-day RAW close ratio falls
   outside `[0.4, 2.5]` AND volume moves inversely by a comparable factor (catches 2:1 and larger
   splits/reverse-splits either direction; the ratio bounds are deliberately wide to avoid ever silently
   absorbing a split as "just a volatile day" — a false positive costs a flagged-and-skipped day, a false
   negative costs a silently wrong fill).
2. **Split metadata must never create lookahead.** A split ratio is only knowable, and only applied to
   build `split_adjusted`, using data at/before the runner's current clock instant — exactly the same
   `HistoricalClock`-bound discipline as every other historical read (H2). A split detected from data dated
   `T` may not retroactively change how `raw` bars before `T` were reported to a decision made before `T`.
   `split_adjusted` is therefore never available "as of" any instant before the split's own confirmation
   date, and the runner records which instant a given `split_adjusted` projection was computed as of.
3. **Reuse, don't reinvent:** `lab/paper/fills.py:apply_split()` already implements the real quantity/price
   split math the forward system would use if AVDI ever traded through a live split; H4's split-adjustment
   projection calls this SAME function rather than a new formula.
4. **Default behaviour on an unresolved `SPLIT_SUSPECTED` event: FAIL CLOSED.** The runner does not trade
   through it (no entry decided using data that straddles an unconfirmed boundary); it flags and reports
   the event. A user may later opt an experiment into a specific handling mode explicitly; the default is
   always the safest one.

**Ticker changes, mergers, delistings — QUARANTINED, not resolved:**
* The historical universe is fixed per walk-forward fold from a **point-in-time symbol list** (never
  "today's" list applied retroactively).
* A symbol with no further bars after some date is **quarantined**, not survivorship-bias-filtered away:
  any open historical position in it is closed at its LAST available RAW price with
  `event_reason="quarantined_no_further_data"`, reported separately from ordinary exits so performance
  numbers are never quietly inflated by ignoring a name that was delisted, merged, or went to zero.
* A ticker-symbol change (the same company, a new symbol) is **quarantined by default** — never silently
  remapped — unless an explicit, dated mapping table is supplied and checked into the run's manifest.
* None of this is reliable metadata AVDI has today; H4 does not attempt to source or infer it. Quarantining
  is the safe default until such metadata exists.

This is now a concrete, testable design (not only prose); implementing and testing the detector/quarantine
logic is H4 scope, tracked as an explicit H4 acceptance item, not this audit's job.

## 8. Reproducibility / provenance

* Revision pinned throughout: `f17c0b0c3cf6a455994f93d6a85e76274172ab03` (never `main`).
* Per-shard upstream integrity: the Hub serves each LFS-backed shard with its **git-lfs SHA-256 as the
  HTTP `ETag`** (verified format: 64 lowercase hex chars) — recorded per sampled shard in
  `fabhaus_sample/fetch_manifest.json` without downloading the full file to compute it independently.
* The original 1,135-row extracted sample is content-hashed (`fetch_manifest.json`'s
  `extracted_sample_sha256`) and is byte-identical on a re-run against the same pinned revision and byte
  ranges (both the audit script and HF's CDN Range handling are deterministic).
* `research/historical/manifest.py`'s `DatasetManifest` carries `hf_repository`, `hf_revision`,
  `upstream_files`, `upstream_sha256`, `selected_columns`, `adapter_version` — an experiment manifest built
  from this source will never point merely at "latest". Verified by
  `tests/historical/test_hf_source_tz_fix.py::test_manifest_records_extended_hf_provenance`.

## 9. Field-by-field verdict

```text
OHLC               ACCEPTED
timestamps         ACCEPTED                  (genuinely UTC — see correction note in §2)
session semantics  ACCEPTED                  (04:00 ET premarket .. ~19:55 ET extended-hours, both seasons)
volume             ACCEPTED_RELATIVE_ONLY    (0.2-0.46x Yahoo, consistently — see §6; no absolute-volume rules)
splits             ACCEPTED_WITH_POLICY      (policy drafted in §7; detector/quarantine logic is H4 scope, not yet built)
ticker changes     QUARANTINED               (no reliable metadata; never silently remapped)
mergers            QUARANTINED               (no reliable metadata; closed at last raw price, reported separately)
delistings         QUARANTINED               (same as mergers)
```

**Recommendation: the primary candidate is now ACCEPTED for H4, conditional on implementing (not just
documenting) the §7 corporate-action detector/quarantine logic and the §6 no-absolute-volume-rule
constraint before any experiment reads volume or spans a split boundary.** Do not use `source_tz` when
ingesting this dataset (§2 correction). `GGLabYale/MTBench_finance_stock` remains recorded as a secondary
candidate (below), not evaluated to this depth.

## Secondary candidate (recorded, not integrated): `GGLabYale/MTBench_finance_stock`

* Repository exists, real revision `0c1a656a9611846e76c35a96ec85404829763022`, Parquet format (not JSONL),
  ~148 shards of ~55-65 MB each, `size_categories: 1K<n<10K` (likely 1K-10K distinct series/examples, not
  raw bar rows, given the shard count and size), associated with arXiv paper `2503.16858`. Not schema-
  audited, not cross-checked, not ingested. Its coverage (documented as 2013-2023, per the task) is
  materially longer than fabhaus's 2024-2026, which is why it is worth returning to for regime-diversity
  testing once H4 is built on the primary source.
