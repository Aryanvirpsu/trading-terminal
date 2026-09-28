"""H1 — dataset adapter interface. Every source (Hugging Face, a local CSV, Yahoo for bootstrap samples,
later FRED/SEC) implements `fetch()` returning the canonical bar schema; everything downstream (Parquet
storage, DuckDB queries, the manifest, the historical provider) is source-agnostic."""
from __future__ import annotations

import abc
import datetime as dt
from pathlib import Path
from typing import List, Optional, Sequence

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ..guards import guard_all, historical_data_root
from ..manifest import DatasetManifest, file_sha256, save_manifest
from ..schemas.bars import dataframe_sha256, validate_bars


class DatasetAdapter(abc.ABC):
    """One adapter instance = one source. `dataset_id` must be unique per (source, subset) — it is the
    manifest key and the Parquet filename stem."""

    source_name: str = "unknown"

    def __init__(self, dataset_id: str):
        self.dataset_id = dataset_id

    @abc.abstractmethod
    def fetch(self, symbols: Sequence[str], start: str, end: str, timeframe: str) -> pd.DataFrame:
        """Return a DataFrame in the canonical bar schema (schemas.bars). Must NOT be pre-validated —
        `import_and_store` runs the shared validation so every adapter is checked the same way."""

    def import_and_store(self, symbols: Sequence[str], start: str, end: str, timeframe: str,
                         *, notes: Optional[str] = None) -> DatasetManifest:
        """Fetch -> validate -> write Parquet -> write the manifest. This is the ONE path every adapter's
        output goes through, so every dataset in the lab is validated and hashed identically. Extended
        provenance (hf_repository/hf_revision/upstream_files/upstream_sha256/selected_columns/
        adapter_version) is read from instance attributes an adapter subclass may set — every attribute
        has a safe empty default, so plain adapters (e.g. the fixture/Yahoo ones) are unaffected."""
        df = self.fetch(symbols, start, end, timeframe)
        report = validate_bars(df)                    # raises BarValidationError on any failure
        content_hash = dataframe_sha256(df)

        root = historical_data_root()
        rel_path = Path("equities") / f"{self.dataset_id}.parquet"
        out_path = root / rel_path
        guard_all(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pandas(df, preserve_index=False)
        pq.write_table(table, out_path)

        manifest = DatasetManifest(
            dataset_id=self.dataset_id, source=self.source_name, revision=getattr(self, "revision", "n/a"),
            symbols=sorted(df["symbol"].unique().tolist()), start=start, end=end, timeframe=timeframe,
            rows=len(df), sha256=content_hash, parquet_path=str(rel_path), parquet_sha256=file_sha256(out_path),
            validation=report.to_dict(), created_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            notes=notes, hf_repository=getattr(self, "hf_dataset_id", None),
            hf_revision=(getattr(self, "revision", None) if getattr(self, "hf_dataset_id", None) else None),
            upstream_files=list(getattr(self, "upstream_files", []) or []),
            upstream_sha256=dict(getattr(self, "upstream_sha256", {}) or {}),
            selected_columns=list(getattr(self, "selected_columns", []) or []),
            adapter_version=getattr(self, "adapter_version", None))
        save_manifest(manifest)
        return manifest


def load_parquet(dataset_id: str) -> pd.DataFrame:
    """Read back a stored dataset by id (used by the DuckDB-facing helpers and the historical provider)."""
    from ..manifest import load_manifest
    m = load_manifest(dataset_id)
    root = historical_data_root()
    p = root / m.parquet_path
    guard_all(p)
    return pd.read_parquet(p)


def duckdb_view(dataset_ids: List[str]):
    """A DuckDB connection with one view per dataset id, queryable as SQL (H1 acceptance: "store in
    Parquet and query with DuckDB"). Read-only, in-memory connection; never touches production paths."""
    import duckdb
    from ..manifest import load_manifest

    con = duckdb.connect(database=":memory:")
    root = historical_data_root()
    for did in dataset_ids:
        m = load_manifest(did)
        p = root / m.parquet_path
        guard_all(p)
        con.execute(f"CREATE VIEW \"{did}\" AS SELECT * FROM read_parquet('{p.as_posix()}')")
    return con
