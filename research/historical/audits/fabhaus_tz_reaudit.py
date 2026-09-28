"""Timezone re-audit of fabhaus/equities_5m_stockprices: prove the 'datetime is actually America/New_York
wall-clock, not UTC' finding holds across winter (EST), summer (EDT), and both DST transition weeks, for
both an ETF and an equity. Never downloads a full shard; uses HTTP Range + bisection to locate a target
calendar date's byte offset in a time-sorted shard, then extracts a small window there.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from research.historical.guards import guard_all  # noqa: E402

DATASET_ID = "fabhaus/equities_5m_stockprices"
REVISION = "f17c0b0c3cf6a455994f93d6a85e76274172ab03"
RAW_ROOT = f"https://huggingface.co/datasets/{DATASET_ID}/resolve/{REVISION}"
TARGET_SYMBOLS = ("AAPL", "SPY")     # one equity, one ETF
OUT_DIR = Path(__file__).resolve().parent / "fabhaus_tz_sample"

SHARD_SIZES = {
    "2024-03.jsonl": 15350696494, "2024-07.jsonl": 16693097575, "2024-11.jsonl": 16526026540,
}


def _fetch_range(shard: str, start: int, length: int) -> bytes:
    url = f"{RAW_ROOT}/{shard}"
    end = start + length - 1
    req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}",
                                               "User-Agent": "avdi-historical-lab-tz-audit"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def _first_complete_date(raw: bytes) -> Optional[str]:
    for line in raw.decode("utf-8", errors="ignore").split("\n")[1:-1]:
        if not line.strip():
            continue
        try:
            return json.loads(line)["date"]
        except Exception:
            continue
    return None


def _locate_offset_for_date(shard: str, target_date: str, probe_len: int = 300_000) -> int:
    """Bisect the shard (which is sorted chronologically) for the byte offset whose date is >= target."""
    size = SHARD_SIZES[shard]
    lo, hi = 0, size - probe_len
    target = dt.date.fromisoformat(target_date)
    for _ in range(22):
        mid = (lo + hi) // 2
        raw = _fetch_range(shard, mid, probe_len)
        d = _first_complete_date(raw)
        if d is None:
            hi = mid
            continue
        d_date = dt.date.fromisoformat(d)
        print(f"  probe @ {mid:>13,} -> date {d}")
        if d_date < target:
            lo = mid
        else:
            hi = mid
        if hi - lo < probe_len:
            break
    return lo


def sample_window(shard: str, offset: int, length: int, label: str) -> dict:
    raw = _fetch_range(shard, offset, length)
    rows = []
    for line in raw.decode("utf-8", errors="ignore").split("\n")[1:-1]:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if row.get("symbol") in TARGET_SYMBOLS:
            rows.append(row)
    print(f"[{label}] shard={shard} offset={offset:,} len={length:,} -> {len(rows)} target rows, "
          f"dates={sorted({r['date'] for r in rows})}")
    return {"label": label, "shard": shard, "offset": offset, "length": length, "rows": rows}


def run():
    guard_all(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    samples = {}

    print("== 2024-01-02 (winter/EST) already audited in FABHAUS_AUDIT_REPORT.md — re-fetch small window")
    samples["winter_2024_01_02"] = sample_window("2024-01.jsonl", 0, 40_000_000, "winter EST (2024-01-02)")

    print("\n== 2024-07-01 (summer/EDT) — file start, no bisection needed")
    samples["summer_2024_07_01"] = sample_window("2024-07.jsonl", 0, 40_000_000, "summer EDT (2024-07-01)")

    print("\n== spring-forward transition (2024-03-10) — bisecting 2024-03.jsonl")
    off = _locate_offset_for_date("2024-03.jsonl", "2024-03-07")     # a couple of days before, for margin
    samples["spring_forward"] = sample_window("2024-03.jsonl", off, 60_000_000, "spring-forward week (around 2024-03-10)")

    print("\n== fall-back transition (2024-11-03) — bisecting 2024-11.jsonl")
    off = _locate_offset_for_date("2024-11.jsonl", "2024-10-31")
    samples["fall_back"] = sample_window("2024-11.jsonl", off, 60_000_000, "fall-back week (around 2024-11-03)")

    for name, s in samples.items():
        with open(OUT_DIR / f"{name}.jsonl", "w", encoding="utf-8") as fh:
            for r in s["rows"]:
                fh.write(json.dumps(r, sort_keys=True) + "\n")
    return samples


if __name__ == "__main__":
    run()
