"""
The semantic layer: fiscal calendar, dimensional hierarchy, KPI reconciliation.

All three were previously DECLARED in the contract and consumed by nothing. These
tests exist to make that impossible again: each one fails if the configuration
stops reaching runtime, and several of them fail if the configuration is merely
read rather than obeyed.

The integration tests at the end of each section are the ones that matter. A unit
test on a helper proves a helper works; only an end-to-end run proves the engine
uses it.
"""
import copy
import os
import sys
from datetime import date

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from whylayer.contract import Contract, CFG
from whylayer.sources import Estate
from whylayer.security import load_personas
from whylayer.telemetry import Telemetry
from whylayer.pipeline import run
from whylayer.fiscal import FiscalCalendar, FiscalCalendarError, from_contract
from whylayer.hierarchy import (Hierarchy, HierarchyError, available_levels,
                                drill_down_kpi, kpi_by_level, roll_up_frame)
from whylayer import kpi_reconciliation as KR

C = Contract()
E = Estate(C)
P = load_personas(C)
FC = C.fiscal
W = (date(2026, 8, 17), date(2026, 8, 30))


def go(sc, persona="data_analyst", **kw):
    return run(sc, persona, C, E, force_offline=True, **kw)


def raw_config():
    return yaml.safe_load(open(os.path.join(CFG, "kpi_contract.yaml")))


# ==========================================================================
# 1. FISCAL CALENDAR
# ==========================================================================
def test_the_contract_declares_a_calendar_and_it_is_loadable():
    assert C.kpi["fiscal_calendar"] == "apr_mar"
    assert FC.key == "apr_mar" and FC.start_month == 4 and FC.year_label == "end"


def test_march_31_april_1_is_a_fiscal_year_boundary():
    """The single date pair the whole convention turns on."""
    assert FC.fiscal_year(date(2026, 3, 31)) == 2026
    assert FC.fiscal_year(date(2026, 4, 1)) == 2027
    assert FC.fiscal_quarter(date(2026, 3, 31)) == 4
    assert FC.fiscal_quarter(date(2026, 4, 1)) == 1
    assert FC.fiscal_month(date(2026, 3, 31)) == 12
    assert FC.fiscal_month(date(2026, 4, 1)) == 1


def test_fy2026_is_april_2025_to_march_2026_exactly():
    assert FC.year_bounds(2026) == (date(2025, 4, 1), date(2026, 3, 31))
    assert FC.fiscal_year(date(2025, 4, 1)) == 2026
    assert FC.fiscal_year(date(2026, 3, 31)) == 2026
    assert FC.fiscal_year(date(2025, 3, 31)) == 2025


@pytest.mark.parametrize("month,q,fm", [
    (4, 1, 1), (5, 1, 2), (6, 1, 3), (7, 2, 4), (8, 2, 5), (9, 2, 6),
    (10, 3, 7), (11, 3, 8), (12, 3, 9), (1, 4, 10), (2, 4, 11), (3, 4, 12)])
def test_every_fiscal_quarter_and_month_number(month, q, fm):
    d = date(2026, month, 15)
    assert FC.fiscal_quarter(d) == q
    assert FC.fiscal_month(d) == fm


def test_quarter_boundaries_are_contiguous_and_cover_the_year():
    ys, ye = FC.year_bounds(2027)
    spans = [FC.quarter_bounds(2027, q) for q in (1, 2, 3, 4)]
    assert spans[0][0] == ys and spans[-1][1] == ye
    for a, b in zip(spans, spans[1:]):
        assert (b[0] - a[1]).days == 1, "a gap or overlap between fiscal quarters"


def test_leap_year_february_is_handled():
    assert FC.month_bounds(2024, 11) == (date(2024, 2, 1), date(2024, 2, 29))
    assert FC.month_bounds(2023, 11) == (date(2023, 2, 1), date(2023, 2, 28))
    assert FC.fiscal_year(date(2024, 2, 29)) == 2024
    # a fiscal year containing 29 February is one day longer
    ys, ye = FC.year_bounds(2024)
    assert (ye - ys).days + 1 == 366


