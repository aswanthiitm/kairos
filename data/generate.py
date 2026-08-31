"""
Synthetic estate for the Why Layer prototype.

Generates four sources with DIFFERENT grains and refresh cadences, then injects
five scenarios whose ground truth is written to ground_truth.json so the engine
can be scored against what was actually planted.

  orders          order-line grain, hourly refresh   -> DuckDB
  dispatch_log    shipment grain,   daily T+1        -> CSV   (deliberately stale)
  interactions    event grain,      15-min           -> JSONL (unstructured, PII)
  market_events   event grain,      weekly           -> CSV   (external)

Deterministic: seeded, so every run reproduces the same estate.
"""
import json, os, random, hashlib
from datetime import date, timedelta
import numpy as np
import pandas as pd

SEED = 20260831
random.seed(SEED); np.random.seed(SEED)
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "generated")
os.makedirs(OUT, exist_ok=True)

START = date(2026, 1, 1)
END   = date(2026, 8, 30)
TODAY = date(2026, 8, 31)
DAYS  = (END - START).days + 1
dates = [START + timedelta(days=i) for i in range(DAYS)]

REGIONS   = ["North", "South", "East", "West"]
SEGMENTS  = ["Enterprise", "Mid-Market", "SMB"]
CHANNELS  = ["Direct", "Distributor", "ModernTrade", "Ecommerce"]
CATEGORIES= ["Dairy", "Staples", "Snacks", "Beverages"]
NEW_CAT   = "ColdPressedOils"
TIERS     = ["Premium", "Standard", "Discount"]
WAREHOUSES= ["WH-1", "WH-2", "WH-3", "WH-4"]

LIST_PRICE = {"Dairy": 240.0, "Staples": 95.0, "Snacks": 180.0,
              "Beverages": 130.0, NEW_CAT: 420.0}
TIER_DISCOUNT = {"Premium": 0.02, "Standard": 0.09, "Discount": 0.22}

# ---------------------------------------------------------------- scenarios
SC = {
    "S1_sla_shock": {
        "warehouse": "WH-3",
        "sla_start": date(2026, 8, 3), "sla_end": date(2026, 8, 24),
        "otd_normal": 0.945, "otd_shocked": 0.70,
        "accounts": ["ACC-N012", "ACC-N027", "ACC-N031"],
        "account_names": ["Kestrel Foods", "Vantiq Retail", "Orbit Foods"],
        "behaviour_lag_start": date(2026, 8, 13),
        "health_multiplier": 0.62, "cohort_multiplier": 0.90,
        "tier_shift_start": date(2026, 8, 12),
        "tier_shift_pp": 0.12,
    },
    "S2_ambiguous_west": {
        "start": date(2026, 8, 10), "end": date(2026, 8, 24),
        "region": "West", "volume_multiplier": 0.945,
        "price_bump_category": "Staples", "price_bump_pct": 0.06,
    },
    "S3_sparse_new_category": {"category": NEW_CAT, "launch": date(2026, 8, 12), "region": "East"},
    "S4_stale_feed": {"feed_max_date": date(2026, 8, 28),
                      "dropped_late_warehouse": "WH-4",
                      "drop_from": date(2026, 8, 24)},
}

# ---------------------------------------------------------------- accounts
def build_accounts():
    rows, idx = [], {r: 0 for r in REGIONS}
    named = dict(zip(SC["S1_sla_shock"]["accounts"], SC["S1_sla_shock"]["account_names"]))
    prefixes = ["Kestrel", "Vantiq", "Orbit", "Sundara", "Marut", "Anvaya", "Trilok", "Pashan",
                "Nimbus", "Kaveri", "Ashwin", "Girija", "Tamas", "Vraj", "Nilaya", "Suvarna",
                "Chandra", "Bhavya", "Ekant", "Harith", "Ishaan", "Jalaj", "Kanan", "Lohit",
                "Mihir", "Nayan", "Ojas", "Pranay", "Rachit", "Samvid"]
    suffixes = ["Foods", "Retail", "Traders", "Distributors", "Mart", "Provisions", "Stores"]
    for r in REGIONS:
        n = 34 if r == "North" else 30
        for _ in range(n):
            idx[r] += 1
            aid = "ACC-%s%03d" % (r[0], idx[r])
            if aid in named:
                nm, seg = named[aid], "Enterprise"
            else:
                nm = "%s %s" % (random.choice(prefixes), random.choice(suffixes))
                seg = random.choices(SEGMENTS, weights=[0.18, 0.37, 0.45])[0]
            home = {"North": "WH-3", "South": "WH-1", "East": "WH-4", "West": "WH-2"}[r]
            # ~30% of accounts are cross-served -> gives a WITHIN-region control group
            wh = home if random.random() < 0.70 else random.choice([w for w in WAREHOUSES if w != home])
            if aid in named:
                wh = "WH-3"
            base = {"Enterprise": 145.0, "Mid-Market": 52.0, "SMB": 17.0}[seg] * random.uniform(0.75, 1.3) * 280.0
            rows.append(dict(account_id=aid, account_name=nm, region=r, segment=seg,
                             warehouse_id=wh, base_units=round(base, 2),
                             channel=random.choices(CHANNELS, weights=[.3, .3, .25, .15])[0],
                             contact_phone="+91%d" % random.randint(6000000000, 9999999999),
                             contact_email="ops@%s.example" % nm.split()[0].lower()))
    return pd.DataFrame(rows)

