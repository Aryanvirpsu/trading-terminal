"""HIST-001 smoke stage: stream the full January 2024 fabhaus shard (pinned revision), filtering to the
10-symbol technology-sector smoke universe on the fly, never holding the full ~15.6GB shard in memory or on
disk -- only the filtered rows (a small fraction of the shard) are persisted. Reuses the HTTP-Range/streaming
approach the FABHAUS_AUDIT_REPORT.md work already established, scaled to a full-month bounded fetch instead
of an audit sample. Per the HIST-001 directive: this does NOT download the whole 478GB corpus -- exactly
one pinned-revision monthly shard, streamed and filtered, is fetched.
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
SHARD = "2024-01.jsonl"
RAW_URL = f"https://huggingface.co/datasets/{DATASET_ID}/resolve/{REVISION}/{SHARD}"
SYMBOLS = {"AAPL", "ADBE", "AMD", "AVGO", "CRM", "CSCO", "DELL", "MSFT", "NVDA", "ORCL"}
SELECTED_COLUMNS = ["symbol", "datetime", "date", "unix_timestamp", "open", "high", "low", "close",
                   "volume", "trade_count"]
OUT_PATH = Path(__file__).resolve().parent / "smoke_2024_01_filtered.jsonl"
MANIFEST_PATH = Path(__file__).resolve().parent / "smoke_2024_01_fetch_manifest.json"
CHUNK_SIZE = 8 * 1024 * 1024   # 8 MB per read -- bounded memory regardless of shard size


def run():
    started = time.time()
    req = urllib.request.Request(RAW_URL, headers={"User-Agent": "avdi-historical-lab-hist001"})
    sha = hashlib.sha256()
    total_bytes = 0
    kept_rows = 0
    seen_rows = 0
    buffer = b""
    symbol_counts = {s: 0 for s in SYMBOLS}

    with urllib.request.urlopen(req, timeout=120) as resp, open(OUT_PATH, "w", encoding="utf-8") as out:
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
            if seen_rows % 2_000_000 < len(complete_lines):
                elapsed = time.time() - started
                print(f"  progress: {total_bytes/1e9:.2f} GB read, {seen_rows:,} rows seen, "
                     f"{kept_rows:,} kept, {elapsed:.0f}s elapsed", file=sys.stderr, flush=True)
        # final partial line (file should end with \n, but handle a truncated last line defensively)
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
        "dataset_id": DATASET_ID, "revision": REVISION, "shard": SHARD, "shard_url": RAW_URL,
        "upstream_content_length": content_length, "upstream_etag": etag,
        "downloaded_bytes": total_bytes, "downloaded_sha256": sha.hexdigest(),
        "rows_seen_total": seen_rows, "rows_kept": kept_rows, "symbol_counts": symbol_counts,
        "selected_columns": SELECTED_COLUMNS, "symbols_requested": sorted(SYMBOLS),
        "fetch_seconds": round(elapsed, 1), "fetch_method": "streamed HTTP GET (Range not needed -- full "
        "shard streamed and filtered on the fly, never held in full in memory or written unfiltered to disk)",
    }
    with open(MANIFEST_PATH, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    print(json.dumps(manifest, indent=1))


if __name__ == "__main__":
    run()
