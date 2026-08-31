# Technical Dossier — MemRL: Self-Evolving Agents via Runtime Reinforcement Learning on Episodic Memory

**Source**: `/Users/liuzhanyu/workspace/RLProject/docs/_extract/MemRL.txt` (41 pages, full text incl. appendices A–I)
**Venue/status**: Preprint, February 13, 2026. arXiv:2601.03192v2 [cs.CL], 12 Feb 2026.
**Authors**: Shengtao Zhang¹\*, Jiaqian Wang²\*, Ruiwen Zhou³, Junwei Liao¹⁴, Yuchen Feng⁵, Zhuo Li¹, Yujie Zheng¹, Weinan Zhang¹⁴, Ying Wen¹⁴, Zhiyu Li⁵, Feiyu Xiong⁵, Yutao Qi², Bo Tang⁶⁵, Muning Wen¹ (¹SJTU, ²Xidian, ³NUS, ⁴Shanghai Innovation Institute, ⁵MemTensor Shanghai, ⁶USTC). Correspondence: tangb@memtensor.cn, muningwen@sjtu.edu.cn.
**Code**: https://github.com/MemTensor/MemRL

---

## 1. Problem formulation

### 1.1 "Runtime Continuous Learning"

The paper's driving question (p1): *"How can we enable an agent to continuously improve its performance after deployment, without compromising the stability of its pre-trained backbone?"* The target regime is named **Runtime Continuous Learning** — "an agent that evolves with continued usage and rapidly adapts to new tasks after deployment ... all while keeping the backbone model frozen to prevent catastrophic forgetting" (p1). Related-work framing (p2): "Runtime Learning focuses on the post-deployment improvement of agents through interaction streams rather than offline data, marking a shift toward the 'era of experience'." It is explicitly distinguished from Continual Learning and Test-Time Adaptation, "which typically update parameters."

### 1.2 Formal setting: Memory-Based MDP (M-MDP)

Adopted from Memento (Zhou et al., 2025a). Tuple `(S, A, P, R, γ, M)` with `M = (S × A × R)*` the "evolving memory bank of past experiences." The joint policy factorizes (Eq. 1, p3):

```
π(a_t | s_t, M_t) = Σ_{m ∈ M_t}  μ(m | s_t, M_t) · p_LLM(a_t | s_t, m)
```

- `μ(m | s_t, M_t)` — **Retrieval Policy** (the plastic, learned object)
- `p_LLM(a_t | s_t, m)` — **Inference Policy**, "parameterized by a frozen LLM"

State `s` is instantiated as the **User Intent**, "encapsulated by the current query embedding." The action space `A_t` is "dynamic and discrete, corresponding to selecting a specific `m` from the memory bank `M_t`." `Q(s, m)` is defined as "the expected utility of the subsequent action `a` augmented by memory `m`", and the optimum is (Eq. 2, p3): `μ*(m|s,M) = argmax_{m∈M} Q(s,m)`.

### 1.3 Frozen vs. plastic

| Frozen | Plastic |
|---|---|
| LLM backbone weights (`p_LLM`) | Memory bank contents `{(z_i, e_i)}` (append-only) |
| Embedding model (Text-Embedding-3-Large) | Scalar utilities `Q_i` |
| Evaluator/verifier criteria (assumption 1, p13) | Induced retrieval policy `μ` |
| All hyperparameters (α, λ, δ, k₁, k₂) | — |

Zero gradient steps are taken anywhere. The "RL update ... is an O(1) operation" on scalars (p24).

### 1.4 Feedback signal — this is the load-bearing assumption

MemRL requires a **verifiable, ground-truth-grounded scalar reward on every task it learns from**, at runtime:

- p5: "Executing `a` then yields an environmental reward `r` (e.g., **execution success or scalar score**)."
- p13: "receives a scalar reward `r_t ∈ [−1,1]` indicating task success or failure."
- Per benchmark, `r` comes from: BigCodeBench unit-test execution (`[EVAL] {"status": ..., "error": ...}`, p34); ALFWorld simulator task completion; LifelongAgentBench OS/DB environment verifiers; **HLE exact-match / multiple-choice grading against the answer key** (prompt templates p37 show `Exact Answer:` / `Answer:` formats and a reflection note stating `Result: {CORRECT|INCORRECT}`).

It is **not** self-judged, and there is no LLM-as-judge fallback. Consequently, MemRL as evaluated **consumes ground-truth labels on the stream it is learning from**. On three of five settings a held-out transfer split exists (Table 9); on HLE it does not.

### 1.5 Online/streaming vs. offline — and do tasks repeat?

Despite the "runtime/streaming" framing, the evaluated protocol is **repeated offline passes over a fixed task set**, and the theory assumes exactly that:

- p5/p13: "We posit two standard assumptions: a **frozen inference policy** `p_LLM` and a **stationary task distribution**." And: "We analyze the learning process on **a fixed dataset** ... Tasks `s` are drawn from a stationary distribution over a fixed dataset."
- Main results are "over **10 epochs**" (Table 1 caption) reported as **Last Epoch SR / Cumulative SR**, where CSR = "the proportion of tasks solved **at least once across epochs**" (p5).

**Tasks repeat, by construction, ten times, with ground-truth reward available on each repetition.** Data partitioning (Table 9, p23):

| Benchmark | Runtime Learning | Transfer Learning | Split |
|---|---|---|---|
| HLE | 2,500 tasks | — | **Full set (Runtime only)** |
| Lifelong Agent (OS) | 500 | 500 | 7:3, seed 42 |
| Lifelong Agent (DB) | 500 | 500 | 7:3, seed 42 |
| BigCodeBench-I (Full) | 1,140 | 1,140 | 7:3, seed 42 |
| ALFWorld | 3,553 | 140 | `valid seen` — "uses the same 3,553 tasks as memory context but is evaluated on 140 **novel instances of seen task types**" |

**Explicit verdict on test-time labels.** For HLE — the headline "Knowledge Frontier" result and the entire cross-model-transfer study (Table 5) — there is **no train/test separation at all**. MemRL sees each of the 2,500 HLE questions ten times, is told CORRECT/INCORRECT after each attempt, writes the full solution trajectory into memory on success, and is scored on those same 2,500 questions. The authors say so plainly (p19): "the gain in HLE stems from **Runtime Memorization**. Since HLE questions are distinct and domain-specific, the agent relies on the Runtime Learning phase to 'memorize' specific solutions to difficult problems through repeated exposure." This is a legitimate description of an intra-episode self-improvement loop, but it is *not* evidence of generalization, and the CSR metric (solved at least once in 10 tries) is definitionally a pass@10 quantity.

### 1.6 Stability–plasticity framing

