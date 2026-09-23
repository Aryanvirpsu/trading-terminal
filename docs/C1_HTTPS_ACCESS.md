# C1_HTTPS_ACCESS.md — C1.6: secure browser access to the OCI cloud-synthetic deployment

_Record date 2026-09-23 · branch `c1/cloud-runtime` · host `avdi` (Oracle Cloud A1, `aarch64`,
129.159.91.34) · builds on `docs/C1_ARM_EQUIVALENCE.md`._

## 1. Topology

```
Internet ──▶ 80/443 (public) ──▶ avdi-proxy (Caddy, TLS + Basic Auth) ──▶ avdi-dashboard-cloud:5057
                                        │                                  (127.0.0.1-only, unchanged)
                                        └── automatic HTTPS (Let's Encrypt, HTTP-01)

avdi-scheduler-cloud: unchanged, one-shot, restart:"no", no proxy path, no recurring trigger.
```

Caddy (`avdi-proxy`) is the only service with a host-published, publicly-reachable port. It joins
the existing compose project's default network (`docker_default`) and reaches the dashboard by
container name (`avdi-dashboard-cloud:5057`) over the private Docker network — the dashboard's own
`127.0.0.1:5057` host binding in `docker/compose.c1.yml` was never touched.

**Hostname:** `129-159-91-34.sslip.io` — a "magic" DNS service that resolves any
`<ip-with-dashes>.sslip.io` name to the embedded IP with no registration, no cost, and no DNS
provider involvement. Chosen explicitly (user decision) over buying or delegating a real domain, to
preserve the $0 constraint with zero extra moving parts.

## 2. Files

| File | Committed? | Contents |
|---|---|---|
| `docker/compose.c1.pin.yml` | yes | Overlay pinning both images to `@sha256` digests (no rebuild) — see §3 |
| `docker/compose.c1.proxy.yml` | yes | Adds the `avdi-proxy` (Caddy) service, ports 80/443, persistent `caddy_data`/`caddy_config` volumes — no secrets |
| `docker/Caddyfile.template` | yes | Structure only, placeholder hostname/hash, with instructions to regenerate |
| `docker/Caddyfile` | **no — host-only** | The real config: hostname + **bcrypt hash only**, `chmod 600`, lives at `~/avdi-deploy/docker/Caddyfile` on `avdi`. No plaintext password anywhere on disk or in git. |

Full deploy invocation on the host (unchanged project name `docker`, unchanged existing volumes):

```bash
cd ~/avdi-deploy
export AVDI_TAG=sha-cd595eb
docker compose -f docker/compose.c1.yml -f docker/compose.c1.pin.yml -f docker/compose.c1.proxy.yml \
  --profile cloud-synthetic up -d
```

## 3. Deployment acceptance — verified live, not assumed

Independently re-inspected on the host before any C1.6 change (i.e. re-derived, not taken on faith
from the earlier deployment summary):

| | dashboard | scheduler |
|---|---|---|
| Running image reference (`docker inspect .Image`) | `sha256:deb3526440ac5142385883f2f91bcd42fdb46313b30136e27c4a72bbef46b97b` | `sha256:cbe22a27f8fdcb6004b10e7af32a6b5bdf12657cae826ed68e5102acaff3db10` |
| Matches `docs/C1_ARM_EQUIVALENCE.md` §3/§8 | ✅ exact | ✅ exact |
| Architecture (`docker image inspect .Architecture/.Os`) | `arm64/linux` | `arm64/linux` |
| Status | running, `healthy` | exited, code `0` |
| Restart policy | `unless-stopped` | `no` |
| Mounts | `docker_avdi_state → /home/tv/.tradingview_mcp_data`, `docker_avdi_out → /out` | same |

**Digest pin applied** — `docker/compose.c1.pin.yml` replaces the mutable `sha-cd595eb` tag
reference with the `@sha256` digest for both images, applied via `up -d` (no rebuild; both digests
were already the exact, already-running content).

**This pin-apply doubled as the controlled-restart test:**

