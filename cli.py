#!/usr/bin/env python3
"""Headless runner:  python cli.py --scenario S1 --persona cfo [--json]"""
import argparse, json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from whylayer.pipeline import run, SCENARIOS
from whylayer.contract import Contract
from whylayer.sources import Estate


def main():
    ap = argparse.ArgumentParser(description="The Why Layer - KPI intelligence to action")
    ap.add_argument("--scenario", default="S1", choices=list(SCENARIOS))
    ap.add_argument("--persona", default="cfo")
    ap.add_argument("--offline", action="store_true", help="force the deterministic narrator")
    ap.add_argument("--json", action="store_true", help="dump the full result")
    ap.add_argument("--all", action="store_true", help="every scenario x persona")
    ap.add_argument("--sweep", action="store_true", help="estate-wide triage sweep")
    ap.add_argument("--backtest", action="store_true", help="historical replay scorecard")
    ap.add_argument("--reset", action="store_true", help="clear learned state")
    a = ap.parse_args()
    C = Contract()
    E = Estate(C)

    if a.reset:
        from whylayer import feedback as FB
        print("reset:", FB.reset()); return

    if a.sweep or a.backtest:
        from datetime import date
        from whylayer.security import load_personas
        from whylayer.telemetry import Telemetry
        from whylayer.triage import sweep, backtest
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
        r = run(sc, pk, C, E, force_offline=a.offline)
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
        print("\ntelemetry: %.0f ms | %d model calls | %d tokens | Rs %.4f | %.0f%% non-LLM"
              % (t["total_ms"], t["llm"]["calls"],
                 t["llm"]["input_tokens"] + t["llm"]["output_tokens"],
                 t["llm"]["inr"], t["method_mix"]["pct_non_llm"]))
        print()


if __name__ == "__main__":
    main()
