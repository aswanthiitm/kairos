"""
Orchestrator: SIFT -> SPLIT -> SOURCE -> SOLVE -> NARRATE, with telemetry
wrapped around every stage.
"""
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from .contract import Contract
from .security import Persona, load_personas
from .sources import Estate
from .telemetry import Telemetry, MethodType
from .sift import detect
from .fitness import assess as assess_fitness
from .split import split as do_split
from . import evidence as EV
from .solve import recommend
from .delegation import route as route_decision, summary as delegation_summary
from .narrate import evidence_packet, narrate
from .propagation import mechanism_ledger
from .hierarchy import drill_down_kpi, available_levels, HierarchyError
from . import kpi_reconciliation as KR
from .ml import ranker as MLR
from . import feedback as FB
from . import caching, delivery
from .drift import data_drift, model_drift
from .forecast import project as forecast_project, with_intervention

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


def _unresolved_definition_result(scenario: str, sc: Dict[str, Any], p: Persona,
                                  c: Contract, rec: Optional[Dict[str, Any]],
                                  tel: Telemetry) -> Dict[str, Any]:
    """Two systems define this KPI differently and the contract declares no rule
    for choosing between them.

    The engine stops. It could pick the first, or the newest, or the larger, and
    any of those would produce a number - which is exactly the problem, because
    the number would carry no authority and nothing downstream would know that.
    Abstaining here is the same discipline the evidence ladder applies to a cause:
    where the basis for a claim is absent, the claim is not made.
    """
    detail = KR.summarise(rec) if rec else {}
    tel.method("semantic", MethodType.RULES, "unresolved KPI definition - abstain",
               "competing definitions with no configured resolution rule; the engine "
               "refuses to choose a definition on the business's behalf, because the "
               "resulting number would look authoritative and would not be",
               detail="%s: %d definitions, no rule" % (sc["kpi"],
                                                      (rec or {}).get("n_definitions", 0)))
    return {
        "scenario": scenario, "scenario_name": sc["name"], "scenario_note": sc["note"],
        "persona": {"key": p.key, "label": p.label, "display": p.display,
                    "channel": p.narrative.get("channel"), "row_restrictions": [],
                    "denied_columns": p.deny_columns, "denied_domains": p.deny_domains,
                    "pii_policy": p.pii_policy},
        "verdict": {"status": "KPI_DEFINITION_UNRESOLVED", "leader_ids": [],
                    "reason": "%s has %d competing definitions and the contract declares "
                              "no resolution rule. %s"
                              % (sc["kpi"], (rec or {}).get("n_definitions", 0),
                                 (rec or {}).get("reason", ""))},
        "semantics": {"kpi_definitions": {sc["kpi"]: detail},
                      "fiscal_calendar": {"key": c.fiscal.key, "label": c.fiscal.label},
                      "hierarchies": {d: h.to_dict() for d, h in c.hierarchies().items()}},
        "movement": None, "split": None, "hierarchy": None, "hypotheses": [],
        "recommendations": [], "separating_test": None, "mechanism_ledger": None,
        "decision_latency": None,
        "ml_ranker": {"status": "not_reached",
                      "reason": "the KPI has no agreed definition, so there is nothing "
                                "to rank candidates against"},
        "withheld_evidence": [], "advisories": [],
        "narrative": {"text": "Analysis halted. %s is defined differently by %s, and no "
                              "resolution rule is configured. Agree an authoritative "
                              "definition before any figure is circulated."
                              % (sc["kpi"],
                                 " and ".join(sorted((rec or {}).get("definitions", {})))),
                      "mode": "policy", "guard": {"passed": True, "bad": []},
                      "attempts": 0, "model": None},
        "evidence_packet": {}, "freshness": [], "telemetry": tel.summary(),
    }


