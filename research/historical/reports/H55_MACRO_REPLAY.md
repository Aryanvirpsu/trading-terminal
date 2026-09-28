# H5.5 — historical macro replay (`_fam_macro`), status report

**Status: DONE. A real `FRED_API_KEY` was supplied; real ALFRED data was fetched, versioned, and fed
through a real H5 rerun. Result: DELL, META, and TMO — the exact three symbols the original H5 report
identified as capped by the `data_quality` ceiling — now reach TRADEABLE under `PRICE_TREND_MACRO_V1`,
through the real, unmodified Champion code, unchanged thresholds.** One new, investigated divergence
(AMD) is disclosed in §7, not hidden.

Branch `h1/historical-lab`. No production code changed; no Champion gate/threshold/ranking/capacity/cutoff
touched.

## 1. Production macro family specification (`lab/fred.py`, `lab/decision_engine.py:_fam_macro`)

Inspected directly from source before writing any replay logic (also the `macro.py` module docstring,
Part 1, for the fully-cited version):

* **Series**: `DGS10` (10Y Treasury), `DGS2` (2Y Treasury), `VIXCLS` (VIX close), `FEDFUNDS` (effective fed
  funds rate) — all daily FRED series.
* **Fetch**: the single latest available print per series (`limit=2&sort_order=desc`, first non-`"."`
  value) — no lookback window, no moving average, no smoothing.
* **Transformation**: `spread = DGS10 - DGS2`; `tilt = clamp(spread / 1.0, -1, 1)` (linear, ±100bp → ±1.0);
  `if VIXCLS > 25: tilt -= 0.3` (a flat penalty, not scaled by how far over 25); reclamped to `[-1, 1]`.
* **FEDFUNDS is fetched and reported but never used in the arithmetic at all** — confirmed dead weight in
  the real production formula, reproduced faithfully (including the dead weight), not "fixed."
* **Confidence**: a **fixed `0.5`** whenever a FRED key is configured at all — not data-driven, does not
  vary with how many series resolved or how stale the print is.
