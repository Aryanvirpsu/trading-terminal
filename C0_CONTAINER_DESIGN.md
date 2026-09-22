# C0_CONTAINER_DESIGN.md — containerizing the terminal without changing what it does

_Design date 2026-09-21 · builds on `C0_INVENTORY.md` · repo `main` @ `223f5ff` · **design only — no Dockerfile, Compose file, constraints file, or script has been created.**_

**Evidence tags:** **[V]** verified by running/reading live output this session · **[S]** static (read from code/config) ·
**[U]** unverified / needs a later gate. Every `[V]` run used a scratch `HOME`; the real `~/.tradingview_mcp_data` was
never written.

---

## 1. The C0 Reference Contract (locked)

### 1.1 Decisions

| # | Decision | Consequence for the design |
|---|---|---|
| **D1** | **TZ:** Case 1 profile = `UTC` (what Case 1 actually runs under). Case 2 profile = `America/New_York` (current host behaviour). The naive-date behaviour (`dt.date.today()` in `lab/paper/*`) is **not** fixed; it is preserved and documented. | `TZ` is a per-profile setting, never baked into the image. A boundary test proves the date-roll behaviour of each profile (§12, L0). |
| **D2** | **Dashboard binding:** a separate `docker/serve_dashboard.py` imports the existing Flask app and binds `0.0.0.0`. `dashboard/app.py` is **not edited** for Docker. | Wrapper is removable, testable, and proven not to change terminal logic (§9.3). |
| **D3** | **Reference interpreter = CPython 3.11** (Case 1 ran **3.11.16** on all 8 real runs **[V]**). 3.14 is a compatibility datapoint, never the baseline. | Image pins `3.11.16`. Host 3.14 results are reported, non-gating. |
| **D4** | **Dependency freeze** derived from the actual Case 1 resolved environment, turned into an explicit repo artifact (pinned constraints), then proven to reproduce the Case 1 baseline. The workflow log is *not* the permanent source of truth. | §10. |

### 1.2 Constraints carried into every section

| # | Constraint | Where enforced |
|---|---|---|
| **K1** | The real Case 2 state is **never mounted**. Validation runs on a **byte-for-byte copy** ("golden copy"). | §5, §11, blocker B3 |
| **K2** | Case 1 persistence is **not improved**. Only `paper/` persists; everything else the pipeline writes to `$HOME` is discarded per run. | §5.2, §6 |
| **K3** | Under the default Case 1 environment `config_version` **must equal `cfg-a0eede144e`**. Any other value is an equivalence failure. | §7.3 preflight, §12 L0 |
| **K4** | The **MCP server is treated separately.** Its existing defects are recorded, not repaired, and never block the dashboard/paper containers. | §1.4, §14 |

### 1.3 Dependency authority hierarchy

```
C0 runtime truth
    │
    ├── Case 1 resolved CPython 3.11.16 environment      (run 35640912640, 2026-09-21; 8-run drift recorded)
    │        ↓
    │   C0 pinned dependency manifest                    (docker/constraints/*.txt — created at the freeze gate)
    │
    ├── pyproject.toml metadata (open ranges)
    │        ↓
    │   reconcile / document differences                 (§10.3)
    │
    └── uv.lock                                          (stale: no yfinance/flask/holidays; mcp 1.12.4)
             ↓
        NOT authoritative
```

### 1.4 Existing MCP defects — recorded, not fixed (K4)

| ID | Defect | Evidence |
|---|---|---|
| E1 | `mcp>=1.12.0` resolves to **2.x**, which removed `mcp.server.fastmcp` → `server.py` fails at import | **[V]** host mcp 2.1.1; Case 1 resolves **2.2.0** on every run **[V]** |
| E2 | Pinned `mcp 1.12.4` fails on Python 3.14 (`issubclass() arg 1 must be a class`) | **[V]** |
| E3 | Dockerfile `HEALTHCHECK` calls `/health`; `server.py` defines no such route | **[S]** (could not run: E1) |
| E4 | `publish-image.yml` builds but never runs the image, so E1–E3 publish green | **[S]** |
| E5 | `docker-compose.yml` names `atilaahmet/tradingview-mcp:latest` (upstream namespace) | **[S]** |

Note `mcp 2.2.0` **is** in the Case 1 resolved set and therefore will be in the C0 `paper`/`dashboard` images — installed,
never imported **[V: dashboard runs on mcp 2.x; Case 1 paper runs green with it]**. That is faithful to Case 1 and is not a
fix. The root `Dockerfile`, root `docker-compose.yml`, and `publish-image.yml` stay untouched.

---

## 2. Reference environments (what "A" and "B" actually are)

The user-level contract is `A == B`. Concretely:

| ID | Role | Runtime | TZ | Deps | State | Status |
|---|---|---|---|---|---|---|
| **R1** | Case 1 **reference** | Linux, CPython 3.11.16. Three evidence forms below | `UTC` | Case 1 manifest | empty (first-run) golden, or a Case 1 ledger copy | see below |
| **R2** | Case 2 **reference** | **Windows host**, CPython **3.11.x venv**, system TZ | `America/New_York` (system) | Case 1 manifest + Flask/pytest closure at **host** versions | copy of Case 2 golden, reached via `USERPROFILE`/`HOME` redirect | **needs A1** (only 3.14 installed) |
| **C1** | Case 1 **container** | C0 image, `paper`/`dashboard`/`tests` targets | `UTC` | identical to manifest | named volume seeded from the same golden as R1 | later gate |
| **C2** | Case 2 **container** | same image | `America/New_York` | same | named volume seeded from the same golden as R2 | later gate |

**R1 evidence forms** (strongest → weakest, all used):
1. **R1-hist (available now) [V]** — the 8 real Actions runs since 2026-09-10: resolved-package logs, `config_version`, report shapes, cache-hit behaviour. Used for L0 and the L3 report format.
2. **R1-bare** — a *stock* `python:3.11.16-slim-bookworm` container with **none of our layers** (no entrypoint, no Compose, no volumes, no wrapper): source bind-mounted **read-only**, deps installed from the manifest, `TZ=UTC`. It isolates *C0's mechanisms* from *Linux + 3.11 + these deps*. Gating reference for Case 1.
3. **R1-gh (optional, needs A2)** — a manual-only diagnostics workflow on the real runner; the only fully faithful Ubuntu reference. Never touches the ledger cache, commits, issues, or secrets.

**If A1 is declined**, R2 degrades to *R1-bare with `TZ=America/New_York`* plus host-3.14 results reported as a compatibility datapoint; the equivalence report must then state that the Windows→Linux delta (§13) is **measured only on 3.14**.

**Gating matrix**

| Comparison | Gates C0? |
|---|---|
| C1 vs R1-bare (and vs R1-hist where deterministic) | **yes** |
| C2 vs R2 (Windows deltas normalised, §13) | **yes** |
| host-3.14 vs anything | **no** — datapoint |
| C1 vs C2 | **no** — expected to differ by exactly TZ + persistence profile |

