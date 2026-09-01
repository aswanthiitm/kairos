"""
Orchestrator: SIFT -> SPLIT -> SOURCE -> SOLVE -> NARRATE, with telemetry
wrapped around every stage.
"""
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from .contract import Contract
from .security import Persona, load_personas
from .sources import Estate
from .telemetry import Telemetry
from .sift import detect
from .fitness import assess as assess_fitness
from .split import split as do_split
from . import evidence as EV
from .solve import recommend
from .delegation import route as route_decision, summary as delegation_summary
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

    # Data quality runs FIRST and is allowed to stop the run. Olszak & Bartus
    # (Procedia CS 270, 2025) found data availability and quality to be the only
    # barrier cited by every one of their 20 organisations, so it is a gate rather
    # than a caveat appended afterwards.
    with tel.stage("fitness"):
        # every source the run can touch - the target KPI's own feed, plus the
        # dispatch, CRM and market data that hypothesis testing and retrieval use
        fit = assess_fitness(c, e, tel, (ws, we))

    if fit["gate"]["blocks_analysis"]:
        return {
            "scenario": scenario, "scenario_name": sc["name"], "scenario_note": sc["note"],
            "persona": {"key": p.key, "label": p.label, "display": p.display,
                        "channel": p.narrative.get("channel"),
                        "row_restrictions": [], "denied_columns": p.deny_columns,
                        "denied_domains": p.deny_domains, "pii_policy": p.pii_policy},
            "data_fitness": fit,
            "verdict": {"status": "UNFIT_DATA", "leader_ids": [],
                        "reason": "the estate did not clear the data fitness gate for "
                                  "this window, so no causal claim is offered"},
            "movement": None, "split": None, "hypotheses": [], "recommendations": [],
            "separating_test": None, "mechanism_ledger": None, "decision_latency": None,
            "withheld_evidence": [], "advisories": [],
            "narrative": {"text": "Analysis halted. %s Repair the feed and re-run this "
                                  "window." % "; ".join(i["detail"] for i in fit["issues"]
                                                        if i["severity"] == "critical"),
                          "mode": "policy", "guard": {"passed": True, "bad": []},
                          "attempts": 0, "model": None},
            "evidence_packet": {}, "freshness": e.freshness_report(tel),
            "telemetry": tel.summary(),
        }

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

    # How fast could this have been known? The headline benefit reported in Olszak &
    # Bartus was speed of decision-making (17 of 20 respondents), so we compute it
    # rather than assert it.
    latency = None
    for g in graded:
        pr = g["grade"].get("tests", {}).get("precedence")
        if pr and pr.get("cause_onset") and pr.get("effect_onset"):
            need = c.get_kpi(sc["kpi"])["materiality"].get("min_persistence_days", 1)
            eff = date.fromisoformat(pr["effect_onset"])
            cause = date.fromisoformat(pr["cause_onset"])
            detectable = eff + timedelta(days=int(need))
            latency = {
                "cause_onset": str(cause),
                "effect_onset": pr["effect_onset"],
                "engine_could_flag_on": str(detectable),
                "days_cause_to_detectable": (detectable - cause).days,
                "persistence_required_days": int(need),
                "window_close": str(we),
                "days_earlier_than_window_close": (we - detectable).days,
                "note": ("the engine needed %d persistent days after the effect became "
                         "visible, so detection lands %s"
                         % (int(need),
                            ("%d days before this window even closes"
                             % (we - detectable).days) if detectable <= we else
                            ("%d days after this window closes - the movement was still "
                             "building when the period ended"
                             % (detectable - we).days))),
            }
            break

    with tel.stage("solve"):
        pbs = FB.apply_learning(c.playbooks["playbooks"])
        recs = recommend(c, tel, sc["kpi"], vd, mv, sp, playbook_store=pbs)
        for r in recs:
            r["delegation"] = route_decision(c, tel, r, vd["status"],
                                             at_risk_inr=abs(mv.delta))

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
        "data_fitness": fit,
        "decision_latency": latency,
        "delegation_policy": delegation_summary(c),
        "recommendations": recs,
        "narrative": nar,
        "evidence_packet": packet,
        "withheld_evidence": withheld,
        "advisories": advisories,
        "freshness": e.freshness_report(tel),
        "telemetry": tel.summary(),
    }