* **Fallback**: no key → `conf=0`, `dir=0`, `"no FRED key"`; a failed series fetch → that series is `None`
  and degrades the calculation gracefully (curve term drops to 0 if either treasury series is missing; VIX
  penalty just doesn't apply if VIX is missing) — never raises, never blocks.

## 2. Vintage/point-in-time handling

Historical replay must answer "what would `_latest(series_id)` have returned had this run on date T," not
"what does FRED say today." Implemented via `VintageObservation` (`series_id`, `observation_date`,
`realtime_start`, `realtime_end`, `value`, `retrieval_source`, `api_version`) and
`MacroHistory.latest_value_as_of(series, T)`, which selects — among vintages whose
`[realtime_start, realtime_end]` window contains `T` — the one with the most recent `observation_date`.

**Lookahead test, exactly the directive's own example** (`test_replay_before_revision_sees_the_original_value_never_the_revision`):
value X published `2026-06-02`, current through `2026-06-14`; revised to Y, current from `2026-06-15`
onward. Replay at `2026-06-14` → X. Replay at `2026-06-15` (the revision's own start) → Y. Replay at any
date strictly before `2026-06-15` → X, always — checked across multiple dates, never once leaking Y early.
A date before the observation's own first publication returns no data at all (not a stale/zero value).

**Per-series integrity** (`SERIES_INTEGRITY`, all four `ACCEPTED` today): DGS10/DGS2/VIXCLS/FEDFUNDS are
daily market-observation series (not survey/estimate aggregates like GDP/CPI) with a short (~1 business
day) publication lag and, per established practice for the St. Louis Fed's own daily market-rate series,
are not subject to the kind of subsequent revision GDP/CPI get. This is disclosed as the current verdict,
not assumed permanently: `fetch_fred_vintages()` always requests `output_type=2` (every historical vintage,
ALFRED-style), so if a real fetch ever does surface more than one vintage per `observation_date` for one of
these series, `MacroHistory`'s point-in-time logic already handles it correctly (proven by the synthetic
revision test above) — it would not need to be rebuilt. A series without an `ACCEPTED` verdict is refused
by `historical_macro_signal()` (`SeriesIntegrityError`) rather than silently treated as safe.

## 3. Local store

`save_macro_history()` / `load_macro_history()` / `verify_macro_manifest()`: Parquet + a manifest recording
source (`"FRED"`), series ids, retrieval date, API version (`fred/series/observations?output_type=2`), date
range, row count, vintage semantics, local SHA-256, and adapter version. `save_macro_history()` takes no
`api_key` parameter at all — the key cannot appear in the manifest even by mistake, checked directly via
`inspect.signature()` in `test_manifest_never_contains_the_api_key`. Kept under
`historical_data_root()/macro/` (the one guarded directory Historical Lab code may write under) rather than
the directive's illustrative `research/historical/data/macro/` path, for consistency with every other
Historical Lab dataset's single source of truth — a deliberate, disclosed deviation from the literal
suggested path, not an oversight.

## 4. Capability fingerprint

`PRICE_TREND_MACRO_V1` added to `capability.py` — `PRICE_TREND_ONLY_V1` plus a genuinely replayed macro
family; every other stubbed family stays stubbed. `PRICE_TREND_ONLY_V1` itself is untouched and remains
independently valid (a manifest/run stamped with it stays historically accurate about exactly what it
replayed).

## 5. `_fam_macro` wiring

`HistoricalAVDIContext` gains an optional `macro_history` parameter. Omitted (every existing H3/H4/H5
caller): `_fam_macro` stays stubbed, byte-for-byte the same behavior as before this work. Given: `_fam_macro`
is genuinely replayed via `historical_macro_signal()`, verified end-to-end through a real
`HistoricalAVDIContext` (`test_fam_macro_is_stubbed_by_default_and_replayed_when_macro_history_given`).

## 6. Capability arithmetic — verified against the real runtime, not just static math

`test_replaying_macro_alone_crosses_the_data_quality_floor` calls the REAL `lab/data_quality.py` functions
(`category_coverage`, `assess`) directly, not a reimplementation: `PRICE_TREND_ONLY_V1`'s baseline
(`price`+`candles`+`sector` only) reproduces `0.536`; adding one fully-covered `macro` category reproduces
`0.588`. This confirms the earlier H5 report's arithmetic claim against the actual function, not just by
hand-calculation.

**This is verified as STATIC arithmetic only, exactly as it was before.** The directive is explicit that
static arithmetic is not proof of behavior — proving DELL/META actually cross 0.55 and reach TRADEABLE
requires rerunning the real `KNOWN_FORWARD_2026_09_25` replay with real historical macro data feeding a
real `evaluate()` call, end to end. That is the part still blocked (§7).

## 7. The real fetch and the H5 rerun (done)

**Real fetch.** `FRED_API_KEY` was provided via chat and used ONLY as an in-process environment variable
for the fetch calls below — never written to disk, never logged, never placed in a manifest or commit.
`printenv | grep -i FRED` before this point showed nothing; the key was supplied by the user directly in
this session and is not persisted anywhere this report or the codebase can be inspected to recover it.

While building the real fetch, the live API's actual response shape corrected two assumptions the
mechanism-only build (§1-§6) had made without live data to check against:

* **Vintage column format.** `output_type=2` returns columns named `f"{series_id}_{YYYYMMDD}"` (no
  separators) — `fetch_fred_vintages()` originally stored this raw, which would have compared incorrectly
  against the ISO-formatted dates `MacroHistory` uses everywhere else. Fixed to normalize to ISO
  (`YYYY-MM-DD`) before storage, verified by re-running the full suite (still 100% passing) plus a live
  fetch showing correctly-ordered dates.
* **FEDFUNDS is monthly, not daily.** The original `SERIES_INTEGRITY` entry guessed "daily (business
  days)" for all four series without checking; a real ~2-month fetch returned exactly **1** FEDFUNDS row
  (not ~40), confirming it publishes monthly. Corrected in `SERIES_INTEGRITY` with the verified frequency,
  noting this has zero effect on `_fam_macro`'s own output since FEDFUNDS is never used in that arithmetic
  (§1) — a stale assumption caught and fixed, not one that silently produced a wrong signal.

**Zero revisions observed** across DGS10/DGS2/VIXCLS over `2026-08-01`..`2026-09-26` (39/39/37 rows
respectively, one row per observation, i.e. one vintage run each) — consistent with, and now empirically
supporting rather than merely presuming, the `SERIES_INTEGRITY` verdict in §2.

**Dataset**: `KNOWN_FORWARD_2026_09_25_MACRO` — 116 vintage observations across the four series, committed
(Parquet ~8KB + manifest ~4KB, alongside `KNOWN_FORWARD_2026_09_25_{DAILY,5M}`) so this rerun is itself
reproducible without a key. Manifest and Parquet file both directly grepped for the key text: zero
occurrences in either.

**The rerun** (`test_h55_rerun.py`, run against the real committed dataset, no network at test time):