---

## 3. Container boundaries

### 3.1 One image, three targets, four services

```
docker/Dockerfile.terminal   (multi-stage; nothing is built in this design step)

  base    python:3.11.16-slim-bookworm@sha256:<pinned at Dockerfile gate>
          + apt: tzdata ca-certificates       (no curl, no compilers in runtime)
          + user tv (uid 10001), HOME=/home/tv, LANG=C.UTF-8
          + dirs owned by tv:  /app  /out  /data  /home/tv/.tradingview_mcp_data
  deps    base + pip install -c constraints/c0-case1-py311.txt <case1 root deps>      ← exactly the 60 Case 1 pins
  app     deps + COPY src lab dashboard canonical automation docker + pyproject.toml README.md
          + pip install --no-deps -e .        (mirrors Case 1's `pip install -e .`)
          + symlink /app/PAPER_DAILY_REPORT.md → /out/PAPER_DAILY_REPORT.md

  ┌─ target paper      = app                                              (== Case 1 environment, byte-for-byte package set)
  ├─ target dashboard  = app + c0-dashboard-extra.txt   (Flask closure)
  └─ target tests      = dashboard + c0-tests-extra.txt  (pytest closure) + COPY tests/
```

`paper ⊂ dashboard ⊂ tests` by package set. The **`paper` target contains no Flask and no pytest**, exactly like the
Case 1 runner **[V: Case 1 install log has neither]**. Nothing is added "because it might be useful".

| Service | Target | Lifecycle | Publishes | Purpose |
|---|---|---|---|---|
| `paper-case1` / `paper-case2` | `paper` | **one-shot job** (`run --rm`), never a daemon | none | runs `automation/paper_scheduler.py <cmd>` |
| `dashboard-case1` / `dashboard-case2` | `dashboard` | long-running, single process (Flask dev server — same as host) | `127.0.0.1:5057` only | the terminal |
| `tests-case1` / `tests-case2` | `tests` | one-shot | none | `pytest tests/unit` |

### 3.2 Filesystem layout inside the image

| Path | Content | Writable at runtime | Persists |
|---|---|---|---|
| `/app` | repo layout (`src/ lab/ dashboard/ canonical/ automation/ docker/ pyproject.toml`); **required** because code finds siblings via `sys.path.insert` | only the report symlink target | no |
| `/app/PAPER_DAILY_REPORT.md` | **symlink** → `/out/PAPER_DAILY_REPORT.md` (lets `paper_scheduler.py:92` write to a volume with **no code change**) | via symlink | via `/out` volume |
| `/out` | reports / junit / normalised artefacts | yes | named volume per profile |
| `/home/tv/.tradingview_mcp_data` | **the state dir** (`HOME=/home/tv`) | yes | Case 2: named volume · Case 1: **tmpfs (ephemeral)** |
| `/data/case1/paper` | Case 1 ledger dir (`PAPER_DATA_DIR`, Case 1 profile only) | yes | Case 1: named volume |
| `/opt`, site-packages | pinned deps | no | image |

### 3.3 Explicitly **not** containers in C0

GitHub Actions workflow + cache/artifact/dedup logic (stays on the bare runner, untouched) · MCP server · legacy stack
(`scheduler_run.py`, `daily_runner.py`, `run_lab.py`) · any browser · any registry.

### 3.4 Network and privileges

Outbound only (Yahoo/TradingView/Finnhub/… as in the inventory). **No** `network_mode: host`, **no** `privileged`,
`cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`. Only the dashboard publishes a port, bound to
`127.0.0.1` on the host — the Flask dev server has **no authentication**. Non-root uid 10001.

---

## 4. Docker build context and ignore hygiene

### 4.1 Rules

* **Context = repo root**, but the terminal build uses its **own ignore file** `docker/Dockerfile.terminal.dockerignore`
  (BuildKit per-Dockerfile ignore; it *replaces* the root `.dockerignore` for that build). It is an **allowlist**: deny
  everything, then re-admit only what the image needs. New files are excluded by default.
* The **root `.dockerignore` gets additive-only exclusions** so the existing MCP image build stops being able to ingest
  secrets/state either. Additive means: no line is removed, so the MCP image contents can only shrink by files that
  should never have been there.
* No Compose file, script, or doc may use the host path `~/.tradingview_mcp_data` as a **mount source** (§5.3 test).

### 4.2 Allowlist (spec)

```
# docker/Dockerfile.terminal.dockerignore
*                                   # deny by default
!pyproject.toml
!README.md                          # pyproject `readme = "README.md"` — build fails without it
!LICENSE
!src/
!lab/
!dashboard/
!canonical/
!automation/
!docker/
!tests/                             # copied only by the `tests` target
# hard denies (later lines win)
**/__pycache__/
**/*.py[cod]
**/*.egg-info/
**/.env
**/.env.*                           # also drops .env.example — not needed in the image
**/*.db
**/*.db-*
**/*.sqlite*
**/*.jsonl
**/*.pickle
**/*.pem
**/*.key
**/*token*.json
**/*session*.json
**/*credential*
**/*secret*
docker/env/*.local
docker/**/*.log
```

### 4.3 Root `.dockerignore` additions (spec; additive)

```
.env
.env.*
.tradingview_mcp_data/
**/*.db
**/*.db-*
**/*.jsonl
**/*.pickle
**/*token*.json
**/*session*.json
graphify-out/
*.pdf
reports/
```

### 4.4 Verification (all must pass before an image is *used*, and before any image leaves the machine)

| Check | Method | Pass |
|---|---|---|
| Context audit | a throw-away `context-audit` stage lists `/ctx`; diff vs committed `docker/context.manifest` | identical |
| No secrets in layers | `docker run --rm <img> find / -xdev ( -name '.env*' -o -name '*.db' -o -name '*.pickle' -o -name '*token*.json' -o -name '*session*.json' ) -not -path '/proc/*'` | empty |
| No secrets in metadata | `docker inspect`/`docker history --no-trunc` grep for `PASSWORD|SECRET|KEY|TOKEN` | none |
| No state dir content | `/home/tv/.tradingview_mcp_data` empty in a freshly created container | empty |

### 4.5 One interaction to be aware of

`publish-image.yml` builds the **root `Dockerfile`** on **every** push to `main` and pushes `:latest`/`:sha-*` to GHCR
(paths-ignore is `**.md` only). Committing C0 files (including the root `.dockerignore` change) will therefore trigger
one more MCP-image build+push — same as any commit today. It will **not** build `docker/Dockerfile.terminal`; that image
is never added to a publishing workflow in C0 (blocker B2).

---

## 5. Volumes and state

### 5.1 Why named volumes only

