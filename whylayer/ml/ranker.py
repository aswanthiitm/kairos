"""
The ML driver-ranker at runtime.

WHAT IT IS
    A learned prior over candidate drivers, fitted on resolved historical
    episodes. It answers "of the candidates on the table, which ones have
    historically turned out to be the driver that mattered".

WHAT IT IS NOT
    An arbiter. ``AUTHORITY`` below is the enforced contract, and it is enforced
    structurally rather than by convention: this module returns a score and a
    bounded multiplier, and it has no access to the ladder, the verdict status,
    or the candidate list. It cannot promote a rung because it is never given
    one to promote.

Three gates before a score is allowed to matter
-----------------------------------------------
1. ARTEFACT PRESENT. No model file -> the engine runs exactly as it did before,
   and says so in the telemetry. A missing model is a silent no-op, never an error.
2. FEATURE CONTRACT MATCHES. The model records the feature-contract version it
   was trained under. A mismatch is refused outright rather than silently scored
   against a reordered vector.
3. IN DISTRIBUTION. Every feature carries the 1st/99th percentile seen in
   training. A candidate whose evidence sits far outside that range is scored,
   but flagged out-of-distribution and its multiplier is dropped to neutral -
   the model is not asked to extrapolate into territory it has never seen.
"""
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .calibration import Isotonic
from .features import (FEATURE_CONTRACT_VERSION, FEATURES, N_FEATURES,
                       featurize, snapshot_id)
from .gbdt import HistGBDT

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODEL_DIR = os.path.join(ROOT, "models")
DEFAULT_MODEL = os.path.join(MODEL_DIR, "driver-ranker-v1.json")

AUTHORITY = (
    "advisory-reorder-only: the ML score may reorder candidates within the set the "
    "evidence ladder already admitted, and may not promote a rung, alter a verdict "
    "status, or add or remove a candidate"
)

OOD_TOLERANCE = 0.25          # fraction of features allowed outside the train range
DEFAULT_ALPHA = 0.45          # weight on the learned term; tuned on the calibration slice
MAX_ALPHA = 0.50              # governance cap, not a tuning bound - see fuse_episode


def fuse_episode(heuristics: Sequence[float],
                 probabilities: Optional[Sequence[Optional[float]]] = None,
                 in_distribution: Optional[Sequence[bool]] = None,
                 alpha: float = DEFAULT_ALPHA) -> List[float]:
    """The single definition of the fused rank score. One episode at a time.

    BOTH terms are normalised within the episode before they are blended:

        fused = (1 - alpha) * h/max(h)  +  alpha * p/max(p)

    Two normalisations, for two different reasons.

    The heuristic is ``share x ladder confidence``. A candidate with no share
    attributed to it - a competitor promotion, a price move - is pinned at the
    0.02 floor while a warehouse carrying 70% of the movement scores thirty times
    higher. Left raw, no bounded ML term could ever reorder those, and the
    "fusion" would be the heuristic with extra steps.

    The calibrated probability is compressed toward the base rate by construction
    (isotonic on an 18%-positive corpus), so its within-episode spread is a few
    hundredths while the heuristic's is the full unit interval. Blending them raw
    would hand the heuristic the argument through scale rather than through
    weight, which is not a decision anyone made.

    So both become a within-episode ranking on [0, 1] and ``alpha`` is then an
    honest statement of how much say each one has.

    ``alpha`` is fitted on the calibration slice and CAPPED AT ``MAX_ALPHA``.
    The cap is a governance constraint, not a hyper-parameter: the heuristic term
    carries the evidence ladder, which the model is deliberately never shown, and
    the evidence-bearing term keeps at least half the weight. A search that wants
    to go higher is told no.

    This is still reordering and nothing else. The candidate set, the rungs and
    the verdict status are all settled before this function is called, and none
    of them is an argument to it.
    """
    alpha = float(min(max(alpha, 0.0), MAX_ALPHA))
    h = np.asarray(list(heuristics), dtype=np.float64)
    if probabilities is None:
        return [float(v) for v in h]
    p = np.array([np.nan if v is None else float(v) for v in probabilities],
                 dtype=np.float64)
    ok = (np.ones(len(h), dtype=bool) if in_distribution is None
          else np.asarray(list(in_distribution), dtype=bool))
    ok &= np.isfinite(p)
    if not ok.any():
        return [float(v) for v in h]
    hn = np.abs(h) / (float(np.max(np.abs(h))) or 1.0)
    pv = np.where(ok, np.nan_to_num(p), 0.0)
    pn = pv / (float(np.max(pv)) or 1.0)
    out = np.where(ok, (1.0 - alpha) * hn + alpha * pn, hn)
    return [float(v) for v in out]


def fuse(heuristic_score: float, probability: Optional[float],
         in_distribution: bool = True, alpha: float = DEFAULT_ALPHA) -> float:
    """Single-candidate convenience wrapper. Only meaningful when the candidate
    is alone, since normalisation is defined over the episode."""
    return fuse_episode([heuristic_score], [probability], [in_distribution], alpha)[0]


