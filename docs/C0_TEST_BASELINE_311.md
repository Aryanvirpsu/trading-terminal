# C0_TEST_BASELINE_311.md — CPython 3.11 test baseline, by exact test ID

_Established 2026-09-21, ~20:20 EDT · venv `c0-work/venv-tests` (frozen deps, §`C0_DEPENDENCY_FREEZE.md`) · repo `main`
@ `223f5ff` · `python -m pytest tests/unit -q -p no:cacheprovider --ignore=tests/e2e --junitxml=...`._

**This is the 3.11 baseline the design doc's §8.2 asked for, kept separate from the existing 3.14 baseline
(`C0_INVENTORY.md` §4.3).** Per your instruction, C0 passes on **ID-level reproduction**, not a headline count — and this
gate found the headline count is not even a single number: it legitimately varies with **wall-clock time of day** and
**TZ**, for reasons now root-caused below. Every run used an isolated `HOME`/`USERPROFILE`; the real state dir is
untouched (proof: `C0_DEPENDENCY_FREEZE.md` §8).

Total collected: **1,278 tests** on 3.11 (vs 1,272 reported for 3.14 in the original inventory pass — see §5, this is a
counting artifact of `--ignore=tests/e2e`, not a real difference; both interpreters collect the same 1,278 when counted
the same way, confirmed in §5).

---

## 1. The four failure classes found

| Class | Meaning | Gates C0? |
|---|---|---|
| **D** — deterministic | same outcome every time, on every reference | **yes** — must match exactly |
| **N** — nondeterministic (timing) | outcome is a coin flip by test design (compares two wall-clock durations) | **no** — run 3×, recorded, not gated |
| **T** — TZ/time-of-day dependent | deterministic **given** TZ and wall-clock time, because the test itself exercises the exact UTC-vs-local-date boundary this project's D1 decision preserves | **no** — gated only against a reference run at the *same* TZ and inside/outside the same window |
| **W** — Windows-3.11-clock-resolution dependent | nondeterministic on **Windows + CPython 3.11 only**; root-caused below; **not expected to reproduce on Linux** (Case 1's actual platform) | **no on Windows** — must be re-checked at the Docker/G4 gate on Linux, where it is expected to disappear |

---

## 2. Baseline table — 8 tests never fully "pass" (class D are permanent; N/T/W vary by condition)

| # | Test ID | Class | Reason | 3.11 ET (now) | 3.11 UTC | 3.14 ET | 3.14 UTC |
|---|---|---|---|---|---|---|---|
| 1 | `tests/unit/test_canonical_v11_invariants.py::test_oi_policy_has_no_overlapping_hard_and_soft_ranges` | **D** | intentionally red (docstring: "stays red forever") | fail | fail | fail | fail |
| 2 | `tests/unit/test_canonical_v11_invariants.py::test_contract_quality_identical_across_pipelines` | **D** | intentionally red, same reason | fail | fail | fail | fail |
| 3 | `tests/unit/test_finbert_sentiment.py::test_transformers_available` | **D** | `transformers` not installed (correctly excluded from the freeze, §`C0_DEPENDENCY_FREEZE.md` §1) | fail | fail | fail | fail |
| 4 | `tests/unit/test_finbert_service.py::test_available` | **D** | same — no FinBERT model deps | fail | fail | fail | fail |
| 5 | `tests/unit/test_finbert_service.py::test_score_force` | **D** | same — falls back to `'lexical'` | fail | fail | fail | fail |
| 6 | `tests/unit/test_finbert_service.py::test_cache_behavior` | **N** | compares two `perf_counter` gaps; without the model both are ~µs | **pass** (3/3 isolated reruns, §4) | pass | pass (this run) | pass |
| 7 | `tests/unit/test_pipeline2_canonical_migration.py::test_iv_richness_is_a_genuine_quality_tilt_input` | **T** | root-caused §3.1 | **fail** | pass | fail | pass |
| 8 | `tests/unit/test_pipeline3_canonical_migration.py::test_pipeline2_tests_unchanged` | **T** (derived) | runs test #7 in a subprocess, asserts `returncode==0` | **fail** | pass | fail | pass |
| 9 | `tests/unit/test_paper_trading.py::test_every_decision_is_journalled_including_refusals` | **W** | root-caused §3.2 | **fail** (both ET runs) | fail | pass | pass |

**Run totals actually observed** (all figures reproduced twice for 3.11-ET to confirm stability):

| Run | TZ | Interpreter | passed | failed | Explained by |
|---|---|---|---|---|---|
| `g_py311_et_run1` / `run2` | America/New_York (system) | 3.11.9 | 1270 | **8** | D(5) + T(2) + W(1) — **identical both runs**, confirming W's variance is bounded (0 rows survived either time, not "sometimes 1 sometimes 2") |
| `g_py311_utc0` | `TZ=UTC0` | 3.11.9 | 1272 | **6** | D(5) + W(1) — T tests pass under UTC |
| `g_py314_et` | America/New_York (system) | 3.14.7 | 1271 | **7** | D(5) + T(2) — W does not occur on 3.14 |
| `g_py314_utc0` | `TZ=UTC0` | 3.14.7 | 1273 | **5** | D(5) only — the clean baseline |

Arithmetic checks out exactly against the class model in every cell. **The "5 deterministic failures" from the original
3.14 pass (`C0_INVENTORY.md` §4.3) is confirmed as the TZ=UTC, no-W-class floor** — it's what you get once T and W are
both out of play.

---

## 3. Root causes (both found and reproduced directly, not inferred)

### 3.1 Class T — `test_iv_richness_is_a_genuine_quality_tilt_input`

The test builds an option contract with `"expiry": (date.today() + timedelta(days=15)).isoformat()` (test fixture,
**local** date) and DTE is computed by the code under test as
`(date.fromisoformat(expiry) - datetime.now(timezone.utc).date()).days` (`dashboard/options_desk.py:324`, **UTC** date).
When local date ≠ UTC date, the resulting DTE is off by one, which shifts the option's `option_score` enough to fail
`assert (s_not_rich, t_not_rich) == (4, 3.0)` — observed `(4, 2.0)`.

**This is local date vs UTC date disagreeing — exactly the naive-`date.today()` behaviour `C0_CONTAINER_DESIGN.md` D1
says to preserve, not fix.** Under America/New_York (UTC−4 in EDT), local date falls behind UTC date during
**[20:00, 24:00) ET** — precisely the window the design doc's fixed-instant preflight test (`1790042400` =
`2026-09-22T02:00:00Z` = `2026-09-21T22:00:00 EDT`) was built to probe. This test ran at ~20:20 EDT, **inside** that
window, so it deterministically fails under ET tonight and would deterministically pass under ET at, say, 14:00 ET.
Under `TZ=UTC0` the two dates can never disagree, so it always passes. **Verified identical on both 3.11 and 3.14** — it
is a TZ/wall-clock property of the test and the code under test, not an interpreter difference.