The paper ledger and `scan_cohort.db` are **SQLite WAL**. WAL needs mmap'd `-shm` shared memory and is unsafe on
network/virtualised filesystems. Docker Desktop on Windows bind mounts cross a VM boundary **[S]**. Named volumes live on
the engine's own ext4, and two containers on one engine share a kernel, so WAL works. **No bind mounts for any path that
can contain a database.** (Docker CLI 29.7.2 present; **daemon not running [V]**.)

### 5.2 Profile state model

| | **Case 1 profile** (mirrors the Actions job) | **Case 2 profile** (mirrors the host) |
|---|---|---|
| `TZ` | `UTC` | `America/New_York` |
| `HOME` | `/home/tv` | `/home/tv` |
| `PAPER_DATA_DIR` | `/data/case1/paper` (**set**, like the workflow) | **unset** → `~/.tradingview_mcp_data/paper` (like the host) |
| Ledger persists in | volume `c0-case1_ledger` → `/data/case1/paper` | volume `c0-case2_state` (whole state dir) |
| Rest of `$HOME/.tradingview_mcp_data` | **`tmpfs`** — empty at every start, discarded at exit (**K2**: predictions.jsonl, calibration.json, scan_cohort.db, security_master.db, sec_cik_map.json … do *not* persist) | same named volume as the ledger — **everything persists** |
| Reports | `c0-case1_out` → `/out` | `c0-case2_out` → `/out` |
| What is **not** replicated | Actions cache-key logic, artifact fallback, holiday/day gate, dedup-by-committed-report, bot commit, failure issue — **these live in the workflow and stay there** | any Task-Scheduler behaviour (there is none) |

`tmpfs` for Case 1's `$HOME` state is the exact analogue of "`/home/runner/.tradingview_mcp_data` is thrown away with the
runner", and it is what makes K2 enforceable rather than accidental.

### 5.3 Real-state protection

* **No path in any C0 file resolves to the host's real state dir.** A test (`docker/tests/test_no_real_state_mount.py`) greps
  Compose files, scripts, and env files for `.tradingview_mcp_data` as a mount source and for any host absolute path; it must
  find none.
* Reference-side runs (R2) never see the real `USERPROFILE`: the harness sets `USERPROFILE`/`HOME` to a work dir and
  **asserts `os.path.expanduser("~")` ≠ the real profile before starting anything** (tripwire).

### 5.4 Seeding, ownership, teardown

* Seed = `docker create` a helper that mounts the empty named volume, `docker cp` the golden copy into it (never opens SQLite), then `chown -R 10001:10001` and remove the helper.
* The image pre-creates `/home/tv/.tradingview_mcp_data`, `/data`, `/out` owned by `tv` so fresh volumes inherit ownership.
* Every A/B run uses **fresh, uniquely named volumes** (`c0-ab-<runid>-…`) and removes them afterwards. Nothing is reused across runs.

---

## 6. Compose profiles (specification — file to be written at the Dockerfile gate)

`docker/compose.c0.yml`, project names `-p c0-case1` / `-p c0-case2`, **no `restart`**, **no `depends_on` between
services**, env files hold only non-secret settings.

```yaml
# SPEC ONLY — not yet a file
x-common: &common                             # NB: `<<` merges are shallow — every service therefore restates its full `build:`
  user: "10001:10001"
  cap_drop: [ALL]
  security_opt: ["no-new-privileges:true"]
  restart: "no"

services:
  # ── Case 1 profile ─────────────────────────────────────────────
  paper-case1:
    <<: *common
    profiles: [case1]
    build: {context: ., dockerfile: docker/Dockerfile.terminal, target: paper}
    env_file: docker/env/case1.env            # TZ=UTC HOME=/home/tv LANG=C.UTF-8 PAPER_DATA_DIR=/data/case1/paper
    volumes: [c0_case1_ledger:/data/case1/paper, c0_case1_out:/out]
    tmpfs: ["/home/tv/.tradingview_mcp_data:uid=10001,gid=10001"]
    entrypoint: ["/app/docker/entrypoint.sh", "case1"]
    command: ["python", "automation/paper_scheduler.py", "status"]     # safe default; `all` is explicit

  dashboard-case1:                              # viewer for a COPIED Case 1 ledger (TZ parity)
    <<: *common
    profiles: [case1]
    build: {context: ., dockerfile: docker/Dockerfile.terminal, target: dashboard}
    env_file: docker/env/case1.env
    volumes: [c0_case1_ledger:/data/case1/paper, c0_case1_out:/out]
    tmpfs: ["/home/tv/.tradingview_mcp_data:uid=10001,gid=10001"]
    ports: ["127.0.0.1:5057:5057"]
    entrypoint: ["/app/docker/entrypoint.sh", "case1"]
    command: ["python", "docker/serve_dashboard.py"]
    healthcheck: &hc
      test: ["CMD","python","-c","import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5057/api/health',timeout=5).status==200 else 1)"]
      interval: 30s
      timeout: 8s
      start_period: 60s
      retries: 3

  tests-case1:
    <<: *common
    profiles: [case1]
    build: {context: ., dockerfile: docker/Dockerfile.terminal, target: tests}
    env_file: docker/env/case1.env
    volumes: [c0_case1_out:/out]
    tmpfs: ["/home/tv/.tradingview_mcp_data:uid=10001,gid=10001"]   # tests write into $HOME (see §8.4)
    entrypoint: ["/app/docker/entrypoint.sh", "case1"]
    command: ["python","-m","pytest","tests/unit","-q","-p","no:cacheprovider","--ignore=tests/e2e","--junitxml=/out/junit.xml"]

  # ── Case 2 profile ─────────────────────────────────────────────
  paper-case2:
    <<: *common
    profiles: [case2]
    build: {context: ., dockerfile: docker/Dockerfile.terminal, target: paper}
    env_file: docker/env/case2.env            # TZ=America/New_York HOME=/home/tv LANG=C.UTF-8   (no PAPER_DATA_DIR)
    volumes: [c0_case2_state:/home/tv/.tradingview_mcp_data, c0_case2_out:/out]
    entrypoint: ["/app/docker/entrypoint.sh", "case2"]
    command: ["python", "automation/paper_scheduler.py", "status"]

  dashboard-case2:
    <<: *common
    profiles: [case2]
    build: {context: ., dockerfile: docker/Dockerfile.terminal, target: dashboard}
    env_file: docker/env/case2.env
    volumes: [c0_case2_state:/home/tv/.tradingview_mcp_data, c0_case2_out:/out]
    ports: ["127.0.0.1:5057:5057"]
    entrypoint: ["/app/docker/entrypoint.sh", "case2"]
    command: ["python", "docker/serve_dashboard.py"]
    healthcheck: *hc

  tests-case2:
    <<: *common
    profiles: [case2]
    build: {context: ., dockerfile: docker/Dockerfile.terminal, target: tests}
    env_file: docker/env/case2.env
    volumes: [c0_case2_out:/out]
    tmpfs: ["/home/tv/.tradingview_mcp_data:uid=10001,gid=10001"]   # NEVER the state volume
    entrypoint: ["/app/docker/entrypoint.sh", "case2"]
    command: ["python","-m","pytest","tests/unit","-q","-p","no:cacheprovider","--ignore=tests/e2e","--junitxml=/out/junit.xml"]

volumes: {c0_case1_ledger: {}, c0_case1_out: {}, c0_case2_state: {}, c0_case2_out: {}}
```

