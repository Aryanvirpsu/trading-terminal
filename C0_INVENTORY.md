# C0_INVENTORY.md — current system map (ground truth for the C0 container design)

_Discovery date 2026-09-21 · repo `main` @ `223f5ff` (local is 5 report-only commits behind `origin/main`) · 58,034 lines of tracked Python._

**Scope of this document:** discovery only. Nothing here changes trading logic, state, broker flags, Case 1 / Case 2
behaviour, or the build freeze (`lab/paper/`, `lab/decision_engine.py`, `automation/paper_scheduler.py`). No Docker
files were written. No AVDI / AWS / Postgres / Redis / Nautilus work was done.

**Evidence tags used below**

| Tag | Meaning |
|---|---|
| **[V]** | verified by actually running it on this machine (with `HOME`/`USERPROFILE` redirected to a scratch dir, so the real `~/.tradingview_mcp_data` was never written) |
| **[S]** | static — read from code / config; not executed |
| **[U]** | not verified and could not be from here (e.g. contents of the GitHub Actions cache, the published GHCR image) |

---

## 0. The ten findings that will shape the C0 design

Ranked by how likely each is to silently break "same terminal, same state, same decisions".

1. **State-dir resolution is inconsistent [S].** `TVMCP_DATA_DIR` is honoured by only **3** modules
   (`dashboard/tracker.py`, `sector_lookup.py`, `scan_cohort.py`). ~20 others hard-code `~/.tradingview_mcp_data`
   (`portfolio.py`, `calibration.py`, `security_master.py`, `strategy_store.py`, `edgar.py`, `run_lab.py`,
   `daily_runner.py`, `scheduler_run.py`, `scan_both.py`, `research.py`, …). The paper ledger uses its **own** var,
   `PAPER_DATA_DIR`. → In a container the only reliable mechanism is a volume mounted at `$HOME/.tradingview_mcp_data`
   with `HOME` set explicitly. Do **not** rely on `TVMCP_DATA_DIR`.
2. **Case 1 persists only the ledger, not the rest of the state [S].** The workflow caches
   `${{ runner.temp }}/tradingview_mcp_data` and points `PAPER_DATA_DIR` at its `paper/` subdir. Everything else the
   pipeline writes goes to `/home/runner/.tradingview_mcp_data/` and is **thrown away every run** (`predictions.jsonl`,
   `calibration.json`, `scan_cohort.db`, `security_master.db`, `sec_cik_map.json`, …). Case 2 keeps all of it.
   Today that has no behavioural effect (no `calibration.json` exists locally either → `calibration.adjustment()` is
   neutral 1.0 in both), but it is a **latent Case 1 ≠ Case 2 difference** that C0 must preserve, not "fix".
3. **Time zone changes `session_date` [S].** `lab/paper/*` derives the session date from `dt.date.today()` (naive local
   time) in 12+ places (`broker.py`, `journal.py`, `risk.py`, `report.py`, `options_shadow.py`, `workflow.py:_today()`).
   Host = **America/New_York (EDT) [V]**; Actions runner = **UTC**; a container defaults to **UTC**. Between 20:00 ET and
   midnight ET the date differs. → `TZ` must be an explicit, per-case container setting.
4. **Interpreter differs across the three places the code runs [V]:** host **Python 3.14.7**, Actions **3.11**,
   existing Dockerfile **3.11**. Case 1 has only ever run on 3.11. A "host vs Docker" A/B therefore compares 3.14 vs 3.11
   unless the host side is also run on 3.11.
5. **Dependency truth is split three ways [V]/[S].** `uv.lock` pins `mcp 1.12.4` and does not contain `yfinance`,
   `flask` or `holidays` at all; the Dockerfile (`uv pip install --system .`) and the Actions job (`pip install -e .`)
   **both ignore the lock**; the host has `mcp 2.1.1` (PyPI latest is now 2.2.0). Several imports are undeclared
   (`python-dotenv`, `numpy`, `holidays`, `typing_extensions`) and only work transitively.
6. **The MCP server does not start on this host [V], and the published-image workflow can't notice [S].**
   `mcp 2.x` removed `mcp.server.fastmcp` → `ModuleNotFoundError`. Pinned `mcp 1.12.4` on Python 3.14 fails differently
   (`issubclass() arg 1 must be a class`). `pyproject.toml` allows `mcp>=1.12.0`, so a fresh Docker build resolves 2.x.
   `publish-image.yml` only *builds* (never runs) the image, so it stays green. The Dockerfile `HEALTHCHECK` calls
   `/health`, and `server.py` defines no such route [S] → it cannot pass. **The paper pipeline and dashboard do not
   import `mcp` at all [V — dashboard ran fine on mcp 2.x]**, so this is isolated to the MCP server.
7. **The dashboard binds `127.0.0.1` hard-coded (`dashboard/app.py:1765`) [V].** Confirmed unreachable via the LAN IP.
   A Docker `-p` publish will not reach it. Fixable without touching `app.py`: a tiny entry script that imports the
   module and calls `app.run(host="0.0.0.0")`.
