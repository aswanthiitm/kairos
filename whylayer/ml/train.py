"""
End-to-end trainer for the driver ranker.

    python -m whylayer.ml.train --build          rebuild the corpus, then train
    python -m whylayer.ml.train                  train from the existing corpus
    python -m whylayer.ml.train --include-feedback   fold in analyst-graded runs

The sequence is fixed and the order matters:

    corpus -> TIME split -> hyper-parameter search on the CALIBRATION slice
           -> refit -> isotonic calibration on that same slice
           -> evaluate once on the TEST slice -> write the model card

The test slice is touched exactly once, at the end. Selecting a model on the
data you then report it on is the most common way a ranking result turns out to
be an artefact, and this system's whole argument is that it does not do that
kind of thing.
"""
import argparse
import json
import os
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .calibration import Isotonic, brier, expected_calibration_error
from .features import FEATURE_CONTRACT_VERSION, FEATURES, N_FEATURES, describe
from .gbdt import HistGBDT, ndcg_at_k
from . import dataset as DS
from . import evaluate as EV
from .ranker import DEFAULT_MODEL, DEFAULT_ALPHA, fuse_episode, save_card

MODEL_VERSION = "driver-ranker-v1"
FEEDBACK_LABELS = os.path.join(DS.RUNTIME, "ml_labels.jsonl")
SNAPSHOT_DIR = os.path.join(DS.RUNTIME, "feature_snapshots")

GRID = [
    {"max_depth": d, "learning_rate": lr, "min_child_weight": mcw}
    for d in (3, 4) for lr in (0.05, 0.10) for mcw in (2.0, 6.0)
]
ALPHA_GRID = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]   # capped at ranker.MAX_ALPHA by governance


def apply_fusion(ev: pd.DataFrame, alpha: float) -> np.ndarray:
    """Fused score for every row, computed one episode at a time because the
    normalisation in ``fuse_episode`` is only defined within an episode."""
    ev = ev.reset_index(drop=True)
    out = np.zeros(len(ev), dtype=np.float64)
    for _, g in ev.groupby("episode_id"):
        out[g.index.values] = fuse_episode(g["heuristic_score"].values,
                                           g["ml_prob"].values, None, alpha)
    return out


# ------------------------------------------------------------------- feedback
def load_feedback_rows(weight: float) -> Tuple[pd.DataFrame, int]:
    """Analyst-graded candidates, joined back to the feature vector that was
    actually scored at the time. ``accept`` is a positive, ``reject`` a negative;
    ``partial`` is deliberately dropped - a half-right diagnosis is not a label,
    and guessing which half would put an opinion into the training set."""
    if not os.path.exists(FEEDBACK_LABELS):
        return pd.DataFrame(), 0
    rows: List[Dict[str, Any]] = []
    with open(FEEDBACK_LABELS) as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("grade") not in ("accept", "reject"):
                continue
            snap = os.path.join(SNAPSHOT_DIR, "%s.json" % r["run_id"])
            if not os.path.exists(snap):
                continue
            blob = json.load(open(snap))
            if blob.get("feature_contract") != FEATURE_CONTRACT_VERSION:
                continue
            feat = (blob.get("candidates") or {}).get(r["hypothesis_id"])
            if not feat:
                continue
            row = {"episode_id": "FB-%s" % r["run_id"],
                   "window_start": str(r.get("ts", ""))[:10],
                   "window_end": str(r.get("ts", ""))[:10],
                   "kpi": r.get("kpi", ""), "slice": "{}",
                   "candidate_id": r["hypothesis_id"],
                   "driver_type": feat.get("driver_type", ""),
                   "ladder": feat.get("ladder"), "null_episode": 0,
                   "n_candidates": blob.get("n_candidates", 1),
                   "heuristic_score": feat.get("heuristic_score", 0.0),
                   "feature_snapshot_id": feat.get("feature_snapshot_id"),
                   "label": 1 if r["grade"] == "accept" else 0,
                   "sample_weight": weight, "source": "analyst"}
            row.update({n: float(v) for n, v in zip(FEATURES, feat["vector"])})
            rows.append(row)
    df = pd.DataFrame(rows)
    return df, len(df)


