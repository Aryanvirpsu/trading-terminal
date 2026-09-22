# C0_DOCKER_G4.md — Docker implementation gate: images, isolated smoke tests

_Gate date 2026-09-22 · builds on `C0_INVENTORY.md`, `C0_CONTAINER_DESIGN.md`, `C0_DEPENDENCY_FREEZE.md`,
`docs/C0_HYGIENE_GATE.md` · branch `c0/hygiene-gate` · repo commits `e4129d4` (docs-only) then this gate's own._

**Scope:** build the smallest possible terminal images, prove L0/L1 mechanics (image builds, exact Case 1 pins,
imports, `config_version`, both TZ profiles, dashboard reachable, scheduler status, state isolated), stop **before**
pointing anything at the real ledger or running full A/B decision equivalence. No `avdi-api`, workers, Redis,
Postgres, or Nautilus. The MCP server is out of scope (E1 stands, untouched).

**Evidence tags:** **[V]** run/verified this session · **[S]** static (read only).

---

## 0. Sequence position

```
90ebd6c  G3 hygiene                                     ✅
e4129d4  docs-only C0 inventory/design commit            ✅
A3 Docker Desktop                                        ✅ approved, started, verified
G4 Docker implementation                                 ✅ this document
isolated L0/L1 smoke validation                           ✅ this document
STOP — no real-ledger A/B in this gate
G5 full A/B equivalence                                  ⛔ not started
```

---

## 1. Docs-only commit (step 1)

`e4129d4` — staged explicitly (`git add C0_INVENTORY.md C0_CONTAINER_DESIGN.md`, nothing else), 2 files, 1342
insertions, 0 deletions. `AGENTS.md`, the two PDFs, and `graphify-out/` were **not** included. Verified via
`git diff --cached --stat` before committing that exactly those two paths were staged.

## 2. Docker Desktop (steps 2–3)

Started via `Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"`; polled `docker info` until the
daemon answered (5m51s cold start). Recorded environment:

| Fact | Value |
|---|---|
| Docker Engine | 29.7.2, API 1.55 |
| Backend | `desktop-linux` (WSL2), kernel `6.18.33.2-microsoft-standard-WSL2` |
| Platform | `linux/amd64` |
| Buildx | v0.36.1-desktop.1 |
| Compose | v5.4.0 |

**Base image — exact Case 1 CPython, not merely close.** `python:3.11.16-slim-bookworm` pulled and pinned by digest
(`sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b`); `python --version` inside it →
**3.11.16**, identical to Case 1's `actions/setup-python@v5` runtime, better than the "3.11.16 target, verify at
build time" the design doc left open — no interpreter delta at all now (Debian bookworm vs. Case 1's Ubuntu 24.04
runner remains a documented, accepted libc/OpenSSL/CA-bundle-only difference, C0_CONTAINER_DESIGN.md §10.7).

## 3. New files (step 4 onward)

| File | Role |
|---|---|
| `docker/Dockerfile.terminal` | multi-stage build: `base → deps → app → {paper, dashboard}`. No `tests` target — out of G4 scope by design (no TESTS row in the acceptance table). |
| `docker/c0_preflight.py` | pure, read-only start-up gate (Python version, TZ + fixed-instant date check, `config_version`, dangerous/tuning env vars, path resolution, no-secrets-in-image scan). Refuses to start on any failure. |
| `docker/entrypoint.sh` | runs the preflight, then `exec "$@"`. |
| `docker/serve_dashboard.py` | the approved D2 wrapper — imports `dashboard/app.py` exactly as `python dashboard/app.py` would, binds `0.0.0.0` instead of the hard-coded `127.0.0.1`. **`dashboard/app.py` itself is untouched** — `git diff --stat -- dashboard/` is empty. |
| `docker/compose.c0.yml` | two profiles (`case1`/`case2`), four services (`avdi-scheduler-*`, `avdi-dashboard-*`), no `tests` service, no bind mounts anywhere. |
| `docker/env/case1.env`, `docker/env/case2.env` | the only per-profile difference: `TZ`, `PAPER_DATA_DIR` (case1 only). |

Container naming follows this gate's own instruction (`avdi-dashboard`, `avdi-scheduler`, MCP kept separate) rather
than the earlier design doc's `dashboard-case1`/`paper-case1` names — same mechanism, renamed.

### 3.1 Two disclosed ignore-pattern interactions

