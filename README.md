# The Why Layer

**A KPI intelligence-to-action engine.** Dashboards say *what* changed. This says *why*,
*how sure it is*, and *what to do* — with every number traceable and an explicit refusal
to answer when the evidence will not carry a claim.

Built for **Accenture Innovation Challenge 2026, Round 2 — BusinessIntelligence.ai**.

```bash
git clone <this-repo> && cd why-layer
./run.sh                      # installs deps, builds the estate, serves on :8000
```
Open <http://127.0.0.1:8000>. No API key required — the engine runs fully offline and
tells you so.

---

## The one idea

> Large language models are strong at **proposing** causal explanations from world
> knowledge and near-random at **inferring** causation from correlation.
> So the model proposes and phrases. It never decides, and it never computes.

That split is enforced in code, not promised in a diagram:

| Job | Method | Why |
|---|---|---|
| KPI definitions | **Semantic contract** (YAML) | a metric that isn't declared cannot be computed |
| Whose definition wins | **Definition reconciliation** — compare, quantify, resolve by configured authority | two systems disagreeing about "revenue" is the normal case, not the exception |
| What period, what level | **Fiscal calendar + dimension hierarchy** | Apr–Mar boundaries and region→city are business facts, not date arithmetic |
| Detect a real movement | **Statistics** — robust seasonal decomposition, MAD control limits | the anomaly must not contaminate its own baseline |
| Does it matter | **Business rules** — materiality gate | statistical ≠ important |
| Where it happened | **Deterministic algebra** — price/volume/mix identity + Adtributor-style lattice scan | arithmetic closes exactly; auditable |
| Is the mechanism admissible | **Curated causal graph** | blocks plausible-sounding nonsense |
| Why it happened | **Retrieval** (BM25 over CRM/tickets/calls) + **causal inference** (DiD, placebo, dose–response) | evidence, not vibes |
| What to do | **Playbook memory** — past interventions with measured outcomes | institutional memory, not generic advice |
| How to say it | **LLM** (optional) behind a **numeric guard** | phrasing only |

Every run reports its own **method ledger** and the share of steps that were non-LLM.

---

## The Evidence Ladder

Bradford Hill's viewpoints (1965) operationalised on Pearl's causal hierarchy (CACM 2019).
**Nothing below L2 is ever phrased as a cause.**

| Rung | Test | Language permitted |
|---|---|---|
| **L0** | co-movement | *"moved alongside"* — never a cause |
| **L1** | temporal precedence within the graph's declared lag, + dose–response gradient | *"associated"* |
| **L2** | independent corroboration — unstructured sources not sharing a pipeline agree | *"likely cause"* |
| **L3** | counterfactual — difference-in-differences against a real untreated cohort, with a placebo test on the pre-period | *"quantified"* |

L3 does not require L2. A validated counterfactual is stronger evidence than corroborating
text, so a hypothesis can reach L3 directly — which is exactly what happens for the CFO,
whose entitlement denies them the CRM verbatims that would have produced L2.

---

## The semantic layer

Three things that every real estate has, that most prototypes declare and none of them
obey. All three are resolved **once**, in a `semantic` stage that runs before FITNESS, and
consumed downstream. No stage re-derives a KPI definition, a fiscal boundary or a
hierarchy — that duplication is how two parts of one engine end up computing two different
"net revenues".

### 1. The fiscal calendar is real

`fiscal_calendar: apr_mar` used to be a comment. It now drives period boundaries:

```
FY2026       2025-04-01 .. 2026-03-31      Q1 Apr–Jun · April = fiscal month 1
FY2027-Q1    2026-04-01 .. 2026-06-30
FY2027-M05   2026-08-01 .. 2026-08-31
```

An analysis can be *asked for* as a period — `--fiscal-period FY2027-Q1` — and the window
comes from the contract. The materiality gate's plan denominator is the fiscal period the
window falls in, prorated by days covered. Switching the key to `jan_dec` or `jul_jun`
moves every boundary with no code change, and a test asserts exactly that.

Making it real **exposed a defect**: the old plan query summed every plan row the filter
matched and divided by a nominal 30.4-day month, so the denominator grew with the size of
the plan table rather than with the period. `min_pct_of_plan` had been calibrated against
that, so it is restated from 0.015 to 0.08 with the reasoning in the contract. Backtest
precision stays 1.0, false alarms stay at zero.