class DriverRanker(object):
    def __init__(self, card: Optional[Dict[str, Any]] = None,
                 path: Optional[str] = None):
        self.path = path
        self.card = card or {}
        self.model: Optional[HistGBDT] = None
        self.calibrator: Optional[Isotonic] = None
        self.lo: Optional[np.ndarray] = None
        self.hi: Optional[np.ndarray] = None
        self.alpha: float = DEFAULT_ALPHA
        self.status = "unavailable"
        self.reason = "no model artefact found at %s" % (path or DEFAULT_MODEL)
        if not card:
            return
        if card.get("feature_contract") != FEATURE_CONTRACT_VERSION:
            self.status = "refused"
            self.reason = ("model was trained against feature contract %r but this "
                           "engine runs %r; scoring is refused rather than run against "
                           "a vector the model never saw"
                           % (card.get("feature_contract"), FEATURE_CONTRACT_VERSION))
            return
        if list(card.get("model", {}).get("feature_names") or []) != FEATURES:
            self.status = "refused"
            self.reason = "feature names in the artefact do not match the contract order"
            return
        self.model = HistGBDT.from_dict(card["model"])
        self.calibrator = Isotonic.from_dict(card["calibration"]["isotonic"])
        self.lo = np.array(card["feature_range"]["p01"], dtype=np.float64)
        self.hi = np.array(card["feature_range"]["p99"], dtype=np.float64)
        self.alpha = float(card.get("fusion", {}).get("alpha", DEFAULT_ALPHA))
        self.status = "active"
        self.reason = "loaded %s trained %s on %d episodes" % (
            card.get("model_version"), str(card.get("trained_at"))[:10],
            card.get("training", {}).get("episodes", 0))

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, path: Optional[str] = None) -> "DriverRanker":
        p = path or DEFAULT_MODEL
        if not os.path.exists(p):
            return cls(None, p)
        try:
            with open(p) as fh:
                return cls(json.load(fh), p)
        except Exception as exc:
            r = cls(None, p)
            r.status = "refused"
            r.reason = "model artefact could not be read: %s" % exc
            return r

    @property
    def available(self) -> bool:
        return self.status == "active"

    @property
    def version(self) -> Optional[str]:
        return self.card.get("model_version")

    # -------------------------------------------------------------- scoring
    def _ood(self, vec: np.ndarray) -> Tuple[bool, int]:
        if self.lo is None:
            return True, 0
        out = int(np.sum((vec < self.lo - 1e-9) | (vec > self.hi + 1e-9)))
        return (out <= OOD_TOLERANCE * N_FEATURES), out

    def score(self, graded: Sequence[Dict[str, Any]], movement: Any,
              split_res: Optional[Dict[str, Any]] = None,
              include_vector: bool = False) -> Optional[List[Dict[str, Any]]]:
        """Score every candidate in one episode.

        ``graded`` is the pipeline's list of ``{"hyp":..., "grade":...}``. Returns
        one record per candidate, in the same order, or None when the ranker is
        not active. Candidates the causal graph rejected are scored as None: a
        hypothesis with no admissible mechanism is out of the competition
        entirely, and the model does not get a vote on that.
        """
        if not self.available:
            return None
        vecs, idx = [], []
        for i, g in enumerate(graded):
            if g["grade"].get("ladder") == "REJECTED":
                continue
            vecs.append(featurize(g["hyp"], g["grade"], movement, split_res,
                                  n_candidates=len(graded),
                                  prior_weight=g["grade"].get("prior_weight", 1.0)))
            idx.append(i)
        out: List[Optional[Dict[str, Any]]] = [None] * len(graded)
        if not vecs:
            return out
        X = np.vstack(vecs)
        raw = self.model.decision_function(X)
        prob = self.calibrator.predict(raw)
        order = np.argsort(-raw, kind="stable")
        rank = np.empty(len(raw), dtype=int)
        rank[order] = np.arange(1, len(raw) + 1)
        for k, i in enumerate(idx):
            ok, n_out = self._ood(X[k])
            out[i] = {
                "probability": round(float(prob[k]), 4),
                "fusion_alpha": self.alpha,
                "raw_score": round(float(raw[k]), 5),
                "rank": int(rank[k]),
                "of_candidates": len(idx),
                "model_version": self.version,
                "feature_contract": FEATURE_CONTRACT_VERSION,
                "feature_snapshot_id": snapshot_id(X[k]),
                "in_distribution": bool(ok),
                "features_outside_training_range": n_out,
                "authority": AUTHORITY,
            }
            if include_vector:
                out[i]["vector"] = [float(v) for v in X[k]]
        return out

    # ---------------------------------------------------------------- report
    def summary(self) -> Dict[str, Any]:
        s: Dict[str, Any] = {"status": self.status, "reason": self.reason,
                             "authority": AUTHORITY, "path": self.path}
        if self.card:
            s.update({
                "model_version": self.card.get("model_version"),
                "feature_contract": self.card.get("feature_contract"),
                "trained_at": self.card.get("trained_at"),
                "objective": self.card.get("objective"),
                "training": self.card.get("training"),
                "holdout": self.card.get("holdout"),
                "calibration_quality": self.card.get("calibration", {}).get("quality"),
                "top_features": dict(list((self.card.get("feature_importance") or {}).items())[:10]),
                "limitations": self.card.get("limitations"),
            })
        return s


_CACHE: Dict[str, Tuple[float, DriverRanker]] = {}


def get(path: Optional[str] = None, refresh: bool = False) -> DriverRanker:
    """Process-wide cache, invalidated on file mtime.

    The artefact is a few hundred KB of JSON and reparsing it per request would
    show up in the latency budget the telemetry publishes. Keying on mtime means
    a retrain lands in a long-running server without a restart - and, more to the
    point, without anyone having to remember that it needs one.
    """
    key = path or DEFAULT_MODEL
    stamp = os.path.getmtime(key) if os.path.exists(key) else 0.0
    hit = _CACHE.get(key)
    if refresh or hit is None or hit[0] != stamp:
        _CACHE[key] = (stamp, DriverRanker.load(key))
    return _CACHE[key][1]


def save_card(card: Dict[str, Any], path: Optional[str] = None) -> str:
    p = path or DEFAULT_MODEL
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as fh:
        json.dump(card, fh, indent=1)
    _CACHE.pop(p, None)
    return p
