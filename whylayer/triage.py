"""
ESTATE-LEVEL OPERATIONS

Two capabilities that answer the two questions a monitoring product is always
asked and rarely answers with a number:

  sweep()     "What should I look at today?" - scan every governed KPI across
              every slice, apply the materiality gate, and report not just what
              survived but HOW MUCH WAS SUPPRESSED AND WHY. Alert fatigue is a
              measurable property of a system, so we measure it.

  backtest()  "How often does this thing cry wolf?" - replay the engine over
              rolling historical windows and score its alert rate against the
              periods where something was actually happening.

Both are pure SIFT: statistics and business rules, no retrieval, no model.
"""
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .contract import Contract
from .security import Persona
from .sources import Estate
from .sift import detect
from .telemetry import Telemetry, MethodType

SLICES: List[Tuple[str, List[Any]]] = [
    ("__all__", [None]),
    ("region", ["North", "South", "East", "West"]),
    ("warehouse_id", ["WH-1", "WH-2", "WH-3", "WH-4"]),
    ("segment", ["Enterprise", "Mid-Market", "SMB"]),
    ("channel", ["Direct", "Distributor", "ModernTrade", "Ecommerce"]),
]

GATE_REASON = {
    "abs_inr": "movement is below the rupee materiality floor in the contract",
    "pct_of_plan": "movement is too small a share of the period plan",
    "persistence": "did not persist for the required number of days",
    "statistical": "inside the expected band once seasonality is removed",
}


def sweep(contract: Contract, estate: Estate, persona: Persona, tel: Telemetry,
          window: Tuple[date, date], kpis: Optional[List[str]] = None) -> Dict[str, Any]:
    ws, we = window
    kpis = kpis or list(contract.kpis)
    rows: List[Dict[str, Any]] = []
    suppressed: Dict[str, int] = {}
    not_sliceable: List[Dict[str, Any]] = []
    errors = 0

    for kpi in kpis:
        for dim, values in SLICES:
            for v in values:
                flt = None if dim == "__all__" else {dim: v}
                if flt and not contract.sliceable(kpi, dim):
                    if not any(n["kpi"] == kpi and n["dimension"] == dim
                               for n in not_sliceable):
                        not_sliceable.append({
                            "kpi": kpi, "dimension": dim,
                            "reason": "%s is measured at %s grain, which does not carry "
                                      "'%s'" % (contract.get_kpi(kpi)["label"],
                                                contract.source_of(kpi)["grain"], dim)})
                    continue
                # a slice the persona cannot see is not scanned at all
                if flt and persona.row_filter:
                    bad = False
                    for col, allowed in persona.row_filter.items():
                        if col in flt and flt[col] not in allowed:
                            bad = True
                    if bad:
                        continue
                try:
                    m = detect(kpi, persona, estate, contract, tel, ws, we, flt)
                except Exception:
                    errors += 1
                    continue
                rec = {
                    "kpi": kpi, "kpi_label": m.label, "slice": flt or {},
                    "slice_label": ("all" if not flt else "%s=%s" % (dim, v)),
                    "verdict": m.verdict, "pct_change": m.pct_change,
                    "delta": m.delta, "z": m.z, "unit": m.unit,
                    "persistence_days": m.persistence_days,
                    "material": m.material,
                    "failed_gates": [g for g, ok in m.gate_checks.items() if not ok],
                    "dq": [f["type"] for f in m.data_quality_flags],
                }
                if not m.material and m.verdict not in ("DATA_QUALITY", "INSUFFICIENT_HISTORY"):
                    for g in rec["failed_gates"]:
                        suppressed[g] = suppressed.get(g, 0) + 1
                rows.append(rec)

    scanned = len(rows)
    material = [r for r in rows if r["verdict"] == "MATERIAL"]
    dq = [r for r in rows if r["verdict"] == "DATA_QUALITY"]
    sparse = [r for r in rows if r["verdict"] == "INSUFFICIENT_HISTORY"]
    material.sort(key=lambda r: -abs(r["z"]))

    tel.method("triage", MethodType.RULES, "estate sweep with suppression accounting",
               "the value of a monitoring layer is mostly in what it does NOT send; "
               "we report the suppression count and reason so alert fatigue is a "
               "measured property rather than a claim",
               detail="scanned=%d material=%d suppressed=%d grain_blocked=%d"
                      % (scanned, len(material), scanned - len(material),
                         len(not_sliceable)))
    return {
        "window": {"start": str(ws), "end": str(we)},
        "scanned": scanned,
        "material": len(material),
        "data_quality": len(dq),
        "insufficient_history": len(sparse),
        "suppressed": scanned - len(material) - len(dq) - len(sparse),
        "suppression_reasons": [
            {"gate": g, "count": n, "meaning": GATE_REASON.get(g, g)}
            for g, n in sorted(suppressed.items(), key=lambda kv: -kv[1])],
        "signal_to_noise": round(len(material) / float(scanned), 4) if scanned else 0.0,
        "errors": errors,
        "not_sliceable": not_sliceable,
        "worklist": material[:12],
        "data_quality_items": dq[:6],
    }


def backtest(contract: Contract, estate: Estate, persona: Persona, tel: Telemetry,
             kpi: str = "net_revenue", filters: Optional[Dict[str, Any]] = None,
             start: date = date(2026, 4, 1), end: date = date(2026, 8, 30),
             window_days: int = 14, step_days: int = 7,
             known_events: Optional[List[Tuple[date, date, str]]] = None) -> Dict[str, Any]:
    """Replay the engine over rolling windows and score it against periods where
    something was genuinely happening."""
    known_events = known_events or []
    windows: List[Dict[str, Any]] = []
    ws = start
    while ws + timedelta(days=window_days - 1) <= end:
        we = ws + timedelta(days=window_days - 1)
        try:
            m = detect(kpi, persona, estate, contract, tel, ws, we, filters)
            fired = m.verdict in ("MATERIAL", "DATA_QUALITY")
            truth = next((lbl for (a, b, lbl) in known_events if not (we < a or ws > b)), None)
            windows.append({"start": str(ws), "end": str(we), "verdict": m.verdict,
                            "fired": fired, "pct_change": round(m.pct_change, 4),
                            "z": round(m.z, 2), "event": truth})
        except Exception:
            pass
        ws += timedelta(days=step_days)

    fired = [w for w in windows if w["fired"]]
    with_event = [w for w in windows if w["event"]]
    tp = len([w for w in fired if w["event"]])
    fp = len(fired) - tp
    fn = len([w for w in with_event if not w["fired"]])
    tel.method("triage", MethodType.STATISTICS, "historical replay scorecard",
               "the engine is scored on how often it fires when nothing was happening; "
               "an explanation engine that cannot report its own false-alarm rate is "
               "asking to be trusted on faith",
               detail="windows=%d fired=%d tp=%d fp=%d fn=%d" % (len(windows), len(fired),
                                                                 tp, fp, fn))
    return {
        "kpi": kpi, "filters": filters or {},
        "windows_tested": len(windows), "alerts_fired": len(fired),
        "alert_rate": round(len(fired) / float(len(windows)), 3) if windows else 0.0,
        "true_positives": tp, "false_positives": fp, "false_negatives": fn,
        "precision": round(tp / float(tp + fp), 3) if (tp + fp) else None,
        "recall": round(tp / float(tp + fn), 3) if (tp + fn) else None,
        "known_events": [{"start": str(a), "end": str(b), "label": l}
                         for a, b, l in known_events],
        "windows": windows,
    }
