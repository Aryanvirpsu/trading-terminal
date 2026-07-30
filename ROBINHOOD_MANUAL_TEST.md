# ROBINHOOD_MANUAL_TEST.md

The one thing that **must** be done by you (the account owner), not by any agent:
authorize the read-only Robinhood MCP in your own browser. This app never sees your
Robinhood password — it only receives an OAuth token after you log in and approve.

**This is READ-ONLY.** `ROBINHOOD_TRADING_ENABLED` stays `false`; the client refuses
every order/write tool. Do not place an order in this phase.

---

## Prerequisites

```bash
cd tradingview-mcp
pip install -e .          # (requests is used; already a dep)
cp .env.example .env      # if you haven't; leave ROBINHOOD_TRADING_ENABLED=false
```

## Step 1 — confirm discovery (no login needed)

```bash
python lab/robinhood_mcp.py discover
```
Expected: the real `authorization_endpoint`, `token_endpoint`, `registration_endpoint`
(see `ROBINHOOD_MCP_AUDIT.md`). This hits only public metadata.

## Step 2 — register a client (one time)

```bash
python lab/robinhood_mcp.py register
```
Prints a masked `client_id` and saves it to
`~/.tradingview_mcp_data/robinhood_mcp_client.json`.

## Step 3 — authorize in your browser

```bash
python lab/robinhood_mcp.py authurl
```
1. Open the printed URL. **Log in to Robinhood yourself** and approve read access.
2. Your browser is redirected to `http://localhost:8765/callback?code=…&state=…`
   (the page may not load — that's fine; you only need the `code` from the address bar).
3. **Copy the `code` value** and the **`code_verifier`** the command printed.

> Prefer not to run a callback server? The redirect URL contains the `code` in the
> address bar even if the page 404s — just copy it.

## Step 4 — exchange the code for a token

```bash
python lab/robinhood_mcp.py exchange --code <CODE> --verifier <VERIFIER>
```
Stores the token (redacted) at `~/.tradingview_mcp_data/robinhood_mcp_token.json`
(chmod 600). No password is stored — only the OAuth token.

## Step 5 — verify read-only access

```bash
python lab/robinhood_mcp.py status      # -> connected
python lab/robinhood_mcp.py tools       # -> the real discovered tools + schemas
```
Paste the `tools` output into `ROBINHOOD_MCP_AUDIT.md` §3 to finish the audit.

## Step 6 — see it in the terminal

```bash
python dashboard/app.py                 # http://127.0.0.1:5057
```
Open the app → click the **Robinhood** account button (top bar). You should see:
* **Robinhood · Cash / Primary** — `READ ONLY`, portfolio value, cash, buying power,
  positions (symbol, qty, avg cost, market value, unrealized P&L), realized P&L,
  masked account id, account type, last refresh.
* **Robinhood · Agentic** — shown separately (execution capability is labelled only
  if the discovered tools include an order tool, and it stays disabled).
* **Paper Account** — shown separately; balances are never combined.

On a stock page (e.g. search `AAPL`), the **Your Robinhood Position** panel appears
under the decision engine if you hold it — shares, avg cost, market value,
unrealized P&L, portfolio weight, orders, and which account holds it.

---

## Safety checklist (all enforced in code)

- [ ] `ROBINHOOD_TRADING_ENABLED=false` (default) — no order tool can be called.
- [ ] No order button anywhere in the UI.
- [ ] No password stored — OAuth token only, chmod 600, redacted in logs.
- [ ] Account numbers masked (`•••• 1234`).
- [ ] A Robinhood outage never blocks the rest of the terminal.

**Never place a real order while testing.** Order execution is a later phase.