**`.dockerignore`** — `docker/env/*.env` matches the pre-existing `env/` pattern (meant for a Python virtualenv
directory, from G3's audit — confirmed harmless there). This has **zero functional effect**: `env_file:` in Compose
reads files directly off the host disk relative to the compose YAML's own location; it never goes through the
Docker build context at all. Left alone, exactly like the `LICENSE`/`.env.example` precedents from G3 — no hygiene
cleanup performed in this gate.

**`.gitignore`** — the same two files also matched `.gitignore:152`'s `ENV/` rule (case-insensitive on this Windows
checkout, so it catches lowercase `env/` too — another pre-existing virtualenv-exclusion rule, unrelated to Docker).
Unlike the `.dockerignore` case, **this one does matter**: an ignored file is never committed, so leaving it alone
would mean `docker/compose.c0.yml`'s `env_file:` references point at files that exist only on this machine — the
implementation this gate is committing would be broken for anyone else checking out the branch. This isn't a
hygiene concern, it's a completeness concern for the deliverable itself, so both files were **force-added**
(`git add -f`, the same single-file-override mechanism G3 used for `docker/context.manifest`) — not a `.gitignore`
edit, not a blanket `-f`. Contents are non-secret by inspection: each file is 4–5 lines
(`C0_PROFILE`, `TZ`, `PAPER_DATA_DIR` for case1 only, `DASHBOARD_PORT`) — no credential, key, or host-specific path.

---

## 4. Images built (step 4)

| Target | Image | Size | Contains |
|---|---|---|---|
| `paper` | `avdi-scheduler:c0` | 589MB | exactly Case 1's 60 pins + the editable project. **No Flask, no pytest** — matches the real `paper_scheduler.py all` dependency footprint. |
| `dashboard` | `avdi-dashboard:c0` | 595MB | `paper` + the 6-package Flask closure + `docker/serve_dashboard.py`. |

Both run as **`USER tv`** (fixed uid 10001), confirmed via `docker inspect`. `avdi-dashboard:c0` exposes `5057/tcp`
only. No image was pushed anywhere; both exist only in this machine's local Docker image store.

---

## 5. L0/L1 acceptance table

```
IMAGE
  [x] builds                                    — both targets, no source builds needed (all wheels), clean

DEPENDENCIES
  [x] exact Case 1 pins preserved                — §5.1: 60/60 exact match; one disclosed, inert, non-Case-1 addendum

IMPORTS
  [x] application imports                        — §5.2: 19 top-level modules + lab.paper.* (10) + canonical.* (7)
                                                     + tradingview_mcp.core.services.strategy_service — all clean

CONFIG
  [x] cfg-a0eede144e                              — §5.3: both profiles, via preflight AND paper_scheduler.py status

TIME
  [x] Case 1 UTC                                  — §5.3: fixed-instant probe → 2026-09-22 (correct)
  [x] Case 2 America/New_York                     — §5.3: fixed-instant probe → 2026-09-21 (correct, one day earlier —
                                                     the exact T-class boundary from C0_TEST_BASELINE_311.md §3.1)

DASHBOARD
  [x] starts                                      — §5.4: healthy in ~6s (case1), ~9s (case2)
  [x] /                                           — 200, both profiles
  [x] /api/health                                 — 200, both profiles
  [x] reachable from host                         — curl 127.0.0.1:5057 from the Windows host, both profiles —
                                                     proves D2 (0.0.0.0 bind) actually fixes the finding-#7 defect

SCHEDULER
  [x] status command                              — §5.5: both profiles, exit 0, reconciled:true, equity 500.0
  [x] starts in intended mode                      — default CMD is `status` (read-only) — a stray `run` can't place
                                                     an order; `all`/`premarket` must be typed explicitly, as designed

STATE
  [x] isolated                                    — §5.6: no bind mounts anywhere (grep-verified); named volumes +
                                                     tmpfs only
  [x] persists where expected                     — §5.6: Case 2 named volume survives a restart (same file, same
                                                     size); Case 1's non-ledger HOME state does NOT (marker-file
                                                     test — written, container recreated, marker gone); Case 1's
                                                     ledger volume DOES persist across the same recreation
  [x] real host state unchanged                   — §5.7: SHA-256 tripwire over all 7 files in the real
                                                     ~/.tradingview_mcp_data, checked repeatedly through the whole
                                                     gate — identical every time
```

### 5.1 Dependencies — 60/60 pins, one disclosed addendum

