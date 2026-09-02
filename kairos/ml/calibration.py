"""
Turning a ranking score into a probability that means what it says.

A LambdaRank score is an ORDERING. It has no units and no probabilistic reading,
and presenting one as "87% likely to be the driver" would be exactly the kind of
false precision this engine exists to refuse. So the raw score is passed through
an isotonic regression fitted on a HELD-OUT, LATER slice of history, and only
the calibrated output is ever shown or fused.

Isotonic rather than Platt scaling because we are not willing to assume the
score-to-probability map is a sigmoid; the pool-adjacent-violators algorithm
(Ayer et al., 1955) fits the monotone map the data actually supports and is the
standard choice for tree ensembles (Niculescu-Mizil & Caruana, ICML 2005).

The calibrator is stored as two arrays - it is readable, and a reviewer can plot
it.  ``ece`` and ``brier`` in the model card say how well it worked; if it did
not work, ``ranker.py`` will refuse to publish a probability at all.
"""
from typing import Any, Dict, List, Optional

import numpy as np


def _pava(y: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Pool adjacent violators: the least-squares monotone non-decreasing fit."""
    n = len(y)
    vals = list(map(float, y))
    wts = list(map(float, w))
    idx: List[int] = list(range(n))          # size of each pooled block
    sizes = [1] * n
    i = 0
    while i < len(vals) - 1:
        if vals[i] <= vals[i + 1] + 1e-15:
            i += 1
            continue
        # merge blocks i and i+1, then walk back in case the merge broke order
        tw = wts[i] + wts[i + 1]
        vals[i] = (vals[i] * wts[i] + vals[i + 1] * wts[i + 1]) / tw
        wts[i] = tw
        sizes[i] += sizes[i + 1]
        del vals[i + 1], wts[i + 1], sizes[i + 1]
        while i > 0 and vals[i - 1] > vals[i] + 1e-15:
            tw = wts[i - 1] + wts[i]
            vals[i - 1] = (vals[i - 1] * wts[i - 1] + vals[i] * wts[i]) / tw
            wts[i - 1] = tw
            sizes[i - 1] += sizes[i]
            del vals[i], wts[i], sizes[i]
            i -= 1
    out = np.empty(n, dtype=np.float64)
    k = 0
    for v, s in zip(vals, sizes):
        out[k:k + s] = v
        k += s
    return out


class Isotonic(object):
    """Monotone score -> probability map, fitted by PAVA and stored as knots."""

    def __init__(self, lo: float = 0.001, hi: float = 0.999):
        self.x: np.ndarray = np.array([])
        self.y: np.ndarray = np.array([])
        self.lo, self.hi = float(lo), float(hi)
        self.n_fit: int = 0

    def fit(self, scores: np.ndarray, labels: np.ndarray,
            weights: Optional[np.ndarray] = None) -> "Isotonic":
        s = np.asarray(scores, dtype=np.float64)
        y = np.asarray(labels, dtype=np.float64)
        w = np.ones_like(s) if weights is None else np.asarray(weights, dtype=np.float64)
        order = np.argsort(s, kind="stable")
        s, y, w = s[order], y[order], w[order]
        fitted = _pava(y, w)
        # Collapse to knots. The PAVA fit is a step function, so only the first
        # and last point of each constant block are needed to reproduce it under
        # linear interpolation - everything between them is redundant. This keeps
        # the stored calibrator to a few dozen numbers a reviewer can actually read.
        keep = np.zeros(len(s), dtype=bool)
        keep[0] = keep[-1] = True
        keep[1:] |= fitted[1:] != fitted[:-1]
        keep[:-1] |= fitted[1:] != fitted[:-1]
        keep &= np.concatenate(([True], s[1:] != s[:-1]))
        keep[0] = keep[-1] = True
        self.x = s[keep]
        self.y = np.clip(fitted[keep], self.lo, self.hi)
        self.n_fit = int(len(s))
        return self

    def predict(self, scores: np.ndarray) -> np.ndarray:
        s = np.atleast_1d(np.asarray(scores, dtype=np.float64))
        if not len(self.x):
            # never fitted: fall back to the logistic squash, and say so upstream
            return 1.0 / (1.0 + np.exp(-np.clip(s, -35, 35)))
        return np.clip(np.interp(s, self.x, self.y), self.lo, self.hi)

    def to_dict(self) -> Dict[str, Any]:
        return {"x": [float(v) for v in self.x], "y": [float(v) for v in self.y],
                "lo": self.lo, "hi": self.hi, "n_fit": self.n_fit}

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Isotonic":
        c = Isotonic(d.get("lo", 0.001), d.get("hi", 0.999))
        c.x = np.array(d["x"], dtype=np.float64)
        c.y = np.array(d["y"], dtype=np.float64)
        c.n_fit = int(d.get("n_fit", len(c.x)))
        return c


# --------------------------------------------------------------------- scoring
def brier(p: np.ndarray, y: np.ndarray) -> float:
    p = np.asarray(p, dtype=np.float64); y = np.asarray(y, dtype=np.float64)
    return float(np.mean((p - y) ** 2)) if len(p) else float("nan")


def expected_calibration_error(p: np.ndarray, y: np.ndarray,
                               bins: int = 10) -> Dict[str, Any]:
    """ECE plus the reliability table, because a single number hides which end of
    the range is miscalibrated - and over-confidence at the top is the failure
    mode that actually costs a decision."""
    p = np.asarray(p, dtype=np.float64); y = np.asarray(y, dtype=np.float64)
    if not len(p):
        return {"ece": float("nan"), "bins": []}
    edges = np.linspace(0.0, 1.0, bins + 1)
    rows, ece = [], 0.0
    for i in range(bins):
        m = (p >= edges[i]) & (p < edges[i + 1] if i < bins - 1 else p <= edges[i + 1])
        if not m.any():
            continue
        conf, obs, n = float(p[m].mean()), float(y[m].mean()), int(m.sum())
        ece += (n / len(p)) * abs(conf - obs)
        rows.append({"bin": "%.1f-%.1f" % (edges[i], edges[i + 1]),
                     "n": n, "mean_predicted": round(conf, 4),
                     "observed_rate": round(obs, 4),
                     "gap": round(obs - conf, 4)})
    return {"ece": round(float(ece), 4), "bins": rows}
