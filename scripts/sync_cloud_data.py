#!/usr/bin/env python3
"""Mirror the cloud paper ledger onto this machine — READ-ONLY, never merged with the local ledger.

    python scripts/sync_cloud_data.py              # pull + verify the newest off-host backup (skips if already mirrored)
    python scripts/sync_cloud_data.py --status     # what is mirrored, how old
    python scripts/sync_cloud_data.py --dry-run    # show what WOULD be downloaded (name, size, time); download nothing
    python scripts/sync_cloud_data.py --refresh    # ask the cloud to prepare a NEW backup first (dispatches ubuntu-ops.yml), then pull
    python scripts/sync_cloud_data.py --from-file backup.tgz    # install a backup you already downloaded

Where the data comes from: the cloud runtime (Ubuntu host) takes a host-verified, consistent SQLite backup at the
close of every session; the restricted `ops` role pulls it into a GitHub Actions artifact named
`avdi-ubuntu-backup-<n>` (workflow `ubuntu-ops.yml`). This script downloads that artifact with the GitHub CLI
(`gh`, already signed in), re-verifies it (sha256 of every file + SQLite integrity_check) and installs it under

    ~/.avdi_cloud_mirror/<backup-name>/        pristine, read-only copy
    ~/.avdi_cloud_mirror/<backup-name>/work/   throw-away ledger copy the viewer opens
    ~/.avdi_cloud_mirror/current.json          pointer to the newest verified mirror

It NEVER writes to ~/.tradingview_mcp_data/paper (the local Case 2 ledger): the cloud (Case 1) and local (Case 2)
ledgers are deliberately separate evidence tracks and are not reconciled. No credentials are read or stored here;
`gh` supplies its own sign-in.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

WORKFLOW = "ubuntu-ops.yml"
ARTIFACT_PREFIX = "avdi-ubuntu-backup-"
DATA_HOME = Path(os.environ.get("TVMCP_DATA_DIR", "~/.tradingview_mcp_data")).expanduser()
# Deliberately NOT inside ~/.tradingview_mcp_data: that folder is the local ledger's evidence store (and the test suite fails
# loudly if anything in it changes). The cloud mirror is a separate, disposable cache.
MIRROR = Path(os.environ.get("AVDI_MIRROR_DIR", "~/.avdi_cloud_mirror")).expanduser()
LOCAL_LEDGER_DIR = (DATA_HOME / "paper").resolve()
KEEP = 3

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def say(msg: str = "") -> None:
    print(msg, flush=True)


def die(msg: str, code: int = 1) -> None:
    print(f"error: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


# ── safety invariant: nothing here may ever touch the local ledger ─────────────────────────────────────────────
def _assert_isolated(p: Path) -> None:
    rp = p.resolve()
    if rp == LOCAL_LEDGER_DIR or LOCAL_LEDGER_DIR in rp.parents:
        die(f"refusing to write inside the local ledger directory ({LOCAL_LEDGER_DIR}) — the cloud mirror must stay separate", 2)
    if MIRROR.resolve() not in rp.parents and rp != MIRROR.resolve():
        die(f"refusing to write outside the mirror directory ({MIRROR})", 2)


# ── GitHub CLI ────────────────────────────────────────────────────────────────────────────────────────────────
def gh(*args: str, check: bool = True, capture: bool = True) -> str:
    exe = shutil.which("gh")
    if not exe:
        die("GitHub CLI (gh) not found. Install it and run `gh auth login`, or download the backup artifact yourself and use --from-file.")
    r = subprocess.run([exe, *args], capture_output=capture, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        die(f"`gh {' '.join(args[:3])} …` failed: {(r.stderr or r.stdout).strip()[:400]}")
    return r.stdout if capture else ""


def repo_name() -> str:
    """owner/repo — from AVDI_REPO, else the git remote (offline; needs no GitHub sign-in), else `gh repo view`."""
    if os.environ.get("AVDI_REPO"):
        return os.environ["AVDI_REPO"]
    try:
        url = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent.parent), "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        m = re.search(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$", url)
        if m:
            return f"{m.group(1)}/{m.group(2)}"
    except (OSError, subprocess.SubprocessError):
        pass
    return gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner").strip()


def require_gh_auth() -> None:
    """Fail early and helpfully: `gh` keeps its sign-in per Windows user / config dir, so another terminal may not have it."""
    exe = shutil.which("gh")
    if not exe:
        die("GitHub CLI (gh) not found. Install it from https://cli.github.com, or download the backup artifact yourself and use --from-file.")
    if os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"):
        return
    r = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        die("""GitHub CLI is not signed in in this terminal. Do ONE of:
    gh auth login                      (browser sign-in; choose github.com, then HTTPS)
    $env:GH_TOKEN = '<token>'          (PowerShell; a token that can read this repo's Actions artifacts)
    python scripts/sync_cloud_data.py --from-file backup.tgz   (after downloading the artifact from the Actions page)
  Signed in somewhere else? Run `gh auth status` there and compare: the sign-in is stored per user / config folder (GH_CONFIG_DIR).""")



def list_artifacts(repo: str) -> list[dict]:
    raw = gh("api", f"repos/{repo}/actions/artifacts?per_page=50", "--jq",
             f'[.artifacts[] | select(.name | startswith("{ARTIFACT_PREFIX}")) | select(.expired | not) | '
             '{id, name, size: .size_in_bytes, created_at, run_id: .workflow_run.id, branch: .workflow_run.head_branch}]')
    arts = json.loads(raw or "[]")
    return sorted(arts, key=lambda a: a["created_at"], reverse=True)


# ── verification ──────────────────────────────────────────────────────────────────────────────────────────────
def _safe_extract(tgz: Path, dest: Path) -> Path:
    """Extract only regular files/dirs that stay inside `dest` (the archive comes from a remote artifact)."""
    root = dest.resolve()
    with tarfile.open(tgz, "r:*") as tf:
        for m in tf.getmembers():
            target = (dest / m.name).resolve()
            if not (target == root or root in target.parents):
                die(f"unsafe path in archive: {m.name!r}")
            if not (m.isfile() or m.isdir()):
                die(f"unexpected entry type in archive: {m.name!r}")
        tf.extractall(dest)  # noqa: S202 — members validated above
    tops = [p for p in dest.iterdir() if p.is_dir()]
    if len(tops) != 1:
        die("archive should contain exactly one backup folder")
    return tops[0]


def verify_backup(d: Path) -> dict:
    """sha256 of every listed file + SQLite integrity_check, mirroring the off-host check in ubuntu-ops.yml."""
    mf = d / "MANIFEST.json"
    if not mf.exists():
        die("MANIFEST.json missing from the backup")
    m = json.loads(mf.read_text(encoding="utf-8"))
    if not m.get("ok"):
        die("the host-side manifest says this backup is NOT ok")
    files = m.get("files") or {}
    if not files:
        die("manifest lists no files")
    for name, meta in files.items():
        p = d / name
        if not p.is_file():
            die(f"manifest lists {name} but it is missing")
        if hashlib.sha256(p.read_bytes()).hexdigest() != meta["sha256"]:
            die(f"sha256 mismatch for {name}")
        if name.endswith(".db"):
            con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro&immutable=1", uri=True)
            try:
                if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    die(f"SQLite integrity_check failed for {name}")
            finally:
                con.close()
    ledgers = [n for n in files if n.endswith(".db") and not n.startswith("shadow__")]
    shadows = [n for n in files if n.endswith(".db") and n.startswith("shadow__")]
    if len(ledgers) != 1:
        die(f"expected exactly one ledger database, found {ledgers}")
    return {"manifest": m, "ledger": ledgers[0], "shadow": shadows[0] if shadows else None,
            "files": {n: {"sha256": v["sha256"], "bytes": (d / n).stat().st_size} for n, v in files.items()}}


# ── install ───────────────────────────────────────────────────────────────────────────────────────────────────
def _from_folder(name: str) -> str | None:
    """Backup folders are named like 20261012T202501Z (UTC) — a fallback when the manifest carries no timestamp."""
    try:
        return datetime.strptime(name[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


def _readonly(p: Path) -> None:
    for f in p.rglob("*"):
        if f.is_file():
            f.chmod(stat.S_IREAD)


def install(tgz: Path, source: dict) -> dict:
    MIRROR.mkdir(parents=True, exist_ok=True)
    _assert_isolated(MIRROR / "x")
    with tempfile.TemporaryDirectory(dir=MIRROR, prefix=".incoming-") as tmp:
        folder = _safe_extract(tgz, Path(tmp))
        info = verify_backup(folder)
        final = MIRROR / folder.name
        _assert_isolated(final)
        same = final.exists() and (final / "MANIFEST.json").exists() and             (final / "MANIFEST.json").read_bytes() == (folder / "MANIFEST.json").read_bytes()
        if same:
            pass  # this exact verified backup is already installed (and may be open in the running viewer): keep it
        else:
            if final.exists():
                if not _rmtree(final):
                    die(f"{final.name} is already mirrored with different content and is in use. Stop the terminal, then re-run.")
            shutil.move(str(folder), str(final))
    if not same:
        _readonly(final)
    # The viewer opens a throw-away COPY of the ledger (the paper code migrates/WAL-pragmas on connect), so the
    # verified original stays byte-identical to what the host hashed.
    work = final / "work"
    work.mkdir(exist_ok=True)
    ledger_name = Path(info["ledger"]).name
    if not (work / ledger_name).exists():
        shutil.copyfile(final / info["ledger"], work / ledger_name)
        (work / ledger_name).chmod(stat.S_IREAD | stat.S_IWRITE)
    ptr = {
        "name": final.name, "path": str(final), "work_dir": str(work), "ledger_file": ledger_name,
        "ledger_name": Path(ledger_name).stem, "shadow_file": info["shadow"], "files": info["files"],
        "backup_created_at": info["manifest"].get("created_iso") or info["manifest"].get("created_at") or info["manifest"].get("at") or _from_folder(final.name),
        "engine_version": info["manifest"].get("engine_version"), "runtime_version": info["manifest"].get("runtime_version"),
        "host_evidence": info["manifest"].get("evidence"), "ledger_summary": info["manifest"].get("ledger_summary"),
        "source": source, "synced_at": datetime.now(timezone.utc).isoformat(), "verified": True,
    }
    tmp_ptr = MIRROR / ".current.json.tmp"
    tmp_ptr.write_text(json.dumps(ptr, indent=2), encoding="utf-8")
    os.replace(tmp_ptr, MIRROR / "current.json")
    prune(final.name)
    return ptr


def _rmtree(p: Path) -> bool:
    """Remove a mirror folder; on Windows a file open in the running viewer cannot be deleted — report instead of crashing."""
    def _fix(func, path, _exc):
        os.chmod(path, stat.S_IWRITE)
        func(path)
    try:
        shutil.rmtree(p, onerror=_fix)
        return True
    except OSError:
        return False


def prune(keep_name: str) -> None:
    dirs = sorted([p for p in MIRROR.iterdir() if p.is_dir() and not p.name.startswith(".")], key=lambda p: p.stat().st_mtime, reverse=True)
    for old in dirs[KEEP:]:
        if old.name != keep_name:
            _assert_isolated(old)
            if not _rmtree(old):
                say(f"  (kept old mirror {old.name}: still in use — it will be removed on a later sync)")


def current() -> dict | None:
    p = MIRROR / "current.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def human(n: float) -> str:
    return f"{n / 1e6:.2f} MB" if n >= 1e6 else f"{n / 1e3:.0f} kB"


def age(iso: str | None) -> str:
    if not iso:
        return "unknown"
    try:
        s = (datetime.now(timezone.utc) - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds()
    except ValueError:
        return "unknown"
    return f"{s / 60:.0f} min ago" if s < 5400 else f"{s / 3600:.1f} h ago" if s < 172800 else f"{s / 86400:.1f} days ago"


def print_status() -> None:
    c = current()
    if not c:
        say(f"No cloud mirror yet ({MIRROR}). Run: python scripts/sync_cloud_data.py")
        return
    say(f"Mirrored: {c['name']}")
    say(f"  ledger          {c['ledger_name']}   shadow DB: {'yes' if c.get('shadow_file') else 'no'}")
    say(f"  backup taken    {c.get('backup_created_at') or 'n/a'}")
    say(f"  downloaded      {c['synced_at']} ({age(c['synced_at'])})")
    s = c.get("source") or {}
    say(f"  from            artifact {s.get('name', '?')} · run {s.get('run_id', '?')} · {human(s.get('size', 0))}")
    say(f"  location        {c['path']}")


def refresh_remote(repo: str, before: str | None) -> None:
    say(f"→ asking the cloud for a fresh backup (dispatching {WORKFLOW}; read-only: status + backup pull)…")
    gh("workflow", "run", WORKFLOW, "--repo", repo)
    deadline = time.time() + 600
    while time.time() < deadline:
        time.sleep(15)
        arts = list_artifacts(repo)
        if arts and arts[0]["name"] != before:
            return
        say("  …waiting for the workflow to publish a new artifact")
    die("timed out waiting for the new backup (check the Actions tab); the previous mirror is unchanged")


def main() -> None:
    ap = argparse.ArgumentParser(description="Mirror the cloud paper ledger locally (read-only).")
    ap.add_argument("--status", action="store_true", help="show what is mirrored and exit")
    ap.add_argument("--dry-run", action="store_true", help="show the newest remote backup; download nothing")
    ap.add_argument("--refresh", action="store_true", help="dispatch ubuntu-ops.yml for a fresh backup first")
    ap.add_argument("--force", action="store_true", help="re-download even if this backup is already mirrored")
    ap.add_argument("--from-file", metavar="TGZ", help="install a backup.tgz you downloaded yourself")
    a = ap.parse_args()

    if a.status:
        return print_status()
    if a.from_file:
        p = Path(a.from_file)
        if not p.is_file():
            die(f"{p} not found")
        ptr = install(p, {"name": p.name, "run_id": None, "size": p.stat().st_size, "created_at": None})
        say(f"✓ installed {ptr['name']} from {p.name}")
        return print_status()

    require_gh_auth()
    repo = repo_name()
    arts = list_artifacts(repo)
    if a.refresh and not a.dry_run:
        refresh_remote(repo, arts[0]["name"] if arts else None)
        arts = list_artifacts(repo)
    if not arts:
        die(f"no `{ARTIFACT_PREFIX}*` artifact found in {repo}. Has the ops workflow run? (Actions → Ubuntu runtime ops)")
    top = arts[0]
    say(f"Newest cloud backup: {top['name']} · {human(top['size'])} · created {top['created_at']} ({age(top['created_at'])}) · run {top['run_id']} ({repo})")

    cur = current()
    if cur and (cur.get("source") or {}).get("name") == top["name"] and not a.force:
        say("Already mirrored — nothing to do.")
        return print_status()
    if a.dry_run:
        say("(dry run: nothing downloaded)")
        return

    with tempfile.TemporaryDirectory(prefix="avdi-dl-") as tmp:
        say("→ downloading…")
        gh("run", "download", str(top["run_id"]), "--repo", repo, "-n", top["name"], "-D", tmp, capture=False)
        tgz = next(Path(tmp).rglob("*.tgz"), None) or next(Path(tmp).rglob("*.tar.gz"), None)
        if not tgz:
            die("the artifact did not contain a .tgz backup")
        say("→ verifying (sha256 + SQLite integrity) and installing…")
        ptr = install(tgz, {k: top[k] for k in ("name", "run_id", "size", "created_at")})
    say(f"✓ mirrored {ptr['name']} — verified. The local ledger was not touched.")
    print_status()


if __name__ == "__main__":
    main()
