"""Pre-market scheduled run wrapper — America/New_York + NYSE calendar.

Drives the EXISTING pipeline (daily_runner.py, run_lab.py) on trading days only:
  * stage 'full'  (08:30 ET) — full data refresh + analysis: daily_runner + run_lab
  * stage 'light' (09:20 ET) — final lightweight refresh: daily_runner only

Skips weekends & NYSE holidays, prevents duplicate simultaneous runs (lock file),
retries transient failures, and persists status to scheduler_status.json for the
dashboard. Manual run still works:  python automation/scheduler_run.py --stage full --force
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys, time
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "..")
PY = sys.executable
DATA_DIR = os.path.expanduser("~/.tradingview_mcp_data")
STATUS = os.path.join(DATA_DIR, "scheduler_status.json")
LOCK = os.path.join(DATA_DIR, "scheduler.lock")
LOCK_STALE_S = 1800
SCHEDULE_ET = {"full": (8, 30), "light": (9, 20)}


def _nyse():
    import holidays
    return holidays.financial_holidays("NYSE")


def is_trading_day(d: date):
    if d.weekday() >= 5:
        return False, "weekend"
    try:
        h = _nyse()
        if d in h:
            return False, f"holiday: {h.get(d)}"
    except Exception:
        pass
    return True, "trading day"


def next_scheduled(now_et: datetime) -> str:
    slots = sorted(SCHEDULE_ET.values())
    d = now_et.date()
    for _ in range(8):
        ok, _why = is_trading_day(d)
        if ok:
            for hh, mm in slots:
                cand = datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET)
                if cand > now_et:
                    return cand.isoformat()
        d = d + timedelta(days=1)
    return ""


def _load():
    try:
        return json.load(open(STATUS, encoding="utf-8"))
    except Exception:
        return {}


def _save(st: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    json.dump(st, open(STATUS, "w", encoding="utf-8"), indent=2, default=str)


def _fresh(path: str, on_et_date: date) -> bool:
    try:
        mt = datetime.fromtimestamp(os.path.getmtime(path), ET).date()
        return mt == on_et_date
    except Exception:
        return False


def _run_script(path: str, timeout=1500, extra_args=None, retries=3) -> tuple:
    last = ""
    cmd = [PY, path] + list(extra_args or [])
    for attempt in range(1, retries + 1):            # retry transient failures
        try:
            r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=timeout)
            if r.returncode == 0:
                return True, attempt, ""
            last = (r.stderr or r.stdout or "")[-300:]
        except Exception as e:
            last = str(e)[:300]
        if attempt < retries:
            time.sleep(5 * attempt)
    return False, retries, last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["full", "light"], default="full")
    ap.add_argument("--force", action="store_true", help="ignore trading-day/holiday gate")
    args = ap.parse_args()

    now_et = datetime.now(ET)
    st = _load()
    st["next_run"] = next_scheduled(now_et)
    st["timezone"] = "America/New_York"

    ok_day, why = is_trading_day(now_et.date())
    if not ok_day and not args.force:
        st.update(last_check=now_et.isoformat(), current_status="skipped", skip_reason=why)
        _save(st)
        print(f"skipped: {why}")
        return 0

    # Duplicate-run guard
    if os.path.exists(LOCK) and (time.time() - os.path.getmtime(LOCK)) < LOCK_STALE_S:
        print("another run in progress — skipping (lock present)")
        return 0
    open(LOCK, "w").write(f"{os.getpid()} {now_et.isoformat()}")

    try:
        st.update(current_status="running", running_stage=args.stage, started_at=now_et.isoformat(),
                  errors=[])
        _save(st)
        runner = os.path.join(HERE, "daily_runner.py")

        stage_results, errors = [], []

        # 1) PROTECTIVE management — fast, must run. This is the stage that closes
        #    stopped-out positions and settles expired options. Short timeout, a
        #    couple of retries, and it ALONE defines run success.
        ok, attempts, err = _run_script(runner, timeout=300, extra_args=["--stage", "protect"], retries=2)
        stage_results.append({"stage": "protect", "ok": ok, "attempts": attempts, "blocking": True})
        if not ok:
            errors.append(f"protect: {err}")
        all_ok = ok  # success is defined by the protective run only

        # CHECKPOINT: persist the protective outcome NOW, before the slow scans. If
        # the OS later kills this process for exceeding its time limit or going on
        # battery, the status file still reflects today's successful protective run
        # (this is why status used to freeze for days — the write only happened at
        # the very end, after the stage that hung).
        ckpt = datetime.now(ET)
        st.update(current_status="running", last_run_at=ckpt.isoformat(), last_run_ok=all_ok,
                  last_stage=args.stage, stage_results=stage_results, errors=errors)
        if all_ok:
            st["last_success_at"] = ckpt.isoformat()
        st["next_run"] = next_scheduled(ckpt)
        _save(st)

        # 2) HEAVY SCANS — best-effort. TradingView can throttle these; a hang here
        #    must never take down the protective run above. Non-blocking, 1 attempt,
        #    up to 30 min (subprocess timeout cancels/kills it if it overruns).
        s_ok, s_att, s_err = _run_script(runner, timeout=1800, extra_args=["--stage", "scans"], retries=1)
        stage_results.append({"stage": "scans", "ok": s_ok, "attempts": s_att, "blocking": False})
        if not s_ok:
            errors.append(f"scans (non-fatal): {s_err}")

        # 3) Self-evolving lab (full stage only) — also best-effort / non-blocking.
        if args.stage == "full":
            l_ok, l_att, l_err = _run_script(os.path.join(REPO, "lab", "run_lab.py"), timeout=1800, retries=1)
            stage_results.append({"stage": "lab", "ok": l_ok, "attempts": l_att, "blocking": False})
            if not l_ok:
                errors.append(f"lab (non-fatal): {l_err}")
        finished = datetime.now(ET)
        st.update(current_status="idle", last_stage=args.stage, last_run_at=finished.isoformat(),
                  last_run_ok=all_ok, stage_results=stage_results, errors=errors)
        if all_ok:
            st["last_success_at"] = finished.isoformat()
        # pre-market ready = today's full analysis artifacts are fresh
        st["premarket_ready"] = (_fresh(os.path.join(DATA_DIR, "last_daily_run.json"), finished.date())
                                 and _fresh(os.path.join(DATA_DIR, "last_scan_both.json"), finished.date()))
        st["data_freshness"] = {
            "last_daily_run": os.path.getmtime(os.path.join(DATA_DIR, "last_daily_run.json"))
            if os.path.exists(os.path.join(DATA_DIR, "last_daily_run.json")) else None,
            "last_scan_both": os.path.getmtime(os.path.join(DATA_DIR, "last_scan_both.json"))
            if os.path.exists(os.path.join(DATA_DIR, "last_scan_both.json")) else None,
        }
        st["next_run"] = next_scheduled(datetime.now(ET))
        _save(st)
        print(f"{args.stage} run {'OK' if all_ok else 'ERRORS'}: {stage_results}")
        return 0 if all_ok else 1
    finally:
        try:
            os.remove(LOCK)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
