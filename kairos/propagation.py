"""
MECHANISM LEDGER - quantifying each hop of the causal chain.

Every product in this space stops at "warehouse SLA explains the revenue drop".
None of them shows the chain arithmetic:

    on-time delivery  -24.5pp
        -> 28-day reorder rate  -11.2pp
            -> order volume  -18,400 units
                -> net revenue  -Rs 1.70 Cr

The causal graph already declares the mechanism. Here we go and MEASURE each hop
against the same untreated cohort, so the reader can see where the loss is
actually created and which hop to intervene on. Latent nodes (customer trust)
are named and explicitly marked as unobserved rather than quietly skipped.

Everything here is SQL and arithmetic. No model is involved.
"""
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .contract import Contract
from .security import Persona
from .sources import Estate
from .telemetry import Telemetry, MethodType

UNIT_LABEL = {"INR": "Rs", "units": "units", "pct": "pp", "INR_per_unit": "Rs/unit"}


def _cohort_change(estate: Estate, persona: Persona, tel: Telemetry, kpi: str,
                   filters: Optional[Dict[str, Any]], window: Tuple[date, date],
                   baseline_days: int) -> Optional[Dict[str, float]]:
    ws, we = window
    bs, be = ws - timedelta(days=baseline_days + 1), ws - timedelta(days=1)
    try:
        s = estate.kpi_series(kpi, persona, filters, tel)
    except Exception:
        return None
    if s is None or not len(s):
        return None
    s = s.dropna()
    win = s[(s["d"] >= pd.Timestamp(ws)) & (s["d"] <= pd.Timestamp(we))]["v"]
    base = s[(s["d"] >= pd.Timestamp(bs)) & (s["d"] <= pd.Timestamp(be))]["v"]
    if not len(win) or not len(base) or base.mean() == 0:
        return None
    return {"window_mean": float(win.mean()), "baseline_mean": float(base.mean()),
            "abs_change": float(win.mean() - base.mean()),
            "pct_change": float(win.mean() / base.mean() - 1.0)}


def mechanism_ledger(contract: Contract, estate: Estate, persona: Persona,
                     tel: Telemetry, hyp: Dict[str, Any], grade: Dict[str, Any],
                     window: Tuple[date, date], baseline_days: int = 28
                     ) -> Optional[Dict[str, Any]]:
    path = grade.get("mechanism_path") or []
    if len(path) < 2 or grade.get("ladder") in ("REJECTED", "L0"):
        return None
    nodes = contract.graph["nodes"]
    exposure = hyp.get("exposure") or {}
    control = hyp.get("control") or {}
    if not exposure:
        return None

    def _lag_to_end(i: int) -> int:
        """Cumulative declared lag from path[i] to the end of the chain. A cause that
        acts with a 10-day lag must be MEASURED 10 days earlier than its effect, or
        the shock lands in the baseline and the hop reads as nothing."""
        idx = {(e["from"], e["to"]): e.get("lag_days", 0) for e in contract.edges()}
        return sum(idx.get((a, b), 0) for a, b in zip(path[i:], path[i + 1:]))

    hops: List[Dict[str, Any]] = []
    for pos, name in enumerate(path):
        spec = nodes.get(name) or {}
        obs = spec.get("observable")
        hop: Dict[str, Any] = {"node": name, "label": spec.get("label", name),
                               "type": spec.get("type"), "observable": obs}
        if not obs or obs not in contract.kpis:
            hop["measured"] = False
            hop["note"] = ("latent - not directly instrumented; its effect is bracketed "
                           "by the measured hops on either side"
                           if spec.get("type") == "latent"
                           else "no governed KPI is mapped to this node")
            hops.append(hop)
            continue

        lag = _lag_to_end(pos)
        hop_win = (window[0] - timedelta(days=lag), window[1] - timedelta(days=lag))
        hop["lag_days_to_effect"] = lag
        hop["measured_window"] = {"start": str(hop_win[0]), "end": str(hop_win[1])}
        t = _cohort_change(estate, persona, tel, obs, exposure, hop_win, baseline_days)
        c = _cohort_change(estate, persona, tel, obs, control, hop_win, baseline_days) \
            if control else None
        if t is None:
            hop["measured"] = False
            hop["note"] = "series unavailable for this cohort (entitlement or no rows)"
            hops.append(hop)
            continue

        kspec = contract.get_kpi(obs)
        trailing = kspec.get("trailing_days")
        if trailing and grade.get("tests", {}).get("precedence", {}).get("cause_onset"):
            onset = date.fromisoformat(grade["tests"]["precedence"]["cause_onset"])
            lagged_onset = onset + timedelta(days=lag)
            covered = max(0, min(trailing, (hop_win[1] - lagged_onset).days))
            hop["trailing_days"] = trailing
            hop["window_coverage"] = round(covered / float(trailing), 2)
            if hop["window_coverage"] < 0.9:
                hop["coverage_note"] = (
                    "this is a %d-day trailing metric and only %.0f%% of its window falls "
                    "after the cause began, so the hop understates the damage - the full "
                    "effect is still accumulating"
                    % (trailing, 100 * hop["window_coverage"]))
        hop.update({
            "measured": True, "kpi": obs, "kpi_label": kspec["label"],
            "unit": kspec["unit"], "unit_label": UNIT_LABEL.get(kspec["unit"], ""),
            "treated": t, "control": c,
            "did_pct": (t["pct_change"] - c["pct_change"]) if c else None,
            "attributable_abs": (t["abs_change"] - (t["baseline_mean"] * c["pct_change"]))
            if c else t["abs_change"],
        })
        hops.append(hop)

    measured = [h for h in hops if h.get("measured")]
    if len(measured) < 2:
        return None

    # per-hop transmission: how much of the downstream move travels through this hop
    for i in range(1, len(measured)):
        up, dn = measured[i - 1], measured[i]
        u = up.get("did_pct") if up.get("did_pct") is not None else up["treated"]["pct_change"]
        d = dn.get("did_pct") if dn.get("did_pct") is not None else dn["treated"]["pct_change"]
        dn["transmission_ratio"] = (d / u) if u else None
        dn["reading"] = (
            "each 1%% move upstream in %s corresponds to %.2f%% here"
            % (up["kpi_label"], dn["transmission_ratio"]) if dn.get("transmission_ratio")
            else None)

    tel.method("propagate", MethodType.CAUSAL, "mechanism ledger across the KPI chain",
               "the causal graph declares the mechanism; here every hop with a governed "
               "KPI is measured against the same untreated cohort, so the reader sees "
               "where the loss is created rather than only that it happened",
               detail="path=%s measured_hops=%d" % (" -> ".join(path), len(measured)))
    return {
        "path": path,
        "hops": hops,
        "measured_hops": len(measured),
        "chain_summary": " -> ".join(
            "%s %s" % (h["kpi_label"],
                       ("%+.1fpp" % (100 * h["did_pct"]) if h.get("did_pct") is not None
                        else "%+.1f%%" % (100 * h["treated"]["pct_change"])))
            for h in measured),
        "intervention_point": measured[0]["kpi"],
        "intervention_note":
            "the chain originates at %s - acting further downstream treats a symptom"
            % measured[0]["kpi_label"],
    }
