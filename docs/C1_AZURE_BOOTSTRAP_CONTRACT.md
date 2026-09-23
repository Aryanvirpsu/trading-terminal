# C1_AZURE_BOOTSTRAP_CONTRACT.md — what C1.1 will create, once the account exists

_C1-PREP item 5 · repo `c1/cloud-runtime` · builds on `docs/C1_HOST_CONTRACT.md`._

**This document performs zero live Azure actions.** No Azure CLI call, no console click, no resource, and no
account were created while writing it. It exists so that the moment an "Azure for Students" account exists
(user-verified, school email, no card — see the C1.0 verification in this session), C1.1 has an exact, pre-agreed
checklist to execute rather than improvising decisions under time pressure with a live subscription open.

Azure is the **implementation** of `docs/C1_HOST_CONTRACT.md`, not a design input to it. Every item below is
justified by a row in that contract; nothing here invents a new requirement Azure happens to be good at.

---

## 0. The one sequencing rule that matters

```
AZURE FOR STUDENTS ACCOUNT CREATED  (user action — school email, no card)
        ↓
Budget + cost alerts                 ← C1.1 starts HERE
Resource group + tags
Region selection
OIDC identity (Azure AD app + federated credential, no stored secret)
        ↓
   ONLY THEN
        ↓
First compute resource (the VM)
```

Guardrails before spend, not spend-then-guardrails — mirrors the original AWS-shaped plan's own rule, carried over
unchanged when the primary host moved to Azure.

---

## 1. Subscription-level guardrails (first, before any resource)

| Item | Setting | Why |
|---|---|---|
| **Budget** | Azure Cost Management → Budgets, monthly, amount **$8** (leaves headroom under the $100/12mo credit — see the math in §5) | catch runaway spend before the credit is gone, not after |
| **Alert thresholds** | 50%, 80%, 100%, 110% of budget, emailed to the account owner | escalating warning, not a single silent cutoff |
| **Cost anomaly / advisor alerts** | enable Azure Advisor cost recommendations; Azure doesn't have a distinct "Cost Anomaly Detection" service the way AWS does — Cost Management's own alerting is the equivalent | closest available guardrail to the AWS-shaped plan's "cost anomaly detection" line |
| **Spending limit** | confirm whether the specific Azure for Students subscription type enforces a hard spending limit (some Azure free/student subscription types do, automatically disabling resources at $0 remaining rather than billing further) — **verify this in the account's own subscription overview once it exists; do not assume either way** | the single most important fact to confirm on day one — changes how much the budget/alerts even matter |

## 2. Identity boundary

| Item | Setting | Why |
|---|---|---|
| **No long-lived Azure credentials in GitHub** | never create/store an Azure "service principal" client secret as a GitHub secret | matches the original plan's "no static keys" rule |
| **OIDC via Azure AD (Entra ID) federated credential** | an App Registration with a **federated credential** trusting `repo:Aryanvirpsu/trading-terminal:ref:refs/heads/c1/cloud-runtime` (and later `:ref:refs/heads/main` once this graduates) | GitHub Actions exchanges its own OIDC token for a short-lived Azure token at run time — nothing persisted, nothing to leak |
| **Role assignment** | scope the App Registration's role to the **one resource group** created in §3, not the subscription | least privilege — a compromised workflow run can't touch anything outside AVDI's own resource group |
| **`deploy-c1.yml`'s `id-token: write` permission** | already present, unused, in `.github/workflows/deploy-c1.yml` (this gate) — exactly the permission OIDC needs, wired ahead of time | C1.3 fills in the `azure/login@v2` step; the permission doesn't need to be added later |

## 3. Resource group and tags