Spec caveats to resolve at G4 (no daemon available to test them now): the exact Compose short-form `tmpfs:` option syntax for
`uid/gid` (fall back to the long `type: tmpfs` form or a `mode`), and `<<` merge behaviour with the Compose version installed.

Design notes: the **default command is `status`** (reads + schema migration on a *copy*), so a stray `compose run` can never
place an order; `all`/`premarket` are always typed explicitly. `tests-*` **never** mount the state volume. Only
`dashboard-*` publish a port, only on loopback.

---

## 7. Environment contract

### 7.1 Fixed by the profile

| Var | Case 1 | Case 2 | Why |
|---|---|---|---|
| `TZ` | `UTC` | `America/New_York` | D1 |
| `HOME` | `/home/tv` | `/home/tv` | the real state-dir switch (~20 hard-coded `expanduser`) |
| `LANG` | `C.UTF-8` | `C.UTF-8` | matches the Actions runner; makes `open()` without `encoding` deterministic (≈3 sites) |
| `PAPER_DATA_DIR` | `/data/case1/paper` | *unset* | mirrors workflow / host |
| `DASHBOARD_PORT` | `5057` (default, not set) | same | wrapper reads it |

### 7.2 Must be **unset** (validation) — and forbidden outright for the ones marked ⛔

`PAPER_*` (all **except** `PAPER_DATA_DIR`), `DECISION_*` (all), `TRADINGVIEW_ENABLED`, `SENTIMENT_MODEL_ENABLED` — **any set
value changes `config_version`** (K3). `PAPER_DATA_DIR` is location-only and is **not** hashed (`db.py` key list; the Case 1
workflow sets it and its reports still read `cfg-a0eede144e` **[V]**).

**K3 is necessary, not sufficient.** The hash covers ~30 named keys, **not every tuning knob**: `PAPER_COOLDOWN_LOSSES/DAYS`,
`PAPER_ALLOW_SPREADS`, `PAPER_ALLOW_MULTI_LEG`, `RISK_PROFILE`, `SCAN_ALLOW_LEVERAGED`, `SIZE_FRACTIONAL`, `OPT_*`, `FRESH_*`,
`REGIME_RISK_BAND`, `SCAN_COHORT_TOP_N`, `HIST_LOOKBACK_RANGE`, `STRATEGY_*`, `TRADINGVIEW_MCP_*` (inventory §5.4) change
behaviour **without** changing `config_version` **[S]**. So the preflight asserts the **absence of the entire §5.4 tuning list**,
not just the hashed keys — an equal hash alone would not prove environment parity. ⛔ `BROKER_PROVIDER` (≠`none`), ⛔ `ROBINHOOD_TRADING_ENABLED` (≠`false`), ⛔ `ROBINHOOD_*`, `RH_*`,
`AXISDIRECT_*`, `SNAPTRADE_*`, `PROXY_PASSWORD`. Provider keys (`FINNHUB_API_KEY`, `FRED_API_KEY`, `ALPHAVANTAGE_API_KEY`)
are **unset in all validation runs** — `gh secret list` returns no repo secrets **[V]**, so Case 1 has been running keyless.
(For non-validation use they are passed only via `--env-file` **outside** the repo.)

### 7.3 Preflight (`docker/entrypoint.sh` → `python docker/c0_preflight.py <profile>`; container refuses to start on any failure)

Pure and read-only: it never opens a DB, never calls the network, never mutates env.

| # | Assertion |
|---|---|
| 1 | `sys.version_info[:3] == (3,11,16)` |
| 2 | `TZ` equals the profile value; `time.tzname` and the **fixed-instant date test** match (below) |
| 3 | `from paper import db` → `db.config_version() == "cfg-a0eede144e"` for the default env (**K3**) |
| 4 | `BROKER_PROVIDER ∈ {unset,none}`; `ROBINHOOD_TRADING_ENABLED ∈ {unset,false}`; §7.2 ⛔ vars unset; **every** inventory-§5.4 tuning var unset (hash alone is not enough) |
| 5 | `db.db_path()` resolves under the profile's expected volume path; `expanduser("~/.tradingview_mcp_data")` under `/home/tv` |
| 6 | `/app/.env` absent; no `*token*.json`/`*session*.json`/`*.pickle`/`*.db` inside `/app` |
| 7 | `/app/lab /app/dashboard /app/canonical /app/src /app/automation` present (layout the `sys.path` hacks require) |
| 8 | prints `pip freeze | sha256`, `python -V`, `TZ`, `config_version`, resolved paths to stdout for the A/B log |

**Fixed-instant date test** (proves D1 without waiting for 8 pm ET): `datetime.fromtimestamp(1790042400)` where
`1790042400 = 2026-09-22T02:00:00Z`. Expected `.date()`: **`2026-09-22`** under Case 1 (UTC) and **`2026-09-21`** under
Case 2 (ET, 22:00 EDT). This uses the same local-time mechanism as `dt.date.today()`, so it asserts the exact behaviour
that is being *preserved*, not fixed.

---

## 8. Test baseline — by exact test ID

### 8.1 Measured today (**host, CPython 3.14.7, ET, `HOME` sandboxed**) — datapoint, not the baseline **[V]**

`python -m pytest tests/unit -q -p no:cacheprovider --ignore=tests/e2e` → **1,272 passed · 6 failed · 25 s**.

| # | Test ID | Class | Reason |
|---|---|---|---|
| 1 | `tests/unit/test_canonical_v11_invariants.py::test_oi_policy_has_no_overlapping_hard_and_soft_ranges` | **D** (deterministic) | Intentionally red — docstring: "stays red forever as an honest record"; asserts `grade_contract()` and `analyse_contract()` agree, which they do not by design |
| 2 | `tests/unit/test_canonical_v11_invariants.py::test_contract_quality_identical_across_pipelines` | **D** | Intentionally red — same reason |
| 3 | `tests/unit/test_finbert_sentiment.py::test_transformers_available` | **D** | optional `transformers` not installed |
| 4 | `tests/unit/test_finbert_service.py::test_available` | **D** | `fb.available()` is `False` — model deps absent |
| 5 | `tests/unit/test_finbert_service.py::test_score_force` | **D** | `'lexical' == 'finbert'` — falls back to lexical without the model |
| 6 | `tests/unit/test_finbert_service.py::test_cache_behavior` | **N** (nondeterministic) | asserts `(t3−t2) < (t1−t0)` on two `perf_counter` timings; without the model both calls are ~µs, so the outcome is a coin flip. Marked `@pytest.mark.slow` (mark unregistered → warnings) |

