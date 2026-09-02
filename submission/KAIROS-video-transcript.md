# KAIRÓS — prototype demo, narration script

**For voiceover.** Read at a normal pace, roughly 150 words per minute. Each scene's
on-screen hold is cut to the length of its narration, so a recording at that pace lines
up without further editing. Pause about half a second between scenes.

Total: **~430 words · ~2 min 55 s** including holds.

---

### 1 · Open  *(0:00)*

This is KAIRÓS — an engine that explains why a business metric moved, and refuses to
guess when it cannot tell. It sits on top of the BI stack a company already owns.

### 2 · The estate  *(0:12)*

Four data sources, three grains, four different refresh cadences. The header is live —
each feed is ageing in real time and counting down to its next refresh. The dispatch
feed is already overdue, and the engine says so before it says anything else.

### 3 · A material movement  *(0:29)*

North revenue is two point one two crore below expectation. That number cleared four
gates: a seasonality-aware band, a robust control limit, a rupee floor, and a
persistence test. Statistical significance alone was never enough.

### 4 · Whose definition  *(0:45)*

Before any of that, a question most tools skip. Finance and Operations define net
revenue differently — one deducts returns, the other also deducts shipping. Both are
correct inside their own system. KAIRÓS resolves the conflict first, records which
definition won and under whose authority, and reports the gap.

### 5 · Where, exactly  *(1:04)*

Revenue equals volume times price, so the movement decomposes exactly — volume, mix and
rate, with a residual under one rupee. No model, nothing unexplained.

### 6 · Why, and how sure  *(1:15)*

Now the standard of proof. Four rungs. Precedence lands a nine-day gap against a ten-day
declared lag. A difference-in-differences against an untreated cohort puts the effect at
minus twenty point six percent, with a clean placebo. Twelve corroborating documents,
zero conflicting. That reaches the top rung.

### 7 · The method ledger  *(1:36)*

Every step declares how it was produced and why. On this run, ninety-one to one hundred
percent of the reasoning is non-LLM — SQL, deterministic algebra, statistics, causal
inference and a learned ranker. The model phrases the answer. It never computes it.

### 8 · When it cannot tell  *(1:54)*

A different case. Two explanations start the same day in the same channel, and
eighty-three percent of the retrieved evidence points elsewhere. Neither clears the bar.
So KAIRÓS refuses — and prices the experiment that would settle it. One point eight
lakh, fourteen days, owner the CFO.

### 9 · Instrumentation first  *(2:14)*

On-time delivery appears to improve. It did not. The failure rows stopped loading, and
the fitness gate catches a class-level load failure that totals alone would hide.

### 10 · Entitlements  *(2:26)*

A regional manager asks about another region and is refused before any query runs. Supply
Chain is denied every rupee column — the evidence packet holds no currency figure at all,
so none can reach the model.

### 11 · The guard  *(2:40)*

And when a model does invent a figure, the guard catches it. Four point two crore is not
in the evidence packet. The narrative fails, retries, fails again, and the deterministic
version is published instead.

### 12 · Close  *(2:52)*

One hundred and twenty-five tests, scored against ground truth planted in the data.
Public repository, README and business proposal. It runs offline, with no API key.
