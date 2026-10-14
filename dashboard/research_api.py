"""Read-only research endpoints: experiments, the strategy watchlist and project docs.

Serves the files under ``experiments/`` and ``docs/`` to the dashboard so the Strategies and Testing
pages can show pre-registrations, verdicts and raw results. Nothing here writes, executes, or touches
the paper pipeline, ``lab/`` or any broker setting. Paths are whitelisted (a fixed root, a strict
name pattern, an extension allow-list and a size cap), so a request cannot read outside those two
folders.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Blueprint, abort, jsonify

bp = Blueprint("research", __name__)

ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS = ROOT / "experiments"
DOCS = ROOT / "docs"

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")
_TEXT_EXT = {".md", ".txt", ".patch"}
_MAX_TEXT = 600_000
_MAX_JSON = 900_000


def _safe_dir(base: Path, name: str) -> Path:
    if not _NAME.match(name):
        abort(404)
    p = (base / name).resolve()
    if base.resolve() not in p.parents or not p.is_dir():
        abort(404)
    return p


def _read_text(p: Path) -> Optional[str]:
    try:
        if p.stat().st_size > _MAX_TEXT:
            return None
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _title(md: Optional[str], fallback: str) -> str:
    if md:
        for line in md.splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    return fallback


def _verdict(md: Optional[str]) -> Optional[str]:
    if not md:
        return None
    for line in md.splitlines():
        if re.match(r"^#{1,3}\s*Verdict", line, re.I):
            return re.sub(r"^#{1,3}\s*Verdict:?\s*", "", line, flags=re.I).strip() or None
    return None


def _status_line(md: Optional[str]) -> Optional[str]:
    if not md:
        return None
    m = re.search(r"Status:\s*\*{0,2}([A-Za-z\- ]+?)\*{0,2}(?:[\.\s(]|$)", md)
    return m.group(1).strip().lower() if m else None


def _mtime(p: Path) -> str:
    return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()


def _kind(folder: str) -> Dict[str, str]:
    m = re.match(r"^ch(\d+)_", folder)
    if m:
        return {"kind": "challenger", "id": "CH-" + m.group(1).zfill(3)}
    if folder.startswith("profit_mode_baseline"):
        return {"kind": "baseline", "id": "BASELINE"}
    if folder.startswith("profit_review"):
        return {"kind": "review", "id": "REVIEW"}
    if folder.startswith("diagnosis"):
        return {"kind": "diagnosis", "id": "DIAGNOSIS"}
    if folder == "strategy_watchlist":
        return {"kind": "watchlist", "id": "WATCHLIST"}
    return {"kind": "other", "id": folder}


def _summarise(d: Path) -> Dict[str, Any]:
    prereg = _read_text(d / "PREREG.md")
    results = _read_text(d / "RESULTS.md")
    readme = _read_text(d / "README.md")
    prov = _read_text(d / "PROVENANCE.md")
    diag = _read_text(d / "DIAGNOSIS.md")
    lead = results or prereg or diag or readme or prov
    k = _kind(d.name)
    status = "complete" if results else ("pre-registered" if prereg else "notes")
    return {
        **k, "folder": d.name, "title": _title(lead, d.name),
        "status": status, "prereg_status": _status_line(prereg),
        "verdict": _verdict(results), "has_prereg": prereg is not None, "has_results": results is not None,
        "updated": max((_mtime(f) for f in d.iterdir() if f.is_file()), default=None),
        "files": sorted(f.name for f in d.iterdir() if f.is_file()),
    }


@bp.route("/api/research/index")
def research_index():
    exps: List[Dict[str, Any]] = []
    if EXPERIMENTS.is_dir():
        exps = [_summarise(d) for d in sorted(EXPERIMENTS.iterdir()) if d.is_dir() and _NAME.match(d.name) and not d.name.startswith("__")]
    docs: List[Dict[str, Any]] = []
    if DOCS.is_dir():
        for f in sorted(DOCS.glob("*.md")):
            md = _read_text(f)
            docs.append({"name": f.stem, "title": _title(md, f.stem), "updated": _mtime(f)})
    return jsonify({"experiments": exps, "docs": docs})


@bp.route("/api/research/experiment/<folder>")
def research_experiment(folder: str):
    d = _safe_dir(EXPERIMENTS, folder)
    out: Dict[str, Any] = {**_summarise(d), "documents": {}, "json": {}, "skipped": []}
    for f in sorted(d.iterdir()):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext in _TEXT_EXT:
            t = _read_text(f)
            if t is None:
                out["skipped"].append({"name": f.name, "reason": "too large"})
            else:
                out["documents"][f.name] = t
        elif ext == ".json":
            try:
                if f.stat().st_size > _MAX_JSON:
                    out["skipped"].append({"name": f.name, "reason": "too large"})
                else:
                    out["json"][f.name] = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                out["skipped"].append({"name": f.name, "reason": "unreadable"})
        else:
            out["skipped"].append({"name": f.name, "reason": f"{ext or 'file'} not shown"})
    return jsonify(out)


@bp.route("/api/research/watchlist")
def research_watchlist():
    d = EXPERIMENTS / "strategy_watchlist"
    if not d.is_dir():
        return jsonify({"state": "unavailable", "reason": "no strategy_watchlist folder"})
    try:
        registry = json.loads((d / "registry.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        registry = None
    return jsonify({"state": "ok", "registry": registry, "readme": _read_text(d / "README.md"), "monitoring": _read_text(d / "MONITORING.md")})


@bp.route("/api/research/doc/<name>")
def research_doc(name: str):
    if not _NAME.match(name):
        abort(404)
    f = (DOCS / (name + ".md")).resolve()
    if DOCS.resolve() not in f.parents or not f.is_file():
        abort(404)
    t = _read_text(f)
    return jsonify({"name": name, "title": _title(t, name), "markdown": t, "updated": _mtime(f)})
