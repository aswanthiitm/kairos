"""
Dimensional hierarchy traversal.

The contract has always declared

    region: {hierarchy: [region, city]}

and nothing read it. This module makes the declaration load-bearing: roll-up
(city -> region), drill-down (region -> city), level validation, and - the part
that is easy to get wrong - aggregation that respects each measure's semantics.

Two refusals matter more than the traversal itself.

GRAIN. A level is only available on a KPI whose source can actually resolve it.
On-time delivery is measured on shipments; the dispatch feed carries a warehouse
and a region and has no city key at all. Attributing a city to it by joining
through the ordering account would produce a number that looks fine and means
nothing, so `available_levels` refuses it and says why. Inferring is worse than
refusing, because a refusal is visible.

RATIOS. Rolling four cities up to a region by averaging their ASPs weights a city
with ten orders the same as one with ten thousand. Every KPI declares an
`aggregation` block, and a ratio is re-aggregated from its numerator and
denominator or not at all.
"""
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from .telemetry import MethodType


class HierarchyError(ValueError):
    """An invalid level, or a level asked of a dimension that has no hierarchy."""


class Hierarchy(object):
    """One declared hierarchy, e.g. region -> city, ordered coarsest first."""

    def __init__(self, dimension: str, levels: Sequence[str],
                 members: Optional[Dict[str, List[str]]] = None):
        self.dimension = dimension
        self.levels: List[str] = list(levels or [])
        if len(self.levels) < 2:
            raise HierarchyError("%r declares no traversable hierarchy" % dimension)
        self.members: Dict[str, List[str]] = {k: list(v) for k, v in
                                              (members or {}).items()}
        self._parent: Dict[str, str] = {}
        for parent, children in self.members.items():
            for c in children:
                if c in self._parent and self._parent[c] != parent:
                    raise HierarchyError(
                        "%r belongs to both %r and %r - a hierarchy member must have "
                        "exactly one parent" % (c, self._parent[c], parent))
                self._parent[c] = parent

    # ---------------------------------------------------------------- levels
    def validate_level(self, level: str) -> str:
        if level not in self.levels:
            raise HierarchyError(
                "%r is not a level of the %s hierarchy. Declared levels: %s"
                % (level, self.dimension, " -> ".join(self.levels)))
        return level

    def index_of(self, level: str) -> int:
        return self.levels.index(self.validate_level(level))

    def parent_level(self, level: str) -> Optional[str]:
        i = self.index_of(level)
        return self.levels[i - 1] if i > 0 else None

    def child_level(self, level: str) -> Optional[str]:
        i = self.index_of(level)
        return self.levels[i + 1] if i + 1 < len(self.levels) else None

    @property
    def leaf(self) -> str:
        return self.levels[-1]

    @property
    def root(self) -> str:
        return self.levels[0]

    # ------------------------------------------------------------- traversal
    def roll_up(self, value: str, level: Optional[str] = None) -> Dict[str, Any]:
        """One step up. Returns the parent level and value."""
        lvl = self.validate_level(level) if level else self.leaf
        up = self.parent_level(lvl)
        if up is None:
            return {"level": lvl, "value": value, "parent_level": None,
                    "parent_value": None,
                    "note": "%r is already the top of the %s hierarchy"
                            % (lvl, self.dimension)}
        parent = self._parent.get(value)
        if parent is None:
            raise HierarchyError(
                "%r is not a declared %s of the %s hierarchy" % (value, lvl, self.dimension))
        return {"level": lvl, "value": value, "parent_level": up, "parent_value": parent}

    def drill_down(self, value: str, level: Optional[str] = None) -> Dict[str, Any]:
        """One step down. Returns the child level and its members."""
        lvl = self.validate_level(level) if level else self.root
        down = self.child_level(lvl)
        if down is None:
            return {"level": lvl, "value": value, "child_level": None, "children": [],
                    "note": "%r is the finest level of the %s hierarchy"
                            % (lvl, self.dimension)}
        if value not in self.members:
            raise HierarchyError(
                "%r is not a declared %s. Declared: %s"
                % (value, lvl, ", ".join(sorted(self.members))))
        return {"level": lvl, "value": value, "child_level": down,
                "children": list(self.members[value])}

    def parent_of(self, value: str) -> Optional[str]:
        return self._parent.get(value)

    def all_members(self, level: str) -> List[str]:
        self.validate_level(level)
        if level == self.root:
            return sorted(self.members)
        return sorted(self._parent)

    def to_dict(self) -> Dict[str, Any]:
        return {"dimension": self.dimension, "levels": self.levels,
                "members": self.members,
                "n_leaves": len(self._parent)}


