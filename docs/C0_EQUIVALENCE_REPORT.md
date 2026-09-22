# C0_EQUIVALENCE_REPORT.md — G5 behavioral A/B equivalence

_Gate date 2026-09-22 · builds on `C0_INVENTORY.md`, `C0_CONTAINER_DESIGN.md`, `C0_DEPENDENCY_FREEZE.md`,
`docs/C0_TEST_BASELINE_311.md`, `docs/C0_HYGIENE_GATE.md`, `docs/C0_DOCKER_G4.md` · branch `c0/hygiene-gate`._

**Scope:** prove containerization did not change the trading system's behavior, in five layers (G5-A through G5-E),
using only fresh synthetic state or byte-for-byte **copies** of the real Case 2 ledger — the real
`~/.tradingview_mcp_data` was never mounted, opened, or written by anything in this gate, and host and container
never operated on the same copy. Stops before any AWS/cloud work.

**Evidence tags:** **[V]** run/verified this session · **[S]** static (read only).

**Bottom line:** across 4 comparison layers and roughly 5,120 individual field/test comparisons, **zero unexplained,
economically meaningful differences were found.** Every difference observed falls into one of the three non-EXACT
buckets below, each with a concrete, traced cause. One process mistake occurred and was caught and corrected — see
§0.1.

```
EXACT                        — identical, no normalization needed
EXPECTED PLATFORM DELTA      — traced to TZ/wall-clock, tooling availability, or "when this ran" metadata
KNOWN PRE-EXISTING DEFECT    — traced to the already-documented W-class Windows/CPython-3.11 clock issue
UNEXPLAINED                  — none found
```

---

## 0. What "host reference" means in this gate, and one mistake made and fixed

Per `C0_CONTAINER_DESIGN.md` §2: the **Windows host** (this machine, CPython 3.11.9 from the G2 venvs
`c0-work/venv-case1` / `c0-work/venv-tests`) is the legitimate reference for the **Case 2** profile — it already
runs on America/New_York system time, no `TZ` override needed. For **Case 1** (UTC), the host was driven with
`TZ=UTC0` (a POSIX-style zone spec Windows' C runtime actually honours — confirmed empirically in G2: `datetime`
calls under `TZ=UTC0` return true-UTC results). `America/New_York` as a literal `TZ` **env var** is not reliably
honoured by Windows' CRT (it expects a POSIX offset spec, not an IANA name) — irrelevant here since the host's own
system zone already *is* America/New_York, so no override was ever needed for that leg.

### 0.1 Disclosed mistake: an accidental write to a tracked file, caught and reverted

Running the host reference's real `automation/paper_scheduler.py report 2026-09-09` (G5-C, §3) from inside the
repo checkout wrote its output to the **tracked** `PAPER_DAILY_REPORT.md` at the repo root — exactly as the
inventory always said it would (`C0_INVENTORY.md` §6.2: the script writes `_ROOT/PAPER_DAILY_REPORT.md`,
`_ROOT` = two directories up from wherever `automation/paper_scheduler.py` is loaded from). Because the host
reference legitimately runs the **real, checked-out** script (not a copy), `_ROOT` resolved to the actual repo,
overwriting the file `git diff` last showed committed with 2026-09-14's real Case 1 data. **Caught via a routine
pre-staging `git status` review**, confirmed as exactly this one file, and reverted with `git checkout --
PAPER_DAILY_REPORT.md` before anything was staged. `git diff PAPER_DAILY_REPORT.md` is empty; the file is back to
its last-committed content. No other tracked file was touched by any G5 command. This is stated plainly rather than
silently fixed, per the standing instruction to report outcomes faithfully.

---

## 1. G5-A — pure deterministic fixtures: the full unit-test suite, host vs. container, both profiles

Built a new `tests` image target (`docker/Dockerfile.terminal`, `avdi-tests:c0` — `paper` + the pytest closure +
`tests/`; needed a per-Dockerfile ignore file, `docker/Dockerfile.terminal.dockerignore`, since the root
`.dockerignore`'s `tests/` exclusion — correct for the MCP image — would otherwise block this target too; confirmed
by testing the failure mode first, then confirming BuildKit picks the file up when it's named after the Dockerfile
and placed beside it). Ran `pytest tests/unit -q --ignore=tests/e2e` **inside the container**, both profiles, and
diffed the resulting per-test-ID outcomes against the **already-recorded G2 host baselines**
(`docs/C0_TEST_BASELINE_311.md`) — not a fresh host run, reusing exactly what that gate already proved under known
conditions (D3: same CPython 3.11.x line; now upgraded to the *exact* 3.11.16 in the container).