`docker run --rm --entrypoint python avdi-scheduler:c0 -m pip freeze` → diffed against
`docker/requirements-c0-case1.txt`: **all 60 named pins present at the exact frozen version**, plus the editable
project (`tradingview-mcp-server==0.7.1`, expected) and **one extra: `packaging==26.3`**.

Traced this precisely rather than waving it away: `packaging==26.3` is present in the **bare, unmodified**
`python:3.11.16-slim-bookworm` base image itself (confirmed: `docker run --rm python:3.11.16-slim-bookworm... pip
freeze` → `packaging==26.3` alone, before any of our layers run) — it ships as part of that base image's own
`setuptools 79.0.1` bootstrap, not something our requirements files installed. It is **never imported by any
application code** (`git grep` for `import packaging` across the whole repo: zero hits) — a build/metadata tool
only. Whether Case 1's `actions/setup-python@v5`-provisioned CPython 3.11.16 also carries a preinstalled `packaging`
is **not directly verifiable** from here — Case 1's own install log only shows the *delta* `pip install -e .`
actually performed, not a full `pip freeze`, so an already-present `packaging` there would be equally invisible in
that log. Recorded as an **open, disclosed, low-risk delta** — not a violation of "exact Case 1 pins preserved" for
any of the 60 *named* packages, and inert by construction (never imported, never affects any code path).

**Correction (2026-09-22, requested before G5): the equivalence contract distinguishes two different things that
"pip freeze" conflates.** `packaging==26.3` means the container's **full `pip freeze` is not literally identical**
to the 60-pin file — that statement in §5 above was too strong. What's actually true, and is the claim G5 relies on:

```
Application dependency closure           Base/bootstrap environment
= the 60 pins in                         = whatever pip/setuptools/wheel bring with
  requirements-c0-case1.txt,             them by default on the base image — not
  exactly, --no-deps throughout           pinned, not part of the freeze, not imported
  (proven: §5.1's own diff)               by any application code (packaging: confirmed
                                           zero `import packaging` anywhere in the repo)
```

G4's claim is **"application dependency closure: exact"**, not **"full environment: exact"** — the base/bootstrap
layer was never in scope for the freeze (`C0_DEPENDENCY_FREEZE.md` §2.3 already excludes "`pip`/`setuptools`/`wheel`
preinstalled on the runner's Python" from what the freeze covers, for the identical reason: Case 1 never reinstalls
them either). `packaging` falls in that same excluded category — it just happens to be visible as a *separate*
top-level `pip freeze` entry on this base image in a way it apparently isn't (or wasn't logged) on Case 1's runner.
Nothing below treats "`pip freeze` matches" as the equivalence bar; the bar is "the 60 named pins match exactly and
nothing outside that closure is ever imported," which is what was actually proven.

### 5.2 Imports — broad matrix, inside the container

Ran inside `avdi-scheduler:c0` (the smaller, no-Flask image, `C0_PROFILE=case1`): 19 individual `lab`/`dashboard`/
`src`-path modules (`decision_engine`, `providers`, `freshness`, `data_quality`, `calibration`, `journal_lab`,
`strategy_lab`, `run_lab`, `market_regime`, `scanner`, `research`, `symbol_snapshot`, `sector_map`, `options_desk`,
`security_master`, `scan_cohort`, `strategy_store`, `tracker`, `sector_lookup`) — **19/19 clean**. Plus the full
`lab.paper` package (10 modules: `broker, config, db, fills, journal, options_shadow, report, risk, strategies,
workflow`) and the full `canonical` package (7 modules) and `tradingview_mcp.core.services.strategy_service` — **all
clean, zero import errors**.

### 5.3 Config + Time — preflight output, both profiles, plus two negative controls

```
case1  C0 PREFLIGHT: {'preflight': 'PASS', 'python': '3.11.16', 'profile': 'case1', 'tz': 'UTC',
                       'fixed_instant_date': '2026-09-22', 'config_version': 'cfg-a0eede144e',
                       'db_path': '/data/case1/paper/robinhood_500_baseline.db',
                       'state_dir': '/home/tv/.tradingview_mcp_data', 'pip_freeze_sha256_16': '25e09283401aeffe'}

case2  C0 PREFLIGHT: {'preflight': 'PASS', 'python': '3.11.16', 'profile': 'case2', 'tz': 'America/New_York',
                       'fixed_instant_date': '2026-09-21', 'config_version': 'cfg-a0eede144e',
                       'db_path': '/home/tv/.tradingview_mcp_data/paper/robinhood_500_baseline.db',
                       'state_dir': '/home/tv/.tradingview_mcp_data', 'pip_freeze_sha256_16': '25e09283401aeffe'}
```