Gregorian arithmetic is deliberately left alone where fiscal semantics are irrelevant. A
28-day trailing baseline is 28 days.

### 2. region → city is traversed, not just declared

`city` is generated on every order line **from the contract's own member map**, so the data
and the config cannot drift apart — 16 cities, each in exactly one region, asserted by a
test. A regional movement is attributed to its cities and the roll-up is checked to close
back to the parent:

```
HIERARCHY DRILL-DOWN: North region -> city  (roll-up closes: True)
  Delhi             -158.69 L    65.3% of the move
  Lucknow            -64.87 L    26.7% of the move
  Jaipur             -12.69 L     5.2% of the move
  Chandigarh          -6.90 L     2.8% of the move
```

Two refusals matter more than the traversal:

- **Ratios are re-aggregated, never averaged.** Every KPI declares an `aggregation` block.
  Rolling four cities up to a region ASP re-divides the summed numerator and denominator;
  averaging the four city ASPs would weight a city with ten orders like one with ten
  thousand, and a test asserts the two answers differ.
- **`otd_pct` is refused at city level.** The dispatch feed has no city key. Attributing
  one through the ordering account would produce a number that looks fine and means
  nothing, so the engine reports the grain limit instead. Inferring is worse than refusing,
  because a refusal is visible.

### 3. Two systems, two definitions of "net revenue"

The brief's real complexity is not a missing number, it is two correct ones:

| | Finance | Operations |
|---|---|---|
| formula | `gross - discounts - returns` | `gross - discounts - returns - shipping` |
| system | Finance ledger (ERP GL) | Ops margin cube |
| owner | Group Controller | Head of Supply Chain Finance |
| S1 window | **₹9.1684 Cr** | ₹8.9927 Cr |

```
KPI RECONCILIATION: net_revenue  [RECONCILED]
  SELECTED  finance     gross_sales - discounts - returns              Rs 9.1684 Cr
  rejected  operations  gross_sales - discounts - returns - shipping   Rs 8.9927 Cr
  difference vs operations: Rs +0.1757 Cr (+1.92%)
  resolution: configured authority precedence finance > operations selects 'finance'
```

The engine separates **computational** differences (expression, unit, grain — these change
the number) from **contextual** ones (owner, system, scope — these describe it). Two
definitions of `order_volume` that differ only in paperwork are reported as `EQUIVALENT`,
not as a conflict.

The gap is measured on *identical rows*, so it is definitional and not a data problem. The
losing definition is kept with the reason it lost; nothing is overwritten.

**The governance line.** With no resolution rule configured, the status is `UNRESOLVED` and
the engine abstains — verdict `KPI_DEFINITION_UNRESOLVED`, no movement, no recommendation,
both definitions still on the record. It could pick the first, or the newest, or the
larger. Any of those produces a number, which is exactly the problem: the number would
carry no authority and nothing downstream would know that.

**It propagates.** Flipping the precedence to `[operations, finance]` in the contract
changes the KPI's SQL, the measure column the price/volume/mix identity decomposes, the
column the difference-in-differences cohorts are built from, and the reported revenue —
with no code change. There is a test that asserts the whole chain.

This matters for the ML layer specifically: a ranker trained across two incompatible
definitions of the same KPI would be learning from a measurement that changes meaning
halfway through.

---

## The learned layer

ML is **deliberately absent from attribution**. Revenue = volume x price is an identity;
fitting a model to it would be worse arithmetic with a confidence interval. So the learned
component is placed where the rules were genuinely weakest — **ranking** — and nowhere else.

### The blind spot it fixes

The shipped ranking rule is `share of the movement x ladder confidence`. It is defensible
and it is also structurally broken for a whole class of driver: a competitor promotion or a
price move has **no share of the movement attributed to it** by the lattice scan, so it sits
on the 0.02 floor no matter how strong its evidence. The corpus makes the damage visible:

| top-1, by the driver actually planted | episodes | evidence heuristic | learned model | fused (ships) |
|---|---|---|---|---|
| service_failure | 28 | **1.000** | 0.893 | 1.000 |
| mix_shift | 22 | 0.273 | 1.000 | 0.636 |
| price_change | 22 | **0.000** | 0.818 | 0.545 |
| instrumentation | 12 | 0.250 | 1.000 | 0.417 |
| external_market | 30 | **0.000** | 0.367 | 0.300 |

