# Cloud data on the local dashboard — and the loop back to the cloud

The paper runtime runs in the cloud (OCI Ubuntu host, `docs/UBUNTU_RUNTIME.md`). This lets you **see what it is doing from
your local dashboard**, work out strategy changes here, and ship them back through the existing pipeline.

## 1. See the cloud data locally (read-only)

```bash
python scripts/sync_cloud_data.py --dry-run   # what would be pulled (name, size, time) — downloads nothing
python scripts/sync_cloud_data.py             # pull + verify + install the newest backup
python scripts/sync_cloud_data.py --status    # what is mirrored, how old
python scripts/sync_cloud_data.py --refresh   # first ask the cloud for a NEW backup (dispatches the read-only ops workflow)
python scripts/run_terminal.py                # dashboard; the cloud viewer starts with it and follows new syncs by itself
```

If `gh` says it is not signed in (its sign-in is stored per Windows user / config folder, so one terminal can have it and another not):
`gh auth login`, or in PowerShell `$env:GH_TOKEN = '<token>'` (read access to the repo's Actions artifacts), or download the artifact from the
GitHub Actions page and run `python scripts/sync_cloud_data.py --from-file backup.tgz`. The mirror folder is shared by every terminal on the same Windows user,
so a sync done anywhere is picked up by the running dashboard.

In the dashboard: **Ledger: Local | Cloud** in the top bar switches Overview, Portfolio and Strategies between this machine's
ledger and the cloud mirror (an amber banner is shown whenever the cloud is selected). **Cloud runtime** (sidebar) shows the
mirror's sync status, the evidence boundary and P&L since it, the latest scan funnel, every candidate with what the Champion did
and why, sessions, and the CH-001 forward evaluation.

**What it is, exactly.** At every session close the cloud host writes a consistent, hash-verified SQLite backup (ledger + shadow
evidence DB). The restricted `ops` role pulls it into a GitHub Actions artifact `avdi-ubuntu-backup-<n>` (`ubuntu-ops.yml`).
The script downloads that artifact with the GitHub CLI, re-checks every sha256 and SQLite `integrity_check`, and installs it
under `~/.avdi_cloud_mirror/<backup>/` (pristine, read-only) with a throw-away `work/` copy that the viewer opens. It lives **outside** `~/.tradingview_mcp_data` (the local ledger's evidence store) on purpose.
So it is a **snapshot as of the last close, not live**; the banner and the Cloud runtime page say how old it is. Open positions are priced at the
backup's own last 5-minute bar — never at today's live quotes — so equity reproduces the cloud host's own figure (checked: $502.35 vs the host's $502.36).

**Guarantees** (all covered by `tests/unit/test_sync_cloud_data.py` and `test_cloud_viewer.py`):

* The local ledger (Case 2, `~/.tradingview_mcp_data/paper`) is never written, and the two ledgers are never merged or reconciled.
  The sync refuses any target inside the local ledger directory; the viewer refuses to start on it.
* The viewer is a **separate process** (`dashboard/cloud_viewer.py`, port 5058) pointed at the mirror, running the *same*
  `lab/paper` accounting code, so cloud numbers equal what the cloud computes. The main dashboard only proxies `/api/cloud/*` to it.
* A tampered, unsafe or not-ok archive installs nothing. No broker client can be built in the viewer
  (`ROBINHOOD_TRADING_ENABLED=false`, `BROKER_PROVIDER=none`).
* No credentials are read or stored: `gh` supplies its own sign-in; the cloud side uses the restricted, command-gated `ops` key —
  your full SSH key is not involved.

## 2. Work out a strategy change

Profit Mode rules apply (`CLAUDE.md`): the Champion is immutable; every idea is a Challenger, pre-registered before its result is
computed, judged net of costs in R terms, never pooled with another version. Use the mirror to look at real cloud behaviour
(funnel, blocked candidates, capacity losses), run the experiment under `experiments/<chNNN_…>/` (PREREG.md before RESULTS.md),
and read the verdict on the **Testing** page.

### Experiments run on the mirror (protocol)
`experiments/` holds the evidence trail; the Testing page shows it (`Comparison` tab for Champion-vs-Challenger replays).
* `diagnosis_no_entries_20261013/` — why so few setups become entries (descriptive; reproducible with `diagnose.py`).
* `ch003_time_stop/` — dead-capital time stop. Protocol enforced by code: `PREREG.md` is hash-locked before any output exists
  (`PREREG.lock.json`); the Champion replay must first reproduce the cloud's real entries (Stage A); the code is then frozen
  (`CODE.lock.json`) and every arm is run once. `python ch003.py` refuses to run if either lock is broken.
* **Forward test:** after each close, `python scripts/sync_cloud_data.py` then `python ch003.py` — positions entered after 2026-10-12 are
  reported as out-of-sample. Promotion is never automatic: it needs the pre-registered gate **and** a person writing `PROMOTION_LOG.md`.

## 3. Ship it to the cloud

Cloud runs **`main`** only. The path that already exists and is already proven:

1. Branch from `main` (not from a stale branch), commit the change with its tests, open a PR → `ci.yml` must pass.
2. Merge to `main` → `publish-avdi-images.yml` builds and validates the images (tests, preflight, smoke).
3. Run **Deploy Ubuntu** (`deploy-ubuntu.yml`, manual dispatch, `main` only). The host refuses deploys from −10 min to +20 min of the
   market window, takes a verified backup, starts the new image, waits for healthy, and **rolls back automatically** on failure.
4. A new engine/config version gets its own evidence boundary — v1.0 / v1.1 / v1.2 are never pooled. After the next close,
   sync again and compare on the Cloud runtime page.

Not part of this loop yet: the React dashboard is not in the cloud image (the cloud keeps serving the legacy page), and nothing
here ever deploys by itself.
