# Provenance — Profit Mode baseline v1

- Source: GitHub Actions artifact `paper-ledger-db-23` (id 10824043848), repo Aryanvirpsu/trading-terminal,
  workflow run 36037448562 (head_sha 2473d1bc7319dfcceb4abdab2f3f6e70f015a746), created 2026-09-24T17:54:30Z,
  expires 2026-12-23. Downloaded read-only 2026-09-24T21:20:14Z via `gh api .../artifacts/10824043848/zip`.
- artifact.zip sha256: 7cae33745b21cb83ce077869c83df81c3ae7ab32396db23bdd3efa0f9513bd4f (equals GitHub's reported digest)
- extracted robinhood_500_baseline.db sha256: 19506161d73965e8692e473fefdb421489061accb0f4ab1006e25f1b2c0ae128
  (487,424 bytes; meta.schema_version=2). Extracted copy made read-only; analysis ran on `work/` copy or
  `mode=ro` URI; nothing written back.
- Ledger window: 2026-09-10 .. 2026-09-24, one config_version (cfg-a0eede144e), one engine_version (decision_engine/gates-v1).
- Market bars: yfinance daily, 17 symbols, 2026-09-08..09-24, unadjusted (fetch_bars.py).
- Files here are sufficient to reproduce every number in docs/PROFIT_MODE_BASELINE_v1.md without network access:
  `signals_enriched.json` (ledger signals + derived R fields), `ohlc_daily.csv` (the exact bars used), and the
  scripts. Run from this folder: `python replay.py 5`, `python replay.py 15`, `python challenger.py`,
  `python exits.py`. (To rebuild from scratch: unzip the artifact into ./orig, `python analyze.py`,
  `python fetch_bars.py`.) `replay_15bps.json` is regenerated, not stored.
- Verified 2026-09-24: re-running from this folder reproduced the report's tables exactly.
