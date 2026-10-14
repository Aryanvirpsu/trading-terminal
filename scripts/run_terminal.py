#!/usr/bin/env python3
"""Run the whole terminal — backend and UI — with one command.

    python scripts/run_terminal.py            # build the UI if it is stale, serve everything from Flask
    python scripts/run_terminal.py --dev      # Flask + Vite dev server together (hot reload)

Default mode serves the built React app and the API from ONE process on one port
(http://127.0.0.1:5057/), exactly what production serves. ``--dev`` additionally starts Vite on
http://127.0.0.1:5173/ui/ with /api proxied to Flask, and stops both on Ctrl+C.

Read-only, paper-only: this launcher never touches broker settings or the paper pipeline.
Stdlib only, so it runs before any dependency is installed.
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
DIST = ROOT / "dashboard" / "ui_dist"
IS_WIN = os.name == "nt"
VITE_PORT = 5173

# Windows consoles default to a legacy code page that cannot print the arrows below.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

_BOLD, _DIM, _RED, _GREEN, _OFF = ("\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[0m") if sys.stdout.isatty() else ("",) * 5


def say(msg: str, colour: str = "") -> None:
    print(f"{colour}{msg}{_OFF}", flush=True)


def die(msg: str) -> "None":
    say(f"error: {msg}", _RED)
    sys.exit(1)


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


def wait_for(port: int, proc: subprocess.Popen, seconds: int = 90) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if proc.poll() is not None:
            return False
        if port_open(port):
            return True
        time.sleep(0.3)
    return False


def newest_mtime(paths: list[Path]) -> float:
    latest = 0.0
    for base in paths:
        if base.is_file():
            latest = max(latest, base.stat().st_mtime)
        elif base.is_dir():
            for p in base.rglob("*"):
                if p.is_file():
                    latest = max(latest, p.stat().st_mtime)
    return latest


def ui_is_stale() -> bool:
    built = DIST / "index.html"
    if not built.exists():
        return True
    sources = [FRONTEND / "src", FRONTEND / "index.html", FRONTEND / "package.json", FRONTEND / "package-lock.json",
               FRONTEND / "vite.config.ts", FRONTEND / "tsconfig.json"]
    return newest_mtime(sources) > built.stat().st_mtime


def npm() -> str:
    exe = shutil.which("npm")
    if not exe:
        die("Node.js / npm not found. Install Node 20+ (https://nodejs.org) or run with --no-build to use the legacy UI.")
    return exe


def run_step(cmd: list[str], cwd: Path, what: str) -> None:
    say(f"→ {what}", _DIM)
    if subprocess.run(cmd, cwd=cwd).returncode != 0:
        die(f"{what} failed")


def ensure_deps() -> None:
    nm = FRONTEND / "node_modules"
    marker = nm / ".package-lock.json"
    lock = FRONTEND / "package-lock.json"
    if nm.is_dir() and marker.exists() and (not lock.exists() or marker.stat().st_mtime >= lock.stat().st_mtime):
        return
    run_step([npm(), "ci" if lock.exists() else "install", "--no-audit", "--no-fund"], FRONTEND, "installing frontend dependencies")


def build_ui(force: bool) -> None:
    if not force and not ui_is_stale():
        say("→ UI bundle is up to date", _DIM)
        return
    ensure_deps()
    run_step([npm(), "run", "build"], FRONTEND, "building the UI (type-check + bundle)")


def stream(proc: subprocess.Popen, tag: str) -> None:
    assert proc.stdout is not None
    for raw in iter(proc.stdout.readline, b""):
        print(f"{_DIM}[{tag}]{_OFF} {raw.decode('utf-8', 'replace').rstrip()}", flush=True)


def spawn(cmd: list[str], cwd: Path, env: dict, tag: str | None = None) -> subprocess.Popen:
    kw: dict = {"cwd": cwd, "env": env}
    if tag:
        kw.update(stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if IS_WIN:
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # lets us kill the whole npm → node tree
    proc = subprocess.Popen(cmd, **kw)
    if tag:
        threading.Thread(target=stream, args=(proc, tag), daemon=True).start()
    return proc


def stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if IS_WIN:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the terminal backend + UI together.")
    ap.add_argument("--dev", action="store_true", help="also start the Vite dev server (hot reload) alongside Flask")
    ap.add_argument("--rebuild", action="store_true", help="force a UI rebuild even if the bundle looks current")
    ap.add_argument("--no-build", action="store_true", help="skip the UI build (serves the last bundle, or the legacy page if none)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("DASHBOARD_PORT", "5057")), help="Flask port (default 5057)")
    ap.add_argument("--open", action="store_true", help="open the browser when ready")
    ap.add_argument("--no-cloud", action="store_true", help="do not start the read-only cloud-mirror viewer")
    ap.add_argument("--cloud-port", type=int, default=int(os.environ.get("CLOUD_VIEWER_PORT", "5058")), help="cloud viewer port (default 5058)")
    a = ap.parse_args()

    if not (ROOT / "dashboard" / "app.py").exists():
        die(f"dashboard/app.py not found under {ROOT}")
    if port_open(a.port):
        die(f"port {a.port} is already in use — a terminal is probably already running (open http://127.0.0.1:{a.port}/), or pass --port.")
    if a.dev and port_open(VITE_PORT):
        die(f"port {VITE_PORT} (Vite) is already in use.")

    if not a.dev and not a.no_build:
        build_ui(a.rebuild)
    elif a.dev:
        ensure_deps()

    env = {**os.environ, "CLOUD_VIEWER_PORT": str(a.cloud_port), "DASHBOARD_PORT": str(a.port), "VITE_API_TARGET": f"http://127.0.0.1:{a.port}", "PYTHONUNBUFFERED": "1"}
    procs: list[subprocess.Popen] = []

    def shutdown(*_):
        say("\nstopping…", _DIM)
        for p in reversed(procs):
            stop(p)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    say(f"{_BOLD}Starting backend{_OFF} (Flask, port {a.port})…")
    flask = spawn([sys.executable, str(ROOT / "dashboard" / "app.py")], ROOT, env, tag="api" if a.dev else None)
    procs.append(flask)
    if not wait_for(a.port, flask):
        shutdown() if flask.poll() is None else die("the backend exited during start-up (see output above)")
    base = f"http://127.0.0.1:{a.port}/"

    # The cloud-mirror viewer: a separate, read-only process (see dashboard/cloud_viewer.py). It starts even before the
    # first sync, and switches to a newer mirror on its own, so syncing never needs a restart.
    if not a.no_cloud:
        if port_open(a.cloud_port):
            say(f"cloud viewer port {a.cloud_port} is busy — skipping the cloud viewer", _RED)
        else:
            cloud = spawn([sys.executable, str(ROOT / "dashboard" / "cloud_viewer.py")], ROOT, env, tag="cloud")
            procs.append(cloud)
            if not wait_for(a.cloud_port, cloud, 30):
                say("cloud viewer did not start (see [cloud] output) — continuing without it", _RED)

    if a.dev:
        say(f"{_BOLD}Starting UI dev server{_OFF} (Vite, port {VITE_PORT})…")
        vite = spawn([npm(), "run", "dev", "--", "--host", "127.0.0.1", "--port", str(VITE_PORT), "--strictPort"], FRONTEND, env, tag="ui")
        procs.append(vite)
        if not wait_for(VITE_PORT, vite):
            shutdown() if vite.poll() is None else die("the UI dev server exited during start-up")
        url = f"http://127.0.0.1:{VITE_PORT}/ui/"
        say(f"\n{_GREEN}{_BOLD}Ready{_OFF}  UI (hot reload): {url}\n       API:             {base}api/health\n       Ctrl+C stops both.\n")
    else:
        built = (DIST / "index.html").exists()
        url = base
        say(f"\n{_GREEN}{_BOLD}Ready{_OFF}  {url}   ({'React UI' if built else 'legacy UI — no bundle built'}, API on the same port)\n       Ctrl+C stops it.\n")

    if a.open:
        webbrowser.open(url)

    try:
        while True:
            for p in procs:
                if p.poll() is not None:
                    say("a process exited — shutting down", _RED)
                    shutdown()
            time.sleep(0.5)
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