Motivated by complementary-learning-systems / adaptive-resonance theory (Grossberg 2013; McClelland 1995; Kumaran 2016). Stability = frozen cortex-analogue (LLM weights); plasticity = hippocampus-analogue (episodic memory + utilities). Operationalized metric: **Forgetting Rate** `FR = N_lost / N_fail`, where `N_lost` = tasks transitioning from previous success to current failure, `N_fail` = total failures in the current epoch (p8). MemRL 0.041 vs MemP 0.051 vs MemRL-w/o-Norm&SimGate 0.073.

---

## 2. Memory representation

### 2.1 Nominal schema (Eq. 5, p4)

```
M = { (z_i, e_i, Q_i) }_{i=1}^{|M|}
```
- `z_i` — **Intent** (the task/query text; stored as an embedding via Text-Embedding-3-Large for retrieval)
- `e_i` — **Experience**, "the raw Experience (e.g., a successful solution trace or trajectory)"
- `Q_i` — **Utility**, "approximates the expected return of applying experience `e_i` to intents similar to `z_i`, serving as the **critic** in RL"

Figure 1's conceptual diagram uses richer vocabulary (intent / strategy / episode / expected outcome / utility score) but the formal object is the 3-tuple. Note: there is **one scalar `Q_i` per memory entry**, not per `(intent, memory)` pair — a point the theory itself concedes (§4.4/A.3, Eq. 14: `Q(m) = Σ_{s∈S(m)} E[r|s,m] Pr(s|m)`).

### 2.2 Actual stored content (Appendix I, pp. 31–36)

Two memory *types*, uniform across all four benchmarks:

**`SUCCESS_PROCEDURE`** (template p32):
```
Task: {task_description}
SCRIPT:
{script}
TRAJECTORY:
{trajectory}
```
where `{script}` is produced by a fixed summarization prompt: "create a concise, high-level script that captures the essential steps and decision points. The script should be: 1. Generic enough to apply to similar tasks 2. Specific enough to provide useful guidance 3. **3-5 high-level steps maximum** 4. Focus on the strategy and key decisions, not detailed actions."

**`FAILURE_REFLECTION`** (template p32):
```
TASK REFLECTION:
Task: {task_description}
What went wrong:
{reflection}
Failed approach:
{failed_trajectory}
```
generated by: "This task failed. Analyze what went wrong and suggest improvements for future similar tasks. Focus on: 1. Incorrect assumptions 2. Steps to improve 3. What to avoid next time." In practice the model emits a three-slot structure visible in the case studies: `ROOT CAUSE` / `PATTERN TO AVOID` / `CORRECT APPROACH` (pp. 26–31).

**Undocumented counters.** The Appendix H case studies report per-entry statistics not present in Eq. 5: "Utility. **selected=19, success=19 (100.0%)**" (CS1, p26); CS3 `selected=48, success=44 (91.7%)`; CS5 `selected=94, success=88 (93.6%)`; CS6 `selected=59, success=55 (93.2%)`. So selection and success counts exist in the implementation but are **not used in the scoring function** — there is no count-based bonus, no UCB, no visit-count decay.

### 2.3 Write path, granularity, growth

- **Granularity: one new triplet per sampled trajectory (per task attempt), not per step and not per strategy.** p5: "for each sampled trajectory, we use an LLM to summarize the experience (Fang et al., 2025), and write it back into the memory bank as a new triplet `(z, e_new, Q_init)`, enabling continual expansion of experience while keeping the LLM parameters unchanged."
- **Both successes and failures are written.** Retrieved context is injected as two labelled blocks: `=== Successful Memories ===` and `=== Failed Memories (for caution) ===` (HLE, p37); `--- SUCCESSFUL MEMORIES (Examples to follow) ---` / `--- FAILED MEMORIES (Examples to avoid or learn from) ---` (ALFWorld, p38).
- **Initialization:** `Q_init = 0.0` for every benchmark (Table 8, p22).
- **Growth:** monotone and unbounded within a run. With one write per attempt, HLE's 10 epochs × 2,500 tasks admits up to ~25,000 entries. **The paper never reports memory-bank size, growth curves, or storage footprint.**
- **Pruning: none.** No eviction, decay, deduplication, or consolidation is implemented. It is deferred twice to future work: "combining these with **periodic consolidation** of similar intentions and experiences within the memory bank could significantly improve the **spatial efficiency** of the memory, reducing redundancy" (p25); and reactively for security: "once contamination or attack is identified, polluted experiences **can be swiftly pruned**" (p25).
- The `δ` similarity gate is the only mechanism that keeps a growing bank from flooding the context, and it is **fixed offline** — the top-20% quantile of the benchmark's initial pairwise-similarity distribution (p22).

---

## 3. Two-Phase Retrieval

### Phase A — Similarity-Based Recall (Eq. 6, p5)

```
C(s) = TopK_{k1} ( { i | sim(Emb(s), Emb(z_i)) > δ } )
```
- `Emb` = **Text-Embedding-3-Large** (Table 7, p22), "Used for Intent and Query encoding"; `sim` = cosine similarity.
- `δ` is a hard gate ("sparsity threshold"), **adaptive per dataset but static in time**: "we determine `δ` by calculating the pairwise cosine similarity distribution of task descriptions within each benchmark and selecting the threshold at the **top 20% quantile**" (p22).
- **Fallback / only real exploration primitive:** "If `C(s) = ∅`, MEMRL relies solely on the frozen LLM for exploration" (p5).
- In the theory, Phase A defines the **effective support set** `S(m) ≜ {s ∈ S | sim(s, z_m) ≥ τ_A}` (p13) and the KL **semantic trust region** (§A.4.2), whose stated purposes are "(1) Trust Region: it constrains the policy to the support set `S`, preventing the agent from retrieving high-Q but semantically irrelevant memories (out-of-distribution errors); (2) Regularization: it stabilizes the learning dynamics during the 'cold start' phase when Q-estimates are noisy" (p16).

### Phase B — Value-Aware Selection (Eq. 7, p5)

```
score(s, z_i, e_i) = (1 − λ) · ŝim(Emb(s), Emb(z_i))  +  λ · Q̂_i
```
Top-`k₂` items by `score` form the injected context `M_ctx(s)`. `·̂` denotes **z-score normalization** (over the Phase-A candidate pool); `λ ∈ [0,1]` "modulates the trade-off." The paper labels similarity the "exploration" term and Q the "exploitation" term — an unconventional and, in my reading, incorrect use of the terms: neither is stochastic, and nothing in Eq. 7 is directed at reducing uncertainty. **There is no UCB term, no Thompson sampling, no ε-greedy, no optimistic initialization** (`Q_init = 0.0` with observed Q in [0,1]).

### Hyperparameters (Table 8, p22)