| Check | Before | After |
|---|---|---|
| `docker_avdi_state` volume `CreatedAt` | `2026-09-23T17:26:52Z` | `2026-09-23T17:26:52Z` — **unchanged**, proving the same volume was reattached, not recreated |
| `robinhood_500_baseline.db` size/mtime | `size=126976 mtime=1790184416` | `size=126976 mtime=1790184416` — **byte-identical** |
| Scheduler `status` reconciliation | `"reconciled": true`, equity $500, 0 positions | `"reconciled": true`, equity $500, 0 positions — unchanged |
| Dashboard health | `healthy`, `/api/health` → 200 | `healthy`, `/api/health` → 200 |
| `config_version` / `pip_freeze_sha256_16` | `cfg-a0eede144e` / (per-image, matches C1-ARM) | unchanged |

No cron entry or systemd timer references the scheduler — confirmed via `crontab -l` and
`systemctl list-timers` on the host; it has run exactly once, on demand, at initial deploy and once
more as a side effect of the digest-pin recreate. No recurring trigger exists.

## 4. Access prerequisites — what existed before this gate

Inspected before making any change: no `nginx`/`caddy`/`traefik`/`certbot` package installed, no
other containers besides the two AVDI services, host-level `iptables` (via `iptables-persistent`)
allowed only loopback, established/related, ICMP, and `tcp/22` — everything else hit an explicit
`REJECT`. No DNS, proxy, or auth layer of any kind existed.

## 5. Proxy and auth choice, explained

