# Technical Dossier — *SkillOpt: Executive Strategy for Self-Evolving Agent Skills*

**Source:** `arXiv:2605.23904v2 [cs.AI]`, 25 May 2026 (dated "May 2026"), 27 pages.
**Authors:** Yifan Yang¹\*‡, Ziyang Gong²\*, Weiquan Huang³\*, Qihao Yang²\*, Ziwei Zhou⁴\*, Zisu Huang⁴\*, Yan Li², Xuemei Gao¹, Qi Dai¹, Bei Liu¹, Kai Qiu¹, Yuqing Yang¹, Dongdong Chen¹, Xue Yang²‡, Chong Luo¹ — ¹ Microsoft, ² Shanghai Jiao Tong University, ³ Tongji University, ⁴ Fudan University. (\* equal contribution, ‡ corresponding.)
**Code:** `https://aka.ms/SkillOpt`. **Correspondence:** yifanyang@microsoft.com, yangxue2019-sjtu@sjtu.edu.cn
**Extracted text used as primary source:** `/Users/liuzhanyu/workspace/RLProject/docs/_extract/SkillOpt.txt`
**Cross-checked against:** `/Users/liuzhanyu/.claude/skills/skillopt-agentic-mc/chapters/ch01…ch05` (consistent; discrepancies noted in §12).

---

## 1. Problem formulation

### 1.1 Skill as external state

A skill `s` is "a natural-language policy inserted into the agent context before execution" (p.4, §3.1). Placement is harness-dependent:

- **Direct chat:** prepended to the system or developer instruction.
- **Tool-use harnesses:** it "becomes persistent procedural memory" — concretely, rendered to a per-task `SKILL.md` in the workspace (p.9).

The skill packages "procedures, domain heuristics, tool policies, output constraints, and failure modes, letting a frozen agent adapt through external text" (p.1, §1).

### 1.2 Notation

| Symbol | Meaning |
|---|---|
| `M` | the **frozen target model** whose behavior is adapted (also called *student* / *training model* / *task-execution model*) |
| `O` | the **optimizer model** (also *teacher*); reads rollout evidence, proposes edits; **training-time only** |
| `h` | the **harness** (direct chat, Codex CLI, Claude Code CLI) |
| `x` | a task |
| `s` | the skill document (the trainable object) |
| `τ(s)` | trajectory produced under skill `s` |
| `r(s) ∈ [0,1]` | scalar score |
| `L_t` | **edit budget** at step `t` — the textual learning rate |
| `B` | rollout batch size |
| `A` | accumulation factor (rollout batches reflected separately then merged into one update) |
| `B_m` | reflection minibatch size |
| `E` | epochs |
| `C` | selection-score cache, keyed by skill hash |
| `B` (buffer) | epoch-local rejected-step buffer (name collides with batch size in the paper) |
| `m_meta` | optimizer-side meta skill |
| `s_cur`, `s_best` | current skill / best validation-gated skill |

**Execution model (Eq. 1, p.4):**

```
(τ(s), r(s)) = h(M, x, s),    r(s) ∈ [0, 1]
```

### 1.3 Objective

Given splits `D_tr`, `D_sel`, `D_test`, SkillOpt uses `D_tr` to generate a candidate set `C(D_tr)`, selects on `D_sel`, reports on `D_test` (Eqs. 2–3, p.5):

```
s*_sel = argmax_{s ∈ C(D_tr)}  (1/|D_sel|) Σ_{x∈D_sel} r(s)          (2)

Test(s*_sel) = (1/|D_test|) Σ_{x∈D_test} r(s*_sel)                    (3)
```

"The training split supplies experience, the selection split gates updates, and the test split is used only for final reporting" (p.5).

### 1.4 What is accessible / what is frozen

Accessible to the loop: task metadata, messages, tool calls, observations, command outputs, final answers, verifier feedback, and "benchmark-specific context such as spreadsheet previews, document references, or compact execution traces" (p.5, §3.2). In the Codex harness specifically, a `codex_trace_summary.txt` is read back and injected into teacher reflection "so the optimizer learns from *what the agent actually did*, not just its final answer" (p.9).

Frozen throughout: target model weights, backend, harness, and benchmark evaluator (p.20, Appendix C). "The task-execution model only receives the current skill and the benchmark task; it does not see the optimizer prompts" (p.20, Appendix A).

**Optimizer state** (p.5): current skill, best validation-gated skill, cached skill hashes, epoch-local rejected-step buffer, optional slow/meta-update state. **Only `best_skill.md` is exported.**

### 1.5 Splits — with an internal inconsistency

- Main text (p.8): "deterministic train/selection/test splits derived from the same dataset seed (`split_seed=42`)"; the selection split is used **only** to accept/reject edits; all reported scores on the disjoint held-out test split.
- **Table 2 caption (p.8): "Panel (a) fixes the split to 4:1:5 train/selection/test"** and "the 100% row reuses the completed 4:1:5 split-ratio run".
- **Appendix C (p.20): "a default 2:1:7 split when no benchmark-specific split is stated"**; and Appendix C ablation protocol (p.21): "The train-size ablation fixes the train/selection/test split to 2:1:7."

The 4:1:5 (Table 2) vs. 2:1:7 (Appendix C) statements describe the *same* train-size ablation and contradict each other. The ratio used for the headline Table 1 runs is never stated unambiguously.

**Benchmark-specific pools** (p.8): LiveMathematicianBench — 35 training items per epoch with rollout batch 200 (i.e. batch > pool, so items are necessarily re-sampled); ALFWorld — 39 training tasks, 140 selection environments, 134 test environments (selection split ~3.6× larger than train).

---

## 2. The deep-learning analogy, operationalized

The paper insists the analogy is load-bearing: "The deep-learning analogy is operational rather than decorative" (p.2).

### 2.1 The mapping (Fig. 1, p.2; §3, pp.4–6; §C.4, p.27)

| Deep-learning concept | SkillOpt counterpart | Text-space mechanism | Implementation numbers |
|---|---|---|---|
| **Parameter / weights** | skill document `s` | a single markdown file, `best_skill.md` | 300–2,000 tokens deployed; measured 379–1,995 tok final, median ≈ 920 |
| **Gradient direction** | trajectory-derived edit direction | optimizer model reads failure + success minibatches, emits structured `append` / `insert_after` / `replace` / `delete` ops in strict JSON | ≤ `L` atomic edits per minibatch; 16 analyst workers in parallel; up to 3 refinement rounds per minibatch |
| **Learning rate** | edit budget `L_t` | max number of edits applied at step `t`, enforced by ranking then clipping the merged pool | default `L_t = 4`, cosine decay, **floor `L_t = 2`**; swept over {1, 2, 4, 8, 16} |
| **LR schedule** | edit-budget schedule | constant, linear, cosine, "autonomous" | default cosine ("larger edits and decays toward smaller consolidation steps", p.5) |
| **Batch / minibatch** | rollout batch `B` / reflection minibatch `B_m` | `B` controls evidence per update; `B_m` controls the granularity of each reflection call | default `B = 40` per step, `B_m = 8`, merge batch size 8; accumulation factor `A` batches reflected separately and merged |
| **Epochs** | epochs `E` | passes over shuffled `D_tr` | default `E = 4` |
| **Validation set / early stopping** | held-out selection gate on `D_sel` | candidate skill re-evaluated with the *same* frozen `M` and harness; accepted only on **strict** improvement | strictly `>` current selection score; **ties rejected** |
| **Momentum** | epoch-wise slow update | compares the *same* sampled training items under previous-epoch vs. current skill; writes longitudinal guidance into a protected region | 20 sampled tasks per epoch; fires only from `e ≥ 2` |
| **Optimizer state / meta-learning** | optimizer-side meta skill `m_meta` | summarizes which edit patterns helped/were rejected/persisted; prepended to future optimizer prompts | never shipped with the deployed skill; fires from `e ≥ 2` |
| **Negative examples / regularization** | rejected-edit buffer | epoch-local record of tried edits + the score drop they caused, fed to later reflection calls | reset at each epoch start |
| **Caching / dedup** | selection-score cache `C` | skill hash → selection score, avoids re-evaluating identical candidates | keyed by `Hash(s)` |

### 2.2 Why boundedness is the crux

Intro, p.2: "if consecutive skill revisions move too far or in inconsistent directions, rejected edits and previous accepted edits no longer provide a meaningful optimization history. With bounded, validation-gated updates, each revision remains close enough to the last one that later optimizer calls can learn from what helped, what failed, and what should be preserved."

§3.4, p.5: "Unbounded rewrites can erase useful rules, introduce incompatible instructions, or overfit to a local failure; bounded updates preserve continuity while still allowing the skill to acquire new procedures."

### 2.3 Separation of timescales, enforced textually

The protected region `<!-- SLOW_UPDATE_START -->` … `<!-- SLOW_UPDATE_END -->` (Appendix C.2/C.3) is **off-limits to every step-level prompt**; only the epoch-boundary slow update may rewrite it, and even that rewrite passes the same `D_sel` gate. Every step-level prompt (failure analyst, success analyst, all three merges) carries an explicit "Do NOT propose any edits that target, modify, or delete content within these markers" clause.

