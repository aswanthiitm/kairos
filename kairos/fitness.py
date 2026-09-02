"""
DATA FITNESS GATE - is this estate fit to be reasoned over at all?

Motivated by the strongest empirical finding in Olszak & Bartus, "AI-enhanced
Business Intelligence for decision-making", Procedia Computer Science 270 (2025)
415-425 (KES 2025). In their in-depth interviews across 20 organisations in three
sectors, the most frequently reported barrier to AI-enhanced BI was DATA
AVAILABILITY AND QUALITY, cited by 20 of 20 respondents - unanimous - with
"integrating diverse data sources and ensuring consistency and accuracy" next at
18 of 20. Every other barrier they found (cost 15, specialist shortage 12, system
integration 11, security and regulation 10) was less common than data quality.

That ranking is an instruction. If the single universal obstacle is data quality,
then a quality verdict cannot be a footnote inside one detector - it has to be a
gate that runs first, across every source the analysis depends on, and that is
allowed to stop the run.

Five dimensions, matching the language the paper's respondents used:

  availability   are there rows at all for this window and slice?
  timeliness     is the feed inside the refresh SLA the contract declares?
  completeness   is the row profile consistent with its own history, or has part
                 of the load gone missing?
  consistency    do the sources agree with each other - referential integrity and
                 chronological sanity ACROSS systems, which is the integration
                 barrier the paper ranks second
  validity       are the values themselves possible?

The output is a fitness verdict - FIT, FIT_WITH_CAVEATS or UNFIT - plus the
specific failures. UNFIT stops the analysis: a confident narrative built on a
broken feed is worse than no narrative.
"""
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from .contract import Contract
from .sources import Estate
from .telemetry import Telemetry, MethodType

SEVERITY_WEIGHT = {"critical": 1.0, "major": 0.45, "minor": 0.12}


def _issue(dim: str, source: str, severity: str, detail: str,
           implication: str, count: Optional[int] = None) -> Dict[str, Any]:
    return {"dimension": dim, "source": source, "severity": severity,
            "detail": detail, "implication": implication, "count": count}


