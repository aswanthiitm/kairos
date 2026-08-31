"""
Scoring the engine against the ground truth that was planted in data/generate.py.

These are not smoke tests. Each one asserts that the engine recovered a specific
fact we deliberately buried in the synthetic estate, or that it correctly
REFUSED to claim something the data cannot support.
"""
import json, os, sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from whylayer.contract import Contract
from whylayer.sources import Estate
from whylayer.security import load_personas
from whylayer.pipeline import run
from whylayer.narrate import numeric_guard

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GT = json.load(open(os.path.join(ROOT, "data", "generated", "ground_truth.json")))

C = Contract()
E = Estate(C)
P = load_personas(C)


def go(sc, persona="data_analyst"):
    return run(sc, persona, C, E, force_offline=True)


# --------------------------------------------------------------- S1 multi-factor
def test_s1_confirms_the_planted_service_failure():
    r = go("S1")
    assert r["verdict"]["status"] == "CONFIRMED"
    assert r["verdict"]["leader_ids"][0] == "H-SLA", "SLA failure must outrank the mix effect"


def test_s1_reaches_the_counterfactual_rung():
    r = go("S1")
    sla = next(h for h in r["hypotheses"] if h["hyp"]["id"] == "H-SLA")
    assert sla["grade"]["ladder"] == GT["scenarios"]["S1_multi_factor"]["expected_max_ladder"]
    cf = sla["grade"]["tests"]["counterfactual"]
    assert cf["parallel_trends_ok"], "placebo must validate the design before L3 is awarded"
    assert cf["p_value"] < 0.05
    assert cf["did_pp"] < 0, "the planted effect is negative"


def test_s1_recovers_the_three_planted_accounts():
    r = go("S1")
    got = {c["value"] for c in r["split"]["by_dimension"]["account_name"]}
    planted = set(GT["scenarios"]["S1_multi_factor"]["true_drivers"][0]["affected_accounts"])
    assert planted <= got, "all three escalating accounts must surface in the top contributors"


def test_s1_identity_closes_exactly():
    r = go("S1")
    idn = r["split"]["identity"]
    assert abs(idn["residual"]) < 1.0, "volume + mix + rate must reconstruct the movement"


def test_s1_finds_the_mix_shift_as_a_second_driver():
    r = go("S1")
    assert any(h["hyp"]["id"] == "H-MIX" for h in r["hypotheses"])


def test_s1_mechanism_lag_matches_the_declared_graph():
    r = go("S1")
    sla = next(h for h in r["hypotheses"] if h["hyp"]["id"] == "H-SLA")
    pr = sla["grade"]["tests"]["precedence"]
    assert pr["consistent"], "cause must precede effect within the declared mechanism lag"


# ------------------------------------------------------------------ S2 ambiguity
def test_s2_refuses_to_pick_a_single_cause():
    r = go("S2")
    assert r["verdict"]["status"] in ("COMPETING", "UNKNOWN")


def test_s2_returns_a_separating_test_instead_of_a_guess():
    r = go("S2")
    t = r["separating_test"]
    assert t and t["test"] and t["days_to_answer"] > 0
    assert t["cost_inr"] >= 0


def test_s2_recommends_no_intervention():
    r = go("S2")
    assert all(x["abstention"] for x in r["recommendations"])


# --------------------------------------------------------------------- S3 sparse
def test_s3_declines_to_model_a_short_series():
    r = go("S3")
    assert r["verdict"]["status"] == "INSUFFICIENT_HISTORY"
    assert r["movement"]["history_days"] < 42
    assert r["movement"]["sparse"] is True


# --------------------------------------------------------------- S4 data quality
def test_s4_blames_the_pipeline_not_the_business():
    r = go("S4")
    assert r["verdict"]["status"] == "DATA_QUALITY"
    kinds = {f["type"] for f in r["movement"]["data_quality_flags"]}
    assert "PARTIAL_LOAD" in kinds, "missing late-shipment rows must be detected"


# ------------------------------------------------------------- S5 entitlements
def test_rsm_cannot_analyse_another_region():
    r = run("S2", "rsm_north", C, E, force_offline=True)
    assert r["verdict"]["status"] == "ENTITLEMENT_DENIED"
    assert r["movement"] is None, "no data may be returned on a denied request"


def test_supply_chain_never_sees_rupee_values():
    r = run("S1", "supply_chain_lead", C, E, force_offline=True)
    blob = json.dumps(r["evidence_packet"])
    assert "1.85" not in blob and "Cr" not in blob
    assert "net_revenue" in r["evidence_packet"]["restricted_fields"]


def test_cfo_is_denied_call_verbatims():
    r = run("S1", "cfo", C, E, force_offline=True)
    assert len(r["withheld_evidence"]) > 0
    for h in r["hypotheses"]:
        assert not h.get("evidence_docs"), "CFO must receive no raw CRM text"


def test_personas_receive_different_narratives():
    texts = {p: run("S1", p, C, E, force_offline=True)["narrative"]["text"]
             for p in ["cfo", "supply_chain_lead", "data_analyst"]}
    assert len(set(texts.values())) == 3


def test_pii_is_redacted_before_the_prompt():
    r = run("S1", "rsm_north", C, E, force_offline=True)
    blob = json.dumps(r["hypotheses"])
    assert "@" not in blob.replace("example", "") or "redacted" in blob
    assert "+91" not in blob


# --------------------------------------------------------------- guard + graph
def test_numeric_guard_rejects_invented_figures():
    packet = {"delta_display": "Rs -1.85 Cr", "pct_change": -16.1}
    ok, _ = numeric_guard("Revenue fell Rs 1.85 Cr, down 16.1%.", packet)
    assert ok
    bad_ok, bad = numeric_guard("Revenue fell Rs 4.2 Cr and margin lost 9.7%.", packet)
    assert not bad_ok and set(bad) == {"4.2", "9.7"}


def test_causal_graph_blocks_impossible_mechanisms():
    assert C.paths("weather", "reorder_behaviour") == []
    assert C.is_blocked("competitor_promo", "otd_pct")
    assert C.paths("warehouse_sla", "net_revenue"), "the real mechanism must remain admissible"


def test_llm_is_never_the_source_of_a_number():
    r = go("S1")
    mix = r["telemetry"]["method_mix"]
    assert mix["quantitative_steps"] > 0
    assert mix["pct_non_llm"] == 100.0, "offline run must be entirely non-LLM"
    for m in r["telemetry"]["methods"]:
        if m["method"] == "LLM":
            assert "narrative" in m["what"].lower() or "hypothes" in m["what"].lower()


def test_every_kpi_is_declared_in_the_contract():
    with pytest.raises(KeyError):
        C.get_kpi("made_up_metric")


def test_telemetry_reports_latency_and_cost():
    r = go("S1")
    t = r["telemetry"]
    assert t["total_ms"] > 0
    assert [s["stage"] for s in t["stages"]] == ["sift", "split", "source", "solve", "narrate"]
    assert "usd" in t["llm"] and "inr" in t["llm"]
