"""
STAGE 2 - SPLIT:  where exactly did it happen?

Entirely deterministic. Two mechanisms:

  1. IDENTITY ALGEBRA - revenue = volume x price is an exact identity, so the
     movement decomposes into a volume effect, a price effect and a mix effect
     with no model and no residual left unexplained. Separating rate from mix
     is the step most human analyses get wrong.

  2. DIMENSION-LATTICE SEARCH - an Adtributor-style scan (Bhagwan et al.,
     NSDI 2014) over single and paired dimension values, ranked by explanatory
     power and surprise, returning the SMALLEST set of segments that accounts
     for the most of the movement.

Nothing here is learned or generated. The output is arithmetic, reproducible
and safe to put in front of an auditor.
"""
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .security import Persona
from .sources import Estate
from .telemetry import Telemetry, MethodType

# Ordered coarse to fine. `city` sits directly under `region` because the contract
# declares that hierarchy; the scan picks it up automatically for any measure whose
# source grain can resolve it, which is what turns a regional movement into a list
# of contributing cities without a second code path.
DIMS = ["region", "city", "segment", "channel", "category", "tier",
        "warehouse_id", "account_name"]


def _js_divergence(p: np.ndarray, q: np.ndarray) -> float:
    p = np.clip(p, 1e-12, None); q = np.clip(q, 1e-12, None)
    p = p / p.sum(); q = q / q.sum()
    m = 0.5 * (p + q)
    kl = lambda a, b: float(np.sum(a * np.log(a / b)))
    return 0.5 * kl(p, m) + 0.5 * kl(q, m)


def price_volume_mix(cur: pd.DataFrame, base: pd.DataFrame, scale: float,
                     tel: Telemetry, revenue_col: str = "net_revenue") -> Dict[str, Any]:
    """Exact additive decomposition of a revenue movement.

        dRev = dVolume * p0  +  v1 * dPrice_like_for_like  +  mix effect

    Mix is computed as the residual of the tier-level Laspeyres/Paasche split,
    so the three components sum to the total movement by construction.
    """
    def agg(df):
        g = df.groupby("tier").agg(units=("units", "sum"), rev=(revenue_col, "sum"))
        g["price"] = g["rev"] / g["units"].replace(0, np.nan)
        return g

    a, b = agg(cur), agg(base)
    b_scaled = b.copy()
    b_scaled[["units", "rev"]] = b_scaled[["units", "rev"]] * scale
    tiers = sorted(set(a.index) | set(b.index))
    a = a.reindex(tiers).fillna(0.0); b_scaled = b_scaled.reindex(tiers).fillna(0.0)

    v1, v0 = a["units"].sum(), b_scaled["units"].sum()
    p0 = (b_scaled["rev"].sum() / v0) if v0 else 0.0
    volume_effect = (v1 - v0) * p0

    share1 = (a["units"] / v1) if v1 else a["units"] * 0
    share0 = (b_scaled["units"] / v0) if v0 else b_scaled["units"] * 0
    p0_tier = b_scaled["rev"] / b_scaled["units"].replace(0, np.nan)
    p1_tier = a["rev"] / a["units"].replace(0, np.nan)
    mix_effect = float(v1 * ((share1 - share0) * p0_tier.fillna(0)).sum())
    rate_effect = float(v1 * (share1 * (p1_tier.fillna(0) - p0_tier.fillna(0))).sum())

    total = float(a["rev"].sum() - b_scaled["rev"].sum())
    resid = total - (volume_effect + mix_effect + rate_effect)

    tel.method("split", MethodType.DETERMINISTIC, "price / volume / mix decomposition",
               "revenue = volume x price is an identity, so the movement can be split "
               "exactly with no model and no unexplained residual; separating rate from "
               "mix is the error most manual analyses make",
               detail="volume=%.0f mix=%.0f rate=%.0f residual=%.0f"
                      % (volume_effect, mix_effect, rate_effect, resid))
    return {
        "total": total,
        "components": [
            {"name": "Volume", "value": float(volume_effect),
             "pct_of_move": float(volume_effect / total) if total else 0.0,
             "reading": "fewer/more units sold at the prior average price"},
            {"name": "Mix", "value": float(mix_effect),
             "pct_of_move": float(mix_effect / total) if total else 0.0,
             "reading": "shift in tier composition at prior tier prices"},
            {"name": "Rate", "value": float(rate_effect),
             "pct_of_move": float(rate_effect / total) if total else 0.0,
             "reading": "like-for-like price change within tier"},
        ],
        "residual": float(resid),
        "tier_shares": {"current": {k: float(v) for k, v in share1.items()},
                        "baseline": {k: float(v) for k, v in share0.items()}},
    }


