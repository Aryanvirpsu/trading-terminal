# ROBINHOOD_LIVE_VERIFICATION.md

Live, **read-only** verification of the Robinhood MCP integration against the real
account (owner OAuth completed 2026-07-26). Trading stayed disabled the entire
time; **no write/order/review/exercise tool was ever invoked**.

> Privacy: this document contains **no** balances, account numbers, tokens, or
> position data. Account references are masked to the last 4 digits only.

---

## Result

| Check | Result | Evidence |
|---|---|---|
| OAuth completed | ✅ | Owner logged in via browser; `exchange` returned a token (`has_refresh_token: true`, ~8.5-day expiry). `status` → `connected`. No password ever seen or stored. |
| Tools discovered | ✅ | `tools/list` → **52 tools**. Classified in `ROBINHOOD_MCP_AUDIT.md §3` (33 reads allowed, 19 mutations refused). |
| Cash account loaded | ✅ | `get_accounts` → `get_portfolio(account_number)` → portfolio value, cash, buying power all present for the default individual account (masked `•••• 5856`), `currency=USD`. |
| Cash account read-only | ✅ | `cash.read_only = true`, `READ ONLY` badge in the UI; the client refuses every mutation tool and the dashboard never called one (`test_view_never_calls_a_write_tool`). |
| Agentic account separated | ✅ | Distinct account (`•••• 7894`, `agentic_allowed=true`) shown in its own panel — **different** account from cash; balances never combined. Badge: “EXECUTION AVAILABLE · DISABLED”. |
| Positions reconciled | ✅ | `get_equity_positions` (paginated, deduped) → this login holds **0 equity positions** currently; both panels show 0, consistent with an empty portfolio. Field mapping + pagination + dedup verified against real-shape fixtures. |
| P&L reconciled | ✅ | Unrealized P&L derived from positions (0, no holdings). Realized P&L via `get_realized_pnl(span=year, asset_classes=["equity"])` → `realized_pnl_state = ok` (reads `total_returns`); the earlier `invalid_request` was fixed by supplying `asset_classes[]`. |
| Stock-page context matched | ✅ | `/api/symbol/rh_position` uses the SAME normalized account snapshot; with 0 holdings it reports `held=false` for every symbol — matching the dashboard (`0 positions`). When holdings exist, weight = position MV ÷ that account's portfolio value. |
| Secrets redacted | ✅ | Logs redact tokens/codes; the token file drops Robinhood's `mfa_code`/`backup_code`/`user_uuid` (only OAuth-standard fields kept). API + rendered DOM contain **0** raw account numbers (verified programmatically). |
| No write tools invoked | ✅ | Default-deny allowlist: `exercise_option`, `place_*`, `review_*`, `cancel_*`, and watchlist/scan mutations all refused **before any network call**; the dashboard code path calls only reads. |

**Accounts on this login:** 4 (individual-default cash, individual agentic-enabled,
Roth IRA, Traditional IRA) — each shown separately, masked, never summed.

---

## Auth hardening verified

* **Token storage** — only OAuth-standard fields persisted (`access_token`,
  `refresh_token`, `token_type`, `scope`, `expires_in`); `mfa_code`/`backup_code`/
  `user_uuid` dropped. PKCE material deleted after exchange. *(Note: on Windows
  `chmod 600` is a no-op — the file relies on the user-profile ACL; on POSIX the
  600 bit is applied.)*
* **Refresh-token handling** — `refresh()` exchanges the refresh token for a new
  access token successfully.
* **Reconnect after expiry** — forcing the stored token to expired triggers an
  automatic refresh on the next call; a mid-session 401 refreshes + re-`initialize`s
  and retries once.
* **Redacted logs** — bearer tokens, auth codes, and long opaque strings are
  replaced with `<redacted>`.

## Mapping hardening (real schema, not one sample)

`lab/robinhood_view.py` now: reads the nested `structuredContent.data.<list|obj>`;
parses **string** money values; handles **currency** (`currency` / `display_currency`);
follows **cursor pagination** for positions/orders; **dedups** positions across
pages by (symbol, instrument_id); normalizes timestamps to ISO; fills missing
fields (market value ← qty×price, cost basis ← avg×qty, unrealized ← MV−cost); and
surfaces **clear permission/error states** (e.g. `realized_pnl_state`) instead of
fabricating values. A stale-while-error cache keeps a Robinhood blip from blanking
the panel or blocking the rest of the terminal.

## Tests

`pytest tests/ -q` → **239 passed, 1 skipped**. Robinhood suite: **31 tests**
(`tests/unit/test_robinhood_mcp.py`) driven by **real-shape fixtures**
(`tests/unit/fixtures_robinhood.py`): auth success/failure, discovery, multiple
accounts separated, cash read access, agentic detection + execution flag, realized
P&L mapped + error state, pagination + dedup, currency, empty portfolio, timeout,
session expiry + reconnect, cached stale fallback, masking, redaction,
outage-not-blocking, and **never-calls-a-write-tool**. Real auth is manual and
read-only only — never automated, never an order.

## Status

Live read-only verification **successful**. Trading remains **disabled**
(`ROBINHOOD_TRADING_ENABLED=false`). Order execution is intentionally not
implemented — stop here per the phase scope.