8. **"Read" paths write to the ledger [V].** Any dashboard hit on `/api/paper/*` (and `symbol_snapshot`) calls
   `paper.db.connect()`, which **creates the ledger file if missing and runs schema migrations**. Verified in a
   sandbox: empty v2 ledger created, WAL grew 268 KB in ~20 s, **no** signals/orders/fills/equity rows written.
   Consequence: pointing a container's dashboard at the real Case 2 directory would migrate that ledger v1 → v2
   (additive columns) on the first paper-endpoint call. The local ledger is still schema **v1**; code is v2.
   _Refinement [V, `C0_CONTAINER_DESIGN.md` §9]: dashboard **start-up, `/` and `/api/health` do not create or open the
   paper ledger**; the boot pre-warm does write `portfolio.db`, `security_master.db` and `last_candidates.json`. Only
   `/api/paper/*` (and `symbol_snapshot`'s account strip) touch the ledger._
9. **SQLite WAL + Docker Desktop bind mounts [S].** The paper ledger and `scan_cohort.db` are WAL databases. WAL needs a
   shared-memory `-shm` file and is unsafe on network/virtualised filesystems. Docker Desktop on Windows bind-mounts
   cross a VM boundary. → Use a **named volume** (or a single container / one filesystem), never a Windows bind mount,
   for anything two processes share. (Docker CLI 29.7.2 is installed; the **daemon is not running** [V].)
10. **Secrets could ride along in a local image build [S].** `.env` is git-ignored but **not** in `.dockerignore`;
    `COPY . .` would bake it into a locally built image. CI builds are safe (clean checkout has no `.env`). Also the
    OAuth tokens live *inside the data dir* (`robinhood_mcp_token.json`, `axisdirect_session.json`) — none exist locally
    today [V], but the data dir must never be baked into an image or copied into a registry.

---

## 1. Runnable entry points

| # | Entry point | Invocation | What it is | Long-running? | Owns |
|---|---|---|---|---|---|
| 1 | `automation/paper_scheduler.py` | `python automation/paper_scheduler.py {premarket\|hours\|postmarket\|all\|status\|report [date]\|performance\|options} [--dry-run]` | **The paper pipeline.** One-shot CLI. `all` = premarket → market_hours → postmarket in one process. Default cmd is `status`. Writes `PAPER_DAILY_REPORT.md` at repo root. | no (one-shot, ~1 min in CI) | paper execution, scanning (via `paper.strategies`), reporting |
| 2 | `dashboard/app.py` | `python dashboard/app.py` → `http://127.0.0.1:5057` (`DASHBOARD_PORT`) | **The Flask terminal** (1,765 lines + `terminal.html` 2,279 lines). Dev server, `debug=False`. `_prewarm()` starts a daemon thread **at import time** (not only under `__main__`). | yes | dashboard UI/API, background cache warm |
| 3 | `src/tradingview_mcp/server.py` | `tradingview-mcp [stdio\|streamable-http] [--host H --port P]` (`HOST`/`PORT` env; default `127.0.0.1:8000`) | **Upstream MCP server** (FastMCP, ~1,600 lines, 30+ tools). Only console-script in `pyproject.toml`. | yes (http) / per-session (stdio) | MCP tools incl. legacy `strategy-500` `paper_trade`, broker tools |
| 4 | `automation/scheduler_run.py` | `python automation/scheduler_run.py [--stage full\|light] [--force]` | **Legacy scheduler wrapper.** NYSE-calendar gate, lock file, retries; spawns `daily_runner.py` (protect → scans) and `lab/run_lab.py` via `subprocess`. Written for Windows Task Scheduler (`daily_runner.py:37` comment). No scheduled task found on this machine [V, read-only `Get-ScheduledTask`]. | no | legacy scheduling |
| 5 | `automation/daily_runner.py` | `--stage {protect\|scans\|all}` | Legacy `strategy-500` account: equity snapshot, **auto-closes stopped positions**, settles expired options; `scans` = `scan_both` + `journal_lab` + `catalyst_scan`. `auto_paper` opening of new positions is **disabled** (comment at ~line 129). | no | legacy account management |
| 6 | `automation/scan_both.py` | `python automation/scan_both.py` | Both-direction scanner; executes nothing; writes `last_scan_both.json`. | no | scanning (legacy) |
| 7 | `automation/research_run.py` | `python automation/research_run.py [update\|status] [--preset P] [--no-track]` | Read-only end-to-end research run; writes tracker DB. | no | research |
| 8 | `lab/run_lab.py` | `python lab/run_lab.py` | Headless "self-evolving lab" (journal_lab, catalyst_scan, strategy_lab, decision sweep, calibration). | no | learning jobs |
| 9 | 30 other `lab/*.py` with `__main__` | e.g. `python lab/calibration.py`, `lab/decision_engine.py`, `lab/auto_paper.py` … | Standalone CLIs / self-tests for individual lab modules. | no | ad hoc |
| 10 | `dashboard/security_master.py` | `__main__` | Builds the ticker universe DB. | no | data prep |
| 11 | `scripts/risk_policy_simulation.py` | `EDGE_SHIFT=…` | Offline simulation. | no | research |
| 12 | `openclaw/trading.py` | `python3 trading.py {price\|snapshot\|backtest\|…}` | CLI shim for an external "OpenClaw" agent. Hard-codes `/root/.local/share/uv/tools/…/python3.12/site-packages`. | no | **external** |
| 13 | `.github/workflows/paper-trading-schedule.yml` | `workflow_dispatch` (primary, from cron-job.org) + 2 `schedule` crons (backup) | **Case 1.** Runs entry #1 `all` on a bare `ubuntu-latest` runner. | no | Case 1 scheduling, cache, report commit |
| 14 | `.github/workflows/publish-image.yml` | push to `main`, `v*` tags, PRs (build only) | Builds multi-arch image → `ghcr.io/<owner>/<repo>`. | no | image publishing |

**Tests:** `tests/unit/` (41 files) + `tests/e2e/test_stock_view_playwright.py` (opt-in via `STOCK_VIEW_E2E`, needs a live
server on `:5057` + `playwright`, not installed). No `conftest.py`, no `pytest.ini`; run from repo root:
`python -m pytest tests/unit -q`.

---

## 2. Process ownership

| Concern | Owning process | Notes |
|---|---|---|
| Dashboard / UI / HTTP API | `dashboard/app.py` (Flask) | Single process, threaded dev server. |
| Scheduling — **Case 1** | GitHub (cron-job.org → `workflow_dispatch`; `schedule` as same-day backup) | Holiday/day guard, dedup via committed `reports/paper/<utc-date>.md`. |
| Scheduling — **Case 2** | **nobody** (manual `python automation/paper_scheduler.py …`) | No scheduled task found [V]. Last local ledger activity: signals 2026-09-09, DB mtime Sep 10. |
| Scheduling — legacy stack | `scheduler_run.py` (intended: Windows Task Scheduler) | Not running here; dashboard `/api/scheduler` therefore reports `stale` [V]. |
| Scanning (paper) | inside `paper_scheduler.py premarket` → `lab/paper/strategies.py` → `dashboard/research.py` | Dashboard modules are **imported as libraries by the paper pipeline** (no Flask needed). |
| Scanning (dashboard) | `dashboard/scanner.py`, `symbol_snapshot.py`, `scan_cohort.py` | Also runs a boot pre-warm thread. |
| Paper execution / fills / risk | `lab/paper/{broker,fills,risk,journal}.py` inside entry #1 | Single writer per run. |
| MCP | `tradingview-mcp` process | Independent of dashboard/paper. Shares `portfolio.db` and `last_candidates.json` with them. |
| Background jobs | (a) dashboard `_prewarm` daemon thread; (b) FinBERT `warm_async()` thread (only if `SENTIMENT_MODEL_ENABLED`); (c) legacy `subprocess` fan-out from `scheduler_run.py`; (d) `robin_stocks` subprocess in `core/broker/robinhood.py` | No queue, no worker pool other than in-process `ThreadPoolExecutor`s (`DECISION_ENGINE_WORKERS`, `STRATEGY_SCAN_WORKERS`). |

---

## 3. Data-flow graph (derived from imports and call sites, not assumptions)

```
 CASE 1 (cloud)                                        CASE 2 (local)
 cron-job.org ──workflow_dispatch──┐                   (manual shell)
 GitHub schedule (backup) ─────────┤                        │
                                   ▼                        │
      gate: holiday/day  →  dedup: reports/paper/<UTC-date>.md already in git?
                                   │                        │
      restore ledger: cache "paper-ledger-*" → else artifact "paper-ledger-db-*"
                                   │                        │
                                   ▼                        ▼
                    python automation/paper_scheduler.py  {all | premarket | hours | postmarket | status …}
                                   │
                                   ▼
                    lab/paper/workflow.py ── premarket ──┬─► strategies.py ──► dashboard/research.py ──► [network: Yahoo, Finnhub,
                                   │                     │                       (universe, price history)       TradingView, SEC, news RSS …]
                                   │                     │                 └─► dashboard/{sector_map,market_regime}.py
                                   │                     ├─► lab/decision_engine.py ──► lab/{providers,freshness,data_quality}.py
                                   │                     │        ├─ calibration.log_prediction ─► HOME/predictions.jsonl   (append)
                                   │                     │        └─ calibration.adjustment ◄──── HOME/calibration.json     (absent ⇒ neutral)
                                   │                     ├─► canonical_bridge.py ──► canonical/*  (pure policy)
                                   │                     │        └─► src/tradingview_mcp/core/services/strategy_service (option chains)
                                   │                     ├─► journal.py · broker.py · fills.py · risk.py ─► db.py ─► PAPER_DATA_DIR/<ledger>.db
                                   │                     └─► options_shadow.py (record) ─────────────────────────────► same DB (options_shadow)
                                   ├── market_hours ─► broker fills/stops/targets · journal.update_excursions · options_shadow.resolve_outcomes
                                   └── postmarket ──► report.daily ─► PAPER_DAILY_REPORT.md  (repo root, overwritten each run)
                                   │
        Case 1 only: save cache · upload artifact (paper/*.db) · commit report+reports/paper/<date>.md · open/close failure issue

 Browser ─► dashboard/app.py  (Flask :5057, 127.0.0.1)
              ├─ /api/paper/*  ─► lab/paper/{broker,report,journal,options_shadow}  ─► the SAME ledger DB  (opens RW + migrates)
              ├─ /api/scan, /api/terminal/*, /api/symbol/* ─► dashboard/{scanner,research,symbol_snapshot,…} ─► network
              ├─ scan_cohort.db · strategy_snapshots.db · setup_tracker.db · security_master.db · sector_lookup.json · analysis_snapshots.json
              ├─ /api/scheduler, /api/health ◄─ HOME/{scheduler_status,last_daily_run,last_scan_both,catalysts,lab_report}.json  (legacy stack writes these)
              └─ tradingview_mcp.core.portfolio ─► HOME/portfolio.db  (legacy strategy-500 ledger)

 LEGACY:  scheduler_run.py ─subprocess─► daily_runner.py (protect → scans) ─► scan_both · journal_lab · catalyst_scan     ─► HOME/*.json
                           └subprocess─► lab/run_lab.py ─► journal_lab · catalyst_scan · strategy_lab · decision sweep · calibration

 MCP:     tradingview-mcp (FastMCP, :8000) ── separate process ── shares HOME/portfolio.db, last_candidates.json
          Nothing in dashboard/ or lab/paper/ imports `mcp`.   TradingView is an *optional confirmation* provider (TRADINGVIEW_ENABLED).

 Broker:  BROKER_PROVIDER=none / ROBINHOOD_TRADING_ENABLED=false.  paper_scheduler.py never imports a broker client.
          Robinhood read-only data path: lab/robinhood_mcp.py → https://agent.robinhood.com/mcp/trading (OAuth token in HOME).
```

---

## 4. Runtime and dependencies

### 4.1 Interpreters

| Where | Python | Install command | Resolves deps from |
|---|---|---|---|
| Host (Case 2, dev) | **3.14.7** | user-site pip [V] | whatever is installed |
| Actions (Case 1) | 3.11 (`actions/setup-python@v5`) | `python -m pip install -e .` | PyPI, **ignores `uv.lock`**, extras **not** installed (no Flask) |
| Existing Dockerfile | 3.11-slim | `uv pip install --system .` | PyPI, **ignores `uv.lock`**, extras **not** installed |

`pyproject.toml`: `requires-python >=3.10`, setuptools `packages.find where=src` (so **only `src/tradingview_mcp` is a
package**). `lab/`, `dashboard/`, `canonical/`, `automation/` are **not** packaged; they are reached via
`sys.path.insert(...)` hacks in `paper_scheduler.py`, `workflow.py`, `canonical_bridge.py`, `app.py`, etc. Consequence:
the code must run **from the repo checkout layout** (`/app` in the current image works because `COPY . .` keeps it).

### 4.2 Declared vs actually imported (third-party, AST scan of all tracked `.py`)

| Module | Used in | Declared in `pyproject`? | Host [V] | `uv.lock` |
|---|---|---|---|---|
| `requests` | dashboard, lab, src | ✅ `>=2.32` | 2.34.2 | 2.32.4 |
| `feedparser` | lab, src | ✅ | 6.0.14 | 6.0.12 |
| `yfinance` | dashboard, lab | ✅ `>=1.7.0` | 1.7.0 | ❌ **absent** |
| `tradingview-screener` | src | ✅ `==3.0.0` (pinned on purpose) | 3.0.0 | 3.0.0 |
| `tradingview-ta` | src | ✅ | 3.3.0 | 3.3.0 |
| `mcp` | src (`server.py` only) | ✅ `mcp[cli]>=1.12.0` | **2.1.1** | **1.12.4** |
| `flask` | dashboard | extra `[dashboard]` only | 3.1.3 | ❌ **absent** |
| `python-dotenv` | lab, src | ❌ undeclared (transitive) | 1.2.3 | 1.1.1 |
| `numpy` | dashboard | ❌ undeclared (transitive via yfinance/pandas) | 2.5.2 | 2.2.6 / 2.3.2 |
| `holidays` | `automation/scheduler_run.py` | ❌ **undeclared, not installed** | ✗ | ❌ |
| `torch`, `transformers` | lab (FinBERT) | extra `[finbert]` only | ✗ | — |
| `robin_stocks` | src (`core/broker/robinhood`) | extra `[robinhood]` | ✗ | — |
| `rapidapi_axisdirect` | src | extra `[axisdirect]` | ✗ | — |
| `snaptrade_client` | lab | ❌ undeclared, optional | ✗ | — |
| `playwright` | tests/e2e | ❌ | ✗ | — |
| `pytest` | tests | dev-dependency | 9.1.1 | 9.0.3 |

Semantic gotchas from this table:

* **`holidays` missing ⇒ `scheduler_run.is_trading_day()` silently degrades** — the `try/except` falls through to
  "trading day", so the holiday gate is *off* on host and would be off in Docker [S].
* The `[dashboard]` extra is not installed by the current image ⇒ **the current image cannot run the terminal**.
* `pandas` is only a transitive dependency; 2.3.3 here vs 2.3.1 locked.

### 4.3 Baseline test result (the number C0 must match) [V]

`python -m pytest tests/unit -q` on host Python 3.14.7, `HOME` redirected to scratch, ignoring e2e:

**1,272 passed · 6 failed · 25 s.** The 6 are all understood and none are regressions:

| Failing test | Cause |
|---|---|
| `test_canonical_v11_invariants::test_oi_policy_has_no_overlapping_hard_and_soft_ranges` | **Intentionally red** — docstring: "stays red forever as an honest record" |
| `test_canonical_v11_invariants::test_contract_quality_identical_across_pipelines` | **Intentionally red** — same |
| `test_finbert_sentiment::test_transformers_available` | optional `torch`/`transformers` not installed |
| `test_finbert_service::test_available` | same |
| `test_finbert_service::test_score_force` | same (`'lexical' == 'finbert'`) |
| `test_finbert_service::test_cache_behavior` | **nondeterministic** — asserts `(t3-t2) < (t1-t0)` on two wall-clock timings (`perf_counter`); with no model both calls are near-instant, so it fails/passes by microseconds. _Corrected in `C0_CONTAINER_DESIGN.md` §8: 5 deterministic failures + 1 nondeterministic._ |

**C0 pass criterion for tests:** identical set — 1,272 pass and exactly these 6 fail (or 4 of them pass if the
`[finbert]` extra is deliberately baked in, in which case record that as a documented delta).

---

## 5. Environment variables

`lab/_config.py` and `proxy_manager.py` call `load_dotenv(<repo>/.env, override=False)` — a real environment variable
always wins over `.env`. In a container there is no `.env` (and there should not be): pass everything via environment.
**No variable is strictly required** — every one has a default and the paper pipeline runs with none set (Case 1 sets
only `PAPER_DATA_DIR` plus optional keys).

### 5.1 Required
_None._ (Minimum for the dashboard: nothing. Minimum for a container that must not lose state: `HOME` + a volume.)

### 5.2 Runtime / location (set these deliberately in a container)

| Var | Default | Read by | Note |
|---|---|---|---|
| `HOME` / `USERPROFILE` | OS | ~20 modules via `expanduser("~/.tradingview_mcp_data")` | **the real state-dir switch** (finding #1) |
| `PAPER_DATA_DIR` | `~/.tradingview_mcp_data/paper` | `lab/paper/db.py:32` (import-time) | Case 1 sets this to the cached dir |
| `PAPER_LEDGER` | `robinhood_500_baseline` | `db.py:46` | picks the ledger **file name**; a new value silently starts an empty ledger |
| `TVMCP_DATA_DIR` | `~/.tradingview_mcp_data` | only `tracker`, `sector_lookup`, `scan_cohort` | **do not rely on it** |
| `TRACKER_DB`, `SCAN_COHORT_DB`, `STRATEGY_STORE_DB` | under data dir | those modules | per-DB overrides |
| `DASHBOARD_PORT` | `5057` | `app.py:1764` | host is **not** configurable (finding #7) |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | `server.py:1582` | MCP; Dockerfile passes `--host 0.0.0.0` |
| `TZ` | OS | implicit via `date.today()` | **not currently set anywhere** (finding #3) |
| `DEBUG_MCP` | unset | `server.py` | |

### 5.3 Optional — provider keys / feature switches

`FINNHUB_API_KEY`, `FRED_API_KEY`, `ALPHAVANTAGE_API_KEY` (the three Case 1 passes from secrets),
`SNAPTRADE_CLIENT_ID`, `SNAPTRADE_CONSUMER_KEY`, `GEMINI_API_KEY` (presence flag only),
`TRADINGVIEW_ENABLED` (default `true`), `SENTIMENT_MODEL_ENABLED` (default `false`), `SENTIMENT_MODEL`,
`FINBERT_TIMEOUT`, `EMBEDDINGS_MODEL[_ENABLED]`, `FORECAST_MODEL[_ENABLED]`, `ENGINE_VERSION`,
`PROXY_HOST/PORT/USERNAME_PREFIX/PASSWORD/SESSION_MIN/SESSION_MAX/ENABLED` (`PROXY_ENABLED` defaults `true` but the
proxy is only active if prefix **and** password are set), `ROBINHOOD_MCP_URL`, `ROBINHOOD_MCP_TIMEOUT`,
`ROBINHOOD_MCP_REDIRECT_URI` (default `http://localhost:8765/callback`).

### 5.4 Optional — tuning knobs (change *decisions*; treat as part of the config, not the environment)

* **Paper config (`lab/paper/config.py`, all `PAPER_*`):** `PAPER_INITIAL_CASH/EQUITY`, `PAPER_BUYING_POWER`,
  `PAPER_MAX_LOSS_PER_TRADE`, `PAPER_MAX_POSITION_NOTIONAL`, `PAPER_MIN_CASH_RESERVE_USD`, `PAPER_MAX_DAILY_LOSS_USD`,
  `PAPER_MAX_DRAWDOWN_USD`, `PAPER_MAX_ENTRIES_PER_DAY`, `PAPER_MAX_OPEN`, `PAPER_MAX_PER_SECTOR`,
  `PAPER_MAX_CORRELATED`, `PAPER_COOLDOWN_LOSSES/DAYS`, `PAPER_MAX_OPTION_PREMIUM`, `PAPER_SLIPPAGE_BPS`,
  `PAPER_GAP_SLIPPAGE_BPS`, `PAPER_FEE_PER_SHARE`, `PAPER_STRATEGIES`. **`config_version` = `cfg-` + SHA-1[:10] of the
  *environment values* of ~30 named keys (`db.py:~345–372`: `TRADINGVIEW_ENABLED`, `DECISION_*`, `SENTIMENT_MODEL_ENABLED`,
  `PAPER_LEDGER`, and the `PAPER_*` account/risk/option/execution keys). It is stamped on every signal and shown in
  every report.** With default env it is **`cfg-a0eede144e`** (identical on every Case 1 report, 2026-09-14 → 09-21 [V]).
  Unset and empty-string hash identically (`os.environ.get(k, '')`), but any set value — even `True` vs `true` —
  produces a different version.
  **L0 check for C0: a default-env container must report `cfg-a0eede144e`.**
* **Decision engine:** `DECISION_MIN_DATA_QUALITY`, `DECISION_CONVICTION_MIN`, `DECISION_ENGINE_WORKERS`,
  `DECISION_ALLOW_FALLBACK_TRADEABLE`, `DECISION_ALLOW_HIGH_DISAGREEMENT`.
* **Freshness / regime / risk / scan:** `FRESH_<KIND>_<EDGE>_S`, `MKT_PREMARKET_OPEN_H`, `REGIME_RISK_BAND`,
  `RISK_PROFILE`, `SCAN_ALLOW_LEVERAGED`, `SCAN_COHORT_TOP_N`, `SIZE_FRACTIONAL`, `OPT_SPREADS_ALLOWED`,
  `OPT_MODEL_GREEKS`, `HIST_LOOKBACK_RANGE`, `SECTOR_LOOKUP_TTL_DAYS`, `FINNHUB_RATE_PER_MIN`,
  `TERMINAL_QUOTE_TTL_S`, `STRATEGY_ANALYSIS_TTL`, `STRATEGY_SCAN_WORKERS`, `STRATEGY_DEEP_CAP`,
  `TRADINGVIEW_MCP_*` (socket timeout, cache/stale TTL, retry delays/jitter, breaker, inflight, min interval, batch budget).

### 5.5 DANGEROUS — must never be set/changed by C0 work

| Var | Why |
|---|---|
| `BROKER_PROVIDER` (must stay `none`) | selects a live-order broker adapter |
| `ROBINHOOD_TRADING_ENABLED` (must stay `false`) | gate for the Robinhood order path |
| `ROBINHOOD_USERNAME`, `ROBINHOOD_PASSWORD`, `RH_USERNAME`, `RH_PASSWORD`, `RH_MFA`, `RH_REQUEST_JSON` | brokerage credentials (passed to a `robin_stocks` subprocess) |
| `AXISDIRECT_CLIENT_ID`, `AXISDIRECT_AUTHORIZATION_KEY` | live Indian-broker execution |
| `PAPER_MARGIN_ENABLED`, `PAPER_ALLOW_SHORTING`, `PAPER_ALLOW_NAKED_OPTIONS`, `PAPER_ALLOW_SPREADS`, `PAPER_ALLOW_MULTI_LEG`, `PAPER_OPTIONS_SHADOW_ONLY` | break the "real $500 cash account, no margin/short/naked, options shadow-only" simulation contract |
| `DECISION_ALLOW_STALE`, `DECISION_ALLOW_FALLBACK_TRADEABLE`, `DECISION_ALLOW_HIGH_DISAGREEMENT` | loosen stale-data / gate protections (freeze: only if proven by paper results) |
| `PAPER_LEDGER`, `PAPER_DATA_DIR`, `HOME` | silently redirect the evidence ledger (new empty ledger, or a demo ledger mixed into real stats) |
| `SNAPTRADE_*`, `PROXY_PASSWORD`, `*_API_KEY` | secrets — never bake into an image or log |

Also present only in `.env.example` (commented): `ZERODHA_*`, `UPSTOX_*`, `ANGELONE_*` — no code reads them today [S].

---

## 6. Files and directories read / written

### 6.1 Inside `~/.tradingview_mcp_data/` (the state directory)

"Case 1 kept?" = survives between Actions runs. Only `paper/` does.

| Path | Format | Writer(s) | Reader(s) | Honours `TVMCP_DATA_DIR` | Case 1 kept? | On this host [V] |
|---|---|---|---|---|---|---|
| `paper/<ledger>.db` (`robinhood_500_baseline.db`) | SQLite **WAL** | `lab/paper/*` (workflow, broker, journal, options_shadow); **also the dashboard on open (migration)** | dashboard `/api/paper/*`, `paper_scheduler` | no (`PAPER_DATA_DIR`) | **yes** (cache + artifact) | 225 KB, schema **v1**, 15 signals / 0 orders / 12 shadow rows |
| `paper/archive/<ledger>.<ts>.<reason>.db` | SQLite | `db.archive_ledger()` | `list_ledgers()` | no | — | absent |
| `portfolio.db` | SQLite (DELETE mode) | `core/portfolio.py` (MCP tools, legacy `strategy-500`, `daily_runner`) | dashboard, MCP | no | no | 64 KB, 1 user row, all else empty |
| `scan_cohort.db` | SQLite **WAL** | `dashboard/scan_cohort.py` | dashboard | **yes** | no | 192 KB, 1 run / 5 observations |
| `strategy_snapshots.db` | SQLite | `dashboard/strategy_store.py` | dashboard | no | no | 36 KB, 1 snapshot |
| `security_master.db` | SQLite | `dashboard/security_master.py` | dashboard/search | no | no | 28 KB, **0 rows** (built lazily from network) |
| `setup_tracker.db` | SQLite | `dashboard/tracker.py` | dashboard | **yes** | no | absent |
| `predictions.jsonl` | JSONL, append-only | `lab/calibration.log_prediction` (called from `decision_engine.py:766` on **every** evaluation) | `calibration.calibrate()` | no | **no** | 829 KB, 5,051 lines; **line 1 is a `"TEST"` symbol** (2026-08-26; looks like a test artefact — origin not traced); mtime Sep 14 |
| `calibration.json` | JSON | `calibration.calibrate()` (run by `run_lab`) | `calibration.adjustment()` (**called from `decision_engine.py:529`**, silent `except → 1.0`) | no | no | **absent** ⇒ neutral |
| `sec_cik_map.json`, `sec_company_tickers.json` | JSON cache | `lab/edgar.py`, `security_master.py` | same | no | no | `sec_cik_map.json` 230 KB present |
| `sector_lookup.json` | JSON cache (atomic tmp+replace) | `dashboard/sector_lookup.py` | same | **yes** | no | absent |
| `analysis_snapshots.json`, `last_candidates.json` | JSON | `dashboard/research.py`, `strategy_service.py` | same | no | no | absent |
| `last_daily_run.json`, `daily_run.log` | JSON / log | `daily_runner.py` | dashboard `/api/scheduler`, `/api/health` | no | no | absent |
| `last_scan_both.json` | JSON | `scan_both.py` | dashboard | no | no | absent |
| `scheduler_status.json`, `scheduler.lock` | JSON / lock (30-min stale) | `scheduler_run.py` | dashboard | no | no | absent |
| `journal_lab.json`, `catalysts.json`, `champions.json`, `lab_report.json`, `lab.log`, `forecast_benchmark.json`, `sentiment_benchmark.json` | JSON / log | `lab/*` | dashboard, `run_lab` | no | no | absent |
| `robinhood_mcp_token.json`, `robinhood_mcp_client.json`, `robinhood_mcp_pkce.json` | JSON — **OAuth secrets** | `lab/robinhood_mcp.py` | same | no | no | absent |
| `axisdirect_session.json` | JSON — **broker session secret** | `core/broker/axisdirect.py` | same | no | no | absent |

### 6.2 Outside the state directory

| Path | Access | Note |
|---|---|---|
| `<repo>/PAPER_DAILY_REPORT.md` | **written** by `paper_scheduler.py` (`_ROOT`) | In a container this lands in the image FS unless mapped out. Case 1 copies it to `reports/paper/<utc-date>.md` and commits it. |
| `<repo>/reports/paper/*.md` | written by the Actions job only | also the **dedup marker** for the workflow |
| `<repo>/.env` | read (optional) by `lab/_config.py`, `proxy_manager.py` | git-ignored; **not** dockerignored |
| `~/.tokens/robinhood.pickle` | read/write by `core/broker/robinhood.py` | `robin_stocks` session cache (secret); only if `BROKER_PROVIDER=robinhood` |
| `<repo>/src/tradingview_mcp/coinlist/*.txt` | read | package data; MCP `exchanges://list` |
| `~/.openclaw/tools/trading.py`, `/root/.local/share/uv/tools/…` | read | external OpenClaw agent; not part of this runtime |
| `<repo>/{lab,dashboard,canonical,src,automation}` | read (`sys.path`) | code must be at the checkout layout |

---

## 7. SQLite schemas and JSONL state

Inspected read-only [V]. (My read-only `mode=ro` connections briefly created 0-byte `-wal`/`-shm` side-files next to
the WAL databases in your real data dir; I removed exactly those four files afterwards — `.db` mtimes/sizes are
unchanged: paper Sep 10, scan_cohort Sep 9.)

**`paper/<ledger>.db` — `SCHEMA_VERSION = 2` in code; local file is v1.** `PRAGMA journal_mode=WAL`,
`foreign_keys=ON`, `synchronous=NORMAL`; process-wide connection with an `RLock`.
Tables: `meta`, `signals` (39 cols incl. `gates_json`, `provenance_json`, `outcome*`, `mfe/mae`), `orders`, `fills`,
`positions`, `equity` (one row per `session_date`, `INSERT OR REPLACE`), `audit` (append-only), `options_shadow`
(31 cols on v1 + 18 evidence columns added by migration 2). 11 indexes.
Local counts: signals 15, options_shadow 12, audit 53, equity 3, orders/fills/positions 0.

**`portfolio.db`** (legacy `strategy-500`, user-scoped): `users`, `positions`, `option_positions`, `trade_history`,
`option_trade_history`, `trade_journal`, `missed_trades`, `post_trade_lessons`, `equity_snapshots`, `ai_tasks_queue`.
No migrations table; DELETE journal mode.

**`scan_cohort.db`** (WAL): `scan_runs`, `scan_observations`, `observation_events`, `observation_snapshots`, `schema_meta`.
**`strategy_snapshots.db`**: `snapshots`, `meta`. **`security_master.db`**: `securities`, `master_meta`.
**`setup_tracker.db`**: created on demand by `tracker.py` (not present here).

**`predictions.jsonl`** — one JSON object per line: `{"ts","symbol","direction","decision","quality","p_direction","p_trade"}`.
Append-only; unbounded growth (5,051 lines / 829 KB so far). It is the input to `calibrate()`, which needs matched
closed trades before `calibration.json` is ever marked `sufficient`.

---

## 8. Case 1 — GitHub Actions cache state, exactly

`.github/workflows/paper-trading-schedule.yml` (Python 3.11, `ubuntu-latest`, UTC, `concurrency: paper-trading-ledger`,
`cancel-in-progress: false`, permissions `contents: write`, `issues: write`, `actions: read`).

1. **Trigger.** Primary: `workflow_dispatch` fired by an external cron service; backup: two `schedule` crons
   (`30 13` and `30 14`, Mon–Fri). `force` input bypasses the gate.
2. **Gate step.** Inline Python computes NYSE holidays (no external package) and a same-day window (`06:00–20:00 ET`) for
   `schedule` fires; `workflow_dispatch` skips the window but still respects holidays.
3. **Dedup step.** If `reports/paper/<UTC-date>.md` already exists in the checkout, skip everything. **Workflow state is
   therefore partly stored in git.**
4. **Cache key.** `paper-ledger-<UTC yyyymmdd-HHMMSS>` — unique per run; restore uses `restore-keys: paper-ledger-` so it
   gets the newest prior cache. Path = `${{ runner.temp }}/tradingview_mcp_data` (whole dir, but only `paper/` is ever
   written there).
5. **Fallback.** If no cache hit (GitHub evicts caches unused ≥7 days) and no `*.db` present: `gh run list --status
   success` → first run → artifact named `paper-ledger-db-*` → `gh run download` into `…/paper`. If none found, **starts a
   fresh $500 ledger** (expected only on the very first run).
6. **Run.** `pip install -e .` then `python automation/paper_scheduler.py all` with `PAPER_DATA_DIR=${{ runner.temp }}/tradingview_mcp_data/paper`
   and optional `FINNHUB_API_KEY`, `FRED_API_KEY`, `ALPHAVANTAGE_API_KEY` from secrets. No other env is set, so every
   `PAPER_*`/`DECISION_*` value is the code default.
7. **Save (always).** `actions/cache/save` of the same path, then `upload-artifact` `paper-ledger-db-<run_number>`
   with glob `paper/*.db` (**excludes `-wal`/`-shm`**; safe only if the DB was cleanly closed — locally the WAL was
   empty at rest [V], suggesting a clean close) with 90-day retention.
8. **Publish.** Copy `PAPER_DAILY_REPORT.md` → `reports/paper/<date>.md`, commit as `paper-trading-bot` with
   `[skip ci]`, push to `main`.
9. **Failure handling.** Opens/updates a "Cloud paper-trading run failed" issue; closes it on the next clean run.

Things **not** persisted between Case 1 runs: everything in §6.1 except `paper/`. **Nothing about Case 1 runs in a
container today**; C0 must leave this workflow untouched.

[U] I could not read the live cache or artifacts (needs a download, which I did not do without your OK). The last 3
runs listed by `gh run list` were green (latest 2026-09-21 19:39Z, a 12-second run — consistent with the dedup guard
skipping a second same-day fire).

---

## 9. Outbound network and ports

**Outbound (all from `requests`/`urllib`/`yfinance`/`feedparser`; no inbound dependency):**

| Host | Used for | Auth |
|---|---|---|
| `query1/query2.finance.yahoo.com`, `finance.yahoo.com`, `fc.yahoo.com` | quotes, history, options (primary price source) | none |
| `scanner.tradingview.com` (+ `tradingview-ta`) | screener / technicals (**optional** confirmation) | none; circuit breaker + retry in `screener_provider.py` |
| `finnhub.io` | quotes, sector lookup, news | `FINNHUB_API_KEY` (rate-limited 55/min default) |
| `api.stlouisfed.org` | FRED macro | `FRED_API_KEY` |
| `www.alphavantage.co` | fallback | `ALPHAVANTAGE_API_KEY` |
| `data.sec.gov`, `www.sec.gov` | EDGAR filings, ticker→CIK | none (UA string) |
| `www.nasdaqtrader.com`, `www.nseindia.com` | symbol directories | none |
| `search.cnbc.com`, `feeds.content.dowjones.io`, `news.google.com`, `www.coindesk.com`, `cointelegraph.com` | news RSS | none |
| `api.stocktwits.com`, `www.reddit.com` | social sentiment | none |
| `api.coingecko.com` | crypto | none |
| `ipinfo.io` | proxy self-check (`proxy_manager.py:124`) | none |
| `agent.robinhood.com/mcp/trading`, `api.robinhood.com` | **read-only** Robinhood data / (gated) robin_stocks | OAuth token in state dir / credentials |
| `p.webshare.io` | optional rotating proxy | `PROXY_*` |
| HuggingFace hub | FinBERT/embeddings model download (**only** if `*_MODEL_ENABLED=true`) | none |

**Ports.** Dashboard **5057/tcp** bound to `127.0.0.1` (not configurable) [V]. MCP **8000/tcp**
(`127.0.0.1` default; Dockerfile `--host 0.0.0.0`; compose maps `8080:8000`). Robinhood OAuth redirect
`localhost:8765` (interactive login only). Playwright e2e expects `127.0.0.1:5057`. **No inbound port is needed for the
paper pipeline.**

[V] In the sandbox run, the dashboard's boot pre-warm logged `circuit breaker OPEN after 4 failures` against the
TradingView screener and `scanned=0` — cause not investigated (no keys, fresh HOME). Yahoo-backed paths still returned 200.

---

## 10. Startup order and dependencies

* **Paper run (`paper_scheduler.py all`)** — strictly sequential, single process: DB open + migrate → `premarket`
  (scan → evaluate → journal → place ≤3 → shadow-record options) → `market_hours` (fills/stops/targets/excursions/shadow
  resolve) → `postmarket` (expire, reconcile, report). Idempotence guards: same-day symbol dedup in `premarket`
  (`workflow.py:122–135`); report is deterministic for a date. Requires network + a writable paper dir. **Needs no other
  process.**
* **Dashboard** — independent of everything else. Order at import: `_prewarm()` thread → `_SM.ensure_loaded()`
  (builds/loads `security_master.db`, network) → account state → regime → candidates (`find_trades`, heavy) →
  FinBERT (if enabled). It serves immediately (2.1 s to first 200 [V]) with a cold state; panels fill as caches warm.
* **Dashboard ↔ paper coupling** is **only the shared ledger file** (plus shared `lab/`/`dashboard/` modules). If both run
  against one file they must share a filesystem that supports WAL (finding #9). No message passing.
* **Legacy stack** — `scheduler_run` → (`daily_runner --stage protect`, must succeed, 300 s, 2 retries) → checkpoint
  status file → (`daily_runner --stage scans`, best-effort, 1800 s) → (`run_lab`, full stage only). Lock file
  `scheduler.lock` prevents overlap (stale after 1800 s).
* **MCP** — independent; no startup dependency on anything else.

## 11. Health checks / liveness equivalents

| Component | Existing check | Reality |
|---|---|---|
| Dashboard | `GET /api/health` | Returns **200 even when everything is empty/failed** (liveness only, not readiness) [V]. Also `/api/paper/account`, `/api/scheduler`, `/api/providers`, `/api/diag`, `/api/terminal/status`. |
| Paper pipeline | `python automation/paper_scheduler.py status` → prints `reconciliation`, `schema_version`, `config_version`; report contains "P&L reconciled: YES/NO" | **Exit code is 0 regardless** of reconcile result. Case 1 failure ⇒ GitHub issue. |
| MCP | Dockerfile `HEALTHCHECK` → `GET http://localhost:8000/health` | **No `/health` route exists** in `server.py` [S] → cannot pass; the streamable-HTTP endpoint is `/mcp` (returns non-200 to a bare GET). Could not confirm at runtime because the server will not start on this host [V]. |
| Legacy scheduler | `scheduler_status.json` age (>18 h ⇒ `stale`) | Reported as `stale` here [V]. |

## 12. Anything hard-coded to a machine, path, localhost or credential

* `dashboard/app.py:1765` `host="127.0.0.1"`; `:704` and ~20 modules `expanduser("~/.tradingview_mcp_data")` (finding #1).
* `automation/paper_scheduler.py:92` writes `PAPER_DAILY_REPORT.md` to the repo root.
* `openclaw/trading.py` hard-codes `/root/.local/share/uv/tools/tradingview-mcp-server/lib/python3.12/site-packages`.
* `lab/robinhood_mcp.py:56` default redirect `http://localhost:8765/callback`.
* `proxy_manager.py` default host `p.webshare.io`.
* `docker-compose.yml` image `atilaahmet/tradingview-mcp:latest` (**appears to be the upstream author's registry namespace**, plus `container_name: tradingview-mcp`) — `docker compose up` would pull/tag under a name that isn't yours.
* `pyproject.toml` author/URL still point at the upstream project.
* America/New_York is hard-coded (correctly) in the dashboard/regime code and the Actions gate; the paper pipeline is **not** ET-aware (finding #3).
* **No** `C:\`, `/home/…`, or `/Users/…` literal paths and **no** committed credentials found in tracked source or `terminal.html` (grep [S]). The frontend uses relative `fetch` URLs only.

## 13. Existing Docker / GHCR — what exists and what is reusable

| Asset | Reality |
|---|---|
| `Dockerfile` | 2-stage, `python:3.11-slim`, `uv pip install --system .`, non-root `mcpuser`, `EXPOSE 8000`, entrypoint `tradingview-mcp streamable-http`. **Runs the MCP server only.** No Flask, no tzdata assurance, no `TZ`, no volume, no `HOME` set for `mcpuser` beyond `useradd -m`. **Reusable:** stage layout, non-root user, `uv` bootstrap. **Not reusable as-is:** entrypoint, HEALTHCHECK, dependency resolution. |
| `.dockerignore` | drops `.git/`, `tests/`, `.github/`, `*.md` (except README), `Dockerfile`, compose. ⇒ **the image cannot run the tests** and cannot serve any `.md`. Missing: `.env`, `.env.*`, `graphify-out/`, `*.pdf`, `reports/`. |
| `docker-compose.yml` | one service, upstream image name, `8080:8000`. No volume, no env file. |
| `publish-image.yml` | Buildx + QEMU, `linux/amd64,arm64`, GHA layer cache, pushes `ghcr.io/<owner>/<repo>` on `main`/tags, **build-only on PRs**, `paths-ignore` for `**.md`. **Reusable nearly verbatim** for a C0 image once the Dockerfile changes. **Gap:** it never runs a container or the tests, so a broken image publishes green (finding #6). |
| GHCR | Package listing needs `read:packages`, which the current `gh` token lacks [U] — could not inspect what is published. |

## 14. What C0 containerization could accidentally change semantically

| # | Risk | Mechanism | Guard |
|---|---|---|---|
| 1 | **Session date shifts** | container `TZ=UTC` vs host ET (finding #3) | explicit `TZ` per case; test at 23:30 ET |
| 2 | **State lands in the wrong place / is lost** | 20 hard-coded `~/.tradingview_mcp_data` paths (finding #1) | volume at `$HOME/.tradingview_mcp_data`; assert at start-up that `paper.db.db_path()` and `predictions.jsonl` resolve under the volume |
| 3 | **Ledger migrated by merely viewing** | `db.connect()` migrates (finding #8) | A/B on **copies**; never mount the live Case 2 dir for the first test |
| 4 | **Config version drift** | `config_version` hashes the env values of ~30 `PAPER_*`/`DECISION_*`/`TRADINGVIEW_ENABLED`/`SENTIMENT_MODEL_ENABLED` keys | container env must leave all of them unset unless deliberately mirroring a case; assert `cfg-a0eede144e` at start-up and in every A/B |
| 5 | **Interpreter drift** | 3.14 (host) vs 3.11 (Docker/Actions) | run host-side baseline on 3.11 as well |
| 6 | **Dependency drift** | unlocked resolve; `mcp 2.x`, `yfinance`, `numpy` float behaviour | freeze a resolved `requirements` set from a known-good Case 1 run; record `pip freeze` in the image |
| 7 | **WAL corruption / stale reads** | Windows bind mount into Linux VM | named volume; single writer |
| 8 | **Report file lost** | `PAPER_DAILY_REPORT.md` written to `/app` | map `_ROOT` output or copy out; keep the file's location semantic |
| 9 | **Case 1 asymmetry erased** | mounting one volume for both `paper/` *and* HOME state makes a "Case 1-style" run persist `predictions.jsonl` | provide two run profiles: `case1` (persist `paper/` only; HOME ephemeral, TZ=UTC) and `case2` (persist all, TZ=America/New_York) |
| 10 | **Import-time side effects** | `_prewarm()` and `PAPER_DATA_DIR` are read at **import** | set env before Python starts; never `os.environ[...]=` after import |
| 11 | **Holiday gate silently off** | `holidays` not installed (§4.2) | do **not** "fix" by adding it in C0 (behaviour change for the legacy scheduler); record it as a known delta |
| 12 | **Secrets baked in** | `.env`, token files, `graphify-out/`, PDFs (finding #10) | extend `.dockerignore`; scan the built image for `.env`/token files as a CI test |
| 13 | **Network egress differences** | Docker Desktop NAT / no corporate proxy; Yahoo/TradingView rate limits on shared IPs (already seen on Actions) | out of scope to "fix"; note in A/B tolerances |
| 14 | **Dev-server exposure** | Flask dev server on `0.0.0.0` | publish only as `127.0.0.1:5057:5057`; no auth exists |

---

## 15. Classification

```
CONTAINERIZE NOW
├── dashboard  (Flask terminal, entry #2)
│     needs: py3.11, `[dashboard]` extra, TZ=America/New_York, HOME=/home/<user>, named volume at $HOME/.tradingview_mcp_data,
│            a 6-line serve script (import dashboard/app.py, app.run(host="0.0.0.0")) — no edit to app.py, no MCP dependency
├── paper job  (entry #1, ONE-SHOT container, not a daemon)
│     isolated safely: it touches only its ledger dir, PAPER_DAILY_REPORT.md and the network; never imports a broker client.
│     C0 scope = image + A/B on COPIES of state. It is NOT scheduled or pointed at real state in C0.
├── test runner  (image stage that keeps tests/, runs `pytest tests/unit`; today's image drops tests/)
└── supporting: entrypoint that asserts data-dir resolution, prints `pip freeze`, refuses to start if BROKER_PROVIDER≠none
                or ROBINHOOD_TRADING_ENABLED≠false; extended .dockerignore (.env, graphify-out, *.pdf, reports/)

KEEP EXTERNAL / REUSE
├── tradingview-mcp (entry #3)  — upstream-derived, currently unrunnable on this host's deps, no /health route,
│     independent of dashboard/paper. Keep its existing Dockerfile path & GHCR workflow; repairing it (pin `mcp<2`,
│     fix HEALTHCHECK) is a separate change, not C0.
├── publish-image.yml  — reuse the buildx/GHCR mechanics for the new image(s)
├── all third-party data APIs (Yahoo, TradingView, Finnhub, FRED, AlphaVantage, SEC, RSS…)
├── Robinhood MCP (agent.robinhood.com) — read-only, OAuth token stays out of images
└── legacy stack (scheduler_run.py / daily_runner.py / run_lab.py) — defer; Windows-Task-Scheduler-shaped, subprocess fan-out,
      lock file, `holidays` gap, still manages existing `strategy-500` positions. Not C0.

PERSIST AS-IS
├── ~/.tradingview_mcp_data/*   (every file in §6.1, same names, same formats)
├── SQLite files (WAL for paper ledger and scan_cohort.db) — no schema change, no journal-mode change
├── predictions.jsonl (append-only), calibration.json
├── paper-state mechanisms: PAPER_DATA_DIR, PAPER_LEDGER naming, Actions cache + artifact fallback, reports/paper/* dedup
└── the Case 1 / Case 2 asymmetry (§0 #2) — two run profiles, not one

DO NOT TOUCH
├── lab/paper/*                       (freeze)
├── lab/decision_engine.py behaviour  (freeze)
├── automation/paper_scheduler.py     (freeze) — note: its report path, sys.path setup and CLI are what the container must adapt *to*
├── Case 1 / Case 2 semantics         (incl. TZ, HOME-state persistence, dedup, ledger source)
├── BROKER_PROVIDER=none · ROBINHOOD_TRADING_ENABLED=false · every broker credential
├── .github/workflows/paper-trading-schedule.yml
└── canonical/* and the option/EV/gate logic
```

---

## 16. First containerization target — the equivalence contract

```
 host input (state COPY S, env E)  ──► host (py3.11) run  ──► output/state A
 docker input (state COPY S, env E) ─► container run      ──► output/state B         success ⇔ A ≡ B
```

Because decisions depend on live quotes, "identical" needs levels:

| Level | What is compared | Deterministic? | Pass rule |
|---|---|---|---|
| L0 | `pip freeze` of the image vs a declared reference set; `python -V`; `TZ`; resolved `db_path()`/`PAPER_DATA_DIR`/`HOME`; `db.config_version()` == `cfg-a0eede144e` | yes | exact |
| L1 | `pytest tests/unit` | yes | 1,272 pass; the same 6 known-red (§4.3) |
| L2 | `test_synthetic_paper_lifecycle.py` + a route-snapshot of the dashboard with network stubbed (`/api/paper/*`, `/api/health`, `/api/scheduler`, `/` HTML hash) | yes | byte-equal JSON after normalising timestamps |
| L3 | `paper_scheduler.py status` + `report <fixed date>` against a **copy** of the Case 2 DB (report is documented deterministic) | yes | equal `schema_version`, `config_version`, reconcile, report body |
| L4 | `premarket --dry-run` on host vs container, **same minute, separate copies of the same state** | no (live quotes) | same universe, same `action` and `failed_gates` per symbol; numeric fields within stated tolerance; **zero** orders/fills/positions in both |
| L5 | file-set diff of the state volume after each run | yes | same set of files created/modified; no unexpected writes (e.g. no token files, nothing outside the volume) |

Rules for every A/B: **never** the live `~/.tradingview_mcp_data`; **never** `premarket` without `--dry-run`; **never**
set any §5.5 variable; container starts with `BROKER_PROVIDER=none`, `ROBINHOOD_TRADING_ENABLED=false` asserted.

## 17. Decisions needed before the Dockerfile design (none block the inventory)

1. **`TZ` policy:** confirm `case2` profile = `America/New_York` (matches this host) and `case1` profile = `UTC` (matches Actions).
2. **Serve script vs. code change:** OK to add a separate entry script (e.g. `docker/serve_dashboard.py`) instead of touching `dashboard/app.py`? _(Recommended: yes.)_
3. **Python for the baseline:** OK to use 3.11 as the reference interpreter (matches Case 1 and the current image) and treat the host's 3.14 as a separate documented data point?
4. **Dependency freeze:** OK to derive the container's reference dependency set from the versions Case 1 actually resolved (readable from a workflow run's `pip install` log) rather than from `uv.lock`? [U — not yet fetched]

## 18. Appendix — how this was produced, and what I touched

* Read-only greps/AST scan of all tracked Python; read of both workflows, `pyproject.toml`, `.dockerignore`, `Dockerfile`,
  `docker-compose.yml`, `.env.example` (key names only).
* Runs **with `HOME`/`USERPROFILE` redirected to scratch dirs:** baseline `pytest`; a 20-second dashboard start
  (`:15057`) with health/paper/scheduler probes; MCP start attempts on `mcp 2.1.1` and on `mcp 1.12.4` (installed to a
  scratch `--target`). No brokers, keys, or real state involved; the dashboard's outbound calls were the normal read-only
  market-data fetches.
* Real state dir: **read-only SQLite inspection** of `paper/`, `portfolio.db`, `scan_cohort.db`,
  `security_master.db`, `strategy_snapshots.db`, and one line of `predictions.jsonl`. Side effect: transient 0-byte
  `-wal`/`-shm` files, since removed (verified no Python process was running; `.db` files unchanged).
* No commits, no pushes, no `git pull`, no Docker build, no changes to any tracked file. This document is the only new
  file (untracked).