### 2.4 Two edit modes

- **`patch` mode (default):** localized ops — append, insert_after, replace, delete.
- **`rewrite_from_suggestions` mode:** the selected suggestions condition a full skill rewrite.

---

## 3. Algorithm

### 3.1 Verbatim pseudocode (Algorithm 1, p.22)

```
Require: Frozen training model M, optimizer model O, harness h,
         splits D_train, D_sel, D_test, initial skill s_0, epochs E,
         edit-budget schedule L_t, rollout batch size B,
         accumulation factor A, reflection minibatch size B_m
Ensure:  Best validation-gated skill s_best and held-out test score

 1: s_cur ← s_0;  s_best ← s_0;  C ← ∅;  B ← [ ];  m_meta ← ∅
 2: score_cur ← Evaluate(M, h, s_0, D_sel);  score_best ← score_cur
 3: C[Hash(s_0)] ← score_cur
 4: for e = 1 to E do
 5:   Shuffle D_train into rollout batches; reset B ← [ ]
 6:   for each optimization step in epoch e do
 7:     Collect A rollout batches by executing h(M, x, s_cur) for sampled tasks x
 8:     Split rollout evidence into failures and successes, then into minibatches of size B_m
 9:     Ask O to analyze failure minibatches and produce failure patch proposals
10:     Ask O to analyze success minibatches and produce success patch proposals
11:     Ask O to merge failure proposals, merge success proposals, and perform a
        final failure-prioritized merge
12:     Ask O to rank merged edits and keep at most L_t edits
13:     Apply the selected edits to obtain a candidate skill s̃
14:     if Hash(s̃) ∈ C then
15:       score_cand ← C[Hash(s̃)]
16:     else
17:       score_cand ← Evaluate(M, h, s̃, D_sel)
18:       C[Hash(s̃)] ← score_cand
19:     end if
20:     if score_cand > score_cur then
21:       s_cur ← s̃;  score_cur ← score_cand
22:       if score_cand > score_best then
23:         s_best ← s̃;  score_best ← score_cand
24:       end if
25:     else
26:       Add rejected edits and observed failure patterns to B
27:     end if
28:   end for
29:   if e ≥ 2 and slow update is enabled then
30:     Compare the same sampled tasks under the previous and current epoch-end skills
31:     Ask O for protected longitudinal guidance; validate the injected guidance through D_sel
32:   end if
33:   if e ≥ 2 and optimizer memory is enabled then
34:     Ask O to update m_meta for future edit generation and selection
35:   end if
36: end for
37: score_test ← Evaluate(M, h, s_best, D_test)
38: return s_best, score_test
```

### 3.2 Step-by-step semantics

**(a) Rollout sampling (forward pass, §3.2).** `A` rollout batches of size `B` are executed by `h(M, x, s_cur)`. "This batch is the evidence unit: small batches update quickly but noisily, while larger batches expose more recurring patterns before the skill changes." Accumulation "decoupl[es] execution throughput from update frequency."

**(b) Reflection batching (backward pass, §3.3).** Failures and successes are **separated first**, then each group partitioned into minibatches of size `B_m`. Rationale: "single trajectories often produce anecdotal fixes, while minibatches expose reusable procedural errors: the agent consistently searches the wrong source, writes an answer in the wrong format, or fails to verify a tool result." Failure minibatches → missing/corrective rules. Success minibatches → preserve what works.

**(c) Optimizer-model prompt structure (Appendix C.2, pp.21–27).** Eight JSON-only contracts. "The prompts require JSON outputs so that edits can be parsed, filtered, applied, and validated without manual intervention" (p.21).

| File | Role | Key contract terms |
|---|---|---|
| `analyst_error.md` | failure analysis | "identify the most important COMMON failure patterns across the batch"; classify failure type; "Edits must be generalizable; do not hardcode task-specific values"; "Only patch gaps in the skill; do not duplicate existing content"; "Produce AT MOST L edits". Output: `{batch_size, failure_summary:[{failure_type,count,description}], patch:{reasoning, edits:[…]}}` |
| `analyst_success.md` | success analysis | "Only propose patches for patterns NOT already covered"; "Focus on patterns that appear across MULTIPLE trajectories"; "Prefer reinforcing existing sections over adding new top-level sections"; AT MOST L edits. Output adds `success_patterns: [...]` |
| `merge_failure.md` | consolidate failure patches | 1 deduplicate, 2 resolve conflicts, 3 preserve unique insights, 4 **prevalent-pattern bias** ("Edits from only one patch may be discarded if task-specific"), 5 **independence** ("no two edits in the merged patch may target the same text region"), 6 `support_count`, 7 protected section |
| `merge_success.md` | consolidate success patches | "Be conservative: success-driven patches reinforce existing behavior"; keep only the most generalizable version; `support_count` |
| `merge_final.md` | failure-prioritized final merge | "FAILURE PATCHES TAKE PRIORITY … preserved unless they directly conflict with a well-supported success pattern"; "if a failure edit and success edit cover the same point, keep the failure version"; "edits that survived previous merge rounds should be given priority"; carry forward `support_count` and `source_type` |
| `ranking.md` | rank + select top-budget | Priority order: **(1) systematic impact** ("A rule that fixes 50% of failures beats one that fixes a single edge case"), **(2) complementarity**, **(3) generality**, **(4) actionability**. Output: `{reasoning, selected_indices: [...]}` (0-based, priority order) |
| `slow_update.md` | epoch-boundary longitudinal guidance | Receives prev + current epoch skill, "the same 20 training tasks rolled out under both skills, categorized into regressions, persistent failures, improvements, and stable successes", plus previous guidance. Must reflect on what "was effective / failed or backfired / blind spots". Priorities: "(1) preventing regressions, (2) fixing persistent failures, (3) reinforcing successful patterns". Must "NOT duplicate content already in the main skill body"; must address the training model directly ("When you encounter X, always do Y."). Output: `{reasoning, slow_update_content}` |
| `meta_skill.md` | optimizer coach | Writes optimizer-side memory: "Which kinds of edits tend to help… tend to be too vague, redundant, brittle, or harmful… What level of abstraction works best… What regression risks future optimizer calls should guard against." Constraint: "Address the FUTURE OPTIMIZER directly, not the training model"; "Do not output training-model-facing task instructions." Output: `{reasoning, meta_skill_content}` |

**(d) Edit proposal vocabulary (Appendix C.3, p.27).** Exactly four atomic ops in patch mode: `append` (content only), `insert_after` (target + content), `replace` (target + content), `delete` (target). Each merged edit carries `support_count` and `source_type ∈ {failure, success}`.

**(e) Aggregation / ranking.** Hierarchical, three-stage: consolidate failure edits → consolidate success edits → failure-prioritized final merge. Then a separate ranking call clips to top-`L_t`. §3.3: "This step filters duplicate, contradictory, and example-specific suggestions before the optimizer selects the final bounded update."

**(f) Bounded update.** Selected edits applied to produce candidate `s̃`. "Step-level edits cannot overwrite the protected slow-update field, so fast local changes and slower epoch-wise consolidation remain separated" (p.5).

**(g) Held-out gate acceptance criterion.** **Strict improvement; ties rejected.** Two thresholds:
- `score_cand > score_cur` → `s̃` becomes the new **current** skill.
- `score_cand > score_best` → `s̃` additionally becomes **`best_skill.md`**.

p.8: "held-out validation gating (strictly greater than the current selection score—ties are rejected)". p.12: "so ties are rejected and the deployed skill never silently drifts. This conservative criterion makes rejected edits informative negative feedback rather than hidden state."

Auditability: "every step also records an `edit_apply_report.json` containing per-edit accept/skip status, so the source of every change to `best_skill.md` is recoverable after the fact" (p.12).

**(h) Rejected-edit buffer.** Epoch-local. Stores "observed failure patterns and, for rejected steps, the edits that were tried and the score drop they caused" (p.6). Later reflection calls in the *same* epoch receive it "so the optimizer model can avoid repeating failed edits and focus on unresolved failures. This gives the loop negative feedback during training without adding inference-time cost." Reset at line 5 of each epoch.

**(i) Epoch-wise slow/meta update.** At epoch end (`e ≥ 2`), the same training items are re-sampled under previous-epoch and current skill, bucketed into **improvements / regressions / persistent failures / stable successes**; the optimizer writes "a concise longitudinal guidance block into a protected slow-update field, and this candidate is still passed through the validation gate" (p.6). The meta skill is optimizer-side only: "This meta guidance is prepended to future optimizer prompts for reflection, merging, and ranking, but it is not shipped with the target model" (p.6).

### 3.3 Harness-agnostic adapter (§3.7)

An adapter "constructs train/evaluation batches, injects the current skill into the agent context, runs the native harness, and returns scored trajectories." All three modes "consume the same `best_skill.md` file format, which is what enables the cross-harness transfer experiments" (p.9).

