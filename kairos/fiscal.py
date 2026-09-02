"""
The fiscal calendar.

A KPI contract that declares ``fiscal_calendar: apr_mar`` and then does Gregorian
month arithmetic everywhere is worse than one that declares nothing: it reads as
a capability and behaves as a comment. This module is the single place fiscal
period boundaries are computed, and every caller that needs one resolves it here.

Configuration-driven, not India-specific. A calendar is fully described by two
numbers and a naming convention:

    start_month   the calendar month the fiscal year opens on (April = 4)
    year_label    'end'   -> FY is named for the calendar year it CLOSES in
                  'start' -> FY is named for the calendar year it OPENS in

For the shipped ``apr_mar`` calendar with ``year_label: end``:

    FY2026  =  2025-04-01 .. 2026-03-31
    Q1 Apr-Jun   Q2 Jul-Sep   Q3 Oct-Dec   Q4 Jan-Mar
    fiscal month 1 = April

Switching ``fiscal_calendar`` to ``jan_dec`` in the contract changes every
boundary the engine resolves, with no code change. There is a test for that,
because a configuration item nobody can flip is not configuration.

Gregorian arithmetic is deliberately left alone where fiscal semantics are not
relevant - a 28-day trailing baseline is 28 days, not "the previous fiscal
month", and pretending otherwise would make the statistics worse.
"""
import calendar as _cal
import re
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

PERIOD_RE = re.compile(
    r"^FY(?P<fy>\d{4})(?:[-_ ]?(?:(?:Q(?P<q>[1-4]))|(?:M(?P<m>0?[1-9]|1[0-2]))))?$",
    re.IGNORECASE)

DEFAULTS: Dict[str, Any] = {"start_month": 1, "year_label": "start",
                            "label": "calendar year"}


class FiscalCalendarError(ValueError):
    """Raised for a malformed period token or an undeclared calendar."""


