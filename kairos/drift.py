"""
Data and model drift.

Two different failures, measured separately because the response differs:

  DATA DRIFT   the estate itself has moved — channel mix, tier mix, warehouse
               share. A baseline fitted on last quarter's shape will mis-state
               what is 'expected' even when nothing is wrong.

  MODEL DRIFT  the learned ranker is seeing feature values unlike the ones it
               was trained on. Its ordering is then extrapolation, not
               inference, and its authority should be withdrawn.

Both use the Population Stability Index, the standard measure for this:

    PSI = sum over bins of (actual% - expected%) * ln(actual% / expected%)

Conventional reading, which we keep rather than inventing our own thresholds:
PSI < 0.10 stable, 0.10-0.25 shifting, > 0.25 drifted.
"""
import json, math, os
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .telemetry import Telemetry, MethodType

STABLE, SHIFTING, DRIFTED = "STABLE", "SHIFTING", "DRIFTED"
BANDS = ((0.10, STABLE), (0.25, SHIFTING))


def psi(expected: np.ndarray, actual: np.ndarray, eps: float = 1e-6) -> float:
    e = np.clip(expected.astype(float), eps, None)
    a = np.clip(actual.astype(float), eps, None)
    e, a = e / e.sum(), a / a.sum()
    return float(np.sum((a - e) * np.log(a / e)))


def _band(v: float) -> str:
    for lim, name in BANDS:
        if v < lim:
            return name
    return DRIFTED


def categorical_drift(estate, tel: Telemetry, dim: str,
                      reference: Tuple[date, date],
                      current: Tuple[date, date]) -> Optional[Dict[str, Any]]:
    """Share-of-units drift on one dimension of the order book."""
    q = ("SELECT %s AS k, SUM(units) AS n FROM orders "
         "WHERE order_date BETWEEN DATE '%s' AND DATE '%s' GROUP BY 1 ORDER BY 1")
    ref = estate.sql(q % (dim, reference[0], reference[1]), tel, "drift",
                     "%s reference distribution" % dim)
    cur = estate.sql(q % (dim, current[0], current[1]), tel, "drift",
                     "%s current distribution" % dim)
    if not len(ref) or not len(cur):
        return None
    keys = sorted(set(ref["k"]) | set(cur["k"]))
    r = ref.set_index("k")["n"].reindex(keys).fillna(0.0).values
    c = cur.set_index("k")["n"].reindex(keys).fillna(0.0).values
    v = psi(r, c)
    rs, cs = r / max(r.sum(), 1), c / max(c.sum(), 1)
    moved = sorted(zip(keys, cs - rs), key=lambda t: -abs(t[1]))[:3]
    return {"dimension": dim, "psi": round(v, 4), "band": _band(v),
            "largest_shifts": [{"value": str(k), "share_change_pp": round(100 * d, 1)}
                               for k, d in moved]}


def data_drift(estate, tel: Telemetry, current: Tuple[date, date],
               reference_days: int = 120,
               dims: Optional[List[str]] = None) -> Dict[str, Any]:
    dims = dims or ["channel", "tier", "segment", "warehouse_id", "category"]
    ref_end = current[0] - timedelta(days=1)
    ref_start = ref_end - timedelta(days=reference_days)
    out = [d for d in (categorical_drift(estate, tel, x, (ref_start, ref_end), current)
                       for x in dims) if d]
    worst = max(out, key=lambda d: d["psi"]) if out else None
    verdict = worst["band"] if worst else STABLE
    tel.method("drift", MethodType.STATISTICS, "population stability on the estate",
               "a baseline fitted on an estate that has since changed shape will "
               "mis-state what 'expected' means, so the shape is monitored separately "
               "from the metric",
               detail="reference %s..%s · worst %s PSI %.3f"
                      % (ref_start, ref_end, worst["dimension"] if worst else "-",
                         worst["psi"] if worst else 0.0))
    return {"kind": "data", "verdict": verdict,
            "reference_window": {"start": str(ref_start), "end": str(ref_end)},
            "current_window": {"start": str(current[0]), "end": str(current[1])},
            "dimensions": sorted(out, key=lambda d: -d["psi"]),
            "worst": worst,
            "reading": {STABLE: "the estate has the same shape the baseline was fitted on",
                        SHIFTING: "the estate is changing shape; expected bands are "
                                  "drifting and should be refitted soon",
                        DRIFTED: "the estate no longer resembles the baseline period; "
                                 "refit before trusting an expected band"}[verdict]}


def model_drift(tel: Telemetry, ml_block: Dict[str, Any],
                _unused: Any = None) -> Dict[str, Any]:
    """Aggregate the ranker's own out-of-distribution flags into a verdict.

    The ranker already checks each candidate against the p01-p99 feature range
    recorded at training time, so drift does not re-derive that - it reads the
    flags and decides what to do about them. A model asked to score inputs
    unlike its training data is extrapolating, and its advisory authority is
    withdrawn rather than silently trusted.
    """
    cands = (ml_block or {}).get("candidates") or {}
    scored = [c for c in cands.values() if isinstance(c, dict)]
    if not scored:
        tel.method("drift", MethodType.RULES, "model drift not evaluated",
                   "the learned ranker did not score anything this run, so there are "
                   "no live inputs to compare against its training support")
        return {"kind": "model", "verdict": "UNKNOWN", "candidates_scored": 0,
                "out_of_distribution": 0, "authority_withdrawn": False,
                "reading": "the ranker did not run this window"}

    ood = [c for c in scored if not c.get("in_distribution", True)]
    frac = len(ood) / float(len(scored))
    verdict = DRIFTED if frac > 0.35 else SHIFTING if frac > 0.15 else STABLE
    withdrawn = verdict == DRIFTED

    tel.method("drift", MethodType.ML, "learned-ranker input support",
               "the ranker records whether each candidate's features fall inside the "
               "p01-p99 range it was trained on; when too many fall outside, its "
               "ordering is extrapolation and its authority is withdrawn for the run",
               detail="%d/%d candidates out of distribution (%.0f%%) -> %s%s"
                      % (len(ood), len(scored), 100 * frac, verdict,
                         " · AUTHORITY WITHDRAWN" if withdrawn else ""))
    return {
        "kind": "model", "verdict": verdict,
        "model_version": (ml_block or {}).get("model_version"),
        "feature_contract": (ml_block or {}).get("feature_contract"),
        "candidates_scored": len(scored), "out_of_distribution": len(ood),
        "out_of_distribution_pct": round(100 * frac, 1),
        "authority_withdrawn": withdrawn,
        "reading": {
            STABLE: "live candidates sit inside the range the ranker was trained on",
            SHIFTING: "some candidates fall outside the ranker's training range; its "
                      "ordering is still applied but is closer to extrapolation",
            DRIFTED: "most candidates fall outside the ranker's training range, so the "
                     "learned ordering was withdrawn and the evidence heuristic ranks "
                     "alone this run",
        }[verdict],
    }
