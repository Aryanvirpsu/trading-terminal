"""HIST-003 capacity variant: identical parameters to EXP-DD-001's uncapped discovery baseline except
PAPER_MAX_ENTRIES_PER_DAY, set via argv[1] (3 or 4). Baseline (2 entries/day) is NOT re-run -- it is
exp_dd_001_no_drawdown_v2_complete, reused as-is per HIST-003's own Amendment 1."""
import os
import pickle
import sys
import time

entries_per_day = sys.argv[1]
os.environ["PAPER_MAX_ENTRIES_PER_DAY"] = entries_per_day

REPO_ROOT = os.environ["REPO_ROOT"]
sys.path.insert(0, REPO_ROOT)

from research.historical.capability import PRICE_TREND_MACRO_V1
from research.historical.hist001.baseline import run_baseline
from research.historical.hist001.build_full_dataset import DAILY_DATASET_ID, INTRADAY_DATASET_ID
from research.historical.macro import load_macro_history

ALL_SECTORS = ["technology", "communication", "consumer_discretionary", "consumer_staples", "financials",
              "health_care", "industrials", "energy", "materials", "utilities", "real_estate"]

macro_history, _ = load_macro_history("HIST001_FULL_2024_2026_MACRO")
print(f"loaded macro history: {len(macro_history)} observations", file=sys.stderr)
print(f"PAPER_MAX_ENTRIES_PER_DAY={os.environ['PAPER_MAX_ENTRIES_PER_DAY']}", file=sys.stderr)

started = time.time()
result = run_baseline(run_id=f"hist003_entries_{entries_per_day}_per_day", intraday_dataset_id=INTRADAY_DATASET_ID,
                      daily_dataset_id=DAILY_DATASET_ID, universe_sectors=ALL_SECTORS,
                      warmup_start="2024-01-01", evaluation_start="2024-04-01", evaluation_end="2026-03-10",
                      macro_history=macro_history, capability_fingerprint=PRICE_TREND_MACRO_V1,
                      progress_every=1000, disable_drawdown_gate=True)
elapsed = time.time() - started
print(f"run_baseline (HIST-003, {entries_per_day}/day) completed in {elapsed:.1f}s", file=sys.stderr)
print(f"cycles={len(result['cycles'])} event_count={result['event_count']} "
     f"orders={len(result['orders'])} fills={len(result['fills'])}", file=sys.stderr)
print(f"ending_equity={result['account']['equity']}", file=sys.stderr)

OUT_PATH = os.path.join(os.path.dirname(__file__), "results", f"hist003_entries_{entries_per_day}_result.pkl")
os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
with open(OUT_PATH, "wb") as fh:
    pickle.dump(result, fh)
print(f"result pickled to {OUT_PATH}", file=sys.stderr)
print(f"HIST003_{entries_per_day}_DONE_FINAL", file=sys.stderr)
