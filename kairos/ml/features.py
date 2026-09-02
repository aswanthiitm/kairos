"""
THE FEATURE CONTRACT.

One function, ``featurize``, is the only way a candidate driver ever becomes a
vector - at training time and at inference time. There is no second code path,
so there is no train/serve skew to argue about. ``dataset.py`` calls it while
replaying history; ``ranker.py`` calls it mid-run. Same function, same order,
same version string, and the version is written into the model file so a model
can never be loaded against a feature set it was not trained on.

Three rules this file enforces
------------------------------
1. NO LABEL LEAKAGE. ``FORBIDDEN`` lists the fields that are only knowable after
   the fact - the validated driver, the realised outcome, the recovery achieved.
   ``assert_no_leakage`` is run over the feature names at import and over the
   source record in ``dataset.py``. An outcome measured next quarter cannot be a
   feature in a recommendation made today, and a model that quietly used one
   would evaluate beautifully and be worthless.

2. NO LADDER RUNG AS A FEATURE. The evidence ladder grade (L0..L3) is available
   at inference time and would be legal to use - but a model fed the rung mostly
   learns to reproduce the rung, and then adds nothing to a ranking that already
   uses it. So the rung is withheld and its COMPONENTS are supplied instead
   (precedence gap, dose-response rho, corroboration counts, DiD effect and
   placebo). The learned score is then genuinely independent information, and
   fusing it with the rung in ``evidence.verdict`` is a real fusion rather than
   double-counting.

3. NO PII, NO FREE TEXT. Features are counts and statistics over retrieved
   documents, never the documents. The model cannot memorise a customer.
"""
import hashlib
import json
import math
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

FEATURE_CONTRACT_VERSION = "driver-features-v1"

# Fields that are only knowable AFTER the decision. Never features.
FORBIDDEN = {
    "validated_driver", "label", "y", "outcome_delta", "outcome_horizon",
    "realised_recovery_pct", "analyst_grade", "true_driver", "episode_truth",
    "ladder", "verdict", "verdict_status", "expected_rank",
}

DRIVER_TYPES = ["service_failure", "external_market", "price_change",
                "mix_shift", "instrumentation"]

FEATURES: List[str] = [
    # --- attribution: how much of the movement does this candidate sit on
    "share_abs",                 # |explanatory power| from the lattice scan
    "share_signed",              # signed, so a candidate moving the wrong way is visible
    "share_shift_abs",           # how far this segment's share of the measure moved
    "dim_surprise",              # Jensen-Shannon divergence of the parent dimension
    "exposure_depth",            # how many dimensions the candidate is conditioned on
    "is_arithmetic_identity",    # mix effects are algebra, not inference

    # --- mechanism: what the curated causal graph allows
    "graph_path_len",
    "mechanism_lag_days",
    "driver_type_service_failure",
    "driver_type_external_market",
    "driver_type_price_change",
    "driver_type_mix_shift",
    "driver_type_instrumentation",

    # --- temporal precedence
    "precedence_present",
    "precedence_consistent",
    "lag_alignment",             # 1.0 = gap sits exactly on the declared lag
    "gap_days_norm",

    # --- dose-response gradient
    "dose_present",
    "dose_rho",
    "dose_p_neglog10",
    "dose_monotone",
    "dose_buckets",

    # --- corroboration from unstructured evidence
    "corrob_on_theme_log",
    "corrob_independent_types",
    "corrob_distinct_accounts_log",
    "corrob_conflict_ratio",
    "corrob_evidence_withheld",  # entitlement removed documents -> weaker, flag it

    # --- counterfactual
    "did_present",
    "did_effect_abs",
    "did_tstat_abs",
    "did_p_neglog10",
    "did_placebo_abs",
    "did_parallel_trends_ok",
    "did_n_days_post_log",

    # --- the episode this candidate is competing inside
    "movement_z_abs",
    "movement_pct_abs",
    "movement_history_days_log",
    "movement_sparse",
    "movement_dq_flags",
    "feed_stale",
    "n_candidates_log",          # a candidate that wins a field of 6 beats one of 2

    # --- what this organisation has learned so far
    "analyst_prior_weight",
]

N_FEATURES = len(FEATURES)


def assert_no_leakage(names: Sequence[str]) -> None:
    bad = sorted(set(n.lower() for n in names) & FORBIDDEN)
    if bad:
        raise ValueError(
            "feature contract violation - these are post-hoc fields and must never "
            "be features: %s" % ", ".join(bad))


assert_no_leakage(FEATURES)
assert len(set(FEATURES)) == N_FEATURES, "duplicate feature name in the contract"