_Correction to `C0_INVENTORY.md` §4.3: that table implied #6 was deterministic. It is **5 D + 1 N**, and the headline count can legitimately read 1,272/6 **or** 1,273/5._

### 8.2 The permanent goal is the **ID-level baseline**, not the counts

1. At the freeze gate, run the suite on the **reference** environments (R1-bare under `TZ=UTC`; R2 under `America/New_York`) and store
   `docker/baseline/tests-<ref>-<tz>.json` = `{test_id: outcome}` **plus** a `reason` and `class` (D/N) for every non-pass. **No baseline exists for 3.11 or for UTC yet** — Case 1 does not run tests in CI **[S]**, so R1 has no history to reuse.
2. **C0 passes** iff, for every test ID, the container's outcome equals the reference's outcome, **except N-class IDs**, which are run **3× on each side, recorded, and not gated**.
3. Any *new* failure, **or any new pass of a D-class failure** (e.g. FinBERT tests passing because `torch` sneaked into the image), is an equivalence failure.
4. The test *count* may differ between 3.14 and 3.11 (skips/param changes). It is reported, never gated.

### 8.3 How the suite is run

Exact command as in §6, `--junitxml` output; parsed by ID. Run under **both** TZ profiles because tests may be TZ-sensitive
and no TZ-specific baseline exists.

### 8.4 Finding: running the tests pollutes real state — tests must always get an ephemeral `HOME` **[V]**

The 1,278-test run wrote, under the (sandbox) `$HOME`: `.tradingview_mcp_data/predictions.jsonl` (24 KB of `"symbol":"TEST"`
rows), `last_candidates.json`, `portfolio.db`, `security_master.db`, and yfinance caches `AppData/Local/py-yfinance/*.db`.
Read-only inspection of your **real** `predictions.jsonl`: **3,281 of 5,051 rows (65%) are `TEST` rows**, spread over 10 distinct
days (first row 2026-08-26; presumably the days the suite was run on the host — not traced). Whether this skews calibration is **not analysed** (calibration
matches predictions to closed trades, so `TEST` rows probably never match) — but it is real contamination of a decision-input
file. Consequence for C0: `tests-*` services use a **tmpfs `HOME`, never a state volume**, and the harness never runs pytest against a real profile. Not fixed here (out of C0 scope).

---

## 9. Startup commands and health checks

### 9.1 Commands

| What | Container command | Reference equivalent | Notes |
|---|---|---|---|
| Case 1 run step | `python automation/paper_scheduler.py all` | identical to the workflow's step | **only** on a copied/empty ledger during C0 |
| Manual paper cmd (Case 2) | `python automation/paper_scheduler.py {status\|report [date]\|performance\|options\|premarket [--dry-run]\|hours\|postmarket\|all}` | same | via `docker compose -p c0-case2 --profile case2 run --rm paper-case2 <cmd>` |
| Dashboard | `python docker/serve_dashboard.py` | `python dashboard/app.py` | §9.3 |
| Tests | see §6 | see §8.3 | |
| Entry | `entrypoint.sh <profile>` → preflight → `exec "$@"` | — | preflight failure = non-zero exit, nothing runs |

`status` runs `db.connect()` → **creates + migrates the ledger** if it is v1 (additive columns). On a **copy** this is the
expected, comparable behaviour on both sides; it is the reason K1 exists.

### 9.2 Health checks

| Component | Check | Meaning | Ledger-safe? |
|---|---|---|---|
| Dashboard | `GET /api/health` → 200 (Compose healthcheck, §6; python `urllib`, no curl in the image) | **liveness only** — returns 200 even with empty/failed providers **[V]** | **yes [V]** — see below |
| Paper job | process exit code + post-run `paper_scheduler.py status` (`reconciliation.reconciled == true`) | success/integrity | n/a (on copy) |
| Tests | pytest exit code + junit by ID | §8 | n/a |
| MCP | — | out of scope (E3) | — |

**Probe-safety evidence [V]** — fresh sandbox `$HOME`, dashboard started, files observed after each step:

| Step | State files present |
|---|---|
| after start | `portfolio.db`, `security_master.db` |
| `GET /api/health` | *(no change)* |
| `GET /` | *(no change)* |
| +20 s (boot pre-warm finished) | + `last_candidates.json` |
| `GET /api/paper/account` | + **`paper/robinhood_500_baseline.db` (+`-wal`,`-shm`)** ← ledger created here |

So the dashboard's **own start-up writes `{portfolio.db, security_master.db, last_candidates.json}`** (call it **W_dash**, the
expected write-set for L5) but **does not touch the paper ledger; the first `/api/paper/*` call does**. The Compose
healthcheck therefore never opens the ledger. (This refines the user's "dashboard startup can migrate it": startup
mutates other state; the *ledger* migrates on first paper-endpoint use. K1 holds either way.)

### 9.3 The dashboard wrapper `docker/serve_dashboard.py` (spec)

```python
# ~10 lines, no logic of its own
import os, sys
sys.path.insert(0, "/app/dashboard")          # what `python dashboard/app.py` puts at sys.path[0]
import app                                    # runs the SAME module-level code, incl. _prewarm() at import
app.app.run(host="0.0.0.0", port=int(os.environ.get("DASHBOARD_PORT", "5057")), debug=False)
```

**Proof obligations (all in the equivalence report):**
1. `git diff --stat -- dashboard/` is empty; wrapper is the only new runtime file.
2. **Same behaviour as `python dashboard/app.py`:** `_prewarm()` runs at import in both **[S: module-level call at `app.py:1760`]**; `app.run(..., debug=False)` = the original's call except `host`; threaded, no reloader — same.
3. **Known, benign difference:** module name is `app`, not `__main__` → Flask `import_name` and logger name differ; `root_path` resolves to `/app/dashboard` either way. Proven by L2: route snapshots via `app.test_client()` (reference) vs over a real socket through the wrapper (container) are equal.
4. Removal = delete the file and change the compose `command`. No other file depends on it.
5. Publish is loopback-only; there is no auth and no TLS — **not** a deployment artefact.

---

## 10. Dependency freeze

### 10.1 What the Case 1 environment actually is **[V]**

Source: `gh run view 35640912640 --log` (2026-09-21 18:49 UTC, a real run; cache hit on `paper-ledger-20260918-170740`).