**Caddy** — a single static binary with a built-in ACME client. Chosen over nginx+certbot because
it needs no separate cert-renewal cron/systemd timer (Caddy manages its own renewal internally,
backed by persistent storage), has official `arm64` images, and its `reverse_proxy` directive
proxies WebSocket upgrades with no extra configuration — relevant if the dashboard grows a
WebSocket endpoint later (it doesn't have one today — confirmed via `grep -rniE
"socketio|websocket|/ws\b|flask_sock"` across `dashboard/` and `docker/serve_dashboard.py`, no
matches).

**HTTP Basic Auth via Caddy's `basic_auth` directive**, bcrypt-hashed, declared *before*
`reverse_proxy` in the same site block. Caddy evaluates directives in that order for every matched
request, so **every** path — `/`, `/api/*`, and any future WebSocket upgrade handshake alike — must
clear auth before Caddy will proxy it; there is no route that bypasses this, and nothing in
`dashboard/` or `docker/serve_dashboard.py` was changed to make this true. A single shared
credential was chosen (not per-user accounts) because this is a one-operator synthetic deployment,
not a multi-tenant service — consistent with the project's existing single-operator paper-trading
model.

Credential handling: the password was generated locally (24 random alphanumeric characters), hashed
immediately with Caddy's own `caddy hash-password`, and only the bcrypt hash was ever written to
disk (host-only, `chmod 600`, never committed). The plaintext was held only in this session's memory
and local scratch files, both since deleted, and is relayed to the user once, out of band from this
document.

## 6. Firewall changes

**Host-level (`iptables`, applied and persisted via `netfilter-persistent`):** two `ACCEPT` rules
added for `tcp/80` and `tcp/443` (`NEW` state), inserted immediately after the existing `tcp/22`
rule and before the catch-all `REJECT` — same style as the pre-existing SSH rule. Nothing else in
`INPUT`, `FORWARD`, or `OUTPUT` was touched. SSH access was verified continuously throughout (every
command in this gate ran over the same SSH session).

**Cloud-level (OCI Security List / NSG):** this layer is not reachable via SSH and was not, and
could not be, changed by this session. The initial attempt failed here — Let's Encrypt's own HTTP-01
and TLS-ALPN-01 validation requests timed out with "likely firewall problem," independently
reproduced from two external vantage points (this dev machine, via both `curl` and `openssl
s_client`). The user added ingress rules for `tcp/80` and `tcp/443` (source `0.0.0.0/0`) in the OCI
Console; Caddy's already-running retry loop (60s backoff, up to 30 days) picked up the change
without any restart.

## 7. Verification evidence

All checks below were run from an external machine (this dev machine, genuinely outside OCI's
network) after the Security List change, and cross-checked against Caddy's own logs and `docker
inspect` on the host.

| Check | Result |
|---|---|
| Certificate valid for `129-159-91-34.sslip.io` | ✅ `openssl s_client` shows `issuer=... O=Let's Encrypt, CN=YE2`, `subject=CN=129-159-91-34.sslip.io`, `notBefore=Sep 23 2026`, `notAfter=Dec 22 2026` (standard 90-day Let's Encrypt validity) |
| `http://…/` redirects to HTTPS, no dashboard content | ✅ `308 Permanent Redirect`, `Location: https://…/`, `Content-Length: 0` |
| Unauthenticated `GET /` | ✅ `401`, `WWW-Authenticate: Basic realm="restricted"` |
| Unauthenticated `GET /api/health` | ✅ `401` |
| Unauthenticated `GET /api/paper/account` | ✅ `401` |
| Wrong credentials | ✅ `401` |
| Correct credentials, `GET /api/health` | ✅ `200`, real dashboard JSON payload returned |
| Correct credentials, `GET /` | ✅ `200` |
| Port `5057` reachable directly from outside | ✅ confirmed **not** reachable — connection timeout, same as before this gate (host binding never changed from `127.0.0.1:5057`) |
| Dashboard health after all of the above | ✅ still `healthy` |
| Images / `config_version` / TZ / broker guards / scheduler behavior | ✅ unchanged — see §3 table |
| Certificate storage persistence | ✅ `docker volume inspect docker_caddy_data` — a named volume, survives container recreation; cert/key files confirmed present under `/data/caddy/certificates/.../129-159-91-34.sslip.io.{crt,key,json}` |

**Checks not independently performed:** WebSocket-specific behavior could not be exercised because
the dashboard does not currently expose a WebSocket endpoint (§5) — the auth-in-front-of-everything
property was verified structurally (site-block-level `basic_auth` ahead of `reverse_proxy`, no
path-scoped exception) rather than against a live WS connection. Certificate auto-renewal (Caddy
renews automatically in the background well before the Dec 22 2026 expiry) was not observed
end-to-end since that is weeks away — only the persistent storage it depends on was confirmed.

## 8. Rollback

To remove HTTPS/auth access entirely and return to the pre-C1.6 state (dashboard reachable only via
SSH tunnel or `127.0.0.1`, no public ports):

```bash
cd ~/avdi-deploy
docker compose -f docker/compose.c1.yml -f docker/compose.c1.pin.yml -f docker/compose.c1.proxy.yml \
  --profile cloud-synthetic stop avdi-proxy
docker compose -f docker/compose.c1.yml -f docker/compose.c1.pin.yml -f docker/compose.c1.proxy.yml \
  --profile cloud-synthetic rm -f avdi-proxy
sudo iptables -D INPUT -p tcp -m state --state NEW -m tcp --dport 80 -j ACCEPT
sudo iptables -D INPUT -p tcp -m state --state NEW -m tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

This does **not** touch `docker_avdi_state`/`docker_avdi_out` (dashboard/scheduler data) or
`docker_caddy_data`/`docker_caddy_config` (certs — left in place in case access is re-enabled
later; delete manually with `docker volume rm` only if a full teardown is wanted). The OCI Security
List rules for 80/443 would need to be removed separately, in the console, if desired — this session
cannot do that either.

## 9. Access procedure

- URL: `https://129-159-91-34.sslip.io/`
- Username: `avdi`
- Password: relayed once, directly, out of band from this document and from git — not written here.

## 10. Boundaries respected

No read, mount, copy, or reference to the real `~/.tradingview_mcp_data` at any point (that path
doesn't exist on this host at all). No `compose down -v`, no volume deletion. No change to
`lab/paper/`, `decision_engine.py`, `paper_scheduler.py`, or Case 1/Case 2 semantics —
`BROKER_PROVIDER=none`/`ROBINHOOD_TRADING_ENABLED=false` confirmed unchanged via the same
`docker/c0_preflight.py` guard, unmodified. No recurring scheduler trigger was created. No Nautilus,
Supabase, Postgres, Redis, or additional cloud compute. No application image rebuild — both images
remain the exact `sha-cd595eb` digests verified in `docs/C1_ARM_EQUIVALENCE.md`. The dashboard was
never exposed unauthenticated, even transiently — Caddy's automatic-HTTPS redirect and `basic_auth`
were both present in the Caddyfile from the container's very first start.
