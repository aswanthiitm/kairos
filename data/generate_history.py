"""
The HISTORICAL estate: three years of episodes with known drivers.

Why this exists
---------------
The ML driver-ranker learns "given everything the evidence engine measured about
a candidate, how likely is it to be the driver that mattered". That question can
only be answered from a corpus of RESOLVED episodes. This company does not have
one yet - ``runtime/feedback.jsonl`` starts empty, and the production estate
contains five planted scenarios, which is not a training set.

So we simulate one, honestly and with the limits stated out loud:

  * the generative process is the SAME one that produces the production estate
    (data/generate.py) - same schema, same account structure, same mechanisms,
    different seed and a longer horizon;
  * ~450 episodes are planted across 16 (region x channel) slices over three
    years, each with a known driver, a known magnitude, and matching CRM text;
  * roughly a quarter are NULL episodes with no planted driver at all, because a
    ranker that has never seen an episode with no answer will confidently invent
    one - and abstention is a first-class outcome in this engine;
  * magnitudes are randomised, so the corpus contains obvious episodes, marginal
    ones, and some that are genuinely undetectable.

WHAT THIS IS NOT: it is not evidence that the ranker works on real data. It is a
bootstrap that makes the ranker exist and lets it be evaluated on a time-based
holdout. Every label an analyst supplies through ``feedback.py`` is worth more
than any row in here, and ``train.py --include-feedback`` weights them that way.

Run:  python data/generate_history.py [--years 3] [--seed 7]
"""
import argparse
import json
import os
import random
import sys
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "history")
sys.path.insert(0, os.path.dirname(HERE))
from kairos.fiscal import from_contract as fiscal_from_contract

# Same schema as the production estate, read from the same contract. The ML
# corpus has to carry the columns production carries - city included - or the
# ranker trains on a feature distribution the live engine never produces.
_CONTRACT = yaml.safe_load(open(os.path.join(
    os.path.dirname(HERE), "config", "kpi_contract.yaml")))
CITIES = _CONTRACT["dimensions"]["region"]["members"]
FISCAL = fiscal_from_contract(_CONTRACT)
RETURN_RATE_BAND = (0.004, 0.030)
SHIP_PCT = {"Direct": (0.012, 0.022), "Distributor": (0.010, 0.018),
            "ModernTrade": (0.018, 0.032), "Ecommerce": (0.028, 0.048)}

REGIONS = ["North", "South", "East", "West"]
SEGMENTS = ["Enterprise", "Mid-Market", "SMB"]
CHANNELS = ["Direct", "Distributor", "ModernTrade", "Ecommerce"]
CATEGORIES = ["Dairy", "Staples", "Snacks", "Beverages"]
TIERS = ["Premium", "Standard", "Discount"]
WAREHOUSES = ["WH-1", "WH-2", "WH-3", "WH-4"]
HOME_WH = {"North": "WH-3", "South": "WH-1", "East": "WH-4", "West": "WH-2"}

LIST_PRICE = {"Dairy": 240.0, "Staples": 95.0, "Snacks": 180.0, "Beverages": 130.0}
TIER_DISCOUNT = {"Premium": 0.02, "Standard": 0.09, "Discount": 0.22}

SLOT_DAYS = 28          # one episode slot per slice; window sits inside it
WARMUP_DAYS = 95        # history the engine needs before the first episode
PLANT_RATE = 0.62       # the rest stay clean and become NULL episodes
SECOND_DRIVER_RATE = 0.16   # co-occurring drivers, the S1-style hard case

DRIVER_MIX = [("service_failure", 0.28), ("external_market", 0.24),
              ("price_change", 0.20), ("mix_shift", 0.18),
              ("instrumentation", 0.10)]


