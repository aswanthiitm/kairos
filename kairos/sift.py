"""
STAGE 1 - SIFT:  is this change real, and does it matter?

Three gates in series. A movement must clear all three before anything
downstream runs. This is the stage that prevents alert fatigue, and it is
entirely non-LLM: decomposition, control-chart logic and business rules.

  gate 0  instrumentation   is the feed complete and fresh? (tested FIRST)
  gate 1  statistical       is it outside the expected band, and persistent?
  gate 2  materiality       does it move plan by enough to be worth a person?
"""
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .contract import Contract
from .security import Persona
from .sources import Estate
from .telemetry import Telemetry, MethodType

MIN_HISTORY_DAYS = 42          # 6 weeks: below this we cannot fit weekly seasonality
SPARSE_INTERVAL_INFLATION = 1.9
PEER_CV_PRIOR = 0.22            # fallback dispersion when a series is too short to fit


def _weekly_index(df: pd.DataFrame) -> Dict[int, float]:
    """Robust multiplicative weekday index from the ratio of actual to a
    centred rolling median. Median-based so the shock we are hunting does not
    contaminate the baseline that is supposed to detect it."""
    s = df.set_index("d")["v"].astype(float)
    trend = s.rolling(7, center=True, min_periods=4).median()
    ratio = (s / trend).replace([np.inf, -np.inf], np.nan).dropna()
    idx = {}
    for wd in range(7):
        vals = ratio[ratio.index.dayofweek == wd]
        idx[wd] = float(np.median(vals)) if len(vals) >= 3 else 1.0
    m = np.mean(list(idx.values())) or 1.0
    return {k: v / m for k, v in idx.items()}


def _expected(df: pd.DataFrame, widx: Dict[int, float], trail: int = 28) -> pd.DataFrame:
    out = df.copy().reset_index(drop=True)
    s = out["v"].astype(float)
    deseason = s / out["d"].dt.dayofweek.map(widx).astype(float)
    # trailing robust level, shifted so today is never used to predict today
    level = deseason.rolling(trail, min_periods=7).median().shift(1)
    out["expected"] = level * out["d"].dt.dayofweek.map(widx).astype(float)
    out["resid"] = out["v"] - out["expected"]
    return out


def _plan_basis(contract: Contract, estate: Estate, persona: Persona, tel: Telemetry,
                filters: Optional[Dict[str, Any]], ws: date, we: date,
                delta: float):
    """Plan for the fiscal months the window touches, prorated by overlap days.

    Filters are applied only for columns the plan table actually carries. Plan is
    set at region x month; asking it for a channel or a warehouse is a grain
    question, and the honest answer is to widen to the grain plan is stated at
    and say so, not to return nothing.
    """
    months = contract.fiscal.months_in_window(ws, we)
    if not months:
        return None, None
    try:
        cols = set(estate.sql("SELECT * FROM plan LIMIT 0", tel, "sift",
                              "plan table columns").columns)
    except Exception:
        return None, None
    usable = {k: v for k, v in (filters or {}).items() if k in cols}
    dropped = sorted(set((filters or {}).keys()) - set(usable))
    row_filter = {k: v for k, v in (persona.row_filter or {}).items() if k in cols}
    clauses = []
    for col, vals in row_filter.items():
        clauses.append("%s IN (%s)" % (col, ", ".join("'%s'" % v for v in vals)))
    for col, val in usable.items():
        if isinstance(val, (list, tuple)):
            clauses.append("%s IN (%s)" % (col, ", ".join("'%s'" % v for v in val)))
        else:
            clauses.append("%s = '%s'" % (col, str(val).replace("'", "''")))
    pairs = " OR ".join("(year = %d AND month = %d)"
                        % (m["calendar_year"], m["calendar_month"]) for m in months)
    clauses.append("(%s)" % pairs)
    q = ("SELECT year, month, SUM(plan_net_revenue) AS p FROM plan WHERE %s "
         "GROUP BY 1, 2" % " AND ".join(clauses))
    try:
        df = estate.sql(q, tel, "sift", "fiscal-period plan target for materiality")
    except Exception:
        return None, None
    if not len(df):
        return None, None
    idx = {(int(r["year"]), int(r["month"])): float(r["p"]) for _, r in df.iterrows()}
    total = 0.0
    parts = []
    for m in months:
        full = idx.get((m["calendar_year"], m["calendar_month"]))
        if full is None:
            continue
        frac = m["days_in_window"] / float(m["days_in_month"])
        total += full * frac
        parts.append({"calendar_year": m["calendar_year"],
                      "calendar_month": m["calendar_month"],
                      "fiscal_year": m["fiscal_year"],
                      "fiscal_month": m["fiscal_month"],
                      "fiscal_quarter": m["fiscal_quarter"],
                      "days_in_window": m["days_in_window"],
                      "days_in_month": m["days_in_month"],
                      "plan_full_month": full,
                      "plan_prorated": full * frac})
    if total <= 0:
        return None, None
    basis = {
        "fiscal_calendar": contract.fiscal.key,
        "periods": sorted({"FY%d-Q%d" % (p["fiscal_year"], p["fiscal_quarter"])
                           for p in parts}),
        "months": parts, "plan_prorated_total": total,
        "filters_applied": usable, "filters_not_on_plan_grain": dropped,
        "summary": "%s prorated over %d fiscal month(s) = %.0f"
                   % (", ".join(sorted({"FY%d-M%02d" % (p["fiscal_year"], p["fiscal_month"])
                                        for p in parts})), len(parts), total),
    }
    if dropped:
        basis["grain_note"] = ("plan is stated at %s grain, so %s could not be applied; "
                               "the denominator is the wider slice and the test is "
                               "correspondingly conservative"
                               % (" x ".join(sorted(cols & {"region", "year", "month"})),
                                  ", ".join(dropped)))
    return (abs(delta) / total), basis