| Parameter | BigCodeBench | LLB (OS) | LLB (DB) | ALFWorld | HLE |
|---|---|---|---|---|---|
| `α` learning rate (Eq. 4) | 0.3 | 0.3 | 0.3 | 0.3 | 0.3 |
| `λ` Q-weight (Eq. 7) | 0.5 | 0.5 | 0.5 | 0.5 | 0.5 |
| `δ` similarity threshold | 0.38 | 0.50 | 0.37 | 0.62 | 0.25 |
| `k₁` Phase-A recall | 10 | 10 | 10 | 5 | 5 |
| `k₂` Phase-B select | 5 | 5 | 5 | 3 | 3 |
| `Q_init` | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| Baselines' top-k (RAG / Self-RAG / Mem0 / MemP) | 5 | 5 | 5 | 3 | 3 |

Backbones (Table 7): GPT-4o (BCB), GPT-4o-mini (Lifelong Bench), GPT-5-mini (ALFWorld), Gemini-3-pro (HLE). Temperature = 0.0 everywhere except HLE (Gemini-3-pro) at 0.6; top-p = 1.0.

### The claimed optimality of Eq. 7 (§A.4.3, pp. 16–17)

The E-step of the variational derivation solves
```
max_μ  Σ_m μ(m|s) Q(s,m) − (1/β) D_KL( μ(·|s) ‖ π_sim(·|s) )
```
yielding the Boltzmann policy `μ*(m|s) = π_sim(m|s) exp(βQ(s,m)) / Z(s)`, hence `log μ*(m|s) ∝ log π_sim(m|s) + β Q_t(s,m)`. The paper then asserts: "This proves that our heuristic combination of similarity and Q-value is **mathematically equivalent** to the optimal policy under the variational objective" (p17).

**This claim is overstated.** The derived optimum is a *softmax sampling distribution* over `log π_sim + βQ`; the implementation is a *deterministic hard top-k₂* over `(1−λ)·z-score(cos-sim) + λ·z-score(Q)`. Raw cosine similarity is not `log π_sim`, z-scoring is not the identity, and argmax-top-k is not Boltzmann sampling (it is the β→∞ limit, which also destroys the trust-region regularization the KL term was introduced to provide). The functional form is *analogous*, not equivalent. Note also two notation collisions: `λ` is the Q-weight in Eq. 7 and the Lagrange multiplier in §A.4.3; `β` is the KL inverse-temperature in Eq. 20 and the true mean reward `β(s,m)` in Eq. 10.

---

## 4. The "RL" part

### 4.1 Two update rules are presented; one is used

**Eq. 3 (p4) — TD / Q-learning form, presented but never used:**
```
Q(s, m) ← Q(s, m) + α [ r + γ max Q(s′, m′) − Q(s, m) ]
```

**Eq. 4 (p4) — Monte Carlo style, the rule actually implemented:**
```
Q_new ← Q_old + α ( r − Q_old )
```
equivalently `Q_{t+1} = (1−α) Q_t + α r_t` (§A.2, p14), which the appendix itself names the **"exponential moving average rule"** (p13) and the **"linear EMA rule"** (p14).

Justification for the reduction (p4): "Equation 4 performs as a naturally simplified version of Equation 3 **by setting `s′` as a terminal state** to balance complexity and performance, sharing a similar **one-step MDP** formulation with Guo et al. (2025) [DeepSeek-R1]."

- **Learning rate:** `α = 0.3` for all five settings.
- **`γ` is never assigned a value** — it is irrelevant, since the bootstrap term is deleted.

### 4.2 In precisely what sense is this RL?

Honest characterization: **MemRL is a contextual multi-armed bandit with a deterministic top-k arm-selection rule and constant-step-size EMA value estimation.** Specifically:

- **Not TD learning.** No bootstrapping is performed. There is no successor state, no value propagation, no Bellman backup. Eq. 3 is decorative.
- **Not incremental Monte Carlo averaging** either, despite the "Monte Carlo style" label. True incremental MC uses `α_n = 1/n` and converges to the sample mean; MemRL uses constant `α = 0.3`, i.e. a recency-weighted EMA with effective window `≈ 1/α ≈ 3.3` samples. This is a deliberate, defensible choice for non-stationary targets — but it means that after 10 epochs (≤10 updates per entry), `Q_i` is essentially a weighted average of that entry's **last ~3 outcomes**. `(1−α)^t = 0.7^10 ≈ 0.028`, so initialization has washed out, but the estimator's asymptotic variance floor is `α/(2−α)·σ² = 0.3/1.7·σ² ≈ 0.176 σ²` — with binary rewards, `σ² ≤ 0.25`, so the standard deviation floor is ≈0.21 on a [0,1] scale. That is large relative to the Q-bin widths (0.1) used in the Figure 7 analysis.
- **It is genuinely RL-flavored** in two respects: the objective is expected return under a learned selection policy rather than a supervised likelihood, and the improvement loop is closed through environment interaction (act → reward → value update → better selection). The M-MDP formalization is coherent.
- **What is missing to be RL proper:** sequential credit assignment, temporal bootstrapping, an exploration policy with any coverage guarantee, and per-state value resolution (`Q` is stored per-`m`, marginalizing over the whole support set `S(m)`).

### 4.3 Reward definition and credit assignment

- Reward: `r_t ∈ [−1,1]`, "indicating task success or failure" (p13). Empirically the reward appears to be in `{0,1}`: observed Q-bins run 0.2→1.0 (Figure 7) and Figure 3 shows Q = 0.92 / 0.95 / 0.82. A [−1,1] range with `Q_init = 0` would produce negative Q values, which never appear. Minor internal inconsistency.
- **Credit assignment: uniform terminal-reward broadcast.** "For the memories **actually injected into the input context** `M_ctx(s)`, we update their utilities in triplets with a Monte Carlo style rule" (p5). All `k₂` retrieved entries receive the identical scalar `r`. No per-step attribution, no discounting, no advantage baseline, no counterfactual weighting.
- For multi-step tasks the paper describes this as backward propagation of a single terminal signal: "By propagating the final reward backward to the memory utility `Q`, MEMRL effectively learns to verify the **whole trajectory**" (p18) — i.e. episode-level, not step-level, credit.
- The authors name this as their #2 limitation: "**Credit-assignment ambiguity** during utility updates, especially with multiple referenced experiences, raises the need for more precise attribution methods like Shapley methods or value decomposition in multi-agent RL" (p8).

### 4.4 Exploration mechanism

There is effectively none. The three things that function as exploration:
1. **Empty-candidate fallback** — `C(s) = ∅` ⇒ pure frozen-LLM generation (p5).
2. **LLM sampling stochasticity** — temperature 0.6 on HLE only; 0.0 (greedy) elsewhere.
3. **The similarity term in Eq. 7**, which the paper calls "exploration" because it can surface a lower-Q entry.

Consequence: a memory that is never selected keeps `Q = Q_init = 0.0` indefinitely. Theorem A.1's premise — "the pair `(s,m)` is updated **infinitely often**" (p13) — is not satisfied by a greedy top-k policy over a monotonically growing bank. Newly written entries enter at `Q = 0.0`, which under z-score normalization typically ranks them below already-reinforced successes, creating a cold-start starvation dynamic that the paper does not measure.