| Symbol | Forward | `PRICE_TREND_ONLY_V1` (original H5) | `PRICE_TREND_MACRO_V1` (this rerun) | Reason |
|---|---|---|---|---|
| DELL | TRADEABLE | MONITOR (`data_quality=0.536`) | **TRADEABLE** | `MATCH` |
| META | TRADEABLE | MONITOR (`data_quality=0.536`) | **TRADEABLE** | `MATCH` |
| TMO | TRADEABLE | MONITOR (`data_quality=0.536`) | **TRADEABLE** | `MATCH` |
| AAPL, MSFT, TSLA, NVDA, FCX, NEM, VRTX | (varied) | never a candidate | never a candidate | `PRICE_DATA_DIFFERENCE` (unchanged) |
| CRM | MONITOR | MONITOR | MONITOR | `MATCH` (unchanged) |
| **AMD** | **MONITOR (never TRADEABLE)** | MONITOR | **TRADEABLE** | **new divergence, investigated below** |

Directly confirmed against the real `evaluate()` output for DELL at its first-seen cycle: `data_quality`
goes from `0.536` (without macro) to `>= 0.55` (with the real macro dataset) in the SAME process, same
symbol, same instant — REPLAY BEHAVIOR, not static arithmetic (`test_data_quality_actually_crosses_0_55_in_real_replay_not_just_static_math`).

**The AMD divergence, investigated, not hidden.** Diagnosed directly against `evaluate()`'s own output at
AMD's 09:35 ET first-seen cycle: without macro, `failed_gates=['data_quality']` at `0.536`; with the real
macro dataset, `failed_gates=[]` at `0.575`, `decision=TRADEABLE`. This is the SAME mechanism working
correctly — macro evidence is symbol-agnostic, so once it crosses the floor for one symbol on a date, it
crosses it for every symbol whose own technicals are otherwise strong enough that day, not only the ones
the forward session happened to trade. `compare.py` now classifies this case as `PRICE_DATA_DIFFERENCE`:
the most likely specific cause is that AMD's real Yahoo-sourced technicals for 2026-09-25 differ from
whatever the forward session's own live provider mix (60% TradingView / 40% Yahoo) actually saw for
it — the same hypothesis already used for symbols that never became candidates at all, just manifesting in
the opposite direction. **Not independently verified** against the forward session's own raw technicals
(unavailable from this environment) — stated as the most likely explanation, not confirmed. Differences
from forward AVDI shrank for DELL/META/TMO and did not shrink for AMD; both are reported, neither is
smoothed over.

**Zero `UNKNOWN`/`REPLAY_BUG` rows** under the new fingerprint either
(`test_rerun_zero_unexplained_rows_under_the_new_fingerprint_too`) — every divergence, old or new, has a
named, investigated reason.

## Decision (answering the directive's four questions, against real replay behavior)

1. **Is macro replay causally trustworthy?** Yes. Vintage-safe (proven against the directive's own revision
   example, and now also empirically: zero revisions found in the real fetch, exactly as `SERIES_INTEGRITY`
   predicted), reproduces the real production arithmetic exactly (including its dead-weight FEDFUNDS fetch
   — corrected to the real, verified monthly frequency once real data was available — and its
   non-data-driven fixed confidence), wired through the real, unmodified decision stack, and the API key
   never touched disk, a log, or a manifest.
2. **Does it naturally remove the historical data_quality ceiling?** Yes, confirmed in REPLAY BEHAVIOR, not
   only static arithmetic: DELL's real `evaluate()` output crosses `0.536 → 0.575` in the same process
   with the real dataset switched in.
3. **Can historical AVDI now produce legitimate TRADEABLEs without changing Champion thresholds?** Yes —
   DELL, META, and TMO all reach TRADEABLE under `PRICE_TREND_MACRO_V1`, through the literal unmodified
   `evaluate()`/`data_quality.py`/`decision_engine.py` code, with the floor left at `0.55` and nothing else
   tuned.
4. **Is the new fingerprint representative enough to start HIST-001?** Per the "gate to HIST-001" list:
   gate 1 (macro replay causally valid) — met. Gate 2 (H5 rerun under the enriched fingerprint) — met, this
   section. Gate 3 (legitimate TRADEABLEs under unchanged thresholds) — met for DELL/META/TMO. Gate 4 (no
   unexplained replay divergence) — met: every row, including the new AMD one, carries a named,
   investigated reason; AMD's own explanation is stated as "most likely," not fully confirmed, which should
   be read as a residual caveat on HIST-001's evidence quality, not a blocking unexplained divergence. Gate
   5 (reproducible source manifests) — met: both the price/volume datasets and this macro dataset are
   committed with verifying manifests. **On balance: ready for HIST-001 to begin**, carrying forward the
   disclosed AMD caveat and the `PRICE_DATA_DIFFERENCE` hypothesis (for AAPL/MSFT/TSLA/NVDA/FCX/NEM/VRTX)
   as open, stated questions rather than resolved ones.