- **Direct chat:** single chat-completion call, skill prepended to system prompt.
- **Codex:** `codex` CLI in a **workspace-write sandbox**; skill rendered to per-task `SKILL.md`; reads back `codex_trace_summary.txt`.
- **Claude Code:** `claude` CLI, mirroring the same workspace contract.

### 3.4 Five design principles (§C.4, p.27)

1. The task-execution model is fixed; only the text skill changes.
2. Every candidate skill is evaluated on a selection split before acceptance, "which prevents unvalidated reflection from accumulating."
3. Minibatch analyses are merged hierarchically "so that the final edits represent recurring evidence rather than single examples."
4. The edit budget serves as a learning-rate analogue, "allowing larger early changes and smaller late refinements."
5. The deployed skill stays lightweight and inspectable; the optimizer-side meta skill stays separate.

### 3.5 Default hyperparameters (p.8, restated Appendix C p.20)

| Knob | Default |
|---|---|
| Epochs `E` | 4 |
| Rollout batch `B` per step | 40 |
| Reflection minibatch `B_m` | 8 |
| Analyst workers (parallel) | 16 |
| Merge batch size | 8 |
| Textual learning rate `L_t` | 4, **cosine decay, floor 2** |
| Schedules available | constant, linear, cosine, autonomous |
| Gate | strict `>` on `D_sel`; ties rejected |
| Slow-update samples/epoch | 20 |
| Meta skill | enabled, teacher-only |
| Edit mode | `patch` (alt. `rewrite_from_suggestions`) |
| Rejected-edit buffer | optional, on by default in the main config |
| Teacher refinement rounds per minibatch | up to 3 |
| Reasoning effort (both teacher and student) | **medium** |
| Split seed | `split_seed = 42` |

---

## 4. Experiments

### 4.1 Setup

**6 benchmarks** (native hard score or exact-match accuracy, held-out test splits):

| Benchmark | Ref | Task shape |
|---|---|---|
| SearchQA | [29] Dunn et al. 2017 | single-round extractive QA |
| SpreadsheetBench | [30] Ma et al. 2024 | multi-round codegen, up to **30 turns**, real `openpyxl`/`pandas` runtime, `mode=multi` |
| OfficeQA (Pro) | [31] Opsahl-Ong et al. 2026 | multi-turn tool loops, up to **24 tool calls** |
| DocVQA | [32] Mathew et al. 2021 | multimodal document QA |
| LiveMathematicianBench ("LiveMath") | [33] He et al. 2026 | mathematical **MCQ** |
| ALFWorld | [34] Shridhar et al. 2021 | embodied, up to **50 steps/episode** |

(Appendix C also lists **SealQA** — "stresses noisy retrieval" — but SealQA appears in no table; apparent leftover.)

**7 target models:** GPT–5.5, GPT–5.4, GPT–5.4-mini, GPT–5.4-nano, GPT–5.2, Qwen3.5–4B, Qwen3.6–35B-A3B.

**3 harnesses:** direct chat, Codex, Claude Code.

**52 cells** = 7 models × 6 benchmarks (42, direct chat) + 5 benchmarks × 2 harnesses for GPT–5.5 only (10). ALFWorld is omitted under Codex/Claude Code "because ALFWorld requires persistent embodied-environment interaction."

**7 baselines:** no skill; human skill (expert-written per benchmark); one-shot LLM skill (generated once from a high-level task description **by GPT–5.5**, never updated); Trace2Skill [9]; TextGrad [40]; GEPA [13]; EvoSkill [10]. "All baselines use the same target model, the same held-out test split, and the same scorer for every benchmark" (p.9). Note the asymmetric coverage: **TextGrad/GEPA/Trace2Skill appear only in direct-chat rows; EvoSkill appears only in harness rows.**

**Optimizer model:** GPT–5.5 for the main runs (Table 6 is headed "the GPT–5.5 / GPT–5.5 (student / teacher) skill runs"). Ablations in §4.2 use "GPT–5.5 as both the target and the optimizer." The qualitative ALFWorld case uses GPT–5.4-nano student with GPT–5.5 teacher.

### 4.2 Table 1 — full main results (percentages; Δ vs. the same model's No-skill row)

#### Direct chat

**GPT–5.5**

| Skill source | SearchQA | Spreadsheet | OfficeQA | DocVQA | LiveMath | ALFWorld |
|---|---|---|---|---|---|---|
| No skill | 77.7 | 41.8 | 33.1 | 78.8 | 37.6 | 83.6 |
| Human skill | 81.8 (+4.1) | 72.9 (+31.1) | 66.9 (+33.8) | 90.1 (+11.3) | 38.4 (+0.8) | 91.8 (+8.2) |
| LLM skill | 80.9 (+3.2) | 43.2 (+1.4) | 51.7 (+18.6) | 89.6 (+10.8) | 40.0 (+2.4) | 93.3 (+9.7) |
| Trace2Skill | 82.4 (+4.7) | 49.6 (+7.8) | 65.7 (+32.6) | 90.6 (+11.8) | 52.0 (+14.4) | 87.3 (+3.7) |
| TextGrad | 81.4 (+3.7) | 41.1 (−0.7) | 42.0 (+8.9) | 87.2 (+8.4) | 49.2 (+11.6) | 82.8 (−0.8) |
| GEPA | 84.8 (+7.1) | 73.6 (+31.8) | 63.9 (+30.8) | 89.1 (+10.3) | 43.2 (+5.6) | 85.8 (+2.2) |
| **SkillOpt** | **87.3 (+9.6)** | **80.7 (+38.9)** | **72.1 (+39.0)** | **91.2 (+12.4)** | **66.9 (+29.3)** | **95.5 (+11.9)** |

**GPT–5.4**

| Skill source | SearchQA | Spreadsheet | OfficeQA | DocVQA | LiveMath | ALFWorld |
|---|---|---|---|---|---|---|
| No skill | 76.9 | 41.4 | 50.0 | 77.6 | 36.8 | 75.4 |
| Human skill | 80.1 (+3.2) | 59.3 (+17.9) | 59.3 (+9.3) | 88.5 (+10.9) | 41.6 (+4.8) | 74.6 (−0.8) |
| LLM skill | 79.4 (+2.5) | 46.1 (+4.7) | 20.4 (−29.6) | 90.4 (+12.8) | 36.8 (+0.0) | 83.6 (+8.2) |
| Trace2Skill | 81.2 (+4.3) | 53.2 (+11.8) | 51.2 (+1.2) | 89.3 (+11.7) | 40.0 (+3.2) | 73.1 (−2.3) |
| TextGrad | 82.5 (+5.6) | 38.6 (−2.8) | 54.7 (+4.7) | 85.3 (+7.7) | 36.6 (−0.2) | 78.4 (+3.0) |
| GEPA | 82.4 (+5.5) | 61.1 (+19.7) | 60.4 (+10.4) | 88.3 (+10.7) | 41.6 (+4.8) | 74.6 (−0.8) |
| **SkillOpt** | **83.1 (+6.2)** | **62.5 (+21.1)** | **62.8 (+12.8)** | **91.2 (+13.6)** | **44.0 (+7.2)** | **91.0 (+15.6)** |

**GPT–5.4-mini**

| Skill source | SearchQA | Spreadsheet | OfficeQA | DocVQA | LiveMath | ALFWorld |
|---|---|---|---|---|---|---|
| No skill | 75.9 | 36.1 | 22.1 | 71.4 | 14.7 | 73.1 |
| Human skill | 77.2 (+1.3) | 42.9 (+6.8) | 45.9 (+23.8) | 85.0 (+13.6) | 28.8 (+14.1) | 56.7 (−16.4) |
| LLM skill | 76.1 (+0.2) | 36.8 (+0.7) | 36.6 (+14.5) | 86.4 (+15.0) | 28.0 (+13.3) | 65.7 (−7.4) |
| Trace2Skill | 78.6 (+2.7) | 40.7 (+4.6) | 20.9 (−1.2) | 88.5 (+17.1) | **32.8 (+18.1)** | 82.8 (+9.7) |
| TextGrad | 77.5 (+1.6) | 38.2 (+2.1) | 30.0 (+7.9) | 84.0 (+12.6) | 27.2 (+12.5) | 70.9 (−2.2) |
| GEPA | 79.4 (+3.5) | 42.5 (+6.4) | 45.3 (+23.2) | 83.7 (+12.3) | 27.2 (+12.5) | 81.3 (+8.2) |
| **SkillOpt** | **80.2 (+4.3)** | **47.5 (+11.4)** | **48.8 (+26.7)** | **90.9 (+19.5)** | **32.8 (+18.1)** | **85.8 (+12.7)** |

**GPT–5.4-nano**

