# C1_PREP_ACCEPTANCE.md — the C1-PREP acceptance contract, checked off with evidence

_Record date 2026-09-23 · branch `c1/cloud-runtime` @ `465267a` · builds on `docs/C1_HOST_CONTRACT.md`,
`docs/C1_AZURE_BOOTSTRAP_CONTRACT.md`, `docker/compose.c1.yml`, `.github/workflows/publish-avdi-images.yml`,
`.github/workflows/deploy-c1.yml`._

Every line is the user's own acceptance checklist, in order. "Evidence" is a specific, reproducible fact — a real
CI run, a real digest, a real command output — not a restatement of intent. Nothing below required an Azure
account, an Azure credential, or touched the real Case 2 ledger.

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | remote-host contract documented | ✅ | `docs/C1_HOST_CONTRACT.md` |
| 2 | cloud-synthetic profile defined | ✅ | `docker/compose.c1.yml`, `docker/env/cloud-synthetic.env` |
| 3 | dashboard image builds | ✅ | real CI run 35823606019, job `dashboard`: success |
| 4 | scheduler image builds | ✅ | same run, job `scheduler`: success |
| 5 | same C0 preflight continues passing | ✅ | `docker/c0_preflight.py` extended (not replaced) with a `cloud-synthetic` entry; case1/case2 profile logic unchanged — confirmed no diff to their behavior in `git diff` |
| 6 | images run together through production compose | ✅ | `docker compose -f docker/compose.c1.yml --profile cloud-synthetic up`, both services, local run this session |
| 7 | health endpoint reachable | ✅ | `GET http://127.0.0.1:5057/api/health` → 200, both the locally-built and the GHCR-pulled image |
| 8 | synthetic state survives expected restart | ✅ | `up -d --force-recreate` on the dashboard service: ledger file's mtime/size unchanged after recreation |
| 9 | destructive recreation behaves as specified | ✅ | `docker compose down -v` then `up`: a written marker file confirmed **gone**; a fresh `/api/paper/account` call showed a brand-new $500/0-position ledger, not stale data |
| 10 | no real Case 2 state referenced | ✅ | real `~/.tradingview_mcp_data` SHA-256 tripwire checked repeatedly through this entire gate (7 files, unchanged every time — see §1) |
| 11 | no live broker path enabled | ✅ | `docker/c0_preflight.py::check_dangerous_env()` refuses `BROKER_PROVIDER≠none`/`ROBINHOOD_TRADING_ENABLED≠false`/any credential var, unconditionally, for every profile — now with the cloud-synthetic combination named explicitly in its own failure text |
| 12 | GHCR SHA images publish successfully | ✅ | real CI run 35823606019 pushed `ghcr.io/aryanvirpsu/trading-terminal/avdi-dashboard:sha-0946492` (digest `sha256:19dee148…`) and `.../avdi-scheduler:sha-0946492` (digest `sha256:72640093…`) |
| 13 | published image can be pulled cleanly | ✅ | `docker pull` of both, from this machine, using a `gh auth token`-derived GHCR login — both succeeded |
| 14 | pulled image digest matches published digest | ✅ | pulled digests were byte-identical to the digests the CI push step reported — `sha256:19dee14820b8ed9ec70d5bf1fdafd63c16cc6cf164937e2bd4aab7a0edcc5003` and `sha256:7264009312379897ed17c992ebad3f897cb5566e1fc0a1ac3de449065bd37be9`, exactly |
| 15 | Azure deployment workflow cannot accidentally deploy | ✅ | real `workflow_dispatch` run 35824049550: `validate`✅ → `deployment-ready-checks`✅ → `deploy`❌ with the explicit message `"Cloud deployment not enabled: C1.3 (GitHub OIDC -> Azure AD) has not been completed."` — a loud, unambiguous failure, never a silent skip, and no Azure credential exists anywhere to have been used even if it had tried |
| 16 | Azure bootstrap requirements documented | ✅ | `docs/C1_AZURE_BOOTSTRAP_CONTRACT.md` |
| 17 | C0 behavior remains unchanged | ✅ | `docker/c0_preflight.py`'s case1/case2 branches are untouched in substance (only the dangerous-env check gained a `profile` parameter for clearer messages — same pass/fail outcome for the same inputs); no file under G0–G5's own scope was modified this gate except that one preflight script, additively |

**All 17 items closed.**

---

## 1. Real-state tripwire — the complete record for this gate

SHA-256 fingerprint of all 7 files under the real `~/.tradingview_mcp_data`, checked at every meaningful boundary
across the whole C1-PREP effort (initial artifact build, after the compose.c1.yml smoke test, after the destructive-
recreation test, after every CI-triggering push): **unchanged, every single time.** No command in this gate ever
referenced that path except in documentation.

## 2. What "pull-then-verify" actually proved, precisely

Two levels of proof exist and both were exercised, deliberately kept distinct:

* **`publish-avdi-images.yml`'s own checks** (gate → build with `load:true` → preflight → smoke → *then* push) prove
  the image that gets pushed is the exact image that was just verified to work — never a rebuild between "tested"
  and "published."
* **`deploy-c1.yml`'s `deployment-ready-checks` job** pulls that *same tag back down from the registry, on a fresh
  runner*, and preflights/smoke-tests the **pulled** copy independently. This is a genuinely separate proof: it
  rules out anything registry-specific going wrong (a corrupted layer, a manifest mismatch, a platform mismatch)
  that a same-runner "build once, never push" test could never catch.

## 3. What is still, correctly, not possible

* No Azure resource, credential, or OIDC identity exists. `vars.AZURE_DEPLOY_ENABLED` was never set.
* `deploy-c1.yml`'s `deploy` job has exactly one step, and that step's only two possible outcomes are "explicit,
  loud failure explaining why" (today) or "explicit, loud failure saying the real login step still needs to be
  written" (if someone sets the variable without doing C1.3 first). There is no code path that reaches an actual
  cloud action.
* The dashboard's port is loopback-only in `docker/compose.c1.yml` — nothing this gate did is reachable from outside
  whatever host it runs on, by design, until C1.6.

## 4. One implementation bug found and fixed by actually triggering the workflow

`deploy-c1.yml`'s first real `workflow_dispatch` run failed at the `validate` job with `invalid reference format:
repository name (Aryanvirpsu/trading-terminal/avdi-dashboard) must be lowercase` — `github.repository` is
mixed-case, and unlike `publish-avdi-images.yml` (which only ever uses it through `docker/metadata-action`, which
auto-lowercases), `deploy-c1.yml` interpolated it directly into raw shell `docker` commands. Fixed by computing the
lowercased value once (`tr '[:upper:]' '[:lower:]'`) and threading it through as a job output; re-triggered against
the same real published tag and confirmed `validate`/`deployment-ready-checks` both pass, with `deploy` failing for
the *intended* reason only. This is exactly why every item above is backed by a **real** run rather than a YAML
read-through — the first version of this same workflow looked correct and was not.