Across 52 holdout episodes whose true driver was a price move or a competitor promotion,
the heuristic ranked it first **zero times**. Not rarely — never. That is not a tuning
problem; it is what happens when a hand-chosen constant meets a driver type it was not
designed for. (Instrumentation is a red herring in that table: data-quality candidates
short-circuit in `evidence.verdict` before any ranking runs, so their fused number never
reaches a user.)

### Where it sits, and why not earlier

```
SPLIT ──► SOURCE ─────────────────────────────► RANK ──► SOLVE
          graph admissibility                    │
          temporal precedence                    ├── evidence heuristic  (share x rung)
          dose-response                          └── learned prior       (P(driver))
          corroboration + conflict                        │
          DiD + placebo ─────────────────────────────► fused ordering
```

The ranker runs **after** the evidence tests, not before them. The features that separate a
real driver from a merely plausible one — lag alignment against the graph's declared lag,
the dose–response gradient, the conflict ratio in the retrieved text, a counterfactual whose
placebo held — do not exist until those tests have run. Ranking before them would mean
ranking on share and driver type alone.

### What it is not allowed to do

`whylayer/ml/ranker.py` declares its authority and the tests enforce it:

- it **is never shown the evidence rung**, so its score is independent information rather
  than a restatement of the ladder — which is what makes fusing the two meaningful;
- it **cannot promote a rung, change a verdict status, or add or remove a candidate**;
  those are all settled before it is called and none of them is an argument to it;
- its weight in the fused ordering is **capped at 0.5 by governance, not by tuning** — the
  evidence-bearing term keeps at least half the say;
- a candidate whose evidence falls outside the training distribution is **scored, flagged,
  and then ignored**; the model is not asked to extrapolate;
- with no model file installed the engine runs exactly as it did before and **says so** in
  the method ledger.

`test_ml_cannot_change_a_verdict_or_an_evidence_rung` runs every scenario twice — once with
the model loaded, once with it removed — and asserts every verdict status and every ladder
grade is identical. Only the order may differ.

### The model

A **histogram gradient-boosted LambdaRank ensemble**, ~350 lines of numpy, no new
dependencies. It serialises to **plain JSON** — `models/driver-ranker-v1.json` is a text
file whose split thresholds are in original feature units, so the one learned component in
the system is as readable as the rest of it. 42 features, none of them post-hoc: the
feature contract lists what may never be used (the outcome, the realised recovery, the
validated driver) and asserts it at import.

Raw LambdaRank scores are orderings, not probabilities, so they pass through an **isotonic
calibration** fitted on a later, held-out slice before anything calls them a probability.

### How it scored

Time-based holdout on 960 simulated historical episodes — train `2021-04..2024-03`,
calibrate `2024-04..2024-11`, test `2024-12..2025-10`. The test slice is touched once, at
the end; hyper-parameters and the fusion weight are selected on the calibration slice.

```
  arm             top-1      hit@3      MRR     NDCG@3
  heuristic       0.325      0.903    0.614      0.672
  learned model   0.772      0.983    0.877      0.896
  fused (ships)   0.597      0.965    0.773      0.816

  calibration   Brier 0.1211   ECE 0.0364   AUC 0.8040   base rate 0.189
  abstention    top-p 0.424 when a driver exists vs 0.299 when none (separation AUC 0.617)
  candidate-generation recall ceiling  0.880
```

**+27.2pp top-1 over the rule it augments**, with the learned term never exceeding half the
weight. Two numbers there are deliberately unflattering:

- **separation AUC 0.617.** The probability alone barely distinguishes "this episode has a
  driver" from "this episode has none". So abstention stays where it was — with the evidence
  ladder — and the model gets no vote on whether to answer at all.
- **candidate recall 0.880.** A ranker cannot rank a driver that was never proposed. 12% of
  planted drivers never reached the candidate set, and that is a ceiling on every number in
  the table above. It is a candidate-generation problem, not a ranking one.

### Where the labels come from

