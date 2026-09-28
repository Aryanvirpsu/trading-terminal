"""H1 — dataset provenance manifest. Every imported dataset (subset) gets one of these next to its
Parquet file, in `data/historical/manifests/`. Manifests are small JSON and ARE committed to git (unlike
the Parquet data itself); they are the durable record of what data an experiment actually ran on."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .guards import guard_all, historical_data_root


@dataclasses.dataclass
class DatasetManifest:
    dataset_id: str
    source: str                      # e.g. "yahoo-bootstrap", "huggingface:<hf dataset id>@<revision>"
    revision: str
    symbols: List[str]
    start: str
    end: str
    timeframe: str
    rows: int
    sha256: str                      # content hash of the canonical-schema dataframe (schemas.bars.dataframe_sha256)
    parquet_path: str                # relative to data/historical/
    parquet_sha256: str              # hash of the actual file on disk, for tamper/corruption detection
    validation: Dict[str, Any]
    created_at: str
    notes: Optional[str] = None
    # Extended provenance (H4 dataset-audit requirement): an experiment must never point merely to
    # "latest". Populated for Hugging Face imports; left at their defaults ([] / {} / None) for sources
    # that don't apply (e.g. yahoo-bootstrap).
    hf_repository: Optional[str] = None              # e.g. "fabhaus/equities_5m_stockprices"
    hf_revision: Optional[str] = None                # the exact pinned commit SHA, never a branch name
    upstream_files: List[str] = dataclasses.field(default_factory=list)      # shard/file(s) actually read
    upstream_sha256: Dict[str, str] = dataclasses.field(default_factory=dict)  # per-file upstream hash (ETag), where available
    selected_columns: List[str] = dataclasses.field(default_factory=list)    # raw fields actually ingested
    adapter_version: Optional[str] = None            # ties a manifest to the exact ingestion logic that produced it
    # H4 dataset-audit requirement: which slice of the forward Champion this dataset can actually replay
    # (see research/historical/capability.py). None only for a raw dataset import that hasn't yet been
    # run through an AVDI replay -- never populate this speculatively.
    capability_fingerprint: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


def file_sha256(path: "os.PathLike[str]") -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest_path(dataset_id: str) -> Path:
    root = historical_data_root()
    return root / "manifests" / f"{dataset_id}.json"


def save_manifest(m: DatasetManifest) -> Path:
    p = manifest_path(m.dataset_id)
    guard_all(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(m.to_dict(), fh, indent=1, default=str)
    return p


def load_manifest(dataset_id: str) -> DatasetManifest:
    p = manifest_path(dataset_id)
    guard_all(p)
    with open(p, encoding="utf-8") as fh:
        d = json.load(fh)
    return DatasetManifest(**d)


def verify_manifest(dataset_id: str) -> Dict[str, Any]:
    """Re-hash the Parquet file on disk against the manifest — catches silent corruption or a stale file."""
    m = load_manifest(dataset_id)
    root = historical_data_root()
    p = root / m.parquet_path
    guard_all(p)
    problems = []
    if not p.exists():
        problems.append(f"parquet file missing: {p}")
    else:
        actual = file_sha256(p)
        if actual != m.parquet_sha256:
            problems.append(f"parquet sha256 mismatch: manifest={m.parquet_sha256} actual={actual}")
    return {"dataset_id": dataset_id, "ok": not problems, "problems": problems}