def test_a_window_crossing_31_march_is_flagged_not_silently_labelled():
    w = FC.describe_window(date(2026, 3, 25), date(2026, 4, 5))
    assert w["crosses_fiscal_year"] is True
    assert w["start"]["fiscal_year"] == 2026 and w["end"]["fiscal_year"] == 2027
    months = w["fiscal_months_touched"]
    assert [m["fiscal_year"] for m in months] == [2026, 2027]
    assert [m["days_in_window"] for m in months] == [7, 5]
    inside = FC.describe_window(date(2026, 8, 17), date(2026, 8, 30))
    assert inside["crosses_fiscal_year"] is False


def test_period_tokens_resolve_and_bad_ones_are_refused():
    assert FC.period_bounds("FY2027-Q2") == (date(2026, 7, 1), date(2026, 9, 30))
    assert FC.period_bounds("FY2027-M05") == (date(2026, 8, 1), date(2026, 8, 31))
    assert FC.period_bounds("FY2027") == (date(2026, 4, 1), date(2027, 3, 31))
    for bad in ("Q2-2027", "FY27", "FY2027-Q5", "FY2027-M13", "", "last quarter"):
        with pytest.raises(FiscalCalendarError):
            FC.period_bounds(bad)


def test_changing_the_configuration_changes_the_resolved_boundaries():
    """The configuration item has to be a switch, not a label."""
    cfg = raw_config()
    cfg["fiscal_calendar"] = "jan_dec"
    other = Contract(kpi_config=cfg)
    assert other.fiscal.key == "jan_dec"
    assert other.period_bounds("FY2026") == (date(2026, 1, 1), date(2026, 12, 31))
    assert other.fiscal.fiscal_quarter(date(2026, 8, 30)) == 3      # was Q2 under apr_mar
    assert other.fiscal.fiscal_month(date(2026, 8, 30)) == 8        # was 5
    assert C.period_bounds("FY2026") == (date(2025, 4, 1), date(2026, 3, 31))

    cfg["fiscal_calendar"] = "jul_jun"
    third = Contract(kpi_config=cfg)
    assert third.period_bounds("FY2026") == (date(2025, 7, 1), date(2026, 6, 30))


def test_an_undeclared_calendar_is_an_error_not_a_silent_gregorian_fallback():
    cfg = raw_config()
    cfg["fiscal_calendar"] = "apr_mar_v2"
    with pytest.raises(FiscalCalendarError):
        from_contract(cfg)


# ---- integration: the pipeline actually uses the calendar ------------------
def test_the_pipeline_reports_the_fiscal_period_of_its_window():
    f = go("S1")["movement"]["fiscal"]
    assert f["calendar"] == "apr_mar"
    assert f["start"]["fiscal_year"] == 2027 and f["start"]["fiscal_quarter"] == 2
    assert f["start"]["fiscal_month"] == 5
    assert f["crosses_fiscal_year"] is False


def test_an_analysis_can_be_requested_as_a_fiscal_period():
    """The strongest integration proof: the window comes from the calendar."""
    r = go("S1", fiscal_period="FY2027-Q1")
    assert r["movement"]["window"]["start"] == "2026-04-01"
    assert r["movement"]["window"]["end"] == "2026-06-30"
    assert r["semantics"]["fiscal_calendar"]["requested_period"] == "FY2027-Q1"
    assert r["movement"]["fiscal"]["start"]["fiscal_quarter"] == 1
    baseline = go("S1")
    assert r["movement"]["window"] != baseline["movement"]["window"], \
        "the fiscal period must actually replace the scenario window"


def test_materiality_is_measured_against_the_fiscal_period_plan():
    """The plan denominator is the fiscal period, prorated by days covered - not
    a nominal 30.4-day month and not the whole plan table."""
    m = go("S1")["movement"]
    b = m["plan_basis"]
    assert b is not None, "the plan basis must be resolved, not skipped"
    assert b["fiscal_calendar"] == "apr_mar"
    assert b["periods"] == ["FY2027-Q2"]
    assert len(b["months"]) == 1
    mo = b["months"][0]
    assert (mo["calendar_year"], mo["calendar_month"]) == (2026, 8)
    assert mo["fiscal_year"] == 2027 and mo["fiscal_month"] == 5
    assert mo["days_in_window"] == 14 and mo["days_in_month"] == 31
    assert abs(mo["plan_prorated"] - mo["plan_full_month"] * 14 / 31) < 1.0
    assert abs(m["plan_pct"] - abs(m["delta"]) / b["plan_prorated_total"]) < 1e-9


