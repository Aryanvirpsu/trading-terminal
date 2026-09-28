"""H5 step 14: machine-readable artifacts alongside H5_FORWARD_REPRODUCTION.md -- reference events,
historical events (the replay's own cycle-by-cycle output), the comparison table, the account timeline,
the run manifest and both dataset manifests, and the capability fingerprint. All written under
`research/historical/reports/`, never under a production data directory.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
from pathlib import Path
from typing import Any

from ..capability import price_trend_only_v1
from ..guards import assert_not_production_path
from . import reference as ref
from .compare import build_comparison_matrix, decision_equivalence_summary, mechanical_equivalence_summary
from .replay import run
from .schedule import SCHEDULE

OUT_PATH = Path(__file__).resolve().parents[1] / "reports" / "h5_artifacts.json"


def _default(o: Any):
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return dataclasses.asdict(o)
    if isinstance(o, dt.datetime):
        return o.isoformat()
    return str(o)


def generate(*, run_id: str = "h5_artifacts_run") -> Path:
    assert_not_production_path(OUT_PATH)
    result = run(run_id=run_id, cycles=SCHEDULE)
    rows = build_comparison_matrix(result)

    bundle = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "session": dataclasses.asdict(ref.SESSION),
        "reference_events": [dataclasses.asdict(e) for e in ref.REFERENCE_EVENTS],
        "reference_orders": [dataclasses.asdict(o) for o in ref.REFERENCE_ORDERS],
        "capability_fingerprint": dataclasses.asdict(price_trend_only_v1()),
        "run_manifest": {
            "run_id": result["run_id"], "session_date": result["session_date"],
            "dataset_manifests": result["manifests"],
        },
        "historical_cycles": result["cycles"],
        "account_timeline_final": result["account"],
        "comparison_table": [r.to_dict() for r in rows],
        "mechanical_equivalence": mechanical_equivalence_summary(result),
        "decision_equivalence": decision_equivalence_summary(rows),
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, indent=1, default=_default)
    return OUT_PATH


if __name__ == "__main__":
    p = generate()
    print(f"wrote {p} ({p.stat().st_size} bytes)")
