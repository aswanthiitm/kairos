"""
Scoring the ranker the way the decision is actually made.

The metric that matters is not AUC over rows. A user is shown ONE episode with a
handful of competing candidates and acts on the top of that list, so the question
is per-episode: did the driver that turned out to matter come first?

Three arms, on identical candidates:

    heuristic   share-weighted ladder confidence - what ships today
    ml          the learned ranker alone
    fused       heuristic x the bounded ML multiplier - what actually ships

Plus two things a ranking metric will not tell you:

    calibration   does "0.8" happen 80% of the time (Brier, ECE, reliability)
    abstention    on episodes where NOTHING was planted, does the model stay
                  quiet? A ranker that always produces a confident winner is
                  worse than useless in an engine whose main claim is that it
                  refuses to guess.
"""
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from .calibration import brier, expected_calibration_error
from .gbdt import auc, ndcg_at_k


def rank_metrics(df: pd.DataFrame, score_col: str, k: int = 3) -> Dict[str, Any]:
    """Per-episode ranking quality over episodes that have a true driver present."""
    top1, hitk, mrr, n = [], [], [], 0
    for _, g in df.groupby("episode_id"):
        if not (g["label"] > 0).any():
            continue
        n += 1
        o = g.sort_values(score_col, ascending=False)
        lab = o["label"].values
        top1.append(float(lab[0] > 0))
        hitk.append(float((lab[:k] > 0).any()))
        pos = np.flatnonzero(lab > 0)
        mrr.append(1.0 / (pos[0] + 1) if len(pos) else 0.0)
    return {
        "episodes_scored": n,
        "top1_accuracy": round(float(np.mean(top1)), 4) if n else None,
        "hit_at_%d" % k: round(float(np.mean(hitk)), 4) if n else None,
        "mrr": round(float(np.mean(mrr)), 4) if n else None,
        "ndcg_at_%d" % k: round(ndcg_at_k(df["label"].values, df[score_col].values,
                                          df["episode_id"].values, k), 4),
    }


def calibration_metrics(p: Sequence[float], y: Sequence[float]) -> Dict[str, Any]:
    p, y = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    ece = expected_calibration_error(p, y)
    return {"brier": round(brier(p, y), 4), "ece": ece["ece"],
            "auc": round(auc(y, p), 4), "reliability": ece["bins"],
            "base_rate": round(float(y.mean()), 4)}


def abstention_quality(df: pd.DataFrame, prob_col: str = "ml_prob") -> Dict[str, Any]:
    """Does the model stay quiet when there is nothing to find?

    Reported as the separation between the top candidate's probability on
    episodes with a planted driver and on episodes with none. If those two
    distributions overlap, the probability cannot support an abstention rule and
    the engine should keep abstaining on evidence alone.
    """
    tops, flags = [], []
    for _, g in df.groupby("episode_id"):
        tops.append(float(g[prob_col].max()))
        flags.append(float((g["label"] > 0).any()))
    tops, flags = np.array(tops), np.array(flags)
    real, null = tops[flags > 0], tops[flags <= 0]
    return {
        "episodes": int(len(tops)),
        "episodes_with_a_true_driver": int(flags.sum()),
        "mean_top_probability_when_driver_present": round(float(real.mean()), 4) if len(real) else None,
        "mean_top_probability_when_none_planted": round(float(null.mean()), 4) if len(null) else None,
        "separation_auc": round(auc(flags, tops), 4),
        "reading": ("separation_auc is how well the top candidate's probability alone "
                    "distinguishes an episode that has a driver from one that does not; "
                    "0.5 means it cannot, and the engine must keep abstaining on the "
                    "evidence ladder alone"),
    }


def compare(df: pd.DataFrame, arms: Optional[Dict[str, str]] = None,
            k: int = 3) -> Dict[str, Any]:
    arms = arms or {"heuristic": "heuristic_score", "ml": "ml_score",
                    "fused": "fused_score"}
    return {name: rank_metrics(df, col, k) for name, col in arms.items()
            if col in df.columns}


