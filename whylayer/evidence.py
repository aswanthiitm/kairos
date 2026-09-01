"""
STAGE 3 - SOURCE:  why did it happen, and how sure are we?

This is where structured contribution meets unstructured evidence, and where
the engine earns the right to say the word "because".

The Evidence Ladder (Bradford Hill's viewpoints, 1965, operationalised on
Pearl's causal hierarchy, CACM 2019):

  L0  co-movement            two series moved together            -> never a cause
  L1  precedence + gradient  cause preceded effect; dose-response -> "associated"
  L2  independent corrob.    sources not sharing a pipeline agree -> "likely cause"
  L3  counterfactual         DiD / synthetic control on a real
                             untreated cohort                     -> quantified

Division of labour, deliberately:
  - the causal GRAPH decides what is admissible          (curated, non-LLM)
  - STATISTICS decide precedence, gradient, effect size  (non-LLM)
  - RETRIEVAL finds the text                             (non-LLM)
  - an LLM may only PROPOSE candidate hypotheses and later PHRASE the result.
    It never grades the ladder and never computes a number. This split follows
    the evidence: models are strong at knowledge-based causal proposal
    (Kiciman et al., TMLR 2024) and near-random at inferring causation from
    correlation (Corr2Cause, ICLR 2024).
"""
import math, re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .contract import Contract
from .security import Persona
from .sources import Estate
from .telemetry import Telemetry, MethodType

LADDER = {"L0": "co-movement", "L1": "precedence + dose-response",
          "L2": "independent corroboration", "L3": "counterfactual"}
STOP = set("the a an and or of to in on for with is was be been are at by from that this it as "
           "we they our their he she has have had not no but if then than so".split())

THEME_TERMS = {
    "service_failure": ["late", "delay", "delayed", "sla", "missed", "dispatch", "window",
                        "consignment", "past the promised", "dual-sourcing", "recovery"],
    "price_objection": ["price", "priced", "pricing", "slab", "landed cost", "expensive",
                        "rate", "discount"],
    "competitor_activity": ["competitor", "rival", "promotion", "promo", "end-cap",
                            "shelf", "offtake", "monsoonsaver"],
    "routine": ["check-in", "invoice", "routine", "packaging", "audit"],
}