def test_a_plan_window_spanning_two_fiscal_months_prorates_each():
    from whylayer.sift import _plan_basis
    pct, basis = _plan_basis(C, E, P["data_analyst"], Telemetry(), {"region": "North"},
                             date(2026, 5, 20), date(2026, 6, 10), 1_000_000.0)
    assert basis is not None and len(basis["months"]) == 2
    assert [m["calendar_month"] for m in basis["months"]] == [5, 6]
    assert [m["days_in_window"] for m in basis["months"]] == [12, 10]
    assert [m["fiscal_month"] for m in basis["months"]] == [2, 3]
    total = sum(m["plan_full_month"] * m["days_in_window"] / m["days_in_month"]
                for m in basis["months"])
    assert abs(basis["plan_prorated_total"] - total) < 1.0


def test_plan_rows_carry_the_fiscal_coordinates_the_gate_resolves():
    df = E.sql("SELECT DISTINCT fiscal_year, fiscal_quarter, fiscal_month, fiscal_period "
               "FROM plan WHERE year = 2026 AND month = 8", Telemetry())
    assert len(df) == 1
    r = df.iloc[0]
    assert (int(r["fiscal_year"]), int(r["fiscal_quarter"]), int(r["fiscal_month"])) \
        == (2027, 2, 5)
    assert r["fiscal_period"] == "FY2027-Q2"


# ==========================================================================
# 2. REGION -> CITY HIERARCHY
# ==========================================================================
def test_city_exists_in_the_generated_orders():
    df = E.sql("SELECT COUNT(*) n, COUNT(DISTINCT city) c FROM orders WHERE city IS NOT NULL",
               Telemetry())
    assert int(df["n"].iloc[0]) > 0 and int(df["c"].iloc[0]) == 16


def test_every_city_maps_to_exactly_one_region_in_the_data():
    df = E.sql("SELECT city, COUNT(DISTINCT region) AS n FROM orders GROUP BY 1", Telemetry())
    assert set(df["n"]) == {1}, "a city appearing in two regions breaks every roll-up"
    assert len(df) == 16


def test_the_data_agrees_with_the_contract_about_who_belongs_where():
    h = C.hierarchy("region")
    df = E.sql("SELECT DISTINCT region, city FROM orders", Telemetry())
    for _, r in df.iterrows():
        assert h.parent_of(r["city"]) == r["region"], \
            "%s is %s in the data and %s in the contract" % (
                r["city"], r["region"], h.parent_of(r["city"]))


def test_roll_up_city_to_region():
    h = C.hierarchy("region")
    assert h.roll_up("Chennai")["parent_value"] == "South"
    assert h.roll_up("Delhi")["parent_value"] == "North"
    assert h.roll_up("South", "region")["parent_level"] is None
    assert C.level_of("Chennai") == "city" and C.level_of("South") == "region"


def test_drill_down_region_to_city():
    step = C.hierarchy("region").drill_down("South")
    assert step["child_level"] == "city"
    assert set(step["children"]) == {"Chennai", "Bengaluru", "Hyderabad", "Kochi"}
    assert C.hierarchy("region").drill_down("Chennai", "city")["child_level"] is None


def test_an_invalid_level_is_refused_cleanly():
    h = C.hierarchy("region")
    with pytest.raises(HierarchyError):
        h.validate_level("pincode")
    with pytest.raises(HierarchyError):
        h.roll_up("Atlantis")
    with pytest.raises(HierarchyError):
        h.drill_down("Atlantis")
    with pytest.raises(HierarchyError):
        C.hierarchy("segment")          # declares no hierarchy


def test_a_city_claimed_by_two_regions_is_rejected_at_load():
    with pytest.raises(HierarchyError):
        Hierarchy("region", ["region", "city"],
                  {"North": ["Delhi"], "West": ["Delhi", "Mumbai"]})