| Skill source | SearchQA | Spreadsheet | OfficeQA | DocVQA | LiveMath | ALFWorld |
|---|---|---|---|---|---|---|
| No skill | 55.8 | 23.5 | 16.3 | 30.8 | 23.2 | 34.3 |
| Human skill | 66.1 (+10.3) | 41.8 (+18.3) | 46.5 (+30.2) | 73.5 (+42.7) | 16.8 (−6.4) | 29.9 (−4.4) |
| LLM skill | 62.4 (+6.6) | 33.6 (+10.1) | 12.8 (−3.5) | 71.7 (+40.9) | 20.0 (−3.2) | 65.7 (+31.4) |
| Trace2Skill | 69.9 (+14.1) | 35.4 (+11.9) | 16.3 (+0.0) | 77.3 (+46.5) | 25.6 (+2.4) | 58.2 (+23.9) |
| TextGrad | 73.4 (+17.6) | 32.5 (+9.0) | 38.7 (+22.4) | 68.3 (+37.5) | 20.8 (−2.4) | 42.5 (+8.2) |
| GEPA | 73.2 (+17.4) | 37.2 (+13.7) | 45.0 (+28.7) | 71.2 (+40.4) | 24.8 (+1.6) | 68.7 (+34.4) |
| **SkillOpt** | **74.8 (+19.0)** | **42.5 (+19.0)** | **50.0 (+33.7)** | **80.2 (+49.4)** | **27.2 (+4.0)** | **69.4 (+35.1)** |

**GPT–5.2**

| Skill source | SearchQA | Spreadsheet | OfficeQA | DocVQA | LiveMath | ALFWorld |
|---|---|---|---|---|---|---|
| No skill | 71.9 | 38.2 | 34.9 | 73.1 | 20.8 | 68.7 |
| Human skill | 75.8 (+3.9) | 48.2 (+10.0) | 55.2 (+20.3) | 89.0 (+15.9) | 23.2 (+2.4) | 56.7 (−12.0) |
| LLM skill | 73.7 (+1.8) | 38.6 (+0.4) | 14.0 (−20.9) | 87.7 (+14.6) | 25.6 (+4.8) | 68.7 (+0.0) |
| Trace2Skill | 79.2 (+7.3) | 43.9 (+5.7) | 43.0 (+8.1) | 87.7 (+14.6) | 28.8 (+8.0) | 76.9 (+8.2) |
| TextGrad | 82.6 (+10.7) | 32.1 (−6.1) | 51.1 (+16.2) | 87.2 (+14.1) | 22.4 (+1.6) | 76.1 (+7.4) |
| GEPA | 82.7 (+10.8) | 54.3 (+16.1) | 53.4 (+18.5) | 89.5 (+16.4) | 28.8 (+8.0) | 82.8 (+14.1) |
| **SkillOpt** | **83.1 (+11.2)** | **57.1 (+18.9)** | **56.4 (+21.5)** | **89.6 (+16.5)** | **36.0 (+15.2)** | **85.1 (+16.4)** |

**Qwen3.5–4B**

| Skill source | SearchQA | Spreadsheet | OfficeQA | DocVQA | LiveMath | ALFWorld |
|---|---|---|---|---|---|---|
| No skill | 68.1 | 9.3 | 14.5 | 86.9 | 22.4 | 30.6 |
| Human skill | 66.3 (−1.8) | 16.4 (+7.1) | 22.7 (+8.2) | 87.8 (+0.9) | 18.4 (−4.0) | 28.4 (−2.2) |
| LLM skill | 65.0 (−3.1) | 10.4 (+1.1) | 20.9 (+6.4) | 88.0 (+1.1) | 28.8 (+6.4) | 55.2 (+24.6) |
| Trace2Skill | 68.5 (+0.4) | 19.3 (+10.0) | 16.3 (+1.8) | 88.0 (+1.1) | 27.2 (+4.8) | 64.9 (+34.3) |
| TextGrad | 60.7 (−7.4) | 13.9 (+4.6) | 20.9 (+6.4) | 85.6 (−1.3) | 10.6 (−11.8) | 53.7 (+23.1) |
| GEPA | 68.6 (+0.5) | 16.9 (+7.6) | 22.7 (+8.2) | 85.1 (−1.8) | 28.8 (+6.4) | 60.4 (+29.8) |
| **SkillOpt** | **71.2 (+3.1)** | **23.9 (+14.6)** | **29.7 (+15.2)** | **89.0 (+2.1)** | **52.0 (+29.6)** | **81.3 (+50.7)** |

**Qwen3.6–35B-A3B**

| Skill source | SearchQA | Spreadsheet | OfficeQA | DocVQA | LiveMath | ALFWorld |
|---|---|---|---|---|---|---|
| No skill | 72.7 | 38.2 | 45.9 | 87.6 | 31.2 | 59.7 |
| Human skill | 74.1 (+1.4) | 44.3 (+6.1) | 41.9 (−4.0) | 88.4 (+0.8) | 29.6 (−1.6) | 44.8 (−14.9) |
| LLM skill | 72.6 (−0.1) | 42.9 (+4.7) | 46.5 (+0.6) | 88.4 (+0.8) | 24.8 (−6.4) | 60.4 (+0.7) |
| Trace2Skill | 75.4 (+2.7) | 33.2 (−5.0) | 32.0 (−13.9) | 90.4 (+2.8) | 29.6 (−1.6) | 70.9 (+11.2) |
| TextGrad | 76.4 (+3.7) | 22.9 (−15.3) | 33.7 (−12.2) | 84.5 (−3.1) | 7.2 (−24.0) | 67.9 (+8.2) |
| GEPA | 75.8 (+3.1) | 45.4 (+7.2) | 43.6 (−2.3) | 88.0 (+0.4) | 31.2 (+0.0) | 73.9 (+14.2) |
| **SkillOpt** | **80.3 (+7.6)** | **47.5 (+9.3)** | **47.1 (+1.2)** | **91.4 (+3.8)** | **41.6 (+10.4)** | **82.1 (+22.4)** |

#### Codex harness (GPT–5.5 only)

| Skill source | SearchQA | Spreadsheet | OfficeQA | DocVQA | LiveMath | ALFWorld |
|---|---|---|---|---|---|---|
| No skill | 81.8 | 27.5 | 38.3 | 87.2 | 35.2 | – |
| Human skill | 84.1 (+2.3) | 50.7 (+23.2) | 40.0 (+1.7) | 88.8 (+1.6) | 48.8 (+13.6) | – |
| LLM skill | 83.4 (+1.6) | 25.0 (−2.5) | 34.3 (−4.0) | 89.8 (+2.6) | 45.6 (+10.4) | – |
| EvoSkill | 61.4 (−20.4) | 67.5 (+40.0) | 42.4 (+4.1) | 89.3 (+2.1) | 63.2 (+28.0) | – |
| **SkillOpt** | **87.3 (+5.5)** | **85.0 (+57.5)** | **51.1 (+12.8)** | **92.2 (+5.0)** | **78.4 (+43.2)** | – |

#### Claude Code harness (GPT–5.5 only)

| Skill source | SearchQA | Spreadsheet | OfficeQA | DocVQA | LiveMath | ALFWorld |
|---|---|---|---|---|---|---|
| No skill | 81.9 | 22.1 | 57.6 | 86.6 | 40.8 | – |
| Human skill | 83.7 (+1.8) | 37.1 (+15.0) | 66.3 (+8.7) | 88.0 (+1.4) | 44.0 (+3.2) | – |
| LLM skill | 82.4 (+0.5) | 37.1 (+15.0) | 56.4 (−1.2) | 89.6 (+3.0) | 44.8 (+4.0) | – |
| EvoSkill | 84.0 (+2.1) | 75.0 (+52.9) | 70.3 (+12.7) | 87.2 (+0.6) | 52.0 (+11.2) | – |
| **SkillOpt** | **85.9 (+4.0)** | **80.4 (+58.3)** | **71.5 (+13.9)** | **90.1 (+3.5)** | **56.5 (+15.7)** | – |

### 4.3 Headline aggregates (p.11) — all independently verified from Table 1

| Claim | Value | Verification |
|---|---|---|
| Cells best-or-tied | **52 / 52** | 42 direct + 10 harness; one cell is an exact tie (GPT–5.4-mini LiveMath, 32.8 = Trace2Skill) |
| GPT–5.5 direct chat avg | 58.8 → 82.3 = **+23.5** | 352.6/6 = 58.77; 493.7/6 = 82.28 ✓ |
| GPT–5.5 oracle-baseline gap | **+5.4** (82.3 vs 76.9) | best-per-cell = 84.8, 73.6, 66.9, 90.6, 52.0, 93.3 → 461.2/6 = 76.87 ✓ |
| Codex GPT–5.5 | 54.0 → 78.8 = **+24.8**; **+14.0** over EvoSkill (64.8) | ✓ |
| Claude Code GPT–5.5 | 57.8 → 76.9 = **+19.1**; **+3.2** over EvoSkill (73.7) | ✓ |
| Per-model avg gains (direct) | GPT–5.5 +23.5, GPT–5.4 **+12.7**, mini **+15.4**, nano **+26.7**, GPT–5.2 **+16.6**, Qwen3.5–4B **+19.2**, Qwen3.6–35B **+9.1** → mean **≈ +17.6** | 123.2/7 = 17.6 ✓ |
| Largest single deltas | OfficeQA +39.0; SpreadsheetBench +38.9 (direct); Claude Code Spreadsheet +58.3; Codex Spreadsheet +57.5; Qwen3.5–4B ALFWorld +50.7; nano DocVQA +49.4 | ✓ |
| Multiplicative gains cited | Qwen3.5–4B Spreadsheet 9.3→23.9 (×2.6); GPT–5.4-nano ALFWorld 34.3→69.4 (×2.0) | ✓ |

