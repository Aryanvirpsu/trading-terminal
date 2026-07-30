"""FRED (St. Louis Fed) free API — macro / rates regime layer.

Rates and the yield curve set the risk backdrop the whole book trades into. This
pulls a few key series and turns them into a macro regime read: an inverted curve
+ rising rates = risk-off headwind; a steepening curve + falling rates = tailwind.
Generous free limits. Cached 6h (macro moves slowly).
"""
from __future__ import annotations
import json, os, sys, time, urllib.request
from typing import Any, Dict

sys.path.insert(0, os.path.dirname(__file__))
from _config import FRED_KEY

_CACHE: Dict[str, tuple] = {}
_TTL = 21600.0
SERIES = {"DGS10": "10y", "DGS2": "2y", "VIXCLS": "vix", "FEDFUNDS": "fedfunds"}


def _latest(series_id: str):
    url = (f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}"
           f"&api_key={FRED_KEY}&file_type=json&limit=2&sort_order=desc")
    d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "lab"}), timeout=12))
    obs = [o for o in d.get("observations", []) if o.get("value") not in (".", None)]
    return float(obs[0]["value"]) if obs else None


def macro_regime() -> Dict[str, Any]:
    if not FRED_KEY:
        return {"available": False}
    now = time.time()
    hit = _CACHE.get("macro")
    if hit and now - hit[0] < _TTL:
        return hit[1]
    vals = {}
    for sid, name in SERIES.items():
        try:
            vals[name] = _latest(sid)
        except Exception:
            vals[name] = None
    spread = (vals.get("10y") - vals.get("2y")) if (vals.get("10y") and vals.get("2y")) else None
    # Simple macro tilt: inverted curve = risk-off; positive/steep = risk-on.
    tilt = 0.0
    if spread is not None:
        tilt = max(-1, min(1, spread / 1.0))       # +1 at +100bp steep, -1 at -100bp inverted
    vix = vals.get("vix")
    if vix and vix > 25: tilt -= 0.3               # high VIX = risk-off
    out = {"available": True, **vals, "yield_curve_10y_2y": round(spread, 2) if spread else None,
           "macro_tilt": round(max(-1, min(1, tilt)), 2),
           "read": ("risk-off" if tilt < -0.2 else "risk-on" if tilt > 0.2 else "neutral")}
    _CACHE["macro"] = (now, out)
    return out


def macro_signal(direction: str = "LONG") -> Dict[str, Any]:
    m = macro_regime()
    if not m.get("available"):
        return {"dir": 0.0, "conf": 0.0, "detail": "no FRED key"}
    d = m["macro_tilt"] * (1 if direction == "LONG" else -1)
    return {"dir": round(d, 2), "conf": 0.5,
            "detail": f"curve {m.get('yield_curve_10y_2y')} VIX {m.get('vix')} -> {m['read']}"}


if __name__ == "__main__":
    import pprint
    pprint.pp(macro_regime())