# --------------------------------------------------------------------- accounts
def build_accounts(rng: random.Random) -> pd.DataFrame:
    prefixes = ["Kestrel", "Vantiq", "Orbit", "Sundara", "Marut", "Anvaya", "Trilok",
                "Pashan", "Nimbus", "Kaveri", "Ashwin", "Girija", "Tamas", "Vraj",
                "Nilaya", "Suvarna", "Chandra", "Bhavya", "Ekant", "Harith", "Ishaan",
                "Jalaj", "Kanan", "Lohit", "Mihir", "Nayan", "Ojas", "Pranay",
                "Rachit", "Samvid", "Tejas", "Urvi", "Vihaan", "Yamini"]
    suffixes = ["Foods", "Retail", "Traders", "Distributors", "Mart", "Provisions",
                "Stores", "Agencies"]
    rows, idx = [], {r: 0 for r in REGIONS}
    for r in REGIONS:
        for ch in CHANNELS:
            for _ in range(11):        # 11 accounts per region x channel = 176 total
                idx[r] += 1
                aid = "ACC-%s%03d" % (r[0], idx[r])
                seg = rng.choices(SEGMENTS, weights=[0.18, 0.37, 0.45])[0]
                home = HOME_WH[r]
                wh = home if rng.random() < 0.70 else rng.choice(
                    [w for w in WAREHOUSES if w != home])
                base = ({"Enterprise": 145.0, "Mid-Market": 52.0, "SMB": 17.0}[seg]
                        * rng.uniform(0.75, 1.3) * 280.0)
                nm = "%s %s" % (rng.choice(prefixes), rng.choice(suffixes))
                rows.append(dict(account_id=aid, account_name=nm, region=r, segment=seg,
                                 city=rng.choice(CITIES[r]),
                                 return_rate=round(rng.uniform(*RETURN_RATE_BAND), 5),
                                 channel=ch, warehouse_id=wh, base_units=round(base, 2),
                                 contact_phone="+91%d" % rng.randint(6000000000, 9999999999),
                                 contact_email="ops@%s.example" % nm.split()[0].lower()))
    return pd.DataFrame(rows)


# --------------------------------------------------------------------- planning
def plan_episodes(start: date, end: date, rng: random.Random) -> List[Dict[str, Any]]:
    """Lay out the episode calendar BEFORE any data exists.

    Every planted effect is decided here, so the ground truth is a statement of
    what was constructed rather than an interpretation of what came out.
    """
    eps: List[Dict[str, Any]] = []
    first = start + timedelta(days=WARMUP_DAYS)
    n_slots = ((end - first).days - SLOT_DAYS) // SLOT_DAYS
    kinds = [k for k, _ in DRIVER_MIX]
    wts = [w for _, w in DRIVER_MIX]
    eid = 0

    for region in REGIONS:
        for channel in CHANNELS:
            for s in range(n_slots):
                t = first + timedelta(days=s * SLOT_DAYS + rng.randint(0, 3))
                eid += 1
                ep_id = "EP-%04d" % eid
                if rng.random() > PLANT_RATE:
                    eps.append({
                        "episode_id": ep_id, "region": region, "channel": channel,
                        "kpi": rng.choice(["net_revenue", "order_volume"]),
                        "measure": "net_revenue", "filters": {"region": region,
                                                              "channel": channel},
                        "window": [str(t + timedelta(days=7)), str(t + timedelta(days=20))],
                        "drivers": [], "null_episode": True})
                    if eps[-1]["kpi"] == "order_volume":
                        eps[-1]["measure"] = "units"
                    continue

                drivers = [_make_driver(rng.choices(kinds, weights=wts)[0],
                                        region, channel, t, rng)]
                if rng.random() < SECOND_DRIVER_RATE:
                    other = [k for k in kinds
                             if k not in (drivers[0]["type"], "instrumentation")]
                    drivers.append(_make_driver(rng.choice(other), region, channel, t, rng))

                lead = drivers[0]
                eps.append({
                    "episode_id": ep_id, "region": region, "channel": channel,
                    "kpi": lead["kpi"], "measure": lead["measure"],
                    "filters": (lead["filters"] if lead["type"] == "instrumentation"
                                else {"region": region, "channel": channel}),
                    "window": lead["window"], "drivers": drivers, "null_episode": False})
    rng.shuffle(eps)
    eps.sort(key=lambda e: e["window"][0])
    return eps