**Alternative-explanations refutation (p.10):**
1. *Not prompt length* — "human skills are already 145–516 tokens long and often exceed the one-shot LLM skill, yet they are beaten in every direct-chat model row."
2. *Not only optimizer capacity* — SkillOpt leads even for GPT–5.4-nano, and a target-matched optimizer recovers much of the gain (Table 5).
3. *Not one skill format* — EvoSkill already lifts Codex SpreadsheetBench 27.5→67.5, "but SkillOpt adds another +17.5 points (67.5→85.0)."

### 4.4 Cost of the optimizer model

No dollar figures are given anywhere. The only cost currency is **training tokens** (Table 6, p.14): 213.8M (SearchQA), 21.4M (Spreadsheet), 20.8M (OfficeQA), 188.2M (DocVQA), 23.2M (LiveMath), 59.3M (ALFWorld) → **526.7M training tokens total** across the six GPT–5.5/GPT–5.5 runs. Optimizer reasoning effort is "medium" for both teacher and student.

---

## 5. Transfer experiments (Table 4, p.9; discussion pp.13)

### (a) Cross-model transfer

| Source | Target | Benchmark | Baseline | Direct (in-domain SkillOpt) | Transferred |
|---|---|---|---|---|---|
| GPT–5.4 | GPT–5.4 | SpreadsheetBench | 41.4 | 62.5 | – (same model) |
| GPT–5.4 | GPT–5.4-mini | SpreadsheetBench | 36.1 | 47.5 | **45.5 (+9.4)** |
| GPT–5.4 | GPT–5.4-nano | SpreadsheetBench | 23.5 | 42.5 | **26.5 (+3.0)** |
| GPT–5.4 | GPT–5.4 | LiveMath | 36.8 | 44.0 | – (same model) |
| GPT–5.4 | GPT–5.4-mini | LiveMath | 14.7 | 32.8 | **19.2 (+4.5)** |
| GPT–5.4 | GPT–5.4-nano | LiveMath | 23.2 | 27.2 | **28.8 (+5.6)** |

Four genuine transfers, all positive. **Notable:** LiveMath on GPT–5.4-nano, the transferred skill (28.8) **exceeds** the in-domain SkillOpt reference (27.2) — "suggesting that some learned procedures are target-model agnostic." **Degradation:** SpreadsheetBench GPT–5.4-mini retains 82% of the in-domain gain (+9.4 of +11.4); SpreadsheetBench GPT–5.4-nano retains only **16%** (+3.0 of +19.0) — the sharpest drop; LiveMath GPT–5.4-mini retains 25% (+4.5 of +18.1). So transfer to the smallest model on the hardest procedural benchmark is where most of the value is lost, though "no row falls below the target's no-skill baseline."

### (b) Cross-harness transfer (all GPT–5.5)

| Source harness | Target harness | Benchmark | Baseline | Direct | Transferred |
|---|---|---|---|---|---|
| Codex | Claude Code | LiveMath | 40.8 | 56.5 | **42.4 (+1.6)** |
| Claude Code | Codex | LiveMath | 35.2 | 78.4 | **48.0 (+12.8)** |
| Codex | Claude Code | SpreadsheetBench | 22.1 | 80.4 | **81.8 (+59.7)** |
| Claude Code | Codex | SpreadsheetBench | 27.5 | 85.0 | **71.1 (+43.6)** |

Strongest signal: Codex→Claude Code SpreadsheetBench **slightly exceeds** the in-domain Claude Code reference (81.8 vs 80.4). **Degradation:** LiveMath Codex→Claude Code retains only **10%** of in-domain gain (+1.6 of +15.7) — the weakest transfer row in the paper; Claude Code→Codex LiveMath retains 30% (+12.8 of +43.2); Claude Code→Codex Spreadsheet retains 76% (+43.6 of +57.5). Interpretation (p.13): the transferred spreadsheet skill "appears to encode workbook-level procedures such as structure-first inspection, formula-aware verification, and static-value materialization."

### (c) Cross-benchmark transfer (OlympiadBench → Omni-MATH)

| Model | Baseline (Omni-MATH no-skill) | Direct | Transferred |
|---|---|---|---|
| GPT–5.4 | 56.6 | – | **60.3 (+3.7)** |
| GPT–5.4-mini | 34.8 | – | **36.6 (+1.8)** |
| GPT–5.4-nano | 38.8 | – | **40.1 (+1.3)** |

"the strictest of the three shifts: source and target benchmarks share only the broad task family (math)." No in-domain Omni-MATH SkillOpt run is reported, so retention fraction cannot be computed. Gains are small (+1.3 to +3.7) but uniformly positive; the paper attributes the smallness to the fact that "both the test instances and the answer-format conventions change."

Note: OlympiadBench (source) is not one of the six main benchmarks and has no reported SkillOpt training curve.

### (d) Optimizer-strength lever (Table 5, p.14)

| Benchmark | Target | Baseline | Strong optimizer (GPT–5.5) | Target-matched optimizer |
|---|---|---|---|---|
| SpreadsheetBench | GPT–5.4-mini | 36.1 | **47.5 (+11.4)** | 43.2 (+7.1) |
| SpreadsheetBench | GPT–5.4-nano | 23.5 | **42.5 (+19.0)** | 35.4 (+11.9) |
| SearchQA | GPT–5.4-mini | 75.9 | **80.2 (+4.3)** | 78.3 (+2.4) |
| SearchQA | GPT–5.4-nano | 55.8 | **74.8 (+19.0)** | 69.9 (+14.1) |

Retention: 62%, 63%, 56%, 74% → the paper's "recovers 56–74% of the strong-optimizer gain." Strong optimizer wins on all four cells. Argument (p.13): "The bounded-edit, validation-gated loop is what makes this monotone: without the gate, a stronger optimizer could just as easily push larger but harmful rewrites."

---

## 6. Ablations

### 6.1 Hyperparameter sweeps (Table 2, p.8) — GPT–5.5 as both target and optimizer

Reported as SearchQA / SpreadsheetBench / LiveMath.

**(a) Training set size** (split fixed per caption at 4:1:5; Appendix says 2:1:7)

| Setting | SearchQA | Spreadsheet | LiveMath |
|---|---|---|---|
| 1 example | 81.0 | 47.5 | 59.1 |
| 20% train | 84.1 | 69.0 | 65.9 |
| 40% train | **86.1** | 73.5 | 64.8 |
| 80% train | **86.1** | 77.6 | 67.0 |
| 100% train | 84.1 | **78.0** | **70.5** |

Procedural benchmarks reward more evidence (Spreadsheet 47.5→78.0; LiveMath 59.1→70.5); SearchQA "saturates at roughly 84−86 after 20% already." Note LiveMath 70.5 here is **higher than the 66.9 reported in Table 1**.

**(b) Reflection minibatch size `B_m`**

| `B_m` | SearchQA | Spreadsheet | LiveMath |
|---|---|---|---|
| 1 | 85.9 | 75.4 | 60.5 |
| 2 | 86.3 | 77.1 | 54.8 |
| 4 | 86.9 | 75.4 | **64.5** |
| 8 (default) | **87.1** | 77.5 | 61.3 |
| 16 | 87.0 | **77.9** | 61.3 |
| 32 | 86.9 | 77.5 | 58.9 |

Flat: SearchQA in 85.9–87.1, Spreadsheet in 75.4–77.9. LiveMath is the noisy one (54.8–64.5, a 9.7-point spread).

**(c) Rollout batch size `B`**

| `B` | SearchQA | Spreadsheet | LiveMath |
|---|---|---|---|
| 8 | 85.1 | 76.8 | 58.1 |
| 24 | 86.4 | 77.1 | **62.9** |
| 40 (default) | 87.1 | **77.5** | 61.3 |
| 56 | 86.5 | 76.8 | 56.5 |
| full epoch | **87.2** | 75.0 | 53.2 |

SearchQA 85.1–87.2, Spreadsheet 75.0–77.5. LiveMath degrades markedly at full-epoch batch (53.2).

**(d) Textual learning rate `L_t`**

| `L_t` | SearchQA | Spreadsheet | LiveMath |
|---|---|---|---|
| 1 | 85.5 | 77.5 | 62.1 |
| 2 | 86.7 | 77.5 | 60.5 |
| 4 (default) | 86.5 | **78.2** | 56.5 |
| 8 | **87.0** | 73.6 | **66.9** |
| 16 | 86.8 | **78.2** | 65.3 |

"the lowest score across all five settings is still only 85.5 on SearchQA." No monotone trend; LiveMath best at `L_t=8` (66.9 — the value reported in Table 1).