There is no corpus of resolved incidents yet — `runtime/feedback.jsonl` starts empty. So one
is simulated: `data/generate_history.py` plants 960 episodes across 16 slices over five
years, with randomised magnitudes and ~40% null episodes carrying no driver at all, and
`whylayer/ml/dataset.py` replays **the real engine** over every one of them. The training
features are produced by the same `featurize` call that runs mid-analysis, so there is no
train/serve skew to argue about.

This is a **bootstrap and it is labelled as one**, in the model card the UI renders and in
`cli.py --ml`. Every label an analyst supplies through the feedback loop is worth more:
`feedback.record` writes the graded candidate against the feature snapshot frozen at
analysis time, and `--include-feedback` folds those rows in at 5x the weight of a
simulated one. The path from "an analyst disagreed" to "the ranker learned" is closed.

### One thing this surfaced

Replaying historical windows exposed a real defect in the engine: several reads were
unbounded, so analysing an old window could use rows that landed **after** it. Harmless in
production, where the estate ends at today — and fatal to any honest backtest. `Estate.as_of`
now enforces point-in-time correctness on every read.

---

## What the prototype demonstrates

| Round-2 requirement | Where it lives |
|---|---|
| 3–5 connected KPIs, 2–3 sources, different grains and cadences | 5 KPIs, **4 sources**, 3 grains, 4 refresh cadences (60 / 1440 / 15 / 10080 min) |
| KPI / semantic contract with lineage and access rules | `config/kpi_contract.yaml` |
| ≥2 personas with different narratives | **4 personas**, each with its own row/column/domain entitlements |
| One multi-factor movement with known drivers | **S1** — service failure (80%) + tier-mix shift (10%) |
| One low-confidence scenario → clarification or abstention | **S2** — two rivals at L1, engine abstains and prices a separating test |
| One sparse-history KPI | **S3** — 19 days of history, peer-group prior, causal claim refused |
| One role-based security scenario | **S5** — RSM-North refused a West query *before execution* |
| Evidence: freshness, method, contribution, confidence, lineage | every response carries all five |
| LLM vs non-LLM breakdown | method ledger in the UI and in `telemetry.method_mix` |
| Runtime telemetry: latency, calls, tokens, cost | `telemetry` block on every response |

### The five scenarios

| | Scenario | Planted truth | Engine verdict |
|---|---|---|---|
| **S1** | North revenue −16.1% | WH-3 SLA collapse → 3 accounts cut reorders (10-day lag); plus a Discount-tier mix shift | **CONFIRMED · L3** — DiD −13.4%, p<0.0001, placebo clean; recovers all three accounts and the 10-day lag |
| **S2** | West volume −8.9% | A competitor promo **and** our own price rise start the same day, in the same channel, with no clean control | **COMPETING** — both stall at L1; proposes a ₹1.8L, 14-day price test |
| **S3** | New category, 19 days | Deliberately too short to fit seasonality | **INSUFFICIENT_HISTORY** — widens the band with a peer prior, refuses a cause |
| **S4** | WH-4 on-time delivery "improves" | Late-shipment rows silently stopped loading | **DATA_QUALITY** — flags the partial load *before* any business story |
| **S5** | RSM-North asks about West | — | **ENTITLEMENT_DENIED** at pre-flight; no rows, no narrative |
| **S6** | South modern-trade volume −18.9% | A competitor promotion hitting one channel only, so the other South channels are an untreated control, plus field reports | **CONFIRMED · L3** — the clean counterpart to S2; DiD −20.4pp, 11 corroborating documents, fires the counter-promotion lever |

---

## What none of the adjacent products do

Tellius, ThoughtSpot SpotIQ/Spotter, Anodot and the BI copilots all do automated
driver decomposition and generated narratives well. Four things here are genuinely
not in that set:

**1. A quantified mechanism ledger across KPIs.** Everyone stops at "the SLA failure
explains the revenue drop". This measures every hop of the declared chain against the
same untreated cohort, **each in its own lag-aligned window**:

```
On-Time Delivery %      -22.4pp   measured 08-07..08-20  (10-day declared lag)
  -> [Account service confidence — latent, not instrumented]
  -> 28-day Reorder Frequency  -0.7pp   only 41% of its trailing window is post-onset
  -> Order Volume        -14.9pp
  -> Net Revenue         -17.8pp
```
Because the cause acts with a lag, measuring it in the effect's window puts the shock
in the baseline and the hop reads as nothing. Aligning by the graph's declared lag is
what turns −1.9pp into the real −22.4pp. The ledger also names latent nodes rather than
skipping them, and flags when a trailing metric has not yet absorbed the effect.