The `fixed_instant_date` difference (2026-09-22 vs. 2026-09-21 for the **identical instant**,
`1790042400` = `2026-09-22T02:00:00Z`) is exactly the C0_CONTAINER_DESIGN.md §7.3 probe working as designed — proof
the container actually honours `TZ`, not just that the env var is set.

**Negative controls (both correctly refuse to start):**
```
TZ=America/New_York under C0_PROFILE=case1  → "C0 PREFLIGHT FAILED: profile 'case1' expects TZ='UTC', got TZ='America/New_York'"
BROKER_PROVIDER=robinhood                   → "C0 PREFLIGHT FAILED: BROKER_PROVIDER='robinhood' — must be 'none' or unset for C0"
```

`automation/paper_scheduler.py status` independently reports the same `config_version: "cfg-a0eede144e"` and
`schema_version: 2` through the real CLI path (§5.5), not just the preflight's own check — two independent
confirmations.

### 5.4 Dashboard — starts, reachable from the host

| Profile | Healthy after | `GET /` | `GET /api/health` | `GET /api/paper/account` |
|---|---|---|---|---|
| case1 | ~6s (2 healthcheck ticks) | 200 | 200 | 200 |
| case2 | ~9s (3 healthcheck ticks) | 200 | 200 | 200 |

Also checked `/api/scheduler`, `/api/paper/{performance,signals,options_shadow}`, `/api/providers` — all 200. The
container log shows `* Running on all addresses (0.0.0.0)` and `* Running on http://172.19.0.2:5057` — confirms D2's
`host="0.0.0.0"` actually took effect (the original `dashboard/app.py:1765` hard-codes `127.0.0.1`, which would have
been unreachable from the host entirely — this is the inventory's finding #7, now concretely fixed by the wrapper,
without editing `dashboard/app.py`).

### 5.5 Scheduler — status, both profiles, via Compose

```
docker compose -f docker/compose.c0.yml -p c0-case1 --profile case1 run --rm avdi-scheduler-case1
  → {"account": {"equity": 500.0, "cash": 500.0, "open_positions": 0, ...},
     "reconciliation": {"reconciled": true, "delta": 0.0}, "schema_version": 2,
     "config_version": "cfg-a0eede144e"}   exit 0

docker compose -f docker/compose.c0.yml -p c0-case2 --profile case2 run --rm avdi-scheduler-case2
  → identical shape, same values, exit 0
```

Both created a **fresh, empty $500 ledger** in their respective volumes (never the real one — §5.7). Default command
is `status` (read-only); `docker compose run avdi-scheduler-case1 python automation/paper_scheduler.py premarket
--dry-run` (or `all`) would be needed to exercise anything beyond a status read, and neither was run in this gate.

### 5.6 State — isolation, persistence, ephemerality