# --------------------------------------------------------------------- retrieval
def _tokens(s: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9']+", s.lower()) if w not in STOP and len(w) > 2]


def retrieve(estate: Estate, persona: Persona, tel: Telemetry,
             query_terms: List[str], window: Tuple[date, date],
             scope: Optional[Dict[str, Any]] = None, lag_days: int = 14,
             top_k: int = 12) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """BM25-style lexical retrieval scoped to the segment and window under
    investigation (plus the mechanism lag), then entitlement-filtered.

    Scoping to segment+window is what keeps this cheap: we never embed or scan
    the whole corpus, only the slice that could possibly be relevant.
    """
    ws, we = window
    lo = ws - timedelta(days=lag_days)
    docs = []
    for it in estate.interactions():
        d = datetime.fromisoformat(it["ts"]).date()
        if not (lo <= d <= we):
            continue
        ok = True
        for k, v in (scope or {}).items():
            iv = it.get(k)
            if isinstance(v, (list, tuple)):
                ok = ok and iv in v
            else:
                ok = ok and iv == v
            if not ok:
                break
        if ok:
            docs.append(it)

    if not docs:
        return [], []
    N = len(docs)
    dfreq: Counter = Counter()
    toks = {}
    for it in docs:
        t = set(_tokens(it["text"]))
        toks[it["interaction_id"]] = t
        for w in t:
            dfreq[w] += 1
    q = [w for term in query_terms for w in _tokens(term)]
    avgdl = np.mean([len(_tokens(it["text"])) for it in docs]) or 1.0

    scored = []
    for it in docs:
        tl = _tokens(it["text"]); dl = len(tl) or 1
        tf = Counter(tl)
        s = 0.0
        for w in q:
            if w not in tf:
                continue
            idf = math.log(1 + (N - dfreq[w] + 0.5) / (dfreq[w] + 0.5))
            s += idf * (tf[w] * 2.5) / (tf[w] + 1.5 * (0.25 + 0.75 * dl / avgdl))
        if s <= 0:
            continue
        scored.append({
            "id": it["interaction_id"], "score": round(float(s), 3),
            "ts": it["ts"], "type": it["type"], "domain": "crm_verbatim",
            "theme": it.get("theme"), "author_role": it.get("author_role"),
            "account_id": it.get("account_id"), "account_name": it.get("account_name"),
            "region": it.get("region"), "warehouse_id": it.get("warehouse_id"),
            "text": it["text"],
        })
    scored.sort(key=lambda r: -r["score"])
    visible, withheld = persona.filter_evidence(scored[:top_k * 3])
    tel.method("source", MethodType.RETRIEVAL, "BM25 retrieval over unstructured evidence",
               "scoped to the exact segment and window plus the mechanism lag, so we "
               "read a few hundred documents rather than the whole corpus - this is what "
               "makes evidence retrieval affordable per insight",
               detail="candidates=%d visible=%d withheld_by_entitlement=%d"
                      % (len(scored), len(visible), len(withheld)))
    return visible[:top_k], withheld


# ------------------------------------------------------------------ causal tests
def _series(estate: Estate, persona: Persona, tel, where_extra: Dict[str, Any],
            start: date, end: date) -> pd.DataFrame:
    where, _ = persona.sql_where(where_extra)
    clause = where or "WHERE 1=1"
    q = ("SELECT order_date AS d, SUM(net_revenue) AS v, SUM(units) AS u FROM orders %s "
         "AND order_date BETWEEN DATE '%s' AND DATE '%s' GROUP BY 1 ORDER BY 1"
         % (clause, start, end))
    df = estate.sql(q, tel, "source", "cohort series for counterfactual")
    df["d"] = pd.to_datetime(df["d"])
    return df


def difference_in_differences(estate: Estate, persona: Persona, tel: Telemetry,
                              treated: Dict[str, Any], control: Dict[str, Any],
                              window: Tuple[date, date], baseline_days: int = 28
                              ) -> Optional[Dict[str, Any]]:
    """The L3 test. Treated and control are dimension filters describing an
    exposed and an unexposed cohort. We also run a placebo on the pre-period:
    if the two cohorts were already diverging before the event, the design is
    invalid and we refuse to award L3."""
    ws, we = window
    bs, be = ws - timedelta(days=baseline_days + 1), ws - timedelta(days=1)
    t_post = _series(estate, persona, tel, treated, ws, we)
    c_post = _series(estate, persona, tel, control, ws, we)
    t_pre = _series(estate, persona, tel, treated, bs, be)
    c_pre = _series(estate, persona, tel, control, bs, be)
    if min(len(t_post), len(c_post), len(t_pre), len(c_pre)) < 5:
        return None

    tp, cp = t_post["v"].mean(), c_post["v"].mean()
    tb, cb = t_pre["v"].mean(), c_pre["v"].mean()
    if not (tb and cb):
        return None
    t_chg, c_chg = tp / tb - 1.0, cp / cb - 1.0

    # Estimator: r_t = log(treated_t) - log(control_t). DiD is the shift in the
    # mean of r between pre and post, which differences out anything common to
    # both cohorts (season, macro, calendar). SE comes from the daily dispersion
    # of r, which is the honest uncertainty - not a ratio-of-means approximation.
    def _logratio(a: pd.DataFrame, b: pd.DataFrame) -> np.ndarray:
        m = a.merge(b, on="d", suffixes=("_t", "_c"))
        m = m[(m["v_t"] > 0) & (m["v_c"] > 0)]
        return np.log(m["v_t"].values) - np.log(m["v_c"].values)

    r_pre, r_post = _logratio(t_pre, c_pre), _logratio(t_post, c_post)
    if len(r_pre) < 5 or len(r_post) < 5:
        return None
    did = float(np.mean(r_post) - np.mean(r_pre))
    se = float(np.sqrt(np.var(r_post, ddof=1) / len(r_post)
                       + np.var(r_pre, ddof=1) / len(r_pre)))
    from scipy import stats as _st
    tstat = did / se if se else 0.0
    pval = float(2 * (1 - _st.norm.cdf(abs(tstat))))

    # placebo: split the pre-period in half; a real effect should not already
    # be present before the event
    half = len(r_pre) // 2
    placebo = float(np.mean(r_pre[half:]) - np.mean(r_pre[:half])) if half >= 3 else 0.0
    parallel_ok = abs(placebo) < max(0.04, abs(did) * 0.5)

    tel.method("source", MethodType.CAUSAL, "difference-in-differences vs untreated cohort",
               "the control cohort already exists in the data (same region, different "
               "warehouse), so a counterfactual is affordable; a placebo on the "
               "pre-period tests the parallel-trends assumption before we trust it",
               detail="treated=%+.1f%% control=%+.1f%% DiD=%+.1f%% se=%.1f%% p=%.4f "
                      "placebo=%+.1f%% parallel_trends=%s"
                      % (100 * t_chg, 100 * c_chg, 100 * did, 100 * se, pval,
                         100 * placebo, parallel_ok))
    return {"treated_change_pct": float(t_chg), "control_change_pct": float(c_chg),
            "did_pp": did, "std_error_pp": se, "t_stat": float(tstat), "p_value": pval,
            "placebo_pp": placebo, "parallel_trends_ok": bool(parallel_ok),
            "n_days_pre": int(len(r_pre)), "n_days_post": int(len(r_post)),
            "treated_def": treated, "control_def": control,
            "attributable_inr": float((tp - tb * (1 + c_chg)) * ((we - ws).days + 1))}


def dose_response(estate: Estate, persona: Persona, tel: Telemetry,
                  window: Tuple[date, date], baseline_days: int = 28
                  ) -> Optional[Dict[str, Any]]:
    """Do accounts that experienced MORE late deliveries show a LARGER revenue
    fall? A monotone gradient is Bradford Hill's biological-gradient viewpoint
    and is far harder to produce by coincidence than a simple correlation."""
    ws, we = window
    bs, be = ws - timedelta(days=baseline_days + 1), ws - timedelta(days=1)
    where, _ = persona.sql_where(None)
    clause = where or "WHERE 1=1"
    q = """
    WITH late AS (
      SELECT o.account_id, SUM(CASE WHEN d.on_time THEN 0 ELSE 1 END) AS late_n
      FROM orders o JOIN dispatch d ON d.order_id = o.order_id
      WHERE d.dispatch_date BETWEEN DATE '%s' AND DATE '%s' GROUP BY 1),
    rev AS (
      SELECT account_id,
             AVG(CASE WHEN order_date BETWEEN DATE '%s' AND DATE '%s' THEN net_revenue END) AS post,
             AVG(CASE WHEN order_date BETWEEN DATE '%s' AND DATE '%s' THEN net_revenue END) AS pre
      FROM orders %s GROUP BY 1)
    SELECT r.account_id, COALESCE(l.late_n,0) AS late_n, r.pre, r.post
    FROM rev r LEFT JOIN late l ON l.account_id = r.account_id
    WHERE r.pre IS NOT NULL AND r.post IS NOT NULL AND r.pre > 0
    """ % (bs, we, ws, we, bs, be, clause)
    df = estate.sql(q, tel, "source", "per-account exposure vs response")
    if len(df) < 12:
        return None
    df["chg"] = df["post"] / df["pre"] - 1.0
    df["bucket"] = pd.cut(df["late_n"], bins=[-0.1, 0.5, 2.5, 5.5, 1e9],
                          labels=["0 late", "1-2 late", "3-5 late", "6+ late"])
    g = df.groupby("bucket", observed=True)["chg"].agg(["mean", "count"]).reset_index()
    g = g[g["count"] >= 3]
    if len(g) < 3:
        return None
    order = list(g["mean"])
    monotone = all(order[i] >= order[i + 1] - 1e-9 for i in range(len(order) - 1))
    from scipy import stats
    rho, p = stats.spearmanr(df["late_n"], df["chg"])
    tel.method("source", MethodType.STATISTICS, "dose-response gradient",
               "accounts with more late deliveries should show larger revenue falls; "
               "a monotone gradient is much harder to produce by coincidence than a "
               "single correlation (Bradford Hill: biological gradient)",
               detail="spearman rho=%.3f p=%.4f monotone=%s" % (rho, p, monotone))
    return {"buckets": [{"exposure": str(r["bucket"]), "mean_change_pct": float(r["mean"]),
                         "n_accounts": int(r["count"])} for _, r in g.iterrows()],
            "spearman_rho": float(rho), "p_value": float(p), "monotone": bool(monotone)}


def _effect_onset(movement, window: Tuple[date, date]) -> Optional[date]:
    """Onset of the CURRENT episode.

    We walk backwards from the end of the window through the run of days that
    breach the expected band (tolerating single-day recoveries) and return where
    that run began. Taking the first breach anywhere in a pre-roll would latch
    onto an unrelated earlier dip; taking the first breach inside the window
    would miss an effect that started before it.
    """
    ws, we = window
    lo = ws - timedelta(days=28)
    rows = [r for r in movement.series if r["e"] is not None
            and lo <= date.fromisoformat(r["d"]) <= we]
    if not rows or not movement.sigma:
        return None
    sig = movement.sigma
    breach = [(date.fromisoformat(r["d"]), (r["v"] - r["e"]) < -1.0 * sig) for r in rows]

    i = len(breach) - 1
    while i >= 0 and not breach[i][1]:
        i -= 1
    if i < 0:
        return None
    j, last_true = i, i
    gap = 0
    while j >= 0:
        if breach[j][1]:
            last_true = j
            gap = 0
        else:
            gap += 1
            if gap > 1:
                break
        j -= 1
    return breach[last_true][0]


def precedence(hyp: Dict[str, Any], estate: Estate, persona: Persona, tel: Telemetry,
               movement, window: Tuple[date, date], lag_days: int
               ) -> Optional[Dict[str, Any]]:
    """Temporal precedence: did the cause actually start, and did it start BEFORE
    the effect, within the mechanism lag the graph declares? A cause that begins
    after its effect is not a cause, however plausible it reads."""
    ws, we = window
    onset: Optional[date] = None
    how = ""
    dt = hyp["driver_type"]
    if dt == "external_market" and hyp.get("event"):
        onset = date.fromisoformat(str(hyp["event"]["start_date"])[:10])
        how = "market event calendar start date"
    elif dt == "price_change":
        where, _ = persona.sql_where(movement.filters)
        q = ("SELECT order_date AS d, SUM(net_revenue)/NULLIF(SUM(units),0) AS asp "
             "FROM orders %s AND order_date BETWEEN DATE '%s' AND DATE '%s' "
             "GROUP BY 1 ORDER BY 1" % (where or "WHERE 1=1", ws - timedelta(days=45), we))
        df = estate.sql(q, tel, "source", "price-onset probe")
        if len(df) > 20:
            df["d"] = pd.to_datetime(df["d"])
            df["sm"] = df["asp"].rolling(7, min_periods=4).mean()
            base = df[df["d"] < pd.Timestamp(ws)]["sm"].head(21).median()
            df["hit"] = df["sm"] > base * 1.02
            run = df["hit"].rolling(3).sum()
            idx = run[run >= 3].index
            if len(idx):
                onset = df.loc[idx[0], "d"].date() - timedelta(days=2)
                how = ("first sustained 3-day break where the 7-day mean realised price "
                       "exceeded the prior median by >2%")
    elif dt == "service_failure":
        wh = (hyp.get("exposure") or {}).get("warehouse_id")
        if wh:
            q = ("SELECT dispatch_date AS d, AVG(CASE WHEN on_time THEN 1.0 ELSE 0.0 END) AS otd "
                 "FROM dispatch WHERE warehouse_id='%s' AND dispatch_date BETWEEN DATE '%s' "
                 "AND DATE '%s' GROUP BY 1 ORDER BY 1" % (wh, ws - timedelta(days=60), we))
            df = estate.sql(q, tel, "source", "SLA-onset probe")
            if len(df) > 20:
                df["d"] = pd.to_datetime(df["d"])
                df["sm"] = df["otd"].rolling(7, min_periods=4).mean()
                base = df["sm"].head(21).median()
                df["hit"] = df["sm"] < base - 0.10
                run = df["hit"].rolling(3).sum()
                idx = run[run >= 3].index
                if len(idx):
                    onset = df.loc[idx[0], "d"].date() - timedelta(days=2)
                    how = ("first sustained 3-day break where the 7-day mean on-time rate "
                           "fell 10pp below its prior median")
    if onset is None:
        return None

    eff = _effect_onset(movement, window)
    gap = (eff - onset).days if eff else None
    ok = eff is not None and -3 <= gap <= lag_days + 10
    tel.method("source", MethodType.STATISTICS, "temporal precedence + lag consistency",
               "the cause must start before the effect, and the gap must match the lag "
               "the causal graph declares for that mechanism; a cause that starts after "
               "its effect is discarded no matter how plausible the story reads",
               detail="cause_onset=%s effect_onset=%s gap=%sd declared_lag=%dd ok=%s"
                      % (onset, eff, gap, lag_days, ok))
    return {"cause_onset": str(onset), "cause_onset_method": how,
            "effect_onset": str(eff) if eff else None, "gap_days": gap,
            "declared_lag_days": lag_days, "consistent": bool(ok)}


# ------------------------------------------------------------------ hypotheses
def build_hypotheses(contract: Contract, movement, split_res: Dict[str, Any],
                     estate: Estate, tel: Telemetry) -> List[Dict[str, Any]]:
    """Candidate generation is RULE-BASED, driven by the contribution scan, the
    causal graph and the event calendar. An LLM may add candidates later
    (narrate.expand_hypotheses) but may never remove or rank them."""
    hyps: List[Dict[str, Any]] = []
    by_dim = split_res.get("by_dimension", {})
    ident = split_res.get("identity")

    wh = by_dim.get("warehouse_id", [])
    if wh and abs(wh[0]["explanatory_power"]) > 0.25:
        top = wh[0]
        hyps.append({
            "id": "H-SLA", "label": "Dispatch SLA failure at %s" % top["value"],
            "cause_node": "warehouse_sla", "effect_node": movement.kpi
            if movement.kpi in [n for n in contract.graph["nodes"]] else "net_revenue",
            "exposure": {"warehouse_id": top["value"]},
            "control": {"warehouse_id": [w for w in ["WH-1", "WH-2", "WH-3", "WH-4"]
                                         if w != top["value"]]},
            "driver_type": "service_failure",
            "query_terms": THEME_TERMS["service_failure"],
            "explanatory_power": top["explanatory_power"]})

    if ident:
        mix = [c for c in ident["components"] if c["name"] == "Mix"]
        if mix and abs(mix[0]["pct_of_move"]) > 0.05:
            hyps.append({
                "id": "H-MIX", "label": "Product tier mix shifted toward Discount",
                "cause_node": "tier_mix", "effect_node": "net_revenue",
                "exposure": {}, "control": {}, "driver_type": "mix_shift",
                "query_terms": THEME_TERMS["price_objection"],
                "explanatory_power": mix[0]["pct_of_move"],
                "arithmetic_only": True})

    ws, we = date.fromisoformat(movement.window["start"]), date.fromisoformat(movement.window["end"])
    ev = estate.sql(
        "SELECT * FROM market_events WHERE start_date <= DATE '%s' AND end_date >= DATE '%s'"
        % (we, ws - timedelta(days=14)), tel, "source", "overlapping market events")
    scope_region = (movement.filters or {}).get("region")
    for _, e in ev.iterrows():
        if scope_region and e["region"] != scope_region:
            continue                      # event cannot touch the slice under analysis
        if e["event_type"] == "competitor_promo":
            hyps.append({
                "id": "H-COMP-%s" % e["event_id"], "label": "Competitor promotion: %s" % e["description"],
                "cause_node": "competitor_promo", "effect_node": "order_volume",
                "exposure": {"region": e["region"], "channel": e["channel"]},
                "control": {"region": e["region"],
                            "channel": [ch for ch in ["Direct", "Distributor",
                                                      "ModernTrade", "Ecommerce"]
                                        if ch != e["channel"]]},
                "driver_type": "external_market",
                "query_terms": THEME_TERMS["competitor_activity"],
                "explanatory_power": None, "event": dict(e.astype(str))})

    price = [c for c in split_res.get("contributors", [])
             if c["dimension"] == "category" and abs(c.get("share_shift", 0)) < 0.05]
    if movement.kpi == "order_volume":
        hyps.append({
            "id": "H-PRICE", "label": "Own price increase suppressed volume",
            "cause_node": "price_level", "effect_node": "order_volume",
            "exposure": {}, "control": {}, "driver_type": "price_change",
            "query_terms": THEME_TERMS["price_objection"], "explanatory_power": None})

    for f in movement.data_quality_flags:
        hyps.insert(0, {
            "id": "H-DQ-%s" % f["type"], "label": "Instrumentation: %s" % f["detail"],
            "cause_node": "data_pipeline", "effect_node": movement.kpi,
            "exposure": {}, "control": {}, "driver_type": "instrumentation",
            "query_terms": [], "explanatory_power": None, "data_quality": True})

    tel.method("source", MethodType.RULES, "rule-based hypothesis generation",
               "candidates come from the contribution scan, the curated causal graph and "
               "the event calendar - not from a model, so the candidate set is stable, "
               "auditable and cannot be hallucinated",
               detail="generated %d candidates" % len(hyps))
    return hyps


def grade(hyp: Dict[str, Any], contract: Contract, estate: Estate, persona: Persona,
          tel: Telemetry, movement, window: Tuple[date, date]) -> Dict[str, Any]:
    """Run every rung the evidence will support and return the highest reached."""
    res: Dict[str, Any] = {"tests": {}, "ladder": "L0", "rejected": None}

    # --- admissibility: does a defensible mechanism even exist?
    eff = hyp["effect_node"] if hyp["effect_node"] in contract.graph["nodes"] else "net_revenue"
    paths = contract.paths(hyp["cause_node"], eff)
    if not paths:
        blocked = contract.is_blocked(hyp["cause_node"], eff)
        res["rejected"] = ("no admissible mechanism in the domain causal graph"
                           + (" (explicitly blocked: %s)" % blocked if blocked else ""))
        res["ladder"] = "REJECTED"
        tel.method("source", MethodType.CAUSAL, "causal-graph admissibility check",
                   "a hypothesis with no defensible mechanism is discarded before it is "
                   "scored or shown - this is the graph's only job, and it is what stops "
                   "a fluent model inventing a plausible-sounding cause",
                   detail="%s -> %s REJECTED" % (hyp["cause_node"], eff))
        return res
    res["mechanism_path"] = paths[0]
    res["mechanism_lag_days"] = contract.edge_lag(paths[0])
    res["ladder"] = "L0"

    if hyp.get("data_quality"):
        res["ladder"] = "L2"
        res["tests"]["instrumentation"] = {"detail": hyp["label"]}
        return res

    if hyp.get("arithmetic_only"):
        res["ladder"] = "L3"
        res["tests"]["identity"] = {"detail": "exact arithmetic decomposition, no "
                                              "inference required"}
        return res

    # --- L1: temporal precedence (applies to every driver type)
    pr = precedence(hyp, estate, persona, tel, movement, window,
                    res.get("mechanism_lag_days", 0))
    if pr:
        res["tests"]["precedence"] = pr
        if pr["consistent"]:
            res["ladder"] = "L1"

    # --- L1 reinforcement: dose-response gradient
    if hyp["driver_type"] == "service_failure":
        dr = dose_response(estate, persona, tel, window)
        if dr:
            res["tests"]["dose_response"] = dr
            if dr["p_value"] < 0.05 and dr["spearman_rho"] < 0:
                res["ladder"] = "L1"

    # --- L2: independent corroboration from unstructured evidence
    scope = dict(hyp.get("exposure") or {})
    scope.pop("channel", None)
    docs, withheld = retrieve(estate, persona, tel, hyp["query_terms"], window,
                              scope=scope or None,
                              lag_days=res.get("mechanism_lag_days", 10))
    res["evidence_docs"] = docs
    res["evidence_withheld"] = withheld
    themes = Counter(d.get("theme") for d in docs)
    want = {"service_failure": "service_failure", "external_market": "competitor_activity",
            "price_change": "price_objection", "mix_shift": "price_objection"}.get(
        hyp["driver_type"])
    on_theme = [d for d in docs if d.get("theme") == want]
    independent = len({(d["author_role"], d["type"]) for d in on_theme})
    accounts = len({d["account_id"] for d in on_theme})
    # Conflict: retrieved evidence that supports a DIFFERENT causal theme. A window
    # where half the text points elsewhere is not corroboration, it is disagreement,
    # and it must suppress the rung rather than be averaged away.
    rival_themes = {t for t in THEME_TERMS if t not in (want, "routine")}
    rival = [d for d in docs if d.get("theme") in rival_themes]
    denom = len(on_theme) + len(rival)
    conflict = (len(rival) / float(denom)) if denom else 0.0
    res["tests"]["corroboration"] = {
        "on_theme_docs": len(on_theme), "independent_source_types": independent,
        "distinct_accounts": accounts, "theme_mix": dict(themes),
        "conflicting_docs": len(rival), "conflict_ratio": round(conflict, 3),
        "conflicting_themes": sorted({d.get("theme") for d in rival}),
        "verdict": ("corroborated" if len(on_theme) >= 3 and independent >= 2
                    and accounts >= 2 and conflict < 0.4
                    else "contested" if conflict >= 0.4
                    else "insufficient")}
    if res["tests"]["corroboration"]["verdict"] == "corroborated":
        res["ladder"] = "L2"
    elif conflict >= 0.4:
        res["tests"]["corroboration"]["note"] = (
            "%.0f%% of the retrieved evidence in this window supports a different "
            "explanation (%s); treated as contested, not corroborating"
            % (100 * conflict, ", ".join(res["tests"]["corroboration"]["conflicting_themes"])))
    tel.method("source", MethodType.RETRIEVAL, "evidence agreement vs conflict",
               "counting supporting documents is not enough - text that points at a rival "
               "cause is disagreement, and it suppresses the rung instead of being "
               "averaged away",
               detail="on_theme=%d rival=%d conflict_ratio=%.2f verdict=%s"
                      % (len(on_theme), len(rival), conflict,
                         res["tests"]["corroboration"]["verdict"]))

    # --- L3: counterfactual
    # Runs whenever an untreated cohort exists. It is deliberately NOT gated on the
    # lower rungs: a validated counterfactual is independent structured evidence and
    # needs no text at all. Gating it behind corroboration meant a persona entitled
    # to the numbers but not the CRM verbatims could never reach L3 on evidence that
    # was sitting right there.
    if hyp.get("exposure") and hyp.get("control") and res["ladder"] != "L0" or (
            hyp.get("exposure") and hyp.get("control")):
        did = difference_in_differences(estate, persona, tel, hyp["exposure"],
                                        hyp["control"], window)
        if did:
            res["tests"]["counterfactual"] = did
            if did["parallel_trends_ok"] and did["p_value"] < 0.05:
                res["ladder"] = "L3"
            elif did["parallel_trends_ok"] and did["p_value"] < 0.20 and res["ladder"] == "L0":
                res["ladder"] = "L1"          # directional but not decisive
            else:
                res["tests"]["counterfactual"]["note"] = (
                    "design did not validate - parallel trends failed or the effect is "
                    "inside the noise; L3 withheld")
    return res


def verdict(graded: List[Dict[str, Any]]) -> Dict[str, Any]:
    """CONFIRMED / COMPETING / UNKNOWN / DATA_QUALITY, plus - when we cannot
    separate rival explanations - the cheapest test that would."""
    live = [g for g in graded if g["grade"]["ladder"] not in ("REJECTED",)]
    dq = [g for g in live if g["hyp"].get("data_quality")]
    if dq:
        return {"status": "DATA_QUALITY", "reason":
                "an instrumentation defect explains the movement; no business "
                "explanation is offered until the feed is repaired and the window re-run",
                "leaders": dq}
    strong = [g for g in live if g["grade"]["ladder"] in ("L2", "L3")]
    if len(strong) >= 1:
        # Rank by share-weighted confidence, not by ladder alone. A 10% mix effect
        # that is L3 "for free" (it is an identity, not an inference) must not
        # outrank an 80% service failure that reached L2 on real evidence.
        conf = {"L3": 0.85, "L2": 0.65, "L1": 0.40, "L0": 0.20}
        def _rank(g):
            ep = abs(g["hyp"].get("explanatory_power") or 0.0)
            c = conf.get(g["grade"]["ladder"], 0.2)
            c *= g["grade"].get("prior_weight", 1.0)      # analyst feedback moves this
            return -(max(ep, 0.02) * c)
        strong.sort(key=_rank)
        top = strong[0]
        rivals = [g for g in strong[1:]
                  if g["grade"]["ladder"] == top["grade"]["ladder"]
                  and g["hyp"]["driver_type"] != top["hyp"]["driver_type"]
                  and not g["hyp"].get("arithmetic_only")]
        if rivals and not top["hyp"].get("arithmetic_only"):
            return {"status": "COMPETING", "leaders": [top] + rivals,
                    "reason": "two explanations reach the same evidence level and the "
                              "data cannot separate them"}
        return {"status": "CONFIRMED", "leaders": strong, "reason":
                "at least one hypothesis reached L2 or better with a validated design"}
    weak = [g for g in live if g["grade"]["ladder"] == "L1"]
    if len(weak) >= 2:
        return {"status": "COMPETING", "leaders": weak,
                "reason": "several explanations are merely associated; none is corroborated"}
    return {"status": "UNKNOWN", "leaders": live, "reason":
            "no hypothesis cleared the L2 evidence floor; stating a cause here would be "
            "a guess dressed as a finding"}


def separating_test(v: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """When ambiguity is real, return the cheapest action that resolves it."""
    if v["status"] not in ("COMPETING", "UNKNOWN"):
        return None
    kinds = {g["hyp"]["driver_type"] for g in v.get("leaders", [])}
    if {"price_change", "external_market"} <= kinds:
        return {"question": "Is the volume fall ours (price) or theirs (competitor promo)?",
                "test": "Two-week controlled price test in 6 matched ModernTrade outlets: "
                        "revert the staples increase in 3, hold in 3.",
                "why_it_separates": "a price effect responds to our own change; a "
                                    "competitor promo effect does not",
                "cost_inr": 180000, "days_to_answer": 14, "owner_role": "cfo"}
    if "service_failure" in kinds:
        return {"question": "Is the reorder fall driven by service, or by something else?",
                "test": "Pull and code 20 lost-order call transcripts from the affected "
                        "accounts against a matched sample from unaffected accounts.",
                "why_it_separates": "service complaints should be concentrated in the "
                                    "exposed cohort if the mechanism is real",
                "cost_inr": 40000, "days_to_answer": 4, "owner_role": "data_analyst"}
    return {"question": "What would change our mind?",
            "test": "Instrument the suspected driver for two weeks and re-run this "
                    "analysis with the added series.",
            "why_it_separates": "converts an unobserved driver into an observed one",
            "cost_inr": 0, "days_to_answer": 14, "owner_role": "data_analyst"}