**2. Evidence conflict, not just evidence count.** Corroboration counts documents that
agree. This also counts documents that point somewhere else, and a contested window
**cannot reach L2**:

| Scenario | on-theme | conflicting | ratio | verdict |
|---|---|---|---|---|
| S1 dispatch SLA | 12 | 0 | 0.00 | corroborated → L3 |
| S2 price rise | 2 | 10 | **0.83** | **contested — rung suppressed** |
| S6 competitor promo | 11 | 0 | 0.00 | corroborated → L3 |

**3. Suppression accounting.** Alert fatigue is treated as a measurable property:

```
python cli.py --sweep
  scanned 73 slices -> 24 material, 9 data-quality, 16 insufficient history, 24 suppressed
  suppressed because:
    persistence     20   did not persist for the required number of days
    statistical      8   inside the expected band once seasonality is removed
    pct_of_plan      4   movement is too small a share of the period plan
    abs_inr          1   below the rupee materiality floor in the contract
  grain-blocked: otd_pct x segment - measured at shipment grain, which does not carry 'segment'
```
The sweep also shrinks with entitlement: the analyst scans 73 slices, the North RSM 58.

**4. A false-alarm rate the engine reports about itself.**

```
python cli.py --backtest
  20 windows, 1 alert (rate 0.05), precision 1.0, recall 0.25
```
Twenty rolling windows across four quiet months, one alert, and it was the real event.
Recall is 0.25 and stays in the output: the SLA event spans four windows and only became
material in the last one. An engine that cannot report its own false-alarm rate is asking
to be trusted on faith.

---

## Grounded in the literature, where the literature actually says something

Two papers shaped the current build. They are cited for what they support and
nothing more.

### Data quality is a gate, not a caveat

Olszak & Bartuś, *AI-enhanced Business Intelligence for decision-making*, **Procedia
Computer Science 270 (2025) 415–425 (KES 2025)** — in-depth interviews across 20
organisations in services, trade and manufacturing. Their barrier ranking is
unambiguous:

| Barrier | Respondents |
|---|---|
| **Data availability and quality** | **20 / 20 — unanimous** |
| Integrating diverse sources, consistency and accuracy | 18 / 20 |
| High implementation cost | 15 / 20 |
| Shortage of AI/BI specialists | 12 / 20 |
| Integration with existing BI systems | 11 / 20 |
| Security and regulatory concerns | 10 / 20 |

If the only universal obstacle is data quality, it cannot live inside one detector.
`whylayer/fitness.py` runs **first** and can stop the analysis, assessing five
dimensions — availability, timeliness, completeness, consistency, validity —
across every source the run touches, and returning **FIT / FIT_WITH_CAVEATS / UNFIT**.

The consistency checks are cross-source, because source integration was their
second-ranked barrier: shipments referencing orders that do not exist, shipments
dated before their own order, CRM accounts missing from the account master.

It also found a real hole in my own earlier work. The original partial-load check
compared **total** dispatch rows, which cannot see a load that drops one *class* of
row. The WH-4 failure rows stopped arriving while total volume barely moved:

```
[critical] dispatch/WH-4  failure rows have almost stopped arriving: 0.8% late now
                          against 6.3% on the trailing baseline
                          -> a whole CLASS of row is missing, so this KPI will
                             appear to improve when nothing improved
```

### Speed is measured, not claimed

Their top-cited *benefit* was speed of decision-making (17/20). So the engine computes
it instead of asserting it — `decision_latency` reports when the cause began, when the
effect became visible, and the first date the engine could legitimately have fired
given the persistence its own materiality rule demands:

```
cause began 2026-08-05 -> effect visible 2026-08-17 -> engine could flag 2026-08-20
15 days cause-to-flag, 10 days before the window even closes
```

Where the movement is still building, it says so rather than flattering itself:
*"detection lands 3 days after this window closes — the movement was still building
when the period ended."*

### Decision rights become machine-checkable

