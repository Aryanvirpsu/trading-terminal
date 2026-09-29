"""HIST-001 Medium stage: stream each of the six 2024 H1 fabhaus monthly shards (pinned revision),
filtering to the full 90-symbol `dashboard/sector_map.py` universe on the fly -- never holding a full shard
in memory or on disk, only the filtered rows are persisted. Generalizes `fetch_smoke_shard.py`'s
single-month/10-symbol approach to six months/90 symbols.

Directive sec 8 (resource-safe execution): each month is its own independent fetch, its own filtered JSONL
file, and its own fetch manifest -- a deterministic partition by calendar month, the same partitioning
fabhaus's own shard layout already uses. No shard's content depends on another's; `build_medium_dataset.py`
combines them afterward in a way proven invariant to the order they're combined in (see
`tests/historical/test_hist001_medium_batching.py`).
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

DATASET_ID = "fabhaus/equities_5m_stockprices"
REVISION = "f17c0b0c3cf6a455994f93d6a85e76274172ab03"
SHARDS = ["2024-01.jsonl", "2024-02.jsonl", "2024-03.jsonl", "2024-04.jsonl", "2024-05.jsonl", "2024-06.jsonl"]

# The full dashboard/sector_map.py universe (90 symbols, 11 sectors) at the HIST-001 pre-registration
# commit -- deterministic, not cherry-picked: every symbol in every sector's industry groups, no subset
# selection judgment call made for Medium at all.
SYMBOLS = {
    "NVDA", "AVGO", "AMD", "MSFT", "ORCL", "CRM", "ADBE", "AAPL", "CSCO", "DELL",           # technology
    "GOOGL", "META", "NFLX", "DIS", "T", "VZ", "TMUS",                                     # communication
    "AMZN", "HD", "LOW", "TSLA", "GM", "F", "MCD", "SBUX", "CMG",                          # consumer_discretionary
    "KO", "PEP", "MDLZ", "PG", "CL", "KMB", "WMT", "COST",                                  # consumer_staples
    "JPM", "BAC", "WFC", "V", "MA", "AXP", "BRK-B", "PGR", "CB",                            # financials
    "LLY", "JNJ", "MRK", "ABBV", "AMGN", "GILD", "VRTX", "UNH", "ABT", "TMO",               # health_care
    "BA", "RTX", "LMT", "CAT", "DE", "HON", "UBER", "UNP", "UPS",                           # industrials
    "XOM", "CVX", "COP", "EOG", "OXY", "SLB", "HAL",                                        # energy
    "LIN", "SHW", "APD", "FCX", "NEM", "VMC", "MLM",                                        # materials
    "NEE", "SO", "DUK", "CEG", "AEP", "EXC",                                                # utilities
    "AMT", "EQIX", "CCI", "PLD", "SPG", "O", "WELL", "AVB",                                 # real_estate
}
assert len(SYMBOLS) == 90, len(SYMBOLS)

SELECTED_COLUMNS = ["symbol", "datetime", "date", "unix_timestamp", "open", "high", "low", "close",
                   "volume", "trade_count"]
HERE = Path(__file__).resolve().parent
CHUNK_SIZE = 8 * 1024 * 1024   # 8 MB per read -- bounded memory regardless of shard size


def _out_paths(shard: str):
    month = shard.replace(".jsonl", "")
    return (HERE / f"medium_{month}_filtered.jsonl", HERE / f"medium_{month}_fetch_manifest.json")


def _fetch_one_shard(shard: str) -> dict:
    out_path, manifest_path = _out_paths(shard)
    url = f"https://huggingface.co/datasets/{DATASET_ID}/resolve/{REVISION}/{shard}"
    started = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "avdi-historical-lab-hist001"})
    sha = hashlib.sha256()
    total_bytes = 0
    kept_rows = 0
    seen_rows = 0
    buffer = b""
    symbol_counts = {s: 0 for s in SYMBOLS}

    with urllib.request.urlopen(req, timeout=120) as resp, open(out_path, "w", encoding="utf-8") as out:
        content_length = resp.headers.get("Content-Length")
        etag = resp.headers.get("ETag")
        while True:
            chunk = resp.read(CHUNK_SIZE)
            if not chunk:
                break
            sha.update(chunk)
            total_bytes += len(chunk)
            buffer += chunk
            *complete_lines, buffer = buffer.split(b"\n")
            for raw_line in complete_lines:
                if not raw_line.strip():
                    continue
                seen_rows += 1
                try:
                    row = json.loads(raw_line)
                except Exception:
                    continue
                sym = row.get("symbol")
                if sym in SYMBOLS:
                    filtered = {k: row.get(k) for k in SELECTED_COLUMNS}
                    out.write(json.dumps(filtered, sort_keys=True) + "\n")
                    kept_rows += 1
                    symbol_counts[sym] += 1
            if seen_rows % 4_000_000 < len(complete_lines):
                elapsed = time.time() - started
                print(f"  [{shard}] progress: {total_bytes/1e9:.2f} GB read, {seen_rows:,} rows seen, "
                     f"{kept_rows:,} kept, {elapsed:.0f}s elapsed", file=sys.stderr, flush=True)
        if buffer.strip():
            try:
                row = json.loads(buffer)
                if row.get("symbol") in SYMBOLS:
                    filtered = {k: row.get(k) for k in SELECTED_COLUMNS}
                    out.write(json.dumps(filtered, sort_keys=True) + "\n")
                    kept_rows += 1
                    symbol_counts[row["symbol"]] += 1
            except Exception:
                pass

    elapsed = time.time() - started
    manifest = {
        "dataset_id": DATASET_ID, "revision": REVISION, "shard": shard, "shard_url": url,
        "upstream_content_length": content_length, "upstream_etag": etag,
        "downloaded_bytes": total_bytes, "downloaded_sha256": sha.hexdigest(),
        "rows_seen_total": seen_rows, "rows_kept": kept_rows, "symbol_counts": symbol_counts,
        "selected_columns": SELECTED_COLUMNS, "symbols_requested": sorted(SYMBOLS),
        "fetch_seconds": round(elapsed, 1), "fetch_method": "streamed HTTP GET, filtered on the fly, "
        "never held in full in memory or written unfiltered to disk",
    }
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    print(f"[{shard}] done: {json.dumps({k: v for k, v in manifest.items() if k != 'symbol_counts'})}",
         file=sys.stderr, flush=True)
    return manifest


def run():
    results = []
    for shard in SHARDS:
        out_path, manifest_path = _out_paths(shard)
        if out_path.exists() and manifest_path.exists():
            print(f"[{shard}] already fetched, skipping", file=sys.stderr, flush=True)
            with open(manifest_path, encoding="utf-8") as fh:
                results.append(json.load(fh))
            continue
        results.append(_fetch_one_shard(shard))
    zero_rows = [m["shard"] for m in results if m["rows_kept"] == 0]
    if zero_rows:
        print(f"WARNING: shards with zero kept rows: {zero_rows}", file=sys.stderr)
    print(json.dumps({"shards_fetched": len(results),
                      "total_kept_rows": sum(m["rows_kept"] for m in results)}, indent=1))


if __name__ == "__main__":
    run()