class Movement(object):
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def detect(kpi: str, persona: Persona, estate: Estate, contract: Contract,
           tel: Telemetry, window_start: date, window_end: date,
           filters: Optional[Dict[str, Any]] = None) -> Movement:
    spec = contract.get_kpi(kpi)
    src = spec["source"]

    # ---------------------------------------------------- gate 0: instrumentation
    fresh = [f for f in estate.freshness_report(tel) if f["source"] == src]
    fresh = fresh[0] if fresh else None
    dq_flags: List[Dict[str, Any]] = []
    if fresh and fresh["breached"]:
        dq_flags.append({
            "type": "STALE_FEED", "source": src,
            "detail": "%s is %.0f min behind a %d min SLA (cadence %d min)"
                      % (fresh["system"], fresh["lag_minutes"], fresh["sla_minutes"],
                         fresh["refresh_cadence_minutes"]),
            "implication": "the window may be measuring a partial load, not the business"})

    df = estate.kpi_series(kpi, persona, filters, tel)
    df = df.dropna().sort_values("d").reset_index(drop=True)
    hist_days = len(df)

    # feed-volume check: a metric can 'improve' simply because bad rows stopped arriving
    if src == "dispatch":
        where, _ = persona.sql_where(filters)
        # bounded at the analysis date: the completeness question is "were the
        # rows there when we looked", and rows that landed afterwards would hide
        # exactly the gap this test exists to find
        cnt = estate.sql(
            "SELECT dispatch_date AS d, COUNT(*) AS n, "
            "SUM(CASE WHEN on_time THEN 0 ELSE 1 END) AS late "
            "FROM dispatch %s %s dispatch_date <= DATE '%s' GROUP BY 1 ORDER BY 1"
            % (where or "", "AND" if where else "WHERE", window_end),
            tel, "sift", "dispatch feed row-count profile")
        cnt["d"] = pd.to_datetime(cnt["d"])
        recent = cnt[cnt["d"] >= pd.Timestamp(window_end) - pd.Timedelta(days=6)]
        base = cnt[(cnt["d"] < pd.Timestamp(window_end) - pd.Timedelta(days=6))].tail(28)
        if len(recent) and len(base):
            lr, lb = recent["late"].mean(), base["late"].mean()
            if lb > 0 and lr < 0.35 * lb:
                dq_flags.append({
                    "type": "PARTIAL_LOAD", "source": src,
                    "detail": "late-shipment rows fell to %.1f/day against a %.1f/day "
                              "baseline while total rows held - the failure rows are "
                              "missing, not the failures" % (lr, lb),
                    "implication": "any apparent improvement in this KPI is an artefact"})
        tel.method("sift", MethodType.STATISTICS, "feed completeness profile",
                   "a metric can improve because bad rows stopped loading; we test the "
                   "shape of the feed, not just its timestamp")

    # ------------------------------------------------------- gate 1: statistical
    sparse = hist_days < MIN_HISTORY_DAYS
    widx = _weekly_index(df) if not sparse else {i: 1.0 for i in range(7)}
    if sparse:
        tel.method("sift", MethodType.RULES, "sparse-history fallback",
                   "with %d days of history a weekly seasonal index is not "
                   "identifiable; we fall back to a flat index, widen the interval "
                   "%.1fx and forbid any causal claim downstream"
                   % (hist_days, SPARSE_INTERVAL_INFLATION))
    else:
        tel.method("sift", MethodType.STATISTICS, "robust weekly seasonal index",
                   "median-based decomposition so the anomaly being hunted cannot "
                   "contaminate the baseline used to detect it")

    ex = _expected(df, widx)
    hist = ex[ex["d"] < pd.Timestamp(window_start)].dropna(subset=["resid"])
    mad = float(np.median(np.abs(hist["resid"] - np.median(hist["resid"])))) if len(hist) else 0.0
    sigma = 1.4826 * mad if mad > 0 else (float(hist["resid"].std()) if len(hist) > 2 else 1.0)
    if sparse:
        sigma *= SPARSE_INTERVAL_INFLATION

    # A near-zero sigma on a short series produces absurd z-scores. Fall back to
    # a peer-group dispersion prior rather than pretending to be certain.
    level = float(np.nanmean(df["v"].tail(28))) if len(df) else 0.0
    floor = PEER_CV_PRIOR * abs(level)
    if sigma <= 0 or (sparse and sigma < floor):
        sigma = max(sigma, floor)
        tel.method("sift", MethodType.STATISTICS, "peer-group dispersion prior",
                   "the series is too short to estimate its own variance; we borrow a "
                   "%.0f%% coefficient of variation from comparable established slices "
                   "and widen the band rather than report false precision"
                   % (100 * PEER_CV_PRIOR))

    win = ex[(ex["d"] >= pd.Timestamp(window_start)) & (ex["d"] <= pd.Timestamp(window_end))]
    win = win.dropna(subset=["expected"])
    actual = float(win["v"].sum())
    expected = float(win["expected"].sum())
    delta = actual - expected
    pct = (delta / expected) if expected else 0.0
    n = max(1, len(win))
    z = delta / (sigma * np.sqrt(n)) if sigma else 0.0

    th = spec["thresholds"]
    breaches = win[np.abs(win["resid"]) > th["warn_sigma"] * sigma] if sigma else win.iloc[0:0]
    persistence = int(len(breaches))

    tel.method("sift", MethodType.STATISTICS, "window z-score vs expected band",
               "robust sigma from MAD of pre-window residuals; z on the window sum "
               "so a persistent small drift is not scored like a one-day spike",
               detail="sigma=%.0f n=%d z=%.2f" % (sigma, n, z))

    # ------------------------------------------------------- gate 2: materiality
    mat = spec["materiality"]
    plan_pct, plan_basis = None, None
    if spec.get("plan_column"):
        # "1.5% of period plan" has to mean the plan for the FISCAL period the
        # window falls in, prorated by the days actually covered. The previous
        # form summed every plan row the filter matched and divided by a nominal
        # 30.4-day month, which made the denominator grow with the length of the
        # plan table rather than with the period under analysis.
        plan_pct, plan_basis = _plan_basis(contract, estate, persona, tel,
                                           filters, window_start, window_end, delta)

    checks = {
        "abs_inr": bool(abs(delta) >= mat.get("min_abs_inr", 0)) if spec["unit"] == "INR" else True,
        "pct_of_plan": bool((plan_pct is None) or (plan_pct >= mat.get("min_pct_of_plan", 0))),
        "persistence": bool(persistence >= mat.get("min_persistence_days", 1)),
        "statistical": bool(abs(z) >= th["warn_sigma"]),
    }
    material = all(checks.values())
    tel.method("sift", MethodType.RULES, "materiality gate",
               "statistical significance is not business significance; a movement "
               "must also move plan by a contract-defined amount and persist; the "
               "plan denominator is the fiscal period the window falls in, resolved "
               "from the contract's fiscal calendar rather than a nominal month",
               detail="%s plan_basis=%s" % (checks, (plan_basis or {}).get("summary")))

    verdict = "MATERIAL" if material else "NOT_MATERIAL"
    if dq_flags:
        verdict = "DATA_QUALITY"
    if sparse:
        verdict = "INSUFFICIENT_HISTORY"

    return Movement(
        kpi=kpi, label=spec["label"], unit=spec["unit"], source=src,
        window={"start": str(window_start), "end": str(window_end), "days": n},
        filters=filters or {}, actual=actual, expected=expected, delta=delta,
        pct_change=pct, z=float(z), sigma=float(sigma), persistence_days=persistence,
        history_days=hist_days, sparse=sparse, plan_pct=plan_pct,
        material=material, gate_checks=checks, verdict=verdict,
        fiscal=contract.fiscal_window(window_start, window_end),
        plan_basis=plan_basis,
        data_quality_flags=dq_flags, freshness=fresh,
        lineage=contract.lineage(kpi),
        series=[{"d": d.strftime("%Y-%m-%d"), "v": float(v),
                 "e": (None if pd.isna(e) else float(e))}
                for d, v, e in zip(ex["d"], ex["v"], ex["expected"])][-90:],
    )