accounts = build_accounts()

def weekday_factor(d):
    return [1.12, 1.08, 1.05, 1.02, 1.10, 0.72, 0.38][d.weekday()]

def season_factor(d):
    doy = d.timetuple().tm_yday
    return 1.0 + 0.06 * np.sin(2 * np.pi * (doy - 20) / 365.0) + 0.03 * np.sin(2 * np.pi * doy / 91.0)

def account_health(aid, wh, d):
    """Service failure propagates to reorder behaviour with a lag.
    Every WH-3 account feels it mildly; the three large accounts that escalated
    cut back hard. This is what makes the WH-3 cohort separable from the rest
    of North -> a usable within-region control group."""
    s1 = SC["S1_sla_shock"]
    if wh != s1["warehouse"] or d < s1["behaviour_lag_start"]:
        return 1.0
    ramp = min(1.0, (d - s1["behaviour_lag_start"]).days / 6.0)
    floor = s1["health_multiplier"] if aid in s1["accounts"] else s1["cohort_multiplier"]
    return 1.0 - (1.0 - floor) * ramp

def tier_for(region, d):
    s1 = SC["S1_sla_shock"]
    w = {"Premium": 0.22, "Standard": 0.50, "Discount": 0.28}
    if region == "North" and d >= s1["tier_shift_start"]:
        shift = s1["tier_shift_pp"]
        w = {"Premium": 0.22 - shift * 0.45, "Standard": 0.50 - shift * 0.55, "Discount": 0.28 + shift}
    return random.choices(list(w), weights=list(w.values()))[0]

# ---------------------------------------------------------------- orders
order_rows = []
oid = 0
for d in dates:
    wf, sf = weekday_factor(d), season_factor(d)
    for a in accounts.itertuples():
        lam = a.base_units * wf * sf * account_health(a.account_id, a.warehouse_id, d) / 7.0
        s2 = SC["S2_ambiguous_west"]
        if a.region == s2["region"] and s2["start"] <= d <= s2["end"]:
            lam *= s2["volume_multiplier"]
        if lam <= 0 or random.random() > min(0.95, lam / 6.0):
            continue
        n_lines = 1 if a.segment == "SMB" else random.randint(1, 3)
        for _ in range(n_lines):
            cat = random.choices(CATEGORIES, weights=[.30, .28, .22, .20])[0]
            s3 = SC["S3_sparse_new_category"]
            if a.region == s3["region"] and d >= s3["launch"] and random.random() < 0.16:
                cat = NEW_CAT
            tier = tier_for(a.region, d)
            units = max(1, int(np.random.poisson(max(1.0, lam / n_lines))))
            lp = LIST_PRICE[cat]
            s2 = SC["S2_ambiguous_west"]
            if (a.region == s2["region"] and cat == s2["price_bump_category"] and d >= s2["start"]):
                lp *= (1 + s2["price_bump_pct"])
            disc = TIER_DISCOUNT[tier] * random.uniform(0.9, 1.1)
            price = lp * (1 - disc) * random.uniform(0.99, 1.01)
            oid += 1
            order_rows.append(dict(
                order_id="ORD-%06d" % oid, order_date=d, account_id=a.account_id,
                account_name=a.account_name, region=a.region, segment=a.segment,
                channel=a.channel, warehouse_id=a.warehouse_id, category=cat, tier=tier,
                units=units, list_price=round(lp, 2), discount_pct=round(disc, 4),
                unit_price=round(price, 2), net_revenue=round(price * units, 2)))
