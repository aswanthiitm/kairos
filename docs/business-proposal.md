# KAIRÓS — Business Proposal

**Accenture Innovation Challenge 2026 · Round 2 · BusinessIntelligence.ai**

*Kairós* — the opportune moment. The distance between a number moving and someone
acting on it is where the value sits, and it is measured in days.

---

## 1. Problem framing

Enterprises have solved metric *detection* and left metric *explanation* unautomated.

A dashboard can say North revenue fell 18.8%. It cannot say the fall is three enterprise
accounts that stopped reordering after a warehouse missed its dispatch SLA ten days
earlier — because that sentence lives in service tickets, call transcripts and a dispatch
log that no BI tool reads. So the translation falls to an analyst, and it takes three to
five days.

Three structural failures keep it there.

**Noise blindness.** Anomaly tools alert on deviation, not consequence. Flag everything
and leaders mute you; flag on a fixed threshold and you miss the slow bleed that breaks
the plan.

**Correlation theatre.** "Insight" features surface what moved alongside a metric. A
leader cannot act on that — repricing, reallocating spend, re-staffing all assume a
direction of cause. Fluent generated prose makes a weak correlation read as settled.

**Organisational amnesia.** The company solved this pattern fourteen months ago and knows
which fix worked. That memory sits in a dead slide. Every recurrence is priced as new.

The measurable consequence is **decision latency**, not data latency. Pipelines refresh
hourly; the explanation refreshes weekly.

### Why this is not already solved

Roughly $1.1B has been deployed across four adjacent categories — BI copilots, warehouse-
native analysts, driver attribution and causal decision intelligence. Each solved a
different third. Everyone automated the **search**. Nobody built the **standard of
proof**, the **evidence outside the warehouse**, or the **memory of what worked**.

Empirically, the binding obstacle is elsewhere again. Olszak & Bartuś (*Procedia Computer
Science* 270, 2025) interviewed twenty organisations across three sectors: **data
availability and quality was the only barrier cited by every single respondent**, with
source integration second at 18 of 20 — ahead of cost, talent and regulation. KAIRÓS
treats that ranking as an instruction rather than a footnote.

---

## 2. Solution design

An **explanation layer that sits on top of the BI stack the client already owns.** It
does not replace Power BI, Tableau or the warehouse; it attaches to the existing semantic
layer and returns narratives into the tools people already open.

### The engine

Ten stages, each defined as much by what it refuses to do as by what it computes:
**SEMANTIC → FITNESS → DRIFT → SIFT → SPLIT → SOURCE → PROPAGATE → FORECAST → SOLVE →
NARRATE.**

### The four governors

**The Evidence Ladder** — Bradford Hill's viewpoints (1965) operationalised on Pearl's
causal hierarchy (CACM 2019). L0 co-movement · L1 precedence and dose–response · L2
independent corroboration · L3 counterfactual. **Nothing below L2 is ever phrased as a
cause.** Conflict is measured: evidence supporting a rival theme above a 0.4 ratio marks
a hypothesis *contested* and blocks L2.

**The Ambiguity Protocol** — when explanations tie, the engine returns *Confirmed /
Competing / Unknown*, shows the rivals side by side, and **prices the cheapest test that
separates them**. Showing rivals by default is a cognitive forcing function, shown to
reduce over-reliance on incorrect AI suggestions (Buçinca et al., CSCW 2021).

**The Numeric Guard** — every figure in generated text is parsed and matched against the
closed evidence packet. Unverifiable numbers fail the narrative.

**The Decision-Rights Router** — each recommendation is routed to a human–AI collaboration
mode derived from evidence × value at risk against the owner's authority limit ×
reversibility, following the human–AI decision-division taxonomy in Prasanth et al.
(*IJACSA* 14(6), 2023). Full delegation is reserved and never assigned.

### Division of labour with the model

The design follows the evidence: models are strong at knowledge-based causal *proposal*
(Kıcıman et al., TMLR 2024) and near-random at inferring causation *from correlation*
(Corr2Cause, ICLR 2024). So the model proposes and phrases; deterministic logic, SQL,
statistics, a learned ranker and causal inference decide and compute.

Enforced, not asserted: **91–100% of reasoning steps are non-LLM**, and the engine
produces a complete answer with the model switched off entirely.

---

## 3. Target users

| Persona | What changes | Delivery |
|---|---|---|
| **CFO / Finance** | Walks into review with cause, size, owner and a costed next step rather than four correlations | Monday exec brief |
| **Regional Sales Manager** | Named accounts and a specific action they own, not a regional average | CRM task + digest |
| **Supply Chain Lead** | The warehouse root cause in operational units — no commercial data | Ops standup board |
| **Data Analyst** | Stops being a lookup service; becomes curator of the causal graph and the playbooks | Analyst workbench |

The analyst is both the biggest winner and the quality control: they grade every
diagnosis, and that grade gates the rollout.

---

## 4. Business case and impact

Value comes from collapsing the distance between a number moving and someone acting.