def by_driver_type(df: pd.DataFrame, arms: Optional[Dict[str, str]] = None
                   ) -> Dict[str, Any]:
    """Top-1 per TRUE driver type.

    The headline gap between the arms is partly a base-rate effect: the shipped
    heuristic multiplies by explanatory power, and a competitor promotion or a
    price move has no share of the movement attributed to it, so it sits on the
    0.02 floor no matter how good its evidence is. Any learned ranker will beat
    that on those episodes. Splitting by the driver that was actually planted is
    what separates "the model learned something" from "the model learned which
    driver type is common".
    """
    arms = arms or {"heuristic": "heuristic_score", "ml": "ml_score",
                    "fused": "fused_score"}
    truth: Dict[str, List[Any]] = {}
    for eid, g in df.groupby("episode_id"):
        pos = g[g["label"] > 0]
        if not len(pos):
            continue
        truth.setdefault(str(pos.iloc[0]["driver_type"]), []).append(g)
    out: Dict[str, Any] = {}
    for t, groups in sorted(truth.items()):
        sub = pd.concat(groups)
        row: Dict[str, Any] = {"episodes": len(groups)}
        for name, col in arms.items():
            if col in sub.columns:
                row[name] = rank_metrics(sub, col)["top1_accuracy"]
        out[t] = row
    return out


def render(report: Dict[str, Any]) -> str:
    """Plain-text scorecard - the thing a reviewer actually reads."""
    L: List[str] = []
    tr = report.get("split", {})
    L.append("DRIVER RANKER - time-based holdout")
    L.append("  train    %s  %4d episodes / %4d candidates / %3d positives"
             % (tr.get("train_window", "?"), tr.get("train_episodes", 0),
                tr.get("train_rows", 0), tr.get("train_positives", 0)))
    L.append("  calibrate%s  %4d episodes"
             % (" " + str(tr.get("calib_window", "?")), tr.get("calib_episodes", 0)))
    L.append("  test     %s  %4d episodes / %4d candidates / %3d positives"
             % (tr.get("test_window", "?"), tr.get("test_episodes", 0),
                tr.get("test_rows", 0), tr.get("test_positives", 0)))
    L.append("")
    L.append("  %-10s %10s %10s %8s %10s" % ("arm", "top-1", "hit@3", "MRR", "NDCG@3"))
    for name, m in report.get("ranking", {}).items():
        L.append("  %-10s %10s %10s %8s %10s"
                 % (name,
                    "%.3f" % m["top1_accuracy"] if m["top1_accuracy"] is not None else "-",
                    "%.3f" % m["hit_at_3"] if m.get("hit_at_3") is not None else "-",
                    "%.3f" % m["mrr"] if m["mrr"] is not None else "-",
                    "%.3f" % m["ndcg_at_3"]))
    c = report.get("calibration", {})
    if c:
        L.append("")
        L.append("  calibration   Brier %.4f   ECE %.4f   AUC %.4f   base rate %.3f"
                 % (c["brier"], c["ece"], c["auc"], c["base_rate"]))
    a = report.get("abstention", {})
    if a:
        L.append("  abstention    top-p when a driver exists %.3f vs %.3f when none "
                 "(separation AUC %.3f)"
                 % (a["mean_top_probability_when_driver_present"] or 0.0,
                    a["mean_top_probability_when_none_planted"] or 0.0,
                    a["separation_auc"]))
    bt = report.get("by_driver_type")
    if bt:
        L.append("")
        L.append("  top-1 by the driver that was actually planted:")
        L.append("      %-18s %6s %10s %8s %8s" % ("driver_type", "eps", "heuristic",
                                                   "ml", "fused"))
        for t, m in bt.items():
            L.append("      %-18s %6d %10s %8s %8s"
                     % (t, m["episodes"],
                        "%.3f" % m.get("heuristic", 0.0), "%.3f" % m.get("ml", 0.0),
                        "%.3f" % m.get("fused", 0.0)))
    r = report.get("candidate_recall")
    if r:
        L.append("")
        L.append("  candidate-generation recall (the ceiling on any ranker): %.3f"
                 % r["overall"])
        for t, v in sorted(r.get("by_driver_type", {}).items()):
            L.append("      %-18s %.2f  (%d planted)" % (t, v["recall"], v["planted"]))
    return "\n".join(L)
