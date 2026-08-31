"""
The learning loop.

Analysts grade narratives and correct drivers. Three things happen:

  1. the grade is stored as a labelled example (the evaluation set grows itself)
  2. a rejected hypothesis has its prior down-weighted, so it ranks lower next time
  3. when an outcome is recorded against a recommendation, the playbook's measured
     effect size is re-estimated as a weighted mean over observations

This is what makes the system get better at THIS company rather than in general,
and it is the asset a platform vendor cannot ship pre-built.
"""
import json, os, copy
from datetime import datetime
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "runtime")
os.makedirs(STORE, exist_ok=True)
FEEDBACK = os.path.join(STORE, "feedback.jsonl")
PRIORS = os.path.join(STORE, "priors.json")
PLAYBOOKS = os.path.join(STORE, "playbooks_learned.json")


def _read_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def record(run_id: str, persona: str, kpi: str, hypothesis_id: str,
           grade: str, correction: Optional[str] = None,
           analyst: str = "analyst") -> Dict[str, Any]:
    """grade in {accept, reject, partial}."""
    row = {"ts": datetime.utcnow().isoformat(), "run_id": run_id, "persona": persona,
           "kpi": kpi, "hypothesis_id": hypothesis_id, "grade": grade,
           "correction": correction, "analyst": analyst}
    with open(FEEDBACK, "a") as f:
        f.write(json.dumps(row) + "\n")

    priors = _read_json(PRIORS, {})
    cur = priors.get(hypothesis_id, {"weight": 1.0, "accepts": 0, "rejects": 0})
    if grade == "accept":
        cur["accepts"] += 1
        cur["weight"] = min(1.6, cur["weight"] * 1.12)
    elif grade == "reject":
        cur["rejects"] += 1
        cur["weight"] = max(0.35, cur["weight"] * 0.75)
    priors[hypothesis_id] = cur
    with open(PRIORS, "w") as f:
        json.dump(priors, f, indent=2)
    return {"stored": row, "prior": cur}


def priors() -> Dict[str, Any]:
    return _read_json(PRIORS, {})


def record_outcome(playbook_id: str, realised_recovery_pct: float,
                   base_playbooks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Close the loop: an intervention landed, here is what it actually achieved.
    The playbook's effect size becomes a weighted mean over observations, so
    recommendations are calibrated by this company's own track record."""
    learned = _read_json(PLAYBOOKS, {})
    base = next((p for p in base_playbooks if p["id"] == playbook_id), None)
    if base is None:
        raise KeyError(playbook_id)
    key = ("at_risk_volume_recovered_pct" if "at_risk_volume_recovered_pct" in base["outcome"]
           else "asp_recovered_pct" if "asp_recovered_pct" in base["outcome"]
           else "volume_defended_pct")
    rec = learned.get(playbook_id) or {"n": base.get("n_observations", 1),
                                       "value": base["outcome"][key], "key": key,
                                       "history": []}
    n_new = rec["n"] + 1
    rec["value"] = (rec["value"] * rec["n"] + realised_recovery_pct) / n_new
    rec["n"] = n_new
    rec["history"].append({"ts": datetime.utcnow().isoformat(),
                           "realised": realised_recovery_pct})
    learned[playbook_id] = rec
    with open(PLAYBOOKS, "w") as f:
        json.dump(learned, f, indent=2)
    return rec


def apply_learning(base_playbooks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Overlay measured outcomes onto the seed playbooks."""
    learned = _read_json(PLAYBOOKS, {})
    out = copy.deepcopy(base_playbooks)
    for pb in out:
        rec = learned.get(pb["id"])
        if rec:
            pb["outcome"][rec["key"]] = round(rec["value"], 4)
            pb["n_observations"] = rec["n"]
            pb["outcome"]["confidence"] = ("high" if rec["n"] >= 3 else
                                           pb["outcome"].get("confidence", "medium"))
    return out


def stats() -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    if os.path.exists(FEEDBACK):
        with open(FEEDBACK) as f:
            rows = [json.loads(l) for l in f if l.strip()]
    g: Dict[str, int] = {}
    for r in rows:
        g[r["grade"]] = g.get(r["grade"], 0) + 1
    acc = g.get("accept", 0)
    total = sum(g.values())
    return {"total_graded": total, "by_grade": g,
            "diagnosis_precision": round(acc / total, 3) if total else None,
            "priors": priors(),
            "learned_playbooks": _read_json(PLAYBOOKS, {})}
