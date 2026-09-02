"""
STAGE 4 - SOLVE:  what should someone actually do?

Recommendations are RETRIEVED from playbook memory, not invented. Each one is
a past intervention with a measured outcome, matched to the current pattern and
rescaled to the money currently at risk.

Every recommendation is emitted in the structure the brief asks for:
    driver -> controllable lever -> action -> expected impact -> owner
           -> confidence -> monitoring plan
An action whose lever is not declared in the contract is not emitted at all,
which is what keeps the engine inside real decision rights.
"""
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from .contract import Contract
from .telemetry import Telemetry, MethodType

LADDER_CONF = {"L3": 0.85, "L2": 0.65, "L1": 0.40, "L0": 0.20, "REJECTED": 0.0}


def _similarity(pb: Dict[str, Any], hyp: Dict[str, Any], grade: Dict[str, Any],
                kpi: str) -> float:
    pat = pb["pattern"]
    s = 0.0
    if pat.get("kpi") == kpi:
        s += 0.35
    if pat.get("driver_type") == hyp.get("driver_type"):
        s += 0.40
    a = set(pat.get("mechanism_path") or [])
    b = set(grade.get("mechanism_path") or [])
    if a and b:
        s += 0.25 * len(a & b) / float(len(a | b))
    return round(s, 4)


def recommend(contract: Contract, tel: Telemetry, kpi: str, verdict: Dict[str, Any],
              movement, split_res: Dict[str, Any],
              playbook_store: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    pbs = playbook_store if playbook_store is not None else contract.playbooks["playbooks"]
    levers = contract.levers()
    out: List[Dict[str, Any]] = []

    # abstention: no action is a legitimate - and often correct - recommendation
    if verdict["status"] in ("UNKNOWN", "COMPETING", "INSUFFICIENT_HISTORY"):
        lv = levers["monitor_only"]
        out.append({
            "driver": "undetermined",
            "lever": "monitor_only", "lever_label": lv["label"],
            "action": "Do not intervene yet. Run the separating test below, then re-run "
                      "this analysis with the result.",
            "expected_impact_inr": 0, "expected_impact_note":
                "acting on the wrong cause has a negative expected value here",
            "owner_role": lv["owner_role"], "decision_right": lv["decision_right"],
            "lead_time_days": 0, "confidence": 0.25, "confidence_basis":
                "no hypothesis cleared the L2 evidence floor",
            "monitoring": {"metric": kpi, "check_in_days": 7,
                           "success_criterion": "the separating test returns a signal"},
            "source_playbook": None, "abstention": True})
        tel.method("solve", MethodType.RULES, "abstention as a recommendation",
                   "when evidence is contradictory the highest-value action is usually "
                   "the cheapest test, not an intervention; recommending nothing is a "
                   "supported outcome rather than a failure of the engine")
        return out

    if verdict["status"] == "DATA_QUALITY":
        out.append({
            "driver": "instrumentation",
            "lever": "monitor_only", "lever_label": levers["monitor_only"]["label"],
            "action": "Repair the feed and re-run the window before any business "
                      "interpretation is circulated.",
            "expected_impact_inr": 0,
            "expected_impact_note": "the movement is a measurement artefact",
            "owner_role": "data_analyst", "decision_right": "Data Engineering",
            "lead_time_days": 1, "confidence": 0.9,
            "confidence_basis": "feed completeness test",
            "monitoring": {"metric": kpi, "check_in_days": 1,
                           "success_criterion": "row counts return to baseline"},
            "source_playbook": None, "abstention": True})
        return out

    for g in verdict.get("leaders", [])[:3]:
        hyp, grade = g["hyp"], g["grade"]
        if hyp.get("arithmetic_only") and len(verdict.get("leaders", [])) > 1:
            pass  # mix effects still get a recommendation, just a pricing one
        ranked = sorted(((_similarity(pb, hyp, grade, kpi), pb) for pb in pbs),
                        key=lambda t: -t[0])
        sim, pb = ranked[0] if ranked else (0.0, None)
        at_risk = abs(hyp.get("explanatory_power") or 0.0) * abs(movement.delta)

        if pb is None or sim < 0.45:
            tel.method("solve", MethodType.RULES, "no playbook match",
                       "similarity %.2f is below the 0.45 floor; we do not dress a "
                       "generic suggestion up as institutional memory" % sim)
            continue

        recovered = (pb["outcome"].get("at_risk_volume_recovered_pct")
                     or pb["outcome"].get("asp_recovered_pct")
                     or pb["outcome"].get("volume_defended_pct") or 0.0)
        conf = LADDER_CONF.get(grade["ladder"], 0.2)
        conf *= {"high": 1.0, "medium": 0.85, "low": 0.6}.get(
            pb["outcome"].get("confidence", "medium"), 0.85)
        conf *= min(1.0, 0.7 + 0.3 * sim)

        for act in pb["actions"]:
            lv = levers.get(act["lever"])
            if lv is None:
                continue                       # lever not in contract -> not emitted
            out.append({
                "driver": hyp["label"],
                "driver_ladder": grade["ladder"],
                "lever": act["lever"], "lever_label": lv["label"],
                "action": act["detail"],
                "expected_impact_inr": round(at_risk * recovered / max(1, len(pb["actions"])), 0),
                "expected_impact_note":
                    "%.0f%% of at-risk value, the rate this playbook actually achieved "
                    "in %s (%s)" % (100 * recovered, pb["applied_on"][:4],
                                    pb["outcome"]["measurement_method"]),
                "owner_role": lv["owner_role"], "decision_right": lv["decision_right"],
                "lead_time_days": lv["typical_lead_time_days"],
                "cost_model": lv["cost_model"],
                "confidence": round(conf, 3),
                "confidence_basis": "evidence %s x playbook outcome confidence '%s' "
                                    "x pattern similarity %.2f"
                                    % (grade["ladder"], pb["outcome"].get("confidence"), sim),
                "monitoring": {
                    "metric": "reorder_rate_28d" if hyp["driver_type"] == "service_failure" else kpi,
                    "check_in_days": int(pb["outcome"].get("weeks_to_effect", 4)) * 7,
                    "success_criterion":
                        "at least %.0f%% of the at-risk value recovered by week %d"
                        % (60 * recovered, pb["outcome"].get("weeks_to_effect", 4))},
                "source_playbook": {"id": pb["id"], "title": pb["title"],
                                    "similarity": sim, "n_observations": pb.get("n_observations", 1),
                                    "caveat": pb["outcome"].get("caveat")},
                "abstention": False})

    # biggest expected value first, so the reader's eye lands on the action that matters
    out.sort(key=lambda r: -(r["expected_impact_inr"] * r["confidence"]))

    tel.method("solve", MethodType.RETRIEVAL, "playbook memory match",
               "recommendations are retrieved from interventions this company has "
               "actually run and measured, then rescaled to the value now at risk - "
               "not generated advice",
               detail="matched %d actions from %d playbooks" % (len(out), len(pbs)))
    return out