# --------------------------------------------------------------- aggregation
def available_levels(contract, kpi: str, dimension: str = "region") -> Dict[str, Any]:
    """Which levels of a hierarchy this KPI can actually be cut by, and why not.

    The answer is a report rather than an exception because "this source cannot
    resolve city" is a fact about the estate that a user is entitled to see, not
    a programming error.
    """
    h = contract.hierarchy(dimension)
    spec = contract.get_kpi(kpi)
    ok, blocked = [], []
    for lvl in h.levels:
        if contract.sliceable(kpi, lvl):
            ok.append(lvl)
        else:
            blocked.append({
                "level": lvl,
                "reason": "%s is measured at %s grain from %s, which carries no %s key; "
                          "attributing one would be inferred, not measured"
                          % (spec["label"], contract.source_of(kpi)["grain"],
                             contract.source_of(kpi)["system"], lvl)})
    return {"kpi": kpi, "dimension": dimension, "levels": h.levels,
            "available": ok, "blocked": blocked,
            "grain": contract.source_of(kpi)["grain"]}


def aggregate_sql(contract, kpi: str, level: str, where: str,
                  start: date, end: date) -> Tuple[str, str]:
    """SQL that aggregates ``kpi`` by ``level`` over a window, honouring the
    measure's declared semantics. Returns (sql, aggregation kind)."""
    spec = contract.get_kpi(kpi)
    agg = spec.get("aggregation") or {}
    kind = agg.get("kind", "additive" if spec.get("additive") else "ratio")
    src = contract.source_of(kpi)
    table, datecol = src.get("table"), src.get("date_column")
    if not table or not datecol:
        raise HierarchyError("source %r declares no table/date_column"
                             % spec["source"])
    clause = where or "WHERE 1=1"
    bounds = "%s AND %s BETWEEN DATE '%s' AND DATE '%s'" % (clause, datecol, start, end)

    if kind == "additive":
        return ("SELECT %s AS k, %s AS v, %s AS num, 1.0 AS den FROM %s %s GROUP BY 1 ORDER BY 1"
                % (level, agg["expression"], agg["expression"], table, bounds)), kind
    if kind == "ratio":
        n, d = agg["numerator"], agg["denominator"]
        return ("SELECT %s AS k, (%s) AS num, (%s) AS den, "
                "(%s)/NULLIF((%s),0) AS v FROM %s %s GROUP BY 1 ORDER BY 1"
                % (level, n, d, n, d, table, bounds)), kind
    raise HierarchyError(
        "%s declares aggregation kind %r, which cannot be re-aggregated by a SQL "
        "roll-up (%s)" % (kpi, kind, agg.get("note", "")))


def roll_up_frame(df: pd.DataFrame, kind: str) -> float:
    """Combine child rows into their parent, correctly for the measure kind."""
    if not len(df):
        return float("nan")
    if kind == "additive":
        return float(df["v"].sum())
    den = float(df["den"].sum())
    return float(df["num"].sum() / den) if den else float("nan")