def assess(contract: Contract, estate: Estate, tel: Telemetry,
           window: Tuple[date, date],
           sources: Optional[List[str]] = None) -> Dict[str, Any]:
    ws, we = window
    issues: List[Dict[str, Any]] = []
    checks_run = 0
    sources = sources or ["orders", "dispatch", "crm", "market"]

    # ---------------------------------------------------------- timeliness
    for f in estate.freshness_report(tel):
        checks_run += 1
        if f["source"] not in sources:
            continue
        if f["breached"]:
            over = f["lag_minutes"] / max(1.0, f["sla_minutes"])
            issues.append(_issue(
                "timeliness", f["source"],
                "critical" if over > 2.0 else "major",
                "%.0f min behind a %d min SLA (%.2fx over), refreshes every %d min"
                % (f["lag_minutes"], f["sla_minutes"], over, f["refresh_cadence_minutes"]),
                "the window may be measuring a partial load rather than the business"))

    # -------------------------------------------------------- availability
    avail = estate.sql(
        "SELECT COUNT(*) AS n FROM orders WHERE order_date BETWEEN DATE '%s' AND DATE '%s'"
        % (ws, we), tel, "fitness", "order rows in window")
    checks_run += 1
    if int(avail["n"].iloc[0]) == 0:
        issues.append(_issue("availability", "orders", "critical",
                             "no order rows in the analysis window",
                             "nothing can be computed for this period"))

    # -------------------------------------------------------- completeness
    # Generalised from the dispatch-specific probe: compare the recent daily row
    # profile of each structured feed against its own trailing baseline.
    for src, table, datecol in (("orders", "orders", "order_date"),
                                ("dispatch", "dispatch", "dispatch_date")):
        if src not in sources:
            continue
        checks_run += 1
        prof = estate.sql(
            "SELECT %s AS d, COUNT(*) AS n FROM %s WHERE %s >= DATE '%s' GROUP BY 1 ORDER BY 1"
            % (datecol, table, datecol, ws - timedelta(days=45)),
            tel, "fitness", "%s daily row profile" % src)
        if len(prof) < 10:
            continue
        prof["d"] = pd.to_datetime(prof["d"])
        recent = prof[prof["d"] >= pd.Timestamp(we) - pd.Timedelta(days=6)]["n"]
        base = prof[prof["d"] < pd.Timestamp(we) - pd.Timedelta(days=6)]["n"].tail(28)
        if len(recent) and len(base) and base.median() > 0:
            ratio = recent.median() / base.median()
            if ratio < 0.6:
                issues.append(_issue(
                    "completeness", src, "critical",
                    "recent daily row count is %.0f%% of its trailing median "
                    "(%.0f/day against %.0f/day)"
                    % (100 * ratio, recent.median(), base.median()),
                    "part of the load is missing; any movement is partly an artefact"))
            elif ratio < 0.85:
                issues.append(_issue(
                    "completeness", src, "major",
                    "recent daily row count is %.0f%% of its trailing median"
                    % (100 * ratio),
                    "the feed may be arriving late or incomplete"))

    # A load failure almost never halves a whole feed - it drops one partition or
    # one class of row, which an aggregate count hides completely. So we also profile
    # per partition, and per outcome class where one exists.
    if "dispatch" in sources:
        checks_run += 1
        part = estate.sql(
            "SELECT warehouse_id, "
            "SUM(CASE WHEN dispatch_date >= DATE '%s' THEN 1 ELSE 0 END) AS recent_n, "
            "SUM(CASE WHEN dispatch_date >= DATE '%s' AND NOT on_time THEN 1 ELSE 0 END) AS recent_late, "
            "SUM(CASE WHEN dispatch_date < DATE '%s' AND dispatch_date >= DATE '%s' THEN 1 ELSE 0 END) AS base_n, "
            "SUM(CASE WHEN dispatch_date < DATE '%s' AND dispatch_date >= DATE '%s' AND NOT on_time THEN 1 ELSE 0 END) AS base_late "
            "FROM dispatch GROUP BY 1"
            % (we - timedelta(days=6), we - timedelta(days=6),
               we - timedelta(days=6), we - timedelta(days=34),
               we - timedelta(days=6), we - timedelta(days=34)),
            tel, "fitness", "dispatch completeness by partition and outcome class")
        for _, r in part.iterrows():
            rn, bn = float(r["recent_n"] or 0), float(r["base_n"] or 0)
            rl, bl = float(r["recent_late"] or 0), float(r["base_late"] or 0)
            if bn <= 0:
                continue
            # normalise the baseline to the same 7-day span as the recent slice
            exp = bn * (7.0 / 28.0)
            if exp > 5 and rn < 0.55 * exp:
                issues.append(_issue(
                    "completeness", "dispatch/%s" % r["warehouse_id"], "critical",
                    "partition %s has %.0f rows in the last 7 days against %.0f expected"
                    % (r["warehouse_id"], rn, exp),
                    "one partition of the load has failed while the feed as a whole "
                    "looks healthy"))
            base_rate = (bl / bn) if bn else 0.0
            recent_rate = (rl / rn) if rn else 0.0
            if base_rate > 0.02 and rn > 20 and recent_rate < 0.3 * base_rate:
                issues.append(_issue(
                    "completeness", "dispatch/%s" % r["warehouse_id"], "critical",
                    "failure rows have almost stopped arriving for %s: %.1f%% late now "
                    "against %.1f%% on the trailing baseline"
                    % (r["warehouse_id"], 100 * recent_rate, 100 * base_rate),
                    "a whole CLASS of row is missing rather than a partition, so this "
                    "KPI will appear to improve when nothing improved"))

    # ---------------------------------------------------------- consistency
    # Cross-source agreement - the paper's second-ranked barrier (18/20).
    if {"orders", "dispatch"} <= set(sources):
        checks_run += 1
        orphan = estate.sql(
            "SELECT COUNT(*) AS n FROM dispatch d LEFT JOIN orders o "
            "ON o.order_id = d.order_id WHERE o.order_id IS NULL",
            tel, "fitness", "dispatch rows with no parent order")
        n = int(orphan["n"].iloc[0])
        if n:
            issues.append(_issue("consistency", "dispatch<->orders", "major",
                                 "%d shipment rows reference an order that does not exist" % n,
                                 "joins across these systems will silently drop or duplicate",
                                 count=n))
        checks_run += 1
        back = estate.sql(
            "SELECT COUNT(*) AS n FROM dispatch d JOIN orders o ON o.order_id = d.order_id "
            "WHERE d.dispatch_date < o.order_date",
            tel, "fitness", "shipments dated before their order")
        n = int(back["n"].iloc[0])
        if n:
            issues.append(_issue("consistency", "dispatch<->orders", "major",
                                 "%d shipments are dated before the order they belong to" % n,
                                 "chronology is broken, so any lag analysis is unsafe",
                                 count=n))
    if "crm" in sources:
        checks_run += 1
        known = set(estate.sql("SELECT DISTINCT account_id FROM accounts", tel, "fitness",
                               "account master")["account_id"])
        unknown = {i["account_id"] for i in estate.interactions()
                   if i.get("account_id") not in known}
        if unknown:
            issues.append(_issue("consistency", "crm<->accounts", "major",
                                 "%d interaction accounts are absent from the account master"
                                 % len(unknown),
                                 "evidence cannot be entitlement-scoped for those accounts",
                                 count=len(unknown)))

    # ------------------------------------------------------------- validity
    checks_run += 1
    bad = estate.sql(
        "SELECT SUM(CASE WHEN units <= 0 THEN 1 ELSE 0 END) AS bad_units, "
        "SUM(CASE WHEN net_revenue < 0 THEN 1 ELSE 0 END) AS neg_rev, "
        "SUM(CASE WHEN discount_pct < 0 OR discount_pct > 1 THEN 1 ELSE 0 END) AS bad_disc "
        "FROM orders WHERE order_date BETWEEN DATE '%s' AND DATE '%s'" % (ws, we),
        tel, "fitness", "value-range validity on orders")
    for col, label in (("bad_units", "non-positive unit counts"),
                       ("neg_rev", "negative net revenue"),
                       ("bad_disc", "discounts outside 0-100%")):
        n = int(bad[col].iloc[0] or 0)
        if n:
            issues.append(_issue("validity", "orders", "major",
                                 "%d rows with %s" % (n, label),
                                 "aggregates over these rows are not trustworthy", count=n))
    checks_run += 1
    dup = estate.sql(
        "SELECT COUNT(*) AS n FROM (SELECT order_id FROM orders GROUP BY 1 HAVING COUNT(*) > "
        "COUNT(DISTINCT category || tier || CAST(units AS VARCHAR)))",
        tel, "fitness", "duplicate order-line detection")
    n = int(dup["n"].iloc[0] or 0)
    if n:
        issues.append(_issue("validity", "orders", "minor",
                             "%d order ids carry identical repeated lines" % n,
                             "possible double-loading; totals may be inflated", count=n))

    # ------------------------------------------------------------- verdict
    penalty = sum(SEVERITY_WEIGHT.get(i["severity"], 0.2) for i in issues)
    score = max(0.0, 1.0 - penalty / max(1.0, checks_run * 0.55))
    has_critical = any(i["severity"] == "critical" for i in issues)
    verdict = ("UNFIT" if has_critical and score < 0.55
               else "FIT_WITH_CAVEATS" if issues else "FIT")

    by_dim: Dict[str, int] = {}
    for i in issues:
        by_dim[i["dimension"]] = by_dim.get(i["dimension"], 0) + 1

    tel.method("fitness", MethodType.RULES, "data fitness gate across all sources",
               "data availability and quality was the single universal barrier in Olszak "
               "& Bartus (Procedia CS 270, 2025) - 20 of 20 organisations - and source "
               "integration was second at 18 of 20, so quality is assessed as a gate "
               "before analysis rather than as a footnote inside one detector",
               detail="checks=%d issues=%d score=%.2f verdict=%s"
                      % (checks_run, len(issues), score, verdict))
    return {
        "verdict": verdict, "score": round(score, 3), "checks_run": checks_run,
        "issues": issues, "issues_by_dimension": by_dim,
        "dimensions_assessed": ["availability", "timeliness", "completeness",
                                "consistency", "validity"],
        "gate": {
            "blocks_analysis": verdict == "UNFIT",
            "meaning": {
                "FIT": "every source cleared its checks; findings carry no data caveat",
                "FIT_WITH_CAVEATS": "usable, but the issues below must travel with any "
                                    "finding drawn from the affected sources",
                "UNFIT": "the estate cannot support a causal claim in this window; "
                         "repair the feed and re-run",
            }[verdict],
        },
        "citation": "Olszak & Bartus, Procedia Computer Science 270 (2025) 415-425",
    }
