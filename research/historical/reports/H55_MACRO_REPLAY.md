# H5.5 — historical macro replay (`_fam_macro`), status report

**Status: mechanism built and unit-tested; blocked on a real `FRED_API_KEY` for the actual fetch and the
H5 rerun.** Nothing below claims completion of the parts that need real data — those are marked BLOCKED
explicitly.

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

## 7. BLOCKED: real fetch and the H5 rerun

`fetch_fred_vintages()` / `build_macro_history()` need a real `FRED_API_KEY` (env-var/config only, per the
directive — never committed, never printed, never placed in a manifest; `save_macro_history()`'s signature
has no slot for it at all). This environment has none configured as of this report
(`printenv | grep -i FRED` → nothing). The user said a key would be provided separately; none has arrived
in this session yet.

**Until a key is supplied, none of the following has been done, and this report does not claim it has
been:**
* an actual historical fetch of real FRED/ALFRED data for the `KNOWN_FORWARD_2026_09_25` date range
* a rerun of the H5 replay under `PRICE_TREND_MACRO_V1`
* any comparison of DELL/META's `data_quality`/classification old-fingerprint vs. new-fingerprint
* any claim that a real Champion `TRADEABLE` decision emerges naturally

**As soon as a `FRED_API_KEY` is available**, the remaining steps are mechanical and already have every
piece built and tested:
1. `build_macro_history(start=..., end=..., api_key=os.environ["FRED_API_KEY"])` for a window covering
   `KNOWN_FORWARD_2026_09_25` (e.g. `2026-08-01`..`2026-09-26`, comfortably past the publication lag).
2. `save_macro_history(history, "KNOWN_FORWARD_2026_09_25_MACRO", date_range=(...))`.
3. Pass `macro_history=history` into the known-forward replay's `HistoricalExecutionContext` construction
   (`research/historical/known_forward/replay.py`), alongside the existing daily/intraday providers.
4. Re-run `compare.py`'s comparison matrix; report the new `data_quality` values for DELL/META/TMO
   specifically (the three symbols the original H5 report identified as `CAPABILITY_DIFFERENCE`), and
   whether any classification changes to `TRADEABLE`.
5. If TRADEABLE emerges naturally under unchanged thresholds: gate 3 of the "gate to HIST-001" list is met
   for at least one evidence family; re-evaluate the remaining gates (4: no unexplained divergence, 5:
   reproducible manifests) before HIST-001 starts.
6. If it does NOT emerge: report exactly what coverage was achieved and why (e.g. the fetch returned data
   but confidence/coverage still fell short for a reason specific to that date), per the directive's "do
   not modify the floor, explain what remains missing."

## Decision (answering the directive's four questions, honestly, given the current state)

1. **Is macro replay causally trustworthy?** The MECHANISM, yes — vintage-safe (proven against the exact
   revision example given), fails closed for any series without a recorded integrity verdict, reproduces
   the real production arithmetic exactly (including its dead-weight FEDFUNDS fetch and its non-data-driven
   fixed confidence), and is wired through the real, unmodified decision stack. This has NOT yet been
   exercised against real fetched data, so "trustworthy in practice, at scale, against real revisions" is
   not yet claimed — only "correct by construction and by the tests that could be run without a key."
2. **Does it naturally remove the historical data_quality ceiling?** Verified only as static arithmetic
   against the real `data_quality.py` functions (0.536 → 0.588 for a symbol with full macro coverage) — not
   yet verified as REPLAY BEHAVIOR, which requires the blocked real-data rerun.
3. **Can historical AVDI now produce legitimate TRADEABLEs without changing Champion thresholds?**
   Unknown — blocked on §7.
4. **Is the new fingerprint representative enough to start HIST-001?** Not yet, and not decidable yet: gate
   3 of the "gate to HIST-001" list (a legitimate TRADEABLE actually produced) cannot be confirmed until
   the blocked rerun happens. `PRICE_TREND_MACRO_V1` exists and is ready to be exercised the moment real
   data is available.