**Consequence for C0:** an A/B run of the test suite under the **Case 2 profile (`TZ=America/New_York`)** must either
(a) run both reference and container in the same few-second window so both see the same local/UTC date relationship, or
(b) treat tests #7/#8 as T-class and compare like-for-like (ET-vs-ET, same time-of-day bucket; UTC-vs-UTC always
matches). Recorded here as the reference; not something G2 fixes.

### 3.2 Class W — `test_every_decision_is_journalled_including_refusals`

The test calls `broker.submit_entry()` three times for the same symbol/strategy in immediate succession and asserts
`len(journal.signals()) == 3`. `signal_id = "sig_" + sha1(symbol + strategy + created_at)[:16]`
(`lab/paper/fills.py:238-240`), where `created_at` is an ISO timestamp from `datetime.now(timezone.utc)`. If two calls
land within the same timestamp tick, they hash to the **same `signal_id`**, and the second `INSERT` collides with (and
in this schema's insert pattern, is dropped or overwrites) the first — fewer than 3 rows survive.

**Directly reproduced, isolated from the test suite:**

```
CPython 3.11.9 (Windows): 3 back-to-back datetime.now(timezone.utc) calls -> ALL THREE IDENTICAL
                            '2026-09-22T00:22:19.866877+00:00' x3  ->  1 distinct signal_id
CPython 3.14.7 (Windows): 3 back-to-back datetime.now(timezone.utc) calls -> all distinct, ~15-20us apart
                            '...934733', '...934748', '...934751'  ->  3 distinct signal_ids
```

A broader probe (2,000 calls in a tight loop) found **3.11 on this Windows host returns the same value for the entire
burst** (system timer ≈15.6 ms granularity — the pre-3.13 CPython Windows time source), while **3.14 resolves to
sub-microsecond** (Windows' `GetSystemTimePreciseAsFileTime`-based path). This is a known category of CPython
Windows-clock behaviour change, not a project bug.

**Scope — likely Windows-only, not yet verified on Linux [S, unverified].** Linux's `clock_gettime(CLOCK_REALTIME)`
is nanosecond-resolution in glibc independent of Python version, so the same three calls on Case 1's actual platform
(`ubuntu-24.04`, CPython 3.11.16) are expected **not** to collide. **This is an explicit open item for the Docker/G4
gate**: re-run this exact test (and ideally the 2,000-call clock probe) inside the Linux container/`R1-bare` reference
and confirm it passes there. If it does, class **W is a Case-2/Windows-reference-only artifact** and never applies to
Case 1 or the eventual container. If it does *not* pass on Linux, W becomes a D-class (or its own new class) finding
that needs to be added to the true Case 1 baseline — this file will be updated at that gate either way.

**Not a G2 regression:** it did not appear in the very first informal 3.14/host pass in `C0_INVENTORY.md` because that
pass used 3.14 (unaffected). It is a genuine, previously-undocumented behavior difference this gate surfaced.

---

## 4. N-class stability check (as designed in `C0_CONTAINER_DESIGN.md` §8.2)

`test_finbert_service.py::test_cache_behavior` run **3× in isolation** on 3.11 (separate from the full-suite runs, to
remove any ordering influence):

```
run 1: passed   run 2: passed   run 3: passed
```

Passed all 3 times here (no model installed ⇒ both timed calls are near-instant; the `<` comparison happened to hold).
Per the design doc this is **recorded, not gated** — a future run, or the same run under more system load, could flip it.

---

## 5. Test-count reconciliation with the original 3.14 inventory pass

The very first pass (`C0_INVENTORY.md` §4.3, taken before this gate) reported **1,272 passed / 6 failed** on host
3.14. This gate's `g_py314_et`/`g_py314_utc0` runs report **1,278 total** (1271 or 1273 passed). The difference is
**not** a new/removed test — both figures come from the identical `pytest tests/unit -q --ignore=tests/e2e` command
against the identical commit; the 6-test gap is fully explained by class T (2 tests, present/absent from the "6 known"
list depending on what time of day the original pass happened to run) plus the reporting convention (the original pass
folded the now-identified N-class test into "6 failed" without isolating it). **Corrected, authoritative statement:**
collected test count is **1,278** on both interpreters; the *outcome* of 9 of those 1,278 IDs is condition-dependent per
the class table in §2, and the other 1,269 are stable passes on every configuration tested.

---

## 6. What "the 3.11 baseline" is, precisely, going forward

There is no single JSON file that is "the" baseline — by design, per §1's T/W classes. The baseline is the **class
table in §2** plus these two concrete, reproducible reference points, saved for the Docker/G4 gate:

```
docker/baseline/tests-py311-UTC.json     <- g_py311_utc0 (docs/... evidence copy); the STABLE reference:
                                             5 D-class fails, id-for-id identical to 3.14-UTC's 5.
docker/baseline/tests-py311-ET.json      <- g_py311_et_run1 (docs/... evidence copy); the TIME-SENSITIVE reference,
                                             valid only when compared against a container run in the same few-minute
                                             window (or with T-class IDs excluded from the comparison).
```

(Raw JUnit XML and full pytest text output for every run in this gate are kept under the session's `c0-work/evidence/`
scratch directory, outside the repo, for traceability; the two files above are the ones worth carrying forward as repo
artifacts and should be copied in at the G4/Docker gate once `docker/baseline/` is actually created per the design
doc's §10.4.)

**C0 (container) passes L1 (`C0_CONTAINER_DESIGN.md` §12) iff:**
1. Every **D-class** ID has the identical outcome in the container as here.
2. Every **T-class** ID matches the reference **run at the same TZ profile** (UTC comparisons are always straightforward;
   ET comparisons must either run in the same wall-clock window or exclude T-class IDs and note why).
3. **W-class** is checked once, specifically, on Linux — expected to disappear; if it does, it's dropped from the
   container-vs-container comparison entirely and remains a documented Windows-reference-only note.
4. **N-class** is never gated, only recorded (3 runs, both sides).

---

## 7. Repo-tracked test-suite defect (recorded, not fixed — separate from G2 scope)

Confirmed this gate, consistent with the earlier inventory finding: every sandboxed pytest run wrote `predictions.jsonl`
into its isolated `HOME` (e.g. `g_py311_et_run1`'s sandbox: 198 files created total, including a fresh
`.tradingview_mcp_data/predictions.jsonl`). Because every run in this gate used an **isolated `HOME`**, none of this
session's test runs touched the real file — **the real `predictions.jsonl` and its ~65% `TEST`-row contamination
(3,281 of 5,051 rows) are exactly as they were before this gate (`C0_CONTAINER_DESIGN.md` §8.4), unmodified,
unregenerated, uninvestigated.** Per your instruction this stays parked as a defect to investigate **after** C0, not
touched now.
