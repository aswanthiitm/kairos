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
    a = ap.parse_args()
    C = Contract()
    E = Estate(C)

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