def kpi_by_level(contract, estate, persona, tel, kpi: str, level: str,
                 window: Tuple[date, date],
                 filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Aggregate one KPI at one hierarchy level over a window.

    Returns a refusal rather than raising when the level is valid but the KPI's
    source cannot resolve it; raises only when the level is not part of the
    declared hierarchy at all. Those are different mistakes: one is a fact about
    the estate, the other is a caller bug.
    """
    dim = _dimension_for_level(contract, level)
    contract.hierarchy(dim).validate_level(level)
    if not contract.sliceable(kpi, level):
        avail = available_levels(contract, kpi, dim)
        return {"kpi": kpi, "level": level, "supported": False,
                "rows": [], "total": None,
                "reason": next((b["reason"] for b in avail["blocked"]
                                if b["level"] == level), "level not available"),
                "available_levels": avail["available"]}
    where, _ = persona.sql_where(filters)
    sql, kind = aggregate_sql(contract, kpi, level, where, window[0], window[1])
    df = estate.sql(sql, tel, "split", "%s by %s" % (kpi, level))
    rows = [{"value": str(r["k"]), "v": (None if pd.isna(r["v"]) else float(r["v"])),
             "numerator": float(r["num"]), "denominator": float(r["den"])}
            for _, r in df.iterrows()]
    return {"kpi": kpi, "level": level, "supported": True, "aggregation": kind,
            "rows": rows, "total": roll_up_frame(df, kind) if len(df) else None,
            "unit": contract.get_kpi(kpi)["unit"]}


def _dimension_for_level(contract, level: str) -> str:
    for dim, h in contract.hierarchies().items():
        if level in h.levels:
            return dim
    raise HierarchyError(
        "%r is not a level of any declared hierarchy. Declared: %s"
        % (level, "; ".join("%s: %s" % (d, " -> ".join(h.levels))
                            for d, h in contract.hierarchies().items()) or "(none)"))


def drill_down_kpi(contract, estate, persona, tel, kpi: str, parent_value: str,
                   window: Tuple[date, date], baseline_days: int = 28,
                   dimension: str = "region",
                   filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Take a movement at one level and attribute it to the level below.

    This is the runtime answer to "why did South-region revenue decline?" - it
    resolves South's children from the contract, measures each against its own
    baseline, and checks that the children close back to the parent. The closure
    check is the part worth having: a roll-up that does not reproduce the number
    it came from is a broken hierarchy, and it should be visible rather than
    inferred from the fact that the code ran.
    """
    from datetime import timedelta
    h = contract.hierarchy(dimension)
    step = h.drill_down(parent_value, h.root)
    child_level = step["child_level"]
    if not child_level:
        return {"supported": False, "reason": step.get("note")}
    avail = available_levels(contract, kpi, dimension)
    if child_level not in avail["available"]:
        return {"kpi": kpi, "parent_level": h.root, "parent_value": parent_value,
                "child_level": child_level, "supported": False,
                "reason": next(b["reason"] for b in avail["blocked"]
                               if b["level"] == child_level),
                "available_levels": avail["available"],
                "grain": avail["grain"]}

    ws, we = window
    span = (we - ws).days + 1
    bs, be = ws - timedelta(days=baseline_days + 1), ws - timedelta(days=1)
    scope = dict(filters or {})
    scope[h.root] = parent_value

    cur = kpi_by_level(contract, estate, persona, tel, kpi, child_level, (ws, we), scope)
    base = kpi_by_level(contract, estate, persona, tel, kpi, child_level, (bs, be), scope)
    kind = cur.get("aggregation", "additive")
    scale = span / float((be - bs).days + 1)

    b_idx = {r["value"]: r for r in base["rows"]}
    children: List[Dict[str, Any]] = []
    for r in cur["rows"]:
        b = b_idx.get(r["value"])
        if kind == "additive":
            expected = (b["v"] or 0.0) * scale if b else 0.0
            move = (r["v"] or 0.0) - expected
        else:
            # A ratio child is compared like for like: its own rate now against
            # its own rate before. Scaling a rate by the window length would be
            # meaningless, so only additive measures get the scale factor.
            expected = (b["v"] if b else None)
            move = ((r["v"] - expected) if (r["v"] is not None and expected is not None)
                    else None)
        children.append({
            "value": r["value"], "current": r["v"], "baseline": expected,
            "move": move, "numerator": r["numerator"], "denominator": r["denominator"],
            "member_of": parent_value})

    total_move = sum(c["move"] for c in children
                     if c["move"] is not None) if kind == "additive" else None
    for c in children:
        c["share_of_move"] = ((c["move"] / total_move)
                              if (kind == "additive" and total_move) else None)
    children.sort(key=lambda c: (c["move"] if c["move"] is not None else 0.0))

    # closure: the children must reproduce the parent, computed independently
    parent = kpi_by_level(contract, estate, persona, tel, kpi, h.root, (ws, we),
                          dict(filters or {}, **{h.root: parent_value}))
    parent_val = next((r["v"] for r in parent["rows"] if r["value"] == parent_value),
                      None)
    rolled = roll_up_frame(pd.DataFrame(
        [{"v": c["current"] or 0.0, "num": c["numerator"], "den": c["denominator"]}
         for c in children]), kind) if children else None
    closes = (parent_val is not None and rolled is not None
              and abs(rolled - parent_val) <= max(1.0, abs(parent_val) * 1e-6))

    tel.method("split", MethodType.DETERMINISTIC, "hierarchy drill-down %s -> %s"
               % (h.root, child_level),
               "the contract declares region -> city, so a regional movement is "
               "attributed to its declared children and checked to close back to "
               "the parent; a %s measure is re-aggregated from its numerator and "
               "denominator rather than averaged" % kind,
               detail="%s -> %d %s, roll-up closes=%s"
                      % (parent_value, len(children), child_level, closes))
    return {
        "kpi": kpi, "dimension": dimension, "parent_level": h.root,
        "parent_value": parent_value, "child_level": child_level,
        "supported": True, "aggregation": kind, "unit": cur.get("unit"),
        "window": {"start": str(ws), "end": str(we)},
        "baseline": {"start": str(bs), "end": str(be),
                     "scale_applied": round(scale, 4) if kind == "additive" else None},
        "children": children, "total_move": total_move,
        "roll_up_check": {"parent_measured": parent_val, "children_rolled_up": rolled,
                          "closes": bool(closes),
                          "note": ("the %d %s values re-aggregate to the region figure, "
                                   "so the drill-down is exhaustive"
                                   % (len(children), child_level) if closes else
                                   "the children do NOT reproduce the parent - the "
                                   "hierarchy is incomplete for this slice")},
    }
