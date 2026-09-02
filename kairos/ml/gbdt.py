"""
Histogram gradient-boosted decision trees, in numpy.

Why write this rather than import LightGBM
------------------------------------------
The rest of this engine is auditable line by line: every number a reader sees
can be traced to arithmetic they can inspect. A model that arrives as a compiled
binary and serialises to a pickle breaks that property exactly where it matters
most - the one component that is genuinely learned rather than derived.

So the model here is:

  * ~350 lines of numpy implementing the standard second-order (Newton) boosting
    formulation of Chen & Guestrin (XGBoost, KDD 2016) with the histogram split
    finder of Ke et al. (LightGBM, NeurIPS 2017);
  * serialised to **plain JSON** - the trained artefact is a text file an auditor
    can open and read, with thresholds in the original feature units;
  * dependency-free, so the repository still installs with numpy and pandas.

This is not a research contribution. It is the same algorithm everyone uses,
written so that it can be defended in the same terms as the rest of the system.

Objectives
----------
``logistic``    binary cross-entropy. g = p - y, h = p(1-p).
``lambdarank``  LambdaMART (Burges, MSR-TR-2010-82). The task is genuinely a
                ranking task - "which of these candidate drivers explains this
                episode" - and the pointwise loss does not know that candidates
                compete within an episode. Pairwise lambdas weighted by |dNDCG|
                optimise the order directly.
"""
import json
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

EPS = 1e-12


# --------------------------------------------------------------------- helpers
def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -35.0, 35.0)))


def _bin_edges(x: np.ndarray, max_bins: int) -> np.ndarray:
    """Quantile bin edges for one feature.

    Quantiles rather than equal width because most of these features are heavily
    skewed (share of movement, -log10 p-values); equal-width bins would put 90%
    of the mass in one bucket and the splitter would have nothing to cut on.
    """
    u = np.unique(x[np.isfinite(x)])
    if len(u) <= 1:
        return np.array([], dtype=np.float64)
    if len(u) <= max_bins:
        # few distinct values (indicators, small counts): split between each pair
        return ((u[:-1] + u[1:]) / 2.0).astype(np.float64)
    qs = np.linspace(0.0, 1.0, max_bins + 1)[1:-1]
    e = np.unique(np.quantile(u, qs))
    return e.astype(np.float64)


class _Tree(object):
    """A single regression tree, stored as flat arrays so it round-trips to JSON.

    ``threshold`` holds the split point in ORIGINAL feature units (not bin
    indices), which is what makes the serialised model readable: a line saying
    ``did_p_neglog10 <= 1.30`` means something to a human.
    """

    __slots__ = ("feature", "threshold", "left", "right", "value", "gain")

    def __init__(self):
        self.feature: List[int] = []
        self.threshold: List[float] = []
        self.left: List[int] = []
        self.right: List[int] = []
        self.value: List[float] = []
        self.gain: List[float] = []

    def _add(self) -> int:
        self.feature.append(-1); self.threshold.append(0.0)
        self.left.append(-1); self.right.append(-1)
        self.value.append(0.0); self.gain.append(0.0)
        return len(self.feature) - 1

    def predict(self, X: np.ndarray) -> np.ndarray:
        out = np.zeros(len(X), dtype=np.float64)
        stack = [(0, np.arange(len(X)))]
        while stack:
            node, idx = stack.pop()
            if not len(idx):
                continue
            if self.feature[node] < 0:
                out[idx] = self.value[node]
                continue
            v = X[idx, self.feature[node]]
            go_left = v <= self.threshold[node]
            stack.append((self.left[node], idx[go_left]))
            stack.append((self.right[node], idx[~go_left]))
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {"feature": self.feature, "threshold": self.threshold,
                "left": self.left, "right": self.right,
                "value": self.value, "gain": self.gain}

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "_Tree":
        t = _Tree()
        t.feature = list(d["feature"]); t.threshold = list(d["threshold"])
        t.left = list(d["left"]); t.right = list(d["right"])
        t.value = list(d["value"]); t.gain = list(d.get("gain") or [0.0] * len(d["feature"]))
        return t


