"""
KPI DEFINITION RECONCILIATION.

Two systems, two defensible definitions of the same three words. Finance books
net revenue as `gross - discounts - returns`; the operations margin cube expenses
outbound freight against it and books `gross - discounts - returns - shipping`.
Both are correct inside their own system. Neither is wrong. They produce
different numbers, and every meeting that starts with "our numbers don't match"
starts here.

An engine that ships one definition has not solved this - it has picked a side
quietly, which is the failure mode, not the fix. So this module:

    1. discovers every declared definition of a semantic KPI
    2. decides whether they are EQUIVALENT or in CONFLICT, by comparing the
       fields that actually determine the number separately from the fields that
       merely describe it
    3. measures the numeric gap on the window under analysis
    4. applies a CONFIGURED resolution rule - never an implicit one
    5. keeps the losing definition, with the reason it lost
    6. hands one canonical definition downstream

Point 4 is the governance line. With no rule declared the result is UNRESOLVED
and the engine abstains. Choosing for the business, silently, on the grounds that
some number is better than none, is exactly the behaviour this system exists to
refuse - and it is worse here than elsewhere, because the wrong choice does not
look wrong. It looks like a number.

Reconciliation happens ONCE, at contract load, and its result is consumed
downstream. SIFT, SPLIT and evidence.py contain no definition logic at all; they
read the resolved spec like any other. That matters for the ML layer in
particular: a ranker trained across two incompatible definitions of the same KPI
would be learning from a measurement that changes meaning halfway through.
"""
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Fields that DETERMINE the number. A difference here is a real conflict.
COMPUTATIONAL_FIELDS = ("expression", "unit", "grain")
# Fields that DESCRIBE the definition. A difference here is context, and is
# reported but does not by itself make two definitions incompatible.
CONTEXTUAL_FIELDS = ("formula", "scope", "system", "owner", "authority",
                     "effective_from", "source", "column")

STATUS_SINGLE = "SINGLE_DEFINITION"
STATUS_EQUIVALENT = "EQUIVALENT"
STATUS_RECONCILED = "RECONCILED"
STATUS_UNRESOLVED = "UNRESOLVED"


def _norm(v: Any) -> str:
    return " ".join(str(v if v is not None else "").split()).lower()