### 4.5 Theory summary

| Result | Statement | Location |
|---|---|---|
| Learning target | `E[r_t \| s_t=s, m_t=m] = β(s,m)`, well-defined under frozen `p_LLM` + fixed task distribution | Eq. 10, p13 |
| Unbiasedness | `lim_{t→∞} E[Q_t] = β(s,m)` | Thm A.1 / Eq. 11, p13 |
| Convergence rate | `E[Q_t] − β(s,m) = (1−α)^t (Q_0 − β(s,m))` — exponential | Eq. 12, p13 |
| Finite-time variance | `v_t = (1−α)^{2t} v_0 + α²σ² Σ_{k=0}^{t−1}(1−α)^{2k}` | Eq. 17, p15 |
| Variance bound | `limsup Var(Q_t) = α²σ² / α(2−α) = α/(2−α) · σ²` | Eq. 18, p15 |
| Global target | `lim E[Q_t(m)] = Σ_{s∈S(m)} E[r\|s,m] Pr(s\|m)` | Eq. 14/19, pp. 14, 16 |
| Retrieval-drift fix | GEM coordinate ascent on `J(μ,Q)`; E-step = Phase-B ranking (policy improvement), M-step = Eq. 4 (value update, an SGD step on `½(r−Q)²`); Monotonic Improvement Theorem ⇒ `μ_{t+1} ≈ μ_t` ⇒ `Pr(s\|m)` time-invariant | §A.4, pp. 16–17 |

The GEM argument is the interesting move, because it is the only place the paper confronts the real hazard: `Pr(s|m)` is a latent distribution that shifts as Q-values change, "creating a circular dependency that threatens stability" (p16). The resolution is a genuine EM-style monotonic-improvement appeal — but it inherits the stationary-task-distribution assumption, which is precisely what a deployed *runtime* agent violates. The convergence guarantee therefore applies to the 10-epoch fixed-dataset protocol that was measured, not to the streaming deployment that motivates the paper.

---

## 5. "Constructive episodic simulation"

**Finding: there is no mental-simulation or rollout module in MemRL. The phrase is a cognitive-science metaphor in the introduction and Figure 1, with no corresponding operator in Section 4.**

Evidence:
- p1: "Human intelligence balances cognitive stability and episodic plasticity via **Constructive Episodic Simulation**, enabling adaptation without rewiring neural circuitry (Schacter & Addis, 2007; Hassabis & Maguire, 2007)." Cited five times as *inspiration* ("inspired by the human cognitive mechanism of constructive simulation").
- Figure 1's annotation strip contains the literal strings "constructive episodic simulation", "mental simulation / rollout", "estimate expected outcome", "update expected outcome" (p1 text dump, lines around the figure).
- **Section 4 enumerates exactly three components**: "(i) a structured **Intent-Experience-Utility** memory bank, (ii) a **Two-Phase Retrieval** mechanism that decouples semantic recall from value-aware selection, and (iii) a **Runtime Utility Update** rule that stabilizes Q-value estimation" (p4). No fourth simulation component.

The two mechanisms that stand in for "simulation":
1. **`Q_i` as the "expected outcome" estimate.** Retrieving high-Q memories is cast as pre-playing the anticipated result of a strategy without executing it. Cost: a scalar lookup; "The Q-value update in a Monte Carlo style on scalars, which is an **O(1)** operation" (p24).
2. **Post-hoc LLM abstraction of episodes** (the "3-5 high-level steps" script and the failure reflection). This is a *retrospective* summarization call, one per trajectory, not a forward rollout. It is the "constructive" (reconstructive) half of the analogy.

**Cost at inference.** No extra rollouts, no simulated environment steps, no tree search. Appendix F: "the average total token consumption per question for MEMRL is approximately **32K**" on HLE, "comparable to that of MemP, as both methods utilize identical interaction loops (reasoning + summarization) ... the significant performance gains of MEMRL reported in the main text are achieved **without increasing the inference budget**" (p24). Retrieval overhead: "basic vector dot-products and scalar score weighting, operating in **milliseconds**" (p24). Wall-clock per HLE epoch (2,500 questions) sits in the ~10–70 h band (Figure 11), dominated by API latency; MemP shows spikes at epochs 4 and 6 attributed to "API network latency and throughput variability, rather than algorithmic complexity."

---

## 6. Experiments

### 6.1 Baselines (Appendix D.1, pp. 20–21) — the complete list

Three families, all "evaluated under a **unified frozen-backbone setting**":

1. **Test-Time Scaling** — **Pass@k** (k=10 in tables): "generates k independent candidate solutions ... and selects the best one based on the benchmark's verifier."
2. **RAG** — **RAG** (Lewis et al., 2020), standard top-k semantic retrieval; **Self-RAG** (Asai et al., 2023), "selective retrieval and uses a critique model (or self-prompting) to verify the relevance and factual correctness of the retrieved content."
3. **Agentic Memory** — **Mem0** (Chhikara et al., 2025), structured add/retrieve/update APIs; **MemP** (Fang et al., 2025), procedural memory with a "build-retrieve-update cycle," recalling "high-level plans rather than raw trajectory data." MemP is the strongest baseline throughout.
4. **No Memory** — frozen backbone, zero-shot.

**Not compared against:** ExpeL, Reflexion (only *approximated* as the "Single-Task Reflection" ablation in Table 3: "conceptually equivalent to Reflexion (Shinn et al., 2023)"), AWM/Agent Workflow Memory, Voyager, A-Mem, Memento, Evo-Memory, or **any fine-tuning / parametric-RL baseline** (SFT, DPO, GRPO, LoRA). The absence of a fine-tuning comparison is notable given that catastrophic forgetting and the cost of weight updates are the paper's central motivation.

### 6.2 Table 1 — Runtime Learning, 10 epochs (Last Epoch SR / CSR), p7

| Method | BigCodeBench Code Gen | LLB OS Task | LLB DB Task | ALFWorld Exploration | HLE Knowledge Frontier | Average |
|---|---|---|---|---|---|---|
| *Backbone* | *GPT-4o* | *GPT-4o-mini* | *GPT-4o-mini* | *GPT-5-mini* | *Gemini-3-pro* | — |
| No Memory | 0.485 | 0.674 | 0.860 | 0.777 | 0.357 | 0.631 |
| Pass@10 | – / 0.577 | – / 0.756 | – / 0.928 | – / 0.928 | – / 0.524 | – / 0.743 |
| RAG | 0.475 / 0.483 | 0.690 / 0.700 | 0.914 / 0.916 | 0.887 / 0.930 | 0.430 / 0.475 | 0.679 / 0.699 |
| Self-RAG | 0.497 / 0.561 | 0.646 / 0.732 | 0.891 / 0.898 | 0.907 / 0.962 | 0.423 / 0.475 | 0.673 / 0.726 |
| Mem0 | 0.487 / 0.495 | 0.670 / 0.702 | 0.920 / 0.926 | 0.894 / 0.969 | 0.436 / 0.470 | 0.681 / 0.712 |
| MemP | 0.578 / 0.602 | 0.736 / 0.742 | **0.960** / 0.966 | 0.885 / 0.919 | 0.522 / 0.570 | 0.736 / 0.760 |
| **MemRL (ours)** | **0.595 / 0.627** | **0.788 / 0.804** | **0.960 / 0.972** | **0.949 / 0.981** | **0.570 / 0.606** | **0.772 / 0.798** |