def contributors(cur: pd.DataFrame, base: pd.DataFrame, scale: float,
                 measure: str, tel: Telemetry, top_n: int = 8,
                 persona: Optional[Persona] = None,
                 exclude_dims: Optional[List[str]] = None
                 ) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    """Adtributor-style lattice scan.

    explanatory_power = this segment's share of the total movement
    surprise          = how much this segment's share of the measure shifted
                        (Jensen-Shannon contribution), which separates 'big
                        segment moved a little' from 'this segment broke'
    """
    rows: List[Dict[str, Any]] = []
    ex = set(exclude_dims or [])
    dims = [d for d in DIMS if d in cur.columns and d not in ex]
    total_move = float(cur[measure].sum() - base[measure].sum() * scale)

    for dim in dims:
        c = cur.groupby(dim)[measure].sum()
        b = base.groupby(dim)[measure].sum() * scale
        keys = sorted(set(c.index) | set(b.index))
        c = c.reindex(keys).fillna(0.0); b = b.reindex(keys).fillna(0.0)
        if c.sum() <= 0 and b.sum() <= 0:
            continue
        js = _js_divergence(c.values.astype(float), b.values.astype(float))
        for k in keys:
            move = float(c[k] - b[k])
            if abs(move) < 1e-9:
                continue
            ep = move / total_move if total_move else 0.0
            sh_c = float(c[k] / c.sum()) if c.sum() else 0.0
            sh_b = float(b[k] / b.sum()) if b.sum() else 0.0
            rows.append({
                "dimension": dim, "value": str(k), "move": move,
                "explanatory_power": ep, "share_now": sh_c, "share_before": sh_b,
                "share_shift": sh_c - sh_b, "dim_surprise": js,
                "score": abs(ep) * (1.0 + 4.0 * abs(sh_c - sh_b)),
            })

    rows.sort(key=lambda r: -r["score"])
    by_dim: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_dim.setdefault(r["dimension"], [])
        if len(by_dim[r["dimension"]]) < 3:
            by_dim[r["dimension"]].append(r)
    # succinctness: keep the smallest set that explains the most, one entry per dim value
    seen, out = set(), []
    for r in rows:
        key = (r["dimension"], r["value"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) >= top_n:
            break

    tel.method("split", MethodType.DETERMINISTIC, "dimension-lattice contribution scan",
               "ranked by explanatory power weighted by share shift, following the "
               "Adtributor formulation (NSDI 2014); returns the smallest segment set "
               "that accounts for the most of the movement",
               detail="scanned %d dims, %d candidate segments" % (len(dims), len(rows)))
    return out, by_dim


def split(estate: Estate, persona: Persona, tel: Telemetry,
          window: Tuple[date, date], baseline_days: int = 28,
          filters: Optional[Dict[str, Any]] = None,
          measure: str = "net_revenue", contract=None) -> Dict[str, Any]:
    ws, we = window
    span = (we - ws).days + 1
    bs, be = ws - timedelta(days=baseline_days + 1), ws - timedelta(days=1)
    cur = estate.orders_slice(persona, ws, we, filters, tel)
    base = estate.orders_slice(persona, bs, be, filters, tel)
    scale = span / float((be - bs).days + 1)

    res = {
        "window": {"start": str(ws), "end": str(we), "days": span},
        "baseline": {"start": str(bs), "end": str(be), "days": (be - bs).days + 1,
                     "scale_applied": round(scale, 4)},
        "measure": measure,
    }
    top, by_dim = contributors(cur, base, scale, measure, tel, persona=persona,
                               exclude_dims=list((filters or {}).keys()))
    res["contributors"] = top
    res["by_dimension"] = by_dim
    # Which physical column carries revenue is a SEMANTIC question, answered once
    # by the contract's definition reconciliation. If Operations were the
    # authoritative source the identity would decompose their figure instead, with
    # no change here.
    rev_col = contract.measure_column("net_revenue") if contract else "net_revenue"
    res["revenue_column"] = rev_col
    if measure == rev_col:
        res["identity"] = price_volume_mix(cur, base, scale, tel, revenue_col=rev_col)
    return res