def test_a_source_that_cannot_resolve_city_says_so_instead_of_inferring_it():
    """The safeguard that matters. Dispatch has no city key; joining through the
    ordering account would produce a number that looks fine and means nothing."""
    a = available_levels(C, "otd_pct", "region")
    assert a["available"] == ["region"]
    assert [b["level"] for b in a["blocked"]] == ["city"]
    assert "no city key" in a["blocked"][0]["reason"]
    assert not C.sliceable("otd_pct", "city")
    res = kpi_by_level(C, E, P["data_analyst"], Telemetry(), "otd_pct", "city", W)
    assert res["supported"] is False and res["rows"] == []
    d = drill_down_kpi(C, E, P["data_analyst"], Telemetry(), "otd_pct", "South", W)
    assert d["supported"] is False and "city" in d["reason"]


def test_additive_roll_up_reproduces_the_parent_exactly():
    d = drill_down_kpi(C, E, P["data_analyst"], Telemetry(), "net_revenue", "North", W)
    assert d["supported"] and d["aggregation"] == "additive"
    assert d["roll_up_check"]["closes"] is True
    assert abs(d["roll_up_check"]["children_rolled_up"]
               - d["roll_up_check"]["parent_measured"]) < 1.0
    assert {c["value"] for c in d["children"]} == set(
        C.hierarchy("region").drill_down("North")["children"])


def test_a_ratio_kpi_is_re_aggregated_not_averaged():
    """Averaging city ASPs weights a city with ten orders like one with ten
    thousand. The roll-up must re-divide the summed parts."""
    d = drill_down_kpi(C, E, P["data_analyst"], Telemetry(), "asp", "South", W)
    assert d["aggregation"] == "ratio" and d["roll_up_check"]["closes"] is True
    kids = d["children"]
    correct = sum(c["numerator"] for c in kids) / sum(c["denominator"] for c in kids)
    naive = sum(c["current"] for c in kids) / len(kids)
    assert abs(correct - d["roll_up_check"]["parent_measured"]) < 1e-6
    assert abs(naive - correct) > 1e-6, \
        "this estate no longer distinguishes the naive average from the true ratio"
    assert d["baseline"]["scale_applied"] is None, \
        "a rate must not be scaled by the length of the window"


def test_a_derived_kpi_refuses_a_sql_roll_up():
    from whylayer.hierarchy import aggregate_sql
    with pytest.raises(HierarchyError):
        aggregate_sql(C, "reorder_rate_28d", "city", "", *W)


# ---- integration: a regional movement names its contributing cities --------
def test_a_regional_movement_identifies_the_contributing_cities():
    r = go("S1")
    h = r["hierarchy"]
    assert h["supported"] and h["parent_value"] == "North" and h["child_level"] == "city"
    assert h["roll_up_check"]["closes"] is True
    worst = h["children"][0]
    assert worst["move"] < 0 and worst["share_of_move"] > 0
    assert sum(c["share_of_move"] for c in h["children"]) == pytest.approx(1.0, abs=1e-6)
    assert C.hierarchy("region").parent_of(worst["value"]) == "North"
    # and the lattice scan reaches city on its own, from the same declaration
    assert "city" in r["split"]["by_dimension"], \
        "the contribution scan must pick up a declared hierarchy level"


def test_the_run_publishes_the_levels_it_could_and_could_not_use():
    s = go("S1")["semantics"]
    assert s["hierarchies"]["region"]["levels"] == ["region", "city"]
    assert s["hierarchies"]["region"]["n_leaves"] == 16
    assert s["levels_available"]["available"] == ["region", "city"]
    assert go("S4")["semantics"]["levels_available"]["blocked"][0]["level"] == "city"


# ==========================================================================
# 3. KPI DEFINITION RECONCILIATION
# ==========================================================================
def test_two_conflicting_definitions_are_declared():
    reg = C.kpi["kpi_definitions"]["net_revenue"]["definitions"]
    assert set(reg) == {"finance", "operations"}
    assert reg["finance"]["formula"] == "gross_sales - discounts - returns"
    assert reg["operations"]["formula"] == "gross_sales - discounts - returns - shipping"


def test_a_computational_conflict_is_detected():
    rec = C.reconciliation("net_revenue")
    assert rec["status"] == KR.STATUS_RECONCILED
    assert rec["computational_conflict"] is True
    fields = {d["field"]: d["kind"] for d in rec["differences"]}
    assert fields["expression"] == "computational"
    assert fields["formula"] == "contextual", \
        "the prose formula describes; the expression decides"