def run(scenario: str, persona_key: str, contract: Optional[Contract] = None,
        estate: Optional[Estate] = None, force_offline: bool = False,
        narrator_mode: str = "auto",
        fiscal_period: Optional[str] = None) -> Dict[str, Any]:
    """Run one investigation.

    ``fiscal_period`` ("FY2027-Q2", "FY2027-M05", "FY2027") replaces the
    scenario's hard-coded window with boundaries resolved from the contract's
    fiscal calendar, so the same analysis can be asked for in the periods the
    business actually closes its books in.
    """
    c = contract or Contract()
    e = estate or Estate(c)
    personas = load_personas(c)
    if persona_key not in personas:
        raise KeyError(persona_key)
    p = personas[persona_key]
    sc = SCENARIOS[scenario]
    tel = Telemetry()

    # ---- SEMANTIC LAYER ---------------------------------------------------
    # Reconciliation happens ONCE, here, and its result is what every stage below
    # reads. No stage re-derives a KPI definition, resolves a fiscal boundary or
    # walks a hierarchy on its own - that duplication is how two parts of one
    # engine end up computing two different "net revenues".
    rec = c.reconciliation(sc["kpi"])
    unresolved = c.unresolved_definitions()
    if rec:
        tel.method("semantic", MethodType.RULES, "KPI definition reconciliation",
                   "two systems define this KPI differently and both are defensible "
                   "inside their own scope; the engine compares them, measures the gap "
                   "and applies the CONFIGURED authority rule rather than picking one "
                   "quietly - and abstains when no rule is declared",
                   detail="%s: %s, %d definitions, selected=%s (%s)"
                          % (sc["kpi"], rec["status"], rec["n_definitions"],
                             rec.get("selected"), rec.get("resolution_rule")))
    if sc["kpi"] in unresolved:
        return _unresolved_definition_result(scenario, sc, p, c, rec, tel)

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
            "semantics": None, "hierarchy": None,
            "verdict": {"status": "ENTITLEMENT_DENIED",
                        "reason": "%s is entitled to %s = %s and requested %s. No data, "
                                  "narrative or evidence is returned for this scope."
                                  % (p.label, conflict["column"],
                                     ", ".join(conflict["entitled_to"]), conflict["requested"]),
                        "leader_ids": []},
            "movement": None, "split": None, "hypotheses": [], "recommendations": [],
            "separating_test": None, "mechanism_ledger": None,
            "ml_ranker": {"status": "not_reached",
                          "reason": "the request was refused before any analysis ran"},
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
    if fiscal_period:
        # Boundaries come from the contract's fiscal calendar, not from month
        # arithmetic done here. Switching fiscal_calendar in the contract moves
        # this window with no code change.
        ws, we = c.period_bounds(fiscal_period)
        tel.method("semantic", MethodType.RULES, "fiscal period resolution",
                   "the analysis window was requested as a fiscal period, so its "
                   "boundaries are resolved from the declared fiscal calendar rather "
                   "than from Gregorian month arithmetic",
                   detail="%s on calendar %r -> %s..%s"
                          % (fiscal_period, c.fiscal.key, ws, we))
    else:
        ws, we = date.fromisoformat(sc["window"][0]), date.fromisoformat(sc["window"][1])

    # The definition SELECTION is settled at contract load and does not depend on
    # the data. How far apart the definitions actually are does, so it is measured
    # here, on the rows under analysis, and reported alongside the answer.
    with tel.stage("semantic"):
        fiscal_ctx = c.fiscal_window(ws, we)
        definitions: Dict[str, Any] = {}
        for name in (sc["kpi"], "net_revenue"):
            r = c.reconciliation(name)
            if not r or name in definitions:
                continue
            r = dict(r)
            try:
                r["numeric"] = KR.measure_difference(
                    r, e, p, (ws, we), sc["filters"], tel,
                    source_spec=c.kpi["sources"][c.get_kpi(name)["source"]])
            except Exception:
                r["numeric"] = None
            definitions[name] = KR.summarise(r)
        if definitions:
            tel.method("semantic", MethodType.SQL,
                       "competing definitions measured on identical rows",
                       "the gap between two systems' definitions is quantified rather "
                       "than described, so the reader sees what the disagreement is "
                       "worth on this window and not merely that it exists",
                       detail="; ".join(
                           "%s %s gap %s" % (
                               k, v["status"],
                               ", ".join("%s %+.0f (%.2f%%)"
                                         % (kk, g["absolute_difference"],
                                            100 * (g["pct_difference"] or 0))
                                         for kk, g in (v.get("numeric") or {})
                                         .get("gaps", {}).items()) or "n/a")
                           for k, v in definitions.items()))
        semantics = {
            "fiscal_calendar": {
                "key": c.fiscal.key, "label": c.fiscal.label,
                "start_month": c.fiscal.start_month,
                "year_label": c.fiscal.year_label,
                "window": fiscal_ctx,
                "requested_period": fiscal_period,
            },
            "hierarchies": {d: h.to_dict() for d, h in c.hierarchies().items()},
            "kpi_definitions": definitions,
            "levels_available": available_levels(c, sc["kpi"], "region"),
        }

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
            "semantics": None, "hierarchy": None,
            "verdict": {"status": "UNFIT_DATA", "leader_ids": [],
                        "reason": "the estate did not clear the data fitness gate for "
                                  "this window, so no causal claim is offered"},
            "movement": None, "split": None, "hypotheses": [], "recommendations": [],
            "separating_test": None, "mechanism_ledger": None, "decision_latency": None,
            "ml_ranker": {"status": "not_reached",
                          "reason": "the data fitness gate stopped the run before "
                                    "candidates existed to rank"},
            "withheld_evidence": [], "advisories": [],
            "narrative": {"text": "Analysis halted. %s Repair the feed and re-run this "
                                  "window." % "; ".join(i["detail"] for i in fit["issues"]
                                                        if i["severity"] == "critical"),
                          "mode": "policy", "guard": {"passed": True, "bad": []},
                          "attempts": 0, "model": None},
            "evidence_packet": {}, "freshness": e.freshness_report(tel),
            "telemetry": tel.summary(),
        }

    with tel.stage("drift"):
        # Only the DATA half runs here: it asks whether the baseline SIFT is about
        # to fit still describes the estate. The MODEL half needs the live feature
        # vectors and runs after the ranker, below.
        drift = {"data": data_drift(e, tel, (ws, we)),
                 "model": {"kind": "model", "verdict": "UNKNOWN",
                           "authority_withdrawn": False,
                           "reading": "not evaluated - the ranker did not run"}}

    with tel.stage("sift"):
        mv = detect(sc["kpi"], p, e, c, tel, ws, we, sc["filters"])

    with tel.stage("split"):
        # The measure column comes from the reconciled definition, not from the
        # scenario. If the contract named Operations authoritative, every number
        # below would decompose their figure instead - with no change here.
        measure_col = (c.measure_column(sc["kpi"])
                       if (c.get_kpi(sc["kpi"]).get("aggregation") or {}).get("column")
                       else sc["measure"])
        sp = do_split(e, p, tel, (ws, we), filters=sc["filters"],
                      measure=measure_col, contract=c)
        # Hierarchy drill-down: the contract declares region -> city, so a regional
        # movement is attributed to its declared children through the same governed
        # aggregation, with the roll-up checked to close back to the parent.
        drill = None
        parent_region = (sc["filters"] or {}).get("region")
        if parent_region:
            try:
                drill = drill_down_kpi(c, e, p, tel, sc["kpi"], parent_region,
                                       (ws, we),
                                       filters={k: v for k, v in sc["filters"].items()
                                                if k != "region"})
            except HierarchyError as exc:
                drill = {"supported": False, "reason": str(exc)}

    with tel.stage("source"):
        hyps = EV.build_hypotheses(c, mv, sp, e, tel)
        pri = FB.priors()
        graded = []
        for h in hyps:
            g = EV.grade(h, c, e, p, tel, mv, (ws, we))
            g["prior_weight"] = (pri.get(h["id"], {}) or {}).get("weight", 1.0)
            graded.append({"hyp": h, "grade": g})

        # ---- ML DRIVER RANKER --------------------------------------------
        # A learned prior over candidates, fitted on resolved historical episodes.
        # It runs AFTER the evidence tests, because the features that separate a
        # real driver from a plausible one - lag alignment, dose-response, conflict
        # ratio, a validated counterfactual - do not exist until those tests have
        # run. It is the last input to RANKING and no input at all to admissibility:
        # the graph decided what is a candidate, the ladder decided what each one
        # earned, and neither is visible to the model.
        mlr = MLR.get()
        ml_scores = mlr.score(graded, mv, sp, include_vector=True)
        ml_block: Dict[str, Any] = mlr.summary()
        if ml_scores:
            snap: Dict[str, Any] = {}
            for g, m in zip(graded, ml_scores):
                if not m:
                    continue
                vec = m.pop("vector", None)
                g["grade"]["ml"] = m
                if vec is not None:
                    snap[g["hyp"]["id"]] = {
                        "vector": vec, "driver_type": g["hyp"].get("driver_type"),
                        "ladder": g["grade"].get("ladder"),
                        "heuristic_score": EV.heuristic_rank_score(g),
                        "feature_snapshot_id": m.get("feature_snapshot_id")}
            FB.save_feature_snapshot(tel.run_id, {
                "run_id": tel.run_id, "scenario": scenario, "persona": p.key,
                "kpi": sc["kpi"], "window": sc["window"],
                "feature_contract": ml_block.get("feature_contract"),
                "model_version": ml_block.get("model_version"),
                "n_candidates": len(graded), "candidates": snap})
            scored = [m for m in ml_scores if m]
            ml_block["candidates"] = {g["hyp"]["id"]: g["grade"]["ml"]
                                      for g in graded if g["grade"].get("ml")}
            try:
                drift["model"] = model_drift(tel, ml_block)
                if drift["model"].get("authority_withdrawn"):
                    ml_block["authority"] = ("WITHDRAWN this run - live features sit "
                                             "outside the training support, so the "
                                             "learned ordering was not applied")
                    for g in graded:
                        g["grade"].pop("ml", None)
            except Exception as ex:
                drift["model"]["reading"] = "model drift check failed: %s" % type(ex).__name__
            tel.method("source", MethodType.ML, "learned driver-ranker prior",
                       "the hand-designed ranking multiplies by explanatory power, so a "
                       "driver that carries no share of the movement - a competitor "
                       "promotion, a price move - can never rank first however strong "
                       "its evidence; a gradient-boosted ranker trained on resolved "
                       "episodes learns that trade-off from outcomes instead of from a "
                       "constant someone chose. It reorders and does nothing else: it "
                       "is not shown the ladder rung and cannot change one",
                       detail="model=%s scored=%d/%d candidates ood=%d weight=%.2f "
                              "holdout top-1 %s vs heuristic %s"
                              % (mlr.version, len(scored), len(graded),
                                 sum(0 if m["in_distribution"] else 1 for m in scored),
                                 mlr.alpha,
                                 ((ml_block.get("holdout") or {}).get("ranking", {})
                                  .get("fused", {}).get("top1_accuracy")),
                                 ((ml_block.get("holdout") or {}).get("ranking", {})
                                  .get("heuristic", {}).get("top1_accuracy"))))
        else:
            tel.method("source", MethodType.RULES, "ML driver-ranker not applied",
                       "the learned prior is optional by design - with no usable model "
                       "artefact the engine ranks on the evidence heuristic alone and "
                       "says so, rather than degrading quietly",
                       detail="%s: %s" % (mlr.status, mlr.reason))

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

    with tel.stage("forecast"):
        fc = forecast_project(mv, tel)

    with tel.stage("solve"):
        pbs = FB.apply_learning(c.playbooks["playbooks"])
        recs = recommend(c, tel, sc["kpi"], vd, mv, sp, playbook_store=pbs)
        for r in recs:
            r["delegation"] = route_decision(c, tel, r, vd["status"],
                                             at_risk_inr=abs(mv.delta))

    if fc and recs:
        fc["with_intervention"] = with_intervention(fc, recs[0])

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
        "hierarchy": drill,
        "semantics": semantics,
        "hypotheses": [{"hyp": g["hyp"], "grade": {k: v for k, v in g["grade"].items()
                                                   if k != "evidence_docs"},
                        "evidence_docs": g["grade"].get("evidence_docs", [])}
                       for g in graded],
        "verdict": {"status": vd["status"], "reason": vd["reason"],
                    "leader_ids": [g["hyp"]["id"] for g in vd.get("leaders", [])]},
        "ml_ranker": ml_block,
        "separating_test": sep,
        "mechanism_ledger": ledger,
        "drift": drift,
        "forecast": fc,
        "corrections": FB.correction_notes(sc["kpi"], [g["hyp"]["id"] for g in graded]),
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