def compare(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Field-by-field semantic comparison of two definitions."""
    diffs: List[Dict[str, Any]] = []
    for field in COMPUTATIONAL_FIELDS + CONTEXTUAL_FIELDS:
        if _norm(a.get(field)) != _norm(b.get(field)):
            diffs.append({
                "field": field,
                "kind": ("computational" if field in COMPUTATIONAL_FIELDS
                         else "contextual"),
                "a": a.get(field), "b": b.get(field)})
    computational = [d for d in diffs if d["kind"] == "computational"]
    return {"equivalent": not computational, "differences": diffs,
            "computational_conflict": bool(computational)}


def _apply_rule(keys: Sequence[str], resolution: Dict[str, Any]
                ) -> Tuple[Optional[str], str]:
    """Returns (selected_key, reason). A None selection means UNRESOLVED."""
    rule = (resolution or {}).get("rule")
    if not rule or rule == "none":
        return None, ("no resolution rule is declared for this KPI, so the engine "
                      "will not choose between the definitions on the business's "
                      "behalf")
    if rule == "authority_precedence":
        order = list((resolution or {}).get("precedence") or [])
        if not order:
            return None, ("resolution.rule is authority_precedence but no precedence "
                          "list is declared")
        for k in order:
            if k in keys:
                return k, ("configured authority precedence %s selects %r"
                           % (" > ".join(order), k))
        return None, ("none of the declared definitions (%s) appears in the "
                      "configured precedence list %s"
                      % (", ".join(sorted(keys)), " > ".join(order)))
    return None, "unknown resolution rule %r" % rule


def reconcile(semantic_kpi: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    """Reconcile every declared definition of one semantic KPI. Pure function of
    configuration: the SELECTION never depends on the data, so it is stable
    across windows and can be settled once, at contract load. Only the numeric
    GAP depends on the window, and that is measured separately."""
    defs: Dict[str, Any] = dict(entry.get("definitions") or {})
    resolution = entry.get("resolution") or {}
    keys = sorted(defs)
    out: Dict[str, Any] = {
        "semantic_kpi": semantic_kpi,
        "description": entry.get("description"),
        "n_definitions": len(defs),
        "definitions": defs,
        "resolution_rule": resolution.get("rule"),
        "resolution_config": resolution,
        "comparisons": [], "differences": [],
        "computational_conflict": False,
        "selected": None, "selected_definition": None, "rejected": [],
        "numeric": None,
    }
    if not defs:
        out["status"] = STATUS_SINGLE
        out["reason"] = "no competing definitions are declared"
        return out
    if len(defs) == 1:
        k = keys[0]
        out.update(status=STATUS_SINGLE, selected=k, selected_definition=defs[k],
                   reason="only one definition is declared, so there is nothing to "
                          "reconcile")
        return out

    # pairwise comparison against the first definition, which is enough to decide
    # equivalence and gives a readable difference list
    base = keys[0]
    for k in keys[1:]:
        c = compare(defs[base], defs[k])
        out["comparisons"].append({"a": base, "b": k, **c})
        out["computational_conflict"] |= c["computational_conflict"]
        for d in c["differences"]:
            out["differences"].append({
                "field": d["field"], "kind": d["kind"],
                "values": {base: d["a"], k: d["b"]}})

    if not out["computational_conflict"]:
        # Same number, different paperwork. Still pick a canonical owner so
        # lineage names one system, but this is not a conflict.
        sel, reason = _apply_rule(keys, resolution)
        sel = sel or base
        out.update(status=STATUS_EQUIVALENT, selected=sel,
                   selected_definition=defs[sel],
                   reason="all declared definitions compute the same measure at the "
                          "same grain and unit; %s is named as the system of record"
                          % sel,
                   rejected=[{"key": k, "definition": defs[k],
                              "reason": "equivalent definition retained for lineage"}
                             for k in keys if k != sel])
        return out

    sel, reason = _apply_rule(keys, resolution)
    if sel is None:
        out.update(status=STATUS_UNRESOLVED, selected=None, reason=reason,
                   rejected=[{"key": k, "definition": defs[k],
                              "reason": "unresolved - no definition was selected"}
                             for k in keys])
        return out
    out.update(status=STATUS_RECONCILED, selected=sel, selected_definition=defs[sel],
               reason=reason,
               rejected=[{"key": k, "definition": defs[k],
                          "reason": "%s; retained for audit, not used downstream"
                                    % reason} for k in keys if k != sel])
    if resolution.get("rationale"):
        out["rationale"] = " ".join(str(resolution["rationale"]).split())
    return out


def reconcile_registry(kpi_config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    reg = kpi_config.get("kpi_definitions") or {}
    return {name: reconcile(name, entry) for name, entry in reg.items()}


# ------------------------------------------------------------------- numeric
def measure_difference(rec: Dict[str, Any], estate, persona, window: Tuple[date, date],
                       filters: Optional[Dict[str, Any]] = None, tel=None,
                       source_spec: Optional[Dict[str, Any]] = None
                       ) -> Optional[Dict[str, Any]]:
    """Evaluate every definition over the SAME rows and report the gap.

    This is the number that ends the meeting: not "the systems disagree" but
    "they disagree by Rs 0.4 Cr, which is 2.0%, and here is which line of the
    formula accounts for it".
    """
    defs = rec.get("definitions") or {}
    if len(defs) < 2:
        return None
    src = source_spec or {}
    table = src.get("table")
    datecol = src.get("date_column")
    if not table or not datecol:
        return None
    ws, we = window
    where, _ = persona.sql_where(filters)
    clause = where or "WHERE 1=1"
    cols, keys = [], []
    for k in sorted(defs):
        expr = defs[k].get("expression")
        if not expr:
            continue
        cols.append("(%s) AS %s" % (expr, k))
        keys.append(k)
    if len(keys) < 2:
        return None
    q = ("SELECT %s FROM %s %s AND %s BETWEEN DATE '%s' AND DATE '%s'"
         % (", ".join(cols), table, clause, datecol, ws, we))
    df = estate.sql(q, tel, "semantic", "competing KPI definitions on the same rows")
    if not len(df):
        return None
    vals = {k: (float(df[k].iloc[0]) if df[k].iloc[0] is not None else None)
            for k in keys}
    sel = rec.get("selected")
    ref = vals.get(sel) if sel in vals else vals[keys[0]]
    others = {k: v for k, v in vals.items() if k != (sel or keys[0])}
    gaps = {}
    for k, v in others.items():
        if v is None or ref is None:
            continue
        gaps[k] = {
            "value": v, "absolute_difference": ref - v,
            "pct_difference": ((ref - v) / ref) if ref else None,
        }
    return {
        "window": {"start": str(ws), "end": str(we)},
        "filters": filters or {},
        "values": vals,
        "reference": sel or keys[0],
        "reference_value": ref,
        "gaps": gaps,
        "unit": (defs.get(sel) or defs[keys[0]]).get("unit"),
        "reading": ("the definitions were evaluated over identical rows, so the gap "
                    "is definitional and not a data problem"),
    }


# ---------------------------------------------------------------- application
def apply_to_kpi(spec: Dict[str, Any], rec: Dict[str, Any],
                 source_spec: Dict[str, Any]) -> Dict[str, Any]:
    """Rewrite a KPI spec so it computes the SELECTED definition.

    This is the whole propagation mechanism. Downstream stages read `sql`,
    `aggregation` and `measure_column` exactly as before and never learn that a
    reconciliation happened - which is the point. There is one definition layer,
    not one per stage.
    """
    d = rec.get("selected_definition")
    if not d or rec.get("status") == STATUS_UNRESOLVED:
        spec["definition_status"] = rec.get("status")
        spec["definition_unresolved"] = rec.get("status") == STATUS_UNRESOLVED
        return spec
    table, datecol = source_spec.get("table"), source_spec.get("date_column")
    if d.get("expression") and table and datecol:
        spec["sql"] = ("SELECT %s AS d, %s AS v FROM %s {where} GROUP BY 1"
                       % (datecol, d["expression"], table))
        agg = dict(spec.get("aggregation") or {})
        agg.update(kind=agg.get("kind", "additive"), expression=d["expression"])
        if d.get("column"):
            agg["column"] = d["column"]
        spec["aggregation"] = agg
    if d.get("unit"):
        spec["unit"] = d["unit"]
    spec["definition_status"] = rec.get("status")
    spec["definition_unresolved"] = False
    spec["definition_source"] = rec.get("selected")
    spec["definition_formula"] = d.get("formula")
    spec["definition_owner"] = d.get("owner")
    spec["definition_system"] = d.get("system")
    return spec


def summarise(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Compact, auditable record for the run output and the UI."""
    return {
        "semantic_kpi": rec["semantic_kpi"],
        "status": rec["status"],
        "n_definitions": rec["n_definitions"],
        "selected": rec.get("selected"),
        "selected_system": (rec.get("selected_definition") or {}).get("system"),
        "selected_formula": (rec.get("selected_definition") or {}).get("formula"),
        "selected_owner": (rec.get("selected_definition") or {}).get("owner"),
        "resolution_rule": rec.get("resolution_rule"),
        "reason": rec.get("reason"),
        "rationale": rec.get("rationale"),
        "computational_conflict": rec.get("computational_conflict"),
        "differences": rec.get("differences"),
        "rejected": [{"key": r["key"], "system": r["definition"].get("system"),
                      "formula": r["definition"].get("formula"),
                      "owner": r["definition"].get("owner"),
                      "reason": r["reason"]}
                     for r in rec.get("rejected") or []],
        "numeric": rec.get("numeric"),
    }
