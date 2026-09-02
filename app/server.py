"""FastAPI service + static UI for the KAIRÓS prototype."""
import os, sys, json
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from kairos.contract import Contract
from kairos.sources import Estate
from kairos.security import load_personas
from kairos.pipeline import run, SCENARIOS
from kairos import feedback as FB
from kairos.triage import sweep as do_sweep, backtest as do_backtest
from kairos.telemetry import Telemetry
from kairos.ml import ranker as MLR
from kairos.fiscal import FiscalCalendarError
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
app = FastAPI(title="KAIRÓS", version="1.0.0")

CONTRACT = Contract()
ESTATE = Estate(CONTRACT)
PERSONAS = load_personas(CONTRACT)


class Feedback(BaseModel):
    run_id: str
    persona: str
    kpi: str
    hypothesis_id: str
    grade: str
    correction: Optional[str] = None


class Outcome(BaseModel):
    playbook_id: str
    realised_recovery_pct: float


@app.get("/api/meta")
def meta() -> Dict[str, Any]:
    return {
        "scenarios": [{"id": k, "name": v["name"], "note": v["note"], "kpi": v["kpi"],
                       "window": v["window"], "filters": v["filters"]}
                      for k, v in SCENARIOS.items()],
        "personas": [{"key": k, "label": p.label, "display": p.display,
                      "row_filter": p.row_filter, "deny_columns": p.deny_columns,
                      "deny_domains": p.deny_domains, "pii_policy": p.pii_policy,
                      "channel": p.narrative.get("channel")}
                     for k, p in PERSONAS.items()],
        "contract": {
            "version": CONTRACT.kpi["version"],
            "kpis": [{"name": k, "label": v["label"], "unit": v["unit"],
                      "grain": v["grain"], "source": v["source"],
                      "drivers": v.get("drivers", []),
                      "materiality": v.get("materiality", {}),
                      "lineage": CONTRACT.lineage(k)}
                     for k, v in CONTRACT.kpis.items()],
            "sources": CONTRACT.kpi["sources"],
            "levers": CONTRACT.levers(),
        },
        "graph": {"nodes": CONTRACT.graph["nodes"], "edges": CONTRACT.edges(),
                  "blocked": CONTRACT.blocked()},
        "llm_configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }


@app.get("/api/freshness")
def freshness() -> Any:
    from kairos.telemetry import Telemetry
    return ESTATE.freshness_report(Telemetry())


@app.get("/api/analyse")
def analyse(scenario: str = "S1", persona: str = "cfo", offline: bool = False,
            narrator: str = "auto", fiscal_period: Optional[str] = None) -> Any:
    if scenario not in SCENARIOS:
        raise HTTPException(404, "unknown scenario")
    if persona not in PERSONAS:
        raise HTTPException(404, "unknown persona")
    try:
        return JSONResponse(json.loads(json.dumps(
            run(scenario, persona, CONTRACT, ESTATE, force_offline=offline,
                narrator_mode=narrator, fiscal_period=fiscal_period),
            default=str)))
    except FiscalCalendarError as ex:
        raise HTTPException(400, str(ex))
    except Exception as ex:  # surfaced rather than swallowed
        raise HTTPException(500, "%s: %s" % (type(ex).__name__, ex))


@app.post("/api/feedback")
def post_feedback(f: Feedback) -> Any:
    return FB.record(f.run_id, f.persona, f.kpi, f.hypothesis_id, f.grade, f.correction)


@app.post("/api/outcome")
def post_outcome(o: Outcome) -> Any:
    return FB.record_outcome(o.playbook_id, o.realised_recovery_pct,
                             CONTRACT.playbooks["playbooks"])


@app.get("/api/sweep")
def sweep(persona: str = "data_analyst", start: str = "2026-08-17",
          end: str = "2026-08-30") -> Any:
    if persona not in PERSONAS:
        raise HTTPException(404, "unknown persona")
    tel = Telemetry()
    r = do_sweep(CONTRACT, ESTATE, PERSONAS[persona], tel,
                 (date.fromisoformat(start), date.fromisoformat(end)))
    r["telemetry"] = tel.summary()
    return JSONResponse(json.loads(json.dumps(r, default=str)))


@app.get("/api/backtest")
def backtest(persona: str = "data_analyst", kpi: str = "net_revenue",
             region: Optional[str] = "North") -> Any:
    if persona not in PERSONAS:
        raise HTTPException(404, "unknown persona")
    tel = Telemetry()
    events = [(date(2026, 8, 3), date(2026, 8, 30), "WH-3 dispatch SLA collapse"),
              (date(2026, 8, 10), date(2026, 8, 24), "West competitor promo + price rise"),
              (date(2026, 8, 12), date(2026, 8, 26), "South modern-trade competitor promo")]
    r = do_backtest(CONTRACT, ESTATE, PERSONAS[persona], tel, kpi,
                    {"region": region} if region else None, known_events=events)
    r["telemetry"] = tel.summary()
    return JSONResponse(json.loads(json.dumps(r, default=str)))


@app.post("/api/reset")
def reset() -> Any:
    return FB.reset()


@app.get("/api/learning")
def learning() -> Any:
    return FB.stats()


@app.get("/api/semantics")
def semantics() -> Any:
    """The semantic layer as the contract resolves it: the fiscal calendar and the
    periods it produces, every declared dimension hierarchy with its members and
    per-KPI availability, and the reconciliation of every competing KPI definition
    with the rule that selected the winner. Served because a semantic decision that
    cannot be inspected at runtime is not governance, it is a comment."""
    from kairos.hierarchy import available_levels
    from kairos.kpi_reconciliation import summarise
    f = CONTRACT.fiscal
    probe = date(2026, 8, 30)
    return {
        "fiscal_calendar": {
            "key": f.key, "label": f.label, "start_month": f.start_month,
            "year_label": f.year_label,
            "example_periods": {t: [str(x) for x in f.period_bounds(t)]
                                for t in ("FY2026", "FY2027", "FY2027-Q1",
                                          "FY2027-Q2", "FY2027-M05")},
            "today": f.describe(probe),
        },
        "hierarchies": {d: h.to_dict() for d, h in CONTRACT.hierarchies().items()},
        "level_availability": {k: available_levels(CONTRACT, k, "region")
                               for k in sorted(CONTRACT.kpis)},
        "kpi_definitions": {k: summarise(r)
                            for k, r in CONTRACT.reconciliations.items()},
        "unresolved": CONTRACT.unresolved_definitions(),
    }


@app.get("/api/ml")
def ml_model_card() -> Any:
    """The driver ranker's model card: what it was trained on, how it scored on a
    time-based holdout, what it is allowed to do, and where it falls over.
    Served as an endpoint because a learned component that cannot be inspected at
    runtime is not auditable, whatever the documentation says."""
    return MLR.get().summary()


@app.get("/")
def index() -> Any:
    return FileResponse(os.path.join(HERE, "static", "index.html"))


app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