# --------------------------------------------------------------------- helpers
def _f(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return default if (math.isnan(x) or math.isinf(x)) else x


def _neglog10p(p: Any) -> float:
    """p-values are useless to a tree in raw form - almost every informative value
    is crushed into [0, 0.05]. -log10 spreads them out. Capped at 12 so a p of
    exactly 0 does not become an outlier the binner wastes a bin on."""
    x = _f(p, 1.0)
    return float(min(12.0, -math.log10(max(x, 1e-12))))


def _log1p(v: Any) -> float:
    return float(math.log1p(max(0.0, _f(v))))


# ------------------------------------------------------------------- featurize
def featurize(hyp: Dict[str, Any], grade: Dict[str, Any], movement: Any,
              split_res: Optional[Dict[str, Any]] = None,
              n_candidates: int = 1,
              prior_weight: float = 1.0) -> np.ndarray:
    """Candidate driver -> fixed-length float vector, in ``FEATURES`` order.

    Absent evidence is encoded as an explicit ``*_present`` indicator plus a zero,
    never as a sentinel the tree has to guess about. "We never ran the
    counterfactual" and "we ran it and it was zero" are different states and the
    model is told which one it is looking at.
    """
    t = grade.get("tests", {}) or {}
    pr = t.get("precedence") or {}
    dr = t.get("dose_response") or {}
    cb = t.get("corroboration") or {}
    did = t.get("counterfactual") or {}

    ep = hyp.get("explanatory_power")
    share_signed = _f(ep, 0.0)
    dtype = hyp.get("driver_type", "")

    # locate this candidate in the lattice scan to recover its share shift and the
    # surprise of the dimension it lives on
    share_shift, dim_surprise = 0.0, 0.0
    exposure = hyp.get("exposure") or {}
    if split_res:
        for row in split_res.get("contributors", []) or []:
            v = exposure.get(row.get("dimension"))
            if v is not None and str(v) == str(row.get("value")):
                share_shift = abs(_f(row.get("share_shift")))
                dim_surprise = _f(row.get("dim_surprise"))
                break

    lag = _f(grade.get("mechanism_lag_days"), 0.0)
    gap = pr.get("gap_days")
    lag_alignment = 0.0
    if gap is not None:
        # 1.0 when the observed cause->effect gap sits on the lag the graph
        # declares; decaying as it drifts. This is the feature that separates
        # "started just before" from "started three weeks before and unrelated".
        lag_alignment = float(max(0.0, 1.0 - abs(_f(gap) - lag) / (lag + 10.0)))

    mv_filters = getattr(movement, "filters", {}) or {}
    fresh = getattr(movement, "freshness", None) or {}

    vals: Dict[str, float] = {
        "share_abs": abs(share_signed),
        "share_signed": share_signed,
        "share_shift_abs": share_shift,
        "dim_surprise": dim_surprise,
        "exposure_depth": float(len(exposure)),
        "is_arithmetic_identity": 1.0 if hyp.get("arithmetic_only") else 0.0,

        "graph_path_len": float(len(grade.get("mechanism_path") or [])),
        "mechanism_lag_days": lag,

        "precedence_present": 1.0 if pr else 0.0,
        "precedence_consistent": 1.0 if pr.get("consistent") else 0.0,
        "lag_alignment": lag_alignment,
        "gap_days_norm": _f(gap) / 30.0 if gap is not None else 0.0,

        "dose_present": 1.0 if dr else 0.0,
        "dose_rho": _f(dr.get("spearman_rho")),
        "dose_p_neglog10": _neglog10p(dr.get("p_value")) if dr else 0.0,
        "dose_monotone": 1.0 if dr.get("monotone") else 0.0,
        "dose_buckets": float(len(dr.get("buckets") or [])),

        "corrob_on_theme_log": _log1p(cb.get("on_theme_docs")),
        "corrob_independent_types": _f(cb.get("independent_source_types")),
        "corrob_distinct_accounts_log": _log1p(cb.get("distinct_accounts")),
        "corrob_conflict_ratio": _f(cb.get("conflict_ratio")),
        "corrob_evidence_withheld": float(len(grade.get("evidence_withheld") or [])),

        "did_present": 1.0 if did else 0.0,
        "did_effect_abs": abs(_f(did.get("did_pp"))),
        "did_tstat_abs": min(20.0, abs(_f(did.get("t_stat")))),
        "did_p_neglog10": _neglog10p(did.get("p_value")) if did else 0.0,
        "did_placebo_abs": abs(_f(did.get("placebo_pp"))),
        "did_parallel_trends_ok": 1.0 if did.get("parallel_trends_ok") else 0.0,
        "did_n_days_post_log": _log1p(did.get("n_days_post")),

        "movement_z_abs": min(30.0, abs(_f(getattr(movement, "z", 0.0)))),
        "movement_pct_abs": min(5.0, abs(_f(getattr(movement, "pct_change", 0.0)))),
        "movement_history_days_log": _log1p(getattr(movement, "history_days", 0)),
        "movement_sparse": 1.0 if getattr(movement, "sparse", False) else 0.0,
        "movement_dq_flags": float(len(getattr(movement, "data_quality_flags", []) or [])),
        "feed_stale": 1.0 if fresh.get("breached") else 0.0,
        "n_candidates_log": _log1p(max(0, n_candidates - 1)),

        "analyst_prior_weight": _f(prior_weight, 1.0),
    }
    for d in DRIVER_TYPES:
        vals["driver_type_%s" % d] = 1.0 if dtype == d else 0.0

    missing = set(FEATURES) - set(vals)
    if missing:
        raise KeyError("feature contract out of sync, unset: %s" % sorted(missing))
    return np.array([vals[n] for n in FEATURES], dtype=np.float64)


def snapshot_id(vec: np.ndarray) -> str:
    """Content hash of the exact vector scored, written into the run record.

    This is what makes a past recommendation reproducible: given the snapshot id
    and the model version, you can re-derive the score that was shown, even after
    the model has been retrained three times.
    """
    payload = FEATURE_CONTRACT_VERSION + "|" + ",".join("%.6g" % v for v in vec)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def to_row(vec: np.ndarray) -> Dict[str, float]:
    return {n: float(v) for n, v in zip(FEATURES, vec)}


def describe() -> Dict[str, Any]:
    return {"version": FEATURE_CONTRACT_VERSION, "n_features": N_FEATURES,
            "features": FEATURES, "forbidden": sorted(FORBIDDEN)}
