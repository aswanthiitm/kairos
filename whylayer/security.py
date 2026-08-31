"""
Entitlement enforcement.

Design rule: entitlements are applied to the DATA and the EVIDENCE SET before
anything reaches a prompt. We never generate a narrative and then try to redact
it - a model that has seen a number will leak it, and post-filtering generated
text is not a control an auditor will accept.

Three levels, per the Round-2 brief:
  row     -> which slices of the estate a persona may see at all
  column  -> which measures are visible (a supply-chain lead sees no rupees)
  domain  -> whole evidence classes (CFO gets themes, never call verbatims)
plus PII handling on free text.
"""
import re
from typing import Any, Dict, List, Optional, Tuple

from .contract import Contract


class Persona(object):
    def __init__(self, key: str, contract: Contract):
        ents = contract.entitlements
        if key not in ents["personas"]:
            raise KeyError("unknown persona %r" % key)
        p = ents["personas"][key]
        self.key = key
        self.label = p["label"]
        self.display = p["display"]
        self.row_filter: Dict[str, List[str]] = p.get("row_filter") or {}
        self.deny_columns: List[str] = p.get("deny_columns") or []
        self.deny_domains: List[str] = p.get("deny_domains") or []
        self.pii_policy: str = p.get("pii_policy", "redact")
        self.narrative: Dict[str, Any] = p.get("narrative", {})
        self._patterns = {k: re.compile(v) for k, v in ents["pii_patterns"].items()}
        self._sensitivity = ents["column_sensitivity"]

    # ------------------------------------------------------------------ rows
    def sql_where(self, extra: Optional[Dict[str, Any]] = None) -> Tuple[str, List[str]]:
        """Row-level security compiled into the WHERE clause. Returns the SQL
        and a human-readable list of the restrictions applied, so the UI can
        show the user what they are NOT seeing."""
        clauses, notes = [], []
        for col, vals in self.row_filter.items():
            lit = ", ".join("'%s'" % v.replace("'", "''") for v in vals)
            clauses.append("%s IN (%s)" % (col, lit))
            notes.append("rows restricted to %s in (%s)" % (col, ", ".join(vals)))
        for col, val in (extra or {}).items():
            if isinstance(val, (list, tuple)):
                lit = ", ".join("'%s'" % str(v).replace("'", "''") for v in val)
                clauses.append("%s IN (%s)" % (col, lit))
            else:
                clauses.append("%s = '%s'" % (col, str(val).replace("'", "''")))
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, notes

    def may_see_row(self, row: Dict[str, Any]) -> bool:
        for col, vals in self.row_filter.items():
            if row.get(col) not in vals:
                return False
        return True

    # --------------------------------------------------------------- columns
    def may_see_column(self, col: str) -> bool:
        return col not in self.deny_columns

    def scrub_measures(self, d: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
        out, withheld = {}, []
        for k, v in d.items():
            if self.may_see_column(k):
                out[k] = v
            else:
                withheld.append(k)
        return out, withheld

    # ---------------------------------------------------------------- domain
    def may_see_domain(self, domain: str) -> bool:
        return domain not in self.deny_domains

    # ------------------------------------------------------------------- pii
    def scrub_text(self, text: str) -> str:
        if self.pii_policy == "none":
            return text
        out = text
        for name, pat in self._patterns.items():
            out = pat.sub("[%s redacted]" % name.replace("contact_", ""), out)
        return out

    # ------------------------------------------------------------- evidence
    def filter_evidence(self, items: List[Dict[str, Any]]
                        ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Returns (visible, withheld). Withheld items are surfaced to the user
        as a count and a reason - never silently dropped, because a persona
        being told 'you are missing 4 items' is itself decision-relevant."""
        visible, withheld = [], []
        for it in items:
            domain = it.get("domain", "crm_verbatim")
            if not self.may_see_domain(domain):
                withheld.append({"id": it.get("id"), "reason":
                                 "domain '%s' not permitted for %s" % (domain, self.display)})
                continue
            if not self.may_see_row(it):
                withheld.append({"id": it.get("id"), "reason":
                                 "outside row entitlement (%s)" % self.row_filter})
                continue
            c = dict(it)
            if "text" in c:
                c["text"] = self.scrub_text(c["text"])
            visible.append(c)
        return visible, withheld


def load_personas(contract: Contract) -> Dict[str, Persona]:
    return {k: Persona(k, contract) for k in contract.entitlements["personas"]}
