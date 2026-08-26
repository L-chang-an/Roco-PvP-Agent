# Technical Dossier: GEPA — Reflective Prompt Evolution Can Outperform Reinforcement Learning

Source: `/Users/liuzhanyu/workspace/RLProject/docs/_extract/GEPA.txt` (96 pages, 6259 lines, 278,617 bytes).
Venue: **Accepted at ICLR 2026 (Oral).** Code: `https://github.com/gepa-ai/gepa`.
Authors: Lakshya A Agrawal, Shangyin Tan, Dilara Soylu, Noah Ziems, Rishi Khare, Krista Opsahl-Ong, Arnav Singhvi, Herumb Shandilya, Michael J Ryan, Meng Jiang, Christopher Potts, Koushik Sen, Alexandros G. Dimakis, Ion Stoica, Dan Klein, Matei Zaharia, Omar Khattab (UC Berkeley, Stanford, BespokeLabs.ai, Notre Dame, Databricks, MIT).

Page references below are to the paper's own page numbering as marked in the extract (`===== [GEPA] PAGE n/96 =====`).

---

## 1. Problem formulation

### 1.1 Compound AI system formalism (p.4)

A compound AI system is a tuple

```
Φ = (M, C, X, Y)
```

- `M = ⟨M_1, ..., M_|M|⟩` — the **language modules** (LLM subcomponents).
- `C` — the **control flow logic**: "C orchestrates the sequencing and invocation of modules—e.g., passing outputs from one module to another, invoking modules conditionally, or leveraging tool APIs. This way, C can invoke different modules in any order multiples of times." (p.4)
- `X, Y` — global input/output schemas.

Each module is
```
M_i = (π_i, θ_i, X_i, Y_i)
```
where `π_i` is its **(system) prompt including instructions and few-shot demonstrations**, `θ_i` the **underlying model weights**, and `X_i, Y_i` its local schemas.

Collections:
```
Π_Φ = ⟨π_1, ..., π_|M|⟩        (all module prompts)
Θ_Φ = ⟨θ_1, ..., θ_|M|⟩        (all module weights)
learnable parameters = ⟨Π, Θ⟩_Φ
```

### 1.2 Objective (Eq. 1, p.4)

A task instance is a pair `(x, m)`: `x` maps to input schema `X`; `m` is **evaluator metadata** ("gold answers, evaluation rubrics, code unit tests"). The system induces `y = Φ(x; ⟨Π,Θ⟩_Φ)`. A metric `µ : Y × M → [0,1]` scores output quality ("exact match, F1, pass rate, etc."). With task distribution `T`:

```
⟨Π*, Θ*⟩_Φ = argmax_{⟨Π,Θ⟩_Φ}  E_{(x,m)~T} [ µ( Φ(x; ⟨Π,Θ⟩_Φ), m ) ]        (1)
```

The authors deliberately keep weights in the formalism even though GEPA never touches them: "We adopt this general problem formulation, allowing updates to both prompts and weights of language modules, to enable comparisons between optimization algorithms that operate in different parameter spaces (e.g., GEPA vs. GRPO)." (p.4)

### 1.3 Rollout, budget, and Eq. 2 (p.4)

A **rollout** is defined explicitly: "rollouts—concretely, invocations of Φ plus evaluation by µ". Note this counts one *system-level* execution (which may internally invoke many LLM calls across modules and hops) plus its metric evaluation as **one** rollout. This is the unit in which GEPA and GRPO are compared.

Budget-constrained problem:
```
⟨Π*, Θ*⟩_Φ = argmax_{⟨Π,Θ⟩_Φ}  E_{(x,m)~T} [ µ( Φ(x; ⟨Π,Θ⟩_Φ), m ) ]
                subject to   #rollouts ≤ B                                  (2)
```
over `D_train = {(x,m)_i}_{i=1}^N` with **full access to µ**. Framing question (p.4): "How do we extract maximal learning signal from every expensive rollout to enable effective adaptation of complex, modular AI systems in low-data or budget-constrained settings?"

### 1.4 What is assumed accessible

1. **The system's source/structure** — module list, so a specific module's prompt can be swapped (system-aware).
2. **Per-instance scalar scores** `µ(y, m) ∈ [0,1]` — required, not just aggregate; the Pareto machinery is built on the per-instance score matrix `S`.
3. **The full serialized trajectory** — "they contain nothing but the instructions of each LLM module, the resulting LLM reasoning chains, tool calls, and potentially the internal workings of the reward function (e.g., compiler error messages, before they are collapsed into scalar rewards)" (p.3).
4. **A feedback function `µ_f`** returning score + `feedback_text` (§3, p.6–7); optionally module-specific.
5. **A reflection LM** capable of diagnosing and rewriting instructions.
6. **A validation split** whose *scores* may be tracked but whose *contents* are restricted: "Although optimizers may monitor the performance of candidate parameters (like model checkpoints) by tracking scores on the validation set (to implement early stopping, for example), direct access to the content of validation instances is restricted." (p.8)
7. Weights `Θ` are **frozen**; only `Π` is optimized.

---

## 2. Core method

GEPA = "**G**enetic-**P**areto", "a *reflective* prompt optimizer for compound AI systems that merges textual reflection with multi-objective evolutionary search" (p.3). Three principles (p.4): genetic prompt evolution, reflection using natural-language feedback, and Pareto-based candidate selection — plus system-aware module targeting and (optionally) system-aware crossover.

### 2.1 Pillar (a): Reflective prompt mutation (§3, pp.5–6)

Rationale (p.5): execution traces "capture the intermediate inferences and underlying reasoning steps"; paired with the final outcome "they provide substantial diagnostic value, allowing practitioners to trace errors or successes back to specific decisions made at the module level."

Mechanics:
- **Module selection policy: round-robin.** "GEPA selects the module (among the |M| modules that the language program contains) to be updated based on a policy (round-robin)" (p.5).
- The reflection LM is shown the tuple **(current prompt, language program trajectory, score, feedback)** with the task "to reflectively attribute successes or failures to prompt elements and propose revised instructions" (pp.5–6).
- Only that one module's prompt is replaced; the rest of the program is untouched. The mutated system is re-run on the *same minibatch*; accepted into the pool only if minibatch score improves.

**Execution traces vs evaluation traces (p.6)** — a key conceptual split:
- *Execution trace*: "The text that LLMs produce".
- *Evaluation trace*: "The text that the environment produces to compute the reward (e.g. compiler error messages before giving reward 0)."
GEPA consumes both, by extending `µ` into `µ_f`.