class FiscalCalendar(object):
    def __init__(self, key: str, spec: Optional[Dict[str, Any]] = None):
        s = dict(DEFAULTS, **(spec or {}))
        self.key = key
        self.label = s.get("label", key)
        self.start_month = int(s["start_month"])
        if not 1 <= self.start_month <= 12:
            raise FiscalCalendarError("start_month must be 1-12, got %r" % s["start_month"])
        self.year_label = str(s.get("year_label", "start")).lower()
        if self.year_label not in ("start", "end"):
            raise FiscalCalendarError("year_label must be 'start' or 'end'")
        self.quarter_names = list(s.get("quarter_names") or ["Q1", "Q2", "Q3", "Q4"])

    # --------------------------------------------------------------- mapping
    def fiscal_month(self, d: date) -> int:
        """1-based month index within the fiscal year. April = 1 for apr_mar."""
        return ((d.month - self.start_month) % 12) + 1

    def fiscal_quarter(self, d: date) -> int:
        return (self.fiscal_month(d) - 1) // 3 + 1

    def fiscal_year(self, d: date) -> int:
        """The label of the fiscal year containing ``d``.

        A date at or after the start month opens a new fiscal year. Which
        calendar year that fiscal year is NAMED for is the convention question,
        and it is the part people get wrong: with ``year_label: end``, both
        2025-04-01 and 2026-03-31 belong to FY2026.
        """
        opens_in = d.year if d.month >= self.start_month else d.year - 1
        if self.start_month == 1:
            opens_in = d.year
        return opens_in + 1 if self.year_label == "end" else opens_in

    def opening_calendar_year(self, fy: int) -> int:
        if self.start_month == 1:
            return fy
        return fy - 1 if self.year_label == "end" else fy

    # ---------------------------------------------------------------- bounds
    def year_bounds(self, fy: int) -> Tuple[date, date]:
        y0 = self.opening_calendar_year(fy)
        start = date(y0, self.start_month, 1)
        end = self._add_months(start, 12) - timedelta(days=1)
        return start, end

    def quarter_bounds(self, fy: int, q: int) -> Tuple[date, date]:
        if not 1 <= int(q) <= 4:
            raise FiscalCalendarError("fiscal quarter must be 1-4, got %r" % q)
        start = self._add_months(self.year_bounds(fy)[0], (int(q) - 1) * 3)
        return start, self._add_months(start, 3) - timedelta(days=1)

    def month_bounds(self, fy: int, m: int) -> Tuple[date, date]:
        if not 1 <= int(m) <= 12:
            raise FiscalCalendarError("fiscal month must be 1-12, got %r" % m)
        start = self._add_months(self.year_bounds(fy)[0], int(m) - 1)
        return start, self._add_months(start, 1) - timedelta(days=1)

    def period_bounds(self, token: str) -> Tuple[date, date]:
        """Resolve 'FY2026', 'FY2026-Q3' or 'FY2026-M05' to inclusive dates."""
        m = PERIOD_RE.match(str(token).strip())
        if not m:
            raise FiscalCalendarError(
                "unrecognised fiscal period %r - expected FY2026, FY2026-Q3 or "
                "FY2026-M05" % token)
        fy = int(m.group("fy"))
        if m.group("q"):
            return self.quarter_bounds(fy, int(m.group("q")))
        if m.group("m"):
            return self.month_bounds(fy, int(m.group("m")))
        return self.year_bounds(fy)

    @staticmethod
    def _add_months(d: date, n: int) -> date:
        """Month arithmetic that survives 29/30/31-day months and leap years."""
        y, m = divmod((d.year * 12 + (d.month - 1)) + n, 12)
        return date(y, m + 1, min(d.day, _cal.monthrange(y, m + 1)[1]))

    # --------------------------------------------------------------- reading
    def period_token(self, d: date, grain: str = "quarter") -> str:
        fy = self.fiscal_year(d)
        if grain == "year":
            return "FY%d" % fy
        if grain == "month":
            return "FY%d-M%02d" % (fy, self.fiscal_month(d))
        return "FY%d-Q%d" % (fy, self.fiscal_quarter(d))

    def describe(self, d: date) -> Dict[str, Any]:
        fy, q, fm = self.fiscal_year(d), self.fiscal_quarter(d), self.fiscal_month(d)
        ys, ye = self.year_bounds(fy)
        qs, qe = self.quarter_bounds(fy, q)
        return {
            "calendar": self.key, "calendar_label": self.label,
            "fiscal_year": fy, "fiscal_year_label": "FY%d" % fy,
            "fiscal_year_start": str(ys), "fiscal_year_end": str(ye),
            "fiscal_quarter": q,
            "fiscal_quarter_label": self.quarter_names[q - 1],
            "fiscal_quarter_start": str(qs), "fiscal_quarter_end": str(qe),
            "fiscal_month": fm,
            "period_token": self.period_token(d, "quarter"),
            "label": "FY%d %s (fiscal month %d)" % (fy, self.quarter_names[q - 1], fm),
        }

    def describe_window(self, start: date, end: date) -> Dict[str, Any]:
        """Fiscal reading of an analysis window, including the awkward case.

        A window that straddles 31 March sits in two fiscal years. Silently
        labelling it with one of them is how a period comparison ends up
        comparing across a year-end reset, so it is flagged instead.
        """
        a, b = self.describe(start), self.describe(end)
        months = self.months_in_window(start, end)
        return {
            "calendar": self.key, "calendar_label": self.label,
            "start": a, "end": b,
            "crosses_fiscal_year": a["fiscal_year"] != b["fiscal_year"],
            "crosses_fiscal_quarter": (a["fiscal_year"], a["fiscal_quarter"])
                                      != (b["fiscal_year"], b["fiscal_quarter"]),
            "fiscal_months_touched": months,
            "label": (a["label"] if a["label"] == b["label"]
                      else "%s to %s" % (a["label"], b["label"])),
        }

    def months_in_window(self, start: date, end: date) -> List[Dict[str, Any]]:
        """Every fiscal month the window touches, with the number of days it
        overlaps. This is what lets a plan figure be prorated correctly instead
        of divided by a nominal 30.4."""
        out: List[Dict[str, Any]] = []
        cur = date(start.year, start.month, 1)
        while cur <= end:
            m_end = self._add_months(cur, 1) - timedelta(days=1)
            lo, hi = max(cur, start), min(m_end, end)
            if lo <= hi:
                out.append({
                    "calendar_year": cur.year, "calendar_month": cur.month,
                    "fiscal_year": self.fiscal_year(cur),
                    "fiscal_month": self.fiscal_month(cur),
                    "fiscal_quarter": self.fiscal_quarter(cur),
                    "days_in_month": (m_end - cur).days + 1,
                    "days_in_window": (hi - lo).days + 1,
                })
            cur = self._add_months(cur, 1)
        return out


# ------------------------------------------------------------------- registry
def from_contract(kpi_config: Dict[str, Any]) -> FiscalCalendar:
    """Build the calendar the contract declares.

    An undeclared calendar key is an error, not a silent fallback to Gregorian -
    that is exactly the failure this module exists to remove.
    """
    key = kpi_config.get("fiscal_calendar") or "jan_dec"
    registry = kpi_config.get("fiscal_calendars") or {}
    if key not in registry:
        raise FiscalCalendarError(
            "kpi_contract declares fiscal_calendar: %r but no such calendar is "
            "defined under fiscal_calendars. Declared: %s"
            % (key, ", ".join(sorted(registry)) or "(none)"))
    return FiscalCalendar(key, registry[key])