Prasanth, Vadakkan, Surendran & Thomas, *Role of Artificial Intelligence and Business
Decision Making*, **IJACSA 14(6), 2023**, Fig. 4 — a taxonomy of human–AI decision
division (aggregated human-AI choice generation, full delegation, hybrid AI-human
sequential choice), with Trunk et al. on decision-making under uncertainty.

This is a narrative literature review with no algorithms, so it is used only for that
taxonomy. It closed a real gap: `decision_right: "RSM up to Rs 25L; above that CFO
approval"` was prose the engine could print but never check. `whylayer/delegation.py`
now **derives** the collaboration mode from evidence grade × value at risk against the
owner's authority limit × reversibility of the lever:

| Recommendation | Mode | Why |
|---|---|---|
| Counter-promotion (S6, ₹0.7L) | `AI_LED_HUMAN_APPROVES` | L3, reversible, inside authority |
| Service credit (S1, ₹44.6L) | `HUMAN_LED_AI_SUPPORTS` | L3, but **exceeds the RSM's ₹25L limit** |
| Price correction | `HUMAN_LED_AI_SUPPORTS` | hard to reverse — the machine may not single it out |
| Anything under abstention | `HUMAN_ONLY_AI_ABSTAINS` | no cause established |
| — | `AI_DELEGATED` | **reserved and deliberately never assigned** |

Every lever is externally visible to a customer, moves money, or both. Full delegation
exists in the taxonomy and is left empty on purpose, and a test asserts it stays empty.
That makes EU AI Act Art. 14 human oversight an inspectable property of each
recommendation rather than a policy sentence.

---

## Architecture

```
                    ┌── orders (DuckDB, order-line, hourly) ────┐
 heterogeneous      ├── dispatch (CSV, shipment, daily T+1) ────┤   ← deliberately stale
 sources            ├── interactions (JSONL, event, 15 min) ────┤   ← unstructured, PII
                    └── market_events (CSV, event, weekly) ─────┘

   semantic contract  ──►  entitlements  ──►  SEMANTIC ──► FITNESS ──► SIFT ──► SPLIT
   (definitions,           (row/column/       │
    lineage, thresholds,    domain, PII)      ├── fiscal calendar   (apr_mar -> period bounds)
    materiality, levers)                      ├── hierarchies       (region -> city)
                                              └── KPI reconciliation (finance vs operations)
                                                        │
                          ──► SIFT ──► SPLIT ──► SOURCE ──► SOLVE ──► NARRATE
   (definitions,           (row/column/       ↑          ↑          ↑          ↑         ↑
    lineage, thresholds,    domain, PII)   statistics  algebra   retrieval  playbook    LLM
    materiality, levers)                    + rules   + lattice  + causal   memory    + guard
                                                                  graph
                                                      ▲
                                          feedback loop ── analyst grades ── priors ── measured outcomes
```

| Stage | File | What it refuses to do |
|---|---|---|
| SEMANTIC | `whylayer/kpi_reconciliation.py` · `fiscal.py` · `hierarchy.py` | pick between competing KPI definitions when no rule says which wins |
| FITNESS | `whylayer/fitness.py` | analyse an estate that fails its quality gate |
| SIFT | `whylayer/sift.py` | wake anyone for a statistically real but immaterial move |
| PROPAGATE | `whylayer/propagation.py` | measure an upstream hop in its effect's window |
| SPLIT | `whylayer/split.py` | leave a residual — the identity closes to <₹1 |
| SOURCE | `whylayer/evidence.py` | award L3 when the placebo test fails |
| SOLVE | `whylayer/solve.py` | emit an action whose lever isn't in the contract |
| DELEGATE | `whylayer/delegation.py` | route any action to full machine delegation |
| NARRATE | `whylayer/narrate.py` | publish a number that isn't in the evidence packet |

### The numeric guard

The model receives a closed JSON **evidence packet** of already-computed facts, with no
tools and no data access. Afterwards every number in the generated text is parsed and
matched against that packet. An unverifiable figure fails the narrative, which is retried
once and then replaced by the deterministic narrator.

```python
numeric_guard("Revenue fell Rs 1.85 Cr, down 16.1%.", packet)   # (True,  [])
numeric_guard("Revenue fell Rs 4.2 Cr, margin lost 9.7%.", packet)  # (False, ['4.2','9.7'])
```