**Meta-prompt (Appendix C, p.22), verbatim skeleton:**
```
I provided an assistant with the following instructions to perform a task for me:
```
<curr_instructions>
```
The following are examples of different task inputs provided to the assistant along with
the assistant's response for each of them, and some feedback on how the assistant's
response could be better:
```
<inputs_outputs_feedback>
```
Your task is to write a new instruction for the assistant.

Read the inputs carefully and identify the input format and infer detailed task description
about the task I wish to solve with the assistant.

Read all the assistant responses and the corresponding feedback. Identify all niche and
domain specific factual information about the task and include it in the instruction, as a
lot of it may not be available to the assistant in the future. The assistant may have utilized
a generalizable strategy to solve the task, if so, include that in the instruction as well.

Provide the new instructions within ``` blocks.
```
Two design notes worth flagging: the meta-prompt explicitly asks the reflection LM to **hoard domain facts into the instruction** (which is why GEPA prompts grow into long declarative documents), and to **verbalize any generalizable strategy** it can infer.

### 2.2 Algorithm 1 — GEPA core (p.6, verbatim pseudocode)

```
Require: System Φ, dataset D_train, eval metric µ, feedback function µ_f, budget B
Require: Hyperparams: minibatch size b, Pareto set size n_pareto
 1: Split D_train into D_feedback, D_pareto, s.t. |D_pareto| = n_pareto
 2: Initialize candidates P ← [Φ], parents A ← [None]
 3: for each (x_i, m_i) in D_pareto do
 4:     S_Φ[i] ← µ(Φ(x_i), m_i)
 5: end for
 6: while budget B not exhausted do
 7:     k ← SELECTCANDIDATE(P, S)                       # Algorithm 2
 8:     j ← SELECTMODULE(Φ_k)                           # round-robin
 9:     M ← minibatch of size b from D_feedback
10:     Gather feedback, scores, traces for Φ_k[j] on M using µ_f
11:     π'_j ← UPDATEPROMPT(π_j, feedbacks, traces[j])  # reflection LM + meta-prompt
12:     Φ' ← copy of Φ_k with module j updated by π'_j
13:     σ, σ' ← avg score on M (before, after)
14:     if σ' improved then
15:         Add Φ' to P; Add k to A                     # A records the PARENT index
16:         for each (x_i, m_i) in D_pareto do
17:             S_Φ'[i] ← µ(Φ'(x_i), m_i)               # full validation sweep
18:         end for
19:     end if
20: end while
21: return Φ* maximizing average score on D_pareto
```

Observations about the accounting: line 17 is a **full sweep of `D_pareto`** every time a candidate is accepted, which is why "the majority of GEPA's rollout budget is spent on validation" (p.9). Rejected mutations cost only `b` rollouts (b = 3 in all experiments). The parent list `A` is what makes lineage/ancestry computable.

### 2.3 Algorithm 2 — Pareto-based candidate selection (p.6, verbatim pseudocode)

Motivation (p.7): "A naive approach is to always select the best-performing candidate, but this often traps the optimizer in a local optimum: once a dominant strategy is found, it becomes difficult to surpass, and the optimizer exhausts its budget without learning new, potentially better strategies." GEPA instead uses a Pareto-based **"illumination"** strategy, citing Mouret & Clune (2015) (MAP-Elites).

```
 1: function SELECTCANDIDATE(P, S)
 2:     // Build instance-wise Pareto sets
 3:     for each i do                                  # i indexes D_pareto instances
 4:         s*[i] ← max_k S_{P[k]}[i]                  # best score achieved on instance i
 5:         P*[i] ← { P[k] : S_{P[k]}[i] = s*[i] }     # all candidates tied at that best
 6:     end for
 7:     C ← unique candidates in ∪_i P*[i]
 8:     D ← ∅
 9:     while there exists Φ ∈ C \ D dominated by another in C \ D do
10:         D ← D ∪ {Φ}
11:     end while
12:     Remove D from each P*[i] to get P̂*[i]
13:     Let f[Φ] = number of i for which Φ ∈ P̂*[i]
14:     Sample Φ_k from Ĉ with probability ∝ f[Φ_k]
15:     return index k of Φ_k in P
16: end function
```

Concretely, the **sampling probability** of a surviving candidate is
```
Pr[Φ] = f[Φ] / Σ_{Φ' ∈ Ĉ} f[Φ']
```
i.e. **proportional to the number of validation instances on which that candidate is (tied) best** — "weighting probabilities by how many tasks each candidate leads" (p.8). The objective space is thus `|D_pareto|`-dimensional (one objective per instance); dominance is over that per-instance score vector. Summary (p.7): "For each training instance, GEPA records the highest score across all candidates, forming a Pareto frontier. Candidates that achieve the best score on at least one task are retained, while strictly dominated ones are pruned. From this pruned set, GEPA stochastically samples a candidate."

Effect (p.8): "This strategy helps GEPA escape local optima without inflating the search, efficiently balancing exploration and exploitation by focusing resources on candidates that embody 'winning' strategies within the optimization budget."

### 2.4 Pillar (c): System-aware module targeting

Two mechanisms:
1. **Single-module mutation with round-robin targeting** (Alg. 1 line 8): each proposal edits exactly one module, so credit assignment and acceptance testing are localized. Module-specific `µ_f` feedback (e.g. per-hop retrieval feedback) is routed to the module being edited.
2. **System-aware crossover (Merge)** over modules — see §6.

**Ancestry/lineage.** `A[k]` stores the parent index of candidate `k`; `GETANCESTORS(i, A)` walks the chain to the root, giving `A_i`. Ancestry is used only by Merge: to forbid merging a candidate with its own ancestor, to find common ancestors `a ∈ A_i ∩ A_j`, and to determine per-module *which lineage changed which module* by diffing against `a`. Lineage is visualized as the optimization tree (Figures 19–26, pp.32–41; annotated PUPA subtree in Figure 5, p.7).

### 2.5 Algorithms 3 & 4 — Merge (Appendix D.1 / Figure 9, p.24, verbatim pseudocode)

```
 1: function DESIRABLE(a, i, j, P)
 2:     for module m = 1 to |M| do
 3:         π_a ← ancestor's prompt for module m
 4:         π_i ← descendant i's prompt for module m
 5:         π_j ← descendant j's prompt for module m
 6:         if (π_a = π_i and π_j ≠ π_i) or (π_a = π_j and π_i ≠ π_j) then
 7:             return True                  # the two lineages edited DISJOINT modules
 8:         end if
 9:     end for
10:     return False
11: end function
```

```
 1: function MERGE(P, A, S, r)                 # r = seeded stochastic sampler
 2:     i, j ← r.sample(2, |P|)                # distinct i ≠ j
 3:     A_i ← GETANCESTORS(i, A);  A_j ← GETANCESTORS(j, A)
 4:     if i ∈ A_j or j ∈ A_i then
 5:         continue                           # skip direct ancestry
 6:     end if
 7:     for a ∈ A_i ∩ A_j do                   # common ancestors
 8:         if this merge(i, j, a) has been tried before then continue end if
