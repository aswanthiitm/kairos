"""
"If we do nothing" — and what acting is worth.

The engine explains a movement that has already happened. The question a leader
asks next is what happens if nobody intervenes, and that is a forecast, not an
explanation. It is deliberately a SEPARATE, clearly-labelled step: an
extrapolation carries far less epistemic weight than a counterfactual, and the
two must never be presented in the same voice.

Method, and its limits stated plainly: deseasonalise with the same weekly index
SIFT already fitted, take a robust (Theil-Sen) slope over the recent level, and
project it forward. The interval widens with the square root of horizon.
No exogenous variables, no regime change, no competitor response. This is a
"the current trend continues" line, which is useful precisely because it is the
thing that will happen if nobody acts.
"""
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .telemetry import Telemetry, MethodType


def _theil_sen(y: np.ndarray) -> float:
    """Median of pairwise slopes — robust to the outliers a shocked series has."""
    n = len(y)
    if n < 3:
        return 0.0
    idx = np.arange(n, dtype=float)
    step = max(1, n // 40)
    slopes = [(y[j] - y[i]) / (idx[j] - idx[i])
              for i in range(0, n - 1, step) for j in range(i + 1, n, step)]
    return float(np.median(slopes)) if slopes else 0.0


def project(movement, tel: Telemetry, horizon_days: int = 28,
            fit_days: int = 28) -> Optional[Dict[str, Any]]:
    """Extrapolate the movement's own series forward."""
    series = [r for r in (movement.series or []) if r.get("v") is not None]
    if len(series) < fit_days + 7:
        tel.method("forecast", MethodType.RULES, "forecast withheld",
                   "too little history to extrapolate responsibly; a trend line on a "
                   "short series is a straight line through noise")
        return None

    tail = series[-fit_days:]
    y = np.array([r["v"] for r in tail], dtype=float)
    # weekly means, so the projection is not driven by weekday shape
    wk = np.array([y[max(0, i - 3):i + 4].mean() for i in range(len(y))])
    slope = _theil_sen(wk)
    level = float(np.median(wk[-7:]))
    resid = y - wk
    sigma = float(1.4826 * np.median(np.abs(resid - np.median(resid)))) or float(y.std())

    last = date.fromisoformat(tail[-1]["d"])
    path = []
    for h in range(1, horizon_days + 1):
        centre = level + slope * h
        width = 1.96 * sigma * np.sqrt(h / 7.0)
        path.append({"d": str(last + timedelta(days=h)),
                     "centre": float(centre),
                     "lo": float(centre - width), "hi": float(centre + width)})

    daily_now = level
    daily_end = path[-1]["centre"]
    cum = float(sum(p["centre"] for p in path))
    # what the same horizon would have produced at the pre-movement level
    baseline_daily = float(np.median([r["e"] for r in series[-fit_days:] if r.get("e")]) or level)
    cum_baseline = baseline_daily * horizon_days

    tel.method("forecast", MethodType.STATISTICS, "do-nothing projection",
               "an extrapolation of the current trend, kept separate from the causal "
               "finding and labelled as such — it answers 'what if nobody acts', which "
               "is a different question from 'what happened'",
               detail="Theil-Sen slope %.1f/day · sigma %.0f · horizon %dd"
                      % (slope, sigma, horizon_days))
    return {
        "method": "deseasonalised Theil-Sen trend, 95% interval widening with sqrt(horizon)",
        "horizon_days": horizon_days,
        "fit_days": fit_days,
        "slope_per_day": float(slope),
        "path": path,
        "daily_now": daily_now,
        "daily_at_horizon": daily_end,
        "cumulative_if_nothing_done": cum,
        "cumulative_at_baseline_rate": cum_baseline,
        "shortfall_over_horizon": cum_baseline - cum,
        "caveats": [
            "trend continuation only — no competitor response, no seasonality regime change",
            "widens with horizon; beyond about four weeks it is indicative, not a number "
            "to plan against",
        ],
    }


def with_intervention(fc: Optional[Dict[str, Any]], recommendation: Optional[Dict[str, Any]]
                      ) -> Optional[Dict[str, Any]]:
    """Overlay the recovery this playbook actually achieved last time.

    The recovery rate is measured, not assumed — it comes from the playbook's
    own outcome record — but the SHAPE (a linear ramp over weeks_to_effect) is a
    modelling choice and is labelled as one.
    """
    if not fc or not recommendation:
        return None
    pb = recommendation.get("source_playbook") or {}
    impact = recommendation.get("expected_impact_inr")
    if not isinstance(impact, (int, float)) or not impact:
        return None
    weeks = max(1, int(recommendation.get("monitoring", {}).get("check_in_days", 28) / 7))
    ramp_days = min(fc["horizon_days"], weeks * 7)
    per_day_full = impact / float(ramp_days)

    path = []
    for i, p in enumerate(fc["path"], start=1):
        frac = min(1.0, i / float(ramp_days))
        path.append({"d": p["d"], "centre": p["centre"] + per_day_full * frac})
    cum = float(sum(x["centre"] for x in path))
    return {
        "path": path,
        "cumulative_if_acted": cum,
        "value_of_acting": cum - fc["cumulative_if_nothing_done"],
        "ramp_days": ramp_days,
        "basis": "recovery rate from %s (measured); linear ramp over %d days is a "
                 "modelling assumption" % (pb.get("title", "the matched playbook"), ramp_days),
    }