* `actions/setup-python@v5`, `python-version: 3.11` → **CPython 3.11.16**. Install: `python -m pip install -e .` (no lock, no extras).
* **61 packages installed = 60 third-party + the editable project** (`tradingview-mcp-server 0.7.1`).
* Key pins: `mcp 2.2.0` (+`mcp-types 2.2.0`), `yfinance 1.7.0`, `numpy 2.4.6`, `pandas 2.3.3`, `requests 2.34.2`,
  `feedparser 6.0.14`, `python-dotenv 1.2.3`, `tradingview-screener 3.0.0`, `tradingview-ta 3.3.0`, `tzdata 2026.4`,
  `typing-extensions 4.16.0`, `cryptography 50.0.1`, `curl_cffi 0.16.3`, `lxml 6.1.3`, `pydantic 2.13.5`, `starlette 1.6.0`,
  `uvicorn 0.53.0`. **No Flask, no pytest, no `holidays`, no torch/transformers.**
* **Not in the log:** the runner's bundled `pip`/`setuptools`/`wheel` (pre-installed, untouched) and the isolated build-backend that built the editable metadata. They are **not runtime deps**; the freeze records the image's `pip` version for provenance only.

### 10.2 Case 1 has been drifting — the freeze must pick a point, not "follow" **[V]**

Each daily run resolves fresh from PyPI. Across the **8 real runs** (09-10 → 09-21), Python is constant (3.11.16) and the set is
constant at 61, but transitive versions moved:

| Package | 09-10 | 09-21 (latest) | Runs on the older version |
|---|---|---|---|
| `platformdirs` | 4.11.8 → 4.11.10 | **4.11.11** | 09-10 … 09-18 |
| `protobuf` | 7.36.1 | **7.36.2** | 09-10 … 09-17 |
| `idna` | 3.19 | **3.20** | 09-10 … 09-16 |
| `urllib3` | 2.7.0 | **2.8.0** | 09-10 … 09-15 |
| `httpx2` / `httpcore2` | 2.12.0 | **2.13.0** | 09-10, 09-11 |
| `uvicorn` | 0.52.4 | **0.53.0** | 09-10, 09-11 |
| `tzdata` (data only) | 2026.3 | **2026.4** | 09-10, 09-11 |
| `pyjwt` | 2.13.0 | **2.14.0** | 09-10 only |

**None touch the decision/pricing stack** (`mcp`, `yfinance`, `numpy`, `pandas`, `requests`, `feedparser`, screener/ta were
constant across all 8 runs). `urllib3`/`idna` are the HTTP stack; `tzdata` is the tz *data* package — worth recording, not worth
chasing. **Rule: the manifest freezes one run (the latest at freeze time) and records the others as a drift log; C0 never
auto-follows Case 1.**

### 10.3 Reconciliation (hierarchy in §1.3) **[V]**

| Package | `pyproject` | **Case 1 (truth)** | Host (Case 2 today) | `uv.lock` |
|---|---|---|---|---|
| `mcp` | `>=1.12.0` | **2.2.0** | 2.1.1 | 1.12.4 |
| `yfinance` | `>=1.7.0` | **1.7.0** | 1.7.0 | *absent* |
| `numpy` | — (transitive) | **2.4.6** | **2.5.2** | 2.2.6 / 2.3.2 |
| `pandas` | — | **2.3.3** | 2.3.3 | 2.3.1 |
| `requests` | `>=2.32` | **2.34.2** | 2.34.2 | 2.32.4 |
| `feedparser` | `>=6.0.12` | **6.0.14** | 6.0.14 | 6.0.12 |
| `python-dotenv` | *undeclared* | **1.2.3** | 1.2.3 | 1.1.1 |
| `tradingview-screener` | `==3.0.0` | **3.0.0** | 3.0.0 | 3.0.0 |
| `tradingview-ta` | `>=3.3.0` | **3.3.0** | 3.3.0 | 3.3.0 |
| `flask` | extra `[dashboard]` `>=3.0` | *not installed* | 3.1.3 | *absent* |
| `holidays` | *undeclared* | *not installed* | *not installed* | *absent* |

**Host vs Case 1:** of the 60 third-party pins, **43 are identical on the host, 17 differ** — `anyio`, `click`, `httpcore2`,
`httpx2`, `idna`, `mcp`, `mcp-types`, **`numpy` (host 2.5.2 vs 2.4.6)**, `peewee`, `platformdirs`, `protobuf`, `pyjwt`,
`sse-starlette`, `tzdata`, `urllib3`, `uvicorn`, `websockets`. The only one that could plausibly touch numeric behaviour is
**`numpy`**: Case 2 (host) already runs a *newer* numpy than Case 1. D4 makes Case 1's the reference for **both** profiles, so
the containers will run 2.4.6 where the host runs 2.5.2 — a **documented, expected Case 2 delta** (§13), to be checked by L3/L4
rather than assumed harmless.

### 10.4 Artefacts to be produced at the freeze gate (not now)

```
docker/constraints/
  c0-case1-py311.txt        60 exact pins (== the Case 1 set, third-party only) + header:
                            source run id, run date, CPython 3.11.16, sha256 of the source list, drift-log pointer
  c0-dashboard-extra.txt    Flask closure: flask, werkzeug, jinja2, itsdangerous, blinker, markupsafe — versions from the HOST
                            (3.1.3 / 3.1.8 / 3.1.6 / 2.2.0 / 1.9.0 / 3.0.3) where they resolve on 3.11 under the Case 1 pins;
                            `click` stays at Case 1's 8.5.0 (Case 1 pin wins). Provenance: "no Case 1 evidence exists for Flask"
  c0-tests-extra.txt        pytest closure: pytest 9.1.1, pluggy 1.6.0, iniconfig 2.3.0, packaging 26.3, colorama (host versions)
  README.md                 hierarchy, provenance, refresh policy, drift log (§10.2)
docker/baseline/
  tests-<ref>-<tz>.json     §8.2
  image-facts.txt           dpkg -l, pip --version, python -V, base digest
```

The Flask/pytest layers have **no Case 1 provenance**; their source is the host (Case 2 truth) and that is stated in the file.

### 10.5 Freeze procedure and proof

| Step | Action | Pass |
|---|---|---|
| F0 | Re-fetch the latest real Case 1 run log; extract the "Successfully installed" list | list parsed; 61 entries |
| F1 | Write `c0-case1-py311.txt` from it (drop the editable project line) | 60 pins, header complete |
| F2 | In a **clean** CPython 3.11.16 environment (R1-bare): `pip install -c c0-case1-py311.txt -e .` | exits 0, no resolver conflicts |
| F3 | `pip freeze` (minus the editable project) **must equal** the 60 pins | **exact equality** |
| F4 | Diff against **R1-hist** (the 8 logs): only the known drift-log entries differ | matches §10.2 |
| F5 | Add Flask closure → confirm `pip check` clean and Case 1 pins unchanged | pins unchanged |
| F6 | Add pytest closure → same | pins unchanged |
| F7 | Build-stage assertion in the Dockerfile: `pip freeze | sha256` of each target equals the recorded hash | build fails on drift |
| F8 | Record `docker/baseline/image-facts.txt` | committed |

