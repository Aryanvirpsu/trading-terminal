"""AVDI Historical Lab — a thin research harness around the EXISTING AVDI decision stack.

Guiding rule (see CLAUDE.md): Historical Lab exists to accelerate evidence, not to maximize backtest
performance. Any architecture or experiment that makes historical results less causally representative
of the forward Ubuntu runtime is a regression, even if reported P&L increases.

This package NEVER writes to the production paper ledger or `paper/shadow`, and refuses to import or run
on the Ubuntu production host (see `guards.py`). It reuses the real scanner, canonical A/B/C/D/E stack,
Champion/Challenger sizing and risk code; it only supplies its own clock, market-data provider, an
isolated historical account/ledger, and the walk-forward/outcome/event-identity machinery around them.
"""
