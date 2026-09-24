"""Fetch the daily OHLC bars used by replay.py (public yfinance data; unadjusted)."""
import json, yfinance as yf
S = json.load(open('signals_enriched.json'))
syms = sorted({s['symbol'] for s in S})
df = yf.download(syms, start='2026-09-08', end='2026-09-26', interval='1d',
                 auto_adjust=False, group_by='ticker', progress=False)
df.to_csv('ohlc_daily.csv')