Headline: "+3.8% in Cumulative Success Rate" over MemP on average; "+6.2%" on both ALFWorld and OS; "+3.6%" on HLE (p6).

**Critical reading.** The right baseline for a CSR metric ("solved at least once across 10 epochs") is **Pass@10**, not No Memory — both spend 10 attempts per task. On that comparison the average margin is `0.798 − 0.743 = +5.5 pp`, not the `0.798 − 0.631 = +16.7 pp` implied by contrasting with No Memory. On DB, Pass@10 (0.928) already beats RAG (0.916), Self-RAG (0.898) and Mem0 (0.926). Also note RAG's last-epoch BCB score (0.475) is *below* No Memory (0.485) and Self-RAG's OS score (0.646) is below No Memory (0.674) — naive retrieval can be net-harmful, which is the paper's thesis and is supported. **No error bars, variances, or multiple seeds are reported anywhere in the paper** (a single fixed seed 42 is used for splits).

### 6.3 Table 2 — Transfer Learning (memory bank frozen after training, held-out sets), p7

| Method | BigCodeBench | LLB OS | LLB DB | ALFWorld | Average |
|---|---|---|---|---|---|
| *Backbone* | *GPT-4o* | *GPT-4o-mini* | *GPT-4o-mini* | *GPT-5-mini* | — |
| No Memory | 0.485 | 0.673 | 0.841 | 0.836 | 0.709 |
| RAG | 0.479 | 0.713 | 0.920 | 0.950 | 0.765 |
| Self-RAG | 0.500 | 0.653 | 0.881 | 0.950 | 0.746 |
| Mem0 | 0.485 | 0.686 | 0.935 | 0.950 | 0.764 |
| MemP | 0.494 | 0.720 | 0.928 | 0.921 | 0.766 |
| **MemRL (ours)** | **0.508** | **0.746** | **0.942** | **0.979** | **0.794** |

"+2.8% in Success Rate" over MemP on average; ALFWorld +5.8, OS +2.6 (p6). Note HLE is absent — there is no HLE transfer split. On BigCodeBench the transfer margin over MemP is +1.4 pp and over No Memory +2.3 pp, i.e. the coding gains largely do not transfer.

### 6.4 Table 10/11 — unified backbone (GPT-4o-mini everywhere), pp. 23–24

Runtime (Last Epoch SR / CSR):

| Method | LLB OS | LLB DB | ALFWorld | Average |
|---|---|---|---|---|
| No Memory | 0.674 / N/A | 0.860 / N/A | 0.278 / N/A | 0.604 / N/A |
| Pass@10 | N/A / 0.756 | N/A / 0.928 | N/A / 0.462 | N/A / 0.715 |
| MemP | 0.736 / 0.742 | **0.960** / 0.966 | 0.299 / 0.413 | 0.665 / 0.707 |
| **MemRL** | **0.788 / 0.804** | **0.960 / 0.972** | **0.440 / 0.680** | **0.730 / 0.819** |

Transfer (SR): No Memory 0.673 / 0.841 / 0.314 → avg 0.609; MemP 0.720 / 0.928 / 0.314 → avg 0.654; MemRL 0.746 / 0.942 / 0.457 → avg **0.715**.

Claims (p23–24): runtime "average Last Epoch SR is **12.6% points** higher than 'No Memory'", "average CSR surpasses MemP by **11.2% points**"; transfer "**10.6% points** higher than 'No Memory' and **6.1% points** higher than MemP." The ALFWorld column is where this is won: MemP's CSR of 0.413 is *below* Pass@10's 0.462, while MemRL reaches 0.680. Note MemP fails to improve ALFWorld transfer at all (0.314 = No Memory exactly).

Rationale for per-benchmark backbones (§E.4, p23): avoid the "**no-signal situation**" (GPT-4o-mini starts at ≈4% on HLE, "environmental feedback is dominated by noise") and the "**ceiling effect**." This is a reasonable experimental-design argument, but it also means the main table mixes five backbone/benchmark pairings chosen partly for favorable learning dynamics.

### 6.5 Table 5 — Cross-model memory transfer on HLE, p20

Memory bank trained 10 epochs by **Gemini-3-pro**, then transferred with no fine-tuning:

| Inference model | Base | Transfer | Gain (Δ) |
|---|---|---|---|
| Qwen3-235B | 0.150 | 0.531 | **+0.381** |
| Gemini-3-flash | 0.347 | 0.583 | +0.236 |
| GPT-5.2(High) | 0.354 | 0.571 | +0.217 |

Claim: MemRL "captures **model-agnostic** problem-solving patterns—such as efficient code skeletons and reasoning templates—rather than model-specific artifacts ... enabling weaker models to 'inherit' the capabilities of a stronger teacher model through simple retrieval" (p20).

**This is the weakest result in the paper, evidentially.** HLE is "Full set (Runtime only)" — no held-out split. The transferred memory bank contains, for each of the 2,500 evaluation questions, Gemini-3-pro's own verified-correct trajectory (`Task: ... SCRIPT: ... TRAJECTORY: ...`), and Phase A retrieves by intent similarity, so the top-1 hit for question *i* is overwhelmingly likely to be the stored solution to question *i*. Qwen3-235B tripling its score (0.150→0.531) is consistent with answer retrieval rather than pattern transfer. The paper's own §B.3 concedes the mechanism on HLE is "Runtime Memorization." A clean test would require a held-out HLE split; none is run.

### 6.6 Lifelong / streaming curves

