"""
The semantic contract loader.

Every KPI the engine reasons about must be declared in config/kpi_contract.yaml.
Nothing else is computable. This is deliberately restrictive: it is what stops
an LLM from inventing a metric definition, and it is where lineage, thresholds,
materiality rules and access classes live.
"""
import copy
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import yaml

from . import kpi_reconciliation as KR
from .fiscal import FiscalCalendar, from_contract as _fiscal_from_contract
from .hierarchy import Hierarchy, HierarchyError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "config")


def _load(name: str) -> Dict[str, Any]:
    with open(os.path.join(CFG, name)) as f:
        return yaml.safe_load(f)


class Contract(object):
    """The semantic layer.

    Three things are resolved HERE, once, and consumed everywhere else:

      * the FISCAL CALENDAR, so no stage does its own Gregorian arithmetic where
        a fiscal boundary is meant;
      * the DIMENSIONAL HIERARCHIES, so `hierarchy: [region, city]` is traversed
        rather than merely declared;
      * COMPETING KPI DEFINITIONS, reconciled to one canonical definition before
        any stage reads a KPI spec.

    Definition selection is a pure function of configuration, so it is settled at
    load time and every downstream stage sees a single, already-reconciled spec.
    The only part that depends on the data - how far apart the definitions
    actually are on a given window - is measured per run by the pipeline.
    """

    def __init__(self, kpi_config: Optional[Dict[str, Any]] = None):
        self.kpi = copy.deepcopy(kpi_config) if kpi_config else _load("kpi_contract.yaml")
        self.graph = _load("causal_graph.yaml")
        self.entitlements = _load("entitlements.yaml")
        self.playbooks = _load("playbooks.yaml")
        self.fiscal: FiscalCalendar = _fiscal_from_contract(self.kpi)
        self._hierarchies: Dict[str, Hierarchy] = {}
        for dim, spec in (self.kpi.get("dimensions") or {}).items():
            if len(spec.get("hierarchy") or []) >= 2:
                self._hierarchies[dim] = Hierarchy(dim, spec["hierarchy"],
                                                   spec.get("members"))
        self.reconciliations: Dict[str, Dict[str, Any]] = KR.reconcile_registry(self.kpi)
        for name, rec in self.reconciliations.items():
            if name in self.kpis:
                KR.apply_to_kpi(self.kpis[name], rec,
                                self.kpi["sources"][self.kpis[name]["source"]])

    # -------------------------------------------------------------- calendar
    def fiscal_period(self, d: date) -> Dict[str, Any]:
        return self.fiscal.describe(d)

    def fiscal_window(self, start: date, end: date) -> Dict[str, Any]:
        return self.fiscal.describe_window(start, end)

    def period_bounds(self, token: str) -> Tuple[date, date]:
        """Resolve a fiscal period token ('FY2027-Q2') to inclusive dates."""
        return self.fiscal.period_bounds(token)

    # ------------------------------------------------------------- hierarchy
    def hierarchies(self) -> Dict[str, Hierarchy]:
        return self._hierarchies

    def hierarchy(self, dimension: str = "region") -> Hierarchy:
        h = self._hierarchies.get(dimension)
        if h is None:
            raise HierarchyError(
                "%r declares no hierarchy in the contract. Dimensions with one: %s"
                % (dimension, ", ".join(sorted(self._hierarchies)) or "(none)"))
        return h

    def level_of(self, value: str, dimension: str = "region") -> Optional[str]:
        """Which level of the hierarchy a value belongs to, or None."""
        h = self.hierarchy(dimension)
        if value in h.members:
            return h.root
        return h.leaf if h.parent_of(value) else None

    # ------------------------------------------------------------ definition
    def reconciliation(self, kpi: str) -> Optional[Dict[str, Any]]:
        return self.reconciliations.get(kpi)

    def definition_status(self, kpi: str) -> str:
        rec = self.reconciliations.get(kpi)
        return rec["status"] if rec else KR.STATUS_SINGLE

    def unresolved_definitions(self) -> List[str]:
        return sorted(k for k, r in self.reconciliations.items()
                      if r["status"] == KR.STATUS_UNRESOLVED)

    def measure_column(self, kpi: str) -> str:
        """The physical column implementing the RESOLVED definition.

        Stages that need a single column rather than an aggregate expression -
        the price/volume/mix identity, the DiD cohort series - ask here instead
        of hard-coding `net_revenue`, so a change of authoritative definition
        actually reaches them.
        """
        spec = self.get_kpi(kpi)
        col = (spec.get("aggregation") or {}).get("column")
        if not col:
            raise KeyError("%s declares no materialised column; it must be computed "
                           "through its aggregation expression" % kpi)
        return col

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
