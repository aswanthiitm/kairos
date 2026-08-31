# The Why Layer — Business Proposal
**Accenture Innovation Challenge 2026 · Round 2 · BusinessIntelligence.ai**

---

## 1. Problem framing

Enterprises have solved metric *detection* and left metric *explanation* unautomated.

A dashboard can say North revenue fell 8%. It cannot say the fall is three enterprise
accounts that stopped reordering after a warehouse missed its dispatch SLA — because that
sentence lives in service tickets, call transcripts and a dispatch log that no BI tool
reads. So the translation falls to an analyst, and it takes three to five days.

Three structural failures keep it there:

1. **Noise blindness.** Anomaly tools alert on deviation, not consequence. Flag everything
   and leaders mute you; flag on a fixed threshold and you miss the slow bleed that breaks
   the plan.
2. **Correlation theatre.** "Insight" features surface what moved alongside a metric.
   A leader cannot act on that — re-pricing, reallocating spend or re-staffing all assume
   a direction of cause. Fluent generated prose makes a weak correlation read as settled.
3. **Organisational amnesia.** The company solved this pattern fourteen months ago and
   knows which fix worked. That memory sits in a dead slide. Every recurrence is priced
   as new.

The measurable consequence is **decision latency**, not data latency. Pipelines refresh
hourly; the explanation refreshes weekly.

### Why now
Reasoning models can read messy operational text at scale; semantic layers make metric
definitions machine-interpretable; causal-inference tooling is production-grade. The
missing capability was never the mathematics — it was reading the notes and holding a
standard of proof.

### Why this is not already solved
Roughly **$1.1B** has been deployed across four adjacent categories — BI copilots
(Tableau Pulse, Power BI Copilot, ThoughtSpot Spotter), warehouse-native analysts (Cortex
Analyst, Databricks Genie, BigQuery Gemini — all GA by mid-2026), driver attribution
(Sisu, $128.7M, absorbed into Snowflake in 2023; Tellius; Anodot) and causal decision
intelligence (Alembic, Aera, causaLens, Quantexa). Each solved a different third.

Everyone automated the **search**. Nobody built the **standard of proof**, the **evidence
outside the warehouse**, or the **memory of what worked**. That is the gap this occupies.

---

## 2. Solution design

An **explanation layer that sits on top of the BI stack the client already owns.** It does
not replace Power BI, Tableau or the warehouse; it attaches to the existing semantic layer
and returns narratives into the tools people already open.

### The engine: SIFT → SPLIT → SOURCE → SOLVE → NARRATE

| Stage | Question | Method | Refuses to |
|---|---|---|---|
| **SIFT** | Is the change real, and does it matter? | Robust seasonal decomposition, MAD control limits, persistence, materiality gate, feed-completeness test | Wake anyone for a statistically real but immaterial move |
| **SPLIT** | Where exactly? | Price/volume/mix identity algebra + Adtributor-style lattice scan | Leave an unexplained residual |
| **SOURCE** | Why, and how sure? | Causal-graph admissibility → precedence → dose–response → corroborated retrieval → difference-in-differences with placebo | Award L3 when the placebo fails |
| **SOLVE** | What do we do? | Playbook memory matched on pattern signature, rescaled to value at risk | Emit an action whose lever isn't in the contract |
| **NARRATE** | How do we say it? | Persona-shaped generation behind a numeric guard | Publish a number absent from the evidence packet |

### The two governors

**The Evidence Ladder** — Bradford Hill's viewpoints (1965) operationalised on Pearl's
causal hierarchy (CACM 2019). L0 co-movement · L1 precedence + dose–response · L2
independent corroboration · L3 counterfactual. **Nothing below L2 is ever phrased as a
cause.**

**The Ambiguity Protocol** — when explanations tie, the engine returns
*Confirmed / Competing / Unknown*, shows the rivals side by side, and prices **the cheapest
test that separates them**. Ambiguity becomes a next step, not a hedge.

### Division of labour with the LLM

The design follows the evidence: models are strong at knowledge-based causal *proposal*
(Kıcıman et al., TMLR 2024) and near-random at inferring causation *from correlation*
(Corr2Cause, ICLR 2024). So the model proposes and phrases; deterministic logic, SQL,
statistics and causal inference decide and compute.

This is enforced at runtime, not asserted. The model receives a closed evidence packet
with no tools and no data access; afterwards every number in its output is parsed and
matched against that packet, and an unverifiable figure fails the narrative. Measured on
the prototype: **91–100% of reasoning steps are non-LLM**, and the engine still produces a
complete answer with the model switched off entirely.

---

## 3. Target users

| Persona | What changes | Delivery |
|---|---|---|
| **CFO / Finance** | Walks into review with cause, size, owner and a costed next step rather than four correlations | Monday exec brief |
| **Regional Sales Manager** | Named accounts and a specific action they own, not a regional average | CRM task + digest |
| **Supply Chain Lead** | The warehouse root cause, in operational units — no commercial data | Ops standup board |
| **Data Analyst** | Stops being a lookup service; becomes curator of the causal graph and playbooks | Analyst workbench |

The analyst is the biggest winner and the quality control: they grade every narrative, and
that grade gates the rollout.

