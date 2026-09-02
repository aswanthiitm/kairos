"""
Building the training table by REPLAYING THE REAL ENGINE.

For every historical episode we run the same code the production pipeline runs -
``sift.detect``, ``split.split``, ``evidence.build_hypotheses``, ``evidence.grade``
- and then call the one ``features.featurize`` there is. Nothing is recomputed by
a parallel "training-time" implementation, because that is where feature skew
comes from and it is invisible until the model is already in production.

The emitted table is ``driver_episode_features``:

    episode_id, window_start, kpi, slice, candidate_id, driver_type,
    <42 contract features>, heuristic_score, label

``label`` is 1 when the candidate matches the driver that was planted in the
episode, 0 otherwise. Null episodes contribute all-zero groups, which is what
teaches the ranker that "none of these" is a real answer.

``heuristic_score`` is the CURRENT ranking rule, recorded per row at build time,
so the evaluation can compare the shipped heuristic against the learned model on
exactly the same candidates rather than on a re-derivation of them.
"""
import json
import os
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..contract import Contract
from ..security import load_personas
from ..sources import Estate
from ..telemetry import Telemetry
from ..sift import detect
from ..split import split as do_split
from .. import evidence as EV
from .features import FEATURES, featurize, snapshot_id

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HIST_DB = os.path.join(ROOT, "data", "history.duckdb")
HIST_GEN = os.path.join(ROOT, "data", "history")
EPISODES = os.path.join(HIST_GEN, "episodes.json")
RUNTIME = os.path.join(ROOT, "runtime")
TABLE = os.path.join(RUNTIME, "driver_episode_features.csv")

# the confidence the shipped heuristic assigns per rung (evidence.verdict)
LADDER_CONF = {"L3": 0.85, "L2": 0.65, "L1": 0.40, "L0": 0.20, "REJECTED": 0.0}


def heuristic_score(hyp: Dict[str, Any], grade: Dict[str, Any],
                    prior_weight: float = 1.0) -> float:
    """The ranking rule as it exists today: share-weighted ladder confidence.
    Reproduced here (not imported) so the comparison arm is pinned to the rule
    the model was measured against, even if the rule later changes."""
    ep = abs(hyp.get("explanatory_power") or 0.0)
    return float(max(ep, 0.02) * LADDER_CONF.get(grade.get("ladder"), 0.2) * prior_weight)


# ------------------------------------------------------------------- labelling
def label_candidate(hyp: Dict[str, Any], episode: Dict[str, Any]) -> int:
    """Did this candidate name the driver that was actually planted?

    Matching is on driver TYPE plus ENTITY, never on the label text. A service
    failure hypothesis that names the wrong warehouse is wrong, even though the
    episode really was a service failure - otherwise the ranker would be rewarded
    for being vaguely right, which is not a standard anyone can act on.
    """
    dt = hyp.get("driver_type")
    for d in episode.get("drivers", []):
        if d["type"] != dt:
            continue
        if dt == "service_failure":
            return int((hyp.get("exposure") or {}).get("warehouse_id") == d["entity"])
        if dt == "external_market":
            ev = hyp.get("event") or {}
            return int(str(ev.get("region")) == d["region"]
                       and str(ev.get("channel")) == d["channel"]
                       and str(ev.get("start_date"))[:10] == str(d["start"])[:10])
        if dt in ("price_change", "mix_shift", "instrumentation"):
            return 1
    return 0


