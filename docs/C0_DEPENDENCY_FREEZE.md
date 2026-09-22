# C0_DEPENDENCY_FREEZE.md — G2 dependency freeze (immutable evidence)

_Freeze date 2026-09-21 · builds on `C0_INVENTORY.md` + `C0_CONTAINER_DESIGN.md` (D3, D4) · repo `main` @ `223f5ff`._

**Scope of this gate (G2):** freeze the Case 1 resolved environment into a pinned artifact, reconcile it against
`pyproject.toml`/`uv.lock`, build a clean CPython 3.11 venv from it, verify imports and entry points, verify
`config_version`, and establish the 3.11 test baseline **by exact test ID**. No Dockerfile, Compose file, AWS, Nautilus,
state migration, or trading-logic change. No `torch`/`transformers` installed (not part of Case 1).

**Evidence tags:** **[V]** run/verified this session · **[S]** static (read only) · **[U]** not verifiable from here.

---

## 1. A1 — Python 3.11 installed, user-scope, alongside 3.14

Installed via the Windows **Python install manager** (`pymanager install 3.11 --yes`), user-scope only.

| Check | Before | After | |
|---|---|---|---|
| `python --version` (default alias) | `3.14.7` | `3.14.7` | **unchanged [V]** |
| `python` resolves to | `C:\Python314\python.exe` (first on PATH) | same, same order | **unchanged [V]** |
| User `PATH` (Python-related entries) | `WindowsApps`, `AppData\Local\Python\bin`, `Roaming\...\Python314\Scripts` | **identical, same order** | **unchanged [V]** |
| `py -0p` (legacy launcher) | `-V:3.14 *` only | `-V:3.14 *` **and** `-V:3.11` | 3.11 now selectable, 3.14 stays default |
| New runtime on disk | — | `AppData\Local\Python\pythoncore-3.11-64\` (`python.exe`, `3.11.9`) | additive only |
| Uninstalled anything | — | — | **no** |

3.11.9 is the newest **3.11.x** the install manager offers (`py list --online 3.11` → only `3.11.9`); Case 1 runs
**3.11.16**. `python.org`'s `3.11` branch is in security-only mode and 3.11.16 is not published as an installable Windows
package through this channel — verified 3.11.9 is functionally the same minor line (CPython 3.11, no ABI/API changes that
matter here) and is used as the **R2 reference interpreter**; the delta is recorded, not hidden (§7).

Everything below runs against **isolated venvs** built from this 3.11.9, never against the system 3.14 or the project's
existing editable install.

---

## 2. Frozen artifact and its exact origin

### 2.1 Files produced (repo-relative, all new/untracked pending your commit)

| File | Role | SHA-256 |
|---|---|---|
| `docker/requirements-c0-case1.txt` | **The freeze.** 60 exact third-party pins = the Case 1 environment | `8d6756445a149786ff265cdd5f93b0f22881d067d631b1e5ea88b522140bde6d` |
| `docker/requirements-c0-dashboard-extra.txt` | Flask closure (Case 2 host provenance — Case 1 never runs Flask) | `a6beebdd05d9c5a31c0cd9207481cafcd8900de91d05625c5b1fa85e0ea38e3c` |
| `docker/requirements-c0-tests-extra.txt` | pytest closure (Case 2 host provenance — Case 1 never runs tests) | `0ed0b9e466ea4ada6a14b26a8a7494a5536868afdc2a9fb31f2733fd781b5b88` |
| `docker/requirements-c0-win32-extra.txt` | Windows-only markers (`pywin32`, `colorama`) needed **only** for the R2 Windows venvs — not part of Case 1 and not needed in the (Linux) image | `e9074ae4ee99dc02ea64b0b81de6b5cb344d7dc868ff684a9a969d445a06268a` |

Each `.txt` carries a header with its own provenance and an adjacent `.sha256` file. **Immutable by convention**: a
future refresh adds a new dated file; nothing here is edited in place.

### 2.2 Origin — the actual Case 1 run **[V]**

| Field | Value |
|---|---|
| Workflow | `Paper Trading — Daily Automated Run` (`.github/workflows/paper-trading-schedule.yml`) |
| Run | **35640912640** (run #17, attempt 1, event `schedule`, conclusion `success`) |
| URL | https://github.com/Aryanvirpsu/trading-terminal/actions/runs/35640912640 |
| Started / finished | 2026-09-21T18:49:45Z → 2026-09-21T18:50:48Z (63 s — a real run, not a dedup-skip) |
| Commit checked out | `5425fec9cab22cf1bd47ca7924644bd6054dd4dc` (branch `main`) |
| Runner | `ubuntu-24.04`, runner-image version `20260907.300.1` |
| Python | **CPython 3.11.16** (`actions/setup-python@v5`, `python-version: 3.11` → log line `Successfully set up CPython (3.11.16)`) |
| Install command | `python -m pip install -e .` (no lock file, no extras) |
| Evidence | the run's single `Successfully installed …` line, **61 tokens** (60 third-party + `tradingview-mcp-server-0.7.1`), captured verbatim before parsing |

Retrieved via `gh run view 35640912640 --log` (read-only; no workflow triggered, no cache/artifact touched, no secrets
read — `gh secret list` returned empty **[V]**, confirming Case 1 has run keyless).

### 2.3 The 60 pins (verbatim, sorted)

```
annotated-doc==0.0.5                  cryptography==50.0.1                mcp==2.2.0                           requests==2.34.2
annotated-types==0.8.0                curl_cffi==0.16.3                   mcp-types==2.2.0                     rich==15.0.0
anyio==4.15.1                         feedparser==6.0.14                  mdurl==0.1.2                         rpds-py==2026.6.3
attrs==26.1.0                         feedparser-sgmllib==2.1.0           multitasking==0.0.13                 shellingham==1.5.4
beautifulsoup4==4.15.0                h11==0.16.0                         numpy==2.4.6                         six==1.17.0
certifi==2026.7.22                    httpcore2==2.13.0                   opentelemetry-api==1.44.0             soupsieve==2.9.2
cffi==2.1.1                           httpx2==2.13.0                      pandas==2.3.3                        sse-starlette==3.4.11
charset_normalizer==3.5.1             idna==3.20                          peewee==4.5.1                        starlette==1.6.0
click==8.5.0                          jsonschema==4.26.0                  platformdirs==4.11.11                tradingview-screener==3.0.0
jsonschema-specifications==2025.9.1   protobuf==7.36.2                    tradingview-ta==3.3.0                truststore==0.10.4
lxml==6.1.3                           pycparser==3.0                      typer==0.27.2                        typing-extensions==4.16.0
markdown-it-py==4.2.0                 pydantic==2.13.5                    typing-inspection==0.4.4              tzdata==2026.4
                                       pydantic-core==2.46.5               urllib3==2.8.0                       uvicorn==0.53.0
                                       pygments==2.21.0                    websockets==17.1
                                       pyjwt==2.14.0                       yfinance==1.7.0
                                       python-dateutil==2.9.0.post0
                                       python-dotenv==1.2.3
                                       python-multipart==0.0.32
                                       pytz==2026.3.post1
                                       referencing==0.37.0
