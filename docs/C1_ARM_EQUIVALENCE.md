# C1_ARM_EQUIVALENCE.md — multi-arch (amd64+arm64) publish pipeline, closed with real evidence

_Record date 2026-09-23 · branch `c1/cloud-runtime` @ `cd595eb` (C1-ARM) · continues from
`v2-c1-prep-baseline` · builds on `docs/C1_PREP_ACCEPTANCE.md`,
`.github/workflows/publish-avdi-images.yml`._

Scope of this gate: extend the existing GHCR publish pipeline to build, independently validate,
and publish a genuine multi-arch (`linux/amd64` + `linux/arm64`) manifest for both AVDI images,
with **no rebuild after validation** — the published artifact is the validated artifact — then
pull that exact SHA-tagged image on the OCI A1 host and verify it there. Every claim below is
backed by a real CI run, a real digest, or a real command run either from this machine or on the
OCI host directly — never a YAML read-through.

---

## 1. Pipeline design — how "published == validated" is guaranteed under multi-arch

`docker buildx build --load` cannot load a multi-platform build into the local Docker daemon —
only a single platform at a time. So each architecture is built, loaded, and independently
validated (container preflight + real smoke check) **before anything is pushed**, under its own
per-arch tag. Only after that per-arch image has already passed validation and already been
pushed are the two already-pushed digests combined into the final multi-arch manifest via
`docker buildx imagetools create` — which never rebuilds, never re-pulls source layers, and
cannot silently substitute a different image than the one that was tested.

Job graph in `.github/workflows/publish-avdi-images.yml`:

```
gate (amd64-native: compose config + build-context audit + full pytest, both TZ profiles)
  → meta (computes lowercased repo, sha_tag, full tag lists — once)
    → dashboard-amd64 ─┐            → scheduler-amd64 ─┐
    → dashboard-arm64 ─┴→ dashboard-manifest            → scheduler-manifest
                                      (needs scheduler-amd64 + scheduler-arm64 + meta)
```

Each `*-amd64`/`*-arm64` job: checkout → [QEMU setup, arm64 only] → buildx setup → build
(`load: true`, single platform, `provenance: false`) → container preflight
(`docker/c0_preflight.py`) → real smoke check (dashboard: poll `/api/health`; scheduler: run
`python automation/paper_scheduler.py status`, assert `"reconciled": true`; arm64 additionally
runs the full `automation/paper_scheduler.py all` premarket/hours/postmarket cycle,
best-effort/`continue-on-error`) → GHCR login (skipped on PR) → push under
`ghcr.io/<repo>/avdi-<name>:sha-<short>-<arch>` → output the pushed digest.

Each `*-manifest` job: GHCR login → `docker buildx imagetools create` combining the two
already-pushed per-arch digests under every real deployment tag → `imagetools inspect --raw`,
parsed to assert the platform list is **exactly** `['linux/amd64', 'linux/arm64']`, failing loudly
otherwise → extract and output the final combined-manifest digest.

---

## 2. Pre-build risk checks (done before writing any workflow code)

| Check | Method | Result |
|---|---|---|
| Pinned base image is a genuine multi-arch manifest list | `docker buildx imagetools inspect python:3.11.16-slim-bookworm@sha256:a36c24f9…` | confirmed — includes `linux/arm64` alongside `linux/amd64`; no `Dockerfile.terminal` change needed |
| All 60 frozen pins in `docker/requirements-c0-case1.txt` have real `aarch64` wheels | live query of `https://pypi.org/pypi/<name>/<version>/json` for every pin | all 60 confirmed — no change needed to the frozen dependency file |
| Local arm64 build/preflight/smoke works before trusting CI | full dashboard + scheduler build/preflight/smoke run locally under Docker Desktop's built-in QEMU, including the full scheduler `all` cycle | passed, before any workflow change was pushed |
| `imagetools create`/`inspect` combine-and-verify logic works before trusting real CI | tested against a scratch local `registry:2` container (`docker run -d -p 127.0.0.1:5500:5000 registry:2`) with two locally-built, locally-pushed single-arch images | **caught a real bug** — see §4 |

---

## 3. Real CI run — 35839682304

All 8 jobs succeeded on the run that included both fixes from §4.

