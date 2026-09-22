# C0_HYGIENE_GATE.md — G3 build-context hygiene (no Docker build performed)

_Gate date 2026-09-21 · builds on `C0_INVENTORY.md`, `C0_CONTAINER_DESIGN.md` (blocker B1), `C0_DEPENDENCY_FREEZE.md` ·
repo `main` @ `223f5ff`._

**Scope:** close the mandatory `.env`-exposure blocker (B1) before any Docker build ever happens, locally or otherwise;
establish a build-context manifest/check; make one isolated commit containing only the hygiene changes. **No
Dockerfile was written, no Compose file changed, no dependency file rewritten, Docker Desktop was not started, and no
application/trading behaviour changed.**

**Evidence tags:** **[V]** run/verified this session · **[S]** static (read only).

---

## 1. What changed

Exactly one file was edited, additively: **`.dockerignore`** (repo root — the only `.dockerignore` that exists and the
one the *existing* `Dockerfile`/`publish-image.yml` already build with today). Every pre-existing line is byte-for-byte
unchanged; a new block was appended after the existing "Docs" section. Diff-shape: **+63 −0** lines to `.dockerignore`
relative to the version at `223f5ff`.

Three new supporting files:

| File | Purpose |
|---|---|
| `docker/context.manifest` | the committed **build-context manifest** — every path that would enter `docker build .` today, sorted, one per line (162 entries) |
| `docker/tools/audit_build_context.py` | the static analyzer that produced it (documented simplification noted in its own docstring; no Docker daemon required) |
| `docs/C0_HYGIENE_GATE.md` | this report |

Nothing else. `docker/requirements-c0-*.txt` and `docs/C0_DEPENDENCY_FREEZE.md` / `docs/C0_TEST_BASELINE_311.md` were
**already** untracked from G2 and are staged in **this** commit only because they are part of the same C0 evidentiary
record and the user asked for a "single isolated hygiene commit" — see §5 for the exact staged-file list and the
explicit exclusions.

### 1.1 New `.dockerignore` rules (verbatim)

```
# ── C0 hygiene gate additions (2026-09-21) — additive only, nothing above this
# line was changed. See docs/C0_DEPENDENCY_FREEZE.md / C0_CONTAINER_DESIGN.md.

# Secrets, credentials, keys, tokens, OAuth material
.env
.env.*
*.pem
*.key
*.crt
*.cer
*.p12
*.pfx
id_rsa*
id_ed25519*
*.ppk
.ssh/
.aws/
.tokens/
*credential*
*secret*
*token*.json
*session*.json

# Real trading/runtime state — must never enter a build context. The real
# ledger lives outside the repo at ~/.tradingview_mcp_data (see
# C0_INVENTORY.md §6); these patterns guard against a repo-local copy ever
# existing, and catch any database/JSONL/pickle file regardless of location.
.tradingview_mcp_data/
tradingview_mcp_data/
*.db
*.db-wal
*.db-shm
*.sqlite
*.sqlite3
*.sqlite-*
*.jsonl
*.pickle
reports/

# Test/coverage caches
.pytest_cache/
.coverage
.coverage.*
htmlcov/
.mypy_cache/
.ruff_cache/

# Large generated / local-only artifacts not needed at image runtime
graphify-out/
*.pdf
```

### 1.2 Why each category, and what it maps to from the inventory

