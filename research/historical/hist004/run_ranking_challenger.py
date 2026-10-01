"""HIST-004 step 2: apply the pre-registered ranking rules (Amendment 2) to every genuine choice episode
in EXP-DD-001's complete baseline where Champion actually used at least one slot among the competing
candidates (the 93 of 720 genuine episodes with an observable decision -- episodes where Champion took
ZERO of the competing candidates have no slot to reassign and are excluded, keeping this strictly a
selection-quality test, never a capacity test -- see Amendment 2).

Every candidate -- including whichever Champion actually selected -- is resolved via the SAME bar-by-bar
H6 method (`resolve_hypothetical`, decision-time price/stop/target from `decision_capture` at the episode's
first cycle, no hindsight). This is deliberate: mixing Champion's real, executed dollar P&L (which requires
`planned_risk` to convert to R, and is missing for ~30% of trades -- the known, separately-tracked
provenance gap) with a bar-by-bar hypothetical R for every other candidate would bias the comparison toward
whichever method happens to be used for Champion's own pick. Resolving every candidate identically keeps the
comparison on a clean, uniform, hindsight-free footing and sidesteps the planned_risk gap entirely for this
experiment, per explicit instruction not to let it block discovery.

For an episode where Champion used K slots, each ranking rule's picks are its own top-K candidates by score
(generalizes the common K=1 case, 88 of 93 episodes, to the 5 episodes where Champion took 2 of the
competing candidates).

Result (2026-10-01): COMPLETE -- NOT PROMOTED. See research/historical/reports/HIST_004_RESULTS.md. The
pickled EXP-DD-001 result this script reads is a large run artifact, intentionally not committed to the
repo -- pass its path via HIST004_INPUT_PKL, or regenerate it with
research/historical/hist001/baseline.py's run_baseline(disable_drawdown_gate=True) over
2024-01-01..2026-03-10.
"""
import datetime as dt
import json
import os
import pickle
import sys

REPO_ROOT = os.environ["REPO_ROOT"]
sys.path.insert(0, REPO_ROOT)

from research.historical.clock import HistoricalClock
from research.historical.hist001.build_full_dataset import INTRADAY_DATASET_ID
from research.historical.hist004.episodes import genuine_choice_episodes
from research.historical.hist004.ranking_challenger import _planned_rr
from research.historical.outcomes import resolve_hypothetical
from research.historical.provider import HistoricalMarketProvider

RESULT_PATH = os.environ.get("HIST004_INPUT_PKL") or os.path.join(
    os.path.dirname(__file__), "exp_dd_001_v2_result.pkl")
with open(RESULT_PATH, "rb") as fh:
    result = pickle.load(fh)

end_clock = HistoricalClock(dt.datetime.fromisoformat(result["cycles"][-1]["et_time"]) + dt.timedelta(minutes=5))
provider = HistoricalMarketProvider(end_clock, [INTRADAY_DATASET_ID])
cycles_by_id = {c["cycle_id"]: c for c in result["cycles"]}

episodes = genuine_choice_episodes(result)
decision_episodes = [e for e in episodes if len(e.selected_event_ids) >= 1]
print(f"genuine episodes: {len(episodes)}, with an observable decision (>=1 slot used): {len(decision_episodes)}",
     file=sys.stderr)

RULE_SCORES = {
    "quality": lambda c: (c.get("quality") or 0.0),
    "planned_rr": lambda c: _planned_rr(c),
    "composite": lambda c: (c.get("quality") or 0.0) * _planned_rr(c),
}


def _top_k_picks(candidates, score_fn, k):
    ordered = sorted(candidates, key=lambda c: (score_fn(c), c["event_id"]), reverse=True)
    return [c["event_id"] for c in ordered[:k]]


totals_r = {"champion": 0.0, "random_ev": 0.0, **{k: 0.0 for k in RULE_SCORES}}
n_resolved_episodes = 0
changes = {k: 0 for k in RULE_SCORES}
changes_improved = {k: 0 for k in RULE_SCORES}
changes_hurt = {k: 0 for k in RULE_SCORES}
by_year = {}
episode_rows = []