| Item | Value |
|---|---|
| Name | `rg-avdi-c1` |
| Region | **a US region** (candidates: `eastus`, `eastus2`, `centralus` — pick whichever the student subscription actually offers cheapest/nearest to the market-data providers' own US-East presence; **not decided here**, decide at creation time by checking current regional pricing/availability, not from this doc) |
| Tags (applied to every resource in the group) | `project=avdi`, `phase=c1`, `owner=<github-username>`, `cost-center=student-credit`, `env=cloud-synthetic` |

Tags exist so a later `az resource list --tag project=avdi` (or the Cost Management UI filtered by tag) can answer
"what is AVDI costing" without hunting through an unrelated subscription's other resources.

## 4. The compute resource (when §1–§3 are done, not before)

| Item | Value | Why |
|---|---|---|
| **VM size** | `Standard_B1s` (1 vCPU, 1 GiB RAM) to start; `Standard_B2ats_v2` (2 vCPU, more RAM) if `docker/compose.c1.yml`'s `mem_limit: 512m` × 2 services turns out too tight in practice | both are in the **750 free hours/month for 12 months** tier confirmed live on azure.microsoft.com/en-us/free/students in this session — 750 hours ≈ continuous for a month, i.e. free to run 24/7 |
| **OS image** | a current Ubuntu LTS (or Debian) minimal image | matches `docs/C1_HOST_CONTRACT.md` §1 row 1 (Linux); Ubuntu specifically keeps parity with Case 1's own `ubuntu-24.04` runner |
| **Disk** | the default OS disk size for the chosen image (no extra data disk needed — the two named volumes in `docker/compose.c1.yml` are small SQLite/JSON state, not a data-warehouse workload) | `docs/C1_HOST_CONTRACT.md` §2 — two small named volumes, nothing else |
| **Firewall / NSG rules** | inbound: **SSH (22/tcp) restricted to the operator's own IP only**, nothing else inbound at first. Dashboard port 5057 is **not** opened to the internet in this phase — `docker/compose.c1.yml` binds it to `127.0.0.1` on the host; reaching it remotely means an SSH tunnel until C1.6 (HTTPS + reverse proxy) exists | matches the compose file's own loopback-only default and its stated reason (no auth on the Flask dev server) |
| **Docker install** | Docker Engine + the `docker compose` plugin from Docker's official apt repo, not a distro-packaged version (parity with what's tested on this machine: Docker CLI/Compose from Docker's own channel) | `docs/C1_HOST_CONTRACT.md` §1 row 2 |
| **GHCR auth on the VM** | `docker login ghcr.io` with a **read-only** PAT or, better, no stored credential at all if the images end up public — decide at C1.5, not here | `docs/C1_HOST_CONTRACT.md` §3 |

## 5. Budget math (why $8/month, not $100/12mo spent evenly)

$100 over 12 months is **not** the same as "$8.33/month always available" — Azure's free-tier *service* allowances
(the 750 VM hours, the free ACR, the always-free tier services) cover the actual compute this deployment needs at
**$0 against the $100 credit**, as long as usage stays inside those free amounts. The **$100 credit** is the
overflow buffer for anything that exceeds a free allowance (e.g. outbound data transfer past a free quota, or a
larger VM size than the free tier covers) — not the expected monthly cost. An **$8/month budget alert** is
deliberately conservative: if AVDI is ever consuming $8 of *credit* in a month, something is wrong (an oversized VM,
an accidental second VM, data egress from a misconfigured service) and the alert should fire long before the $100
credit or the 12-month window is at real risk. This number is a starting guardrail, not a spending plan — tune it
down once real usage is observed.

## 6. What this doc does not decide

* **Exact region** (§3) — pricing/availability at the moment the account is created.
* **B1s vs. B2ats_v2** (§4) — observed memory pressure once both containers actually run together.
* **How GHCR credentials reach the VM** (§4) — a C1.5 decision, possibly resolved by making the images public
  instead (avdi-dashboard/avdi-scheduler contain no secrets by construction — `docs/C0_HYGIENE_GATE.md`,
  `docs/C0_DOCKER_G4.md` §5.6 — so public images may be entirely acceptable; not decided here).
* **Whether the student subscription enforces a hard spending cap** (§1) — must be read from the actual account,
  not assumed from this document.