orders = pd.DataFrame(order_rows)

# ---------------------------------------------------------------- dispatch
disp_rows = []
s1, s4 = SC["S1_sla_shock"], SC["S4_stale_feed"]
for o in orders.sample(frac=0.62, random_state=SEED).itertuples():
    dd = o.order_date + timedelta(days=random.randint(0, 2))
    if dd > s4["feed_max_date"]:
        continue                                    # <- feed is 2 days stale overall
    shocked = (o.warehouse_id == s1["warehouse"] and s1["sla_start"] <= dd <= s1["sla_end"])
    p_on = s1["otd_shocked"] if shocked else s1["otd_normal"]
    on_time = random.random() < p_on
    if (o.warehouse_id == s4["dropped_late_warehouse"] and dd >= s4["drop_from"] and not on_time):
        continue                                    # <- late rows silently missing for WH-4
    promised = 3
    actual = promised + (0 if on_time else random.randint(1, 6))
    reason = "" if on_time else random.choice(
        ["dispatch_window_missed", "vehicle_unavailable", "loading_bay_congestion",
         "route_deviation", "documentation_delay"])
    disp_rows.append(dict(shipment_id="SHP-%06d" % (len(disp_rows) + 1), dispatch_date=dd,
                          warehouse_id=o.warehouse_id, order_id=o.order_id, region=o.region,
                          promised_days=promised, actual_days=actual, on_time=on_time,
                          delay_reason=reason))
dispatch = pd.DataFrame(disp_rows).sort_values("dispatch_date")

# ---------------------------------------------------------------- market events
market = pd.DataFrame([
    dict(event_id="MK-001", start_date=date(2026, 3, 2), end_date=date(2026, 3, 16), region="South",
         channel="ModernTrade", event_type="competitor_promo", intensity=0.6,
         description="Regional competitor 15% off staples bundle"),
    dict(event_id="MK-002", start_date=date(2026, 5, 11), end_date=date(2026, 5, 25), region="North",
         channel="Ecommerce", event_type="own_promo", intensity=0.5,
         description="Our summer beverages push"),
    dict(event_id="MK-003", start_date=SC["S2_ambiguous_west"]["start"],
         end_date=SC["S2_ambiguous_west"]["end"], region="West", channel="ModernTrade",
         event_type="competitor_promo", intensity=0.75,
         description="Competitor 'MonsoonSaver' trade promotion across West ModernTrade"),
    dict(event_id="MK-004", start_date=date(2026, 8, 12), end_date=date(2026, 9, 30), region="East",
         channel="Direct", event_type="own_launch", intensity=0.4,
         description="Cold-pressed oils range launch, East only"),
])

# ---------------------------------------------------------------- CRM text
TEMPLATES_SLA = [
    "Buyer escalated again on the {wh} shipments - {n} consignments landed {d} days past the promised window. They asked for a written recovery plan before the next indent.",
    "Call summary: procurement head at {acct} said the last three deliveries from {wh} were late and they have started dual-sourcing part of the monthly requirement. Tone was firm but not hostile.",
    "Ticket: repeated delivery delays ex-{wh}. Customer says planning team is now buffering stock with an alternate supplier. Requested service credit.",
    "Field note: met the {acct} category manager. Their words - 'we cannot plan promotions if the truck is four days late'. They are holding back the festive indent until we confirm dispatch reliability.",
    "Reached out to {acct} on {phone}. They confirmed the delay pattern started early August and is specific to the {wh} lane. Mail sent to {email} with a recovery commitment.",
]
TEMPLATES_PRICE = [
    "Distributor pushed back on the revised staples price - said the landed cost now sits above the competing brand on shelf.",
    "Call summary: buyer mentioned the new price list and asked whether the earlier slab can be honoured for the current quarter.",
]
TEMPLATES_COMPET = [
    "Modern trade partner flagged a competitor promotion running across their West stores - aggressive shelf pricing on staples for a fortnight.",
    "Field note: competitor 'MonsoonSaver' end-caps visible in three West outlets. Our facings unchanged but offtake looked slower.",
]
TEMPLATES_NOISE = [
    "Routine check-in call. Nothing flagged. Indent for next month confirmed as usual.",
    "Ticket: invoice copy requested for GST reconciliation. Closed same day.",
    "Buyer asked about the new packaging format timeline. No commercial issue raised.",
    "Field note: shelf audit complete, planogram compliance at expected level.",
    "Ticket: minor short-supply on one SKU, resolved with next-day top-up.",
    "Call summary: quarterly business review scheduled. General satisfaction with service.",
]

