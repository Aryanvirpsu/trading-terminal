"""AVDI continuous paper runtime — command line.

    python automation/avdi_runtime.py run                 # the supervised service (paper only)
    python automation/avdi_runtime.py status              # health + operational status (JSON); exit 1 if unhealthy
    python automation/avdi_runtime.py health              # same, quiet (Docker HEALTHCHECK)
    python automation/avdi_runtime.py schedule [YYYY-MM-DD]   # print the ET schedule for a date
    python automation/avdi_runtime.py discovery-once [--entries]   # ONE discovery cycle (observation-only by default)
    python automation/avdi_runtime.py tracker-once [--scope positions|all]
    python automation/avdi_runtime.py close-once          # postmarket + report + CH-001 + backup
    python automation/avdi_runtime.py backup              # consistent snapshot of ledger + shadow DB + state

Every command that can touch the ledger takes the SAME exclusive runtime lock as `run`, so it cannot
run beside the service (stop the service first). PAPER ONLY: refuses to start if live execution or a
broker is configured.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("lab", "dashboard", "src"):
    sys.path.insert(0, os.path.join(_ROOT, _p))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from paper import runtime as rt  # noqa: E402
from paper import session_calendar as cal  # noqa: E402


def _print(o) -> None:
    print(json.dumps(o, indent=2, default=str))


def _fatal(code: int, why: str):
    """Fatal, non-retryable start conditions exit non-zero after a delay so a supervisor restart policy
    cannot spin (Docker also applies its own exponential restart back-off)."""
    rt.log("fatal_exit", code=code, reason=why)
    import time as _t
    _t.sleep(float(os.environ.get("AVDI_FATAL_EXIT_DELAY_S", "30")))
    raise SystemExit(code)


def _locked_or_exit():
    lock = rt.runtime_lock()
    if not lock.acquire():
        try:                                   # make the attempt visible in health/status
            st = rt.load_state()
            st["duplicate_attempts"] = int(st.get("duplicate_attempts", 0)) + 1
            st["last_duplicate_attempt"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            rt.save_state(st)
        except Exception:
            pass
        rt.log("duplicate_runtime_refused", note="another AVDI runtime holds the singleton lock")
        _fatal(3, "duplicate runtime")
    return lock


def main(argv=None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    cmd = a[0] if a else "status"
    flags = {x for x in a[1:] if x.startswith("--")}
    args = [x for x in a[1:] if not x.startswith("--")]

    if cmd in ("status", "health"):
        h = rt.health()
        if cmd == "status":
            _print(h)
        else:
            print("healthy" if h["healthy"] else "UNHEALTHY: " + ",".join(h["unhealthy"]))
        return 0 if h["healthy"] else 1

    if cmd == "schedule":
        d = dt.date.fromisoformat(args[0]) if args else dt.datetime.now(cal.ET).date()
        _print({"date": d.isoformat(), "trading_day": cal.is_trading_day(d), "early_close": cal.is_early_close(d),
                "slots": [{"id": s.id, "kind": s.kind, "et": s.when.strftime("%H:%M"), "session_type": s.session_type,
                           "entries": s.allow_entries} for s in rt.build_slots(d)]})
        return 0

    try:
        rt.assert_paper_only()
    except rt.LiveExecutionRefused as e:
        _fatal(4, str(e))
    if cmd == "run":
        lock = _locked_or_exit()
        try:
            rt.log("runtime_start", pid=os.getpid(), data_dir=rt.db._DATA_DIR, config=rt.rcfg())
            rt.Runtime().run_forever()
        finally:
            lock.release()
        return 0

    lock = _locked_or_exit()
    try:
        acts = rt.PaperActions()
        d = dt.datetime.now(cal.ET).date()
        if cmd == "discovery-once":
            now = dt.datetime.now(cal.ET)
            _print(acts.discovery(d, f"{d.isoformat()}T{now:%H%M}manual", "--entries" in flags, "manual"))
        elif cmd == "tracker-once":
            _print(acts.tracker(d, "positions" if "--scope" in a and "positions" in a else "all"))
        elif cmd == "close-once":
            _print(acts.close(d))
        elif cmd == "backup":
            _print(rt.backup_now("manual"))
        else:
            print(f"unknown command {cmd!r}", file=sys.stderr)
            return 2
    finally:
        lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