* **No bind mounts anywhere** — `docker/compose.c0.yml` uses only named volumes (`c0_case1_ledger`, `c0_case1_out`,
  `c0_case2_state`, `c0_case2_out`) and one `tmpfs` mount (Case 1's non-ledger HOME state); grep-confirmed no host
  path appears as a volume source.
* **Case 2 persistence, proven, not assumed:** stopped and restarted `avdi-dashboard-case2` — the ledger file's
  mtime and size were unchanged after restart (same file, not recreated).
* **Case 1 ephemerality, proven with a marker file, not just reasoned about:** wrote
  `/home/tv/.tradingview_mcp_data/EPHEMERAL_MARKER.txt` inside the running `avdi-dashboard-case1` container,
  force-recreated the container (`up -d --force-recreate`, same named volumes reattached), then checked for the
  marker: **absent** ("No such file or directory") — the tmpfs was genuinely destroyed and rebuilt, not just
  visually similar. In the same recreation, `/data/case1/paper/robinhood_500_baseline.db` (the named-volume ledger)
  kept its **original mtime and size** — proving the ledger, and only the ledger, survives for the Case 1 profile,
  exactly matching K2 ("Case 1 only persists `paper/`").
* **tmpfs permissions:** mounted `mode=0o1777` (sticky, world-writable) specifically so the non-root `uid 10001`
  process can write to a root-owned tmpfs mountpoint — verified with a direct write test inside the container
  (`touch` succeeded) before relying on it for the ephemerality test above. This resolves the open caveat
  `C0_CONTAINER_DESIGN.md` §6 flagged ("the exact Compose short-form `tmpfs:` uid/gid syntax... resolve at G4").
* **Secrets/state scan of both images** (`find` for `.env*`, `*.db`, `*.pickle`, `*token*.json`, `*session*.json`,
  `*credential*`, `*secret*`, excluding `/proc`): only Python-stdlib/`mcp`-library files matched by name
  (`lib2to3`'s grammar `.pickle` tables, `mcp`'s OAuth-flow source file literally named
  `client_credentials.py`, stdlib `secrets.py`) — **no actual secret or state file baked into either image**.
  `docker history --no-trunc` grepped for `password|secret|token|api_key` in build args/env: empty.
* State dirs (`/home/tv/.tradingview_mcp_data`, `/data/case1/paper`, `/out`) confirmed **empty**, correctly owned by
  `tv:tv`, before any volume is attached — proving a fresh volume will inherit clean ownership (design doc §5.4's
  mechanism, now verified rather than assumed).

### 5.7 Real host state — untouched, checked repeatedly

SHA-256 fingerprint of all 7 files under the real `~/.tradingview_mcp_data` (the same tripwire from every earlier
C0 gate), re-checked **four separate times** across this gate — before any container ran, after the first dashboard
session, after the persistence/ephemerality tests, and after final teardown:

```
real-state files: start=7 now=7 EQUAL=True     (× 4, identical result every time)
```

No command in this gate ever referenced that path except in comments/documentation.

---

## 6. A finding volunteered, not engineered toward (per your instruction to keep W/T/E1/OPT alive)

Re-ran G2's exact W-class reproduction snippet **inside the Linux container**:

```
3 back-to-back datetime.now(timezone.utc) calls:
  ['2026-09-22T23:15:09.797096+00:00', '...797121+00:00', '...797125+00:00']
  distinct timestamps: 3 / 3   distinct signal_ids: 3 / 3
  time.get_clock_info('time').resolution = 1e-09          (vs. Windows/3.11's 0.015625 in G2)
  2000-call burst: 715 distinct timestamps (vs. Windows/3.11's 1 in G2)
```

**The signal_id collision does not reproduce on Linux.** This confirms the C0_TEST_BASELINE_311.md §3.2 hypothesis
("likely Windows-only, not yet verified on Linux") — Linux's nanosecond clock resolution makes the collision
structurally impossible here, exactly as predicted, not "fixed." This is now evidence, not a hypothesis: **W-class
is a Case-2/Windows-reference-only artifact and does not apply to Case 1 or this container.** Recorded, not acted
on — no test was run in a container in this gate (that's explicitly out of scope, §0), so this is a targeted,
narrow observation about the *mechanism*, not a claim about the full test suite's behaviour in Docker.

**E1 (MCP `mcp.server.fastmcp` import failure), T-class (TZ/date-boundary tests), and the optional
torch/transformers failures were not touched, not run, and not "fixed" in this gate** — the MCP image was not built
at all (out of scope per your container-boundary list), and no test suite was executed inside a container.

---

## 7. What is explicitly still open

* **No A/B against real or copied Case 2 state** — every run in this gate used a fresh, empty volume. The
  byte-for-byte copy procedure (`C0_CONTAINER_DESIGN.md` §11) is G5's job, not this gate's.
* **No `tests` image/target** — pytest-in-Docker validation is deferred; the acceptance table above has no TESTS
  row by design.
* **MCP server** — E1–E5 stand exactly as recorded in `C0_CONTAINER_DESIGN.md` §1.4; not rebuilt, not repaired.
* **`packaging==26.3` provenance in the real Case 1 runner** — genuinely unconfirmed (§5.1); flagged for anyone who
  later gets a chance to inspect a live Case 1 runner's full `pip freeze`.
* **The disclosed `env/` `.dockerignore` collision** (§3.1) — harmless, not fixed.
* **Images not pushed anywhere** — local Docker image store only.
* **Volumes left on disk** (`c0-case1_c0_case1_ledger`, `c0-case1_c0_case1_out`, `c0-case2_c0_case2_state`,
  `c0-case2_c0_case2_out`) — all fresh/synthetic, none derived from real state; not pruned, since G5 may want to
  reuse the harness that creates them.

**Next:** G5 full A/B decision equivalence, per `C0_CONTAINER_DESIGN.md` §11–§12, starting from a byte-for-byte copy
of the real Case 2 ledger — not started in this gate.