def test_equivalent_definitions_are_recognised_as_equivalent():
    rec = C.reconciliation("order_volume")
    assert rec["status"] == KR.STATUS_EQUIVALENT
    assert rec["computational_conflict"] is False
    assert {d["kind"] for d in rec["differences"]} == {"contextual"}, \
        "same measure, different paperwork, is not a conflict"


def test_configured_authority_precedence_selects_the_intended_definition():
    rec = C.reconciliation("net_revenue")
    assert rec["resolution_rule"] == "authority_precedence"
    assert rec["selected"] == "finance"
    assert "precedence" in rec["reason"] and "finance" in rec["reason"]


def test_the_losing_definition_is_retained_with_the_reason_it_lost():
    rec = KR.summarise(C.reconciliation("net_revenue"))
    assert len(rec["rejected"]) == 1
    lost = rec["rejected"][0]
    assert lost["key"] == "operations"
    assert lost["formula"] == "gross_sales - discounts - returns - shipping"
    assert lost["system"] and lost["owner"] and lost["reason"]
    assert C.kpi["kpi_definitions"]["net_revenue"]["definitions"]["operations"], \
        "the losing definition must not be overwritten in the contract"


def test_the_materialised_columns_really_are_the_declared_formulas():
    """Integrity: each definition's column must equal its formula over base facts,
    or the reconciliation is comparing labels rather than measures."""
    df = E.sql("""SELECT SUM(gross_revenue) - SUM(discount_amount) - SUM(returns_amount)
                         AS fin_formula, SUM(net_revenue) AS fin_column,
                         SUM(gross_revenue) - SUM(discount_amount) - SUM(returns_amount)
                         - SUM(shipping_cost) AS ops_formula,
                         SUM(net_revenue_ops) AS ops_column FROM orders""", Telemetry())
    r = df.iloc[0]
    assert abs(float(r["fin_formula"]) - float(r["fin_column"])) < 1.0
    assert abs(float(r["ops_formula"]) - float(r["ops_column"])) < 1.0
    assert float(r["fin_column"]) > float(r["ops_column"]), \
        "operations expenses freight, so its figure must be the smaller one"


def test_the_numeric_reconciliation_delta_is_correct():
    d = go("S1")["semantics"]["kpi_definitions"]["net_revenue"]["numeric"]
    direct = E.sql("""SELECT SUM(gross_revenue) - SUM(discount_amount) - SUM(returns_amount) AS fin,
                             SUM(gross_revenue) - SUM(discount_amount) - SUM(returns_amount)
                             - SUM(shipping_cost) AS ops FROM orders
                      WHERE region = 'North'
                        AND order_date BETWEEN DATE '2026-08-17' AND DATE '2026-08-30'""",
                   Telemetry()).iloc[0]
    assert abs(d["values"]["finance"] - float(direct["fin"])) < 1.0
    assert abs(d["values"]["operations"] - float(direct["ops"])) < 1.0
    gap = d["gaps"]["operations"]
    assert abs(gap["absolute_difference"] - (float(direct["fin"]) - float(direct["ops"]))) < 1.0
    assert abs(gap["pct_difference"]
               - (float(direct["fin"]) - float(direct["ops"])) / float(direct["fin"])) < 1e-9
    assert gap["absolute_difference"] > 0


def test_a_missing_resolution_rule_produces_abstention_not_a_quiet_choice():
    cfg = raw_config()
    del cfg["kpi_definitions"]["net_revenue"]["resolution"]
    c2 = Contract(kpi_config=cfg)
    assert c2.definition_status("net_revenue") == KR.STATUS_UNRESOLVED
    assert c2.unresolved_definitions() == ["net_revenue"]
    assert c2.reconciliation("net_revenue")["selected"] is None

    e2 = Estate(c2)
    r = run("S1", "data_analyst", c2, e2, force_offline=True)
    assert r["verdict"]["status"] == "KPI_DEFINITION_UNRESOLVED"
    assert r["movement"] is None and r["recommendations"] == []
    assert "no resolution rule" in r["verdict"]["reason"]
    both = r["semantics"]["kpi_definitions"]["net_revenue"]["rejected"]
    assert {x["key"] for x in both} == {"finance", "operations"}, \
        "both definitions stay auditable when neither is selected"


