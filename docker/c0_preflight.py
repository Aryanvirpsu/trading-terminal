"""C0 G4 preflight — runs inside every container before the real command starts
(invoked by docker/entrypoint.sh). Pure, read-only, no network, no DB writes.

Refuses to start (non-zero exit) rather than run with an environment that doesn't match
what this gate's containers are supposed to be. See C0_CONTAINER_DESIGN.md §7.3 for the
design this implements, and C0_INVENTORY.md §5 for why each check exists.

Profile is read from the C0_PROFILE env var ("case1" or "case2"), set by
docker/compose.c0.yml per service — never guessed from TZ, so a misconfigured TZ is
itself something this script can catch instead of silently trusting.
"""
from __future__ import annotations

import os
import sys
import hashlib
import datetime as dt

ROOT = "/app"
EXPECTED_PY = (3, 11, 16)

PROFILES = {
    # profile: (expected TZ env value, expected local date for the fixed instant below)
    "case1": {"tz": "UTC", "fixed_instant_date": "2026-09-22", "home_state_mode": "ephemeral"},
    "case2": {"tz": "America/New_York", "fixed_instant_date": "2026-09-21", "home_state_mode": "persistent"},
}
# 2026-09-22T02:00:00Z == 2026-09-21T22:00:00 EDT — inside the [20:00,24:00) ET window
# where UTC date and America/New_York date disagree (see C0_TEST_BASELINE_311.md §3.1,
# the T-class finding this instant was chosen to probe).
FIXED_INSTANT = 1790042400

# The exact key list lab/paper/db.py:config_version() hashes (copied, not imported —
# this must catch a version drift between this file and db.py, not silently follow it).
HASHED_KEYS = [
    "TRADINGVIEW_ENABLED", "DECISION_ALLOW_STALE", "DECISION_MIN_DATA_QUALITY",
    "DECISION_CONVICTION_MIN", "DECISION_ALLOW_FALLBACK_TRADEABLE",
    "DECISION_ALLOW_HIGH_DISAGREEMENT", "SENTIMENT_MODEL_ENABLED",
    "PAPER_LEDGER", "PAPER_INITIAL_CASH", "PAPER_INITIAL_EQUITY",
    "PAPER_BUYING_POWER", "PAPER_MARGIN_ENABLED", "PAPER_ALLOW_SHORTING",
    "PAPER_ALLOW_NAKED_OPTIONS", "PAPER_FRACTIONAL_SHARES",
    "PAPER_MAX_LOSS_PER_TRADE", "PAPER_MAX_POSITION_NOTIONAL",
    "PAPER_MIN_CASH_RESERVE_USD", "PAPER_MAX_DAILY_LOSS_USD",
    "PAPER_MAX_DRAWDOWN_USD", "PAPER_MAX_ENTRIES_PER_DAY", "PAPER_MAX_OPEN",
    "PAPER_MAX_PER_SECTOR", "PAPER_MAX_CORRELATED", "PAPER_RISK_PER_TRADE",
    "PAPER_MAX_OPTION_PREMIUM", "PAPER_OPTIONS_SHADOW_ONLY",
    "PAPER_SLIPPAGE_BPS", "PAPER_GAP_SLIPPAGE_BPS", "PAPER_FEE_PER_SHARE",
    "PAPER_FEE_PCT", "PAPER_STRATEGIES",
]
EXPECTED_DEFAULT_CONFIG_VERSION = "cfg-a0eede144e"

# Tuning knobs that change behaviour WITHOUT changing config_version (C0_INVENTORY.md
# §5.4) — a clean config_version alone is not proof of a clean environment.
UNHASHED_TUNING_KEYS = [
    "PAPER_COOLDOWN_LOSSES", "PAPER_COOLDOWN_DAYS", "PAPER_ALLOW_SPREADS",
    "PAPER_ALLOW_MULTI_LEG", "RISK_PROFILE", "SCAN_ALLOW_LEVERAGED",
    "SIZE_FRACTIONAL", "OPT_SPREADS_ALLOWED", "OPT_MODEL_GREEKS",
    "REGIME_RISK_BAND", "SCAN_COHORT_TOP_N", "HIST_LOOKBACK_RANGE",
    "STRATEGY_ANALYSIS_TTL", "STRATEGY_SCAN_WORKERS", "STRATEGY_DEEP_CAP",
    "FINNHUB_RATE_PER_MIN", "TERMINAL_QUOTE_TTL_S", "SECTOR_LOOKUP_TTL_DAYS",
    "MKT_PREMARKET_OPEN_H",
]
UNHASHED_TUNING_PREFIXES = ("FRESH_", "TRADINGVIEW_MCP_")