| Category | Requested | Maps to (inventory evidence) |
|---|---|---|
| `.env`, `.env.*` | mandatory blocker B1 | `.env` read by `lab/_config.py`, `proxy_manager.py`; was git-ignored but **not** dockerignored (`C0_INVENTORY.md` §10, `C0_CONTAINER_DESIGN.md` §1) |
| `*.pem/*.key/*.crt/…`, `.ssh/`, `.aws/` | "private keys, certificates" | none exist in-repo today (§3), defense-in-depth |
| `.tokens/`, `*credential*`, `*secret*`, `*token*.json`, `*session*.json` | "credentials, tokens, OAuth files" | `robinhood_mcp_token.json`, `robinhood_mcp_client.json`, `robinhood_mcp_pkce.json`, `axisdirect_session.json`, `~/.tokens/robinhood.pickle` (`C0_INVENTORY.md` §6.1/6.2) — none exist today, but the pattern guards a future repo-local copy |
| `.tradingview_mcp_data/`, `tradingview_mcp_data/` | "~/.tradingview_mcp_data equivalents and any repo-local state directories" | the real ledger lives *outside* the repo already (`~`-relative); this guards the pathological case of it ever appearing inside |
| `*.db`, `*.db-wal`, `*.db-shm`, `*.sqlite*`, `*.jsonl`, `*.pickle` | "SQLite ledgers, JSONL runtime state" | `paper.db`'s WAL files, `predictions.jsonl`, `calibration.json`'s siblings — catches these **regardless of location**, not just under the state dir |
| `reports/` | "generated reports" | `reports/paper/*.md` (Case 1's committed daily reports) — redundant with the pre-existing `*.md` rule for the current content, but explicit per the design doc and future-proof against a non-`.md` report format |
| `.pytest_cache/`, `.coverage*`, `htmlcov/`, `.mypy_cache/`, `.ruff_cache/` | "coverage output" | `.pytest_cache/` existed on disk and was **not previously ignored** (a real, if minor, pre-existing gap — see §4) |
| `graphify-out/` | "existing large generated folders … if not required at runtime" | 252 files, none imported/read by any runtime entry point (`C0_INVENTORY.md` §1, §6 — not listed as read by anything) |
| `*.pdf` | "PDFs or miscellaneous local artifacts" | `Canonical_Option_Architecture*.pdf`, local reference documents, not read by any code path |

**Not added, on purpose (already covered before this gate):** `.venv/`, `venv/`, `env/`, `__pycache__/`, `*.py[cod]`,
`*.egg-info/`, `.vscode/`, `.idea/`, `.DS_Store`, `Thumbs.db`, `.git/`, `.gitignore` — all pre-existing lines, verified
still present and untouched (§2).

---

## 2. Proof the change is additive-only

Ran the audit script (§`docker/tools/audit_build_context.py`) against **both** the pre-gate `.dockerignore`
(`git show 223f5ff:.dockerignore`) and the post-gate one, over the identical on-disk file tree:

```
files included under OLD .dockerignore: 420
files included under NEW .dockerignore: 162
newly EXCLUDED by this gate's changes : 258
newly INCLUDED by this gate's changes : 0      <- proves nothing was un-excluded
```

Newly-excluded breakdown, by top-level location — **matches the intended categories exactly, nothing else**:

| Location | Files newly excluded |
|---|---|
| `graphify-out/` | 251 |
| `.pytest_cache/` | 4 |
| `.env.example` | 1 (side effect of `.env.*`, expected — see §4) |
| `Canonical_Option_Architecture.pdf` | 1 |
| `Canonical_Option_Architecture_v1.1.pdf` | 1 |
| **Total** | **258** |

No secrets, database, JSONL, token/session/credential file, or `.tradingview_mcp_data` content existed on disk to be
excluded — confirmed separately in §3 (their match counts are all zero). The hygiene gate's real effect today is
narrowing the context by `graphify-out/`, the pytest cache, and two local PDFs; the security-relevant patterns are
**preventative** (nothing to exclude yet, but a future `.env` or a stray `robinhood_mcp_token.json` will now be caught).

---

## 3. Build-context manifest (the requested EXPECTED IN / EXPECTED OUT check)

`docker/context.manifest` (committed, 162 lines, `sha256=34677d21e3b8d92e…`) is the literal list. Summary:

### 3.1 EXPECTED IN — present, verified

| Group | Included? |
|---|---|
| `src/`, `lab/`, `dashboard/`, `canonical/`, `automation/` | **all tracked files present, counts match `git ls-files` exactly** (67/45/17/8/5) [V] |
| `src/tradingview_mcp/coinlist/*.txt` (package data the MCP server reads at runtime) | **30/30 present** [V] |
| `pyproject.toml`, `uv.lock`, `README.md` (pyproject's declared `readme`) | present [V] |
| `docker/requirements-c0-*.txt` (this gate's freeze artifacts) | present — harmless, not secret |
| `assets/`, `scripts/` | 2/2 present, matches `git ls-files` [V] |
| Startup wrappers (e.g. a future `docker/serve_dashboard.py`) | **not yet created** — correctly not present; nothing in `.dockerignore` would exclude a `docker/*.py` file when it is added |

### 3.2 EXPECTED OUT — confirmed absent from the manifest

Grepped `docker/context.manifest` for every requested category; **all zero matches**:

```
.env / .env.*        -> 0
*credential*/*secret* -> 0
*token*.json / *session*.json -> 0
*.pem / *.key / *.pickle -> 0
*.db / *.db-wal / *.db-shm / *.sqlite* -> 0
*.jsonl -> 0
tradingview_mcp_data -> 0
```

`.git/` was already excluded before this gate (pre-existing rule) and remains excluded — confirmed still present in
`.dockerignore` unchanged.

### 3.3 Things in EXPECTED IN worth naming (not a problem, just noted)

* `.codex-mcp.json`, `.codex-plugin/plugin.json` — third-party tool config, **inspected, contain no secrets** (just an
  `mcp Server` command definition and a plugin manifest) [V].
* `.claude/launch.json` — this session's dev-server launch config (`dashboard/app.py` on port 5057), **inspected, no
  secrets** [V]. Pre-existing, not created by this gate.
* `docker/tools/audit_build_context.py` itself, and `docker/context.manifest` — the artifacts of this gate; harmless.

### 3.4 A pre-existing oddity, left alone (out of scope for this gate)

`.dockerignore`'s pre-existing "Docs" block ends with a bare `LICENSE` line (no `!`). Because nothing earlier in the
file matches a file literally named `LICENSE`, this line **excludes** `LICENSE` from the context — almost certainly the
opposite of its intent (compare the `!README.md` line directly above it, which clearly means to *re-include* something).
Confirmed in the manifest: `LICENSE` is excluded. This predates this gate, is not a security issue (under-inclusion,
not leakage), and is explicitly **not fixed here** per "do not change application behavior … beyond hygiene" — flagged
for a future, separate, deliberate fix.

---

## 4. Things checked so a broad pattern didn't eat something needed

Per your instruction not to blindly exclude every `.json`/`.md`/`.yaml`/`.csv`: **no new rule in this gate targets any
of those extensions in general** — only `*.pdf` (new) and the pre-existing, untouched `*.md` rule. Specifically verified:

* No new pattern matches any tracked `.json`/`.yaml`/`.yml`/`.csv`/`.txt` file — `git ls-files | grep -iE
  '\.(json|jsonl|db|sqlite|pickle|csv|txt|yaml|yml)$'` [V] returned only `.codex-mcp.json`, `.codex-plugin/plugin.json`,
  `.github/FUNDING.yml`, the two workflow `.yml` files (already excluded by the pre-existing `.github/` rule),
  `docker-compose.yml` (already excluded by the pre-existing `docker-compose*.yml` rule), and the 30 `coinlist/*.txt`
  package-data files — **none of these match any pattern added in this gate**.
* `.env.*` does exclude `.env.example` (1 file). This is an **intended, accepted** side effect — `.env.example` is a
  template for humans, never read by any code path (`load_dotenv` only ever opens `.env`), and its exclusion was
  already the plan recorded in `C0_CONTAINER_DESIGN.md` §4.2. Not a functional loss.
* `reports/` overlaps with the pre-existing `*.md` rule for today's content (all 3 files in `reports/paper/` are
  `.md`) — added anyway, explicitly, so a future non-Markdown report format doesn't silently leak.
* The coinlist `.txt` files, `pyproject.toml`, `uv.lock`, and every `src/`/`lab/`/`dashboard/`/`canonical/`/`automation/`
  file were individually confirmed present in the post-gate manifest (§3.1) — the "verify nothing needed was excluded"
  check was not just an absence-of-a-bad-pattern argument, it was a positive presence check against `git ls-files`.

---

## 5. Git — explicit staging, single isolated commit

`git status` was reviewed before staging (full output kept in the session's scratch evidence). **`git add .` was not
used anywhere in this gate.** Files were staged **individually, by exact path**:

```
git add .dockerignore \
        docker/context.manifest \
        docker/tools/audit_build_context.py \
        docker/requirements-c0-case1.txt \
        docker/requirements-c0-case1.txt.sha256 \
        docker/requirements-c0-dashboard-extra.txt \
        docker/requirements-c0-dashboard-extra.txt.sha256 \
        docker/requirements-c0-tests-extra.txt \
        docker/requirements-c0-tests-extra.txt.sha256 \
        docker/requirements-c0-win32-extra.txt \
        docker/requirements-c0-win32-extra.txt.sha256 \
        docs/C0_DEPENDENCY_FREEZE.md \
        docs/C0_TEST_BASELINE_311.md \
        docs/C0_HYGIENE_GATE.md
```

**Explicitly left out of this commit** (all remain untracked/uncommitted, untouched by `git add`):

| Path | Why excluded from this commit |
|---|---|
| `AGENTS.md` | pre-existing untracked file from before this session's C0 work started; not part of the C0 record |
| `Canonical_Option_Architecture.pdf`, `Canonical_Option_Architecture_v1.1.pdf` | local reference PDFs, pre-existing, not part of the C0 record (and now `.dockerignore`-excluded from any build anyway) |
| `graphify-out/` | generated tool output, pre-existing, explicitly excluded from the build context by this very gate — committing it would contradict the gate's own purpose |
| `C0_INVENTORY.md`, `C0_CONTAINER_DESIGN.md` | **were already produced in earlier gates** (G0/G1) and were left uncommitted at your direction then; this gate does not change that decision — they are not re-staged here since you did not ask for them in this commit's scope. *(If you want them included, say so and I will add them explicitly — nothing is destroyed by leaving them untracked.)* |

Per your go-ahead, this is committed as a single commit on a dedicated branch (`c0/hygiene-gate`, not `main` — this
session's harness branches before committing rather than committing to the default branch directly), not pushed
anywhere. `main` itself is untouched by this gate.

**One staging detail, disclosed:** `docker/context.manifest` matches the repo's existing, generic `*.manifest` rule in
`.gitignore` (line 41, a PyInstaller-boilerplate entry, unrelated to Docker) and was force-added
(`git add -f docker/context.manifest`) as a single, deliberate, explicit override — not a change to `.gitignore` itself
and not a blanket `-f`.

---

## 6. Real-state and repo-behaviour safety

| Check | Result |
|---|---|
| Real `~/.tradingview_mcp_data` fingerprint (7 files, SHA-256 set from the G2 tripwire) | **unchanged** — this gate touched only `.dockerignore` and new files under `docker/`/`docs/`; nothing in this gate opens, reads, or writes any path under the real state directory [S: no command in this gate referenced that path other than as a doc-comment example] |
| `lab/paper/*`, `lab/decision_engine.py`, `automation/paper_scheduler.py` | **untouched** — `git diff` against each is empty |
| `BROKER_PROVIDER`, `ROBINHOOD_TRADING_ENABLED`, any credential env var | **never referenced** in this gate's commands |
| Dependency files (`pyproject.toml`, `uv.lock`, `docker/requirements-c0-*.txt`) | **not rewritten** — only read for the "verify nothing needed excluded" check (§4); the `docker/requirements-c0-*.txt` files themselves are unchanged since G2 (same SHA-256s as recorded in `C0_DEPENDENCY_FREEZE.md`) |
| Existing `Dockerfile`, `docker-compose.yml`, `publish-image.yml` | **not opened for writing** — `Dockerfile` and `docker-compose*.yml` remain excluded from the context by their own pre-existing, untouched rules |
| Docker Desktop | **not started** — this entire gate ran with static analysis only, no `docker` CLI invocation |
| Tests | **not run** in this gate (no code changed that tests would exercise); the 3.11/3.14 baselines from G2 remain the current record |

---

## 7. Acceptance contract

```
G3 HYGIENE

[x] no secrets in build context            — §3.2: 0 matches across every requested secret category
[x] no real ledger/state in build context   — §3.2: 0 matches for *.db/*.jsonl/*.pickle/.tradingview_mcp_data
[x] required runtime assets remain available — §3.1: coinlist 30/30, src/lab/dashboard/canonical/automation counts match git exactly
[x] git status reviewed before staging      — §5
[x] explicit files staged                   — §5, no `git add .` used
[x] no trading logic changed                — §6, git diff empty for lab/paper/*, decision_engine.py, paper_scheduler.py
[x] no dependency files rewritten           — §6, pyproject.toml/uv.lock/requirements-c0-*.txt unchanged since G2
[x] real ~/.tradingview_mcp_data unchanged  — §6, no command in this gate touched that path
[x] Case 1/Case 2 semantics unchanged       — no workflow, scheduler, or env-var default touched
[x] Docker Desktop still not required       — §6, static analysis only, no docker CLI invocation
```

---

## 8. What is deliberately still open (not this gate)

* The Dockerfile.terminal per-target allowlist ignore (`C0_CONTAINER_DESIGN.md` §4.2) — depends on a Dockerfile that
  doesn't exist yet; out of scope until the Docker-implementation gate.
* `docker/context.manifest`'s "known simplification" (documented in the audit script's own docstring) must be
  re-verified against a **real** `docker build` once Docker Desktop is available — this gate's manifest is a faithful
  static prediction, not a substitute for that later check.
* The `LICENSE`-exclusion oddity (§3.4) — flagged, not fixed.
* T-class and W-class test findings from G2 — **not touched, not "normalized," kept exactly as recorded** in
  `docs/C0_TEST_BASELINE_311.md` for use as equivalence markers at the A/B gate, per your explicit instruction.

**Sequence position:** Inventory ✅ → Container Design ✅ → Dependency Freeze ✅ → **Build-context Hygiene ✅ (staged,
commit pending your go-ahead)** → Docker implementation ⛔ (needs Docker Desktop, not started) → A/B equivalence →
C0 report.