# ---------------------------------------------------------------------- replay
def replay_episode(episode: Dict[str, Any], contract: Contract, estate: Estate,
                   persona, priors: Optional[Dict[str, Any]] = None
                   ) -> List[Dict[str, Any]]:
    """Run the engine over one episode and return one row per candidate driver."""
    ws = date.fromisoformat(episode["window"][0])
    we = date.fromisoformat(episode["window"][1])
    # "now" is the morning after the window closes, so freshness and the market
    # calendar are evaluated as they would have been on the day of the decision
    estate.now = datetime.combine(we + timedelta(days=1), datetime.min.time())
    tel = Telemetry()
    pri = priors or {}

    mv = detect(episode["kpi"], persona, estate, contract, tel, ws, we,
                episode.get("filters"))
    # The measure column comes from the contract's resolved definition, exactly as
    # it does at inference time. Hard-coding "net_revenue" here would train the
    # ranker on one definition and score it against another the moment the
    # authoritative source changed - the quietest possible train/serve skew.
    measure = episode.get("measure", "net_revenue")
    if measure == "net_revenue":
        measure = contract.measure_column("net_revenue")
    sp = do_split(estate, persona, tel, (ws, we), filters=episode.get("filters"),
                  measure=measure, contract=contract)
    hyps = EV.build_hypotheses(contract, mv, sp, estate, tel)
    if not hyps:
        return []

    out: List[Dict[str, Any]] = []
    for h in hyps:
        g = EV.grade(h, contract, estate, persona, tel, mv, (ws, we))
        if g.get("ladder") == "REJECTED":
            continue                      # the graph already removed it; never scored
        pw = float((pri.get(h["id"], {}) or {}).get("weight", 1.0))
        vec = featurize(h, g, mv, sp, n_candidates=len(hyps), prior_weight=pw)
        row: Dict[str, Any] = {
            "episode_id": episode["episode_id"],
            "window_start": episode["window"][0],
            "window_end": episode["window"][1],
            "kpi": episode["kpi"],
            "slice": json.dumps(episode.get("filters") or {}, sort_keys=True),
            "candidate_id": h["id"],
            "driver_type": h.get("driver_type", ""),
            "ladder": g.get("ladder"),
            "null_episode": int(bool(episode.get("null_episode"))),
            "n_candidates": len(hyps),
            "heuristic_score": heuristic_score(h, g, pw),
            "feature_snapshot_id": snapshot_id(vec),
            "label": label_candidate(h, episode),
        }
        row.update({n: float(v) for n, v in zip(FEATURES, vec)})
        out.append(row)
    return out


# ----------------------------------------------------------------------- build
def build(limit: Optional[int] = None, persona_key: str = "data_analyst",
          progress_every: int = 25, verbose: bool = True) -> pd.DataFrame:
    if not os.path.exists(EPISODES):
        raise RuntimeError("no historical estate - run: python data/generate_history.py")
    meta = json.load(open(EPISODES))
    episodes = meta["episodes"][: limit or len(meta["episodes"])]
    c = Contract()
    e = Estate(c, db_path=HIST_DB, gen_dir=HIST_GEN)
    p = load_personas(c)[persona_key]

    rows: List[Dict[str, Any]] = []
    t0 = time.time()
    for i, ep in enumerate(episodes, 1):
        try:
            rows += replay_episode(ep, c, e, p)
        except Exception as exc:               # one bad episode must not lose the corpus
            if verbose:
                print("  ! %s skipped: %s" % (ep["episode_id"], exc))
        if verbose and i % progress_every == 0:
            print("  replayed %4d/%d episodes  %6d rows  %5.1fs"
                  % (i, len(episodes), len(rows), time.time() - t0))

    df = pd.DataFrame(rows)
    os.makedirs(RUNTIME, exist_ok=True)
    df.to_csv(TABLE, index=False)
    if verbose:
        print("driver_episode_features: %d rows / %d episodes / %d positives -> %s"
              % (len(df), df.episode_id.nunique() if len(df) else 0,
                 int(df.label.sum()) if len(df) else 0, TABLE))
    return df


def load_table(path: Optional[str] = None) -> pd.DataFrame:
    path = path or TABLE
    if not os.path.exists(path):
        raise RuntimeError("no training table - run: python -m whylayer.ml.train --build")
    return pd.read_csv(path)


def time_split(df: pd.DataFrame, train_frac: float = 0.65,
               calib_frac: float = 0.15) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """TIME-BASED holdout, split on whole episodes.

    A random split would put candidates from the same episode on both sides and
    let the model see the answer to a question it is about to be asked. It would
    also ignore that this is a temporal decision system: the only honest question
    is whether a model fitted on the past ranks the future, so train, calibrate
    and test are three consecutive periods.
    """
    eps = (df[["episode_id", "window_start"]].drop_duplicates()
           .sort_values(["window_start", "episode_id"]))
    n = len(eps)
    a, b = int(n * train_frac), int(n * (train_frac + calib_frac))
    tr = set(eps.episode_id.iloc[:a]); ca = set(eps.episode_id.iloc[a:b])
    te = set(eps.episode_id.iloc[b:])
    return (df.episode_id.isin(tr).values, df.episode_id.isin(ca).values,
            df.episode_id.isin(te).values)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="build the driver-ranker training table")
    ap.add_argument("--limit", type=int, default=None)
    build(limit=ap.parse_args().limit)
