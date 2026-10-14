# Terminal UI

React + TypeScript + Vite front end for the paper-trading terminal. It is a pure client of the Flask API in
`dashboard/app.py` — it adds no trading logic and cannot place, preview or queue an order.

## Run

From the repo root (starts everything, builds what is stale):

```bash
python scripts/run_terminal.py            # one process, one port: http://127.0.0.1:5057/
python scripts/run_terminal.py --dev      # Flask + Vite hot reload: http://127.0.0.1:5173/ui/
python scripts/run_terminal.py --rebuild  # force a UI rebuild      (also: --no-build, --port N, --open)
```

Or by hand: `npm ci && npm run build` here, then `python dashboard/app.py`. Flask serves `dashboard/ui_dist/`
at `/` (hashed assets cached for a year, `index.html` never cached). If no bundle exists it falls back to the legacy
single-file terminal, so a missing build never takes the dashboard down.

## Layout

| Path | What |
|---|---|
| `src/pages/` | one file per sidebar page: Overview, Stock, Scanner, Markets, Portfolio, Strategies, Testing, System |
| `src/components/` | shell (sidebar, search, drawer), shared blocks, and the stock-analysis panels in `widgets/` |
| `src/lib/` | `api.ts` (cached, de-duplicated fetch), `snapshot.ts` (one snapshot per symbol + live quote), `store.ts` (persisted workspace), `chartEngine.ts` (canvas chart) |
| `DESIGN.md` | the visual spec (flat dark, one accent, no effects) |

Routes are hash-based (`#/stock/PLTR/options`, `#/testing/ch002_slot_ranking/results`), so every page is deep-linkable
and Flask needs no catch-all route.

## Data

Everything comes from `/api/*`. The Strategies and Testing pages read `/api/research/*`
(`dashboard/research_api.py`): read-only, restricted to `experiments/` and `docs/`, covered by
`tests/unit/test_research_api.py`. CI (`.github/workflows/frontend.yml`) type-checks and builds on every change here.

## Checks

```bash
npm run typecheck && npm run build
python -m pytest tests/unit/test_research_api.py -q
```