# ------------------------------------------------------------------ recall
def candidate_recall(episode_ids: Optional[set] = None) -> Optional[Dict[str, Any]]:
    """The ceiling on any ranker: how often the true driver was even PROPOSED.

    Reported because a ranker cannot fix a candidate generator, and quoting a
    top-1 number without it would overstate what the layer can do.
    """
    if not os.path.exists(DS.EPISODES):
        return None
    meta = json.load(open(DS.EPISODES))
    df = DS.load_table()
    if episode_ids is not None:
        df = df[df.episode_id.isin(episode_ids)]
    eps = {e["episode_id"]: e for e in meta["episodes"]}
    planted, matched = Counter(), Counter()
    for eid, g in df.groupby("episode_id"):
        for d in eps.get(eid, {}).get("drivers", []):
            planted[d["type"]] += 1
        for t in g[g.label == 1].driver_type:
            matched[t] += 1
    tot_p, tot_m = sum(planted.values()), sum(matched.values())
    return {"overall": round(tot_m / tot_p, 4) if tot_p else None,
            "planted": tot_p, "matched": tot_m,
            "by_driver_type": {t: {"planted": planted[t], "matched": matched[t],
                                   "recall": round(matched[t] / planted[t], 4)}
                               for t in sorted(planted)}}


# -------------------------------------------------------------------- trainer
def train(build_corpus: bool = False, include_feedback: bool = False,
          feedback_weight: float = 5.0, objective: str = "lambdarank",
          out_path: Optional[str] = None, verbose: bool = True) -> Dict[str, Any]:
    if build_corpus:
        DS.build(verbose=verbose)
    df = DS.load_table()
    df["sample_weight"] = 1.0
    df["source"] = "bootstrap"
    n_fb = 0
    if include_feedback:
        fb, n_fb = load_feedback_rows(feedback_weight)
        if n_fb:
            df = pd.concat([df, fb], ignore_index=True)
            if verbose:
                print("folded in %d analyst-labelled candidates at weight %.1f"
                      % (n_fb, feedback_weight))

    tr, ca, te = DS.time_split(df)
    X = df[FEATURES].values.astype(np.float64)
    y = df["label"].values.astype(np.float64)
    gid = pd.factorize(df["episode_id"])[0]
    w = df["sample_weight"].values.astype(np.float64)

    # ---- hyper-parameter search, selected on the CALIBRATION slice only
    best, best_score = None, -np.inf
    for params in GRID:
        m = HistGBDT(objective=objective, n_estimators=400, subsample=0.85,
                     colsample=0.8, reg_lambda=2.0, early_stopping_rounds=60,
                     **params)
        m.fit(X[tr], y[tr], groups=gid[tr], sample_weight=w[tr],
              eval_set=(X[ca], y[ca], gid[ca]), feature_names=FEATURES)
        s = ndcg_at_k(y[ca], m.decision_function(X[ca]), gid[ca], 3)
        if verbose:
            print("  grid depth=%d lr=%.2f mcw=%.0f -> %d trees, calib NDCG@3 %.4f"
                  % (params["max_depth"], params["learning_rate"],
                     params["min_child_weight"], len(m.trees), s))
        if s > best_score:
            best, best_score, best_params = m, s, params
    model = best

    # ---- calibration on the same later-in-time slice, never on train
    raw_ca = model.decision_function(X[ca])
    cal = Isotonic().fit(raw_ca, y[ca])

    # ---- fusion weight, chosen on the calibration slice
    # The isotonic map is in-sample here, so this number is optimistic; it selects
    # one scalar and the test slice below is still untouched.
    cv = df[ca].copy().reset_index(drop=True)
    cv["ml_prob"] = cal.predict(model.decision_function(X[ca]))
    alpha, alpha_score = DEFAULT_ALPHA, -np.inf
    alpha_curve = {}
    for al in ALPHA_GRID:
        cv["fused_score"] = apply_fusion(cv, al)
        s_al = EV.rank_metrics(cv, "fused_score")["top1_accuracy"] or 0.0
        alpha_curve[al] = round(s_al, 4)
        if s_al > alpha_score:
            alpha, alpha_score = al, s_al
    if verbose:
        print("  fusion alpha search (calibration top-1): %s -> alpha=%.2f"
              % (alpha_curve, alpha))

    # ---- the single pass over the test slice
    ev = df[te].copy().reset_index(drop=True)
    ev["ml_score"] = model.decision_function(X[te])
    ev["ml_prob"] = cal.predict(ev["ml_score"].values)
    ev["fused_score"] = apply_fusion(ev, alpha)

    ranking = EV.compare(ev)
    calib = EV.calibration_metrics(ev["ml_prob"].values, ev["label"].values)
    abst = EV.abstention_quality(ev, "ml_prob")

    def _win(mask):
        d = df[mask]
        return "%s..%s" % (d.window_start.min(), d.window_start.max()) if len(d) else "-"

    split_info = {
        "train_window": _win(tr), "calib_window": _win(ca), "test_window": _win(te),
        "train_episodes": int(df[tr].episode_id.nunique()),
        "calib_episodes": int(df[ca].episode_id.nunique()),
        "test_episodes": int(df[te].episode_id.nunique()),
        "train_rows": int(tr.sum()), "test_rows": int(te.sum()),
        "train_positives": int(df[tr].label.sum()),
        "test_positives": int(df[te].label.sum()),
    }
    report = {"split": split_info, "ranking": ranking, "calibration": calib,
              "abstention": abst,
              "by_driver_type": EV.by_driver_type(ev),
              "candidate_recall": candidate_recall(set(df[te].episode_id))}

    lo = np.percentile(X[tr], 1, axis=0)
    hi = np.percentile(X[tr], 99, axis=0)
    lift = None
    if ranking.get("heuristic") and ranking.get("fused"):
        a, b = ranking["heuristic"]["top1_accuracy"], ranking["fused"]["top1_accuracy"]
        if a is not None and b is not None:
            lift = round(b - a, 4)

    card: Dict[str, Any] = {
        "model_version": MODEL_VERSION,
        "feature_contract": FEATURE_CONTRACT_VERSION,
        "trained_at": datetime.utcnow().isoformat(),
        "objective": objective,
        "selected_params": best_params,
        "selection_metric": {"on": "calibration slice", "ndcg_at_3": round(best_score, 4)},
        "training": {
            "episodes": split_info["train_episodes"], "rows": split_info["train_rows"],
            "positives": split_info["train_positives"],
            "window": split_info["train_window"],
            "bootstrap_rows": int((df[tr]["source"] == "bootstrap").sum()),
            "analyst_rows": int((df[tr]["source"] == "analyst").sum()),
            "corpus": "simulated historical estate (data/generate_history.py) replayed "
                      "through the production engine",
        },
        "holdout": report,
        "fusion": {"alpha": alpha, "rule": "(1-alpha)*heuristic_normalised + alpha*P",
                   "selected_on": "calibration slice top-1",
                   "alpha_curve": alpha_curve},
        "top1_lift_over_heuristic": lift,
        "calibration": {"isotonic": cal.to_dict(),
                        "quality": {"brier": calib["brier"], "ece": calib["ece"],
                                    "auc": calib["auc"]}},
        "feature_range": {"p01": [float(v) for v in lo], "p99": [float(v) for v in hi]},
        "feature_importance": model.feature_importance(),
        "feature_contract_detail": describe(),
        "limitations": [
            "Trained on a SIMULATED historical estate, not on this company's resolved "
            "incidents. It demonstrates that the layer learns and can be evaluated; it "
            "is not evidence of accuracy on real episodes.",
            "The ranker cannot exceed candidate-generation recall (%s on the holdout) - "
            "a driver that was never proposed can never be ranked first."
            % (report["candidate_recall"]["overall"] if report["candidate_recall"] else "n/a"),
            "Authority is advisory: reordering only. It cannot promote an evidence rung "
            "or change a verdict status.",
            "Calibrated on %d episodes; probabilities near the extremes rest on few "
            "observations." % split_info["calib_episodes"],
        ],
        "model": model.to_dict(),
    }
    path = save_card(card, out_path or DEFAULT_MODEL)
    if verbose:
        print()
        print(EV.render(report))
        print()
        print("top features: %s" % ", ".join(
            "%s %.3f" % (k, v) for k, v in list(model.feature_importance().items())[:8]))
        print("model card -> %s  (%.0f KB)" % (path, os.path.getsize(path) / 1024))
    return card


def main() -> None:
    ap = argparse.ArgumentParser(description="train the ML driver ranker")
    ap.add_argument("--build", action="store_true", help="rebuild the training corpus first")
    ap.add_argument("--include-feedback", action="store_true",
                    help="fold analyst-graded runs into the corpus")
    ap.add_argument("--feedback-weight", type=float, default=5.0)
    ap.add_argument("--objective", default="lambdarank",
                    choices=["lambdarank", "logistic"])
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    train(build_corpus=a.build, include_feedback=a.include_feedback,
          feedback_weight=a.feedback_weight, objective=a.objective, out_path=a.out)


if __name__ == "__main__":
    main()