### Cost and latency

End-to-end **13–25 ms** per insight, offline. With narration on Haiku 4.5 a run is one
call, ~2.5k input / ~200 output tokens ≈ **₹0.30 per insight**. The expensive stages are
deliberately the cheap ones: retrieval is scoped to a single segment and window, so we read
hundreds of documents rather than a corpus.

---

## Entitlements are enforced on the data, never on the output

| Persona | Row | Column | Domain | Result on S1 |
|---|---|---|---|---|
| CFO | all | — | no CRM verbatims | L3 via counterfactual; 20 evidence items withheld |
| RSM North | `region = North` | no margin | — | **L2 only** — the row filter shrinks the control cohort, and the engine says so |
| Supply Chain | all | no ₹ at all | no verbatims | movement shown as % only; packet contains no rupee figure |
| Analyst | all | — | — | full method detail and lineage |

That RSM result is not a bug. A narrower entitlement produces genuinely weaker evidence,
and the engine raises an advisory rather than hiding it.

---

## Running it

```bash
./run.sh                                   # web UI on :8000
python cli.py --scenario S1 --persona cfo  # headless
python cli.py --all                        # every scenario x persona
python -m pytest tests/ -q                 # 125 tests against planted ground truth
python cli.py --sweep                      # estate triage with suppression accounting
python cli.py --backtest                   # false-alarm scorecard over history
python cli.py --reset                      # clear learned state (demos start clean)
python cli.py --ml                         # model card + holdout scorecard for the ranker
python cli.py --semantics                  # fiscal calendar, hierarchies, KPI definitions
python cli.py --scenario S1 --fiscal-period FY2027-Q1   # analyse a fiscal period
python data/generate.py                    # rebuild the estate (seeded, reproducible)

# retraining the ranker (a trained model ships in models/, so this is optional)
python data/generate_history.py                     # 5 years of episodes with known drivers
python cli.py --train-ranker --build-corpus         # replay, fit, calibrate, score
python cli.py --train-ranker --include-feedback     # fold in analyst-graded runs
export ANTHROPIC_API_KEY=sk-...            # optional: enables LLM narration + guard
```

### Tests

The suite scores the engine against `data/generated/ground_truth.json` — it asserts the
engine **recovered what we buried** and **refused what it should refuse**:

```
test_s1_reaches_the_counterfactual_rung        placebo validates before L3 is awarded
test_s1_recovers_the_three_planted_accounts    all three escalating accounts surface
test_s1_mechanism_lag_matches_the_declared_graph
test_s2_refuses_to_pick_a_single_cause
test_s3_declines_to_model_a_short_series
test_s4_blames_the_pipeline_not_the_business
test_rsm_cannot_analyse_another_region         no data returned on a denied request
test_supply_chain_never_sees_rupee_values      packet-level leak check
test_numeric_guard_rejects_invented_figures
test_causal_graph_blocks_impossible_mechanisms
```

---

## Platform mapping

Built custom on DuckDB so it runs anywhere with no account. The mapping is deliberate:

| Component | Prototype | Native equivalent |
|---|---|---|
| Governed metrics | `kpi_contract.yaml` | dbt Semantic Layer · Snowflake Semantic Views · Unity Catalog metrics |
| Query execution | DuckDB SQL | Snowflake / Databricks SQL / Fabric |
| Text retrieval | BM25 over JSONL | Cortex Search · Vector Search · Azure AI Search |
| Row/column security | `security.py` | Snowflake row-access + masking policies · Unity Catalog |
| Narration | Anthropic API | Cortex AISQL · Databricks Model Serving · Bedrock |

**We do not rebuild text-to-SQL or the semantic layer.** Cortex Analyst, Databricks Genie
and BigQuery Gemini were all GA by mid-2026; that layer is a platform feature now. The Why
Layer sits above it and owns what platforms don't: the standard of proof, the evidence
outside the warehouse, and the memory of what worked.

---

## Limitations

Stated plainly, because the whole thesis is about not overclaiming.

- **Synthetic data.** Effect sizes are recovered because they were planted. On real data
  the honest expectation is more L1/L2 and fewer L3s.
- **DiD needs a real control.** Where every unit is treated, the engine correctly returns
  L1 and abstains. This is common.