---

## 4. Business case and impact

**Where the value comes from** — not "faster charts", but collapsing the distance between
a number moving and someone acting.

| Lever | Mechanism | Illustrative basis |
|---|---|---|
| Recovered leakage | Acting in week 1 rather than week 2 on service-driven churn | Prototype S1: ₹1.85 Cr movement, 80% attributable, playbook recovery rate 71% |
| Analyst capacity | "Why did X move" fire-drills automated | Each incident is 2–4 analyst days today |
| Avoided wrong actions | Abstention prevents intervening on the wrong cause | Prototype S2: two rivals, both L1 — a confident engine would have picked one |
| Compounding memory | Effect sizes re-estimated from realised outcomes | Playbook accuracy improves with each closed loop |

**Cost.** End-to-end 13–25 ms per insight; with narration on a small model, one call at
~2.5k input / ~200 output tokens ≈ **₹0.30 per insight**. Retrieval is scoped to a single
segment and window, so evidence gathering stays affordable per insight rather than per
corpus.

**Success metrics — deliberately not insight volume**, which is the metric that manufactures
alert fatigue:
1. **Diagnosis precision** — analysts blind-grade narratives; this gates rollout.
2. **Decision latency** — days from movement to owned action.
3. **Recommendation calibration** — predicted vs realised recovery.

**Why it is defensible.** The causal graph and playbook library are built from the client's
own history. A competitor's generic model cannot copy them and they strengthen with every
incident. Packaged per sector (retail, CPG, BFSI, telco) they become a reusable delivery
accelerator rather than a one-off build.

---

## 5. Phased roadmap

| Phase | Scope | Exit criterion |
|---|---|---|
| **1 — Prove precision** (0–3 mo) | One P&L metric, one function. SIFT + SPLIT on the existing semantic layer. Narratives reviewed, never auto-published. | Analyst-graded precision clears an agreed bar. **Precision is the only gate.** |
| **2 — Add evidence and memory** (3–6 mo) | Connect unstructured sources with entitlements and redaction. Seed playbooks from past incidents. Enable the Evidence Ladder and abstention. | ≥60% of material movements reach L2+; abstention rate is stable and defensible |
| **3 — Scale and close the loop** (6–12 mo) | Multi-KPI, multi-persona, outcome logging, self-improving effect sizes. Recommendations remain **proposed**, never auto-executed. | Recommendation calibration measurable; decision latency down |
| **4 — Productise** (12 mo+) | Industry-tuned graph and playbook packs as a delivery accelerator | Reuse across engagements |

---

## 6. Key risks and mitigations

| Risk | Mitigation — built in, not bolted on |
|---|---|
| **Confident but wrong** | L2 evidence floor; explicit *Unknown* state; mandatory citation on every clause; numeric guard rejects unverifiable figures |
| **Spurious causes** | Curated causal graph blocks inadmissible mechanisms; placebo test must validate before L3; automated search over many dimensions is treated as exploratory, not confirmatory (Gelman & Loken's forking paths) |
| **Over-trust / automation bias** | Rival hypotheses shown by default — a cognitive forcing function shown to reduce over-reliance (Buçinca et al., CSCW 2021); actions stay *proposed* |
| **Sensitive evidence** | Row, column and domain entitlements applied to the data *before* prompt assembly; PII redacted pre-retrieval; withheld items surfaced as a count, never silently dropped |
| **Regulatory** | EU AI Act Art. 12 post-hoc reconstruction is satisfied by full lineage; Art. 14 human oversight by never auto-executing. India's DPDP obligations (May 2027) make purpose limitation on call data a design constraint |
| **Financial reporting exposure** | A narrative that reaches a board pack sits inside ICFR scope, so SPLIT is deterministic arithmetic (audit-safe) and narratives ship marked decision-support until a human validates |
| **Messy client text** | Unstructured sources are Phase 2; the engine delivers on SIFT + SPLIT alone until then; where text is absent the honest output is *Unknown* plus the cheapest test |
| **Platform commoditisation** | We deliberately do **not** build text-to-SQL or a semantic layer — those are now free platform features. The moat is the standard of proof, the evidence outside the warehouse, and the memory |

---

## 7. What the prototype proves

Running against a synthetic estate with deliberately planted ground truth, the engine:

- **recovered** the buried service failure at L3 — DiD −13.4%, p<0.0001, placebo clean —
  and independently rediscovered the 10-day mechanism lag it was given in the causal graph;
- **named** all three affected accounts and separated the 80% volume driver from the 10%
  mix driver with an identity that closes to under ₹1;
- **abstained** where two explanations were confounded by design, and priced a ₹1.8L,
  14-day separating test instead of guessing;
- **refused** to model a 19-day-old series, falling back to a peer prior;
- **blamed the pipeline**, not the business, when late shipment rows stopped loading;
- **denied** a regional manager a query outside their entitlement before any SQL ran, and
  produced a rupee-free narrative for a persona denied financial columns;
- **caught its own hallucination** — an injected ₹4.2 Cr figure failed the numeric guard
  and the engine published the deterministic narrative instead.

21 automated tests assert these outcomes against the ground-truth file.