def _make_driver(kind: str, region: str, channel: str, t: date,
                 rng: random.Random) -> Dict[str, Any]:
    if kind == "service_failure":
        wh = HOME_WH[region] if rng.random() < 0.75 else rng.choice(WAREHOUSES)
        return {
            "type": "service_failure", "entity": wh, "region": region, "channel": channel,
            "kpi": "net_revenue", "measure": "net_revenue",
            "sla_start": str(t), "sla_end": str(t + timedelta(days=27)),
            "behaviour_lag_start": str(t + timedelta(days=10)),
            "otd_shocked": round(rng.uniform(0.55, 0.84), 3),
            "health_multiplier": round(rng.uniform(0.60, 0.90), 3),
            "evidence_docs": rng.randint(3, 9),
            "window": [str(t + timedelta(days=14)), str(t + timedelta(days=27))]}
    if kind == "external_market":
        return {
            "type": "external_market", "entity": "%s/%s" % (region, channel),
            "region": region, "channel": channel,
            "kpi": "order_volume", "measure": "units",
            "start": str(t + timedelta(days=3)), "end": str(t + timedelta(days=20)),
            "volume_multiplier": round(rng.uniform(0.70, 0.93), 3),
            "intensity": round(rng.uniform(0.4, 0.9), 2),
            "evidence_docs": rng.randint(2, 8),
            "window": [str(t + timedelta(days=7)), str(t + timedelta(days=20))]}
    if kind == "price_change":
        return {
            "type": "price_change", "entity": rng.choice(CATEGORIES),
            "region": region, "channel": channel,
            "kpi": "order_volume", "measure": "units",
            "start": str(t + timedelta(days=3)), "end": str(t + timedelta(days=27)),
            "bump_pct": round(rng.uniform(0.045, 0.13), 3),
            "elasticity": round(rng.uniform(0.45, 1.1), 2),
            "evidence_docs": rng.randint(1, 6),
            "window": [str(t + timedelta(days=7)), str(t + timedelta(days=20))]}
    if kind == "mix_shift":
        return {
            "type": "mix_shift", "entity": "Discount tier",
            "region": region, "channel": channel,
            "kpi": "net_revenue", "measure": "net_revenue",
            "start": str(t + timedelta(days=3)), "end": str(t + timedelta(days=27)),
            "shift_pp": round(rng.uniform(0.06, 0.20), 3),
            "evidence_docs": rng.randint(0, 4),
            "window": [str(t + timedelta(days=7)), str(t + timedelta(days=20))]}
    # instrumentation: late shipment rows silently stop loading for one warehouse
    wh = HOME_WH[region]
    return {
        "type": "instrumentation", "entity": wh, "region": region, "channel": channel,
        "kpi": "otd_pct", "measure": "net_revenue", "filters": {"warehouse_id": wh},
        "drop_from": str(t + timedelta(days=6)), "drop_to": str(t + timedelta(days=20)),
        "evidence_docs": 0,
        "window": [str(t + timedelta(days=10)), str(t + timedelta(days=20))]}


