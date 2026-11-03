# Quote archive (`shadow_quotes`) — telemetry only

One append-only row per `workflow.quote_for()` call made by the discovery cycle and by the trackers, in the **shadow DB**
(`shadow/shadow_candidates.db`, never the ledger). It exists so research (e.g. SR-001) can see the quote stream the Champion
actually saw, instead of only the quotes attached to finalists.

| Column | Meaning |
|---|---|
| `received_at` | UTC time `quote_for` returned (our receipt time) |
| `source_ts` | provider's own timestamp (join key to `shadow_candidates.quote_source_ts` for the decision-time quote) |
| `context` | `discovery` (with `cycle_id`), `tracker_fast` (open positions + pending orders, ~60 s), `tracker_full` (also tracked signals, ~5 min) |
| `ok` | 0 = no quote returned (missing data is recorded, not dropped) |
| `last`, `bid`, `ask`, `provider` | as resolved by `fills.Quote.resolved()` |
| `bid_ask_synthetic` | 1 when bid/ask were **derived from `last`** (`PAPER_SPREAD_BPS` default 10 bps). Today this is always 1: providers supply only `last`. |

**Guarantees:** logging only. `quote_for` still returns exactly what it returned; the row is written after the fact; any failure is
swallowed (`log_quote` returns False). No decision, order, sizing, fill, ledger or config path reads this table. Rollback = revert; the
extra table is harmless. Schema is created by the existing `CREATE TABLE IF NOT EXISTS` script, no migration.

**Volume:** about 3,000 rows per session (3 positions x 1/min + ~27 tracked symbols x 1/5 min + discovery). ~190k rows over 60 sessions.

**Known limits (unchanged by this patch):** candidates are quoted at the 15-minute discovery cadence only while they are finalists, then
every ~5 minutes while their signal is tracked; there are still no real bid/ask quotes; bars are still fetched after the fact; shadow
tracking of bars still stops 25 days after a symbol was last seen; GitHub backup artifacts are kept 90 days.