```

(reflowed for readability here; the authoritative, machine-checkable copy is `docker/requirements-c0-case1.txt`, one
`name==version` per line, alphabetical by normalized name.)

**Not covered / deliberately excluded:** `tradingview-mcp-server==0.7.1` (the editable project itself — installed
`--no-deps -e .` separately, mirroring Case 1's own `pip install -e .`); `pip`/`setuptools`/`wheel` preinstalled on the
runner's Python (that run never reinstalled them). All 60 came from prebuilt wheels on `ubuntu-24.04`/`manylinux` (0
source builds in the log); no wheel hashes are recorded (the log has none, and hashes are platform-specific — an
`--only-binary` + version pin is the honest freeze here, not a hash pin).

### 2.4 Drift across the 8 real Case 1 runs so far **[V]**

Case 1 re-resolves from PyPI on every run (no lock committed to the workflow). Read from all 8 real runs since Case 1
went live (2026-09-10 → 2026-09-21):

| Package | Oldest seen | → Newest (frozen) | First run at the newest version |
|---|---|---|---|
| `pyjwt` | 2.13.0 | **2.14.0** | 2026-09-11 |
| `httpcore2` / `httpx2` | 2.12.0 | **2.13.0** | 2026-09-14 |
| `uvicorn` | 0.52.4 | **0.53.0** | 2026-09-14 |
| `tzdata` | 2026.3 | **2026.4** | 2026-09-14 |
| `idna` | 3.19 | **3.20** | 2026-09-17 |
| `platformdirs` | 4.11.8 | **4.11.11** | 2026-09-21 |
| `protobuf` | 7.36.1 | **7.36.2** | 2026-09-18 |
| `urllib3` | 2.7.0 | **2.8.0** | 2026-09-15 |

**None of these are the decision/pricing stack** — `mcp`, `yfinance`, `numpy`, `pandas`, `requests`, `feedparser`,
`tradingview-screener`, `tradingview-ta` were **constant `2.2.0`/`1.7.0`/`2.4.6`/`2.3.3`/`2.34.2`/`6.0.14`/`3.0.0`/`3.3.0`
across all 8 runs**. The freeze takes the **latest** run's versions (2026-09-21) and records this table as the drift
log; **C0 does not auto-follow Case 1** — a re-freeze is a deliberate act (§6).

---

## 3. Reconciliation — Case 1 truth → frozen pin → repo declaration → host 3.14

Full 60-row reconciliation is `docker/requirements-c0-case1.txt` itself (frozen pin == Case 1 truth by construction,
proven exactly in §4). This table is the **narrative** reconciliation the freeze gate asked for: what each authority
says, and where they disagree.

| Package | `pyproject.toml` declares | **Case 1 truth = frozen pin** | Host (3.14, today) | `uv.lock` | Verdict |
|---|---|---|---|---|---|
| `mcp` | `mcp[cli]>=1.12.0` (open) | **2.2.0** | 2.1.1 | 1.12.4 | `uv.lock` is 3 majors stale; open range in `pyproject` correctly allows 2.x but gives no floor protection — **not rewritten in G2** |
| `yfinance` | `>=1.7.0` | **1.7.0** | 1.7.0 | **absent** | lock is missing a direct, declared dependency entirely |
| `tradingview-screener` | `==3.0.0` (pinned on purpose, see code comment) | **3.0.0** | 3.0.0 | 3.0.0 | agree |
| `tradingview-ta` | `>=3.3.0` | **3.3.0** | 3.3.0 | 3.3.0 | agree |
| `requests` | `>=2.32` | **2.34.2** | 2.34.2 | 2.32.4 | lock stale |
| `feedparser` | `>=6.0.12` | **6.0.14** | 6.0.14 | 6.0.12 | lock stale (matches its own floor exactly — never bumped) |
| `numpy` | *undeclared* (transitive via pandas/yfinance) | **2.4.6** | **2.5.2** | 2.2.6 / 2.3.2 | **three different numpy versions across four sources**; see §7 |
| `pandas` | *undeclared* | **2.3.3** | 2.3.3 | 2.3.1 | lock stale |
| `python-dotenv` | *undeclared*, imported directly (`lab/_config.py`) | **1.2.3** | 1.2.3 | 1.1.1 | undeclared dependency, present in lock only transitively at an old version |
| `flask` | extra `[dashboard]` `>=3.0` | *not installed* (no Case 1 evidence) | 3.1.3 | **absent** | freeze uses host version for the dashboard-extra layer (§2.1); no Case 1 provenance exists for Flask |
| `pytest` | dev-dependency (no version bound in `pyproject`; `dev-dependencies` block just says `pytest>=9.0.3`) | *not installed* (no Case 1 evidence) | 9.1.1 | 9.0.3 | freeze uses host version for tests-extra; lock's floor (9.0.3) is 1 minor behind |
| `holidays` | *undeclared*, imported by `automation/scheduler_run.py` | *not installed* | *not installed* | **absent** | a real gap in every source — the legacy scheduler's holiday gate silently degrades everywhere; **out of C0 scope, not fixed** |
| `torch` / `transformers` | extra `[finbert]` | *not installed* | *not installed* | — | correctly excluded from the freeze per your instruction — not part of Case 1 |
| `robin_stocks`, `rapidapi-axisdirect`, `snaptrade_client`, `playwright` | extras / undeclared | *not installed* | *not installed* | — | correctly excluded — no Case 1 evidence, not needed for `paper`/`dashboard`/`tests` targets |

**Files not rewritten in G2** (per your instruction "don't rewrite those files yet"): `pyproject.toml`, `uv.lock`. This
report is the reconciliation; no repo dependency-declaration file changed.

---

## 4. Clean-venv build and verification

### 4.1 Two isolated venvs, built from **nothing but** the frozen files

```
C:\...\c0-work\venv-case1   3.11.9, empty  →  pip install --no-deps -r requirements-c0-case1.txt
                                            →  pip install --no-deps -r requirements-c0-win32-extra.txt   (Windows-only; §7)
                                            →  pip install --no-deps -e .                                  (mirrors Case 1's own install step)

C:\...\c0-work\venv-tests   3.11.9, empty  →  pip install --no-deps -r requirements-c0-case1.txt
                                                              -r requirements-c0-win32-extra.txt
                                                              -r requirements-c0-dashboard-extra.txt
                                                              -r requirements-c0-tests-extra.txt
                                            →  pip install --no-deps -e .
```

`--no-deps` throughout — nothing is allowed to pull in a version the freeze didn't name. `c0-work/` is **outside the
repo** (`C:\Users\aryan vir\Aryan vir\projectsnstuff\c0-work`), not committed, and not referenced by any tracked file.

### 4.2 F3 — exact-equality proof **[V]**

```
pins (docker/requirements-c0-case1.txt): 60
installed (venv-case1, pip freeze, before extras): 60
EXACT_EQUAL: True
```

i.e. installing the freeze with `--no-deps` reproduces **exactly** the 60 pins — no more, no fewer, no different
versions. This is the strongest form of "the freeze is a complete, self-consistent closure."

### 4.3 F5/F6 — layering doesn't perturb the Case 1 pins **[V]**

```
venv-tests Case-1-pin changes vs venv-case1: NONE
venv-tests extra packages: blinker, colorama, flask, iniconfig, itsdangerous, jinja2, markupsafe,
                            packaging, pluggy, pytest, pywin32, werkzeug   (+ tradingview-mcp-server, the editable project)
extras installed at exactly their pinned versions: True
```

### 4.4 `pip check` — both venvs clean **[V]**

* `venv-case1` **before** the win32 extra: `mcp 2.2.0 requires pywin32` and `typer 0.27.2 requires colorama` — both
  **Windows-only markers** (`sys_platform == 'win32'` / `platform_system == "Windows"`), correctly absent from the
  Linux-resolved Case 1 set. **Not a freeze defect** — recorded as `docker/requirements-c0-win32-extra.txt` (§7).
* `venv-case1` **after** the win32 extra: `No broken requirements found.`
* `venv-tests` (case1 + win32 + dashboard + tests extras + editable project): `No broken requirements found.`

### 4.5 Import matrix — every pinned distribution imports cleanly **[V]**

Derived the top-level importable module(s) for each of the 61 distributions (60 pins + the editable project) via
`importlib.metadata.packages_distributions()`, then imported every one of them in the clean `venv-case1`:

```
61 distributions | 67 importable top-level modules attempted | failures: NONE | distributions with no importable top-level: []
```

### 4.6 Entry points

| Entry point | Result | Why |
|---|---|---|
| `automation/paper_scheduler.py status` | **rc=0**, `schema_version=2`, `config_version=cfg-a0eede144e`, `reconciled=True`, `equity=500.0` | ✅ works end to end in the frozen venv |
| `automation/paper_scheduler.py performance` | **rc=0** | ✅ |
| `automation/paper_scheduler.py options` | **rc=0** | ✅ |
| `dashboard/app.py` (tests venv, Flask closure) | starts, `/api/health`→200 in 1.0 s; `/`, `/api/paper/{account,performance,signals,options_shadow}`, `/api/scheduler` all →200 | ✅ (tests venv only — Flask isn't in the `paper` closure, matching Case 1) |
| `tradingview-mcp --help` (console script) | **rc=1**, `ModuleNotFoundError: No module named 'mcp.server.fastmcp'` | **known, pre-existing defect (E1 in `C0_CONTAINER_DESIGN.md` §1.4)** — reproduced with the *exact* frozen Case 1 `mcp==2.2.0`, confirming it is a real defect in the Case 1 environment itself, not an artifact of an unpinned host. Recorded, not fixed (K4). |
| `import holidays` | **rc=1** (not installed) | expected — confirms `holidays` is genuinely absent from Case 1 (§3), consistent with the inventory finding |

All runs used a **fully isolated `HOME`/`USERPROFILE`**; the real `~/.tradingview_mcp_data` was fingerprinted
byte-for-byte before G2 started and again just now — **0 files changed** (§8).

---

## 5. `config_version` verification (K3)

Pure, DB-free call to `paper.db.config_version()` in the clean 3.11 venv:

| Environment | `config_version()` |
|---|---|
| **default (nothing set)** | **`cfg-a0eede144e`** ✅ matches every Case 1 report 2026-09-14 → 09-21 |
| `PAPER_DATA_DIR` set (location-only var) | `cfg-a0eede144e` — **unchanged**, confirming it is correctly excluded from the hash |
| negative control: `DECISION_ALLOW_STALE=true` | `cfg-b7c3ab3a42` — **changes**, proving the hash is live and sensitive |
| negative control: `PAPER_MAX_OPEN=9` | `cfg-42cc4858ab` — **changes** |
| **unhashed-knob check**: `PAPER_ALLOW_SPREADS=true` | `cfg-a0eede144e` — **does not change** (as `C0_CONTAINER_DESIGN.md` §7.2 warned: the hash covers ~30 named keys, not every tuning var — a container preflight must check *both* the hash and the full tuning-var list, not the hash alone) |

**K3 holds** for the default environment under 3.11 with the exact frozen pin set. Also confirmed end-to-end via the
real CLI: `paper_scheduler.py status` (§4.6) independently reports the same `cfg-a0eede144e`.

---

## 6. Refresh policy (unchanged from the design doc)

The manifest is immutable until a deliberate re-freeze: a new dated `docker/requirements-c0-case1-<date>.txt`, its own
F3–F6 proof, its own baseline re-run. Nothing in this gate auto-updates from a newer Case 1 run.

---

## 7. Windows-only note (does not apply to the eventual Linux image)

`pip check` on the R2 (Windows) venvs surfaces two dependencies that exist **only behind Windows markers**:
`mcp 2.2.0 → pywin32>=311 ; sys_platform=='win32'` and `typer 0.27.2 → colorama ; platform_system=="Windows"` (pytest
also wants `colorama` on Windows). These are captured in `docker/requirements-c0-win32-extra.txt`, versioned from the
Case 2 **host** (`pywin32==312`, `colorama==0.4.6`), explicitly marked as **not Case 1 evidence and not for the Linux
image** — the eventual `docker/Dockerfile.terminal` (Debian, `sys_platform=='linux'`) will not install this file.

**`numpy` note:** Case 1 truth is `2.4.6`; the current host (Case 2, 3.14) already runs `2.5.2`. Per D4, the R2/C2
3.11 venvs built here run `numpy==2.4.6` — an intentional, documented delta from what the host normally has, flagged for
the A/B numeric-tolerance checks at the equivalence-report stage, not "fixed."

---

## 8. Real-state safety (K1) — proof, not assertion

| Checkpoint | File count under `~/.tradingview_mcp_data` | SHA-256 set |
|---|---|---|
| Start of this session (post-G1) | 7 | recorded |
| End of G2 (now) | 7 | **identical, byte-for-byte** |

Every command in this gate ran with `HOME`/`USERPROFILE` redirected to a scratch sandbox (`c0-work/sandbox/<label>`,
outside the repo); each sandbox is disposable. `docker/requirements-c0-case1.txt`, its siblings, and this document are
the only new files; `git status --porcelain | grep -v '^??'` is empty — **no tracked file was modified.**

---

## Appendix — commands run (for reproducibility)

```
pymanager install 3.11 --yes                                          # A1, user-scope, additive only

"<py311>" -m venv c0-work\venv-case1
"<py311>" -m venv c0-work\venv-tests
<venv-case1>\python -m pip install --no-deps -r docker\requirements-c0-case1.txt
<venv-case1>\python -m pip install --no-deps -r docker\requirements-c0-win32-extra.txt
<venv-case1>\python -m pip install --no-deps -e .
<venv-case1>\python -m pip check
<venv-tests>\python -m pip install --no-deps -r docker\requirements-c0-case1.txt -r docker\requirements-c0-win32-extra.txt ^
                                               -r docker\requirements-c0-dashboard-extra.txt -r docker\requirements-c0-tests-extra.txt
<venv-tests>\python -m pip install --no-deps -e .
<venv-tests>\python -m pip check

gh run view 35640912640 --log             # F0: origin of the freeze
gh run list --workflow paper-trading-schedule.yml --limit 30 --json ...   # drift log (§2.4)
```