inter, iid = [], 0
def add_inter(ts, acct_row, typ, text, theme, sentiment):
    global iid
    iid += 1
    inter.append(dict(interaction_id="INT-%06d" % iid, ts=ts.isoformat(), type=typ,
                      account_id=acct_row.account_id, account_name=acct_row.account_name,
                      region=acct_row.region, segment=acct_row.segment,
                      warehouse_id=acct_row.warehouse_id, author_role=random.choice(
                          ["field_sales", "service_desk", "key_account_manager"]),
                      theme=theme, sentiment=sentiment, text=text))

acc_by_id = {a.account_id: a for a in accounts.itertuples()}
# S1 evidence: dense, specific, on the affected accounts
for aid in SC["S1_sla_shock"]["accounts"]:
    a = acc_by_id[aid]
    for k in range(random.randint(5, 8)):
        ts = date(2026, 8, 6) + timedelta(days=random.randint(0, 22))
        t = random.choice(TEMPLATES_SLA).format(wh="WH-3", n=random.randint(2, 5),
                                                d=random.randint(3, 6), acct=a.account_name,
                                                phone=a.contact_phone, email=a.contact_email)
        add_inter(ts, a, random.choice(["ticket", "call_transcript", "field_note"]),
                  t, "service_failure", -0.7)
# a few other WH-3 accounts also grumble (corroboration, independent of the big three)
for a in accounts[(accounts.warehouse_id == "WH-3")].sample(7, random_state=1).itertuples():
    if a.account_id in SC["S1_sla_shock"]["accounts"]:
        continue
    ts = date(2026, 8, 8) + timedelta(days=random.randint(0, 18))
    add_inter(ts, a, "ticket", random.choice(TEMPLATES_SLA).format(
        wh="WH-3", n=random.randint(1, 3), d=random.randint(2, 4), acct=a.account_name,
        phone=a.contact_phone, email=a.contact_email), "service_failure", -0.5)

# S2 evidence: deliberately THIN and CONTRADICTORY - two each way, no majority
west_rows = list(accounts[accounts.region == "West"].itertuples())
_s2 = [(3,  date(2026, 8, 14), "call_transcript", TEMPLATES_PRICE[0],  "price_objection",     -0.40),
       (9,  date(2026, 8, 16), "field_note",      TEMPLATES_COMPET[0], "competitor_activity", -0.30),
       (14, date(2026, 8, 19), "field_note",      TEMPLATES_COMPET[1], "competitor_activity", -0.30),
       (21, date(2026, 8, 12), "call_transcript", TEMPLATES_PRICE[1],  "price_objection",     -0.35)]
for i, ts, typ, txt, theme, sent in _s2:
    add_inter(ts, west_rows[i], typ, txt, theme, sent)

# background noise across the whole estate
for _ in range(1400):
    a = acc_by_id[random.choice(list(acc_by_id))]
    ts = START + timedelta(days=random.randint(0, DAYS - 1))
    add_inter(ts, a, random.choice(["ticket", "call_transcript", "field_note", "email"]),
              random.choice(TEMPLATES_NOISE), "routine", random.uniform(-0.1, 0.4))
interactions = pd.DataFrame(inter).sort_values("ts")

# ---------------------------------------------------------------- plan
plan_rows = []
for r in REGIONS:
    base = orders[orders.region == r].groupby("order_date").net_revenue.sum().mean()
    for m in range(1, 9):
        plan_rows.append(dict(region=r, year=2026, month=m,
                              plan_net_revenue=round(base * 30.4 * 1.04, 2)))
plan = pd.DataFrame(plan_rows)

# ---------------------------------------------------------------- persist
orders.to_csv(os.path.join(OUT, "orders.csv"), index=False)
dispatch.to_csv(os.path.join(OUT, "dispatch_log.csv"), index=False)
market.to_csv(os.path.join(OUT, "market_events.csv"), index=False)
plan.to_csv(os.path.join(OUT, "plan.csv"), index=False)
accounts.to_csv(os.path.join(OUT, "accounts.csv"), index=False)
with open(os.path.join(OUT, "interactions.jsonl"), "w") as f:
    for r in interactions.to_dict("records"):
        f.write(json.dumps(r) + "\n")

import duckdb
db = os.path.join(HERE, "warehouse.duckdb")
if os.path.exists(db):
    os.remove(db)