# ------------------------------------------------------------------- objectives
def _grad_logistic(y: np.ndarray, f: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    p = sigmoid(f)
    return p - y, np.maximum(p * (1.0 - p), 1e-6)


def _grad_lambdarank(y: np.ndarray, f: np.ndarray, groups: np.ndarray,
                     sigma: float = 1.0) -> Tuple[np.ndarray, np.ndarray]:
    """LambdaMART gradients with binary relevance.

    For every (positive, negative) pair inside an episode we push the positive
    up and the negative down, weighted by how much swapping them would change
    NDCG. Pairs already far apart contribute almost nothing, so the model spends
    its capacity on the boundary cases - which, for us, are exactly the episodes
    where the heuristic ranking is wrong.
    """
    g = np.zeros_like(f); h = np.zeros_like(f)
    for gid in np.unique(groups):
        m = np.flatnonzero(groups == gid)
        yy, ss = y[m], f[m]
        pos = m[yy > 0]; neg = m[yy <= 0]
        if not len(pos) or not len(neg):
            continue
        # ideal DCG for this episode (binary gains, so gain = 1 per positive)
        idcg = sum(1.0 / math.log2(r + 2.0) for r in range(len(pos))) or 1.0
        order = np.argsort(-ss, kind="stable")
        rank = np.empty(len(m), dtype=np.int64)
        rank[order] = np.arange(len(m))
        pos_of = {int(i): int(rank[k]) for k, i in enumerate(m)}
        for i in pos:
            for j in neg:
                ri, rj = pos_of[int(i)], pos_of[int(j)]
                dndcg = abs(1.0 / math.log2(ri + 2.0) - 1.0 / math.log2(rj + 2.0)) / idcg
                if dndcg < EPS:
                    continue
                rho = 1.0 / (1.0 + math.exp(sigma * (f[i] - f[j])))
                lam = -sigma * rho * dndcg
                hes = sigma * sigma * rho * (1.0 - rho) * dndcg
                g[i] += lam;  g[j] -= lam
                h[i] += hes;  h[j] += hes
    return g, np.maximum(h, 1e-6)


# ----------------------------------------------------------------------- model
class HistGBDT(object):
    """Second-order gradient boosting over histogram-binned features."""

    def __init__(self, objective: str = "logistic", n_estimators: int = 300,
                 learning_rate: float = 0.06, max_depth: int = 4,
                 max_bins: int = 64, min_child_weight: float = 4.0,
                 min_samples_leaf: int = 8, reg_lambda: float = 2.0,
                 gamma: float = 0.0, subsample: float = 0.85,
                 colsample: float = 0.85, seed: int = 20260831,
                 early_stopping_rounds: int = 40):
        if objective not in ("logistic", "lambdarank"):
            raise ValueError("objective must be 'logistic' or 'lambdarank'")
        self.params = dict(
            objective=objective, n_estimators=int(n_estimators),
            learning_rate=float(learning_rate), max_depth=int(max_depth),
            max_bins=int(max_bins), min_child_weight=float(min_child_weight),
            min_samples_leaf=int(min_samples_leaf), reg_lambda=float(reg_lambda),
            gamma=float(gamma), subsample=float(subsample),
            colsample=float(colsample), seed=int(seed),
            early_stopping_rounds=int(early_stopping_rounds))
        self.trees: List[_Tree] = []
        self.base_score: float = 0.0
        self.n_features: int = 0
        self.feature_names: List[str] = []
        self.best_iteration: Optional[int] = None
        self.train_history: List[Dict[str, float]] = []

    # ------------------------------------------------------------------ fit
    def fit(self, X: np.ndarray, y: np.ndarray,
            groups: Optional[np.ndarray] = None,
            eval_set: Optional[Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]] = None,
            feature_names: Optional[Sequence[str]] = None,
            sample_weight: Optional[np.ndarray] = None) -> "HistGBDT":
        p = self.params
        X = np.ascontiguousarray(X, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        n, f = X.shape
        self.n_features = f
        self.feature_names = list(feature_names or ["f%d" % i for i in range(f)])
        obj = p["objective"]
        if obj == "lambdarank" and groups is None:
            raise ValueError("lambdarank needs group ids (one per episode)")
        groups = np.asarray(groups) if groups is not None else np.arange(n)
        # Sample weights exist for one reason: a label an analyst supplied through
        # feedback.py is worth several rows of the simulated bootstrap corpus, and
        # the trainer must be able to say so rather than drowning real evidence in
        # synthetic history.
        w = (np.ones(n, dtype=np.float64) if sample_weight is None
             else np.asarray(sample_weight, dtype=np.float64))
        if len(w) != n:
            raise ValueError("sample_weight must have one entry per row")

        # --- bin once. Everything downstream works on uint8 bin indices.
        self._edges = [_bin_edges(X[:, j], p["max_bins"]) for j in range(f)]
        Xb = np.empty((n, f), dtype=np.uint8)
        for j in range(f):
            Xb[:, j] = np.searchsorted(self._edges[j], X[:, j], side="left")
        n_bins = p["max_bins"] + 1

        # base score: the prior log-odds, so tree 1 does not spend itself on the mean
        if obj == "logistic":
            rate = float(np.clip(y.mean(), 1e-4, 1 - 1e-4))
            self.base_score = float(np.log(rate / (1 - rate)))
        else:
            self.base_score = 0.0
        F = np.full(n, self.base_score, dtype=np.float64)

        Fv = None
        if eval_set is not None:
            Xv, yv, gv = eval_set
            Xv = np.ascontiguousarray(Xv, dtype=np.float64)
            Fv = np.full(len(Xv), self.base_score, dtype=np.float64)

        rng = np.random.default_rng(p["seed"])
        best, best_iter, since = -np.inf, 0, 0
        self.trees, self.train_history = [], []

        for it in range(p["n_estimators"]):
            if obj == "logistic":
                g, h = _grad_logistic(y, F)
            else:
                g, h = _grad_lambdarank(y, F, groups)
            g, h = g * w, h * w

            rows = np.arange(n)
            if p["subsample"] < 1.0:
                # sample whole EPISODES, not rows: splitting a group across the
                # subsample would break the pairwise structure lambdarank needs
                gid = np.unique(groups)
                keep = gid[rng.random(len(gid)) < p["subsample"]]
                if len(keep) >= 4:
                    rows = np.flatnonzero(np.isin(groups, keep))
            cols = np.arange(f)
            if p["colsample"] < 1.0:
                cols = np.flatnonzero(rng.random(f) < p["colsample"])
                if len(cols) < 2:
                    cols = np.arange(f)

            tree = self._grow(Xb, g, h, rows, cols, n_bins)
            self.trees.append(tree)
            F += p["learning_rate"] * tree.predict(X)
            rec = {"iter": it}
            if Fv is not None:
                Fv += p["learning_rate"] * tree.predict(Xv)
                score = (ndcg_at_k(yv, Fv, gv, 3) if obj == "lambdarank" and gv is not None
                         else auc(yv, Fv))
                rec["valid"] = round(float(score), 5)
                if score > best + 1e-5:
                    best, best_iter, since = score, it, 0
                else:
                    since += 1
            self.train_history.append(rec)
            if Fv is not None and since >= p["early_stopping_rounds"]:
                break

        self.best_iteration = (best_iter if Fv is not None else len(self.trees) - 1)
        if Fv is not None:
            # drop the trees that made validation worse - keeping them would mean
            # shipping a model we already know is past its best point
            self.trees = self.trees[: self.best_iteration + 1]
        return self

    # ---------------------------------------------------------------- growth
    def _grow(self, Xb: np.ndarray, g: np.ndarray, h: np.ndarray,
              rows: np.ndarray, cols: np.ndarray, n_bins: int) -> _Tree:
        p = self.params
        t = _Tree()
        root = t._add()
        stack: List[Tuple[int, np.ndarray, int]] = [(root, rows, 0)]
        lam = p["reg_lambda"]

        while stack:
            node, idx, depth = stack.pop()
            G, H = float(g[idx].sum()), float(h[idx].sum())
            t.value[node] = -G / (H + lam)
            if depth >= p["max_depth"] or len(idx) < 2 * p["min_samples_leaf"] \
                    or H < 2 * p["min_child_weight"]:
                continue

            parent = (G * G) / (H + lam)
            best = (0.0, -1, -1)          # (gain, feature, bin)
            for j in cols:
                b = Xb[idx, j]
                hg = np.bincount(b, weights=g[idx], minlength=n_bins)
                hh = np.bincount(b, weights=h[idx], minlength=n_bins)
                hc = np.bincount(b, minlength=n_bins)
                cg, ch, cc = np.cumsum(hg), np.cumsum(hh), np.cumsum(hc)
                GL, HL, CL = cg[:-1], ch[:-1], cc[:-1]
                GR, HR, CR = G - GL, H - HL, len(idx) - CL
                ok = ((HL >= p["min_child_weight"]) & (HR >= p["min_child_weight"])
                      & (CL >= p["min_samples_leaf"]) & (CR >= p["min_samples_leaf"]))
                if not ok.any():
                    continue
                gain = 0.5 * ((GL * GL) / (HL + lam) + (GR * GR) / (HR + lam) - parent) \
                    - p["gamma"]
                gain = np.where(ok, gain, -np.inf)
                k = int(np.argmax(gain))
                if gain[k] > best[0]:
                    best = (float(gain[k]), int(j), k)

            if best[1] < 0:
                continue
            _, j, k = best
            edges = self._edges[j]
            # threshold in ORIGINAL units: bin k means "x <= edges[k]"
            thr = float(edges[k]) if k < len(edges) else float("inf")
            t.feature[node] = j
            t.threshold[node] = thr
            t.gain[node] = best[0]
            go_left = Xb[idx, j] <= k
            l, r = t._add(), t._add()
            t.left[node], t.right[node] = l, r
            stack.append((l, idx[go_left], depth + 1))
            stack.append((r, idx[~go_left], depth + 1))
        return t

    # -------------------------------------------------------------- predict
    def decision_function(self, X: np.ndarray) -> np.ndarray:
        X = np.ascontiguousarray(X, dtype=np.float64)
        out = np.full(len(X), self.base_score, dtype=np.float64)
        lr = self.params["learning_rate"]
        for t in self.trees:
            out += lr * t.predict(X)
        return out

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Only meaningful for the logistic objective; a lambdarank score is an
        ordering, not a probability, and must be passed through the isotonic
        calibrator in ``calibration.py`` before anyone calls it one."""
        return sigmoid(self.decision_function(X))

    # ----------------------------------------------------------- importance
    def feature_importance(self) -> Dict[str, float]:
        tot: Dict[str, float] = {n: 0.0 for n in self.feature_names}
        for t in self.trees:
            for node, fi in enumerate(t.feature):
                if fi >= 0:
                    tot[self.feature_names[fi]] += t.gain[node]
        s = sum(tot.values()) or 1.0
        return {k: round(v / s, 6) for k, v in
                sorted(tot.items(), key=lambda kv: -kv[1])}

    # ------------------------------------------------------------ serialise
    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": "HistGBDT", "params": self.params,
            "base_score": self.base_score, "n_features": self.n_features,
            "feature_names": self.feature_names,
            "best_iteration": self.best_iteration,
            "edges": [list(map(float, e)) for e in self._edges],
            "trees": [t.to_dict() for t in self.trees],
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "HistGBDT":
        m = HistGBDT(**{k: v for k, v in d["params"].items()})
        m.base_score = d["base_score"]; m.n_features = d["n_features"]
        m.feature_names = list(d["feature_names"])
        m.best_iteration = d.get("best_iteration")
        m._edges = [np.array(e, dtype=np.float64) for e in d["edges"]]
        m.trees = [_Tree.from_dict(t) for t in d["trees"]]
        return m


# ----------------------------------------------------------------- ranking metrics
def auc(y: np.ndarray, s: np.ndarray) -> float:
    """Rank-based AUC (Mann-Whitney U), tie-safe."""
    y = np.asarray(y); s = np.asarray(s, dtype=np.float64)
    pos, neg = int((y > 0).sum()), int((y <= 0).sum())
    if pos == 0 or neg == 0:
        return 0.5
    order = np.argsort(s, kind="stable")
    ranks = np.empty(len(s), dtype=np.float64)
    ranks[order] = np.arange(1, len(s) + 1)
    # average ranks within ties, or a constant scorer would read as AUC 1.0
    su = np.sort(s)
    i = 0
    while i < len(su):
        j = i
        while j + 1 < len(su) and su[j + 1] == su[i]:
            j += 1
        if j > i:
            tie = (i + j + 2) / 2.0
            ranks[np.isin(s, su[i])] = tie
        i = j + 1
    return float((ranks[y > 0].sum() - pos * (pos + 1) / 2.0) / (pos * neg))


def ndcg_at_k(y: np.ndarray, s: np.ndarray, groups: Optional[np.ndarray],
              k: int = 3) -> float:
    y = np.asarray(y, dtype=np.float64); s = np.asarray(s, dtype=np.float64)
    if groups is None:
        groups = np.zeros(len(y), dtype=np.int64)
    vals = []
    for gid in np.unique(groups):
        m = np.flatnonzero(groups == gid)
        if not (y[m] > 0).any():
            continue                      # a null episode has no ideal ranking
        order = m[np.argsort(-s[m], kind="stable")][:k]
        dcg = sum(y[i] / math.log2(r + 2.0) for r, i in enumerate(order))
        ideal = np.sort(y[m])[::-1][:k]
        idcg = sum(v / math.log2(r + 2.0) for r, v in enumerate(ideal)) or 1.0
        vals.append(dcg / idcg)
    return float(np.mean(vals)) if vals else 0.0


def save(model: HistGBDT, path: str) -> None:
    with open(path, "w") as fh:
        json.dump(model.to_dict(), fh, indent=1)


def load(path: str) -> HistGBDT:
    with open(path) as fh:
        return HistGBDT.from_dict(json.load(fh))