### 1.1 Results

| Comparison | Container non-pass | Host baseline non-pass | Same IDs collected |
|---|---|---|---|
| Case 1 (container `TZ=UTC`) vs. host `g_py311_utc0` (`TZ=UTC0`) | 6 | 6 | 1278/1278 [V] |
| Case 2 (container `TZ=America/New_York`, run ~19:2x ET) vs. host `g_py311_et_run1` (ET, run ~20:19 ET) | 6 | 8 | 1278/1278 [V] |

### 1.2 Every non-pass ID, classified

| Test ID | Class | Container (both profiles) | Host baseline | Classification |
|---|---|---|---|---|
| `test_canonical_v11_invariants.py::test_oi_policy_has_no_overlapping_hard_and_soft_ranges` | D | fail | fail | **EXACT** — intentionally red by design |
| `test_canonical_v11_invariants.py::test_contract_quality_identical_across_pipelines` | D | fail | fail | **EXACT** — intentionally red by design |
| `test_finbert_sentiment.py::test_transformers_available` | D | fail | fail | **EXACT** — `transformers` correctly excluded from the freeze |
| `test_finbert_service.py::test_available` | D | fail | fail | **EXACT** — same |
| `test_finbert_service.py::test_score_force` | D | fail | fail | **EXACT** — same |
| `test_finbert_service.py::test_cache_behavior` | N | pass | pass (this baseline) | not gated (nondeterministic by design) |
| `test_pipeline2_canonical_migration.py::test_iv_richness_is_a_genuine_quality_tilt_input` | T | pass (out of window) | fail (host `et_run1`, in-window) / absent (`utc0`) | **EXPECTED PLATFORM DELTA** — pure function of (TZ, wall-clock instant); mechanism proven identical (§4 of `C0_DOCKER_G4.md`'s fixed-instant probe); this pair of runs happened at different real times of day |
| `test_pipeline3_canonical_migration.py::test_pipeline2_tests_unchanged` | T (derived) | pass | fail (`et_run1`) / absent (`utc0`) | **EXPECTED PLATFORM DELTA** — same root cause, one level up (subprocess re-runs the test above) |
| `test_paper_trading.py::test_every_decision_is_journalled_including_refusals` | W | **pass, both profiles** | fail, both TZ | **KNOWN PRE-EXISTING DEFECT (W-class)** — confirmed does not reproduce on Linux, exactly as `C0_TEST_BASELINE_311.md` §3.2 and `C0_DOCKER_G4.md` §6 predicted; now proven on the *full* test, not just the isolated clock snippet |
| `test_pipeline3_canonical_migration.py::test_sector_funnel_unchanged` | new | **fail, both profiles** | pass | **EXPECTED PLATFORM DELTA** — traced (§1.3): `git` is not installed in the minimal image, by design |

**No ID appeared as a failure in the container that isn't explained by one of the three buckets above. No D-class
ID changed outcome. No new, unexplained failure exists.**

### 1.3 The one new finding: `git` absent from the minimal image

`test_sector_funnel_unchanged` shells out to `git diff --stat -- lab/paper/strategies.py` to verify a source file
is unchanged since a past migration — a repo-history meta-check, not application behavior. `docker run --rm
--entrypoint sh avdi-tests:c0 -c "which git || echo 'git NOT installed'"` → **`git NOT installed`** [V]. This is
correct, not a gap: the image is deliberately minimal (`apt-get install tzdata ca-certificates`, nothing else —
Case 1's own `pip install -e .` needs no compiler and no `git` at runtime; `git` was only ever needed by the CI
runner that *checks out* the repo, not by anything the pipeline executes). **Not fixed** — installing `git` just to
turn one meta-test green would grow the image beyond Case 1's real footprint for a test that checks nothing about
trading behavior.

### 1.4 T-class and W-class as designed probes — used, not engineered around

Per instruction, neither was touched:
* **T-class** behaved exactly per its profile's timezone and the real wall-clock at run time — this is the
  *correct* behavior being preserved, not a bug to fix. The underlying mechanism (naive `date.today()` vs. UTC-based
  DTE math) is identical in container and host; only the *moment* each was run differed.
* **W-class** was proven, on the full test this time (not just the isolated reproduction), to be Windows/CPython-3.11
  only — it never touched the container.

---

## 2. G5-B — fresh synthetic state: decisions, DB rows, reports, `config_version`, host vs. container

`docker/tools/g5_fixture_scenario.py` (new, committed): a network-free, deterministic scenario — one MONITOR, one
REJECT, one TRADEABLE signal for the same symbol/strategy via `broker.submit_entry()` (the exact `_result()`/
`_quote()` shapes already audited in `tests/unit/test_paper_trading.py`), then closes the TRADEABLE position at its
target via `broker.manage_open_positions()`, then dumps `journal.signals()`, `broker.account()`, `report.daily()`,
`config_version()`, `schema_version()`, and the audit event list as one normalized JSON object. **Deliberately does
not** reuse the test file's `fresh_db` fixture, because that fixture sets ~15 `PAPER_*` env vars — which would move
`config_version()` away from the K3 baseline `cfg-a0eede144e`. This script runs under the **default** config, exactly
like Case 1/Case 2 actually do.

Normalization: only `created_at`/`updated_at`/`closed_at`/`filled_at`/`outcome_at`/`recorded_at`/`generated_at`/`at`
and the four ID fields (`signal_id`, `order_id`, `position_id`, `fill_id` — all literally time-derived, per
`lab/paper/fills.py:238-240`) are replaced with fixed placeholders. **Every** economic field — symbol, strategy,
action, gates, entry/stop/target, quantity, quality, `config_version`, `schema_version`, P&L, reconciliation — is
compared verbatim.

### 2.1 Results

| Comparison | Total diffs | Unexplained |
|---|---|---|
| **Case 2**: host (system ET) vs. container (`TZ=America/New_York`) | **0** | 0 |
| **Case 1**: host (`TZ=UTC0`) vs. container (`TZ=UTC`) | 11 (all traced to one cause, §2.2) | 0 |
| Container case1 vs. container case2 (same image, cross-profile sanity check) | 1 (`.label`, the run name I passed in — intentionally different) | 0 |

**The Case 2 comparison is a perfect, unpolluted, zero-diff match** — the strongest single result in this gate.

### 2.2 The Case 1 host diffs are W-class again, reproduced and confirmed, not a new bug

Host's case1 run journals only **2** signals (`TRADEABLE`, `MONITOR`) where 3 were submitted — the `REJECT` call
vanished. Re-ran the identical script **3 more times** on host under `TZ=UTC0`: **every run** lost exactly the
`REJECT` signal. Traced directly: `TZ=UTC0 python -c "..."` running 3 back-to-back `datetime.now(timezone.utc)`
calls returns **the identical timestamp** on this host (clock resolution `0.015625`s), and
`lab/paper/journal.py`'s `INSERT OR REPLACE INTO signals(signal_id, ...)` means a later call with a colliding
`signal_id` **silently overwrites** an earlier one — the same mechanism as W-class, applied to real code (not just
the isolated reproduction snippet from `C0_TEST_BASELINE_311.md`). **The container's own case1 run correctly
captured all 3 signals** (`['TRADEABLE', 'REJECT', 'MONITOR']`) [V], and its content is internally consistent with
its own case2 run (§2.1's third row, 0 unexpected diffs) — so the discrepancy is entirely attributable to the host
measurement being corrupted by an already-known, Windows-only defect, not to any container behavior. **Classified
as KNOWN PRE-EXISTING DEFECT (W-class)** — consistent with §1's finding, now observed in a second, independent
context. Not "fixed" — per instruction, W-class stays exactly as weird as it already was.

---

## 3. G5-C — copied Case 2 state: the real ledger's data, computed twice, never shared

**The core proof.** Per your diagram: real state → read-only fingerprint → two **independent** copies (never the
same copy touched by both sides).

```
V1  fingerprint real ~/.tradingview_mcp_data/paper/robinhood_500_baseline.db
      sha256 = 50c8fab3c920d527a166e5f2a59f54fdf9c2f59c98ec328244eac45915e05099   [V]
V2  copy A (host)      -> c0-work/golden/hostrun/robinhood_500_baseline.db       sha256 identical  [V]
    copy B (container) -> c0-work/golden/containerrun/robinhood_500_baseline.db  sha256 identical  [V]
V3  real file re-fingerprinted immediately after copying: unchanged                                [V]
```

Ran `status`, `report 2026-09-09` (the ledger's most complete date — 5 signals, 1 equity snapshot), and
`performance` — all **pure reads** (`report.py`/`performance()` contain zero `INSERT`/`UPDATE` statements [S];
the only write either side performs is the one-time, expected, allowed v1→v2 schema migration on first connect)
— on copy A from the **host**, on copy B from the **container** (bind-mounted **read-write**, since the migration
needs to write; copy B, not the real file). Migration ran identically on both copies: `schema_version` 1 → **2** on
each, `signals` row count unchanged at **15** on each [V].

### 3.1 Results — normalized diff, full JSON output of all three commands

| Command | Total diffs | What differed |
|---|---|---|
| `status` | **0** | — |
| `report 2026-09-09` | 1 | `.generated_at` only (`2026-09-22T23:31:30...` vs `...23:32:11...` — literally when each command happened to run, ~40s apart) |
| `performance` | 1 | `.generated_at` only (same field, same reason) |

**Every economically meaningful field matched exactly**: equity, cash, realized/unrealized P&L, drawdown, all 15
signals' actions/gates/quality/entry/stop/target, the reconciliation delta (`0.0` both sides), `config_version`,
`schema_version`, gate-outcome breakdowns, blocked-winner counts, calibration/quality-bucket stats. The only
non-matching field on either command is a **meta timestamp that records when the report was generated**, not
anything the trading logic computed — this is the textbook case for the normalization rule you set ("normalize
only what's already established as a platform artifact... never decision values, prices, gates, provenance,
confidence, signal IDs, config/engine versions, fills, risk results").

### 3.2 Real state — final check for this leg

Real `~/.tradingview_mcp_data` SHA-256 tripwire, checked before making the copies, immediately after, and again
after this leg completed: **unchanged, all 3 checks** [V] (§6 has the full, gate-spanning record).

---

## 4. G5-D — Case 1 reproduction: live data, one informative run, structural comparison

Per the design doc's own L4 characterization, this leg is **not deterministic** (live market data) and is not
gated pass/fail — it is a single, informative run, compared **structurally** (section/field names, report shape,
`config_version`) against a real historical Case 1 report, never value-for-value (which is impossible without a
market-data time machine).

Reference: `reports/paper/2026-09-21.md` pulled from `origin/main` [V] — a real Case 1 report from an actual cloud
run. Test: `avdi-scheduler-case1` (fresh, empty volume — torn down and recreated first) ran
`automation/paper_scheduler.py all` for real, hitting live market data.

### 4.1 Structural comparison

| Section | Real Case 1 (2026-09-21) | Container (2026-09-22, fresh $500, live data) | Match |
|---|---|---|---|
| Header format | `# PAPER_DAILY_REPORT.md — <date>` + `_Generated <ts> · config \`<ver>\`_` | identical format | ✅ |
| `## Account` table | 10 rows, same metric names | identical 10 rows, same names | ✅ |
| `## Signals` | TRADEABLE/MONITOR/REJECT/Entered/Exited | identical fields | ✅ |
| `## Gate failures today` | conditional table | present (3 gates this run) | ✅ |
| `## Execution quality` | 4 fields | identical 4 fields | ✅ |
| `## Integrity` | reconciliation line | identical, `YES (delta 0.0)` | ✅ |
| `## What the engine got wrong` | present (real run had a blocked winner) | **absent** — correctly conditional, this run had none | ✅ (conditional section behaves identically) |
| `## Gate cost` | present | present | ✅ |
| `config_version` | `cfg-a0eede144e` | `cfg-a0eede144e` | ✅ |

**Values legitimately differ** (equity $504.66 vs. $500.00, different candidates, different gate-failure counts) —
**expected**: different calendar day, different live market conditions, a fresh $500 ledger vs. Case 1's real
accumulated history. This is not compared value-for-value and is not a gating check — it confirms the container,
run for real, produces output **structurally indistinguishable** from a genuine Case 1 cloud run.

Reconciliation: `true`, `equity: 500.0`, `0 orders processed` (no candidate passed every gate this run — same shape
as several real Case 1 days, e.g. 2026-09-10/2026-09-14/2026-09-15/2026-09-18 in the historical record).

---

## 5. G5-E — classification summary (every difference found, across all four layers)

| # | Difference | Layer | Classification | Traced cause |
|---|---|---|---|---|
| 1 | `test_iv_richness_is_a_genuine_quality_tilt_input` + its meta-test | G5-A | **EXPECTED PLATFORM DELTA** | TZ/wall-clock-instant, mechanism proven identical |
| 2 | `test_every_decision_is_journalled_including_refusals` | G5-A, G5-B | **KNOWN PRE-EXISTING DEFECT** | W-class, Windows/CPython-3.11 clock resolution, confirmed absent on Linux in two independent contexts |
| 3 | `test_sector_funnel_unchanged` | G5-A | **EXPECTED PLATFORM DELTA** | `git` deliberately absent from the minimal runtime image |
| 4 | G5-C `report`/`performance` `.generated_at` | G5-C | **EXPECTED PLATFORM DELTA** | wall-clock of when each command happened to run |
| 5 | G5-D value differences (equity, candidates, gate counts) | G5-D | **EXPECTED PLATFORM DELTA** (non-deterministic leg, not gated) | different day, live market data, fresh vs. accumulated ledger |
| — | 5 D-class intentional/optional-dependency test failures | G5-A | **EXACT** (identical both sides) | by design (2) / freeze correctly excludes torch/transformers (3) |
| — | Everything else across all four layers | all | **EXACT** | — |

**UNEXPLAINED: none.**

### 5.1 Process note (not a system finding)

One accidental write to a tracked file (`PAPER_DAILY_REPORT.md`) occurred during G5-C's host-reference run and was
caught and reverted before staging (§0.1). This reflects a gap in this gate's own isolation of the *host* leg (the
container leg was never at risk — it writes only inside its own filesystem/volumes), not a defect in the
application or the containerization. Recorded for the same reason everything else in this document is: faithful
reporting, not because it changed any result.

---

## 6. Real-state safety — the complete record for this gate

| Checkpoint | Real `~/.tradingview_mcp_data` files | SHA-256 set |
|---|---|---|
| Start of G5 (= end of G4) | 7 | recorded |
| Before making the G5-C golden copies | 7 | **identical** [V] |
| Immediately after copying (copy-verification step) | 7 | **identical** [V] |
| After G5-C's read commands completed | 7 | **identical** [V] |
| After G5-D's live-data run | 7 | **identical** [V] |
| End of gate | 7 | **identical** [V] |

Five separate checks, one file set, zero changes. The two golden copies
(`c0-work/golden/{hostrun,containerrun}/robinhood_500_baseline.db`) live outside the repo, outside
`~/.tradingview_mcp_data`, and were never written back to the real location.

---

## 7. New artifacts from this gate

| File | Role |
|---|---|
| `docker/Dockerfile.terminal` (modified) | added the `tests` target |
| `docker/Dockerfile.terminal.dockerignore` (new) | lets the `tests` target see `tests/` without loosening the root `.dockerignore` |
| `docker/tools/g5_fixture_scenario.py` (new) | the G5-B deterministic, network-free scenario harness — reusable for future A/B rounds |
| `docker/baseline/tests-{host-py311-utc0,host-py311-et,container-case1-utc,container-case2-et}.json` (new) | the four per-test-ID result sets this report's §1 is built from |
| `docker/baseline/image-facts.txt` (new) | image/base/interpreter provenance snapshot |
| `docs/C0_EQUIVALENCE_REPORT.md` (this file) | — |

Corrected in the same commit: `docs/C0_DOCKER_G4.md` §5.1 (the application-closure vs. base/bootstrap-environment
distinction you asked for before G5 — done prior to running any of the above).

---

## 8. Recommendation

Every difference this gate found has a traced, documented, non-economically-meaningful cause. **No unexplained
difference exists between the host/reference terminal and the containerized one**, across unit-test behavior
(G5-A), synthetic decision/DB/report generation (G5-B), real historical-data computation (G5-C, the closest thing
to a direct "did containerization change a real trading number" test — answer: no, to the last cent, except a
generation timestamp), and a live structural run (G5-D).

Per your own stated bar ("if G5 comes back with no unexplained economically meaningful differences, then I would
call C0 complete"), **this gate's evidence supports declaring C0 complete** — the recommendation, not a unilateral
decision: merging `c0/hygiene-gate` into `main` and moving to C1 is yours to make. Nothing in this gate touched
`main`, pushed anything, or went near AWS/cloud deployment.