for ep in decision_episodes:
    first_cycle = cycles_by_id[ep.first_cycle_id]
    decision_time = dt.datetime.fromisoformat(first_cycle["et_time"])
    k = len(ep.selected_event_ids)

    candidates = []
    skip_episode = False
    for eid in ep.event_ids:
        symbol = ep.candidate_detail_by_event[eid]["symbol"]
        key = f"{ep.first_cycle_id}|{symbol}"
        rec = result["decision_capture"].get(key)
        if rec is None or rec.get("price") is None or rec.get("stop") is None or rec.get("target") is None:
            skip_episode = True
            break
        candidates.append({"event_id": eid, "symbol": symbol, "quality": rec.get("quality"),
                           "price": rec["price"], "stop": rec["stop"], "target": rec["target"],
                           "direction": "LONG"})
    if skip_episode or len(candidates) < 2 or k >= len(candidates):
        continue   # k >= len(candidates) means there was no genuine exclusion to reassign

    outcome_r = {}
    for c in candidates:
        try:
            h = resolve_hypothetical(provider, c["symbol"],
                                     {"price": c["price"], "stop": c["stop"], "target": c["target"],
                                      "direction": c["direction"]}, decision_time)
        except Exception:
            h = None
        outcome_r[c["event_id"]] = h.net_r if (h is not None and h.resolved and h.net_r is not None) else None

    if any(v is None for v in outcome_r.values()):
        continue    # can't compare on a common footing if any candidate's R is unavailable

    n_resolved_episodes += 1
    champion_picks = set(ep.selected_event_ids)
    champion_r = sum(outcome_r[e] for e in champion_picks)
    totals_r["champion"] += champion_r

    random_ev = (sum(outcome_r.values()) / len(candidates)) * k   # expected sum of k uniform random picks
    totals_r["random_ev"] += random_ev

    year = ep.session_date[:4]
    by_year.setdefault(year, {"champion": 0.0, **{k2: 0.0 for k2 in RULE_SCORES}})
    by_year[year]["champion"] += champion_r

    row = {"session_date": ep.session_date, "symbols": ep.symbols, "k": k,
          "champion_picks": [ep.candidate_detail_by_event[e]["symbol"] for e in champion_picks],
          "champion_r": round(champion_r, 4)}

    for rule_name, score_fn in RULE_SCORES.items():
        picks = set(_top_k_picks(candidates, score_fn, k))
        rule_r = sum(outcome_r[e] for e in picks)
        totals_r[rule_name] += rule_r
        by_year[year][rule_name] += rule_r
        row[f"{rule_name}_picks"] = [next(c["symbol"] for c in candidates if c["event_id"] == e) for e in picks]
        row[f"{rule_name}_r"] = round(rule_r, 4)
        if picks != champion_picks:
            changes[rule_name] += 1
            if rule_r > champion_r:
                changes_improved[rule_name] += 1
            elif rule_r < champion_r:
                changes_hurt[rule_name] += 1
    episode_rows.append(row)

print(f"\nepisodes with every candidate resolvable on a common R footing: {n_resolved_episodes}", file=sys.stderr)
print("\n=== TOTAL R BY RULE ===")
for k_, v in totals_r.items():
    print(f"  {k_}: {round(v, 3)}  (per-episode: {round(v / n_resolved_episodes, 4) if n_resolved_episodes else None})")

print("\n=== HOW OFTEN EACH RULE CHANGES THE PICK(S), AND WITH WHAT EFFECT ===")
for k_ in RULE_SCORES:
    neutral = changes[k_] - changes_improved[k_] - changes_hurt[k_]
    print(f"  {k_}: changed {changes[k_]}/{n_resolved_episodes} -- improved {changes_improved[k_]}, "
         f"hurt {changes_hurt[k_]}, neutral (changed pick, same R) {neutral}")

print("\n=== BY YEAR (total R) ===")
print(json.dumps(by_year, indent=2))

OUT_PATH = os.path.join(os.path.dirname(__file__), "results", "hist004_ranking_result.json")
with open(OUT_PATH, "w", encoding="utf-8") as fh:
    json.dump({"n_genuine_episodes": len(episodes), "n_decision_episodes": len(decision_episodes),
              "n_resolved_episodes": n_resolved_episodes, "totals_r": totals_r, "changes": changes,
              "changes_improved": changes_improved, "changes_hurt": changes_hurt, "by_year": by_year,
              "episodes": episode_rows}, fh, indent=2, default=str)
print(f"\nfull detail written to {OUT_PATH}", file=sys.stderr)
print("HIST004_RANKING_DONE_FINAL", file=sys.stderr)
