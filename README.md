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

---

## Architecture

```
                    ┌── orders (DuckDB, order-line, hourly) ────┐
 heterogeneous      ├── dispatch (CSV, shipment, daily T+1) ────┤   ← deliberately stale
 sources            ├── interactions (JSONL, event, 15 min) ────┤   ← unstructured, PII
                    └── market_events (CSV, event, weekly) ─────┘

   semantic contract  ──►  entitlements  ──►  SIFT ──► SPLIT ──► SOURCE ──► SOLVE ──► NARRATE
   (definitions,           (row/column/       ↑          ↑          ↑          ↑         ↑
    lineage, thresholds,    domain, PII)   statistics  algebra   retrieval  playbook    LLM
    materiality, levers)                    + rules   + lattice  + causal   memory    + guard
                                                                  graph
                                                      ▲
                                          feedback loop ── analyst grades ── priors ── measured outcomes
```

| Stage | File | What it refuses to do |
|---|---|---|
| SIFT | `whylayer/sift.py` | wake anyone for a statistically real but immaterial move |
| SPLIT | `whylayer/split.py` | leave a residual — the identity closes to <₹1 |
| SOURCE | `whylayer/evidence.py` | award L3 when the placebo test fails |
| SOLVE | `whylayer/solve.py` | emit an action whose lever isn't in the contract |
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
python -m pytest tests/ -q                 # 21 tests against planted ground truth
python data/generate.py                    # rebuild the estate (seeded, reproducible)
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
- **Learning is shallow.** Priors re-weight hypothesis ranking and outcomes re-estimate
  effect sizes; nothing is fine-tuned.
- **The numeric guard is magnitude-tolerant.** It matches within 1% to survive legitimate
  rounding, so a fabricated figure that lands within 1% of a real one can pass. Run
  `narrator = simulated LLM - hallucinated figures` in the UI: the invented ₹4.2 Cr is
  caught and the narrative falls back, while a 9.7% that sits within tolerance of a real
  9.64 is not. It is a strong filter on material fabrication, not a proof of correctness.

## Repository layout

```
config/     semantic contract · causal graph · entitlements · playbooks · LLM pricing
data/       generator with planted ground truth, DuckDB estate
whylayer/   contract · security · sources · sift · split · evidence · solve · narrate
            · pipeline · feedback · telemetry
app/        FastAPI service + single-page UI (no CDN, no build step)
tests/      21 tests scored against ground truth
docs/       business proposal
```

## Licence

MIT. Synthetic data only; no real customer data is included.
