"""CH-005 integrity: the pre-registration, code and data match their locks; results (if present) were produced under those locks."""
import hashlib
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXP = os.path.join(ROOT, "experiments", "ch005_pullback_uptrend")


def _sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def test_prereg_matches_its_lock():
    lock = json.load(open(os.path.join(EXP, "PREREG.lock.json")))
    assert _sha(os.path.join(EXP, "PREREG.md")) == lock["prereg_sha256"]


def test_code_and_data_match_the_freeze():
    cl = json.load(open(os.path.join(EXP, "CODE.lock.json")))["sha256"]
    paths = {"engine.py": os.path.join(EXP, "engine.py"), "ch005.py": os.path.join(EXP, "ch005.py"),
             "tests/unit/test_ch005_engine.py": os.path.join(ROOT, "tests", "unit", "test_ch005_engine.py"),
             "data/prices_daily.json.gz": os.path.join(EXP, "data", "prices_daily.json.gz"), "data/universe.json": os.path.join(EXP, "data", "universe.json")}
    for k, p in paths.items():
        assert _sha(p) == cl[k], k
    lock = json.load(open(os.path.join(EXP, "PREREG.lock.json")))["data_sha256"]
    assert lock["prices_daily.json.gz"] == cl["data/prices_daily.json.gz"] and lock["universe.json"] == cl["data/universe.json"]


def test_results_were_produced_by_the_frozen_engine_and_never_auto_promote():
    p = os.path.join(EXP, "ch005_results.json")
    if not os.path.exists(p):
        return
    r = json.load(open(p))
    assert r["engine_sha256"] == json.load(open(os.path.join(EXP, "CODE.lock.json")))["sha256"]["engine.py"]
    assert r["verdict"]["auto_promote"] is False
    assert set(r["periods"]) == {"train", "validation", "test"}