# Credentials / broker-live / dangerous knobs (C0_INVENTORY.md §5.5) — checked
# specifically, not by banning an entire prefix (ROBINHOOD_MCP_URL etc. are benign
# read-only defaults and must stay usable).
DANGEROUS_EXACT = [
    "ROBINHOOD_USERNAME", "ROBINHOOD_PASSWORD", "RH_USERNAME", "RH_PASSWORD",
    "RH_MFA", "RH_REQUEST_JSON", "AXISDIRECT_CLIENT_ID",
    "AXISDIRECT_AUTHORIZATION_KEY", "SNAPTRADE_CLIENT_ID",
    "SNAPTRADE_CONSUMER_KEY", "PROXY_PASSWORD",
]

REQUIRED_DIRS = ["lab", "dashboard", "canonical", "src", "automation", "docker"]
FORBIDDEN_BASENAME_SUBSTRINGS = ("token", "session", "credential", "secret")
FORBIDDEN_EXTENSIONS = (".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".jsonl", ".pickle")


class PreflightFailure(Exception):
    pass


def fail(msg: str) -> None:
    raise PreflightFailure(msg)


def check_python() -> None:
    got = sys.version_info[:3]
    if got != EXPECTED_PY:
        fail(f"Python {got} != expected {EXPECTED_PY} (Case 1 reference is exactly 3.11.16)")


def check_profile_and_time() -> dict:
    profile = os.environ.get("C0_PROFILE", "")
    if profile not in PROFILES:
        fail(f"C0_PROFILE={profile!r} — must be one of {sorted(PROFILES)}")
    spec = PROFILES[profile]

    tz = os.environ.get("TZ", "")
    if tz != spec["tz"]:
        fail(f"profile {profile!r} expects TZ={spec['tz']!r}, got TZ={tz!r}")

    got_date = dt.datetime.fromtimestamp(FIXED_INSTANT).date().isoformat()
    if got_date != spec["fixed_instant_date"]:
        fail(
            f"profile {profile!r}: fixed-instant local-time probe gave {got_date}, "
            f"expected {spec['fixed_instant_date']} — TZ is not actually being honoured "
            "by the C library (check that tzdata is installed and TZ is exported)"
        )
    return {"profile": profile, "tz": tz, "fixed_instant_date": got_date}


def check_dangerous_env() -> None:
    bp = os.environ.get("BROKER_PROVIDER", "none").strip().lower() or "none"
    if bp != "none":
        fail(f"BROKER_PROVIDER={bp!r} — must be 'none' or unset for C0")
    rte = os.environ.get("ROBINHOOD_TRADING_ENABLED", "false").strip().lower() or "false"
    if rte not in ("false", "0", "no", "off"):
        fail(f"ROBINHOOD_TRADING_ENABLED={rte!r} — must be false/unset for C0")
    for k in DANGEROUS_EXACT:
        if os.environ.get(k):
            fail(f"{k} is set — credentials/live-broker vars must never be set for C0")
    for k, v in os.environ.items():
        if k.endswith("_API_KEY") and v:
            fail(f"{k} is set — provider keys are not used in this gate's smoke tests")


def check_unhashed_tuning_env() -> None:
    for k in UNHASHED_TUNING_KEYS:
        if os.environ.get(k):
            fail(f"{k} is set — an unhashed tuning var would silently change behaviour "
                 "without changing config_version (see C0_INVENTORY.md §5.4)")
    for k in os.environ:
        if k.startswith(UNHASHED_TUNING_PREFIXES):
            fail(f"{k} is set — matches an unhashed tuning-var prefix {UNHASHED_TUNING_PREFIXES}")


def check_config_version() -> str:
    sys.path.insert(0, os.path.join(ROOT, "lab"))
    from paper import db  # noqa: E402  (path must be inserted first)

    for k in HASHED_KEYS:
        if k in os.environ and k != "PAPER_DATA_DIR":  # PAPER_DATA_DIR is not hashed; not in this list anyway
            fail(f"{k} is set — this gate's default-env containers must reproduce "
                 f"{EXPECTED_DEFAULT_CONFIG_VERSION} exactly")
    got = db.config_version()
    if got != EXPECTED_DEFAULT_CONFIG_VERSION:
        fail(f"config_version()={got!r} != expected {EXPECTED_DEFAULT_CONFIG_VERSION!r}")
    return got


def check_paths(profile: str) -> dict:
    home = os.environ.get("HOME", "")
    if home != "/home/tv":
        fail(f"HOME={home!r} != expected /home/tv")
    sys.path.insert(0, os.path.join(ROOT, "lab"))
    from paper import db  # noqa: E402

    resolved_db_path = db.db_path()
    resolved_data_dir = os.path.expanduser("~/.tradingview_mcp_data")
    if profile == "case1":
        pdd = os.environ.get("PAPER_DATA_DIR", "")
        if not pdd.startswith("/data/case1/"):
            fail(f"case1 PAPER_DATA_DIR={pdd!r} does not resolve under /data/case1/")
        if not resolved_db_path.startswith("/data/case1/"):
            fail(f"case1 db_path()={resolved_db_path!r} does not resolve under /data/case1/")
    else:
        if os.environ.get("PAPER_DATA_DIR"):
            fail("case2 must not set PAPER_DATA_DIR (mirrors the host, which never sets it)")
        if not resolved_db_path.startswith("/home/tv/.tradingview_mcp_data/"):
            fail(f"case2 db_path()={resolved_db_path!r} does not resolve under /home/tv/.tradingview_mcp_data/")
    for d in REQUIRED_DIRS:
        p = os.path.join(ROOT, d)
        if not os.path.isdir(p):
            fail(f"required directory missing: {p}")
    return {"db_path": resolved_db_path, "state_dir": resolved_data_dir}


def check_no_secrets_in_app() -> None:
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git")]
        for f in filenames:
            low = f.lower()
            if low == ".env" or low.startswith(".env."):
                fail(f"found .env-shaped file inside the image: {os.path.join(dirpath, f)}")
            if any(s in low for s in FORBIDDEN_BASENAME_SUBSTRINGS):
                fail(f"found token/session/credential/secret-shaped filename inside the image: "
                     f"{os.path.join(dirpath, f)}")
            if low.endswith(FORBIDDEN_EXTENSIONS):
                fail(f"found a database/JSONL/pickle file baked into the image: "
                     f"{os.path.join(dirpath, f)}")


def pip_freeze_hash() -> str:
    try:
        from importlib.metadata import distributions
        pairs = sorted(f"{d.metadata['Name']}=={d.version}" for d in distributions())
        return hashlib.sha256("\n".join(pairs).encode()).hexdigest()[:16]
    except Exception as e:  # noqa: BLE001 — evidence-only, must never block startup
        return f"ERROR:{type(e).__name__}"


def main() -> int:
    try:
        check_python()
        tinfo = check_profile_and_time()
        check_dangerous_env()
        check_unhashed_tuning_env()
        cv = check_config_version()
        pinfo = check_paths(tinfo["profile"])
        check_no_secrets_in_app()
    except PreflightFailure as e:
        print(f"C0 PREFLIGHT FAILED: {e}", file=sys.stderr)
        return 1

    evidence = {
        "preflight": "PASS",
        "python": ".".join(map(str, sys.version_info[:3])),
        **tinfo,
        "config_version": cv,
        **pinfo,
        "pip_freeze_sha256_16": pip_freeze_hash(),
    }
    print("C0 PREFLIGHT:", evidence, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
