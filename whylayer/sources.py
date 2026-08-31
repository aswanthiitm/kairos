"""
Heterogeneous source access.

Four sources, three grains, four refresh cadences. Every read goes through the
contract (for definitions and lineage) and the persona (for entitlements).
No caller may query raw tables directly.
"""
import json, os
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional

import duckdb
import pandas as pd

from .contract import Contract
from .security import Persona
from .telemetry import Telemetry, MethodType

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "warehouse.duckdb")
GEN = os.path.join(ROOT, "data", "generated")


class Estate(object):
    def __init__(self, contract: Contract, now: Optional[datetime] = None):
        self.c = contract
        self.now = now or datetime(2026, 8, 31, 1, 0, 0)
        if not os.path.exists(DB):
            raise RuntimeError("warehouse not built - run: python data/generate.py")
        self.con = duckdb.connect(DB, read_only=True)
        self._interactions: Optional[List[Dict[str, Any]]] = None

    # ------------------------------------------------------------------ sql
    def sql(self, q: str, tel: Optional[Telemetry] = None, stage: str = "query",
            what: str = "governed query") -> pd.DataFrame:
        df = self.con.execute(q).df()
        if tel:
            tel.method(stage, MethodType.SQL, what,
                       "KPI definitions are governed; the engine may only run SQL "
                       "generated from the semantic contract, never model-authored SQL",
                       detail=" ".join(q.split())[:400])
        return df

    # ------------------------------------------------------------- freshness
    def freshness_report(self, tel: Optional[Telemetry] = None) -> List[Dict[str, Any]]:
        out = []
        probes = {
            "orders":   "SELECT MAX(order_date) AS t FROM orders",
            "dispatch": "SELECT MAX(dispatch_date) AS t FROM dispatch",
            "market":   "SELECT MAX(start_date) AS t FROM market_events WHERE start_date <= DATE '2026-08-31'",
        }
        for src, q in probes.items():
            t = self.con.execute(q).fetchone()[0]
            ts = datetime.combine(t, datetime.min.time()) if isinstance(t, date) else t
            # a daily feed is "current" if it holds yesterday's data
            out.append(self.c.freshness(src, ts + timedelta(hours=23), self.now))
        ints = self.interactions()
        latest = max(datetime.fromisoformat(i["ts"]) for i in ints) if ints else self.now
        out.append(self.c.freshness("crm", latest + timedelta(hours=23), self.now))
        if tel:
            tel.method("sift", MethodType.RULES, "source freshness vs contract SLA",
                       "a stale feed must be ruled out before any business explanation; "
                       "instrumentation is hypothesis #1, not an afterthought")
        return out

    # ---------------------------------------------------------- unstructured
    def interactions(self) -> List[Dict[str, Any]]:
        if self._interactions is None:
            p = os.path.join(GEN, "interactions.jsonl")
            with open(p) as f:
                self._interactions = [json.loads(l) for l in f if l.strip()]
        return self._interactions

    # ---------------------------------------------------------------- series
    def kpi_series(self, kpi: str, persona: Persona,
                   filters: Optional[Dict[str, Any]] = None,
                   tel: Optional[Telemetry] = None) -> pd.DataFrame:
        """Daily series for a contract KPI, with row-level security applied in SQL."""
        spec = self.c.get_kpi(kpi)
        where, _ = persona.sql_where(filters)
        if spec["sql"] == "__derived__":
            return self._reorder_rate(persona, filters, tel)
        table = {"orders": "orders", "dispatch": "dispatch"}[spec["source"]]
        q = spec["sql"].format(where=where).replace("FROM orders", "FROM orders") \
            .replace("FROM dispatch", "FROM dispatch")
        df = self.sql(q + " ORDER BY 1", tel, "sift", "%s daily series" % kpi)
        df.columns = ["d", "v"]
        df["d"] = pd.to_datetime(df["d"])
        return df

    def _reorder_rate(self, persona: Persona, filters, tel) -> pd.DataFrame:
        where, _ = persona.sql_where(filters)
        q = """
        WITH o AS (SELECT account_id, order_date FROM orders %s GROUP BY 1,2),
        w AS (SELECT DISTINCT date_trunc('week', order_date) AS wk FROM o)
        SELECT w.wk AS d,
               AVG(CASE WHEN x.recent > 0 THEN 1.0 ELSE 0.0 END) AS v
        FROM w
        JOIN (SELECT a.account_id, w2.wk,
                     SUM(CASE WHEN o2.order_date BETWEEN w2.wk - INTERVAL 28 DAY AND w2.wk
                              THEN 1 ELSE 0 END) AS recent
              FROM (SELECT DISTINCT account_id FROM o) a
              CROSS JOIN w w2
              LEFT JOIN o o2 ON o2.account_id = a.account_id
              GROUP BY 1,2) x ON x.wk = w.wk
        GROUP BY 1 ORDER BY 1""" % where
        df = self.sql(q, tel, "sift", "28-day reorder rate (weekly grain)")
        df.columns = ["d", "v"]
        df["d"] = pd.to_datetime(df["d"])
        return df

    # ------------------------------------------------------------ slice data
    def orders_slice(self, persona: Persona, start: date, end: date,
                     filters: Optional[Dict[str, Any]] = None,
                     tel: Optional[Telemetry] = None) -> pd.DataFrame:
        where, _ = persona.sql_where(filters)
        clause = where or "WHERE 1=1"
        q = ("SELECT * FROM orders %s AND order_date BETWEEN DATE '%s' AND DATE '%s'"
             % (clause, start, end))
        return self.sql(q, tel, "split", "order lines for contribution analysis")