# ------------------------------------------------------------------- generation
class Effects(object):
    """Date-indexed lookup of every planted effect, so the order loop stays O(1)."""

    def __init__(self, episodes: List[Dict[str, Any]]):
        self.sla: List[Dict[str, Any]] = []
        self.market: List[Dict[str, Any]] = []
        self.price: List[Dict[str, Any]] = []
        self.mix: List[Dict[str, Any]] = []
        self.drop: List[Dict[str, Any]] = []
        for ep in episodes:
            for d in ep["drivers"]:
                d = dict(d, episode_id=ep["episode_id"])
                {"service_failure": self.sla, "external_market": self.market,
                 "price_change": self.price, "mix_shift": self.mix,
                 "instrumentation": self.drop}[d["type"]].append(d)
        for lst in (self.sla, self.market, self.price, self.mix, self.drop):
            for d in lst:
                for k in ("sla_start", "sla_end", "behaviour_lag_start", "start",
                          "end", "drop_from", "drop_to"):
                    if k in d:
                        d[k] = date.fromisoformat(d[k])

    def health(self, region: str, channel: str, wh: str, d: date) -> float:
        m = 1.0
        for s in self.sla:
            if (s["region"] == region and s["channel"] == channel and wh == s["entity"]
                    and s["behaviour_lag_start"] <= d <= s["sla_end"] + timedelta(days=14)):
                ramp = min(1.0, (d - s["behaviour_lag_start"]).days / 6.0)
                m *= 1.0 - (1.0 - s["health_multiplier"]) * ramp
        return m

    def volume(self, region: str, channel: str, d: date) -> float:
        m = 1.0
        for e in self.market:
            if e["region"] == region and e["channel"] == channel and e["start"] <= d <= e["end"]:
                m *= e["volume_multiplier"]
        return m

    def price_bump(self, region: str, channel: str, cat: str, d: date) -> float:
        m = 1.0
        for p in self.price:
            if (p["region"] == region and p["channel"] == channel
                    and p["entity"] == cat and p["start"] <= d <= p["end"]):
                m *= (1.0 + p["bump_pct"])
        return m

    def price_elastic(self, region: str, channel: str, cat: str, d: date) -> float:
        m = 1.0
        for p in self.price:
            if (p["region"] == region and p["channel"] == channel
                    and p["entity"] == cat and p["start"] + timedelta(days=2) <= d <= p["end"]):
                m *= max(0.55, 1.0 - p["bump_pct"] * p["elasticity"] * 4.0)
        return m

    def tier_weights(self, region: str, channel: str, d: date) -> Dict[str, float]:
        w = {"Premium": 0.22, "Standard": 0.50, "Discount": 0.28}
        for x in self.mix:
            if x["region"] == region and x["channel"] == channel and x["start"] <= d <= x["end"]:
                s = x["shift_pp"]
                w = {"Premium": max(0.02, w["Premium"] - s * 0.45),
                     "Standard": max(0.02, w["Standard"] - s * 0.55),
                     "Discount": w["Discount"] + s}
        return w

    def otd(self, wh: str, region: str, channel: str, d: date) -> Optional[float]:
        for s in self.sla:
            if s["entity"] == wh and s["sla_start"] <= d <= s["sla_end"]:
                return s["otd_shocked"]
        return None

    def dropped(self, wh: str, d: date) -> bool:
        return any(x["entity"] == wh and x["drop_from"] <= d <= x["drop_to"]
                   for x in self.drop)


def weekday_factor(d: date) -> float:
    return [1.12, 1.08, 1.05, 1.02, 1.10, 0.72, 0.38][d.weekday()]


def season_factor(d: date) -> float:
    doy = d.timetuple().tm_yday
    return (1.0 + 0.06 * np.sin(2 * np.pi * (doy - 20) / 365.0)
            + 0.03 * np.sin(2 * np.pi * doy / 91.0))