def test_an_empty_precedence_list_is_also_unresolved():
    cfg = raw_config()
    cfg["kpi_definitions"]["net_revenue"]["resolution"]["precedence"] = []
    assert Contract(kpi_config=cfg).definition_status("net_revenue") == KR.STATUS_UNRESOLVED
    cfg2 = raw_config()
    cfg2["kpi_definitions"]["net_revenue"]["resolution"]["precedence"] = ["treasury"]
    assert Contract(kpi_config=cfg2).definition_status("net_revenue") == KR.STATUS_UNRESOLVED


# ---- integration: the selected definition propagates downstream ------------
def test_the_resolved_definition_rewrites_the_kpi_the_engine_computes():
    spec = C.get_kpi("net_revenue")
    assert spec["definition_status"] == KR.STATUS_RECONCILED
    assert spec["definition_source"] == "finance"
    assert "shipping_cost" not in spec["sql"], \
        "the engine must not be computing the rejected definition"
    assert "gross_revenue" in spec["sql"] and "returns_amount" in spec["sql"]
    assert C.measure_column("net_revenue") == "net_revenue"


def test_flipping_the_authority_rule_changes_the_number_downstream():
    """The whole point of the propagation path, asserted end to end."""
    cfg = raw_config()
    cfg["kpi_definitions"]["net_revenue"]["resolution"]["precedence"] = \
        ["operations", "finance"]
    c2 = Contract(kpi_config=cfg)
    e2 = Estate(c2)
    assert c2.reconciliation("net_revenue")["selected"] == "operations"
    assert c2.measure_column("net_revenue") == "net_revenue_ops"
    assert "shipping_cost" in c2.get_kpi("net_revenue")["sql"]

    base = go("S1")
    flipped = run("S1", "data_analyst", c2, e2, force_offline=True)
    assert flipped["split"]["revenue_column"] == "net_revenue_ops"
    assert base["split"]["revenue_column"] == "net_revenue"
    assert flipped["movement"]["actual"] < base["movement"]["actual"], \
        "the operations definition expenses freight, so it must report less revenue"
    assert flipped["semantics"]["kpi_definitions"]["net_revenue"]["selected"] == "operations"
    # the identity algebra decomposes whichever measure was selected
    assert abs(flipped["split"]["identity"]["residual"]) < 1.0


def test_the_run_output_makes_the_reconciliation_visible():
    d = go("S1")["semantics"]["kpi_definitions"]["net_revenue"]
    for k in ("status", "selected", "selected_system", "selected_formula",
              "selected_owner", "resolution_rule", "reason", "differences",
              "rejected", "numeric"):
        assert k in d, "reconciliation must be auditable from the run output alone"
    assert d["rationale"], "a resolution rule should carry its business rationale"


def test_the_method_ledger_declares_the_semantic_step():
    ms = [m for m in go("S1")["telemetry"]["methods"] if m["stage"] == "semantic"]
    assert len(ms) >= 2
    whats = {m["what"] for m in ms}
    assert "KPI definition reconciliation" in whats
    assert any("identical rows" in w for w in whats)


def test_reconciliation_happens_once_and_not_inside_each_stage():
    """Definition logic must live in the semantic layer only. If SIFT, SPLIT or
    evidence.py start reasoning about definitions there will be two answers."""
    import whylayer.sift, whylayer.split, whylayer.evidence, whylayer.solve
    for mod in (whylayer.sift, whylayer.split, whylayer.evidence, whylayer.solve):
        src = open(mod.__file__).read()
        assert "kpi_definitions" not in src and "authority_precedence" not in src, \
            "%s is re-deriving KPI definitions" % mod.__name__


def test_the_ml_layer_never_mixes_incompatible_definitions():
    """A ranker trained across two definitions of the same KPI would be learning
    from a measurement that changes meaning halfway through."""
    r = go("S1")
    assert r["semantics"]["kpi_definitions"]["net_revenue"]["status"] in (
        KR.STATUS_RECONCILED, KR.STATUS_EQUIVALENT, KR.STATUS_SINGLE)
    assert r["ml_ranker"]["status"] == "active"
    from whylayer.ml import features as FT
    assert not (set(FT.FEATURES) & {"net_revenue_ops", "definition_source"})