| Job | Result | Duration |
|---|---|---|
| `gate` | ✅ | 96s |
| `meta` | ✅ | 6s |
| `dashboard-amd64` | ✅ | 93s |
| `dashboard-arm64` | ✅ (QEMU) | 376s |
| `scheduler-amd64` | ✅ | 83s |
| `scheduler-arm64` | ✅ (QEMU) | 447s |
| `dashboard-manifest` | ✅ | 8s |
| `scheduler-manifest` | ✅ | 6s |

**`gate` test result** (both TZ profiles, judged against the known baseline by
`docker/tools/ci_test_gate.py`, not by pytest's raw exit code):

```
6 failed, 1272 passed, 9 warnings in 26.72s
collected=1278 passed=1272 always_red=6 never_gated=3
CI TEST GATE PASSED — matches the known baseline exactly.
```

Identical to the pre-C1-ARM baseline — this change touched no application or test code.

**Published manifests** (immutable `sha-cd595eb` tag, matching the merge-point commit on
`c1/cloud-runtime`):

| Image | Combined manifest digest | `linux/amd64` child | `linux/arm64` child |
|---|---|---|---|
| `ghcr.io/aryanvirpsu/trading-terminal/avdi-dashboard:sha-cd595eb` | `sha256:deb3526440ac5142385883f2f91bcd42fdb46313b30136e27c4a72bbef46b97b` | `sha256:c0de698371519cf447c17a464a2e104b19fd6e71f962a07910cfbea8ced69fef` | `sha256:2860a34fc3cc95c499f1c97f9528e02c54cdeb36aea73e258af7fe722d1a49be` |
| `ghcr.io/aryanvirpsu/trading-terminal/avdi-scheduler:sha-cd595eb` | `sha256:cbe22a27f8fdcb6004b10e7af32a6b5bdf12657cae826ed68e5102acaff3db10` | `sha256:ddf43baaa3620e4b48b08292bba99a5a92db034c1cd7a91652050c1afa70e7b4` | `sha256:10b486e05019c933119bd0ca6fe1ff583f68e256ef444bee4f03da2f72406191` |

Both `dashboard-manifest` and `scheduler-manifest` jobs independently asserted
`platforms=['linux/amd64', 'linux/arm64']` — no more, no less — before completing.

---

## 4. Bugs found and fixed during this gate

1. **YAML syntax error.** A job `name:` contained an unquoted colon
   (`Smoke — full … cycle (best-effort, per spec's "preferably")`), which YAML parsed as a mapping
   value and failed with `mapping values are not allowed here` at parse time. Fixed by quoting the
   entire string.
2. **Attestation-manifest pollution in the combined manifest — caught locally, before it ever hit
   real CI.** After combining two real single-arch images via `imagetools create` against the
   scratch registry, `imagetools inspect --raw` showed **four** manifest entries, not two: the two
   real platform manifests plus two `attestation-manifest` entries with `platform:
   unknown/unknown`. The platform-equality check in §1 would have failed on every build,
   regardless of correctness. Root cause: `docker/build-push-action@v6` attaches
   provenance/attestation by default; the repo's pre-existing `publish-image.yml` already disables
   this but the new per-arch build steps in `publish-avdi-images.yml` did not. Fixed by adding
   `provenance: false` to all four per-arch `docker/build-push-action@v6` steps. Re-verified clean
   against the scratch registry, then re-verified clean again in the real CI run.

---

## 5. Independent local re-verification (separate from, and in addition to, CI's own checks)

Everything below was run from this development machine against the **actual published GHCR
images**, deliberately re-doing what CI already proved, to rule out anything registry- or
runner-specific. No host volume was mounted in any of these `docker run` invocations — all state
referenced (`/home/tv/.tradingview_mcp_data` inside the container) is the container's own
filesystem, never the real `~/.tradingview_mcp_data` on this host.

**Manifest re-inspection** — `docker buildx imagetools inspect` from this machine reproduced the
exact same combined and per-arch digests CI reported, for both images (§3 table).

**Native pull resolves to amd64 on this host** — `docker pull ghcr.io/…/avdi-dashboard:sha-cd595eb`
and `…avdi-scheduler:sha-cd595eb` both resolved automatically to the `linux/amd64` child, digest
matching the pushed amd64 digest exactly.

**Explicit arm64 pull proves a real, executable arm64 image** —
`docker pull --platform linux/arm64 …` followed by
`docker run --rm --platform linux/arm64 --entrypoint uname … -m` returned `aarch64` for both
images.

**Preflight comparison, arm64 vs. amd64, both images, pulled straight from GHCR:**

| Field | dashboard amd64 | dashboard arm64 | scheduler amd64 | scheduler arm64 |
|---|---|---|---|---|
| `preflight` | PASS | PASS | PASS | PASS |
| `python` | 3.11.16 | 3.11.16 | 3.11.16 | 3.11.16 |
| `profile` | cloud-synthetic | cloud-synthetic | cloud-synthetic | cloud-synthetic |
| `tz` | UTC | UTC | UTC | UTC |
| `fixed_instant_date` | 2026-09-22 | 2026-09-22 | 2026-09-22 | 2026-09-22 |
| `config_version` | cfg-a0eede144e | cfg-a0eede144e | cfg-a0eede144e | cfg-a0eede144e |
| `pip_freeze_sha256_16` | `4c054231ac1b2195` | `4c054231ac1b2195` | `25e09283401aeffe` | `25e09283401aeffe` |

Every field is byte-identical between architectures, within each image. (Dashboard and scheduler
have different `pip_freeze` hashes from each other, as expected — different requirement sets — but
each image's own hash is invariant across `amd64`/`arm64`.)

**Scheduler smoke check, real CLI, arm64, pulled from GHCR** —
`python automation/paper_scheduler.py status` on the pulled arm64 scheduler image:

```json
{
  "account": {"equity": 500.0, "cash": 500.0, "open_positions": 0, "drawdown_pct": 0.0, "day_pnl": 0, "realized_pnl": 0},
  "milestone": {"target": 50, "progress": 0, "remaining": 50},
  "reconciliation": {"reconciled": true, "cash_from_fills": 500.0, "cash_from_state": 500.0, "delta": 0.0, "equity": 500.0, "note": "ok"},
  "schema_version": 2,
  "config_version": "cfg-a0eede144e"
}
```

`"reconciled": true` — matches the exact assertion CI's own smoke step makes, reproduced
independently on this machine against the arm64 image pulled straight from the registry. This is a
fresh, synthetic $500/0-position ledger (the container's own internal state, not any mounted real
data) — consistent with `cloud-synthetic` profile semantics.

---

## 6. Difference classification

Per the requested taxonomy — every observed difference between the arm64 and amd64 builds is:

| Difference | Classification | Basis |
|---|---|---|
| None observed in `config_version`, `python`, `tz`, `fixed_instant_date`, `pip_freeze_sha256_16`, reconciliation output, or preflight outcome | **EXACT** | §5 table — every field byte-identical across architectures, both images |
| Build duration (arm64 ~4× amd64 under QEMU emulation in CI: 376s/447s vs. 93s/83s) | **EXPECTED ARCHITECTURE DELTA** | QEMU-emulated builds are inherently slower than native; does not affect the published artifact, only CI wall-clock |
| Nothing found | — | no **KNOWN DEFECT** or **UNEXPLAINED** difference exists; nothing here triggers the stop condition |

No economically-meaningful or unexplained difference exists. The gate to proceed is clear on the
GHCR/CI side.

---

## 7. Real-state tripwire

SHA-256 fingerprint of all 7 files under the real `~/.tradingview_mcp_data`, checked before and
after every command in this gate: **unchanged throughout** —
`7ef684f97b316500625d012f42099a0b2426ea8282e6e08fc9fd335b49e0dec3` (7 files), matching the value
recorded at the end of C1-PREP. No command in this gate mounted, wrote to, or otherwise referenced
that path.

No Case 1/Case 2 ledger, decision-engine, paper-pipeline logic, Nautilus, Supabase integration, or
live-execution path was touched. `BROKER_PROVIDER=none` / `ROBINHOOD_TRADING_ENABLED=false` remain
the only values `docker/c0_preflight.py` accepts, unconditionally, for every profile including
`cloud-synthetic`.

---

## 8. OCI A1 host verification — native ARM hardware, not QEMU emulation

Host: `avdi` (Oracle Cloud A1, `aarch64`, Ubuntu, `6.17.0-1020-oracle`), reached via SSH. This is
the first point in the whole C1-ARM gate where the arm64 image runs on **genuine ARM silicon**
rather than QEMU emulation — every prior arm64 result (CI, local dev machine) was emulated.

No repo checkout exists on this host and none was created — both images were pulled directly from
GHCR (public, anonymous pull, no credential used or needed) under the immutable `sha-cd595eb` tag.

**Docker resolves the multi-arch tag to arm64 natively** (no `--platform` flag needed — the host's
own architecture is arm64, so Docker's default platform matching picks the correct child manifest
on its own):

| | dashboard | scheduler |
|---|---|---|
| `docker image inspect --format '{{.Architecture}} {{.Os}}'` | `arm64 linux` | `arm64 linux` |
| `uname -m` inside container | `aarch64` | `aarch64` |
| pulled digest | `sha256:deb3526440ac5142385883f2f91bcd42fdb46313b30136e27c4a72bbef46b97b` (combined index) | `sha256:cbe22a27f8fdcb6004b10e7af32a6b5bdf12657cae826ed68e5102acaff3db10` (combined index) |

Both match the exact digests recorded in §3, pulled independently, on genuinely different hardware
than anything used so far in this gate.

**Preflight on native ARM hardware** — identical to every other measurement in this report:

| Field | dashboard (OCI, native arm64) | scheduler (OCI, native arm64) |
|---|---|---|
| `preflight` | PASS | PASS |
| `python` | 3.11.16 | 3.11.16 |
| `config_version` | cfg-a0eede144e | cfg-a0eede144e |
| `pip_freeze_sha256_16` | `4c054231ac1b2195` | `25e09283401aeffe` |

Both hashes are byte-identical to the CI-built and locally-pulled arm64 (QEMU) values in §5 — no
divergence introduced by running on real hardware instead of emulation.

**Scheduler smoke check (real CLI)** — `python automation/paper_scheduler.py status` on the OCI
host: `"reconciled": true`, `cash_from_fills == cash_from_state == 500.0`, `delta: 0.0` — a fresh
synthetic $500/0-position ledger, container-internal state only, identical in shape to every other
smoke result in this report.

**Local-only health check, port 5057 not publicly exposed:**

```
docker run -d --name avdi-dash-verify -p 127.0.0.1:5057:5057 -e C0_PROFILE=cloud-synthetic -e TZ=UTC \
  ghcr.io/aryanvirpsu/trading-terminal/avdi-dashboard:sha-cd595eb
```

`ss -tlnp` on the host showed the published port bound as `127.0.0.1:5057` — **not** `0.0.0.0:5057`
— meaning nothing outside the host's own loopback interface can reach it, regardless of any OCI
security-list/firewall rule. `curl http://127.0.0.1:5057/api/health` from the host itself returned
`200` with a normal health payload on the first attempt. The container's own internal Flask process
logs `Running on all addresses (0.0.0.0)` — that is Flask binding inside its own network namespace,
which is irrelevant to external reachability; the `docker -p 127.0.0.1:...` publish mapping is what
governs host-level exposure, and that was loopback-only throughout.

**Cleanup verified:** the verification container was stopped and removed
(`docker stop && docker rm`), `docker ps -a` confirmed empty, and a final `ss -tlnp` sweep showed
the host back to its pre-verification state — only `ssh` (22) and internal `systemd-resolved` (53,
loopback-scoped) listening, nothing on 5057, no residual container.

No OCI security-list, firewall, or network configuration was modified — verification relied
entirely on the loopback-only Docker port bind, never on a network-layer change.

---

## 9. Summary — everything proven, nothing outstanding

- Both AVDI images build, validate, and publish correctly for both `linux/amd64` and
  `linux/arm64`, with no rebuild between validation and publish (§1–§3).
- The published multi-arch manifest for `sha-cd595eb` contains exactly the two intended platforms
  — nothing more, nothing less — for both images (§3).
- The arm64 variant is a real, independently pullable, independently executable image whose
  behavior (config, dependency closure, reconciliation logic) is byte-for-byte identical to the
  amd64 variant — reproduced on a local dev machine under QEMU (§5) **and** on genuine ARM
  hardware on the OCI A1 host (§8), with no divergence introduced by either environment.
  Cross-referencing all three environments (CI/QEMU, local dev/QEMU, OCI/native) for both images
  gives six independent preflight runs, all reporting the same `config_version` and the same
  per-image `pip_freeze_sha256_16`.
- Port 5057 was proven reachable only via loopback on the OCI host, and the host was returned to
  its pre-verification state afterward.
- None of this touched the build freeze, the real ledger, the decision engine, Nautilus, Supabase,
  or any live-broker path. The real-state tripwire on this dev machine's
  `~/.tradingview_mcp_data` was unchanged throughout (§7); the OCI host has no copy of that path at
  all.

No unexplained or economically-meaningful difference was found anywhere in this gate. C1-ARM is
complete.