### 10.6 Refresh policy

The manifest is immutable until a **deliberate** re-freeze (its own commit, its own equivalence re-run). A read-only
`docker/tools/case1_drift.py` may *compare* a newer Case 1 log to the manifest and report; it never edits it.

### 10.7 Base-image / OS delta (documented, not hidden)

Case 1 = Ubuntu runner image `20260907.300.1` + `actions/python-versions` CPython 3.11.16. C0 image = Debian bookworm-slim +
`python:3.11.16`. Same CPython version; different glibc/OpenSSL/CA bundle. `tzdata`: the Case 1 set includes the **pip** `tzdata`
(via pandas) *and* the runner has system tz data; the image installs **apt `tzdata`** (system zoneinfo is what `zoneinfo`
consults first) and keeps the pip one from the manifest. The exact `python:3.11.16-slim-bookworm` tag's existence and digest
are **[U]** until the daemon is up.

---

## 11. Copied-state validation procedure (K1)

**Golden copies live outside the repo** (`<work>/c0/golden/…`, git-ignored, never mounted from the real path).

| Step | Action | Evidence |
|---|---|---|
| V0 | Preconditions: no Python process running; Docker daemon up; disk space | recorded |
| V1 | **Fingerprint the real dir**: SHA-256 + size + mtime of every file under `~/.tradingview_mcp_data` → `state_real.manifest` | file |
| V2 | **Byte-for-byte copy** → `golden/case2/` (`robocopy /E /COPY:DAT /DCOPY:DAT`, or `cp -a`); copy `-wal`/`-shm` together if present (they are currently absent) | — |
| V3 | Verify copy: SHA-256 equality file-by-file, identical file list | `golden.manifest` == `state_real.manifest` (hash column) |
| V4 | Mark golden **read-only** (`attrib +R -H /S /D`) | attribute listing |
| V5 | **Re-fingerprint the real dir**; must equal V1 exactly | tripwire #1: real state untouched |
| V6 | Case 1 golden: **empty** dir (first-run path, like Case 1's first day). *Optional:* a real Case 1 ledger copy (needs A4 — a download) | — |
| V7 | Create fresh named volumes for **each** run; seed from golden via `docker cp` (§5.4); R2 gets its own working copy of golden as `%USERPROFILE%` | volume names logged |
| V8 | Run reference and container **side by side, same minute**, each on its own copy | logs |
| V9 | Collect artefacts (state snapshots, reports, junit, stdout) into `<work>/c0/runs/<runid>/{ref,ctr}/` | — |
| V10 | Tear down volumes; **re-fingerprint the real dir again**; must equal V1 | tripwire #2 — appended to the equivalence report |

**Never** in a run: `premarket` without `--dry-run` outside the designated L4 rung, any §7.2 ⛔ variable, any real-path
mount, any network credentials. The harness `assert`s all of these before it launches anything.

---

## 12. A/B equivalence checks

Common rules: identical `env`, identical golden copy, identical inputs; reference-side `HOME`/`USERPROFILE` redirected
(tripwire, §5.3); outputs normalised by **one shared normaliser** (`docker/ab/normalize.py`) that only (a) strips timestamps
and generated-at headers, (b) folds `\r\n`→`\n`, (c) sorts unordered JSON keys. Nothing else is ever normalised.

| Lvl | What is compared | Deterministic? | Method | Pass rule |
|---|---|---|---|---|
| **L0** | Environment facts: `python -V`, `pip freeze` hash vs manifest, `TZ`, **fixed-instant date test (§7.3)**, resolved `db_path()`/`HOME`/`PAPER_DATA_DIR`, `config_version`, layout | yes | preflight output, both sides | exact; **`config_version == cfg-a0eede144e` on the Case 1 profile** |
| **L1** | Unit tests by ID | D-class yes, N-class no | §8 | ID-level equality (§8.2) under **both** TZs |
| **L2** | State-only dashboard routes on golden: `/`, `/api/paper/{account,report?date=,performance,signals,options_shadow}` | yes (golden has 0 open positions ⇒ no live marks) | reference: `app.test_client()`; container: same script **and** real-socket requests through the wrapper (§9.3) | byte-equal after normalisation |
| **L3** | `paper_scheduler.py status` and `report <fixed date>` on golden | yes (report is documented deterministic) | run both sides | equal `schema_version`, `config_version`, `reconciliation`, normalised report body |
| **L4a** | `premarket --dry-run` — no orders | **no** (live quotes) | same minute, separate copies; **noise envelope** below | same universe; same `action` + `failed_gates` per symbol outside the envelope; **0 orders / 0 fills / 0 positions on both** |
| **L4b** | `all` (full day) on the copy | no | same | signals/orders/fills within envelope; `reconciled: YES` both sides; report structurally equal |
| **L5** | State-mutation set: file list + per-table row counts + sizes after each run | yes (set), no (values) | diff the two state trees | same files created/modified; for the dashboard the set equals **W_dash** (§9.2); **no** token/session/pickle files; **nothing** outside the volume |
| **L6** *(optional)* | `paper status` while `dashboard` is up on the same volume | — | — | no WAL errors, no `database is locked` |

**Noise envelope (L4).** Run the *reference twice* (A1, A2) on separate copies, same minute. The A1↔A2 difference defines the
intrinsic non-determinism (quote ticks, cache warmth, provider flakiness). Container B passes iff diff(A,B) ⊆ that envelope:
per-symbol categorical fields (`action`, `failed_gates`) equal unless the symbol was unstable in A1↔A2; numeric fields (`entry`,
`stop`, `target`, `quantity`, `planned_risk`, `expected_value`) within the A1↔A2 spread. Run L4 **in regular hours** (real
decisions) and **outside hours** (stale-data path) — both are separate reportable cases.

**Equivalence failure = any of:** L0 mismatch (incl. `config_version`); L1 ID-level mismatch; L2/L3 diff after normalisation; L4
outside envelope; L5 unexpected write; any secret in an image; any tripwire tripped. **Any failure stops the sequence.**

**Deliverable:** `C0_EQUIVALENCE_REPORT.md` — per-level table, the raw diffs, the A1↔A2 envelope, tripwire hashes (V1/V5/V10), image
scan output, and the §13 delta list marked *observed / not observed*.

---

## 13. Platform and environment deltas (document; do **not** fix)

| Delta | Applies | Effect | Handling |
|---|---|---|---|
| **CRLF vs LF in generated text** — `open(path,"w",encoding="utf-8")` on Windows writes `\r\n` | C2 vs R2 | `PAPER_DAILY_REPORT.md`, JSON state files differ **by bytes** | normaliser folds newlines; Case 1 reports (Linux) are LF already **[V: `git show origin/main:reports/paper/…`]** |
| **Naive-date** `dt.date.today()` (D1) | Case 1: UTC · Case 2: ET | `session_date` differs 20:00 ET–00:00 ET **between the two profiles**, by design | preserved; asserted at L0 |
| **numpy 2.4.6 vs host 2.5.2** (D4) | C2 vs R2-host-3.14 datapoint | possible last-digit numeric differences | L3/L4 tolerances; recorded |
| **Python 3.11 vs 3.14** (D3) | container vs current host | interpreter behaviour | reference is 3.11; 3.14 reported only |
| **Debian vs Ubuntu** (glibc, OpenSSL, CA bundle) | C1 vs R1-gh | network/TLS edge cases only | recorded; R1-bare shares Debian so isolates C0 mechanics |
| `open()` without `encoding` (≈3 sites) | Windows cp1252 vs `C.UTF-8` | none expected | `LANG=C.UTF-8` set; matches Actions |
| File locking / `os.replace` semantics | Windows vs Linux | only legacy `scheduler.lock` path (not in C0) | n/a |
| Boot pre-warm network burst | both | writes W_dash; may hit provider rate limits (seen: TradingView breaker OPEN in sandbox) | expected; not gated |
| yfinance cache under `$HOME/AppData/Local/py-yfinance` (Windows) vs `~/.cache/py-yfinance` (Linux) | both | cache location only | inside tmpfs/volume; L5 lists it |
| `holidays` package absent | both | legacy scheduler holiday gate silently off | **not** fixed (out of C0) |
| Case 1 vs Case 2 HOME-state persistence (K2) | by profile | latent calibration/cache difference | preserved; two profiles |

---

## 14. Explicitly excluded from C0

AWS, EC2/ECS, S3, Secrets Manager, OIDC · Postgres/Redis/SQS/EventBridge · NautilusTrader · any state or ledger **migration**
or schema change · any change to `lab/paper/*`, `lab/decision_engine.py`, `automation/paper_scheduler.py`, `canonical/*` ·
fixing the naive-date behaviour · improving or emulating Case 1's cache/artifact/dedup/gate logic · changing
`paper-trading-schedule.yml` · **any** broker/live path, `BROKER_PROVIDER`, `ROBINHOOD_TRADING_ENABLED`, credentials ·
the MCP server/image/health-check repair (E1–E5) · the legacy stack (`scheduler_run`/`daily_runner`/`run_lab`) · FinBERT/torch/
robinhood/axisdirect extras · pushing any image, editing `publish-image.yml`, multi-arch builds (amd64 only; the runner is x86_64) ·
production WSGI server, auth, TLS, reverse proxy · Datadog/monitoring · fixing test-suite pollution (§8.4) or the intentionally-red tests.

---

## 15. Blockers and gates

### 15.1 C0 blocker list

| # | Blocker | Cleared when |
|---|---|---|
| **B1** | **`.env`/credential/OAuth/DB/state exposure** — `.env` is git-ignored but **not** dockerignored; OAuth tokens live inside the state dir | root `.dockerignore` additions (§4.3) + allowlist ignore (§4.2) committed; context audit and image scans (§4.4) pass |
| **B2** | No image leaves the machine (no `docker push`, no registry, no new publish workflow) | equivalence report passes; explicit user go-ahead |
| **B3** | Real Case 2 state untouched | V1 = V5 = V10 fingerprints equal (§11) |
| **B4** | Dependency manifest exists and reproduces Case 1 | F0–F8 pass (§10.5) |
| **B5** | Preflight guards implemented | §7.3 unit-tested |
| **B6** | Reference environments exist | Python 3.11 venv on host (A1) *or* R1-bare fallback declared; Docker daemon running |
| **B7** | **ID-level** test baselines recorded per reference/TZ | §8.2 files committed |
| **B8** | MCP defects recorded, not repaired | §1.4 stays in the report unchanged |

### 15.2 Sequence

```
C0 Inventory ✅ ─► C0 Container Design (this doc) ─► [GATE G1: user approves design + A1/A2]
   ─► Dependency reference freeze (§10; needs py3.11 + network; no Docker)          ─► GATE G2: F0–F8 pass, baselines stored
   ─► Hygiene commit: root .dockerignore additions + docker/ allowlist ignore        ─► GATE G3: B1 verified (context audit)
   ─► Dockerfile / Compose / entrypoint / preflight / serve_dashboard.py (Docker daemon starts here)
                                                                                     ─► GATE G4: L0 + image scans pass
   ─► A/B on COPIED state, levels L1→L6, both profiles                               ─► GATE G5: all levels pass, tripwires equal
   ─► C0_EQUIVALENCE_REPORT.md                                                       ─► GATE G6: user review
   ─► only then C1
```

Every gate is **stop-on-failure**; a failed gate produces a written finding, not a workaround.

---

## 16. Approvals requested (none block this document) and defaults

| ID | Needed at | Request | If you say nothing / decline |
|---|---|---|---|
| **A1** | G2 | Install **CPython 3.11** on the host (user-scope, e.g. `py install 3.11`) to build the R2 reference venv | R2 falls back to R1-bare + `TZ=America/New_York`; Windows→Linux delta is then measured only on 3.14 and stated as such |
| **A2** | G2 (optional) | Add a **manual-only** `c0-reference.yml` workflow (ubuntu-latest, py3.11, no cache/commit/issue/secrets, no ledger) for the true Case 1 reference | skip; R1-bare + R1-hist are used and the Debian-vs-Ubuntu delta is stated |
| **A3** | G4 | Start Docker Desktop | required before any build; nothing before G4 needs it |
| **A4** | G5 (optional) | Download the latest `paper-ledger-db-*` artifact for a **real Case 1 ledger copy** | Case 1 golden = empty first-run ledger only |

---

## Appendix A — what was done in this step (evidence log)

* **Read-only GitHub queries:** `gh run list` (12 Case 1 runs), `gh run view <id> --log` for **8** real runs (parsed only for the
  "Successfully installed" list and CPython version — logs held in the session scratch dir, not the repo), `gh secret list`
  (empty). No workflow was triggered, edited, or downloaded from.
* **Sandboxed runs (scratch `HOME`, real state untouched):** one fresh-`HOME` dashboard start with ledger/health/pre-warm
  probes (§9.2); reuse of the earlier baseline `pytest` run for the §8.4 write-set.
* **Read-only on real state:** counted `TEST` rows in `predictions.jsonl` (§8.4); nothing written.
* **Inventory edits:** two corrections to `C0_INVENTORY.md` (nondeterministic test #6; dashboard start-up vs first paper call).
* **Not done, on purpose:** no Dockerfile/Compose/constraints/scripts, no Docker daemon start, no Python install, no
  `pip install` into the repo environment, no commit/push/pull.