- **Figure 4 (OS Interaction, 10 epochs, y ∈ [65, 80]%)** — four curves: `MemP+RL` (= MemRL), `MemP`, `RAG+RL`, `RAG`; solid = epoch SR, dashed = CSR. "while initial performance is comparable, a clear divergence emerges as training progresses ... this advantage is most pronounced in the Cumulative Success Rate (dashed lines), where the **monotonic widening gap** indicates that the RL-driven value function effectively filters noisy memories" (p6). Note the RL-vs-non-RL comparison is run on *both* MemP and RAG substrates — a clean, well-designed ablation.
- **Figure 8 (HLE, 10 epochs, y ∈ [0.35, 0.60])** — MemRL vs MemP epoch SR and CSR, with `pass@1 … pass@10` reference marks. "while heuristic methods like MemP suffer from catastrophic forgetting—evidenced by a **widening gap between CSR and current Success Rate**—MEMRL maintains **synchronized growth**" (p8).
- **Figure 9 (Appendix B.1, HLE, epochs 2→5, y ∈ [0.02, 0.10])** — forgetting-rate trajectories: MemP trends upward; MemRL stays flat and low; `MemRL w/o Norm_SimGate` is highest with "visible spikes ... the agent frequently retrieves and reinforces 'noisy' strategies that work for specific instances but degrade general performance on previously mastered tasks" (p18).
- **Figure 11 (Appendix F.2)** — wall-clock hours per HLE epoch, 10 epochs, MemRL vs MemP.

---

## 7. Ablations

| # | Ablation | Setting | Result | Where |
|---|---|---|---|---|
| 1 | **Remove runtime RL entirely** (Phase B off ⇒ pure semantic retrieval) | OS Interaction, 10 epochs | `MemP+RL > MemP` and `RAG+RL > RAG`; gap widens monotonically in CSR; MemRL "achieves a smoother learning curve and superior stability" | §5.3.1, Fig. 4, p6 |
| 2 | **Q-weight `λ` sweep** | `λ ∈ {0, 0.25, 0.5, 0.75, 1}`, OS | "clear **concave** trend peaking at **λ = 0.5**." `λ=0` (pure semantic) "plateaus due to an inability to filter functional distractors"; `λ→1` "induces **volatility and context detachment**" | §5.3.2, Fig. 5, p6 |
| 3 | **Cross-task vs. single-task retrieval scope** (≈ Reflexion) | all 5 | Single-Task Reflection: BCB 0.614, OS 0.714, DB 0.938, ALFWorld 0.930, HLE **0.610**, avg 0.761. MemRL (Cross-Task): 0.627, **0.804**, **0.972**, **0.981**, 0.606, avg **0.798**. OS **+9.0**, ALFWorld **+5.1**, BCB +1.3, DB +3.4, **HLE −0.4** | Table 3, §5.3.3, pp. 6–7 |
| 4 | **Retrieval bandwidth `(k₁, k₂)`** | HLE (CS/AI) subset: sparse (3,1), moderate (5,3), dense (10,5) | **Inverted-U**; moderate (5,3) best. Sparse "limits performance due to insufficient guidance"; dense "degrades success rate by introducing distractions into the reasoning context" | §5.3.4, Fig. 6, p7 |
| 5 | **Remove z-score normalization + similarity gating** | HLE | Forgetting rate spikes **0.041 → 0.073** (MemP = 0.051). "strict filtering is essential to manage utility variance and ensure that self-evolution remains stable" | §5.4.2, Fig. 9, pp. 8, 18 |
| 6 | **Multi-task memory merging** (`M_unified = M_OS ∪ M_DB`, no retraining) | LLB | OS 0.788 → 0.784 (**−0.004**); DB 0.960 → 0.960 (**0.000**). "the semantic spaces of OS commands and SQL queries are largely orthogonal, [so] the Phase-A similarity filter effectively acts as a **semantic gate**" | Table 6, §C.2, p20 |
| 7 | **Cross-model memory transfer** | HLE | See §6.5 | Table 5, §C.1, p20 |
| 8 | **Unified-backbone consistency** | GPT-4o-mini on OS/DB/ALFWorld | See §6.4 | Tables 10–11, §E.4 |

### 7.1 Utility-ablation / Q-critic validation (§5.4.1, Figure 7, pp. 7–8)

Success rate by Q-bin, and memory composition by Q-bin:

| Q-estimate range | 0.2–0.3 | 0.3–0.4 | 0.4–0.5 | 0.5–0.6 | 0.6–0.7 | 0.7–0.8 | 0.8–0.9 | 0.9–1.0 |
|---|---|---|---|---|---|---|---|---|
| Downstream task SR | 21.5% | 46.5% | 73.4% | 79.1% | 82.4% | 84.3% | 86.2% | 88.1% |
| SUCCESS-memory share | 22% | 47% | 73% | 79% | 82% | 84% | 86% | 88% |
| FAILURE-memory share | 78% | 53% | 27% | 21% | 18% | 16% | 14% | **12%** |

**Pearson r = 0.861.** Pool average success share = 74.6%. Interpretation offered: "the agent retains a small fraction of 'failure' memories (∼12%) even in high-Q bins (0.9−1.0), suggesting that Q-values capture **utility beyond binary outcomes** by recognizing strategically useful **near-misses**" (p7) — substantiated qualitatively by six case studies in Appendix H, all `FAILURE_REFLECTION` entries with high empirical hit rates (CS1 19/19, CS2 20/20, CS3 44/48, CS4 23/26, CS5 88/94, CS6 55/59).

**Critical note on Figure 7.** `Q` is an EMA of exactly the reward signal being plotted on the y-axis. Binning memories by `Q` and plotting their mean realized reward is close to tautological — `r = 0.861` measures the self-consistency of an EMA, not out-of-sample predictive validity. Worse, the two rows of the table above are numerically near-identical (SR ≈ SUCCESS-share in every bin), which is what you would expect if "downstream task success rate" and "memory composition" are two views of the same underlying counts rather than independent measurements. A genuine test would hold out future outcomes: rank memories by `Q` at epoch *k* and measure success at epoch *k+1*.

### 7.2 Cross-task transfer conditions (§B.2–B.3, pp. 18–19)

**Table 4 — task structure vs. CSR gain:**

| Benchmark | Interaction | MemP (%) | MemRL (%) | Gain (pp) |
|---|---|---|---|---|
| ALFWorld | Multi-step | 91.9 | 98.1 | **+6.2** |
| OS Task | Multi-step | 74.2 | 80.4 | **+6.2** |
| HLE | Single-step | 57.0 | 60.6 | +3.6 |
| BigCodeBench | Single-step | 60.2 | 62.7 | +2.5 |

"In sequential tasks, a retrieved memory must be valid for the **entire** trajectory. Standard semantic retrieval often fetches memories that match the initial instruction but fail in later steps." Conclusion: MemRL "transcends the role of a simple retrieval enhancer to function as a **Trajectory Verifier**" (p18).

**Intra-dataset similarity vs. absolute gain `Δ = SR_MemRL − SR_NoMem`** (Figure 10, p19), positive linear trend:

| Benchmark | Mean intra-dataset cosine similarity | Δ |
|---|---|---|
| ALFWorld | **0.518** | **+0.172** |
| Lifelong-OS | 0.390 | ≈+0.10 ~ +0.11 |
| BigCodeBench | 0.308 | ≈+0.10 ~ +0.11 |
| Lifelong-DB | (mid) | — |
| HLE | **0.186** | **+0.213** (0.357 → 0.570) — outlier |