11:         if S[a] > min(S[i], S[j]) then continue end if   # both children must beat ancestor
14:         if not DESIRABLE(a, i, j, P) then continue end if
17:         Φ' ← copy of P[a]
18:         for module m = 1 to |M| do
19:             π_a ← P[a].M_m.π;  π_i ← P[i].M_m.π;  π_j ← P[j].M_m.π
22:             if   π_a = π_i and π_j ≠ π_i then Φ'.M_m.π ← π_j     # take j's edit
24:             else if π_a = π_j and π_i ≠ π_j then Φ'.M_m.π ← π_i  # take i's edit
26:             else if π_i ≠ π_j ≠ π_a then
27:                 d* = argmax{ S[i], S[j] }   (break ties randomly)
28:                 Φ'.M_m.π ← π_{d*}                                # both edited: take better parent's
30:             else Φ'.M_m.π ← π_i                                  # default
31:             end if
32:         end for
33:         return (Φ', i, j, a)
34:     end for
35:     return None
36: end function
```

### 2.6 Proposal-strategy dispatch (p.5, Figure 3)

In each iteration GEPA picks one of the two proposal strategies — "(Reflective Prompt Mutation (Section 3) or System Aware Merge (Appendix D.1))" — so GEPA+Merge interleaves crossover with mutation. In the reported experiments "merge is invoked a maximum" of 5 times per run (p.26).

---

## 3. The feedback function `µ_f`

### 3.1 Concept (§3 / p.6–7)

`µ_f` "extracts textual traces during evaluation and returns them with the final score as `feedback_text`." Key properties stated:
- **Module-specific when available**: "in multi-hop systems the evaluator may provide feedback after each hop."
- **Human feedback is a legal source**: "there are domains where human-graders are able to rate the AI system's responses, along with providing detailed feedback justifying their scalar ratings. When available, D_train can be augmented with such human-written explanations for each instance; during reflection, and GEPA can consume these explanations as auxiliary `feedback_text` to guide targeted prompt updates, even when natural-language feedback from rollouts is limited or unavailable." (p.7)
- The core argument (Abstract, p.1): "the interpretable nature of *language* often provides a much richer learning medium for LLMs, compared to policy gradients derived from sparse, scalar rewards."

Contrast with RL: GRPO compresses each rollout into one scalar advantage; `µ_f` preserves the *reason* — which documents were missing, which constraint failed, which PII leaked, which compiler error fired — and hands that text to the reflection LM as a diagnosis to be written into the instruction.

### 3.2 Per-benchmark feedback engineering (Appendix E.1, pp.23–25)

| Benchmark | Compound system | `µ_f` textual feedback | Splits (train/val/test) |
|---|---|---|---|
| **HotpotQA** | HoVerMultiHop with the last hop modified "to answer the question instead of generating another query"; rest unmodified | "The textual feedback module identifies the set of relevant documents remaining to be retrieved at each stage of the program, and provides that as feedback to the modules at that stage" — i.e. **per-stage, module-targeted retrieval feedback** | 150 / 300 / 300 |
| **IFBench** | 2-stage: produce an answer, then rewrite it to satisfy the output constraints | Lists **which constraints were satisfied and which failed** | 150 / 300 / 294; train/val split from **IF-RLVR Train**, test = IFBench (58 new OOD constraints) |
| **HoVer** | HoverMultiHop, up to 3 hops, **2 query writers + 2 summarizers** (4 modules) | **Correct documents retrieved so far + documents still remaining**, per hop | 150 / 300 / 300 (full-finetune GRPO comparison used a 2-hop variant) |
| **PUPA** | PAPILLON: 2 optimizable modules (`craft_redacted_request` query rewriter, `respond_to_query` response rewriter) + an untrusted external-LLM call | **Decomposition of the aggregate score into its two components**: response quality and PII leakage | 111 / 111 / 221 |
| **AIME-2025** | single-step ChainOfThought | **No bespoke textual feedback described** — effectively score-only (correct/incorrect) plus the reasoning trace | train/val from AIME 2022–2024 (90 questions, split equally); test = 30 AIME-2025 questions, each run 5× |
| **LiveBench-Math** | single-step ChainOfThought | **No bespoke textual feedback described** | n = 368 retrieved July 30 2025, shuffled with python seed 0, split equally |
| **NPUEval / KernelBench** (§5.1) | Sequential10 / Sequential5 refinement agents | Compiler errors + profiling results, **used as retrieval keys to pull relevant technical-manual sections into the reflection context** | full task set used as both `D_train` and `D_pareto` |

### 3.3 How much feedback engineering was needed — assessment

Honest reading: **moderate but real, and it is hand-written per task.** Four of the six benchmarks got a bespoke feedback module; each is a short, natural, *already-computable* decomposition of the existing metric rather than new supervision:
- HotpotQA / HoVer: the recall metric already knows which gold documents are missing — `µ_f` just prints them.
- IFBench: the constraint checker already evaluates each constraint — `µ_f` just prints the per-constraint verdicts.
- PUPA: the metric is already a weighted sum of two sub-scores — `µ_f` just un-sums it.

So the engineering is best described as **"do not collapse the metric to a scalar"** rather than "write a new grader." Still, it is manual, per-task, requires access to metric internals, and the paper provides no ablation isolating engineered `µ_f` from plain score-only feedback. Notably, the two tasks with **no** engineered feedback are precisely where GEPA is weakest against RL: AIME-2025 (GEPA 32.00 vs GRPO 38.00 on Qwen3 8B — GEPA's only loss) and LiveBench-Math (+0.69 over GRPO, the narrowest margin). That correlation is suggestive of `µ_f` richness driving the gains, though the paper never states it.

---

## 4. Experiments

### 4.1 Setup

**Tasks (6):** HotpotQA, IFBench, HoVer, PUPA, AIME-2025, LiveBench-Math. First four are genuinely multi-module compound systems (2–4 modules); the last two are single-module ChainOfThought.

**Models:** Qwen3 8B (temp 0.6, top-p 0.95, top-k 20) and GPT-4.1 mini (`openai/gpt-4.1-mini-2025-04-14`, temp 1.0); 16384-token context (Appendix E.2, p.25).

**Baselines:** Baseline (unoptimized), MIPROv2, MIPROv2-No-Demos, Trace (OptoPrime), TextGrad, GRPO, GEPA, GEPA+Merge, GEPA-Qwen-Opt.

**Protocol:** standard train/val/test; optimizers get full access to train text+labels; validation *scores* only.

### 4.2 Table 1 — Qwen3 8B (p.8)

| Qwen3 8B | HotpotQA | IFBench | Hover | PUPA | AIME-2025 | LiveBench-Math | Aggregate | Improvement |
|---|---|---|---|---|---|---|---|---|
| Baseline | 42.33 | 36.90 | 35.33 | 80.82 | 27.33 | 48.70 | 45.23 | — |
| GRPO | 43.33 | 35.88 | 38.67 | 86.66 | **38.00** | 51.26 | 48.91 | +3.68 |
| MIPROv2 | 55.33 | 36.22 | 47.33 | 81.55 | 20.00 | 46.60 | 47.84 | +2.61 |
| GEPA | 62.33 | **38.61** | **52.33** | **91.85** | 32.00 | **51.95** | **54.85** | **+9.62** |
| GEPA+Merge | **64.33** | 28.23 | 51.67 | 86.26 | 32.00 | 51.95 | 52.40 | +7.17 |

**Total optimization budget (# rollouts):**

| | HotpotQA | IFBench | Hover | PUPA | AIME-2025 | LiveBench-Math | Aggregate |
|---|---|---|---|---|---|---|---|
| GEPA (+Merge) | 6871 | 3593 | 7051 | 2426 | 1839 | 1839 | 3936 |
| GRPO | 24000 | 24000 | 24000 | 24000 | 24000 | 24000 | 24000 |

Table 1's caption gives the cleanest single data point: "for IFBench, GEPA found optimal prompts after just **678 rollouts** achieving 38.61%, outperforming GRPO's test set score of 35.88% with 24,000 rollouts."

Note MIPROv2 *degrades* AIME-2025 (20.00 vs 27.33 baseline) and LiveBench-Math (46.60 vs 48.70) on Qwen3 8B; GEPA+Merge badly degrades IFBench on Qwen3 8B (28.23 vs 36.90 baseline).

### 4.3 Table 2 — GPT-4.1 Mini (p.8)

| GPT-4.1 Mini | HotpotQA | IFBench | Hover | PUPA | AIME-2025 | LiveBench-Math | Aggregate | Improvement |
|---|---|---|---|---|---|---|---|---|
| Baseline | 38.00 | 47.79 | 46.33 | 78.57 | 49.33 | 58.20 | 53.03 | — |
| Trace (OptoPrime) | 60.33 | 51.19 | 46.00 | 74.18 | 45.33 | 60.74 | 56.30 | +3.27 |
| MIPROv2-No-Demos | 38.00 | 52.04 | 51.33 | 91.85 | 48.67 | 60.97 | 57.14 | +4.11 |
| MIPROv2 | 58.00 | 49.15 | 48.33 | 83.37 | 51.33 | 61.84 | 58.67 | +5.64 |
| TextGrad | 62.33 | 48.64 | 47.67 | 85.68 | 46.67 | 63.84 | 59.14 | +6.11 |
| GEPA | **69.00** | 52.72 | 51.67 | 94.47 | **59.33** | **64.13** | 65.22 | +12.19 |
| GEPA+Merge | 65.67 | **55.95** | **56.67** | **96.46** | **59.33** | **64.13** | **66.36** | **+13.33** |
| GEPA-Qwen-Opt (optimized *with Qwen3-8B*, evaluated on GPT-4.1-Mini) | 65.67 | 49.83 | 54.67 | 90.05 | 52.67 | 59.31 | 62.03 | +9.00 |

Cross-model transfer (Observation 6, p.11): GEPA-Qwen-Opt gains **+27.67% on HotpotQA** over baseline and its +9.00 aggregate "outperform[s] all baselines (MIPROv2, TextGrad, Trace) that optimized directly for (and using) GPT-4.1-Mini."

### 4.4 The 35× claim — exact accounting (Observation 1, p.9)

Verbatim: "Across four benchmarks, GEPA adapts rapidly and generalizes robustly in compound AI systems—beating GRPO (24,000 rollouts) by up to 19% while using up to **35×** fewer rollouts. It reaches optimal test performance with **4–35×** fewer rollouts and exceeds GRPO on **5 out of 6** tasks by 19.0%, 2.73%, 13.66%, 5.19% and 0.7%. GEPA matches GRPO's best validation after only **243, 402, 330, 1143, 1179, and 306 rollouts—up to 78×** greater sample efficiency. GEPA+Merge widens the gap, outperforming GRPO by **21%** at a comparable rollout budget to GEPA."

Decomposition of the numbers:
- **"up to 19%"** = HotpotQA, 62.33 − 43.33 = 19.00. The other four wins: IFBench 38.61 − 35.88 = 2.73; HoVer 52.33 − 38.67 = 13.66; PUPA 91.85 − 86.66 = 5.19; LiveBench-Math 51.95 − 51.26 = 0.69 (quoted as 0.7). The one loss is AIME-2025 (32.00 vs 38.00, −6.00).
- **"up to 35×"** = 24000 / 678 = 35.4, using the IFBench figure from Table 1's caption (rollouts at which GEPA reached its optimal prompt), not the 3593 total budget. Budget-total ratios are only 3.4×–13.1× (24000/6871 = 3.5, /3593 = 6.7, /7051 = 3.4, /2426 = 9.9, /1839 = 13.1).
- **"4–35× fewer rollouts to reach optimal test performance"** — the low end (4×) is the multi-hop retrieval tasks with large `D_pareto` sweeps.
- **"up to 78×"** = 24000 / 306 = 78.4, i.e. matching GRPO's *best validation* score on the cheapest task.
- **"21%"** (GEPA+Merge) = HotpotQA 64.33 − 43.33 = 21.00.
- **Abstract-level claims (p.1):** outperforms GRPO "by 6% on average and by up to 20%, while using up to 35x fewer rollouts", and MIPROv2 "by over 10% (e.g., +12% accuracy on AIME-2025)". The +12% on AIME-2025 is Table 1's 32.00 − 20.00 on Qwen3 8B; the "over 10%" margin is PUPA on GPT-4.1 Mini (94.47 − 83.37 = 11.10) / HotpotQA (69.00 − 58.00 = 11.00).

**Crucial caveat the authors state themselves:** "The majority of GEPA's rollout budget is spent on validation, where scores are utilized solely for candidate selection and not for producing learning signals. If we restrict the analysis to train set rollouts, GEPA requires only **79 to 737 rollouts** to reach optimal performance. To match GRPO's best validation scores, GEPA achieves this with only **102, 32, 6, and 179 train rollouts** for four tasks" (p.9). So GEPA's *learning-signal* consumption is 2–3 orders of magnitude below GRPO's; the headline 35× understates the learning efficiency but the budget-total ratio (3.4×–13×) overstates practical cheapness in the multi-hop settings. Note also that a GEPA "rollout" of a 4-module 3-hop HoVer program is a much more expensive object than a GRPO rollout of one generation, so rollout-parity is not compute-parity in either direction.

Additional cost/config facts: GEPA's budget was **matched to MIPROv2 per benchmark**, "discrepancy always within 10.15%" (p.26). MIPROv2 ran `auto=heavy` (18 instruction candidates, 18 bootstrapped few-shot sets), 2270 (PUPA) to 6926 (HoVer) rollouts. Total spend for Table 2 was **under $500**: GEPA $86, GEPA-Merge $67, MIPROv2 $76, Trace+TextGrad $172 (E.3, p.26).

**GRPO configuration (E.4, pp.25–26).** Compound-system GRPO used the implementation of Ziems et al. (2025): group size 12, 4 instances/step (effective batch 48), LoRA r=16 α=64 dropout 0.05, bf16, targets [q,k,v,o,up,down,gate], lr 1e-5, β=0.01, reward-scale normalization, grad-norm clip 0.1, grad accumulation 20, constant-with-warmup schedule, **500 steps = 24,000 rollouts**, validation every 20 steps with early stopping, on 1×H100/A100 80GB plus separate inference GPUs. Single-module GRPO used full-parameter fine-tuning: group size 16, global batch 32 (4/device × 8 GPUs), rollout micro-batch 12, FSDP2, temp 1.0, KL regularization, lr 1e-6, validation every 5 steps. Figure 11 (p.28) confirms the gap is not a LoRA artifact: "GEPA achieves a comparable performance gap relative to both full-parameter and parameter-efficient versions of GRPO."

**GEPA hyperparameters:** minibatch size `b = 3`; merge invoked at most 5 times; `D_feedback` = train split, `D_pareto` = validation split (p.26). Reflection-LM call counts (Appendix N, Table 4, p.96) — GPT-4.1-Mini / Qwen3-8B: AIME-2025 24/90, LiveBench-Math 34/38, HotpotQA 69/64, IFBench 21/17, Hover 92/50, PUPA 46/38. So the entire optimization involves **only tens of reflection calls**.

### 4.5 Instruction-vs-few-shot finding (Observation 2, p.9)

"Reflective prompt evolution enables instruction-optimization alone to outperform joint instruction and few-shot optimization." GEPA beats MIPROv2 "in all settings, achieving margins as high as **11.1%** for GPT-4.1 mini and **10.3%** for Qwen3 8B", and GEPA/GEPA+Merge "more than double the aggregate gains over baseline seen with MIPROv2 across all benchmarks and models (+13.33% and +12.19% vs +5.64% for MIPROv2)."

This is framed as a reversal of prior consensus: "While prior works such as Opsahl-Ong et al. (2024) and Wan et al. (2024) have provided compelling evidence for the effectiveness of few-shot example optimization—often outperforming instruction-based approaches—our findings suggest an exciting shift in this trend. We attribute this primarily to recent advances in the instruction-following and self-reflective abilities of LLMs, as well as the design choices in GEPA that capitalize on these improved capabilities."

Also (p.9): "in contrast to prior findings where instruction optimization yielded improvements primarily through quasi-exemplars (Wan et al., 2024), GEPA's prompts frequently contain detailed **declarative** instructions for completing the task." Corroborated by Figure 17 (p.30): "Most of GEPA's prompt tokens are used for providing instructions, whereas most of MIPROv2's prompt tokens pertain to few-shot examples."

---

## 5. GEPA as inference-time search (§5.1, pp.12–13)

**Mechanism.** Rather than optimizing for generalization, "pass[] the set of tasks to be solved (for example, a list of Pytorch modules to be converted to CUDA) as the training set to GEPA, ensuring that both `D_train` and `D_pareto` contain the full set of tasks. This way, GEPA can 'overfit' the set of tasks, iteratively proposing better solutions to every problem. We also note that this allows GEPA to apply lessons and insights extracted from rollouts for one task to other tasks." (p.12) — i.e. the Pareto frontier becomes a **cross-task lesson-transfer** device.

**`µ_f` as a dynamic knowledge injector.** "kernel development expertise—often codified in technical manuals and documentation—can be selectively surfaced by retrieving relevant manual sections based on rollout failures (e.g., compiler error messages). By using error information to make targetted retrieval queries, GEPA promotes integration of architectural best practices into prompt evolution" (p.12). Sampling stochasticity is removed by caching, "ensur[ing] that observed improvements tie closely to inference scaling through prompt updates and GEPA's diverse prompt exploration, rather than stochasticity in the model's sampling process."

**NPU kernels — NPUEval, AMD XDNA2, GPT-4o (Figure 7, p.13).** Metric = mean vector utilization (%).

| Method | Mean vector utilization |
|---|---|
| Sequential10 (up to 10 refinements w/ compiler+profiling feedback) | **4.25%** |
| Sequential10 + RAG (from technical manuals) | **16.33%** |
| Sequential10 + RAG + MIPROv2 | **19.03%** |
| GEPA Best-1 (a single GEPA prompt, **no runtime RAG**) | **26.85%** |
| GEPA Pareto (best-of-frontier) | **30.52%** |

Individual kernels reach "up to **70%** vector utilization." The headline is that GEPA *distills the manual into the prompt*, beating a system that retrieves the manual at runtime.

**CUDA kernels — KernelBench, NVIDIA V100, GPT-4o (Figure 8, p.13).** 35 tasks from the KernelBench "representative subset" spanning three difficulty levels; Sequential5 agent. `fast_p` = fraction of tasks where the generated kernel runs faster than `p ×` PyTorch-eager. GEPA "boosts GPT-4o's close-to-0% `fast_1` score to above **20%** with increasing search budget" (x-axis to ~3000 rollouts; `fast_0.5` plotted to ~0.5).

The authors are explicit these are preliminary: "these are early results and warrant further systematic study."

**Adversarial prompt search (§5.2, pp.12–14) — the mirror-image application.** Reward is **inverted**: "the optimizer proposes prompt edits to include additional information like trivia that minimize task performance (pass@1), while requiring that prompts do not contradict the task and still contain all information needed to solve it." Evolution pool = AIME 2022–2024; evaluation on AIME-2025 (30 problems) with **GPT-5 Mini**, 5 runs per problem (150 generations). Result: pass@1 collapses from **76% (clean) to 10% (adversarial)**. The evolved attack prompt injects irrelevant trivia (honey never spoils; the Nile is 6,650 km; dolphins sleep with one eye open) and induces GPT-5 Mini to emit the literal placeholder `### <final answer>` instead of an answer.

---

## 6. Merge / system-aware crossover

**How it works** (Algorithms 3–4 above; Appendix D.1, p.23): "candidates are merged only if they share a common ancestor but have optimized disjoint sets of prompts (complementary strategies), are pareto-optimal, and both candidates improve upon the aggregate performance of the ancestor. GEPA routinely checks if the pool has 2 such candidates, invoking merge when identified. **These strict lineage conditions mean merge occurs sparsely.**"

The four gating conditions, in order: (i) not direct ancestors of each other; (ii) this `(i,j,a)` triple not already tried; (iii) `S[a] ≤ min(S[i], S[j])`; (iv) `DESIRABLE` — at least one module where exactly one lineage diverged from the ancestor. Then the merged child takes, per module, whichever lineage edited it; where both edited the same module, the higher-scoring parent's version wins.

**When it helps (Observation 5, p.11).** "GEPA+Merge can outperform GEPA by as much as **5%**, providing an aggregate **2%** additional improvement over the already strong performance established by GEPA." Attribution: "the ability of GEPA+Merge to identify distinct optimization lineages, that have learnt complementary strategies (by evolving distinct modules), and merging them by picking the best version of different modules from each of these lineages."

**Failure rate / when it hurts.** Model-dependent: "While in our analysis, we found GEPA+Merge works especially well for GPT-4.1 Mini, it lead to performance degradation when used with Qwen3 8B. Even Qwen3 8B benefits from Merge on **one out of four tasks**." (p.11) So Merge's failure rate on Qwen3 8B is **3/4 tasks**, and the damage can be severe (IFBench 38.61 → 28.23, i.e. −10.38 vs GEPA and −8.67 vs the *unoptimized* baseline; aggregate 54.85 → 52.40). On GPT-4.1 Mini it is +1.14 aggregate but still loses on HotpotQA (69.00 → 65.67).

**Diagnosed cause.** "We attribute these discrepancies to the way the rollout budget is allocated between reflective mutation and crossover, and the timing of invocation of the crossover strategy. In our experiments, we fixed the same hyperparameters for GPT-4.1 Mini and Qwen3 8B, leading to suboptimal choice for Qwen3 8B. Intuitively, crossover would provide the maximum benefit, when there are independent lineages that perform well. Hence, the hyperparameters should be chosen such that Merge is invoked once the optimization tree has evolved sufficiently different lineages." (p.11)

Note the structural precondition: Merge is a no-op on single-module systems, which is why AIME-2025 and LiveBench-Math have **identical** GEPA and GEPA+Merge numbers in both tables (32.00/51.95 and 59.33/64.13).

---

## 7. Ablations & analyses

### 7.1 Pareto vs greedy vs beam (Observation 3, Table 3, p.10 — Qwen3 8B, evolution harness held fixed)

| Qwen3 8B | HotpotQA | IFBench | Hover | PUPA | Aggregate | Improvement |
|---|---|---|---|---|---|---|
| Baseline | 42.33 | 36.90 | 35.33 | 80.82 | 48.84 | — |
| SelectBestCandidate (TextGrad-style) | 58.33 | 30.44 | 45.33 | 85.45 | 54.89 | +6.05 |
| BeamSearch (N=4) (APO-style) | 57.33 | 36.39 | 41.00 | 81.08 | 53.95 | +5.11 |
| **GEPA (Pareto)** | **62.33** | **38.61** | **52.33** | **91.85** | **61.28** | **+12.44** |

"GEPA with Pareto-based sampling outperforms the BeamSearch strategy by upto **11.33%**, and SelectBestCandidate strategy by up to **8.17%**, with an aggregate margin of **+7.33%** and **+6.4%** across all benchmarks, respectively." (p.10) Note SelectBestCandidate is *worse than baseline* on IFBench (30.44 vs 36.90) — greedy selection can actively harm.

Qualitative diagnosis via search trees (Figure 6, p.10): "always choosing the current best candidate gives immediate improvement but quickly stalls, wasting rollouts on a single candidate"; the Pareto method "was able to generate a balanced search tree, finding a better performing program within the same budget."

### 7.2 Feedback ablations

**There is no dedicated `µ_f` ablation in the paper.** No experiment removes `feedback_text` and keeps everything else fixed. The closest evidence is indirect: (a) the two benchmarks without engineered feedback (AIME-2025, LiveBench-Math) show GEPA's weakest relative results; (b) TextGrad and Trace, which use textual gradients but different selection and no `µ_f`-style evaluation traces, land at +6.11 and +3.27 vs GEPA's +12.19. This is the most conspicuous methodological gap in the evaluation, given that `µ_f` is billed as a central contribution.

### 7.3 Prompt length growth and lineage (Appendix K.1, p.34; Figure 5, p.7)

The PUPA `craft_redacted_request` lineage on GPT-4.1 Mini (validation score in parentheses), measured over the verbatim dumps:

| Node | Chars | Words | Score | Annotated change (Figure 5) |
|---|---|---|---|---|
| 0 (base) | 164 | 25 | 82.26 | "Base Instruction. Given a private user query, create a privacy-preserving request for a powerful external LLM." |
| 2 | 2966 | 437 | 90.99 | "Expanded Privacy Strategies & Task Understanding" — PII identification/generalization, query-intent analysis, reasoning explanations |
| 4 | 4467 | 635 | 94.44 | "Structured Output & Domain Best-Practices" — formalizes Reasoning/Request split, bans names/codes, requires privacy justification |
| 5 | 4829 | 671 | 94.67 | "Detailed, Transparent Transformation Rationale" — location/name removal, professional scenarios, fictional characters |
| 11 | 6397 | 892 | 97.60 | "Rigorous, Exhaustive Protocol" — strict stepwise PII abstraction, bans partial redaction, "zero leakage tolerated" |

Growth is **monotone in both length and score** — a 39× character expansion from base to best. The optimization tree for this run has at least 16 nodes (0–15, with scores 81.56–97.6 visible in Figures 6/25).

**Measured size of final GEPA prompts per module** (chars/words, from Appendix L dumps):

| Benchmark / model | Modules (chars / words) |
|---|---|
| HotpotQA, GPT-4.1 Mini | create_query_hop2 3729/546; final_answer 4135/598; summarize1 2403/356; **summarize2 78/6 (unchanged from base)** |
| HotpotQA, Qwen3 8B (+Merge) | 1396/203; 2371/325; 5502/748; 2390/341 |
| IFBench, GPT-4.1 Mini (+Merge) | 2267/335; 4343/623 |
| IFBench, Qwen3 8B | 540/81; 1221/165 |
| HoVer, GPT-4.1 Mini (+Merge) | 2695/387; 3195/470; **summarize1 64/6 (unchanged)**; 2476/352 |
| HoVer, Qwen3 8B | 1828/264; 1151/167; 1314/184; 1374/184 |
| PUPA, GPT-4.1 Mini (+Merge) | craft_redacted_request 6397/892; **respond_to_query 125/21 (≈unchanged)** |
| PUPA, Qwen3 8B | 3849/501; 1350/180 |
| KernelBench CUDA (Appendix M.2) | 10398/1387 — includes a full worked `load_inline` elementwise-add example and `ModelNew` class |
| NPUEval (Appendix M.1, Figure 27) | image-only figure; text did not extract |

Two structural findings from these measurements: (i) **round-robin does not guarantee every module gets improved** — three modules were left at (or within a few characters of) their base prompt, meaning GEPA sometimes concentrates all gains in one or two modules; (ii) content is overwhelmingly **declarative instruction plus hoarded domain facts**, not exemplars, consistent with the Observation-2 narrative and with the meta-prompt's explicit instruction to capture "niche and domain specific factual information."

### 7.4 Prompt size / test-time token cost (Observation 4, p.11; Figures 17–18, pp.30–31)

"prompts produced by GEPA and GEPA+Merge are up to **9.2× shorter** than those from MIPROv2" (p.11). Figure 17: "GEPA consistently produces prompts that are around less than **33%** of the size of MIPROv2's prompts, while getting higher performance." And a general trend: "in aggregate, optimizers that achieve higher performance tend to produce shorter prompts."

Figure 18 token counts and per-benchmark reduction factors vs MIPROv2:

| Model | Benchmark token counts (MIPROv2) | GEPA reduction | GEPA+Merge reduction |
|---|---|---|---|
| GPT-4.1 Mini | 7511.8 / 15454.0 / 2637.0 / 5567.0 / 6389.0 | 4.3×, 7.2×, 2.1×, 2.4×, 5.3× | 4.8×, 7.5×, 2.1×, 3.3×, 5.1× |
| Qwen3 8B | 6259.0 / 10071.0 / 2438.0 / 5252.0 / 7275.0 | 4.9×, 4.7×, 6.4×, 3.7×, 6.0× | 4.5×, 3.8×, **8.0×**, 2.8×, **9.2×** |

Why it matters (p.11): "not only reducing runtime cost for downstream tasks (as all API-providers meter the input tokens), but also decreasing latency and improving the overall efficiency of LLM-serving systems." Figure 17 uses aggregate prompt token count explicitly as a **cost proxy** on the x-axis, plotting score-vs-cost Pareto curves in which GEPA dominates MIPROv2 and the SelectBestCandidate ablation on both axes.

### 7.5 Generalization gap (Figure 16, pp.30–31)

Protocol from Wan et al. (2024): gap = final test performance − best achieved validation performance (negative = overfit to validation).

| GPT-4.1 Mini | Baseline | MIPROv2 | GEPA | GEPA+Merge |
|---|---|---|---|---|
| HotpotQA | −2.33 | −4.33 | **+0.67** | −1.66 |
| HoVer | 1.66 | −3.00 | −2.66 | **4.00** |
| PUPA | −2.62 | −4.69 | −1.83 | −1.14 |

| Qwen3 8B | Baseline | MIPROv2 | GEPA | GEPA+Merge |
|---|---|---|---|---|
| HotpotQA | −4.00 | −2.34 | **2.33** | **2.33** |
| HoVer | 0.33 | 2.33 | −0.67 | −0.66 |
| PUPA | 0.54 | −2.40 | **1.46** | −1.72 |

MIPROv2 is negative in 5 of 6 cells; GEPA variants are positive in 5 of 12 and generally closer to zero. Authors' reading (p.9): "reflectively evolved instructions now demonstrate a lower generalization gap, underscoring both advancements in model capabilities and the benefits of GEPA's design", with the hypothesis (Figure 16 caption) that "this difference may be due to the improving capabilities of the underlying LLMs, as more recent models are both better at adhering to instructions and capable of reflecting on their outputs." Fair caveat: the gaps are small relative to run-to-run noise and no variance is reported anywhere in the paper.

### 7.6 Other analyses present

- Figure 10 (p.27): final test performance, aggregate and per-benchmark.
- Figure 11 (p.28): GEPA vs **full-parameter** GRPO on 2-hop HoVer — same relative gap as vs LoRA GRPO.
- Figures 12–15 (pp.29–30): rollout-vs-score curves per benchmark per model/setting, both validation and test traces.
- Figures 19–26 (pp.32–41): full optimization/lineage trees per benchmark per model, with node scores.
- Appendix L (pp.41–89): full-length final prompts for all 4 multi-module benchmarks × 2 models, alongside MIPROv2's; "MIPROv2 optimized prompts contain upto 4 few-shot examples for each task. We provide just the first" (p.41) — the MIPROv2 dumps are truncated for space.
- Appendix N (p.96): reflection-LM call counts (Table 4).

---

## 8. Limitations

### 8.1 Stated by the authors

The paper has **no dedicated Limitations section** (the only "Criticisms and Limitations" string in the extract, near line 5657, is inside a MIPROv2 few-shot demo about David Easton's political-systems theory, not the authors' own). Limitations appear inline:

1. **Merge is hyperparameter-fragile and model-dependent** — degrades Qwen3 8B on 3 of 4 tasks; "the optimal budget allocation between mutation and crossover, as well as *when* to invoke merge needs further study" (p.11).
2. **Validation evaluation dominates the budget** — "Since tracking candidates' validation performance accounts for majority of GEPA's rollout budget" (p.9); mitigation proposed but not implemented.
3. **Inference-time-search results are preliminary** — "these are early results and warrant further systematic study" (p.12).
4. **AIME-2025 loss to GRPO** is reported without concealment (Table 1 caption: "on all benchmarks except AIME").
5. **Baseline configuration disparity acknowledged** — identical hyperparameters used across both models, "leading to suboptimal choice for Qwen3 8B" (p.11).

### 8.2 Unstated / structural limitations

1. **Requires a strong reflection LM.** The whole method is a bet on the reflector's diagnostic ability. There is no ablation over the reflection LM, no report of which model performed reflection in each run, and no result with a weak reflector. GEPA-Qwen-Opt shows Qwen3 8B *can* be the optimizer, but nothing bounds how far down the capability curve this survives.
2. **`µ_f` engineering is manual and per-task.** Four benchmarks got hand-written feedback modules requiring access to metric internals. There is no automation, no ablation isolating its contribution, and the two tasks without it are GEPA's weakest.
3. **Per-instance scores on a held-out validation set are mandatory.** Algorithm 2 needs the full `|P| × |D_pareto|` score matrix. Tasks with only aggregate/pooled metrics (e.g. corpus-level BLEU, human A/B preference at the aggregate level, safety pass/fail at fleet level) cannot use Pareto selection at all.
4. **It does not optimize weights.** Despite Eq. 1 admitting `Θ`, GEPA freezes weights entirely. The paper's title claim is that prompt evolution *outperforms* RL at equal rollout budget — not that it subsumes it. Nothing here improves the base model's latent capabilities, and gains are bounded by what the frozen model can do when instructed well.
5. **Learning that cannot be verbalized is out of reach.** Any skill acquisition that resists natural-language articulation — fine-grained calibration, low-level token-distribution shaping, tacit motor-like policies, arithmetic reliability — cannot be transmitted through an instruction string. AIME-2025 is the visible instance: GEPA loses to GRPO there, and it is exactly the task where the deficit is raw reasoning capability rather than task-format understanding, and where no engineered `µ_f` existed.
6. **Prompt growth as an unbounded cost.** The PUPA lineage grew 164 → 6397 chars; the CUDA prompt reached 10398 chars. GEPA is shorter *than MIPROv2*, but absolute prompt size grows monotonically with optimization, adding permanent per-inference token cost and risking context saturation on long-context tasks. No stopping criterion or length regularizer is described.
7. **Overfitting to the validation set is structurally invited.** The returned candidate is "Φ* maximizing average score on D_pareto" (Alg. 1 line 21), with `n_pareto` = 111–300. The generalization-gap analysis suggests this is benign in practice, but the mechanism is selection on a small validation set.
8. **No variance / seed reporting.** Single runs throughout (except AIME's 5 repeats per problem); several headline margins (0.69 on LiveBench-Math, +1.14 for Merge) are within plausible noise. Merge itself uses a seeded stochastic sampler.
9. **Round-robin leaves modules unimproved.** Measured: 3 modules across the dumps remain at their base prompt. A learned or feedback-driven module-selection policy is an obvious missing ablation.
10. **Rollout-parity is not compute-parity.** A GEPA rollout of a 4-module multi-hop program with retrieval is not comparable in wall-clock or FLOPs to a GRPO generation, and GRPO additionally pays gradient compute. The comparison is defensible as "expensive-rollout accounting" but should not be read as a compute benchmark.
11. **Cross-benchmark contamination risk is unaddressed** for AIME 2022–2024 as training pool against AIME-2025 test, and for LiveBench-Math retrieved July 2025.

---

## 9. Exact quotable claims

1. p.1 (Abstract): "Across six tasks, GEPA outperforms GRPO by 6% on average and by up to 20%, while using up to 35x fewer rollouts."
2. p.1 (Abstract): "GEPA also outperforms the leading prompt optimizer, MIPROv2, by over 10% (e.g., +12% accuracy on AIME-2025), and demonstrates promising results as an inference-time search strategy for code optimization."
3. p.1 (Abstract): "We argue that the interpretable nature of *language* often provides a much richer learning medium for LLMs, compared to policy gradients derived from sparse, scalar rewards."
4. p.3: "they contain nothing but the instructions of each LLM module, the resulting LLM reasoning chains, tool calls, and potentially the internal workings of the reward function (e.g., compiler error messages, before they are collapsed into scalar rewards)."
5. p.3: "we argue that *algorithms that learn deliberately in natural language by reflecting on these trajectories* can make more effective use of the strong language priors that LLMs have, compared with standard RL approaches."
6. p.4: "rollouts—concretely, invocations of Φ plus evaluation by µ—are often computationally, monetarily, or timewise expensive."
7. p.6: "The text that LLMs produce is the *execution trace* of the AI system. The text that the environment produces to compute the reward (e.g. compiler error messages before giving reward 0) is the *evaluation trace*."
8. p.7: "A naive approach is to always select the best-performing candidate, but this often traps the optimizer in a local optimum: once a dominant strategy is found, it becomes difficult to surpass, and the optimizer exhausts its budget without learning new, potentially better strategies."
9. p.9: "beating GRPO (24,000 rollouts) by up to 19% while using up to 35× fewer rollouts. It reaches optimal test performance with 4–35× fewer rollouts and exceeds GRPO on 5 out of 6 tasks by 19.0%, 2.73%, 13.66%, 5.19% and 0.7%."
10. p.9: "The majority of GEPA's rollout budget is spent on validation, where scores are utilized solely for candidate selection and not for producing learning signals. If we restrict the analysis to train set rollouts, GEPA requires only 79 to 737 rollouts to reach optimal performance."
11. p.9: "in contrast to prior findings where instruction optimization yielded improvements primarily through quasi-exemplars (Wan et al., 2024), GEPA's prompts frequently contain detailed *declarative* instructions for completing the task."
12. p.11: "prompts produced by GEPA and GEPA+Merge are up to 9.2× shorter than those from MIPROv2, representing a substantial improvement in efficiency, alongside performance improvements."
13. p.11: "While in our analysis, we found GEPA+Merge works especially well for GPT-4.1 Mini, it lead to performance degradation when used with Qwen3 8B. Even Qwen3 8B benefits from Merge on one out of four tasks."
14. p.12: "This way, GEPA can 'overfit' the set of tasks, iteratively proposing better solutions to every problem. We also note that this allows GEPA to apply lessons and insights extracted from rollouts for one task to other tasks."
15. p.23: "candidates are merged only if they share a common ancestor but have optimized disjoint sets of prompts (complementary strategies), are pareto-optimal, and both candidates improve upon the aggregate performance of the ancestor... These strict lineage conditions mean merge occurs sparsely."
16. p.15 (Conclusion): "Our results across benchmarks and models suggest that language-based reflection can offer a scalable strategy for optimizing complex real-world AI workflows, especially in resource-constrained settings."

---

## 10. Open problems the authors name

1. **Adaptive merge scheduling and mutation/crossover budget allocation** (Observation 5, p.11): "the hyperparameters should be chosen such that Merge is invoked once the optimization tree has evolved sufficiently different lineages. We propose the study of such adaptive techniques as future work."
2. **Cheaper candidate selection via smaller or dynamic validation subsets** (Observation 1, p.9): "sample efficiency can be further improved by evaluating on a smaller validation set or by tracking scores on dynamically selected validation subsets instead of the full set—both of which we propose as directions for future work."
3. **GEPA as a general inference-time search / domain-adaptation technique** (§5.1, p.12): "leveraging GEPA for inference-time search, particularly when coupled with domain specific textual feedback, could generalize to other code generation and domain adaptation tasks—a direction we leave for future work."

### 10.1 On combining with RL and weight-space learning — precise status

The paper's stance is **comparative, not integrative**. Observation 1's title is "Reflective Prompt Evolution is highly sample-efficient and can outperform **weight-space** reinforcement learning" (p.9) — weight-space RL is positioned as the *baseline being beaten*. Eq. 1/2 include `Θ` explicitly, and the stated reason is comparability: "to enable comparisons between optimization algorithms that operate in different parameter spaces (e.g., GEPA vs. GRPO)" (p.4). The related-work section notes prior hybrids — "globally aligned local rewards per module. GEPA combines global rewards with environment textual feedback" (p.14, discussing multi-module GRPO / Ziems et al. 2025, whose implementation GEPA's GRPO baseline uses) — and the reference list includes "module grpo: Composing policy gradients and prompt optimization for language model programs, 2025."

**There is no explicit "combine GEPA with RL" or "apply reflective evolution to weight space" future-work statement in the paper.** The nearest openings the authors leave are: (a) the shared `⟨Π, Θ⟩` formalism, which makes joint prompt+weight optimization expressible but unexplored here; (b) the observation that GEPA's train-rollout consumption is 79–737 rollouts, leaving most of an RL-scale budget unspent — an obvious sequential or interleaved hybrid the paper does not run; (c) the note that human-written explanations can serve as `feedback_text` (p.7), pointing toward reflective learning from human feedback as an alternative to RLHF-style scalar modelling. These are inferences about where the work points, not claims the authors make.
