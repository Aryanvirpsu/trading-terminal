# ROBINHOOD_MCP_AUDIT.md

Audit of the **official Robinhood MCP server** and the read-only client built to
consume it. This is a real MCP integration (JSON-RPC over Streamable HTTP with
OAuth 2.0), **not** robin_stocks, **not** SnapTrade, and **not** REST scraping.

Endpoint: `https://agent.robinhood.com/mcp/trading`

---

## 1. Transport + auth — CONFIRMED against the live endpoint (2026-07-26)

An unauthenticated MCP `initialize` probe (standard handshake, no credentials, no
orders) returned:

```
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer resource_metadata=
    "https://agent.robinhood.com/.well-known/oauth-protected-resource/mcp/trading"
access-control-expose-headers: Mcp-Session-Id      ← Streamable HTTP transport
access-control-allow-methods: GET, POST, OPTIONS, DELETE
```

So the server implements the **MCP Authorization spec** (OAuth 2.0 Protected
Resource, RFC 9728). Fetching the public discovery metadata (no auth) gives the
full flow:

| Field | Value (verified) |
|---|---|
| Protected-resource metadata | `…/.well-known/oauth-protected-resource/mcp/trading` |
| `resource` | `https://agent.robinhood.com/mcp/trading` |
| `authorization_servers` | `https://agent.robinhood.com/mcp/trading` |
| `scopes_supported` | `internal` |
| `bearer_methods_supported` | `header` |
| **authorization_endpoint** | `https://robinhood.com/oauth` |
| **token_endpoint** | `https://api.robinhood.com/oauth2/token/` |
| **registration_endpoint** | `https://agent.robinhood.com/oauth/trading/register` |
| `grant_types_supported` | `authorization_code`, `refresh_token` |
| `code_challenge_methods_supported` | `S256` (PKCE **required**) |
| Transport | Streamable HTTP (`Mcp-Session-Id`, `Accept: application/json, text/event-stream`) |

**Auth flow implemented** (`lab/robinhood_mcp.py`):
1. Discover protected-resource → authorization-server metadata.
2. **Dynamic Client Registration** (RFC 7591) → `client_id` (public client, `token_endpoint_auth_method=none`).
3. **Authorization Code + PKCE (S256)** — the user opens `https://robinhood.com/oauth?…` and logs in **in their own browser** (this app never sees the password); redirect returns `?code=`.
4. Exchange `code` at the token endpoint → `access_token` + `refresh_token`.
5. `Authorization: Bearer <token>` on every MCP call; `refresh_token` used on expiry.

Tokens (never passwords) are stored at
`~/.tradingview_mcp_data/robinhood_mcp_token.json` (chmod 600) and are redacted
from all logs.

---

## 2. MCP methods used

| MCP method | Purpose | Implemented |
|---|---|---|
| `initialize` + `notifications/initialized` | handshake, session id | ✅ `initialize()` |
| `tools/list` | **tool discovery** (name, description, inputSchema) | ✅ `list_tools()` |
| `tools/call` | invoke a tool (read-only guarded) | ✅ `call_tool()` |

`_rpc()` handles: bearer + `Mcp-Session-Id`, JSON *and* SSE response bodies,
401 → refresh + re-`initialize` + retry once (session expiry), 403 → `forbidden`,
5xx → `outage`, network error → `timeout`/`outage`. Every failure is a structured
`RobinhoodMCPError(kind, detail)` with `kind ∈ not_configured | auth_required |
token_expired | timeout | transport | tool_error | forbidden | outage`.

---

## 3. Discovered tools — LIVE (`tools/list`, owner-authenticated 2026-07-26)

**52 tools discovered.** Classified below by the client's safety model. This phase
DEFAULT-DENIES: while `ROBINHOOD_TRADING_ENABLED=false`, only tools on the explicit
READ allowlist may be called — everything else (orders, previews, cancels,
exercises, watchlist/scan mutations, and any unknown/future tool) is refused
**before any network call**. Verified: 33 reads allowed, 19 mutations refused; no
write tool is ever invoked by the dashboard (`test_view_never_calls_a_write_tool`).

### Account / holdings / P&L reads used by the dashboard

