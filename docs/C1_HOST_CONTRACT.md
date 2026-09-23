# C1_HOST_CONTRACT.md — what any remote host must provide (cloud-agnostic)

_C1-PREP item 1 · repo `main`/`c1/cloud-runtime` @ `4c4cfa4` · builds on `C0_CONTAINER_DESIGN.md`,
`docs/C0_DOCKER_G4.md`, `docs/C0_EQUIVALENCE_REPORT.md`._

**Purpose:** define, once, what "a place to run AVDI" means — independent of which cloud (or non-cloud) machine
actually provides it. Azure is the current plan (see `docs/C1_AZURE_BOOTSTRAP_CONTRACT.md`), but nothing in the
images, compose files, or workflows below may assume Azure specifically.

```
AVDI images → GHCR → any Linux Docker host        (the rule)
AVDI → Azure-specific runtime                       (not this)
```

If Azure credits run out, the same images and the same compose file move to AWS, a VPS, a university server, or a
developer's own machine by changing *only* where `docker compose` runs — nothing in the repo changes.

---

## 1. The contract, as a checklist

| # | Requirement | Why | Source of truth |
|---|---|---|---|
| 1 | **Linux**, x86_64 or arm64 | the images are built `linux/amd64` today (matches Case 1's `ubuntu-24.04` runner and this session's Docker Desktop backend); arm64 is not yet built or tested | `docker/Dockerfile.terminal` |
| 2 | **Docker Engine ≥ 24** and **Docker Compose v2** (the `docker compose` plugin, not legacy `docker-compose`) | `docker/compose.c1.yml` uses Compose Spec long-form volumes (`type: tmpfs`, `mode:`) and `healthcheck:` — both need Compose v2 | verified against Compose v5.4.0 in this session; any v2.x should work |
| 3 | **Outbound HTTPS** to `ghcr.io` and the market-data providers (Yahoo, TradingView, Finnhub, FRED, AlphaVantage, SEC, news RSS — the full list is `C0_INVENTORY.md` §9) | image pulls + the running pipeline's own network needs | `C0_INVENTORY.md` §9 |
| 4 | **Read access to GHCR** for `ghcr.io/<owner>/<repo>/avdi-dashboard` and `.../avdi-scheduler` | pulling the published images (item 3 of this gate) | this doc, §3 |
| 5 | **Persistent block/volume storage** for exactly the paths named in §2 below — nothing else needs to survive a restart | state isolation, same discipline as every C0 gate | `C0_CONTAINER_DESIGN.md` §5 |
| 6 | **One inbound port**, `5057/tcp`, for the dashboard — reachable only from wherever the operator decides (a public IP, a VPN, or behind a reverse proxy later per C1.6) | the dashboard is the only service that listens; the scheduler never binds a port | `docker/compose.c1.yml` |
| 7 | A **health check** the host's own tooling (Azure Monitor, a cron script, whatever) can poll: `GET :5057/api/health` → `200` | matches every earlier gate's health-check story | `C0_CONTAINER_DESIGN.md` §9.2, `docs/C0_DOCKER_G4.md` §5.4 |
| 8 | **`TZ` set explicitly** by the host, never left to the OS default | naive `date.today()` in `lab/paper/*` makes `TZ` a semantic input, not cosmetic — see §4 | `C0_CONTAINER_DESIGN.md` D1 |
| 9 | **No broker credentials, no live-trading env vars, ever** | structurally enforced (see §5) | `docker/c0_preflight.py` |
| 10 | Enough RAM/CPU for one Flask dev-server process + one short-lived Python batch job — **not** a data warehouse | sizing target is Azure's free B1s/B2ats burstable tier (1–2 vCPU, ~1–4 GiB) | `docs/C1_AZURE_BOOTSTRAP_CONTRACT.md` |

Nothing above says "Azure." A host that satisfies all ten rows is a valid AVDI host.

---

## 2. Persistent volumes — exactly two, named the same way regardless of host

