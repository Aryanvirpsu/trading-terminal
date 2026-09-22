"""C0 G4 dashboard wrapper — Docker networking only, no application-logic change.

Approved in C0_CONTAINER_DESIGN.md §9.3 (D2): imports dashboard/app.py exactly as
`python dashboard/app.py` would (same sys.path[0] insertion, same module-level
_prewarm() call at import time), and binds 0.0.0.0 instead of the hard-coded
127.0.0.1 in dashboard/app.py's own __main__ block — which is never reached here,
since this file calls app.run() itself instead. dashboard/app.py is not edited.
"""
import os
import sys

sys.path.insert(0, "/app/dashboard")
import app  # noqa: E402  (dashboard/app.py — runs its module-level code, incl. _prewarm())

if __name__ == "__main__":
    port = int(os.environ.get("DASHBOARD_PORT", "5057"))
    app.app.run(host="0.0.0.0", port=port, debug=False)
