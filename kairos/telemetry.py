"""
Runtime telemetry + the method ledger.

Two jobs:
  1. Record latency / model calls / tokens / cost per run (a Round-2 requirement).
  2. Force every computation to declare HOW it was produced. Any step that
     touches the answer registers a MethodType. The UI renders this as the
     "LLM vs non-LLM" breakdown, and it is the mechanism that keeps us honest
     about the LLM never being the source of quantitative truth.
"""
import time, json, uuid
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

import yaml
import os

_CFG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")


class MethodType(object):
    SQL           = "SQL"            # governed query against the semantic contract
    DETERMINISTIC = "DETERMINISTIC"  # closed-form arithmetic / identity algebra
    STATISTICS    = "STATISTICS"     # decomposition, control charts, intervals
    ML            = "ML"             # learned model
    CAUSAL        = "CAUSAL"         # DiD / synthetic control / graph admissibility
    RETRIEVAL     = "RETRIEVAL"      # lexical or vector search over text
    RULES         = "RULES"          # explicit business rules from the contract
    LLM           = "LLM"            # generative model


QUANTITATIVE = {MethodType.SQL, MethodType.DETERMINISTIC, MethodType.STATISTICS,
                MethodType.ML, MethodType.CAUSAL}


class Telemetry(object):
    def __init__(self, run_id: Optional[str] = None):
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.t0 = time.time()
        self.stages: List[Dict[str, Any]] = []
        self.methods: List[Dict[str, Any]] = []
        self.llm_calls: List[Dict[str, Any]] = []
        self._pricing = yaml.safe_load(open(os.path.join(_CFG, "llm_pricing.yaml")))

    # ---------------------------------------------------------------- stages
    @contextmanager
    def stage(self, name: str):
        t = time.time()
        rec = {"stage": name, "ms": None}
        self.stages.append(rec)
        try:
            yield rec
        finally:
            rec["ms"] = round((time.time() - t) * 1000, 1)

    # --------------------------------------------------------------- methods
    def method(self, stage: str, method: MethodType, what: str, why: str,
               detail: Optional[str] = None):
        """Declare how a piece of the answer was produced."""
        self.methods.append({"stage": stage, "method": method, "what": what,
                             "why": why, "detail": detail})

    # ------------------------------------------------------------- llm calls
    def llm(self, purpose: str, model: str, in_tok: int, out_tok: int,
            ms: float, cached: bool = False, fallback: bool = False):
        p = self._pricing["models"].get(model, {"input": 0.0, "output": 0.0})
        usd = (in_tok / 1e6) * p["input"] + (out_tok / 1e6) * p["output"]
        self.llm_calls.append({
            "purpose": purpose, "model": model, "input_tokens": in_tok,
            "output_tokens": out_tok, "ms": round(ms, 1), "cached": cached,
            "fallback": fallback, "usd": round(usd, 6),
            "inr": round(usd * self._pricing["usd_to_inr"], 4)})

    # ---------------------------------------------------------------- report
    def summary(self) -> Dict[str, Any]:
        total_ms = round((time.time() - self.t0) * 1000, 1)
        in_tok = sum(c["input_tokens"] for c in self.llm_calls)
        out_tok = sum(c["output_tokens"] for c in self.llm_calls)
        usd = sum(c["usd"] for c in self.llm_calls)
        by_method: Dict[str, int] = {}
        for m in self.methods:
            by_method[m["method"]] = by_method.get(m["method"], 0) + 1
        n_quant = sum(v for k, v in by_method.items() if k in QUANTITATIVE)
        n_llm = by_method.get(MethodType.LLM, 0)
        return {
            "run_id": self.run_id,
            "total_ms": total_ms,
            "stages": self.stages,
            "llm": {
                "calls": len(self.llm_calls),
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "usd": round(usd, 6),
                "inr": round(usd * self._pricing["usd_to_inr"], 4),
                "detail": self.llm_calls,
            },
            "method_mix": {
                "counts": by_method,
                "quantitative_steps": n_quant,
                "llm_steps": n_llm,
                "pct_non_llm": round(100.0 * n_quant / max(1, n_quant + n_llm), 1),
            },
            "methods": self.methods,
        }

    def to_json(self, **kw) -> str:
        return json.dumps(self.summary(), indent=2, **kw)
