"""AVDI continuous paper runtime — discovery loop + tracking loop under one supervised process.

Two logical loops, independently understandable and testable:
  DISCOVERY  every ~15 min in the regular session (09:35 ET first slot): full scan -> A/B/C/D/E -> paper
             orders (entries only until the entry cutoff) -> Champion journal + shadow candidate log.
  TRACKING   every 60 s (open positions / orders only) and every 300 s (all tracked signals + raw bars):
             fresh quotes, stops/targets, MFE/MAE, complete-bar collection for CH-001 / Challengers.

SAFETY MODEL
  * PAPER ONLY. `assert_paper_only()` refuses to start if live execution is enabled or a broker is set.
  * SINGLETON. An exclusive OS file lock (`runtime/runtime.lock`) is held for the process lifetime; a second
    runtime (or a manual trading command) cannot start. Discovery additionally holds a non-blocking
    `discovery.lock`, so a slow scan can never overlap the next one.
  * NO BACKFILL. A slot missed by more than SLOT_GRACE_S (downtime, long previous scan) is recorded as
    missed and skipped — the runtime never fakes a scan for a past time.
  * STATE LIVES IN THE LEDGER. Cash, open positions, entries used today and sector exposure are derived
    from the persisted paper ledger on every cycle, so a restart resumes with the true account state.
    `runtime_state.json` holds only scheduling bookkeeping (slots done/missed, last errors, heartbeat).
  * FAIL CLOSED. Ledger inconsistency / restore failure / unhealthy providers -> no entries. Shadow-only
    tooling fails open (never affects the Champion).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import os
import shutil
import signal
import sqlite3
import sys
import time
import traceback
from typing import Any, Callable, Dict, List, Optional

from . import db
from . import market_calendar as cal

# ── configuration (env-overridable; defaults are operating choices, NOT Champion gates) ─────────────

def rcfg() -> Dict[str, Any]:
    e = os.environ.get
    return {
        "prep_health": (8, 30), "prep_scan": (9, 15), "first_discovery": (9, 35),
        "interval_min": int(e("AVDI_DISCOVERY_INTERVAL_MIN", "15")),
        "entry_cutoff_min_before_close": int(e("AVDI_ENTRY_CUTOFF_MIN_BEFORE_CLOSE", "60")),
        "last_obs_min_before_close": int(e("AVDI_LAST_OBS_MIN_BEFORE_CLOSE", "10")),
        "close_process_min_after_close": int(e("AVDI_CLOSE_PROCESS_MIN_AFTER_CLOSE", "10")),
        "tracker_fast_s": int(e("AVDI_TRACKER_FAST_S", "60")),
        "tracker_full_s": int(e("AVDI_TRACKER_FULL_S", "300")),
        "slot_grace_s": int(e("AVDI_SLOT_GRACE_S", "420")),
        "tick_s": float(e("AVDI_TICK_S", "5")),
        "backup_keep": int(e("AVDI_BACKUP_KEEP", "14")),
        "disk_min_free_pct": float(e("AVDI_DISK_MIN_FREE_PCT", "10")),
    }


def rt_dir() -> str:
    d = os.path.join(db._DATA_DIR, "runtime")
    os.makedirs(d, exist_ok=True)
    return d


def backup_dir() -> str:
    return os.path.join(db._DATA_DIR, "backups")


def report_dir() -> str:
    return os.path.join(db._DATA_DIR, "reports")


# ── structured logging ───────────────────────────────────────────────────────────────────────────────

def log(event: str, **fields: Any) -> None:
    rec = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "event": event, **fields}
    print(json.dumps(rec, default=str), flush=True)


# ── safety: paper only ───────────────────────────────────────────────────────────────────────────────

class LiveExecutionRefused(RuntimeError):
    pass


def assert_paper_only(env: Optional[Dict[str, str]] = None) -> None:
    """Refuse to run if anything could route to a real broker. Live execution is a separate decision."""
    env = os.environ if env is None else env
    if str(env.get("ROBINHOOD_TRADING_ENABLED", "false")).strip().lower() in ("1", "true", "yes", "on"):
        raise LiveExecutionRefused("ROBINHOOD_TRADING_ENABLED is on — this runtime is paper-only")
    if str(env.get("BROKER_PROVIDER", "none")).strip().lower() not in ("", "none"):
        raise LiveExecutionRefused(f"BROKER_PROVIDER={env.get('BROKER_PROVIDER')!r} — this runtime is paper-only")


# ── singleton lock ───────────────────────────────────────────────────────────────────────────────────

class FileLock:
    """Non-blocking exclusive OS lock (fcntl on POSIX, msvcrt on Windows). Released automatically if the
    process dies, so a crash can never leave a stale lock that blocks the restart."""

    def __init__(self, path: str):
        self.path = path
        self._fh = None

    def acquire(self) -> bool:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fh = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps({"pid": os.getpid(), "since": dt.datetime.now(dt.timezone.utc).isoformat()}))
        fh.flush()
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None

    @property
    def held(self) -> bool:
        return self._fh is not None


def runtime_lock() -> FileLock:
    return FileLock(os.path.join(rt_dir(), "runtime.lock"))


def _dup_path() -> str:
    return os.path.join(rt_dir(), "duplicate_attempts.json")


def note_duplicate_attempt() -> None:
    """Record that a second runtime tried to start. Kept OUT of runtime_state.json, which the running
    service rewrites every tick and would silently overwrite this."""
    try:
        try:
            with open(_dup_path(), encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            d = {"count": 0}
        d["count"] = int(d.get("count", 0)) + 1
        d["last"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        save_state(d, _dup_path())
    except Exception:
        pass


def recent_duplicate_attempt(now: dt.datetime, within_s: int = 86400) -> Optional[Dict[str, Any]]:
    try:
        with open(_dup_path(), encoding="utf-8") as fh:
            d = json.load(fh)
        if (now - dt.datetime.fromisoformat(d["last"])).total_seconds() <= within_s:
            return d
    except Exception:
        pass
    return None


def discovery_lock() -> FileLock:
    return FileLock(os.path.join(rt_dir(), "discovery.lock"))


# ── schedule ─────────────────────────────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class Slot:
    id: str                 # unique within the session date, e.g. "discovery@0935"
    kind: str               # prep_health | prep_scan | discovery | close
    when: dt.datetime       # ET-aware
    session_type: str
    allow_entries: bool


def build_slots(d: dt.date, c: Optional[Dict[str, Any]] = None) -> List[Slot]:
    """The ET schedule for one session date (empty on holidays/weekends; early closes shift the tail)."""
    c = c or rcfg()
    sess = cal.session(d)
    if sess is None:
        return []
    opn, close = sess
    at = lambda hm: dt.datetime.combine(d, dt.time(*hm), cal.ET)
    slots = [Slot("prep_health@%02d%02d" % c["prep_health"], "prep_health", at(c["prep_health"]), "premarket_prep", False),
             Slot("prep_scan@%02d%02d" % c["prep_scan"], "prep_scan", at(c["prep_scan"]), "premarket_prep", False)]
    cutoff = close - dt.timedelta(minutes=c["entry_cutoff_min_before_close"])
    last = close - dt.timedelta(minutes=c["last_obs_min_before_close"])
    t = at(c["first_discovery"])
    while t <= last:
        allow = t <= cutoff
        slots.append(Slot(f"discovery@{t:%H%M}", "discovery", t, "regular" if allow else "post_cutoff", allow))
        t += dt.timedelta(minutes=c["interval_min"])
    slots.append(Slot(f"close@{(close + dt.timedelta(minutes=c['close_process_min_after_close'])):%H%M}", "close",
                      close + dt.timedelta(minutes=c["close_process_min_after_close"]), "close", False))
    return slots


# ── persisted scheduling state (NOT trading state — that lives in the ledger) ───────────────────────

def _state_path() -> str:
    return os.path.join(rt_dir(), "runtime_state.json")


def load_state(path: Optional[str] = None) -> Dict[str, Any]:
    try:
        with open(path or _state_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(state: Dict[str, Any], path: Optional[str] = None) -> None:
    p = path or _state_path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1, default=str)
    os.replace(tmp, p)


# ── actions (the real paper pipeline) ────────────────────────────────────────────────────────────────

class PaperActions:
    """Thin adapters over lab/paper/workflow.py. Everything here is paper-only."""

    def prep_health(self, d: dt.date) -> Dict[str, Any]:
        from . import broker, workflow
        out: Dict[str, Any] = {}
        out["providers"] = workflow.provider_health()
        out["disk"] = disk_status()
        out["ledger"] = ledger_status()
        out["reconciled"] = broker.reconcile()["reconciled"]
        out["secrets_present"] = sorted(k for k in ("FINNHUB_API_KEY", "FRED_API_KEY", "ALPHAVANTAGE_API_KEY")
                                        if os.environ.get(k))          # names only, never values
        out["ok"] = bool(out["providers"].get("healthy")) and out["ledger"]["ok"] and out["reconciled"] \
            and not out["disk"]["low"]
        return out

    def discovery(self, d: dt.date, cycle_id: str, allow_entries: bool, session_type: str) -> Dict[str, Any]:
        from . import workflow
        return workflow.premarket(d.isoformat(), cycle_id=cycle_id, allow_entries=allow_entries,
                                  session_type=session_type,
                                  scan_ts=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))

    def tracker(self, d: dt.date, scope: str) -> Dict[str, Any]:
        from . import workflow
        return workflow.market_hours(d.isoformat(), scope=scope)

    def close(self, d: dt.date) -> Dict[str, Any]:
        from . import shadow_log, workflow
        out: Dict[str, Any] = {}
        out["postmarket"] = {k: v for k, v in workflow.postmarket(d.isoformat()).items() if k != "report"}
        rep = None
        try:
            from . import report
            rep = report.daily(d.isoformat())
            os.makedirs(report_dir(), exist_ok=True)
            with open(os.path.join(report_dir(), f"{d.isoformat()}.json"), "w", encoding="utf-8") as fh:
                json.dump(rep, fh, indent=1, default=str)
        except Exception as e:
            out["report_error"] = str(e)[:160]
        out["shadow_bars"] = shadow_log.update_bars(d.isoformat(), intraday_every_min=1)
        out["ch001"] = shadow_log.evaluate_ch001()
        out["backup"] = backup_now("close")
        return out


# ── health / disk / ledger / backup helpers ──────────────────────────────────────────────────────────

def disk_status(path: Optional[str] = None) -> Dict[str, Any]:
    p = path or db._DATA_DIR
    while p and not os.path.isdir(p):
        p = os.path.dirname(p)
    u = shutil.disk_usage(p or ".")
    free_pct = round(u.free / u.total * 100, 1)
    return {"free_pct": free_pct, "free_gb": round(u.free / 1e9, 1), "low": free_pct < rcfg()["disk_min_free_pct"]}


def ledger_status() -> Dict[str, Any]:
    try:
        row = db.query_one("PRAGMA quick_check") or {}
        ok = list(row.values())[0] == "ok" if row else False
        return {"ok": ok, "path": db.db_path()}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}


def account_snapshot(session_date: Optional[str] = None) -> Dict[str, Any]:
    """Account facts re-derived from the persisted ledger (what a restart sees)."""
    from . import risk
    st = risk.account_state(session_date or dt.date.today().isoformat())
    return {"cash": st["cash"], "available_cash": st["available_cash"], "buying_power": st["buying_power"],
            "open_positions": st["open_positions"], "entries_today": st["entries_today"],
            "sector_positions": st["sector_positions"], "equity": st["equity"]}


def backup_now(reason: str = "manual") -> Dict[str, Any]:
    """Consistent snapshot (sqlite online-backup API) of the ledger, shadow DB, state and reports."""
    from . import shadow_log
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = os.path.join(backup_dir(), stamp)
    os.makedirs(dest, exist_ok=True)
    files: Dict[str, Any] = {}
    srcs = [os.path.join(db._DATA_DIR, f) for f in os.listdir(db._DATA_DIR) if f.endswith(".db")]
    if os.path.exists(shadow_log.path()):
        srcs.append(shadow_log.path())
    for src in srcs:
        out = os.path.join(dest, os.path.relpath(src, db._DATA_DIR).replace(os.sep, "__"))
        s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        d = sqlite3.connect(out)
        with d:
            s.backup(d)
        ok = d.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        d.close()
        s.close()
        files[os.path.basename(out)] = {"bytes": os.path.getsize(out), "integrity_ok": ok,
                                        "sha256": hashlib.sha256(open(out, "rb").read()).hexdigest()}
    for extra in (_state_path(),):
        if os.path.exists(extra):
            shutil.copy2(extra, dest)
    if os.path.isdir(report_dir()):
        shutil.copytree(report_dir(), os.path.join(dest, "reports"), dirs_exist_ok=True)
    ok_all = bool(files) and all(f["integrity_ok"] for f in files.values())
    with open(os.path.join(dest, "MANIFEST.json"), "w", encoding="utf-8") as fh:
        json.dump({"created": stamp, "reason": reason, "files": files, "ok": ok_all}, fh, indent=1)
    # retention
    keep = rcfg()["backup_keep"]
    old = sorted(p for p in os.listdir(backup_dir()) if os.path.isdir(os.path.join(backup_dir(), p)))
    for p in old[:-keep]:
        shutil.rmtree(os.path.join(backup_dir(), p), ignore_errors=True)
    return {"path": dest, "ok": ok_all, "files": len(files), "at": stamp}


# ── the supervisor ───────────────────────────────────────────────────────────────────────────────────

class Runtime:
    def __init__(self, actions: Any = None, clock: Optional[Callable[[], dt.datetime]] = None,
                 config: Optional[Dict[str, Any]] = None, state_path: Optional[str] = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.actions = actions or PaperActions()
        self.clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
        self.c = config or rcfg()
        self._state_path = state_path
        self.sleep = sleep
        self.stop = False
        self.state = load_state(state_path)
        self.state.setdefault("done", {})
        self.state.setdefault("missed", {})
        self.state.setdefault("errors", [])
        self.state.setdefault("counters", {"discovery_runs": 0, "tracker_fast": 0, "tracker_full": 0,
                                           "discovery_failures": 0, "provider_failures": 0})

    # -- persistence & logging ------------------------------------------------
    def _save(self) -> None:
        save_state(self.state, self._state_path)

    def _err(self, where: str, exc: BaseException) -> None:
        rec = {"at": self.clock().isoformat(timespec="seconds"), "where": where,
               "error": f"{type(exc).__name__}: {exc}"[:300]}
        self.state["errors"] = (self.state["errors"] + [rec])[-20:]
        self.state["last_exception"] = rec
        log("exception", where=where, error=rec["error"], trace=traceback.format_exc()[-600:])

    # -- one scheduling pass ----------------------------------------------------
    def startup(self) -> Dict[str, Any]:
        """Reconstruct the account from the persisted ledger BEFORE any entry can be submitted."""
        info: Dict[str, Any] = {"restore_ok": False}
        try:
            info["ledger"] = ledger_status()
            from . import broker
            rec = broker.reconcile()
            info["reconciled"] = rec["reconciled"]
            info["account"] = account_snapshot(self.clock().astimezone(cal.ET).date().isoformat())
            info["restore_ok"] = bool(info["ledger"]["ok"] and rec["reconciled"])
        except Exception as e:
            self._err("startup", e)
            info["error"] = str(e)[:160]
        self.state["restore_ok"] = info["restore_ok"]
        self.state["started_at"] = self.clock().isoformat(timespec="seconds")
        self.state["startup"] = info
        interrupted = [k for k, v in self.state["done"].items() if v.get("status") == "started"]
        for k in interrupted:
            self.state["done"][k]["status"] = "interrupted"     # at-most-once: never re-run, never backfill
        if interrupted:
            log("startup_interrupted_slots", slots=interrupted)
        self._save()
        log("startup", **{k: v for k, v in info.items() if k != "ledger"}, ledger=info.get("ledger"))
        return info

    def tick(self) -> List[str]:
        now = self.clock()
        et = now.astimezone(cal.ET)
        d = et.date()
        events: List[str] = []
        self.state["heartbeat"] = now.isoformat(timespec="seconds")
        self.state["session_date"] = d.isoformat()
        self.state["market_open_today"] = cal.is_trading_day(d)
        if not cal.is_trading_day(d):
            self._save()
            return events
        sess = cal.session(d)
        opn, close = sess
        # 1) tracker first: stops/targets matter more than a new scan
        in_track = opn <= et <= close + dt.timedelta(minutes=5)
        if in_track:
            nf = self.state.get("next_fast_ts", 0)
            nl = self.state.get("next_full_ts", 0)
            ts = now.timestamp()
            scope = "all" if ts >= nl else ("positions" if ts >= nf else None)
            if scope:
                self._run_tracker(d, scope, events)
                self.state["next_fast_ts"] = ts + self.c["tracker_fast_s"]        # no catch-up bursts
                if scope == "all":
                    self.state["next_full_ts"] = ts + self.c["tracker_full_s"]
        # 2) scheduled slots (at most once each; stale slots are skipped, never back-filled)
        missed_now: List[str] = []
        for slot in build_slots(d, self.c):
            sid = f"{d.isoformat()}:{slot.id}"
            if sid in self.state["done"] or sid in self.state["missed"]:
                continue
            if et < slot.when:
                break
            late = (et - slot.when).total_seconds()
            if late > self.c["slot_grace_s"]:
                self.state["missed"][sid] = {"late_s": int(late), "at": now.isoformat(timespec="seconds")}
                events.append(f"missed:{sid}")
                missed_now.append(sid)
                continue
            self._run_slot(d, slot, sid, events)
        if missed_now:
            log("slots_missed", count=len(missed_now), first=missed_now[0], last=missed_now[-1],
                note="skipped, never back-filled")
        self._prune(d)
        self._save()
        return events

    def _prune(self, d: dt.date) -> None:
        cutoff = (d - dt.timedelta(days=7)).isoformat()
        for k in ("done", "missed"):
            for sid in [s for s in self.state[k] if s.split(":")[0] < cutoff]:
                del self.state[k][sid]

    def _run_tracker(self, d: dt.date, scope: str, events: List[str]) -> None:
        try:
            out = self.actions.tracker(d, scope)
            self.state["counters"]["tracker_full" if scope == "all" else "tracker_fast"] += 1
            self.state["last_tracker"] = {"at": self.clock().isoformat(timespec="seconds"), "scope": scope,
                                          "symbols": len(out.get("symbols_watched", [])),
                                          "exits": out.get("exits"),
                                          "quote_age_s_max": out.get("quote_age_s_max"),
                                          "quotes_missing": out.get("quotes_missing")}
            events.append(f"tracker:{scope}")
        except Exception as e:
            self._err(f"tracker:{scope}", e)

    def _run_slot(self, d: dt.date, slot: Slot, sid: str, events: List[str]) -> None:
        self.state["done"][sid] = {"status": "started", "at": self.clock().isoformat(timespec="seconds")}
        self._save()                                              # at-most-once even if we crash mid-run
        try:
            if slot.kind == "prep_health":
                out = self.actions.prep_health(d)
                self.state["prep"] = {"at": self.clock().isoformat(timespec="seconds"), "ok": out.get("ok"),
                                      "detail": {k: out[k] for k in out if k != "providers"}}
            elif slot.kind in ("prep_scan", "discovery"):
                self._run_discovery(d, slot, sid, events)
            elif slot.kind == "close":
                out = self.actions.close(d)
                self.state["last_close"] = {"at": self.clock().isoformat(timespec="seconds"),
                                            "backup": out.get("backup"), "ch001": out.get("ch001")}
                if out.get("backup"):
                    self.state["last_backup"] = {"at": self.clock().isoformat(timespec="seconds"),
                                                 "ok": out["backup"].get("ok"), "path": out["backup"].get("path")}
            if self.state["done"][sid]["status"] == "started":
                self.state["done"][sid]["status"] = "done"
            events.append(f"slot:{sid}")
        except Exception as e:
            self.state["done"][sid]["status"] = "failed"
            self._err(f"slot:{sid}", e)

    def _run_discovery(self, d: dt.date, slot: Slot, sid: str, events: List[str]) -> None:
        guard = discovery_lock()
        if not guard.acquire():
            self.state["done"][sid]["status"] = "overlap_skipped"
            log("discovery_overlap_skipped", slot=sid)
            events.append(f"overlap:{sid}")
            return
        try:
            allow = slot.allow_entries and self.state.get("restore_ok", True)
            cycle_id = f"{d.isoformat()}T{slot.when:%H%M}"
            if slot.session_type == "regular" and allow:
                try:                      # v1.1 starting snapshot: taken ONCE, before the first real in-session cycle
                    from . import shadow_log
                    b = shadow_log.ensure_boundary("v1.1", cycle_id, d.isoformat())
                    if b and b.get("created_now"):
                        log("evidence_boundary", version="v1.1", cycle_id=cycle_id, equity=b["equity"],
                            cash=b["cash"], open_positions=b["open_positions"])
                except Exception as e:
                    self._err("boundary", e)
            out = self.actions.discovery(d, cycle_id, allow, slot.session_type)
            ev = out.get("evaluated") or []
            self.state["counters"]["discovery_runs"] += 1
            st = out.get("state")
            if st != "ok":
                self.state["counters"]["provider_failures"] += 1
            else:
                self.state["counters"]["provider_failures"] = 0
            acct = None
            try:
                acct = account_snapshot(d.isoformat())
            except Exception:
                pass
            self.state["last_discovery"] = {
                "at": self.clock().isoformat(timespec="seconds"), "slot": sid, "cycle_id": cycle_id,
                "state": st, "session_type": slot.session_type, "entries_allowed": allow,
                "finalists": len(out.get("finalists") or []),
                "tradeable": sum(1 for e in ev if e.get("action") == "TRADEABLE"),
                "entries": len(out.get("orders_placed") or []), "account": acct}
            self.state["done"][sid]["status"] = "done" if st == "ok" else f"skipped:{st}"
            log("discovery_cycle", cycle_id=cycle_id, state=st, session_type=slot.session_type,
                finalists=self.state["last_discovery"]["finalists"], entries=self.state["last_discovery"]["entries"])
            events.append(f"discovery:{cycle_id}")
        except Exception as e:
            self.state["counters"]["discovery_failures"] += 1
            self.state["done"][sid]["status"] = "failed"
            self._err(f"discovery:{sid}", e)
        finally:
            guard.release()

    # -- lifecycle ---------------------------------------------------------------
    def run_forever(self) -> None:
        def _sig(_s, _f):
            self.stop = True
            log("shutdown_requested")
        for s in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(s, _sig)
            except (ValueError, OSError):
                pass
        self.startup()
        while not self.stop:
            try:
                self.tick()
            except Exception as e:
                self._err("tick", e)
            self.sleep(self.c["tick_s"])
        self.state["stopped_at"] = self.clock().isoformat(timespec="seconds")
        self._save()
        log("stopped")


# ── health ───────────────────────────────────────────────────────────────────────────────────────────

def health(now: Optional[dt.datetime] = None, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Operational status + unhealthy/degraded conditions. Unhealthy => exit 1 (Docker HEALTHCHECK)."""
    from . import shadow_log
    c = rcfg()
    now = now or dt.datetime.now(dt.timezone.utc)
    et = now.astimezone(cal.ET)
    state = load_state() if state is None else state
    bad: List[str] = []
    degraded: List[str] = []

    def age(ts: Optional[str]) -> Optional[float]:
        try:
            return (now - dt.datetime.fromisoformat(ts)).total_seconds()
        except Exception:
            return None

    hb_age = age(state.get("heartbeat"))
    if hb_age is None or hb_age > 120:
        bad.append("runtime_heartbeat_stale")
    lock = runtime_lock()
    if lock.acquire():                 # we got it => nobody is running the runtime
        lock.release()
        bad.append("runtime_not_running")
    if os.environ.get("AVDI_FORCE_UNHEALTHY", "").strip().lower() in ("1", "true", "yes"):
        bad.append("forced_unhealthy_test")       # deliberate hook to prove deploy rollback; never set in normal runs
    if state.get("restore_ok") is False:
        bad.append("persistence_restore_failed")
    dup = recent_duplicate_attempt(now)
    if dup:
        degraded.append("duplicate_runtime_attempted")
    led = ledger_status()
    if not led["ok"]:
        bad.append("db_inaccessible")
    else:
        try:
            from . import broker
            if not broker.reconcile()["reconciled"]:
                bad.append("ledger_inconsistent")
        except Exception:
            bad.append("db_inaccessible")
    dsk = disk_status()
    if dsk["low"]:
        bad.append("disk_low")
    sh = shadow_log.status()
    if not sh.get("ok"):
        degraded.append("shadow_logger_failure")
    ctr = state.get("counters", {})
    if ctr.get("provider_failures", 0) >= 3:
        bad.append("repeated_provider_failure")
    d = et.date()
    in_session = False
    if cal.is_trading_day(d):
        opn, close = cal.session(d)
        in_session = opn + dt.timedelta(minutes=20) <= et <= close
        if in_session:
            la = age((state.get("last_discovery") or {}).get("at"))
            if la is None or la > 2 * c["interval_min"] * 60:
                bad.append("no_discovery_scan_recent")
            ta = age((state.get("last_tracker") or {}).get("at"))
            if ta is None or ta > max(3 * c["tracker_full_s"], 900):
                bad.append("tracker_stopped")
            qa = (state.get("last_tracker") or {}).get("quote_age_s_max")
            if qa is not None and qa > 900:
                degraded.append("stale_market_data")
    bk_age = age((state.get("last_backup") or {}).get("at"))
    if bk_age is not None and bk_age > 30 * 3600 and cal.is_trading_day(d):
        degraded.append("backup_stale")
    return {"healthy": not bad, "unhealthy": bad, "degraded": degraded, "in_session": in_session,
            "session_date": d.isoformat(), "et_now": et.isoformat(timespec="seconds"),
            "heartbeat_age_s": hb_age, "last_discovery": state.get("last_discovery"),
            "last_tracker": state.get("last_tracker"), "last_backup": state.get("last_backup"),
            "last_exception": state.get("last_exception"), "counters": ctr, "disk": dsk,
            "shadow": sh, "restore_ok": state.get("restore_ok"), "started_at": state.get("started_at"),
            "code_version": os.environ.get("AVDI_CODE_VERSION", "unknown")}