- **The causal graph is hand-curated.** That is the point — it encodes domain judgement —
  but it is also maintenance, and a wrong edge silently blocks a true cause.
- **BM25, not embeddings.** Fine at this scale, would need a vector index at production
  corpus sizes.
- **The playbook library is seeded with three entries.** Its value compounds only with use.
- **Two KPI definitions were wrong and had to be fixed.** `reorder_rate_28d` was first
  written as "share of accounts that reordered within 28 days", which saturates at ~1.0
  when accounts order weekly and can never move. The replacement divided by accounts
  active *in the current window*, which is survivorship-biased — an account going quiet
  leaves the denominator and the average goes **up** exactly when behaviour deteriorates.
  Both are recorded in `sources.py` because they are the kind of error that silently
  destroys a driver metric.
- **Plan is stated at region × month.** A materiality test on a narrower slice
  (region × channel) widens to the grain plan exists at and says so in
  `plan_basis.grain_note`. It does not invent a channel-level plan.
- **`otd_pct` cannot be cut by city, permanently.** That is a property of the dispatch
  feed, not a gap to close — the fix is a city key on shipments, in the source system.
- **Only two KPIs declare competing definitions.** The resolver handles N definitions and
  three resolution outcomes, but `net_revenue` and `order_volume` are the only KPIs with
  more than one declared today.
- **The fiscal calendar is month-aligned.** `start_month` + `year_label` covers Apr–Mar,
  Jan–Dec and Jul–Jun. It does **not** implement 4-4-5 or 52/53-week retail calendars,
  which are week-aligned and would need a different period model.
- **`city` is an attribute of the account, not a ship-to address.** One city per account
  for the life of the estate; real B2B accounts deliver to several.
- **The ranker is trained on simulated history.** 960 planted episodes, not this company's
  resolved incidents. It demonstrates that the layer learns, is calibrated, and can be
  evaluated on a temporal holdout. It is **not** evidence of accuracy on real episodes, and
  the model card says so in the UI rather than only here.
- **The learned gain is largest where the old rule was weakest.** Roughly 44% of the corpus
  is price or competitor episodes, which the heuristic ranks first exactly never, so the
  headline +29.6pp overstates what would be seen on a driver mix with fewer of them. The
  per-driver-type table above is the honest version.
- **Calibrated on 144 episodes.** Probabilities near 0 and 1 rest on few observations.
- **Causal ML is not built.** Double ML / causal forests for heterogeneous intervention
  effects — "this fix helps *this* cohort by *this* much" — is the next layer and is not
  here. Recommendation impact is still rescaled playbook history.
- **Learning is otherwise shallow.** Priors re-weight hypothesis ranking and outcomes
  re-estimate effect sizes; nothing is fine-tuned.
- **The numeric guard is magnitude-tolerant.** It matches within 1% to survive legitimate
  rounding, so a fabricated figure that lands within 1% of a real one can pass. Run
  `narrator = simulated LLM - hallucinated figures` in the UI: the invented ₹4.2 Cr is
  caught and the narrative falls back, while a 9.7% that sits within tolerance of a real
  9.64 is not. It is a strong filter on material fabrication, not a proof of correctness.

## Repository layout

```
config/     semantic contract · causal graph · entitlements · playbooks · LLM pricing
data/       generate.py — the estate with planted ground truth
            generate_history.py — 5 years of resolved episodes for training
whylayer/   contract · security · sources · fitness · sift · split · evidence
            · propagation · solve · delegation · narrate · triage · pipeline
            · feedback · telemetry
            fiscal.py — the fiscal calendar (apr_mar, jan_dec, jul_jun)
            hierarchy.py — region -> city roll-up, drill-down, aggregation semantics
            kpi_reconciliation.py — competing KPI definitions, resolved and audited
whylayer/ml/  features (the contract) · gbdt (numpy) · calibration · ranker
            · dataset · evaluate · train
models/     driver-ranker-v1.json — the trained model, as readable JSON
app/        FastAPI service + single-page UI (no CDN, no build step)
tests/      125 tests scored against ground truth
            (24 on the learned layer, 54 on the semantic layer)
docs/       business proposal
```

## Licence

MIT. Synthetic data only; no real customer data is included.