Two distinct mechanisms are named: "In high-similarity benchmarks, MEMRL succeeds via **Positive Transfer**—generalizing shared patterns to new instances. In contrast, the gain in HLE stems from **Runtime Memorization**" (p19). HLE's low similarity (0.186) is also the stated reason cross-task retrieval provides no benefit there (Table 3: 0.606 vs 0.610 single-task).

---

## 8. Limitations

### 8.1 Stated by the authors (§6 p8, Appendix G pp. 25–26)

1. **High-variance step-wise updates on long horizons** — "The current step-wise update, though fast, may introduce high-variance noise in long-horizon trajectories, inspiring us to explore **multi-step updates or periodic memory consolidation**."
2. **Credit-assignment ambiguity** with multiple retrieved memories — remedies proposed: Shapley values, QMIX/VDN-style value decomposition.
3. **Degradation to reflection under low task similarity** — "performance may drift toward **reflection-like behavior** when task similarity is low, highlighting the need for a sufficiently diverse yet relevant experience base; for industrial deployment, ensuring high **task density** and hierarchical abstraction may be crucial." (§G.3 adds active curriculum learning and environment design as mitigations.)
4. **Memory security / reward hacking (§G.4)** — "MEMRL is sensitive to the quality of feedback, particularly vulnerable to '**reward hacking**' if the verifier produces false positives. Incorrectly learned high Q-values from spurious feedback can quickly solidify and propagate erroneous behavioral patterns, leading to systemic failures ... A maliciously injected sample into the memory bank could rapidly **diffuse pollution**, potentially causing an intelligent agent to collapse." Silver lining: "polluted experiences can be swiftly pruned."
5. **Multi-agent shared memory (§G.5)** — open questions of "what to share" and "with whom to share," and "the risks of **negative transfer**."
6. **Dedicated domains (§G.6)** — a frozen backbone may not "comprehend foundational terminology"; anticipates "a future **hybrid model** where companies periodically fine-tune foundational models (e.g., annually) ... while simultaneously leveraging MEMRL for daily behavioral adaptation." Also: "requires the base model to possess a relatively high level of 'intelligence'."
7. **Backbone-selection dependence (§E.4)** — implicit admission: "extremely low initial competence can make the feedback effectively unusable ... weaker models may start at ≈4% success rate (e.g., GPT-4o-mini) [on HLE], yielding too few successful trajectories for stable utility estimation."

### 8.2 Unstated but material

1. **Reward availability at runtime is the binding constraint, and it is assumed away.** Every benchmark supplies an oracle verifier: unit tests, a simulator, or an answer key. In actual deployment — the paper's stated setting — no such signal exists for most tasks. MemRL as specified has no self-verification, no LLM-judge, no user-implicit-feedback path, and no mechanism for learning from unlabeled interactions. Figure 1's illustrative example ("User: Booked it! Great experience.") gestures at implicit human feedback but nothing in Section 4 or the experiments implements it. This is the single largest gap between the paper's claims and its evidence.
2. **Train/test contamination on HLE.** No held-out split exists (Table 9: "Full set (Runtime only)"). All HLE numbers — 0.570 last-epoch, 0.606 CSR, the +0.213 Δ, the entire Table 5 cross-model transfer study, Figure 8's forgetting analysis — are measured on the same 2,500 questions the memory was fitted to with ground-truth reward. The authors call this "Runtime Memorization" and treat it as a feature; readers should treat it as an absence of generalization evidence for the flagship benchmark.
3. **Case studies mostly show self-retrieval, not transfer.** In Appendix H, **three of six case studies (CS2, CS3, CS4) have a target task whose text is verbatim identical to the origin task** of the retrieved memory; CS1's target is a near-paraphrase of its origin. E.g. CS4 origin and target are both "Run a sleep process in the background for 3 seconds, log its start and end times ... store the process ID in '/tmp/sleep_pid'". The agent is retrieving *its own prior failure reflection on the same task* at rank 1 or 2. That is Reflexion-style self-retry, which the paper elsewhere sets up as the ablation baseline. Only CS5 and CS6 show retrieval across genuinely different task instances. This weakens the "cross-task horizontal transfer" narrative for the OS domain, where the largest cross-task gain (+9.0) is claimed.
4. **Memory bloat is unmeasured.** Append-only writes, one triplet per attempt, no eviction. Bank size is never reported; retrieval is O(|M|) per query; there is no ablation on |M| at all. The `(k₁,k₂)` sweep is a *retrieval bandwidth* ablation, not a memory-size ablation — the user-facing question "how does performance scale with bank size?" is unanswered.
5. **`δ` is calibrated once, offline, and never adapts.** It is set from the *initial* pairwise-similarity distribution of task descriptions. As the bank fills with LLM-generated summaries (whose embedding distribution differs from raw task text), a fixed absolute cosine cutoff drifts in meaning. z-score normalization in Phase B compounds this: scores are normalized *within the candidate pool*, so `score` values are incomparable across queries, and the same memory can rank differently depending on which distractors happened to survive Phase A.
6. **Does utility generalize across intents? The paper cannot say.** A single scalar `Q_i` is shared across the entire support set `S(m) = {s : sim(s, z_m) > τ_A}`. Eq. 14 makes the marginalization explicit — `Q(m) = Σ_s E[r|s,m] Pr(s|m)` — so a memory that is excellent for intent cluster A and harmful for cluster B converges to an uninformative average, and the `Pr(s|m)` weighting is itself policy-dependent. No experiment measures intra-support-set reward heterogeneity, and no per-intent or contextual Q parameterization is offered.
7. **The stationarity assumption contradicts the motivating setting.** §4.4 and §A.1 require a frozen inference policy *and* a fixed, stationary task distribution over a fixed dataset. Runtime deployment is by definition non-stationary. The convergence and no-forgetting guarantees therefore certify the 10-epoch closed-dataset experiment, not the "era of experience" scenario in the introduction. No distribution-shift experiment is run (e.g., train on OS, then stream DB tasks, then return to OS).
8. **Theorem A.1's "updated infinitely often" premise is violated by the algorithm.** Greedy top-k₂ selection over a growing bank leaves most entries at `Q_init = 0.0` forever. No exploration bonus exists to fix this, and the paper reports no coverage statistics (what fraction of written memories are ever retrieved?). The `selected=` counters in Appendix H prove the instrumentation exists; the distribution is not shown.
9. **Statistical rigor.** Single seed (42), no confidence intervals, no repeated runs, no significance tests. Several headline margins are small: DB last-epoch is a tie with MemP (0.960 = 0.960); BCB transfer is +1.4 pp over MemP; HLE cross-task retrieval is −0.4 vs the single-task ablation.
10. **The Pass@k framing.** CSR is a pass@N metric. Reporting MemRL's CSR against baselines' *last-epoch SR*, and headlining gains vs. "No Memory" (a 1-attempt baseline), inflates the apparent effect. Pass@10 is included, to the authors' credit, but is not the comparison emphasized in prose.
11. **Reward scale inconsistency.** `r ∈ [−1,1]` (p13) with `Q_init = 0.0`, yet no negative Q appears anywhere; observed Q-bins span 0.2–1.0 and Figure 3 shows Q = 0.82/0.92/0.95. The implementation is almost certainly `r ∈ {0,1}`.
12. **The variational "equivalence" is an analogy, not a proof** (see §3 above). Also, the trust region the KL term supplies is discarded by hard top-k selection.