con = duckdb.connect(db)
con.execute("CREATE TABLE orders AS SELECT * FROM read_csv_auto('%s')" % os.path.join(OUT, "orders.csv"))
con.execute("CREATE TABLE dispatch AS SELECT * FROM read_csv_auto('%s')" % os.path.join(OUT, "dispatch_log.csv"))
con.execute("CREATE TABLE market_events AS SELECT * FROM read_csv_auto('%s')" % os.path.join(OUT, "market_events.csv"))
con.execute("CREATE TABLE plan AS SELECT * FROM read_csv_auto('%s')" % os.path.join(OUT, "plan.csv"))
con.execute("CREATE TABLE accounts AS SELECT * FROM read_csv_auto('%s')" % os.path.join(OUT, "accounts.csv"))
con.close()

# ---------------------------------------------------------------- ground truth
win_s, win_e = date(2026, 8, 17), date(2026, 8, 30)
o = orders[(orders.order_date >= win_s) & (orders.order_date <= win_e) & (orders.region == "North")]
base = orders[(orders.order_date >= win_s - timedelta(days=28)) &
              (orders.order_date < win_s - timedelta(days=0)) & (orders.region == "North")]
gt = {
    "generated_at": TODAY.isoformat(),
    "seed": SEED,
    "analysis_window": {"start": win_s.isoformat(), "end": win_e.isoformat()},
    "scenarios": {
        "S1_multi_factor": {
            "kpi": "net_revenue", "region": "North",
            "true_drivers": [
                {"driver": "warehouse_sla_failure", "entity": "WH-3",
                 "mechanism": ["warehouse_sla", "delivery_delay", "customer_trust",
                               "reorder_behaviour", "order_volume", "net_revenue"],
                 "affected_accounts": SC["S1_sla_shock"]["account_names"],
                 "expected_rank": 1, "type": "causal_service_failure"},
                {"driver": "tier_mix_shift", "entity": "Discount tier",
                 "mechanism": ["tier_mix", "price_level", "net_revenue"],
                 "expected_rank": 2, "type": "arithmetic_mix"}],
            "control_group_available": True,
            "control_definition": "North accounts served by warehouses other than WH-3",
            "expected_verdict": "CONFIRMED", "expected_max_ladder": "L3"},
        "S2_ambiguous": {
            "kpi": "order_volume", "region": "West",
            "confounded_pair": ["competitor_promo MK-003", "staples price increase +6%"],
            "note": "both start 2026-08-10 in the same region and channel mix; no clean control",
            "expected_verdict": "COMPETING", "expected_max_ladder": "L1",
            "expected_behaviour": "abstain from a single cause; propose a separating test"},
        "S3_sparse": {
            "kpi": "net_revenue", "slice": {"category": NEW_CAT, "region": "East"},
            "history_days": (END - SC["S3_sparse_new_category"]["launch"]).days + 1,
            "expected_verdict": "INSUFFICIENT_HISTORY",
            "expected_behaviour": "widen interval using peer-category prior, no causal claim"},
        "S4_data_quality": {
            "kpi": "otd_pct", "warehouse": "WH-4",
            "artefact": "late shipments missing from feed since 2026-08-24; feed also 2 days stale overall",
            "expected_verdict": "DATA_QUALITY",
            "expected_behaviour": "flag pipeline artefact BEFORE any business explanation"},
        "S5_entitlement": {
            "personas": ["cfo", "rsm_north", "supply_chain_lead", "data_analyst"],
            "expected_behaviour": "same movement, four narratives; withheld-evidence notice for restricted personas"},
    },
    "measured": {
        "north_window_revenue": float(o.net_revenue.sum()),
        "north_prior_28d_daily_avg": float(base.groupby("order_date").net_revenue.sum().mean()),
        "affected_account_window_revenue": float(o[o.account_id.isin(SC["S1_sla_shock"]["accounts"])].net_revenue.sum()),
    },
}
with open(os.path.join(OUT, "ground_truth.json"), "w") as f:
    json.dump(gt, f, indent=2, default=str)

print("orders        %7d rows  %s .. %s" % (len(orders), orders.order_date.min(), orders.order_date.max()))
print("dispatch      %7d rows  %s .. %s  (STALE by design)" % (len(dispatch), dispatch.dispatch_date.min(), dispatch.dispatch_date.max()))
print("interactions  %7d rows" % len(interactions))
print("market_events %7d rows" % len(market))
print("accounts      %7d rows" % len(accounts))
print("duckdb -> data/warehouse.duckdb")
