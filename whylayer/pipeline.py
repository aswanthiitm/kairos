"""
Orchestrator: SIFT -> SPLIT -> SOURCE -> SOLVE -> NARRATE, with telemetry
wrapped around every stage.
"""
from datetime import date
from typing import Any, Dict, List, Optional

from .contract import Contract
from .security import Persona, load_personas
from .sources import Estate
from .telemetry import Telemetry
from .sift import detect
from .split import split as do_split
from . import evidence as EV
from .solve import recommend
from .narrate import evidence_packet, narrate
from .propagation import mechanism_ledger
from . import feedback as FB

SCENARIOS: Dict[str, Dict[str, Any]] = {
    "S1": {"name": "North revenue drop (multi-factor)", "kpi": "net_revenue",
           "window": ("2026-08-17", "2026-08-30"), "filters": {"region": "North"},
           "measure": "net_revenue",
           "note": "two interacting drivers: a service failure and a mix shift"},
    "S2": {"name": "West volume dip (ambiguous)", "kpi": "order_volume",
           "window": ("2026-08-10", "2026-08-24"), "filters": {"region": "West"},
           "measure": "units",
           "note": "a competitor promotion and our own price rise start the same day"},
    "S3": {"name": "New category, sparse history", "kpi": "net_revenue",
           "window": ("2026-08-20", "2026-08-30"),
           "filters": {"category": "ColdPressedOils"}, "measure": "net_revenue",
           "note": "19 days of history - too short to fit seasonality"},
    "S6": {"name": "South modern-trade volume (external cause)", "kpi": "order_volume",
           "window": ("2026-08-12", "2026-08-26"),
           "filters": {"region": "South", "channel": "ModernTrade"}, "measure": "units",
           "note": "the clean counterpart to S2: one cause, an untreated control on the "
                   "other South channels, and corroborating field reports"},
    "S4": {"name": "WH-4 on-time delivery 'improvement'", "kpi": "otd_pct",
           "window": ("2026-08-24", "2026-08-28"), "filters": {"warehouse_id": "WH-4"},
           "measure": "net_revenue",
           "note": "the feed stopped loading late shipments"},
}