---

## 9. Exact quotable claims (verbatim, with page numbers)

1. p1 — "fine-tuning is computationally expensive and prone to catastrophic forgetting, while existing memory-based methods rely on passive semantic matching that often retrieves noise."
2. p1 — "Our objective is to achieve an agent that evolves with continued usage and rapidly adapts to new tasks after deployment, referred to as **Runtime Continuous Learning** ... all while keeping the backbone model frozen to prevent catastrophic forgetting."
3. p4 — "Equation 4 performs as a naturally simplified version of Equation 3 by setting `s′` as a terminal state to balance complexity and performance, sharing a similar one-step MDP formulation with Guo et al. (2025)."
4. p5 — "If `C(s) = ∅`, MEMRL relies solely on the frozen LLM for exploration."
5. p5 — "for each sampled trajectory, we use an LLM to summarize the experience (Fang et al., 2025), and write it back into the memory bank as a new triplet `(z, e_new, Q_init)`, enabling continual expansion of experience while keeping the LLM parameters unchanged."
6. p5 — "We posit two standard assumptions: a frozen inference policy `pLLM` and a stationary task distribution."
7. p13 — "At each time step `t`, the agent observes an intent state `s_t`, retrieves a memory item `m_t ∈ M_t`, generates an output `a_t`, and receives a scalar reward `r_t ∈ [−1,1]` indicating task success or failure."
8. p13 — "We analyze the learning process on a fixed dataset and posit two key conditions that ensure the stability of the environment."
9. p6 — "While on the HLE benchmark, the single-task baseline (0.610) is tied with MEMRL (0.606). We attribute this to the HLE dataset's low internal semantic similarity (0.186)."
10. p7 — "the agent retains a small fraction of 'failure' memories (∼12%) even in high-Q bins (0.9−1.0), suggesting that Q-values capture **utility beyond binary outcomes** by recognizing strategically useful near-misses."
11. p17 — "This proves that our heuristic combination of similarity and Q-value is mathematically equivalent to the optimal policy under the variational objective."
12. p19 — "the gain in HLE stems from **Runtime Memorization**. Since HLE questions are distinct and domain-specific, the agent relies on the Runtime Learning phase to 'memorize' specific solutions to difficult problems through repeated exposure."
13. p20 — "These results suggest that MEMRL captures **model-agnostic** problem-solving patterns—such as efficient code skeletons and reasoning templates—rather than model-specific artifacts."
14. p22 — "we determine `δ` by calculating the pairwise cosine similarity distribution of task descriptions within each benchmark and selecting the threshold at the top 20% quantile."
15. p24 — "On the HLE benchmark, the average total token consumption per question for MEMRL is approximately 32K ... the significant performance gains of MEMRL reported in the main text are achieved without increasing the inference budget."
16. p25 — "MEMRL is sensitive to the quality of feedback, particularly vulnerable to 'reward hacking' if the verifier produces false positives. Incorrectly learned high Q-values from spurious feedback can quickly solidify and propagate erroneous behavioral patterns, leading to systemic failures."
17. p26 — "Utility. selected=19, success=19 (100.0%)." (Case Study 1 — evidence of per-entry counters absent from the formal triplet.)

---

## 10. Open problems named by the authors

1. **Multi-step / n-step utility updates and periodic memory consolidation** to replace the single-step EMA on long-horizon trajectories, trading speed for stability and reducing memory redundancy (§6, §G.1).
2. **Precise credit assignment across simultaneously-retrieved memories** — Shapley-value attribution or multi-agent value decomposition (VDN, QMIX) (§6, §G.2).
3. **Generalizing beyond direct experience matching when task similarity is low** — hierarchical abstraction, active curriculum learning, environment design that promotes "diverse yet related tasks," and retrieval that can "proactively identify and adapt to novel task structures" (§G.3).
4. **Memory security and trustworthiness** — defenses against reward hacking, verifier false positives, and memory-poisoning attacks; detection and pruning of contaminated experiences (§G.4).
5. **Multi-agent / swarm shared memory** — collective memory pools, selective knowledge diffusion, "what to share" and "with whom," avoiding negative transfer, and "trade-offs between global collective intelligence and targeted knowledge distribution" (§G.5).
6. **Hybrid parametric + non-parametric architectures for specialized domains** — periodic (e.g., annual) backbone fine-tuning for core vocabulary, with MemRL handling daily behavioral adaptation (§G.6).
7. **Maintaining sufficient "task similarity density" in industrial deployment** — framed as a deployment-engineering open problem, not an algorithmic one (§G.3).

---

## Bottom line

MemRL is a clean, cheap, and well-instrumented contribution: attach a scalar EMA-estimated utility to every episodic memory entry, gate retrieval by a hard semantic threshold, then re-rank by a 50/50 z-scored blend of similarity and utility. The empirical case that *utility-aware re-ranking beats pure semantic retrieval* is well made — the `MemP+RL vs MemP` and `RAG+RL vs RAG` paired ablation (Figure 4), the concave `λ` sweep, the forgetting-rate ablation (0.041 / 0.051 / 0.073), and the memory-merging non-interference result are all genuinely informative, and the cost analysis (~32K tokens/question, O(1) updates, negligible latency) is credible.

Three claims should be discounted. First, "reinforcement learning": what is implemented is constant-step-size EMA value estimation for a contextual bandit with a greedy top-k arm-selection rule and no exploration mechanism — the TD rule in Eq. 3 is presented and then discarded, and `γ` is never assigned a value. Second, "constructive episodic simulation": no simulation or rollout operator exists in the method; the phrase is a cognitive-science metaphor for the Q-value plus a retrospective LLM summarization call. Third, and most consequentially, the runtime/deployment framing: MemRL consumes ground-truth verifier reward on every task it learns from, its theory assumes a fixed dataset and a stationary task distribution, tasks repeat ten times by construction, HLE has no held-out split at all, and three of six Appendix-H case studies retrieve a memory whose origin task is verbatim identical to the target. The honest reading is that MemRL is a strong *repeated-exposure, verifier-in-the-loop* self-improvement method with an unusually thorough stability analysis — not yet a demonstration of label-free post-deployment learning.
