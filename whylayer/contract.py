"""
The semantic contract loader.

Every KPI the engine reasons about must be declared in config/kpi_contract.yaml.
Nothing else is computable. This is deliberately restrictive: it is what stops
an LLM from inventing a metric definition, and it is where lineage, thresholds,
materiality rules and access classes live.
"""
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "config")


def _load(name: str) -> Dict[str, Any]:
    with open(os.path.join(CFG, name)) as f:
        return yaml.safe_load(f)


class Contract(object):
    def __init__(self):
        self.kpi = _load("kpi_contract.yaml")
        self.graph = _load("causal_graph.yaml")
        self.entitlements = _load("entitlements.yaml")
        self.playbooks = _load("playbooks.yaml")

    # ------------------------------------------------------------------ kpis
    @property
    def kpis(self) -> Dict[str, Any]:
        return self.kpi["kpis"]

    def get_kpi(self, name: str) -> Dict[str, Any]:
        if name not in self.kpis:
            raise KeyError(
                "KPI '%s' is not in the semantic contract. Declared KPIs: %s"
                % (name, ", ".join(sorted(self.kpis))))
        return self.kpis[name]

    def source_of(self, kpi: str) -> Dict[str, Any]:
        return self.kpi["sources"][self.get_kpi(kpi)["source"]]

    def sliceable(self, kpi: str, dim: str) -> bool:
        """Can this KPI be cut by this dimension at its source grain?
        on-time delivery is measured on shipments, which carry a warehouse and a
        region but know nothing about customer segment - asking for that slice is a
        grain error, not a data error, and it deserves a real answer."""
        dims = self.get_kpi(kpi).get("sliceable_by")
        if not dims:
            return True
        return dim in dims or dim == "account_id"

    def levers(self) -> Dict[str, Any]:
        return self.kpi["levers"]

    def lineage(self, kpi: str) -> str:
        return self.source_of(kpi)["lineage"]

    # ----------------------------------------------------------- causal graph
    def edges(self) -> List[Dict[str, Any]]:
        return self.graph["edges"]

    def blocked(self) -> List[Dict[str, Any]]:
        return self.graph["blocked"]

    def is_blocked(self, frm: str, to: str) -> Optional[str]:
        for b in self.blocked():
            if b["from"] == frm and b["to"] == to:
                return b["reason"]
        return None

    def paths(self, frm: str, to: str, max_len: int = 6) -> List[List[str]]:
        """All admissible directed paths from cause to effect.
        An empty result means the hypothesis has no defensible mechanism and is
        rejected before scoring - this is the graph doing its only job."""
        adj: Dict[str, List[str]] = {}
        for e in self.edges():
            adj.setdefault(e["from"], []).append(e["to"])
        out: List[List[str]] = []

        def walk(node: str, path: List[str]):
            if len(path) > max_len:
                return
            if node == to:
                out.append(list(path))
                return
            for nxt in adj.get(node, []):
                if nxt in path:
                    continue
                if self.is_blocked(node, nxt):
                    continue
                walk(nxt, path + [nxt])

        if self.is_blocked(frm, to):
            return []
        walk(frm, [frm])
        return out

    def edge_lag(self, path: List[str]) -> int:
        lag, idx = 0, {(e["from"], e["to"]): e.get("lag_days", 0) for e in self.edges()}
        for a, b in zip(path, path[1:]):
            lag += idx.get((a, b), 0)
        return lag

    # ------------------------------------------------------------- freshness
    def freshness(self, source: str, latest_data_ts: datetime,
                  now: datetime) -> Dict[str, Any]:
        s = self.kpi["sources"][source]
        lag_min = (now - latest_data_ts).total_seconds() / 60.0
        sla = s["freshness_sla_minutes"]
        return {
            "source": source, "system": s["system"], "kind": s["kind"],
            "grain": s["grain"], "lineage": s["lineage"],
            "refresh_cadence_minutes": s["refresh_cadence_minutes"],
            "latest_data": latest_data_ts.isoformat(),
            "lag_minutes": round(lag_min, 1),
            "sla_minutes": sla,
            "breached": lag_min > sla,
            "status": "STALE" if lag_min > sla else "OK",
        }
