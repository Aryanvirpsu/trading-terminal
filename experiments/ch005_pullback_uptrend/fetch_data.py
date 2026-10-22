"""CH-005 data fetch: daily bars for the AVDI sector-map universe + SPY, from Yahoo's public chart API (read-only HTTP GET).

    python -I experiments/ch005_pullback_uptrend/fetch_data.py

Writes (next to this script, under data/):
  universe.json        the frozen symbol list with AVDI sector assignments (taken from dashboard/sector_map.py, nothing hand-picked)
  prices_daily.json.gz canonical daily bars {symbol: {"d":[YYYY-MM-DD..], "o","h","l","c","v"}}  (deterministic serialization)
  data_manifest.json   sha256 of both files, row counts, fetch time, source, and the data-audit results

Price convention (frozen in PREREG.md): o/h/l/c are Yahoo's UNADJUSTED-FOR-DIVIDENDS but SPLIT-ADJUSTED values (`indicators.quote`,
not `adjclose`), because fills must occur at tradable prices; volume is split-adjusted by Yahoo, so close*volume is the true dollar
volume.  Dividends are therefore NOT credited to long positions (a small, conservative omission over <=15-session holds).
The session date is the UTC date of Yahoo's bar timestamp (09:30 ET = 13:30/14:30 UTC, the same calendar date as the ET session).
No strategy outcome is computed here.
"""
import datetime as dt, gzip, hashlib, importlib.util, json, sys, time, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
ROOT = HERE.parent.parent
START, END = "2014-01-01", "2026-10-22"          # 2014 lead-in so the first test-bed day has 200 prior sessions
UA = {"User-Agent": "Mozilla/5.0"}


def load_universe():
    spec = importlib.util.spec_from_file_location("sector_map", ROOT / "dashboard" / "sector_map.py")
    sm = importlib.util.module_from_spec(spec)
    sys.path[:0] = [str(ROOT / "dashboard"), str(ROOT / "src"), str(ROOT / "lab")]
    spec.loader.exec_module(sm)
    uni = {}
    for sector, meta in sm.SECTORS.items():
        for grp in meta.get("industries", {}).values():
            for t in grp:
                uni.setdefault(t, sector)              # a ticker listed under two sectors keeps its first sector (deterministic dict order)
    return dict(sorted(uni.items()))


def fetch(sym):
    p1 = int(dt.datetime.fromisoformat(START).replace(tzinfo=dt.timezone.utc).timestamp())
    p2 = int(dt.datetime.fromisoformat(END).replace(tzinfo=dt.timezone.utc).timestamp())
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={p1}&period2={p2}&interval=1d&events=div%7Csplit"
    for attempt in range(4):
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))["chart"]["result"][0]
            break
        except Exception as e:                           # noqa: BLE001
            if attempt == 3:
                return None, f"{type(e).__name__}: {str(e)[:80]}"
            time.sleep(2 * (attempt + 1))
    q = r["indicators"]["quote"][0]
    rows = []
    for ts, o, h, l, c, v in zip(r["timestamp"], q["open"], q["high"], q["low"], q["close"], q["volume"]):
        if None in (o, h, l, c) or v is None:
            continue                                      # Yahoo emits null rows on some halts/holidays; recorded in the audit
        rows.append((dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d"), round(o, 4), round(h, 4), round(l, 4), round(c, 4), int(v)))
    ev = r.get("events", {})
    splits = sorted((dt.datetime.fromtimestamp(int(k), dt.timezone.utc).strftime("%Y-%m-%d"), v.get("numerator"), v.get("denominator")) for k, v in (ev.get("splits") or {}).items())
    return {"rows": rows, "splits": splits, "tz": r["meta"].get("exchangeTimezoneName"), "n_null": len(r["timestamp"]) - len(rows)}, None


def main():
    DATA.mkdir(exist_ok=True)
    uni = load_universe()
    symbols = list(uni) + ["SPY"]
    bars, errors, info = {}, {}, {}
    for i, s in enumerate(symbols):
        d, err = fetch(s)
        if err:
            errors[s] = err
            continue
        rows = d["rows"]
        bars[s] = {"d": [r[0] for r in rows], "o": [r[1] for r in rows], "h": [r[2] for r in rows], "l": [r[3] for r in rows],
                   "c": [r[4] for r in rows], "v": [r[5] for r in rows]}
        info[s] = {"n": len(rows), "first": rows[0][0], "last": rows[-1][0], "splits": d["splits"], "tz": d["tz"], "null_rows": d["null_rows"] if "null_rows" in d else d["n_null"]}
        time.sleep(0.25)
    (DATA / "universe.json").write_text(json.dumps({"source": "dashboard/sector_map.py SECTORS (AVDI sector map)", "symbols": uni, "benchmark": "SPY"}, indent=1, sort_keys=True) + "\n", newline="\n")
    blob = json.dumps(bars, sort_keys=True, separators=(",", ":")).encode()
    with gzip.GzipFile(DATA / "prices_daily.json.gz", "wb", mtime=0) as fh:          # mtime=0 -> byte-identical gzip for identical content
        fh.write(blob)
    sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
    man = {"source": "Yahoo Finance chart API v8, interval=1d, split-adjusted OHLCV, NOT dividend-adjusted", "requested_window": [START, END],
           "fetched_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "sha256": {"universe.json": sha(DATA / "universe.json"), "prices_daily.json.gz": sha(DATA / "prices_daily.json.gz")},
           "symbols_requested": len(symbols), "symbols_fetched": len(bars), "fetch_errors": errors, "per_symbol": info}
    (DATA / "data_manifest.json").write_text(json.dumps(man, indent=1, sort_keys=True) + "\n", newline="\n")
    print(f"fetched {len(bars)}/{len(symbols)} symbols; errors: {errors}")
    print("sha256", man["sha256"])


if __name__ == "__main__":
    main()