def run(scenario: str, persona_key: str, contract: Optional[Contract] = None,
        estate: Optional[Estate] = None, force_offline: bool = False,
        narrator_mode: str = "auto") -> Dict[str, Any]:
    c = contract or Contract()
    e = estate or Estate(c)
    personas = load_personas(c)
    if persona_key not in personas:
        raise KeyError(persona_key)
    p = personas[persona_key]
    sc = SCENARIOS[scenario]
    tel = Telemetry()

    # ---- pre-flight entitlement check -------------------------------------
    # A persona whose row entitlement does not intersect the requested scope is
    # refused explicitly. We never run the query and then return an empty result,
    # because "no data" and "not allowed to see this data" are different answers
    # and conflating them leaks information about what exists.
    conflict = None
    for col, allowed in (p.row_filter or {}).items():
        want = sc["filters"].get(col)
        if want is not None and want not in allowed:
            conflict = {"column": col, "requested": want, "entitled_to": allowed}
            break
    if conflict:
        tel.method("security", "RULES", "row-entitlement pre-flight",
                   "the requested scope lies outside this persona's row entitlement; the "
                   "query is refused before execution rather than returning an empty "
                   "result, which would itself disclose what exists")
        return {
            "scenario": scenario, "scenario_name": sc["name"], "scenario_note": sc["note"],
            "persona": {"key": p.key, "label": p.label, "display": p.display,
                        "channel": p.narrative.get("channel"),
                        "row_restrictions": ["%s in (%s)" % (k, ", ".join(v))
                                             for k, v in (p.row_filter or {}).items()],
                        "denied_columns": p.deny_columns,
                        "denied_domains": p.deny_domains, "pii_policy": p.pii_policy},
            "access_denied": conflict,
            "verdict": {"status": "ENTITLEMENT_DENIED",
                        "reason": "%s is entitled to %s = %s and requested %s. No data, "
                                  "narrative or evidence is returned for this scope."
                                  % (p.label, conflict["column"],
                                     ", ".join(conflict["entitled_to"]), conflict["requested"]),
                        "leader_ids": []},
            "movement": None, "split": None, "hypotheses": [], "recommendations": [],
            "separating_test": None, "mechanism_ledger": None,
            "withheld_evidence": [], "advisories": [],
            "narrative": {"text": "Access denied. %s may only analyse %s = %s. This request "
                                  "targeted %s = %s and was refused before any query ran."
                                  % (p.label, conflict["column"],
                                     ", ".join(conflict["entitled_to"]),
                                     conflict["column"], conflict["requested"]),
                          "mode": "policy", "guard": {"passed": True, "bad": []},
                          "attempts": 0, "model": None},
            "evidence_packet": {}, "freshness": [], "telemetry": tel.summary(),
        }
    ws, we = date.fromisoformat(sc["window"][0]), date.fromisoformat(sc["window"][1])

    with tel.stage("sift"):
        mv = detect(sc["kpi"], p, e, c, tel, ws, we, sc["filters"])

    with tel.stage("split"):
        sp = do_split(e, p, tel, (ws, we), filters=sc["filters"], measure=sc["measure"])

    with tel.stage("source"):
        hyps = EV.build_hypotheses(c, mv, sp, e, tel)
        pri = FB.priors()
        graded = []
        for h in hyps:
            g = EV.grade(h, c, e, p, tel, mv, (ws, we))
            g["prior_weight"] = (pri.get(h["id"], {}) or {}).get("weight", 1.0)
            graded.append({"hyp": h, "grade": g})
        if mv.verdict == "INSUFFICIENT_HISTORY":
            vd = {"status": "INSUFFICIENT_HISTORY", "leaders": [],
                  "reason": "only %d days of history; the series cannot support a "
                            "seasonal baseline, so no causal claim is made and the "
                            "interval is widened using a peer-group prior"
                            % mv.history_days}
        else:
            vd = EV.verdict(graded)
        sep = EV.separating_test(vd)

    with tel.stage("propagate"):
        ledger = None
        if vd.get("leaders"):
            top = vd["leaders"][0]
            ledger = mechanism_ledger(c, e, p, tel, top["hyp"], top["grade"], (ws, we))

    with tel.stage("solve"):
        pbs = FB.apply_learning(c.playbooks["playbooks"])
        recs = recommend(c, tel, sc["kpi"], vd, mv, sp, playbook_store=pbs)

    withheld: List[Dict[str, Any]] = []
    for g in graded:
        withheld += g["grade"].get("evidence_withheld") or []

    with tel.stage("narrate"):
        packet = evidence_packet(mv, sp, vd, recs, sep, p, withheld,
                                 kpi_name=sc["kpi"])
        nar = narrate(packet, p, tel, force_offline=force_offline,
                      mode=narrator_mode)

    # Entitlements can genuinely weaken evidence: a row filter shrinks the control
    # cohort available for a counterfactual. We say so rather than hiding it.
    advisories: List[Dict[str, Any]] = []
    if p.row_filter:
        for g in graded:
            cf = g["grade"]["tests"].get("counterfactual")
            if cf and not cf["parallel_trends_ok"]:
                advisories.append({
                    "type": "ENTITLEMENT_WEAKENED_EVIDENCE",
                    "hypothesis": g["hyp"]["id"],
                    "detail": "your row entitlement (%s) restricts the untreated cohort "
                              "available for the counterfactual, so this hypothesis is "
                              "graded %s here. An unrestricted analyst may see a stronger "
                              "grade on the same movement."
                              % (p.row_filter, g["grade"]["ladder"])})

    _, row_notes = p.sql_where(sc["filters"])
    return {
        "scenario": scenario, "scenario_name": sc["name"], "scenario_note": sc["note"],
        "persona": {"key": p.key, "label": p.label, "display": p.display,
                    "channel": p.narrative.get("channel"),
                    "row_restrictions": row_notes,
                    "denied_columns": p.deny_columns,
                    "denied_domains": p.deny_domains,
                    "pii_policy": p.pii_policy},
        "movement": mv.to_dict(),
        "split": sp,
        "hypotheses": [{"hyp": g["hyp"], "grade": {k: v for k, v in g["grade"].items()
                                                   if k != "evidence_docs"},
                        "evidence_docs": g["grade"].get("evidence_docs", [])}
                       for g in graded],
        "verdict": {"status": vd["status"], "reason": vd["reason"],
                    "leader_ids": [g["hyp"]["id"] for g in vd.get("leaders", [])]},
        "separating_test": sep,
        "mechanism_ledger": ledger,
        "recommendations": recs,
        "narrative": nar,
        "evidence_packet": packet,
        "withheld_evidence": withheld,
        "advisories": advisories,
        "freshness": e.freshness_report(tel),
        "telemetry": tel.summary(),
    }