**(e) Learning-rate scheduler**

| Schedule | SearchQA | Spreadsheet | LiveMath |
|---|---|---|---|
| constant | **87.3** | **80.7** | 62.1 |
| cosine (stated default) | 87.1 | 77.5 | 61.3 |
| linear | 87.2 | 72.9 | **62.9** |

The **constant** row (87.3 / 80.7) reproduces the Table 1 GPT–5.5 SearchQA and SpreadsheetBench headline numbers exactly, not the cosine default. See §12.

**(f) Slow-update samples per epoch**

| Samples | SearchQA | Spreadsheet | LiveMath |
|---|---|---|---|
| 5 | 86.8 | 76.4 | 64.5 |
| 10 | 86.4 | 74.3 | **65.3** |
| 20 (default) | **87.1** | **77.5** | 61.3 |
| 40 | 86.9 | 75.4 | 54.8 |

5/10/40 all within ±2.7 points of the default (per the paper's own summary; the LiveMath 40-sample row is −6.5, so the ±2.7 claim holds only for SearchQA/Spreadsheet).

### 6.2 Component ablations (Table 3, p.8)

| Component | Setting | SearchQA | SpreadsheetBench | LiveMath |
|---|---|---|---|---|
| Learning-rate form | **lr=4 (default)** | **87.1** | **77.5** | **61.3** |
| | dynamic lr | 85.8 | 71.8 | 54.0 |
| | **without lr** (unbounded rewriting) | 84.6 | 75.7 | 57.3 |
| Rejected buffer | **with rejected buffer** | **87.1** | **77.5** | **61.3** |
| | **without rejected buffer** | 85.5 (−1.6) | 72.9 (−4.6) | 58.9 (−2.4) |
| Slow/meta update | **meta skill + slow update** | **87.1** | **77.5** | **61.3** |
| | **without meta skill** | 85.1 (−2.0) | 75.7 (−1.8) | 58.1 (−3.2) |
| | **without meta skill and slow update** | 86.3 (−0.8) | **55.0 (−22.5)** | 59.7 (−1.6) |

Key readings:
- **No edit budget ("without lr")**: −2.5 / −1.8 / −4.0 → "any moderate, bounded edit budget already beats baselines that rewrite the skill without a budget."
- **"dynamic lr"** is worse than both fixed lr=4 *and* no lr on SpreadsheetBench (71.8) — the worst learning-rate variant overall.
- **No rejected buffer**: −1.6 / −4.6 / −2.4; framed as "a stabilizer for the default loop rather than as an extra deployment-time mechanism."
- **No slow + no meta**: SpreadsheetBench 77.5 → 55.0, **−22.5 points, "the largest degradation in the ablation suite."** Note the non-monotonicity: removing meta alone (75.7) is *worse* than removing both (86.3) on SearchQA, and removing both is *better* than removing meta alone on LiveMath (59.7 vs 58.1). Only SpreadsheetBench shows the clean ordering.
- Mechanistic explanation (p.12): removing both "removes the long-horizon evidence stream **and** the protected-region contract that keeps local edits from overwriting durable procedural lessons."

### 6.3 Gate strictness and edit observability (p.12)

No "no-gate" numerical ablation row exists. The paper argues for the gate qualitatively (propose-and-test vs. unconditional self-editing) and indirectly:
- **Edit economy as gate evidence** (p.14): only 1–4 edits are ever committed; "the optimizer model proposes many more edits per epoch, but only a handful pass the held-out check."
- **Figure 3** (p.12): epoch checkpoints (1, 2, 4, 8 for SpreadsheetBench; 1, 2, 4, 8, 12, 16 for SearchQA and LiveMath) plot train-rollout score, selection-best score, and unseen-test score together, "confirming that the gate tends to select skills that generalize rather than skills that only fit the selection split." Y-ranges: SpreadsheetBench 0.3–1.0 hard score; SearchQA 0.82–0.88; LiveMath 0.45–0.85. Note these runs go to 8–16 epochs vs. the default 4.
- Baselines that lack a gate (Trace2Skill: "mines trajectory lessons without a held-out gate") stand in as the empirical no-gate comparison.

### 6.4 Edit-type restrictions

No ablation restricts the op vocabulary (e.g. append-only, no-delete). The only mode-level alternative mentioned is `patch` vs. `rewrite_from_suggestions`, and **no numbers are reported for rewrite mode**. This is an unfilled cell in the ablation grid.

### 6.5 Skill length effects (Table 6, p.14)

| Benchmark | Initial (tok) | Final (tok) | Accepted edits | Growth |
|---|---|---|---|---|
| SearchQA | 16 | 857 | 4 | ×53.6 |
| SpreadsheetBench | 224 | 1,995 | 4 | ×8.9 |
| OfficeQA | 145 | 883 | 1 | ×6.1 |
| DocVQA | 81 | 959 | 3 | ×11.8 |
| LiveMath | 154 | 379 | 1 | ×2.5 |
| ALFWorld | 516 | 1,321 | 2 | ×2.6 |

Range 379–1,995 tokens; **median ≈ 920** (paper says "roughly 920"; exact median of the six = (883+959)/2 = 921). Growth "×2.5 to ×53". Edits 1–4, **median 2.5**. Length is *not* varied as an independent variable — there is no ablation forcing a token cap. The paper's length argument is observational plus the negative control that human skills at 145–516 tokens lose in every direct-chat row.

---

## 7. Cost accounting

### 7.1 Training-time cost

| Benchmark | Train tokens | Reported cost / test-point | Δ vs no-skill (Table 1) | Implied ratio (tokens ÷ Δ) |
|---|---|---|---|---|
| SearchQA | 213.8M | 37.9M | +9.6 | 22.3M ✗ |
| SpreadsheetBench | 21.4M | 0.6M | +38.9 | 0.55M ✓ |
| OfficeQA | 20.8M | 1.1M | +39.0 | 0.53M ✗ |
| DocVQA | 188.2M | 46.4M | +12.4 | 15.2M ✗ |
| LiveMath | 23.2M | 3.6M | +29.3 | 0.79M ✗ |
| ALFWorld | 59.3M | 15.9M | +11.9 | 4.98M ✗ |

**Only SpreadsheetBench's cost-per-point reproduces from the Table 1 delta.** The paper's own worked example is internally inconsistent: "+39.0 points on OfficeQA at 1.1M tokens / point, total 20.8M tokens" (p.15) implies 39.0 × 1.1M = 42.9M ≠ 20.8M. The `Cost / pt` column therefore either uses a different gain reference (unspecified — not the no-skill delta, not the best-baseline delta by any consistent reading) or contains errors. Treat the *absolute* token counts as the reliable figures and the cost-per-point column as unverifiable.

**Two cost regimes** as framed by the paper: cheap procedural benchmarks (SpreadsheetBench, OfficeQA, LiveMath) at 0.6–3.6M tokens/point; expensive long-trajectory or multimodal benchmarks (SearchQA 37.9M/pt, DocVQA 46.4M/pt) "cost an order of magnitude more per point."

**Cost drivers not itemized:** the paper never separates (i) rollout tokens on `D_tr`, (ii) **selection-split re-evaluation tokens** — one full `D_sel` pass per candidate skill per step, which is likely the dominant term, (iii) optimizer-model tokens across the 8 prompt contracts × 16 parallel analysts × up to 3 refinement rounds, (iv) slow/meta-update rollouts (20 tasks × 2 skills × epochs). No wall-clock time, no per-call counts, no USD.

### 7.2 "Zero inference-time model calls at deployment" — what it precisely means

The claim (abstract, p.1) is narrow and, read literally, correct: at deployment the artifact is a static `best_skill.md`; **no optimizer/critic/judge model is invoked at inference time**. Supporting statements: "The deployed artifact is still a static `best_skill.md` that calls only the target model" (p.13); "after export, the optimized `best_skill.md` adds no optimizer calls, no weight updates, and only a compact text artifact to the target agent" (p.15).

What it does **not** mean:
- **Prompt-token overhead is non-zero.** 379–1,995 tokens (median ~920) are prepended to every request in direct chat. On short-prompt tasks this can be the majority of the input. The paper never reports inference token counts, latency, or cost deltas at deployment.
- **In harness mode the skill occupies a file the agent must read.** SkillOpt "renders the current skill to a per-task `SKILL.md`" — reading it consumes agent turns and tokens inside the Codex/Claude Code loop, and possibly repeatedly.
- **Longer/more procedural skills may induce more tool calls** (e.g. the SpreadsheetBench rule "reopening the saved workbook to check boundary rows and remaining blanks" is an *additional* verification pass). No turn-count or step-count comparison against no-skill is reported anywhere.
- The 300–2,000 token figure is a measured range over six runs, not an enforced budget.

So the honest reading: **zero extra model *calls*, non-zero and unmeasured extra *tokens and turns*.**

---

## 8. Comparison to GEPA as the authors frame it

### 8.1 How they credit and distinguish it

Related Work (p.3): "GEPA demonstrates that trajectory feedback can guide reflective prompt evolution and outperform reinforcement learning on several language-agent tasks [13]." Then the distinction: "By treating language artifacts as optimizable objects, these methods can directly exploit execution feedback, but they mainly target **prompts, system designs, or full configurations rather than reusable domain adaptation**. SkillOpt instead optimizes a **persistent skill document that can be trained, validated, exported, and reused** with the adapted model, applying language-level controllability to a stable procedural skill state."

Reiterated in §4.1 (p.10): "TextGrad and GEPA optimize prompts but not a persistent skill artifact."

And in §2 (p.3), the contrast is framed as scope-narrowing rather than superiority of idea: SkillOpt "studies a narrower problem: how to train one compact domain skill with deep-learning-style controls such as trajectory batches, reflection minibatches, textual learning rates, validation gates, rejected-edit buffers, and slow/meta updates."

**The claimed axes of difference, distilled:**

| Axis | GEPA (as characterized) | SkillOpt |
|---|---|---|
| Optimized object | prompt / system design / configuration | persistent, exportable skill document |
| Search strategy | Pareto reflective prompt evolution (population-based) | single-lineage bounded editing with strict gate |
| Step-size control | none claimed | textual learning rate `L_t` + schedule |
| Negative feedback | not claimed | rejected-edit buffer |
| Cross-epoch consolidation | not claimed | protected slow update + optimizer meta skill |
| Reuse story | tied to the pipeline it optimized | transfers across models, harnesses, nearby benchmarks |

Note this is a *characterization* by the authors; GEPA does maintain a Pareto frontier and does score candidates on held-out feedback data, so the "no validation" implication should be read as "no strict single-lineage acceptance gate," not "no validation at all."

### 8.2 Head-to-head numbers (direct chat only)

GEPA is not run under Codex or Claude Code, so all head-to-head numbers are direct chat.

| Model | GEPA 6-bench avg | SkillOpt 6-bench avg | SkillOpt − GEPA |
|---|---|---|---|
| GPT–5.5 | 73.4 | 82.3 | **+8.9** |
| GPT–5.4 | 68.1 | 72.4 | **+4.4** |
| GPT–5.4-mini | 59.9 | 64.3 | **+4.4** |
| GPT–5.4-nano | 53.4 | 57.4 | **+4.0** |
| GPT–5.2 | 65.3 | 67.9 | **+2.6** |
| Qwen3.5–4B | 47.1 | 57.9 | **+10.8** |
| Qwen3.6–35B-A3B | 59.7 | 65.0 | **+5.4** |
| **Mean** | | | **≈ +5.8** |

Per-benchmark on GPT–5.5: SearchQA +2.5, Spreadsheet +7.1, OfficeQA +8.2, DocVQA +2.1, LiveMath +23.7, ALFWorld +9.7. GEPA wins **zero** of the 42 direct-chat cells against SkillOpt. GEPA's closest calls are GPT–5.2 SearchQA (82.7 vs 83.1, −0.4) and GPT–5.2 DocVQA (89.5 vs 89.6, **−0.1**). GEPA is the strongest single direct-chat baseline overall (it supplies the oracle-best entry on SearchQA and SpreadsheetBench for GPT–5.5).

---

## 9. Limitations

### 9.1 Stated (Appendix B, p.20)

1. **Requires reliable scalar feedback.** "the optimization loop relies on scored trajectories and a held-out selection split, so it is most directly applicable when the target task has automatic verifiers, exact-match metrics, executable checks, or otherwise reliable feedback signals. For open-ended domains where success is subjective, multi-dimensional, or costly to judge, the validation gate may require stronger human or model-based evaluation."
2. **Training cost.** "training the skill requires additional rollout computation and calls to an optimizer model; this cost is amortized when the same skill is reused, but may be less attractive for one-off tasks."
3. **Single-document bottleneck.** "SkillOpt intentionally optimizes a single portable skill rather than growing a large skill library or changing model weights. This design improves deployment simplicity, but a single skill may be insufficient for highly heterogeneous domains that require many disjoint procedures."
4. **Heuristic overfitting risk on transfer.** "optimized skills can encode domain-specific heuristics from the training distribution, so careful held-out evaluation remains necessary before transferring them to substantially different models, harnesses, or task settings."

### 9.2 Unstated but evident

**a. Does it need a stronger optimizer than the target?** Not strictly — Table 5 shows a target-matched optimizer recovers 56–74% of the gain, which the authors explicitly use to argue "SkillOpt is not a distillation pipeline from a stronger teacher into a weaker student." But every headline number in Table 1 uses **GPT–5.5 as optimizer**, including for Qwen and small-GPT targets. So the reported +17.6 average is a *stronger-optimizer* number; the same-scale number is roughly 60–75% of it. Also untested: whether a *weaker* optimizer than the target works at all, and whether Table 5's finding extends beyond the 4 cells measured (both on GPT–5.4-mini/nano, both on SearchQA/SpreadsheetBench).

**b. Validation-split cost is the hidden dominant term.** Every candidate skill triggers a full `D_sel` evaluation on the frozen model *inside the harness*. For ALFWorld that is 140 embodied episodes at up to 50 steps each, per candidate. The hash cache only helps for byte-identical skills. This cost is never itemized, and the token totals in Table 6 make it impossible to separate.

**c. No variance, seeds, or significance anywhere.** Single `split_seed=42`; no error bars in any table; no repeated runs. I recomputed every cell's margin over the best available baseline: **11 of 52 cells are decided by ≤ 0.8 points**, including one exact tie:

| Cell | SkillOpt | Best baseline | Margin |
|---|---|---|---|
| GPT–5.4-mini, LiveMath | 32.8 | 32.8 (Trace2Skill) | **0.0 (tie)** |
| GPT–5.2, DocVQA | 89.6 | 89.5 (GEPA) | +0.1 |
| GPT–5.2, SearchQA | 83.1 | 82.7 (GEPA) | +0.4 |
| Claude Code, DocVQA | 90.1 | 89.6 (LLM skill) | +0.5 |
| GPT–5.5, DocVQA | 91.2 | 90.6 (Trace2Skill) | +0.6 |
| GPT–5.4, SearchQA | 83.1 | 82.5 (TextGrad) | +0.6 |
| Qwen3.6–35B, OfficeQA | 47.1 | 46.5 (LLM skill) | +0.6 |
| GPT–5.4-nano, Spreadsheet | 42.5 | 41.8 (Human) | +0.7 |
| GPT–5.4-nano, ALFWorld | 69.4 | 68.7 (GEPA) | +0.7 |
| GPT–5.4, DocVQA | 91.2 | 90.4 (LLM skill) | +0.8 |
| GPT–5.4-mini, SearchQA | 80.2 | 79.4 (GEPA) | +0.8 |

So the "52/52 best-or-tied" claim rests on ~21% of cells whose margins are smaller than plausible run-to-run noise. LiveMath's own ablation spread (54.0–70.5 across settings) suggests noise of several points on at least that benchmark.

**d. Apparent per-benchmark hyperparameter selection in the headline table.** The GPT–5.5 headline row (87.3 / 80.7 / … / 66.9) matches the *constant*-schedule ablation row on SearchQA and SpreadsheetBench and the `L_t=8` ablation row on LiveMath — while the stated default (cosine, `L_t=4`) yields 87.1 / 77.5 / 61.3 (Table 3). If Table 1 reports the best over hyperparameter variants (arguably legitimate under Eq. 2 if selection is done on `D_sel`, which is not stated), then the "+38.9 SpreadsheetBench" and "+29.3 LiveMath" headline deltas are max-over-sweeps rather than single-config results, and the fair single-default numbers would be ≈ +35.7 and ≈ +23.7. The paper never reconciles this.

**e. Harness breadth is narrower than advertised.** "Three execution harnesses" is true, but Codex and Claude Code are evaluated **on GPT–5.5 only** (10 of 52 cells). Baseline coverage is also asymmetric: GEPA/TextGrad/Trace2Skill never run in harnesses; EvoSkill never runs in direct chat. So "beats every per-cell competitor among human, one-shot LLM, Trace2Skill, TextGrad, GEPA, and EvoSkill" is a union over the table, not a per-cell 6-way comparison.

**f. Multi-skill / compositional adaptation is out of scope.** Explicitly single-document; §5 Outlook defers "skill libraries that share infrastructure across domains." No routing, no applicability conditions, no composition. The `SLOW_UPDATE` protected region is the only internal structure imposed.

**g. Contamination and benchmark provenance.** Not discussed at all. LiveMathematicianBench is explicitly a *live* benchmark (arXiv 2604.01754, i.e. dated ~April 2026) whose freshness relative to model cutoffs is unaddressed; SearchQA (2017), DocVQA (2021), SpreadsheetBench (2024), OlympiadBench (2024), Omni-MATH (2024) are older and plausibly in pretraining data. OfficeQA Pro [31] and the model releases are all 2026-dated, so no independent verification of any of these artifacts is possible from the paper alone.

**h. Case-study numbers do not match the main table.** The SpreadsheetBench qualitative run reports 40.4 → 78.9 (p.16) vs. Table 1's 41.8 → 80.7; ALFWorld reports 49.3 → 74.6 with GPT–5.4-nano student vs. Table 1's 34.3 → 69.4. These are labeled "representative run[s]" but the discrepancy (esp. ALFWorld no-skill 49.3 vs 34.3) is unexplained.

**i. TextGrad appears under-tuned.** It is *negative* vs. no-skill in 13 of 42 direct-chat cells, including Qwen3.6 LiveMath at 7.2 (−24.0) and Qwen3.6 Spreadsheet at 22.9 (−15.3). A baseline that catastrophically damages performance suggests configuration mismatch rather than a fair comparison.

**j. Rejected-buffer scope is epoch-local and reset each epoch** (Algorithm 1 line 5) — so negative feedback does not accumulate across epochs except indirectly via `m_meta`. Not discussed as a limitation.

**k. `LiveMath: 35 training items per epoch with rollout batch 200`** implies each item is rolled out ~5.7× per step. With a 35-item train pool and a 4-epoch budget, the risk of fitting the training pool is high, yet LiveMath is also the benchmark with the largest ablation variance.

**l. Reflection over successes could entrench luck.** The success analyst encodes patterns "across MULTIPLE trajectories," but on benchmarks with high base rates (SearchQA at 77.7 no-skill) most trajectories succeed, so the success channel is fed mostly by tasks the skill did not need to fix. Not analyzed.

---

## 10. Exact quotable claims (verbatim, with page numbers)

1. p.1 (Abstract): "**SkillOpt is, to our knowledge, the first systematic controllable text-space optimizer for agent skills: a separate optimizer model turns scored rollouts into bounded add/delete/replace edits on a single skill document, and an edit is accepted only when it strictly improves a held-out validation score.**"
2. p.1 (Abstract): "**A textual learning-rate budget, rejected-edit buffer, and epoch-wise slow/meta update make skill training stable while adding zero inference-time model calls at deployment.**"
3. p.2 (§1): "**The deep-learning analogy is operational rather than decorative.**"
4. p.2 (§1): "**The deployed output is a compact best_skill.md file of roughly 300–2,000 tokens, with the adapted model and execution harness remaining fixed.**"
5. p.2 (§1): "**if consecutive skill revisions move too far or in inconsistent directions, rejected edits and previous accepted edits no longer provide a meaningful optimization history.**"
6. p.5 (§3.4): "**The learning-rate analogue in SkillOpt is the edit budget L_t: the maximum number of skill edits applied at step t. After aggregation, the optimizer model ranks the merged edit pool by expected utility and clips it to the top L_t edits. This is the key difference from ad hoc prompt rewriting.**"
7. p.6 (§3.5): "**This gate turns reflection into propose-and-test optimization rather than unconditional self-editing, which is crucial because plausible textual diagnoses can still hurt the actual target model.**"
8. p.6 (§3.6): "**The meta skill is optimizer-side only. It summarizes which edit patterns helped, which were rejected, and which failures persisted across epochs. This meta guidance is prepended to future optimizer prompts for reflection, merging, and ranking, but it is not shipped with the target model.**"
9. p.8 (§4, defaults): "**held-out validation gating (strictly greater than the current selection score—ties are rejected)**"
10. p.12 (§4.2): "**The validation gate is intentionally strict: a candidate skill is accepted only when its selection-split score is strictly greater than the current selection score, so ties are rejected and the deployed skill never silently drifts.**"
11. p.13 (§4.3): "**the target-matched optimizer is far from collapsed—it recovers 56–74% of the strong-optimizer gain across the four cells, confirming that SkillOpt is not a distillation pipeline from a stronger teacher into a weaker student: the optimization loop itself contributes substantial value on top of whatever the optimizer can already do.**"
12. p.14 (§4.4): "**LiveMathematicianBench's +29.3 point gain over no skill arises from a single accepted edit, and OfficeQA's +39.0 point gain similarly arises from one accepted edit. This is direct evidence that the validation gate is doing real work.**"
13. p.15 (§4.4): "**The bulk of the optimizer's text-space search is thus rejected, captured by the rejected-edit buffer (Section 3.5) for future use, and never reaches the target model.**"
14. p.20 (Appendix B): "**it is most directly applicable when the target task has automatic verifiers, exact-match metrics, executable checks, or otherwise reliable feedback signals.**"
15. p.20 (Appendix B): "**a single skill may be insufficient for highly heterogeneous domains that require many disjoint procedures.**"
16. p.27 (§C.3): "**The edit budget L_t acts as a textual learning rate: it limits how many proposed edits can be applied at a step, preserving continuity between adjacent skills.**"

**Representative learned rules, verbatim from deployed `best_skill.md` files (Figure 4, p.15):**

- SearchQA: "Infer the expected answer type from clue wording, then choose the shortest canonical entity supported by co-occurring distinctive evidence."
- SpreadsheetBench: "Inspect workbook structure and formulas, then write evaluated static values across the full requested target range instead of relying on Excel recalculation."
- OfficeQA: "Treat oracle parsed pages as primary evidence, lock table/date/unit context, and output exactly the requested rounded value without extra labels."
- DocVQA: "For tables, forms, charts, and legends, first bind the question to the exact visual row/header/field, then copy only the aligned answer span."
- LiveMathematicianBench: "In strongest-statement MCQs, rank choices by theorem strength and prefer a justified stronger-result option over true but weaker corollaries."
- ALFWorld: "Keep a horizon-aware visited/frontier ledger, diversify search after repeated same-type failures, and avoid revisiting the destination until holding the target."

---

## 11. Open problems the authors name (§5 Outlook, p.17)

Verbatim: "SkillOpt optimizes a single skill artifact for a single target domain; natural extensions include **skill libraries that share infrastructure across domains**, **reuse of optimizer-side meta skills across benchmarks**, **reward-free or preference-driven validation gates for open-ended tasks**, and **self-distillation of optimized skills back into the target model as a stepping stone toward weight-level adaptation**."

Plus the closing programmatic ambition: "We hope that treating the skill itself as the trainable object—rather than as a side artifact of prompting—will let future work apply the full toolkit of optimization (**learning rates, schedules, regularization, curricula, validation**) to a part of the agent stack that has so far been hand-engineered." Note the explicitly *unimplemented* items in that list: **regularization** and **curricula** — SkillOpt has no explicit regularizer (length penalty, KL analogue) and no curriculum over `D_tr`.

Derived open problems the paper leaves implicit: no no-gate numerical ablation; no `rewrite_from_suggestions` results; no edit-op restriction study; no inference-token/latency accounting; no multi-seed variance; harness coverage limited to one target model.

---

## 12. Cross-check notes and internal inconsistencies

Verified consistent between the PDF text and the pre-existing skill chapters (`ch01`–`ch05`): all defaults, the Algorithm 1 transcription, Table 1/3/4/5/6 numbers, the 8 prompt contracts, the five design principles, and all headline aggregates. The chapters state the split as "three-split protocol" without a ratio, so they do not reproduce the 4:1:5 / 2:1:7 conflict.

Inconsistencies found **within the paper itself**:

| # | Issue | Locations |
|---|---|---|
| 1 | Train/selection/test ratio: **4:1:5** vs **2:1:7** for the same ablation | Table 2 caption p.8 vs Appendix C pp.20–21 |
| 2 | Table 1 GPT–5.5 numbers match the *constant* schedule (87.3/80.7) and `L_t=8` (LiveMath 66.9), not the stated cosine/`L_t=4` default (87.1/77.5/61.3) | Table 1 p.7, Table 2(d)(e) p.8, Table 3 p.8 |
| 3 | `Cost / pt` column irreproducible from Table 1 deltas for 5 of 6 benchmarks; the paper's own OfficeQA example is self-contradictory (39.0 × 1.1M ≠ 20.8M) | Table 6 p.14, text p.15 |
| 4 | Case-study run scores differ from Table 1 (Spreadsheet 40.4→78.9 vs 41.8→80.7; ALFWorld 49.3→74.6 vs 34.3→69.4) | §4.5 p.16 vs Table 1 p.7 |
| 5 | **SealQA** described in Appendix C but appears in no table | Appendix C p.20 |
| 6 | "human skills are already 145–516 tokens long … (Table 6)" but Table 6's Initial column spans 16–516 | p.10 vs Table 6 p.14 |
| 7 | Table 4 is cross-referenced as "Tables 4–4" and "Table 4 (a)/(b)/(c)" — the sub-tables are never separately numbered | §4.3 pp.12–13 |
| 8 | Buffer `B` and rollout batch size `B` share a symbol in Algorithm 1 | p.22 |
| 9 | Slow-update samples described as "5, 10, and 40 each within ±2.7 points" — true for SearchQA/Spreadsheet, but LiveMath at 40 samples is −6.5 | p.11 vs Table 2(f) p.8 |
| 10 | Figure 3 uses 8 and 16 epoch checkpoints although the stated default is `E = 4` | Fig. 3 p.12 vs defaults p.8 |
