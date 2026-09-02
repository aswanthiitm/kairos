#!/usr/bin/env python3
"""Headless runner:  python cli.py --scenario S1 --persona cfo [--json]"""
import argparse, json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kairos.pipeline import run, SCENARIOS
from kairos.contract import Contract
from kairos.sources import Estate


def main():
    ap = argparse.ArgumentParser(description="KAIRÓS - KPI intelligence to action")
    ap.add_argument("--scenario", default="S1", choices=list(SCENARIOS))
    ap.add_argument("--persona", default="cfo")
    ap.add_argument("--offline", action="store_true", help="force the deterministic narrator")
    ap.add_argument("--json", action="store_true", help="dump the full result")
    ap.add_argument("--all", action="store_true", help="every scenario x persona")
    ap.add_argument("--sweep", action="store_true", help="estate-wide triage sweep")
    ap.add_argument("--backtest", action="store_true", help="historical replay scorecard")
    ap.add_argument("--reset", action="store_true", help="clear learned state")
    ap.add_argument("--ml", action="store_true",
                    help="model card + holdout scorecard for the ML driver ranker")
    ap.add_argument("--train-ranker", action="store_true",
                    help="retrain the ML driver ranker from the historical corpus")
    ap.add_argument("--build-corpus", action="store_true",
                    help="with --train-ranker: replay the historical estate first")
    ap.add_argument("--include-feedback", action="store_true",
                    help="with --train-ranker: fold in analyst-graded runs")
    ap.add_argument("--fiscal-period", default=None, metavar="FY2027-Q1",
                    help="analyse a fiscal period instead of the scenario window; "
                         "boundaries come from the contract's fiscal calendar")
    ap.add_argument("--semantics", action="store_true",
                    help="the semantic layer: fiscal calendar, dimension hierarchies "
                         "and competing KPI definitions")
    a = ap.parse_args()
    C = Contract()
    E = Estate(C)

    if a.reset:
        from kairos import feedback as FB
        print("reset:", FB.reset()); return

    if a.semantics:
        from datetime import date as _d
        from kairos.hierarchy import available_levels
        from kairos.kpi_reconciliation import summarise
        f = C.fiscal
        print("SEMANTIC LAYER")
        print("\n1. FISCAL CALENDAR   %s  (%s)" % (f.key, f.label))
        print("   fiscal year opens in month %d and is named for the year it %ss"
              % (f.start_month, f.year_label))
        for tok in ("FY2026", "FY2027-Q1", "FY2027-Q2", "FY2027-M05"):
            lo, hi = f.period_bounds(tok)
            print("     %-12s %s .. %s" % (tok, lo, hi))
        today = _d(2026, 8, 30)
        print("     %s falls in %s" % (today, f.describe(today)["label"]))

        print("\n2. DIMENSION HIERARCHIES")
        for dim, h in C.hierarchies().items():
            print("   %s: %s  (%d leaves)" % (dim, " -> ".join(h.levels),
                                              h.to_dict()["n_leaves"]))
            for parent in sorted(h.members):
                print("     %-8s -> %s" % (parent, ", ".join(h.members[parent])))
        print("   availability by KPI:")
        for kpi in sorted(C.kpis):
            av = available_levels(C, kpi, "region")
            print("     %-18s available %-18s blocked %s"
                  % (kpi, ",".join(av["available"]),
                     ",".join(b["level"] for b in av["blocked"]) or "-"))

        print("\n3. COMPETING KPI DEFINITIONS")
        for name, rec in C.reconciliations.items():
            d = summarise(rec)
            print("   %s  ->  %s" % (name, d["status"]))
            for k, defn in rec["definitions"].items():
                mark = "SELECTED" if k == d["selected"] else "rejected"
                print("     [%-8s] %-11s %-46s owner=%s"
                      % (mark, k, defn["formula"], defn["owner"]))
            print("     rule: %s | %s" % (d["resolution_rule"], d["reason"]))
            if d["computational_conflict"]:
                print("     conflicting fields: %s"
                      % ", ".join("%s (%s)" % (x["field"], x["kind"])
                                  for x in d["differences"] if x["kind"] == "computational"))
        if C.unresolved_definitions():
            print("\n   UNRESOLVED (engine will abstain): %s"
                  % ", ".join(C.unresolved_definitions()))
        return

    if a.train_ranker:
        from kairos.ml.train import train
        train(build_corpus=a.build_corpus, include_feedback=a.include_feedback)
        return

    if a.ml:
        from kairos.ml.ranker import get as get_ranker
        from kairos.ml.evaluate import render
        r = get_ranker()
        print("ML DRIVER RANKER")
        print("  status    %s" % r.status)
        print("  %s" % r.reason)
        print("  authority %s" % r.summary()["authority"])
        if not r.available:
            print("\n  build one with:  python cli.py --train-ranker --build-corpus")
            return
        s = r.summary()
        print("  objective %s   fusion weight alpha=%.2f (capped by governance)"
              % (s["objective"], r.alpha))
        print("  corpus    %s" % s["training"]["corpus"])
        print()
        print(render(s["holdout"]))
        print()
        print("  strongest features")
        for k, v in s["top_features"].items():
            print("      %-32s %.3f" % (k, v))
        print()
        print("  stated limitations")
        for x in s["limitations"]:
            print("    - %s" % x)
        return

    if a.sweep or a.backtest:
        from datetime import date
        from kairos.security import load_personas
        from kairos.telemetry import Telemetry
        from kairos.triage import sweep, backtest
        P = load_personas(C)[a.persona]; tel = Telemetry()
        if a.sweep:
            r = sweep(C, E, P, tel, (date(2026, 8, 17), date(2026, 8, 30)))
            print("ESTATE SWEEP  %s to %s   (as %s)" % (r["window"]["start"], r["window"]["end"], P.label))
            print("  scanned %d slices -> %d material, %d data-quality, %d insufficient history,"
                  " %d suppressed  (signal-to-noise %.1f%%)"
                  % (r["scanned"], r["material"], r["data_quality"],
                     r["insufficient_history"], r["suppressed"], 100 * r["signal_to_noise"]))
            print("  suppressed because:")
            for x in r["suppression_reasons"]:
                print("    %-14s %3d   %s" % (x["gate"], x["count"], x["meaning"]))
            print("  worklist:")
            for w in r["worklist"][:8]:
                print("    %-18s %-22s z=%7.2f  %+.1f%%"
                      % (w["kpi"], w["slice_label"], w["z"], 100 * w["pct_change"]))
        if a.backtest:
            ev = [(date(2026, 8, 3), date(2026, 8, 30), "WH-3 dispatch SLA collapse")]
            r = backtest(C, E, P, tel, "net_revenue", {"region": "North"}, known_events=ev)
            print("\nBACKTEST  net_revenue / North")
            print("  %d windows, %d alerts (rate %.2f), precision %s, recall %s"
                  % (r["windows_tested"], r["alerts_fired"], r["alert_rate"],
                     r["precision"], r["recall"]))
            for w in r["windows"]:
                print("    %s  %-14s %s" % (w["start"], w["verdict"],
                                            "<- " + w["event"] if w["event"] else ""))
        return

    combos = ([(s, p) for s in SCENARIOS for p in
               ["cfo", "rsm_north", "supply_chain_lead", "data_analyst"]]
              if a.all else [(a.scenario, a.persona)])

    for sc, pk in combos:
        r = run(sc, pk, C, E, force_offline=a.offline,
                fiscal_period=a.fiscal_period)
        if a.json:
            print(json.dumps(r, indent=2, default=str)); continue
        t = r["telemetry"]
        print("=" * 78)
        print("%s  %s   |   %s" % (sc, r["scenario_name"], r["persona"]["label"]))
        print("verdict: %s" % r["verdict"]["status"])
        print("-" * 78)
        print(r["narrative"]["text"])
        if r["separating_test"]:
            print("\nSEPARATING TEST: %s (Rs %s, %d days, owner %s)"
                  % (r["separating_test"]["test"], r["separating_test"]["cost_inr"],
                     r["separating_test"]["days_to_answer"], r["separating_test"]["owner_role"]))
        if r["recommendations"]:
            print("\nRECOMMENDATIONS")
            for x in r["recommendations"]:
                print("  - [%s] %s\n      owner=%s impact=%s conf=%.0f%% review=%dd"
                      % (x["lever"], x["action"][:88], x["owner_role"],
                         ("Rs %.1fL" % (x["expected_impact_inr"] / 1e5))
                         if isinstance(x["expected_impact_inr"], (int, float)) and x["expected_impact_inr"] else "-",
                         100 * x["confidence"], x["monitoring"]["check_in_days"]))
        sm = r.get("semantics") or {}
        if sm.get("fiscal_calendar"):
            fw = sm["fiscal_calendar"]["window"]
            print("\nPERIOD: %s on the %s calendar%s"
                  % (fw["label"], sm["fiscal_calendar"]["key"],
                     "  (requested as %s)" % sm["fiscal_calendar"]["requested_period"]
                     if sm["fiscal_calendar"].get("requested_period") else ""))
        for kpi, d in (sm.get("kpi_definitions") or {}).items():
            if d["status"] not in ("RECONCILED",):
                continue
            print("\nKPI RECONCILIATION: %s  [%s]" % (kpi, d["status"]))
            n = d.get("numeric") or {}
            for k, defn in sorted((C.kpi.get("kpi_definitions", {})
                                   .get(kpi, {}).get("definitions", {})).items()):
                v = (n.get("values") or {}).get(k)
                print("  %-9s %-11s %-46s %s"
                      % ("SELECTED" if k == d["selected"] else "rejected", k,
                         defn["formula"],
                         ("Rs %.4f Cr" % (v / 1e7)) if v is not None else "-"))
            for k, g in (n.get("gaps") or {}).items():
                print("  difference vs %s: Rs %+.4f Cr (%+.2f%%)"
                      % (k, g["absolute_difference"] / 1e7,
                         100 * (g["pct_difference"] or 0)))
            print("  resolution: %s" % d["reason"])
        h = r.get("hierarchy") or {}
        if h.get("supported"):
            print("\nHIERARCHY DRILL-DOWN: %s %s -> %s  (roll-up closes: %s)"
                  % (h["parent_value"], h["parent_level"], h["child_level"],
                     h["roll_up_check"]["closes"]))
            for c in h["children"]:
                if c["move"] is None:
                    continue
                share = ("%6.1f%% of the move" % (100 * c["share_of_move"])
                         if c["share_of_move"] is not None else "")
                print("  %-12s %+12.2f L  %s" % (c["value"], c["move"] / 1e5, share))
        elif h and h.get("reason"):
            print("\nHIERARCHY DRILL-DOWN unavailable: %s" % h["reason"][:120])
        ml = r.get("ml_ranker") or {}
        if ml.get("candidates"):
            print("\nML DRIVER RANKER  (%s, advisory reorder only)" % ml.get("model_version"))
            for hid, m in sorted(ml["candidates"].items(),
                                 key=lambda kv: kv[1]["rank"]):
                print("  #%d  %-20s P(driver)=%.3f%s"
                      % (m["rank"], hid, m["probability"],
                         "  [out of training distribution - score not applied]"
                         if not m["in_distribution"] else ""))
        elif ml.get("status") in ("unavailable", "refused"):
            print("\nML DRIVER RANKER: %s (%s) - ranking on the evidence heuristic alone"
                  % (ml.get("status"), ml.get("reason")))
        print("\ntelemetry: %.0f ms | %d model calls | %d tokens | Rs %.4f | %.0f%% non-LLM"
              % (t["total_ms"], t["llm"]["calls"],
                 t["llm"]["input_tokens"] + t["llm"]["output_tokens"],
                 t["llm"]["inr"], t["method_mix"]["pct_non_llm"]))
        print()


if __name__ == "__main__":
    main()