| Tool | Args (required **bold**) | Class | Notes / tested response |
|---|---|---|---|
| `get_accounts` | — | READ ✅ | 4 accounts. Fields: `account_number`, `rhs_account_number`, `type`, `brokerage_account_type`, `management_type`, `agentic_allowed`, `is_default`, `option_level`, `deactivated`. |
| `get_portfolio` | **account_number** | READ ✅ | `total_value`, `equity_value`, `options_value`, `cash`, `currency`, `buying_power{buying_power, unleveraged_buying_power, display_currency}` — money values are **strings**. |
| `get_equity_positions` | **account_number**, `cursor` | READ ✅ | `{positions:[…], next_cursor?}` — **paginated**; empty for a cash-only account. |
| `get_option_positions` | **account_number**, +filters, `cursor` | READ ✅ | paginated option positions. |
| `get_realized_pnl` | **account_number**, `span`∈{3month,all,day,month,week,year}, **asset_classes[]**, `display_currency`, `timezone` | READ ✅ | returns `total_returns`, `total_rate_of_return`, `data_points[]`. Requires `asset_classes` as an **array** (else `InvalidArgument: un-specified asset class`). |
| `get_equity_orders` | **account_number**, +filters, `cursor` | READ ✅ | orders list (paginated). *(name contains "orders" but it is a READ — the substring guard would misclassify it, which is why we use an explicit allowlist.)* |
| `get_option_orders` | **account_number**, … | READ ✅ | as above. |
| `get_equity_tax_lots` | **account_number**, **symbol**, `cursor` | READ ✅ | per-lot cost basis. |
| `get_pnl_trade_history` | **account_number**, `span`, `symbol`, `cursor` | READ ✅ | per-trade realized P&L. |

Other reads allowed (market data): `get_equity_quotes`, `get_option_quotes`,
`get_index_quotes`, `get_indexes`, `get_equity_fundamentals`, `get_financials`,
`get_equity_historicals`, `get_option_historicals`, `get_equity_price_book`,
`get_equity_technical_indicators`, `get_earnings_calendar`, `get_earnings_results`,
`get_equity_tradability`, `get_option_chains`, `get_option_instruments`,
`get_option_level_upgrade_info`, `get_watchlists`, `get_watchlist_items`,
`get_option_watchlist`, `get_popular_watchlists`, `get_scans`,
`get_scanner_filter_specs`, `run_scan`, `search`.

### WRITE / mutation tools — REFUSED in this phase (never invoked)

| Tool | Why blocked |
|---|---|
| `place_equity_order`, `place_option_order` | places a **real order with real money** |
| `review_equity_order`, `review_option_order` | **previews** an order (the brief forbids previewing) |
| `cancel_equity_order`, `cancel_option_order`, `cancel_option_exercise` | cancels an order/exercise |
| `exercise_option` | **exercises an option — real money action** (note: no "order" substring — caught only by the allowlist) |
| `create_scan`, `update_scan_config`, `update_scan_filters` | mutate saved scanners |
| `create_watchlist`, `update_watchlist`, `add_to_watchlist`, `add_option_to_watchlist`, `remove_from_watchlist`, `remove_option_from_watchlist`, `follow_watchlist`, `unfollow_watchlist` | mutate watchlists |

### Supported account types (this login)

`individual` (cash, self-directed) — one is `is_default`; one has
`agentic_allowed=true`; plus `ira_roth` and `ira_traditional`. No separately
managed `management_type=agentic` account exists on this login — **agentic is a
per-account capability (`agentic_allowed`)**, which is exactly how the dashboard
labels the Agentic panel (execution *available* but disabled).

### Unsupported / assumed functionality corrected

* Earlier phase-4A code assumed positions/portfolio were **embedded** in the
  account object — the real API keys them per `account_number` in **separate**
  tool calls, with **string** money values and **cursor pagination**. Fixed
  (`lab/robinhood_view.py`), with real-shape fixtures in
  `tests/unit/fixtures_robinhood.py`.
* `get_realized_pnl` needed `asset_classes[]` + a valid `span` — hardcoding a
  single sample would have missed this; the mapping is defensive.

---

## 4. Security posture (verified by tests)

* OAuth **tokens only** — no password is ever sent to or stored by this app.
* Tokens / auth codes / `client_secret` / long opaque strings are **redacted** from
  every log line (`test_log_redaction_strips_secrets`).
* Account numbers are **masked** to `•••• 1234` before leaving the client
  (`test_account_masking`, `test_view_masks_account_numbers`); the raw number never
  appears in any API response.
* Cash and Agentic balances are kept **separate** and never summed.
* A Robinhood outage returns a structured state (and a stale snapshot if available)
  — it never raises into, or blocks, the rest of the terminal.

Files: `lab/robinhood_mcp.py` (client), `lab/robinhood_view.py` (dashboard
normaliser), `tests/unit/test_robinhood_mcp.py` (27 tests).
