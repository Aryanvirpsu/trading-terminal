# ROBINHOOD_READONLY_AFTER.md

Phase 4A result: the terminal now integrates the **official Robinhood MCP server**
as a **read-only** account source, shown separately from the paper account. No
orders, no ML, no scraping. Scanner and stock-view performance work is untouched.

---

## Final report

| Check | Result | Evidence |
|---|---|---|
| MCP connected | ✅ transport + OAuth **confirmed live** | 401 + `WWW-Authenticate: Bearer resource_metadata=…`; public metadata resolves `authorization/token/registration` endpoints (`ROBINHOOD_MCP_AUDIT.md` §1). Full auth handshake implemented in `lab/robinhood_mcp.py`. |
| Tools discovered | ✅ mechanism built, ⏳ live list needs owner auth | `list_tools()` (`tools/list`); the account owner runs `python lab/robinhood_mcp.py tools` after OAuth (`ROBINHOOD_MANUAL_TEST.md`). Client resolves real tool names at runtime. |
| Cash account visible | ✅ | `/api/accounts?account=robinhood` → `cash` panel (portfolio, cash, buying power, positions, cost basis, unrealized/realized P&L, orders, type, masked id, last refresh). Rendered as **Robinhood · Cash / Primary**. |
| Cash account read-only | ✅ | `cash.read_only=true`; `READ ONLY` badge; the client refuses every write/order tool before any network call (`test_order_tools_blocked_before_network`). |
| Agentic account separated | ✅ | `agentic` is a distinct panel/key; classified by account type (`test_view_agentic_detection`); **never combined** with cash. Execution capability shown only if a write tool is discovered — and it stays disabled. |
| Paper account separated | ✅ | Rendered as its own card with a "balances are never combined" note; `/api/accounts` keeps `paper` / `cash` / `robinhood` as separate keys. |
| Position context visible | ✅ | `/api/symbol/rh_position` + the **Your Robinhood Position** stock-page panel: shares, avg cost, market value, unrealized P&L, portfolio weight, orders, holding account (`test_position_for_ticker`). |
| Secrets redacted | ✅ | Tokens/codes/secrets stripped from logs (`test_log_redaction_strips_secrets`); account numbers masked to `•••• 1234` and never leaked (`test_view_masks_account_numbers`); OAuth **token only**, chmod 600 — no password stored. |
| Outage handled | ✅ | Robinhood failure → structured state + stale snapshot, never raises; the stock page and other panels keep working (`test_view_outage_serves_stale`, `test_view_never_raises_on_bad_tool`; live: `/api/symbol/summary` still 200 while RH is `auth_required`). |

---

## What was built

* **`lab/robinhood_mcp.py`** — read-only Streamable HTTP MCP client: OAuth 2.0 +
  PKCE (discovery → dynamic client registration → auth code → token → refresh),
  `initialize` / `tools/list` / `tools/call`, session reconnect on 401, strict
  timeouts, structured `RobinhoodMCPError`, secret-redacted logs, account masking,
  and a **read-only guard** that blocks order tools unless
  `ROBINHOOD_TRADING_ENABLED=true` (default false).
* **`lab/robinhood_view.py`** — turns discovered tools into the dashboard's
  **separate** Cash and Agentic account objects (never combined), with a 120 s
  stale-while-error cache and `position_for(symbol)` for the stock page.
* **`dashboard/app.py`** — `/api/accounts?account=robinhood` and
  `/api/symbol/rh_position` (both non-blocking, `_clean`-serialised).
* **`dashboard/terminal.html`** — a **Robinhood** account button; a full account
  dashboard (Cash `READ ONLY` + Agentic + Paper as separate cards with connection
  status, endpoint, last refresh, positions, P&L); and an independent, non-blocking
  **Your Robinhood Position** panel on the stock page.
* **`.env.example`** — `ROBINHOOD_MCP_URL`, `ROBINHOOD_MCP_REDIRECT_URI`,
  `ROBINHOOD_MCP_TIMEOUT`, and the safety switch `ROBINHOOD_TRADING_ENABLED=false`.

## Safety (phase 4A)
No order tools invoked · no order button · no password stored · account numbers
masked · tokens/values redacted from logs · `ROBINHOOD_TRADING_ENABLED=false`.
Agentic execution is surfaced **only** when confirmed by a discovered MCP tool, and
even then it is disabled in this phase.

## Tests
`pytest tests/ -q` → **235 passed, 1 skipped** (27 new in
`tests/unit/test_robinhood_mcp.py`: auth success/failure, discovery, multiple
accounts, cash read access, agentic detection, empty portfolio, timeout, session
expiry + reconnect, cached fallback, masking, redaction, outage-not-blocking). Real
Robinhood auth is a **manual** read-only test (`ROBINHOOD_MANUAL_TEST.md`) — never
automated, never an order.

## Startup + authentication commands
```bash
# 1) run the terminal
python dashboard/app.py                         # http://127.0.0.1:5057

# 2) authorize the read-only Robinhood MCP (account owner, in a browser)
python lab/robinhood_mcp.py discover
python lab/robinhood_mcp.py register
python lab/robinhood_mcp.py authurl             # open URL, log in, approve
python lab/robinhood_mcp.py exchange --code <CODE> --verifier <VERIFIER>
python lab/robinhood_mcp.py status              # -> connected
# then click the "Robinhood" account button in the terminal
```

## Remaining / next phase
Live tool discovery + real balances need the owner's one-time browser OAuth (I have
no credentials and must not enter them). **Order execution is deliberately NOT
implemented** — that is a later phase, gated behind `ROBINHOOD_TRADING_ENABLED` and
an explicit confirmation flow.
