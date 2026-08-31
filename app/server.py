"""FastAPI service + static UI for the Why Layer prototype."""
import os, sys, json
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from whylayer.contract import Contract
from whylayer.sources import Estate
from whylayer.security import load_personas
from whylayer.pipeline import run, SCENARIOS
from whylayer import feedback as FB

HERE = os.path.dirname(os.path.abspath(__file__))
app = FastAPI(title="The Why Layer", version="1.0.0")

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
    from whylayer.telemetry import Telemetry
    return ESTATE.freshness_report(Telemetry())


@app.get("/api/analyse")
def analyse(scenario: str = "S1", persona: str = "cfo", offline: bool = False,
            narrator: str = "auto") -> Any:
    if scenario not in SCENARIOS:
        raise HTTPException(404, "unknown scenario")
    if persona not in PERSONAS:
        raise HTTPException(404, "unknown persona")
    try:
        return JSONResponse(json.loads(json.dumps(
            run(scenario, persona, CONTRACT, ESTATE, force_offline=offline,
                narrator_mode=narrator),
            default=str)))
    except Exception as ex:  # surfaced rather than swallowed
        raise HTTPException(500, "%s: %s" % (type(ex).__name__, ex))


@app.post("/api/feedback")
def post_feedback(f: Feedback) -> Any:
    return FB.record(f.run_id, f.persona, f.kpi, f.hypothesis_id, f.grade, f.correction)


@app.post("/api/outcome")
def post_outcome(o: Outcome) -> Any:
    return FB.record_outcome(o.playbook_id, o.realised_recovery_pct,
                             CONTRACT.playbooks["playbooks"])


@app.get("/api/learning")
def learning() -> Any:
    return FB.stats()


@app.get("/")
def index() -> Any:
    return FileResponse(os.path.join(HERE, "static", "index.html"))


app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