| Volume | Mounted at | Contents | Never contains |
|---|---|---|---|
| `avdi_state` | `/home/tv/.tradingview_mcp_data` | the paper ledger, `portfolio.db`, `security_master.db`, caches — everything the terminal normally keeps under `~/.tradingview_mcp_data` (`C0_INVENTORY.md` §6.1) | the **real** Case 2 data — this volume starts empty on every fresh deployment (§4 of `docs/C0_EQUIVALENCE_REPORT.md`'s discipline continues here) |
| `avdi_out` | `/out` | `PAPER_DAILY_REPORT.md` (symlinked from `/app`), any future exported artifacts | nothing state-bearing — purely generated output |

Two volumes, not the four `compose.c0.yml` used (that file's `c0_case1_*`/`c0_case2_*` split exists to run two
*profiles side by side* for equivalence testing — a production deployment runs one profile, so it needs one set of
volumes). No `tmpfs` in production: C0's case1 profile used `tmpfs` deliberately to *prove* ephemerality
(`docs/C0_DOCKER_G4.md` §5.6); a real remote instance should behave like Case 2 — durable across restarts — because
a cloud host that loses its evidence trail on every redeploy would be useless for accumulating the 50-trade
milestone.

---

## 3. GHCR access

Images: `ghcr.io/<owner>/<repo>/avdi-dashboard:<tag>` and `ghcr.io/<owner>/<repo>/avdi-scheduler:<tag>` (item 3 of
this gate — see `.github/workflows/publish-avdi-images.yml`). Tags are the **immutable commit SHA**
(`sha-<7 chars>`) by default; a host pins to a SHA, never to a mutable tag like `latest`, so "what's running" is
always answerable exactly. Pulling requires either the images be public, or a registry login
(`docker login ghcr.io`) with a token scoped to `read:packages` — **which token, and how it reaches the host, is a
C1.3/C1.5 decision, not fixed here.**

---

## 4. TZ / profile rules

Reuses the exact mechanism `C0_CONTAINER_DESIGN.md` D1 established and `docker/c0_preflight.py` already checks:

* `TZ` is passed as a container environment variable, never inferred from the host OS.
* The naive `dt.date.today()` calls throughout `lab/paper/*` are **not** made timezone-aware here — that stays
  exactly as C0 left it, preserved on purpose, not "fixed."
* A remote AVDI instance is **not** required to be `TZ=UTC` or `TZ=America/New_York` specifically — those two exist
  because they reproduce Case 1/Case 2. A new cloud deployment gets its own profile name (`cloud-synthetic`, defined
  in `docker/compose.c1.yml`) with its **own** deliberate `TZ` choice, disclosed rather than defaulted:
  **`TZ=UTC`**, matching general server best practice and Case 1's own precedent, so a session-date boundary
  question ("what day did this signal fire on?") has one unambiguous, globally-consistent answer regardless of
  where the host physically sits.

---

## 5. No broker/live-trading credentials — structural, not a policy note

`docker/c0_preflight.py` already refuses to start if `BROKER_PROVIDER` is anything but `none`/unset or
`ROBINHOOD_TRADING_ENABLED` is anything but `false`/unset, and refuses if any of the credential-shaped env vars
(`ROBINHOOD_USERNAME/PASSWORD`, `RH_*`, `AXISDIRECT_*`, `SNAPTRADE_*`, `PROXY_PASSWORD`, anything ending
`_API_KEY`) are set (`C0_CONTAINER_DESIGN.md` §7.2/§7.3). The production compose override (item 2) simply **never
supplies** any of these — the preflight is the enforcement, the compose file is the demonstration that nothing
needs to.

---

## 6. What this contract deliberately does not decide yet

* **Which host** provides all of the above (Azure VM is the plan — `docs/C1_AZURE_BOOTSTRAP_CONTRACT.md` — but this
  doc doesn't require it).
* **How the scheduler is triggered** on a remote host (a VM-level cron/systemd-timer calling `docker compose run`,
  vs. a container that sleeps-and-loops, vs. reusing the existing GitHub Actions cron pattern pointed at the remote
  instance instead of a GitHub-hosted runner) — genuinely undecided, deferred to C1.5.
* **How GHCR credentials reach the host** — deferred to C1.3 (GitHub OIDC → Azure AD) / C1.5.
* **HTTPS/domain/reverse proxy** — deferred to C1.6.