| Lever | Mechanism | Basis in the prototype |
|---|---|---|
| Recovered leakage | Acting in week 1 rather than week 2 on service-driven churn | S1: ₹2.15 Cr movement, 87.6% attributable, playbook recovery rate 71% measured |
| Analyst capacity | "Why did X move" fire-drills automated | Each incident is 2–4 analyst days today |
| Avoided wrong actions | Abstention prevents intervening on the wrong cause | S2: two rivals, both L1 — a confident engine would have picked one |
| Compounding memory | Effect sizes re-estimated from realised outcomes | Playbook accuracy improves with each closed loop |

**Cost.** ≈450 ms end-to-end; ≈80 ms and zero tokens on a cache hit. One model call at
~2.5k in / ~200 out ≈ **₹0.30 per insight**. Retrieval is scoped to a single segment and
window, so evidence gathering stays affordable per insight rather than per corpus.

**Scale.** At 4.5M order rows — a hundred times the demo estate — the three queries the
engine actually runs complete in 1.9–3.2 ms.

**Success metrics — deliberately not insight volume**, which is the metric that
manufactures alert fatigue:
1. **Diagnosis precision** — analysts blind-grade narratives; this gates rollout.
2. **Decision latency** — days from movement to owned action.
3. **Recommendation calibration** — predicted against realised recovery.

**Defensibility.** The causal graph and the playbook library are built from the client's
own history. A competitor's generic model cannot copy them, and they strengthen with every
incident. Packaged per sector they become a reusable delivery accelerator rather than a
one-off build.

---

## 5. Phased roadmap

| Phase | Scope | Exit criterion |
|---|---|---|
| **1 — Prove precision** (0–3 mo) | One P&L metric, one function. SEMANTIC + FITNESS + SIFT + SPLIT on the existing semantic layer. Narratives reviewed, never auto-published. | Analyst-graded precision clears an agreed bar. **Precision is the only gate.** |
| **2 — Add evidence and memory** (3–6 mo) | Connect unstructured sources with entitlements and redaction. Seed playbooks from past incidents. Enable the Evidence Ladder and abstention. | ≥60% of material movements reach L2+; abstention rate stable and defensible |
| **3 — Close the loop** (6–12 mo) | Multi-KPI, multi-persona, outcome logging, the learned ranker trained on the client's resolved episodes. Recommendations remain **proposed**. | Recommendation calibration measurable; decision latency down |
| **4 — Productise** (12 mo+) | Industry-tuned graph and playbook packs as a delivery accelerator | Reuse across engagements |

---

## 6. Key risks and mitigations

| Risk | Mitigation — built in, not bolted on |
|---|---|
| **Confident but wrong** | L2 evidence floor; explicit *Unknown*; mandatory citation; numeric guard rejects unverifiable figures |
| **Spurious causes** | Curated causal graph blocks inadmissible mechanisms; placebo must validate before L3; automated search treated as exploratory, not confirmatory |
| **Poor data quality** | A fitness gate runs *first* across five dimensions and may halt the run. Partition- and class-level completeness catches a load that drops one kind of row while totals hold |
| **The ground moves** | Drift monitored separately for data and model; a ranker scoring outside its training support has its authority withdrawn for that run |
| **Definitional disputes** | Competing KPI definitions reconciled once, before analysis, with the resolution and the gap recorded |
| **Over-trust** | Rival hypotheses shown by default; actions stay *proposed*; full machine delegation reserved and never assigned |
| **Sensitive evidence** | Row, column and domain entitlements applied to data *before* prompt assembly; PII redacted pre-retrieval; withheld items surfaced as a count |
| **Regulatory** | EU AI Act Art. 12 post-hoc reconstruction satisfied by full lineage; Art. 14 human oversight by never auto-executing. India's DPDP obligations (May 2027) make purpose limitation on call data a design constraint |
| **Financial reporting exposure** | A narrative reaching a board pack sits inside ICFR scope, so SPLIT is deterministic arithmetic and narratives ship marked decision-support until a human validates |
| **Platform commoditisation** | We deliberately do not build text-to-SQL or a semantic layer — now free platform features. The moat is the standard of proof, the evidence outside the warehouse, and the memory |

---

## 7. What the prototype demonstrates

Against a synthetic estate with deliberately planted ground truth, KAIRÓS:

- **recovered** the buried service failure at L3 — DiD −20.6%, p<0.0001, placebo clean —
  and independently rediscovered the 10-day mechanism lag from the causal graph;
- **named** the three affected accounts and separated the 87.6% volume driver from the
  mix driver with an identity that closes to under ₹1;
- **abstained** where two explanations were confounded by design, and priced a ₹1.8L,
  14-day separating test instead of guessing;
- **refused** to model a 19-day-old series, falling back to a peer prior;
- **blamed the pipeline**, not the business, when late-shipment rows stopped loading;
- **reconciled** two competing definitions of net revenue and reported the gap;
- **denied** a regional manager a query outside their entitlement before any SQL ran, and
  produced a rupee-free narrative for a persona denied financial columns;
- **withdrew** its own learned ranker's authority when inputs fell outside its training
  support;
- **caught its own hallucination** — an injected ₹4.2 Cr figure failed the numeric guard
  and the deterministic narrative was published instead.

**125 automated tests** assert these outcomes against the ground-truth file.
