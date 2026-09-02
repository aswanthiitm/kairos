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
ML_LABELS = os.path.join(STORE, "ml_labels.jsonl")
SNAPSHOTS = os.path.join(STORE, "feature_snapshots")


def _read_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save_feature_snapshot(run_id: str, payload: Dict[str, Any]) -> str:
    """Freeze the exact feature vectors the ranker scored on this run.

    Written at ANALYSIS time, not at feedback time. When an analyst grades this
    run - possibly weeks later, after the model has been retrained twice - the
    label has to attach to the evidence as it stood on the day, or the training
    row describes a state of the world that never existed. This file plus the
    model version is also what makes a past score reproducible.
    """
    os.makedirs(SNAPSHOTS, exist_ok=True)
    p = os.path.join(SNAPSHOTS, "%s.json" % run_id)
    with open(p, "w") as f:
        json.dump(payload, f)
    return p


def record(run_id: str, persona: str, kpi: str, hypothesis_id: str,
           grade: str, correction: Optional[str] = None,
           analyst: str = "analyst") -> Dict[str, Any]:
    """grade in {accept, reject, partial}."""
    row = {"ts": datetime.utcnow().isoformat(), "run_id": run_id, "persona": persona,
           "kpi": kpi, "hypothesis_id": hypothesis_id, "grade": grade,
           "correction": correction, "analyst": analyst}
    with open(FEEDBACK, "a") as f:
        f.write(json.dumps(row) + "\n")

    # The same grade becomes a supervised ML label when we still hold the feature
    # snapshot for that run. accept -> 1, reject -> 0; "partial" is stored for the
    # audit trail but never becomes a training row, because half a diagnosis is
    # not a label and choosing which half would be putting an opinion in the data.
    if os.path.exists(os.path.join(SNAPSHOTS, "%s.json" % run_id)):
        with open(ML_LABELS, "a") as f:
            f.write(json.dumps({"ts": row["ts"], "run_id": run_id, "kpi": kpi,
                                "persona": persona, "hypothesis_id": hypothesis_id,
                                "grade": grade, "analyst": analyst}) + "\n")

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


def reset() -> Dict[str, Any]:
    """Clear all learned state. Demos and tests must start from a known point,
    and an engine that learns needs an explicit way to forget."""
    removed = []
    for p in (FEEDBACK, PRIORS, PLAYBOOKS, ML_LABELS):
        if os.path.exists(p):
            os.remove(p); removed.append(os.path.basename(p))
    if os.path.isdir(SNAPSHOTS):
        n = 0
        for fn in os.listdir(SNAPSHOTS):
            if fn.endswith(".json"):
                os.remove(os.path.join(SNAPSHOTS, fn)); n += 1
        if n:
            removed.append("feature_snapshots (%d)" % n)
    # The trained model is deliberately NOT removed. Resetting learned state means
    # forgetting what this deployment has been told, not deleting a versioned
    # artefact that ships with the repository and is replaced by retraining.
    return {"reset": True, "removed": removed,
            "note": "analyst feedback, priors, learned playbooks and feature "
                    "snapshots cleared; the trained ranker in models/ is versioned "
                    "and is replaced by retraining, not by reset"}


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
    ml_rows = 0
    if os.path.exists(ML_LABELS):
        with open(ML_LABELS) as f:
            ml_rows = sum(1 for l in f if l.strip())
    snaps = (len([x for x in os.listdir(SNAPSHOTS) if x.endswith(".json")])
             if os.path.isdir(SNAPSHOTS) else 0)
    return {"total_graded": total, "by_grade": g,
            "diagnosis_precision": round(acc / total, 3) if total else None,
            "priors": priors(),
            "learned_playbooks": _read_json(PLAYBOOKS, {}),
            "ml_training_labels": ml_rows,
            "feature_snapshots_held": snaps,
            "ml_retrain_hint": "python -m kairos.ml.train --include-feedback"}


# ---------------------------------------------------------------- corrections
# The correction an analyst types was previously stored and never read again.
# These read it back and let it change what the engine does next.

def corrections(kpi: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every correction an analyst has written, newest first."""
    rows: List[Dict[str, Any]] = []
    if os.path.exists(FEEDBACK):
        with open(FEEDBACK) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if not (r.get("correction") or "").strip():
                    continue
                if kpi and r.get("kpi") != kpi:
                    continue
                rows.append(r)
    return list(reversed(rows))


def correction_notes(kpi: str, hypothesis_ids: List[str]) -> List[Dict[str, Any]]:
    """Corrections that bear on the hypotheses in front of us right now.

    A correction is a labelled counter-example: an analyst looked at this exact
    hypothesis on this KPI and said the engine had it wrong, and said why. It is
    surfaced on every later run of the same shape until someone acts on it —
    silently down-weighting the prior and hiding the reason would waste the most
    expensive signal in the system.
    """
    out = []
    for r in corrections(kpi):
        if r.get("hypothesis_id") in hypothesis_ids:
            out.append({
                "hypothesis_id": r["hypothesis_id"],
                "grade": r.get("grade"),
                "correction": r["correction"],
                "analyst": r.get("analyst", "analyst"),
                "recorded_at": r.get("ts"),
                "effect": "prior weight %.2f; shown on every run of this shape until "
                          "the causal graph or a playbook is amended"
                          % (priors().get(r["hypothesis_id"], {}).get("weight", 1.0)),
            })
    return out


def correction_stats() -> Dict[str, Any]:
    rows = corrections()
    by_hyp: Dict[str, int] = {}
    for r in rows:
        by_hyp[r["hypothesis_id"]] = by_hyp.get(r["hypothesis_id"], 0) + 1
    return {"total": len(rows), "by_hypothesis": by_hyp,
            "latest": rows[0] if rows else None}
