"""Robinhood broker adapter.

Wraps the community SDK `robin_stocks` (PyPI — NOT an official Robinhood
library; Robinhood has never published a public trading API) behind the
BrokerAdapter interface.

READ THIS BEFORE USING:

1. NO PAPER MODE. Robinhood has no sandbox/simulated environment of any
   kind. Every order placed through this adapter — mode='live' — is a REAL
   order with REAL money, immediately. There is no safe way to "test" this
   integration short of placing an actual trade.

2. UNOFFICIAL / AGAINST ROBINHOOD'S TERMS. robin_stocks works by replaying
   Robinhood's private mobile-app API. Robinhood has not authorized this for
   third-party use, and their Terms of Service restrict automated access to
   their systems. Accounts have been flagged or restricted for this kind of
   usage before. Using this is a deliberate choice to accept that risk.

3. CREDENTIAL MODEL IS RISKIER THAN AN API KEY. This needs your actual
   Robinhood username and password (there's no OAuth/API-key flow), plus a
   TOTP code if you have authenticator-app 2FA enabled.

4. LOGIN CAN HANG IF ROBINHOOD DEMANDS INTERACTIVE VERIFICATION. On a new/
   untrusted device, Robinhood may require an SMS/email code typed into a
   live prompt, or a push notification approved in the app — robin_stocks
   implements both by blocking synchronously (calling input() or polling for
   up to 2 minutes). To keep that from ever touching this MCP server's own
   stdio transport, every call in this module runs in an isolated
   subprocess (stdin closed, hard timeout) via _robinhood_worker.py. Worst
   case here is a clean timeout error — this design guarantees it can never
   hang or corrupt the running server.

Given all of the above: this integration is provided because you explicitly
asked for it after being told about these tradeoffs, not because it's the
recommended way to get US market execution. Alpaca (official API, real
paper-trading sandbox) remains the safer alternative if you change your
mind later.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from tradingview_mcp.core.broker.base import BrokerAdapter, BrokerNotConfiguredError

_WORKER_SCRIPT = Path(__file__).parent / "_robinhood_worker.py"
_DEFAULT_TIMEOUT_S = 25

try:
    import robin_stocks  # noqa: F401  presence check
    _SDK_AVAILABLE = True
except ImportError:
    _SDK_AVAILABLE = False


def _creds() -> tuple:
    username = os.environ.get("ROBINHOOD_USERNAME", "").strip()
    password = os.environ.get("ROBINHOOD_PASSWORD", "").strip()
    return username, password


def _run_worker(action: str, args: Optional[dict] = None, mfa_code: Optional[str] = None,
                 timeout: float = _DEFAULT_TIMEOUT_S) -> Dict[str, Any]:
    """Run one robin_stocks action in an isolated subprocess.

    stdin is closed (DEVNULL) so Robinhood's interactive verification flow
    fails fast with EOFError instead of blocking. A hard timeout is the
    second layer of defense in case something still tries to poll/sleep.
    """
    if not _SDK_AVAILABLE:
        raise BrokerNotConfiguredError(
            "robin_stocks is not installed. Run: pip install robin_stocks "
            '(or `pip install -e ".[robinhood]"` from the repo root).'
        )
    username, password = _creds()
    if not username or not password:
        raise BrokerNotConfiguredError(
            "ROBINHOOD_USERNAME / ROBINHOOD_PASSWORD are not set in .env. Note there is no "
            "OAuth/API-key flow for Robinhood — this needs your actual account credentials."
        )

    env = dict(os.environ)
    env["RH_USERNAME"] = username
    env["RH_PASSWORD"] = password
    env["RH_MFA"] = mfa_code or ""
    env["RH_REQUEST_JSON"] = json.dumps({"action": action, "args": args or {}})

    try:
        proc = subprocess.run(
            [sys.executable, str(_WORKER_SCRIPT)],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"Robinhood login/action timed out after {timeout}s without completing. This "
            "almost always means Robinhood is demanding interactive verification this "
            "integration can't complete automatically (an SMS/email code typed into a live "
            "prompt, or an app push-notification approval) — this adapter only supports "
            "accounts where a TOTP (authenticator app) code passed upfront is sufficient, or "
            "an already-trusted device/session. Try approving any pending Robinhood app "
            "notification first, or check whether your account requires a device challenge."
        ) from None

    stdout = (proc.stdout or "").strip()
    if not stdout:
        raise RuntimeError(
            f"Robinhood worker produced no output (exit code {proc.returncode}). "
            f"stderr: {(proc.stderr or '')[:500]}"
        )
    try:
        # robin_stocks prints its own progress lines ("Starting login process...")
        # to stdout before our JSON result — take the LAST line, which is ours.
        last_line = stdout.splitlines()[-1]
        result = json.loads(last_line)
    except (json.JSONDecodeError, IndexError):
        raise RuntimeError(f"Could not parse Robinhood worker output: {stdout[:500]!r}") from None

    if not result.get("ok"):
        raise RuntimeError(result.get("error", "Unknown Robinhood error"))
    return result.get("data", {})


def login(mfa_code: Optional[str] = None) -> Dict[str, Any]:
    """Log into Robinhood (or validate/reuse an existing cached session).

    robin_stocks caches its own session at ~/.tokens/robinhood.pickle, so
    repeated calls are cheap once a session exists. Pass mfa_code if your
    account uses authenticator-app (TOTP) 2FA and no session is cached yet.
    """
    _run_worker("login", mfa_code=mfa_code)
    return {"status": "success", "note": "Session established (or reused from ~/.tokens/robinhood.pickle)."}


def _session_pickle_path() -> Path:
    return Path(os.path.expanduser("~/.tokens/robinhood.pickle"))


def _has_valid_session() -> bool:
    """True only if the session pickle exists, is non-empty, AND actually
    contains an access token — robin_stocks opens this file in 'wb' mode
    (truncating/creating it) BEFORE the pickle.dump() call, so a login that
    fails partway through (e.g. wrong credentials) can leave a 0-byte or
    malformed file behind that merely *existing* would wrongly count as
    "logged in". Never logs/returns the file's actual contents."""
    path = _session_pickle_path()
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        import pickle
        with open(path, "rb") as f:
            data = pickle.load(f)
        return isinstance(data, dict) and bool(data.get("access_token"))
    except Exception:
        return False


def session_status() -> Dict[str, Any]:
    valid = _has_valid_session()
    return {
        "session_cached": valid,
        "session_path": str(_session_pickle_path()),
        "hint": None if valid else "Call the robinhood_login tool to establish or refresh a session.",
    }


class RobinhoodBroker(BrokerAdapter):
    name = "robinhood"

    def place_order(
        self,
        symbol: str,
        quantity: float,
        side: str,
        order_type: str = "MARKET",
        price: Optional[float] = None,
        product: str = "MIS",
        exchange: str = "NASDAQ",
    ) -> Dict[str, Any]:
        return _run_worker("place_order", args={
            "symbol": symbol.strip().upper(),
            "quantity": quantity,
            "side": side.strip().lower(),
            "order_type": order_type.strip().lower(),
            "price": price,
        })

    def get_positions(self) -> Dict[str, Any]:
        return _run_worker("positions")

    def get_holdings(self) -> Dict[str, Any]:
        return _run_worker("holdings")

    def get_order_status(self, order_id: str) -> Dict[str, Any]:
        return _run_worker("order_status", args={"order_id": order_id})

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        return _run_worker("cancel_order", args={"order_id": order_id})