def generate(years: int, seed: int) -> Dict[str, Any]:
    rng = random.Random(seed)
    nrng = np.random.default_rng(seed)
    end = date(2025, 12, 31)
    start = date(end.year - years + 1, 1, 1)
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]

    accounts = build_accounts(rng)
    episodes = plan_episodes(start, end, rng)
    fx = Effects(episodes)
    print("planned %d episodes (%d null) over %s .. %s"
          % (len(episodes), sum(1 for e in episodes if e["null_episode"]), start, end))

    # ------------------------------------------------------------------ orders
    rows, oid = [], 0
    acc = list(accounts.itertuples())
    for d in days:
        wf, sf = weekday_factor(d), season_factor(d)
        tw_cache: Dict[Any, Dict[str, float]] = {}
        for a in acc:
            health = fx.health(a.region, a.channel, a.warehouse_id, d)
            lam = a.base_units * wf * sf * health * fx.volume(a.region, a.channel, d) / 7.0
            p_order = min(0.95, lam / 6.0) * health
            if lam <= 0 or rng.random() > p_order:
                continue
            key = (a.region, a.channel)
            if key not in tw_cache:
                tw_cache[key] = fx.tier_weights(a.region, a.channel, d)
            tw = tw_cache[key]
            for _ in range(1 if a.segment == "SMB" else rng.randint(1, 3)):
                cat = rng.choices(CATEGORIES, weights=[.30, .28, .22, .20])[0]
                tier = rng.choices(list(tw), weights=list(tw.values()))[0]
                el = fx.price_elastic(a.region, a.channel, cat, d)
                units = max(1, int(nrng.poisson(max(1.0, lam * el))))
                lp = LIST_PRICE[cat] * fx.price_bump(a.region, a.channel, cat, d)
                disc = TIER_DISCOUNT[tier] * rng.uniform(0.9, 1.1)
                price = lp * (1 - disc) * rng.uniform(0.99, 1.01)
                oid += 1
                gross = round(lp * units, 2)
                billed = round(price * units, 2)
                disc_amt = round(gross - billed, 2)
                ret_amt = round(billed * a.return_rate * rng.uniform(0.7, 1.3), 2)
                fin_net = round(gross - disc_amt - ret_amt, 2)
                slo, shi = SHIP_PCT[a.channel]
                ship = round(max(0.0, fin_net) * rng.uniform(slo, shi), 2)
                rows.append((("ORD-%07d" % oid), d, a.account_id, a.account_name,
                             a.region, a.city, a.segment, a.channel, a.warehouse_id,
                             cat, tier, units, round(lp, 2), round(disc, 4),
                             round(price, 2), gross, disc_amt, ret_amt, ship,
                             fin_net, round(fin_net - ship, 2)))
    orders = pd.DataFrame(rows, columns=[
        "order_id", "order_date", "account_id", "account_name", "region", "city",
        "segment", "channel", "warehouse_id", "category", "tier", "units",
        "list_price", "discount_pct", "unit_price", "gross_revenue",
        "discount_amount", "returns_amount", "shipping_cost",
        "net_revenue", "net_revenue_ops"])

    # ---------------------------------------------------------------- dispatch
    disp = []
    for o in orders.sample(frac=0.62, random_state=seed).itertuples():
        dd = o.order_date + timedelta(days=rng.randint(0, 2))
        if dd > end:
            continue
        p_on = fx.otd(o.warehouse_id, o.region, o.channel, dd)
        on_time = rng.random() < (p_on if p_on is not None else 0.945)
        if not on_time and fx.dropped(o.warehouse_id, dd):
            continue                      # the failure rows silently stop loading
        actual = 3 + (0 if on_time else rng.randint(1, 6))
        disp.append(("SHP-%07d" % (len(disp) + 1), dd, o.warehouse_id, o.order_id,
                     o.region, 3, actual, on_time,
                     "" if on_time else rng.choice(
                         ["dispatch_window_missed", "vehicle_unavailable",
                          "loading_bay_congestion", "route_deviation",
                          "documentation_delay"])))
    dispatch = pd.DataFrame(disp, columns=[
        "shipment_id", "dispatch_date", "warehouse_id", "order_id", "region",
        "promised_days", "actual_days", "on_time", "delay_reason"]).sort_values("dispatch_date")

    # ----------------------------------------------------------- market events
    mk = []
    for i, e in enumerate(fx.market, 1):
        mk.append(dict(event_id="MKH-%04d" % i, start_date=e["start"], end_date=e["end"],
                       region=e["region"], channel=e["channel"],
                       event_type="competitor_promo", intensity=e["intensity"],
                       description="Competitor trade promotion, %s %s"
                                   % (e["region"], e["channel"])))
    # decoy events that changed nothing - a promo calendar full of real promos
    # would let the ranker learn "an event exists" instead of "the event bit"
    for i in range(len(mk) // 3):
        r, ch = rng.choice(REGIONS), rng.choice(CHANNELS)
        s = start + timedelta(days=rng.randint(WARMUP_DAYS, (end - start).days - 30))
        mk.append(dict(event_id="MKD-%04d" % (i + 1), start_date=s,
                       end_date=s + timedelta(days=rng.randint(7, 18)), region=r,
                       channel=ch, event_type="competitor_promo",
                       intensity=round(rng.uniform(0.1, 0.35), 2),
                       description="Competitor shelf activity, %s %s (no measured impact)"
                                   % (r, ch)))
    market = pd.DataFrame(mk).sort_values("start_date")

    # ---------------------------------------------------------------- CRM text
    interactions = _build_text(episodes, accounts, rng, start, end)

    # -------------------------------------------------------------------- plan
    plan = []
    for r in REGIONS:
        base = orders[orders.region == r].groupby("order_date").net_revenue.sum().mean()
        for y in range(start.year, end.year + 1):
            for m in range(1, 13):
                first = date(y, m, 1)
                nxt = date(y + (m // 12), (m % 12) + 1, 1)
                dim = (nxt - first).days
                plan.append(dict(region=r, year=y, month=m, days_in_month=dim,
                                 fiscal_year=FISCAL.fiscal_year(first),
                                 fiscal_quarter=FISCAL.fiscal_quarter(first),
                                 fiscal_month=FISCAL.fiscal_month(first),
                                 fiscal_period=FISCAL.period_token(first, "quarter"),
                                 plan_net_revenue=round(base * dim * 1.04, 2)))
    return {"accounts": accounts, "orders": orders, "dispatch": dispatch,
            "market": market, "interactions": interactions,
            "plan": pd.DataFrame(plan), "episodes": episodes,
            "start": start, "end": end}


# --------------------------------------------------------------------- CRM text
T_SLA = [
    "Buyer escalated again on the {wh} shipments - {n} consignments landed {d} days past the promised window. They asked for a written recovery plan before the next indent.",
    "Call summary: procurement head at {acct} said the last three deliveries from {wh} were late and they have started dual-sourcing part of the monthly requirement.",
    "Ticket: repeated delivery delays ex-{wh}. Customer says planning is now buffering stock with an alternate supplier. Requested service credit.",
    "Field note: {acct} category manager said they cannot plan promotions if the truck is four days late. Holding back the indent until dispatch reliability is confirmed.",
    "Reached out to {acct} on {phone}. They confirmed the delay pattern is specific to the {wh} lane. Mail sent to {email} with a recovery commitment.",
]
T_PRICE = [
    "Distributor pushed back on the revised {cat} price - said the landed cost now sits above the competing brand on shelf.",
    "Call summary: buyer mentioned the new price list on {cat} and asked whether the earlier slab can be honoured this quarter.",
    "Ticket: {acct} disputing the {cat} rate revision. Requested a discount slab review before releasing the next order.",
]
T_COMP = [
    "Modern trade partner flagged a competitor promotion running across their {region} stores - aggressive shelf pricing for a fortnight.",
    "Field note: competitor end-caps visible in three {region} outlets. Our facings unchanged but offtake looked slower.",
    "Call summary: category buyer confirmed the competitor fortnight and gave them incremental shelf for the period.",
    "Ticket: store team reporting our facings intact but rate of sale down since the competitor promotion started. No supply issue raised.",
]
T_NOISE = [
    "Routine check-in call. Nothing flagged. Indent for next month confirmed as usual.",
    "Ticket: invoice copy requested for GST reconciliation. Closed same day.",
    "Buyer asked about the new packaging format timeline. No commercial issue raised.",
    "Field note: shelf audit complete, planogram compliance at expected level.",
    "Ticket: minor short-supply on one SKU, resolved with next-day top-up.",
    "Call summary: quarterly business review scheduled. General satisfaction with service.",
]
ROLES = ["field_sales", "service_desk", "key_account_manager"]
TYPES = ["ticket", "call_transcript", "field_note", "email"]


def _build_text(episodes, accounts, rng, start, end) -> pd.DataFrame:
    """Evidence text, deliberately imperfect.

    Roughly one episode in six also gets documents pointing at a DIFFERENT cause.
    Without them the corroboration conflict ratio would be zero everywhere and
    the ranker would never learn what contested evidence looks like - which is
    the case where getting the order wrong is most expensive.
    """
    by_slice: Dict[Any, List[Any]] = {}
    for a in accounts.itertuples():
        by_slice.setdefault((a.region, a.channel), []).append(a)
    rows, iid = [], 0

    def add(ts, a, typ, text, theme, sent):
        nonlocal iid
        iid += 1
        rows.append(dict(interaction_id="INH-%06d" % iid, ts=ts.isoformat(), type=typ,
                         account_id=a.account_id, account_name=a.account_name,
                         region=a.region, segment=a.segment, warehouse_id=a.warehouse_id,
                         author_role=rng.choice(ROLES), theme=theme, sentiment=sent,
                         text=text))

    for ep in episodes:
        pool = by_slice.get((ep["region"], ep["channel"]), [])
        if not pool:
            continue
        for drv in ep["drivers"]:
            n = int(drv.get("evidence_docs", 0))
            w0 = date.fromisoformat(drv.get("start") or drv.get("sla_start")
                                    or drv["window"][0])
            for _ in range(n):
                a = rng.choice(pool)
                ts = w0 + timedelta(days=rng.randint(0, 18))
                if drv["type"] == "service_failure":
                    add(ts, a, rng.choice(TYPES[:3]), rng.choice(T_SLA).format(
                        wh=drv["entity"], n=rng.randint(1, 5), d=rng.randint(2, 6),
                        acct=a.account_name, phone=a.contact_phone,
                        email=a.contact_email), "service_failure", -0.6)
                elif drv["type"] == "external_market":
                    add(ts, a, rng.choice(TYPES[:3]),
                        rng.choice(T_COMP).format(region=drv["region"]),
                        "competitor_activity", -0.4)
                elif drv["type"] == "price_change":
                    add(ts, a, rng.choice(TYPES[:3]), rng.choice(T_PRICE).format(
                        cat=drv["entity"], acct=a.account_name), "price_objection", -0.4)
                elif drv["type"] == "mix_shift":
                    add(ts, a, rng.choice(TYPES[:3]), rng.choice(T_PRICE).format(
                        cat=rng.choice(CATEGORIES), acct=a.account_name),
                        "price_objection", -0.3)
            # contested windows
            if n and rng.random() < 0.17:
                for _ in range(rng.randint(1, max(1, n // 2 + 1))):
                    a = rng.choice(pool)
                    ts = w0 + timedelta(days=rng.randint(0, 18))
                    theme, txt = rng.choice([
                        ("competitor_activity", rng.choice(T_COMP).format(region=drv["region"])),
                        ("price_objection", rng.choice(T_PRICE).format(
                            cat=rng.choice(CATEGORIES), acct=a.account_name)),
                        ("service_failure", rng.choice(T_SLA).format(
                            wh=a.warehouse_id, n=2, d=3, acct=a.account_name,
                            phone=a.contact_phone, email=a.contact_email))])
                    if theme != {"service_failure": "service_failure",
                                 "external_market": "competitor_activity",
                                 "price_change": "price_objection",
                                 "mix_shift": "price_objection"}.get(drv["type"]):
                        add(ts, a, rng.choice(TYPES[:3]), txt, theme, -0.35)

    span = (end - start).days
    for _ in range(int(span * 6)):
        a = rng.choice(list(accounts.itertuples()))
        ts = start + timedelta(days=rng.randint(0, span))
        add(ts, a, rng.choice(TYPES), rng.choice(T_NOISE), "routine", rng.uniform(-0.1, 0.4))
    return pd.DataFrame(rows).sort_values("ts")


# ------------------------------------------------------------------------ main
def main() -> None:
    ap = argparse.ArgumentParser(description="build the historical training estate")
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    g = generate(a.years, a.seed)

    for name in ("orders", "dispatch", "market", "plan", "accounts"):
        fn = {"dispatch": "dispatch_log", "market": "market_events"}.get(name, name)
        g[name].to_csv(os.path.join(OUT, "%s.csv" % fn), index=False)
    with open(os.path.join(OUT, "interactions.jsonl"), "w") as f:
        for r in g["interactions"].to_dict("records"):
            f.write(json.dumps(r) + "\n")
    with open(os.path.join(OUT, "episodes.json"), "w") as f:
        json.dump({"generated_with_seed": a.seed, "years": a.years,
                   "start": str(g["start"]), "end": str(g["end"]),
                   "n_episodes": len(g["episodes"]),
                   "n_null": sum(1 for e in g["episodes"] if e["null_episode"]),
                   "episodes": g["episodes"]}, f, indent=1, default=str)

    import duckdb
    db = os.path.join(HERE, "history.duckdb")
    if os.path.exists(db):
        os.remove(db)
    con = duckdb.connect(db)
    for tbl, fn in [("orders", "orders"), ("dispatch", "dispatch_log"),
                    ("market_events", "market_events"), ("plan", "plan"),
                    ("accounts", "accounts")]:
        con.execute("CREATE TABLE %s AS SELECT * FROM read_csv_auto('%s')"
                    % (tbl, os.path.join(OUT, "%s.csv" % fn)))
    con.close()

    print("orders        %8d rows  %s .. %s" % (len(g["orders"]), g["orders"].order_date.min(),
                                                g["orders"].order_date.max()))
    print("dispatch      %8d rows" % len(g["dispatch"]))
    print("interactions  %8d rows" % len(g["interactions"]))
    print("market_events %8d rows" % len(g["market"]))
    print("accounts      %8d rows" % len(g["accounts"]))
    print("episodes      %8d  (%d null)" % (len(g["episodes"]),
                                            sum(1 for e in g["episodes"] if e["null_episode"])))
    print("duckdb -> data/history.duckdb")


if __name__ == "__main__":
    main()
