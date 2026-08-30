# Technical Dossier — *Agentic Monte Carlo: Simulating Reinforcement Learning for Black-Box Agents*

**Authors:** Dae Yon Hwang, Raunaq Suri, Valentin Villecroze, Anthony L. Caterini, Jesse C. Cresswell, Noël Vouitsis, Brendan Leigh Ross — Layer 6 AI, Toronto.
**Venue:** Proceedings of the 43rd ICML, Seoul, PMLR 306, 2026. arXiv:2606.05296v1 [cs.LG], 3 Jun 2026.
**Code:** `https://github.com/layer6ai-labs/Agentic-Monte-Carlo`
**Source for this dossier:** full extracted text of all 30 pages (`AMC.txt`), pages 1–9 main body, 10–12 references, 13–16 Appendix A + B, 17–22 Appendix C (prompts), 23–26 Appendix D (ablations), 26–29 Appendix E (qualitative), 30 Table 13.

---

## 1. Problem formulation

### 1.1 The regime split that motivates the paper

The framing claim (p. 1): "LLM agents operate in two distinct regimes: open-weight agents amenable to reinforcement learning (RL) and black-box agents whose behaviour must be controlled purely at test time." PPO/GRPO "rely on a fundamental assumption: white-box access to model parameters to compute policy gradients" (p. 1), which frontier models (GPT-5, Gemini 3, Claude 4.6) do not provide. The alternatives — prompt engineering or fine-tuning an open-weight surrogate — perform no RL on the target model.

### 1.2 MDP / notation

Standard discrete MDP, stated in §2.1 (p. 2):

- state `s_t ∈ S` at timestep `t`; action `a_t ~ π(a_t | s_t)`, `a_t ∈ A`
- transition `s_{t+1} ~ p(s_{t+1} | s_t, a_t)`
- scalar reward `r(s_t) ∈ R` given by the environment; **state-based reward, not `r(s,a)`**
- "We assume discrete state and action spaces."
- horizon `T`, trajectory `s_{0:T} ~ π(s_{0:T})`, cumulative reward shorthand `r(s_{0:T}) = Σ_{t=0}^{T} r(s_t)`
- footnote 3 (p. 3): "We omit discount factors here."

**Critical modelling move (footnote 2, p. 3):** "Since our policy of interest π will be notionally intractable, we fold transition dynamics into our concept of the policy, ignore actions, and focus on the resulting state transitions π(s_{t+1} | s_t) throughout."

So `π` denotes the *composition* of the LLM policy and the environment dynamics, and the object of inference is a distribution over **state** trajectories. This is what makes the method black-box-compatible (you never need to factor out the LLM's action distribution), but it also imports the classic control-as-inference caveat discussed in §7.3 below: the posterior tilts the *environment's* transition probabilities as well as the agent's action probabilities.

### 1.3 Access assumptions — what is and is not available

| Quantity | Assumed available? | Evidence |
|---|---|---|
| Ability to **sample** from `π` (i.e., call the API) | Yes — this is the only access to `π` required | §2.2, Algorithm 1 line 4 |
| **Log-probabilities / logits** of `π` | **No.** "exact sampling from this optimal posterior is impossible for black-box agents, whose full log-probabilities are generally unavailable" (p. 2). Related work explicitly separates AMC from twisted-SMC methods that "modulate the proposal distribution via access to the LLM's logits, a requirement that is impossible to satisfy with proprietary black-box models" (p. 5) | pp. 2, 5 |
| **Weights / gradients** of `π` | No | throughout |
| **Per-step environment reward `r(s_t)` at test time** | **Yes.** "In our setting, r(s_t) is available at test time, so we only need to learn future rewards" (p. 4). A fallback is given: "if r were not available at test-time, we would parameterize V_θ directly" | p. 4 |
| **Terminal/cumulative reward at test time for trajectory selection** | Yes in the reported setting: "we can simply keep the trajectory with the highest cumulative reward or, if r is unavailable at test time, the trajectory i with the largest weight w_T^{(i)}" (p. 4); metrics section confirms "we will also have access to a (real or approximated) reward function r" (p. 6) | pp. 4, 6 |
| **Environment that can run N trajectories in parallel** | Yes, assumed: "the nature of many digital environments allows us to simulate agent trajectories in parallel" (p. 3) | p. 3 |
| **Training tasks with rewards, offline, for value-function fitting** | Yes: `M` trajectories collected from `π` on a training split per benchmark (§4.1, App. B) | pp. 5, 16 |

Note that a POMDP is never formalized. In practice the "state" fed to both policy and value model is the *textual interaction history* — the value prompts take `INSTRUCTION`, `HISTORY` (consecutive `(STATE, ACTION)` pairs), and `NOW` (current state) (App. C, pp. 19–20). So Markovianity is recovered by defining the state as the full history, which is standard but means `V_θ` inputs grow with `t`.

### 1.4 Objective

KL-regularized RL (Eq. 1, p. 3):

```
π* = argmax_{π_θ}  E_{π_θ(s_{0:T})}[ r(s_{0:T}) ] − β · KL[ π_θ ‖ π ]
```

`π_θ` is the trainable policy, `π` the fixed pre-trained reference (the black box), `β` the regularization coefficient.

---

## 2. Core theory

### 2.1 RL-as-inference: the posterior

Eq. 2 (p. 3):

```
π*(s_{0:T}) ∝ π(s_{0:T}) · e^{ r(s_{0:T}) / β }
```

"Equation 2 frames KL-regularized RL as a Bayesian inference problem where the reference policy's probabilities π(s_{0:T})—which serve as the prior—are modulated to upsample high-reward trajectories as encoded by the likelihood e^{r(s_{0:T})/β}." (p. 3)

**Proposition A.1 (Appendix A.2, pp. 13–14)** — attributed to Korbak et al. (2022) and the control-as-inference literature (Levine, 2018); reproduced for completeness. With
`π_*^{(Bayes)}(s_{0:T}) = (1/Z) π(s_{0:T}) e^{r(s_{0:T})/β}`, `Z = ∫ π(s_{0:T}) e^{r(s_{0:T})/β} ds_{0:T}` (Eq. 8) and
`π_*^{(VI)} = argmax_{π_θ} J(π_θ)` (Eq. 9), then `π_*^{(Bayes)} = π_*^{(VI)}`.

Proof (exact chain, p. 14):
```
J(π_θ) = E_{π_θ}[r] − β KL[π_θ‖π]
       = β ∫ π_θ log( π(s_{0:T}) e^{r/β} / π_θ(s_{0:T}) ) ds
       = β ∫ π_θ log( Z π_*^{(Bayes)} / π_θ ) ds
       = β log Z − β KL[ π_θ ‖ π_*^{(Bayes)} ]
```
Since `β log Z` is constant in `π_θ`, argmax `J` = argmin KL, uniquely zero when the arguments coincide. **This is exact and principled** — it is literally the ELBO identity.

The conceptual payoff (p. 2): "instead of updating the parameters of the prior (which we cannot access), we can simply sample from the posterior distribution to recover optimal behaviour."

### 2.2 Importance weights and the soft value function

Target weight (Eq. 3, p. 3), for a *partial* trajectory:

```
w_t = π*(s_{0:t}) / π(s_{0:t})
```

**Proposition A.2 (Appendix A.3, p. 14)** derives the recursion. First marginalize the posterior:
```
π*(s_{0:t}) = ∫ π*(s_{0:T}) ds_{t+1:T}
            = (1/Z) π(s_{0:t}) e^{(1/β) Σ_{τ=0}^{t−1} r(s_τ)} ∫ π(s_{t+1:T}|s_t) e^{(1/β) Σ_{τ=t}^{T} r(s_τ)} ds_{t+1:T}
            = (1/Z) π(s_{0:t}) e^{(1/β) Σ_{τ=0}^{t−1} r(s_τ)} e^{V(s_t)}
```
hence
```
w_t = (1/Z) e^{(1/β) Σ_{τ=0}^{t−1} r(s_τ)} e^{V(s_t)}
```
and isolating `w_{t−1}` gives the **recursive weight update (Eq. 4, p. 3)**:

```
w_t = w_{t−1} · E_{π(s_{t+1:T}|s_t)}[ e^{(1/β) Σ_{τ=t}^{T} r(s_τ)} ] / E_{π(s_{t:T}|s_{t−1})}[ e^{(1/β) Σ_{τ=t}^{T} r(s_τ)} ]
    = w_{t−1} · exp( V(s_t) − V(s_{t−1}) + r(s_{t−1})/β )
```

with the **soft value function**

```
V(s_t) = log E_{π(s_{t+1:T} | s_t)}[ e^{(1/β) Σ_{τ=t}^{T} r(s_τ)} ]
```

"V is known as the soft value function in maximum entropy RL because its log-sum-exp structure emulates a soft maximum reward value over trajectories (Haarnoja et al., 2017; 2018)." (p. 3)

Two structural remarks:

1. `V` here plays exactly the role of the **twist function** in Zhao et al. (2024)'s twisted SMC — an optimal lookahead potential that makes the intermediate targets the true marginals of the final posterior. Because the exponent sum starts at `τ = t` while the expectation is over `s_{t+1:T} | s_t`, `r(s_t)` factors out deterministically: `V(s_t) = r(s_t)/β + log E[e^{(1/β)Σ_{τ=t+1}^{T} r(s_τ)}]`. This is precisely what licenses the parameterization in Eq. 6.
2. Because `V` is the *optimal* twist, the intermediate targets are exact marginals and SIR is unbiased in the `N → ∞` limit — but AMC substitutes a learned `V_θ`, which breaks the exactness of the intermediate targets (though not of the final target when the final reward is known; the paper does not make this distinction).

### 2.3 SIR / bootstrap filter

"The most standard SMC method is the bootstrap filter (Gordon et al., 1993; Doucet et al., 2001), i.e., sequential importance resampling (SIR)." (p. 3) The proposal is the **prior itself**: "it is tractable to sample from our (fixed) LLM prior π, so we can sample trajectories `{s_{0:t}^{(i)}}` ~ π and then reweight them" (p. 3). This is the crux of black-box compatibility — a bootstrap proposal needs no density evaluation, only sampling.

Convergence claim (p. 4): "the weighted empirical measure of the resulting trajectories … converges weakly to π* as N → ∞ (Doucet et al., 2001)."

Resampling criterion: "an arbitrary boolean resampling criterion `c(t, {w_t^{(i)}}_{i=1}^N)`, which in experiments we simply set to true only on a cross-validated subset of timesteps." (p. 4)

**ESS criterion** (Appendix D.1.2, pp. 23–24), evaluated as an alternative: resample at step `t` using normalized weights `w̃_t^{(i)}` whenever

```
ESS := ( Σ_{i=1}^{N} (w̃_t^{(i)})^2 )^{-1}  <  N ρ ,    ρ ∈ (0,1)
```

Tested at `ρ ∈ {0.1, 0.5, 0.9}`; **fixed-step beat ESS at every threshold** (Table 8). The efficiency argument for fixed-step is important and easy to miss: "While ESS requires value network evaluation at every step, fixed-step allows the recursive update rule (Equation 4) to telescope, effectively skipping value estimation at most steps." (p. 24) Concretely, between two resampling steps `t_0` and `t`, `w_t ∝ exp(V(s_t) − V(s_{t_0}) + (1/β)Σ_{τ=t_0}^{t−1} r(s_τ))`, so only the endpoints of the interval need a value evaluation.

### 2.4 Where the theory is principled vs. heuristic

**Principled:**
- Prop. A.1 (KL-RL ⇔ Bayesian posterior) — exact, standard.
- Prop. A.2 (recursive weight decomposition) — exact given the definition of `V`; the paper notes it parallels Piché et al. (2019, App. A.4).
- SIR with the bootstrap proposal and optimal twist `V` is a textbook, asymptotically-consistent posterior sampler.
- Prop. A.4 (Appendix A.4, pp. 15–16): a genuine proof, via Cauchy–Schwarz, that SMC improves the *average* reward over N trajectories in a simplified binary-reward setting (details in §6).

**Heuristic / approximate:**
1. **`V → V_θ`.** The learned value replaces the exact twist. No error bound is given; the authors flag this as open ("AMC introduces potential error in the reward model … or value function, and it would be useful to understand its interplay with the already-extensive body of work around SMC", p. 9).
2. **Single-trajectory inner expectation (Eq. 7).** They "approximate the inner expectation by a single trajectory, thus accepting some bias through the logarithm to arrive at an estimator for the (non-soft) value function as the regression target" (p. 4). With one sample, `log E[e^X] ↦ log e^X = X`, i.e. the log-sum-exp collapses to the plain Monte-Carlo return — a Jensen-biased (downward) estimator of the soft value. Appendix D.2 additionally reframes this: because the training set keeps only the max-reward sample per task, "one can view Equation 7 as using a hard maximum to approximate the soft maximum of Equation 5" (p. 24). Note that a hard max is biased *upward* relative to the true soft value while the single-sample log collapse is biased downward — the two biases are described in the same breath without reconciling their directions.
3. **`β` conflated with sampling temperature.** "we set β = 1 for training and adjust the sampling temperature post hoc" (p. 4); Table 7 lists the row as "Temperature (β) 1.0". In the derivation `β` is the KL coefficient scaling the reward exponent in the likelihood; sampling temperature reshapes `π` itself (the prior). These are not the same knob, and changing temperature invalidates the identity of the proposal with the prior used in the weight derivation.
4. **`top-p = 0.95` truncation** (Table 7) means the actual proposal is a truncated `π`, not `π`. The weights are derived assuming the proposal *is* the prior, so there is an unaccounted proposal/prior mismatch. Not discussed.
5. **Resampling steps are cross-validated on the benchmark**, i.e. tuned per environment (WebShop `[6]`, SciWorld `[4, 12]`, TextCraft `[4]`) — a hyperparameter fitted with test-time knowledge of horizon structure.
6. **Selection rule.** Returning `argmax_i` cumulative reward is a Best-of-N-style selector on top of the SMC population, not a draw from the posterior. The theory (weak convergence of the weighted measure) justifies sampling from the normalized weights, not taking the reward-argmax. Appendix A.4 explicitly warns: "in this theoretical setup, AMC will not always have higher maximum reward, particularly when there already exist very strong trajectories" (p. 16) — which is exactly the observed TextCraft/GPT-5.1 inversion.
7. **Notation inconsistency:** Algorithm 1 line 6 (p. 13) writes `w_t^{(i)} = w_{t−1}^{(i)} e^{V(s̃_t^{(i)}) − V(s_{t−1}^{(i)}) + r(s_{t−1}^{(i)})}` — the `1/β` on the reward term of Eq. 4 is dropped. Harmless in the experiments because `β = 1`.

---

## 3. Algorithm

### 3.1 Algorithm 1 — Sequential Importance Resampling (Appendix A.1, p. 13, verbatim structure)

```
Require: reward function r, value function V, prior policy π, resampling criterion c,
         trajectory count N, time horizon T
Ensure:  posterior trajectories s_{0:T}^{(i)} and weights w_T^{(i)}, i = 1..N

1: Initialization: for i = 1..N, sample s_0^{(i)} ~ π(s_0); set w_0^{(i)} = 1/N
2: for t = 1 to T do
3:   for i = 1 to N do
4:     sample  s̃_t^{(i)} ~ π(s_t | s_{t−1}^{(i)})                 # black-box API call + env step
5:     set     s̃_{0:t}^{(i)} ← ( s_{0:t−1}^{(i)}, s̃_t^{(i)} )
6:     compute w_t^{(i)} = w_{t−1}^{(i)} · exp( V(s̃_t^{(i)}) − V(s_{t−1}^{(i)}) + r(s_{t−1}^{(i)}) )
7:   if c(t, {w_t^{(i)}}) then
8:     resample N trajectories {s_{0:t}^{(i)}} from {s̃_{0:t}^{(i)}} ~
              Multinomial( N, { w_t^{(i)} / Σ_j w_t^{(j)} }_{i=1..N} )
9:     reset w_t^{(i)} ← 1/N
10:  else
11:    s_{0:t}^{(i)} ← s̃_{0:t}^{(i)} for i = 1..N
```

At inference `V` is replaced by the learned `V_θ` (§2.4, p. 4).

### 3.2 AMC end-to-end (§2.4, p. 4)

1. **Offline, once per environment:** collect `{s_{0:T}^{(j)}}_{j=1}^{M} ~ π(s_{0:T})` on a training-task split; train `f_θ` by Eq. 7; set `V_θ = f_θ + r` (Eq. 6).
2. **Test time, per task:** run Algorithm 1 with `N = 15` particles, proposal = the black-box prior, weights from `V_θ`, multinomial resampling at the cross-validated fixed steps.
3. **Output:** `{s_{0:T}^{(i)}}, {w_T^{(i)}}` approximating `π*`; then "keep the trajectory with the highest cumulative reward or, if r is unavailable at test time, the trajectory i with the largest weight `w_T^{(i)}`."

### 3.3 Implementation specifics

- **Particle count:** `N = 15` throughout the headline tables. "Following the discussion in Section 2.2 in which we note that SMC approximates the optimal policy only as N → ∞, we are compelled to choose a reasonably large (but tractable) trajectory count; to this end, we use N = 15 trajectories throughout." (p. 6). Scaling curves go to `N = 20` (Fig. 4), `N = 25` (Fig. 3 right), `N = 20` in Table 11. Ablations in D.1/D.3 use `N = 5`.
- **Proposal:** the unmodified black-box policy, ReAct-style prompting (ReflAct additionally on SciWorld). Temperature 1.0, top-p 0.95, max output length 4096 (Table 7).
- **Horizons / max steps (Table 7):** WebShop 10, SciWorld 20, TextCraft 20. (Weather `T = 10`, Movie `T = 12` in D.6.)
- **Resampling steps (Table 7):** WebShop `[6]`, SciWorld `[4, 12]`, TextCraft `[4]`.
- **Resampling scheme:** multinomial, with replacement, weights reset to `1/N`.
- **No lookahead / partial rollout.** There is no MCTS-style simulation, no partial rollout for value estimation, and no tree expansion. Partial-trajectory value estimation is entirely amortized into `V_θ` — that is the whole point of learning it: "Estimating this expectation precisely would require multiple simulations of our agent until the terminal timestep T, which can quickly become computationally prohibitive. Instead, we opt to learn V" (p. 4).
- **Value-call budget:** "the value function is only evaluated on the initial state and on resampling steps and requires only a single prefill" (p. 8). With `N = 15` this is ≈ 1 + 15 = 16 value prefills per WebShop task and ≈ 31 per SciWorld task.

---

## 4. Value function

- **Parameterization (Eq. 6, p. 4):** `V_θ(s_t) = f_θ(s_t) + r(s_t)`, "where `f_θ` is a transformer-based model with a regression head that outputs a scalar prediction of `r(s_{t+1:T})` (if r were not available at test-time, we would parameterize `V_θ` directly)."
- **Architecture / init:** pre-trained instruct LLM backbone + **scalar regression head** + **LoRA** blocks, trained "in tandem" (p. 5). LoRA rank `r = 8`, `α = 16` (Table 7). Output format: "Scalar (float) value".
- **Backbones used:** Llama-3.2-11B (WebShop, TextCraft, Weather, Movie), Llama-3.1-8B (SciWorld, chosen to match Xi et al. 2025b), Qwen-2.5-3B and Qwen-2.5-7B (GRPO comparison), Qwen-3-4B (backbone-sensitivity ablation, Table 10).
- **Ideal objective (Eq. 5, p. 4):**
  ```
  V = argmin_{V_θ}  E_{p(t,s_t)} ‖ V_θ(s_t) − log E_{π(s_{t+1:T}|s_t)}[ e^{(1/β) Σ_{τ=t}^{T} r(s_τ)} ] ‖²₂
  ```
  where `p(t, s_t)` is "any distribution with full support over timesteps t and states s_t."
- **Practical loss (Eq. 7, pp. 4–5):**
  ```
  L(f_θ) = (1/P) Σ_{k=1}^{P} ‖ f_θ(s_{t_k}^{(k)}) − Σ_{τ=t_k+1}^{T} r(s_τ^{(k)}) ‖²₂
  ```
  i.e. **Monte-Carlo return-to-go regression, not TD.** `β = 1` at training time.
- **Loss inconsistency to note:** Table 7 (p. 16) lists the loss function as "Exponentially weighted MSE", whereas Eq. 7 is a plain squared error. The exponential weighting is never described in the text.
- **Training data collection (Appendix B, "Training Set Construction", pp. 16–17):** "We first sampled G trajectories per task and only included in our dataset the sample with the highest cumulative reward. In total, we generated M = 1.4k trajectories for WebShop and 1.1k for SciWorld, both using G = 3. For TextCraft, given the limited availability of only 44 tasks, we set G = 8." So the retained/filtered trajectory counts are M = 1.4k / 1.1k / 44, implying ≈ 4.2k / 3.3k / 352 raw rollouts. "By treating each individual step within a trajectory as a separate data point … we produced approximately **P = 14k, 22k, and 880 samples** for WebShop, SciWorld, and TextCraft," split **80% / 20%** train/validation.
- **Optimization (Table 7):** AdamW, lr `1e-5`, cosine schedule, warmup ratio `1e-1`, max grad norm 1, 3 epochs.
- **Compute:** two NVIDIA RTX 6000 Ada (48 GB each), 1 TB system RAM; ≈ **2 h** for the Llama-3.1-8B value model, ≈ **3 h** for Llama-3.2-11B (p. 16).
- **Per-environment, not per-task:** one value model per benchmark, trained on that benchmark's training tasks and generalizing to held-out tasks. Also *per-prior* in principle, though Table 6 shows cross-prior transfer.
- **Input modality: pure text.** The value prompts (App. C, pp. 19–20) feed `INSTRUCTION`, `HISTORY` (`(STATE, ACTION)` pairs), `NOW` (current state). Ranges are environment-specific: WebShop and TextCraft `0.0–1.0`, SciWorld `−1.0–1.0` (SciWorld can emit a terminal `−1` failure reward). The SciWorld prompt hard-codes a domain heuristic: "Heavily penalize states where the observation is 'No known action matches that input', as this indicates the agent is stuck in an invalid command loop or syntax error."

---

## 5. Experiments

### 5.1 Setup

- **Benchmarks:** WebShop (Yao et al., 2022) — e-commerce, open-ended action space, continuous reward from attribute overlap. SciWorld / ScienceWorld (Wang et al., 2022) — long-horizon science tasks, "a massive combinatorial action space of approximately 200K action-object combinations per step", terminal failure reward of **−1**, sub-goal milestone scoring. TextCraft (Prasad et al., 2024) — hierarchical Minecraft crafting, three primitive actions (`get`, `craft`, `inventory`), **sparse binary reward of 1** on success; only recipe depths **3 and 4** evaluated "since performance on lower-depth tasks has already reached saturation" (p. 16). All run through **AgentGym** (Xi et al., 2025a). Extras: **Weather** (18 actions, binary exact-match reward, `T = 10`, resample at 4) and **Movie** (16 actions, binary exact-match, `T = 12`, resample at 6) from AgentBoard (Ma et al., 2024).
- **Prior policies `π`:** Llama-3.2-11B-Instruct, Llama-3.1-8B-Instruct (open-weight); GPT-4.1-mini, GPT-5.1 (black-box); Qwen-2.5-3B (GRPO head-to-head). Footnote 4 (p. 6): "We do not use native reasoning capabilities of OpenAI models."
- **Baselines:** (1) single-trajectory **ReAct**; (2) single-trajectory **ReflAct** (SciWorld only); (3) **Best-of-N** (N = 15 headline) — highest-reward of N parallel trajectories; (4) **SMC (FoA)** — Fleet-of-Agents 2-shot prompt-based value, WebShop only, at the same resampling step AMC uses; (5) **SMC (Zero-shot)** — same LLM prompted for a scalar value with no training; (6) **GRPO**, treated as an oracle: "we consider GRPO to be an oracle rather than a typical baseline" (p. 7), both as reported by Xi et al. (2025b) and retrained by the authors (App. D.4).
- **Protocol:** 3 independent trials / seeds; mean ± standard error; primary metric = cumulative environment reward of the **highest-reward trajectory** of the N produced.

### 5.2 Main results

**Table 1 — WebShop** (AMC value model: Llama-3.2-11B)

| Policy `π` | Method | Score |
|---|---|---|
| Llama-3.2-11B | ReAct | 0.159 (±0.030) |
| | Best-of-15 (ReAct) | 0.562 (±0.012) |
| | SMC (FoA-ReAct) | 0.580 (±0.016) |
| | **AMC (ReAct)** | **0.625 (±0.009)** |
| GPT-4.1-mini | ReAct | 0.113 (±0.026) |
| | Best-of-15 (ReAct) | 0.403 (±0.016) |
| | **AMC (ReAct)** | **0.488 (±0.020)** |
| GPT-5.1 | ReAct | 0.171 (±0.017) |
| | Best-of-15 (ReAct) | 0.519 (±0.013) |
| | **AMC (ReAct)** | **0.543 (±0.009)** |

**Table 2 — SciWorld** (AMC value model: Llama-3.1-8B)

| Policy `π` | Method | Score |
|---|---|---|
| Llama-3.1-8B | ReAct | 0.013 (±0.014) |
| | Best-of-15 (ReAct) | 0.311 (±0.014) |
| | AMC (ReAct) | 0.347 (±0.015) |
| | ReflAct | 0.051 (±0.015) |
| | Best-of-15 (ReflAct) | 0.347 (±0.010) |
| | **AMC (ReflAct)** | **0.376 (±0.016)** |
| GPT-4.1-mini | ReAct | 0.250 (±0.023) |
| | Best-of-15 (ReAct) | 0.616 (±0.020) |
| | **AMC (ReAct)** | **0.673 (±0.009)** |
| GPT-5.1 | ReAct | 0.090 (±0.049) |
| | Best-of-15 (ReAct) | 0.533 (±0.026) |
| | **AMC (ReAct)** | **0.597 (±0.023)** |

**Table 3 — TextCraft** (AMC value model: Llama-3.2-11B)

| Policy `π` | Method | Score |
|---|---|---|
| Llama-3.2-11B | ReAct | 0.102 (±0.029) |
| | Best-of-15 (ReAct) | 0.296 (±0.019) |
| | **AMC (ReAct)** | **0.543 (±0.057)** |
| GPT-4.1-mini | ReAct | 0.432 (±0.055) |
| | Best-of-15 (ReAct) | 0.728 (±0.010) |
| | **AMC (ReAct)** | **0.852 (±0.020)** |
| GPT-5.1 | ReAct | 0.691 (±0.012) |
| | Best-of-15 (ReAct) | **0.889 (±0.000)** |
| | AMC (ReAct) | 0.790 (±0.021) |

The **one loss** is TextCraft + GPT-5.1 (0.790 vs 0.889). Diagnosis (p. 7): "GPT-5.1 generates shorter, higher-confidence trajectories, leaving little room for improvement and reducing the diversity of the value function's training data. This makes it more difficult for AMC to separate out promising trajectories, leading it to accidentally prune some. Based on this finding, we expect AMC to be most useful for model-task pairs where the model produces good but not uniformly perfect trajectories." Also note the SMC (Zero-shot) baseline beats AMC there (0.815 vs 0.790, Table 4).

**Table 13 — Weather / Movie** (Appendix D.6, p. 30; Llama-3.2-11B value model)

| Dataset | Policy | ReAct | Best-of-5 | SMC-ZS (5) | AMC (5) | Best-of-15 | SMC-ZS (15) | AMC (15) |
|---|---|---|---|---|---|---|---|---|
| Weather | Llama-3.2-11B | 0.433 (±0.041) | 0.517 (±0.020) | 0.483 (±0.054) | **0.533 (±0.020)** | 0.550 (±0.000) | 0.550 (±0.000) | 0.550 (±0.000) |
| Weather | GPT-4.1-mini | 0.450 (±0.035) | 0.483 (±0.041) | 0.517 (±0.020) | **0.550 (±0.000)** | 0.533 (±0.020) | 0.550 (±0.000) | 0.550 (±0.000) |
| Weather | GPT-5.1 | 0.550 (±0.000) | 0.550 (±0.000) | 0.550 (±0.000) | **0.567 (±0.020)** | 0.550 (±0.000) | 0.550 (±0.000) | **0.583 (±0.020)** |
| Movie | Llama-3.2-11B | 0.417 (±0.041) | **0.833 (±0.041)** | 0.767 (±0.054) | 0.783 (±0.020) | 0.850 (±0.000) | 0.850 (±0.035) | **0.883 (±0.020)** |
| Movie | GPT-4.1-mini | 0.750 (±0.061) | 0.800 (±0.035) | 0.817 (±0.020) | **0.850 (±0.000)** | 0.850 (±0.000) | 0.850 (±0.000) | 0.850 (±0.000) |
| Movie | GPT-5.1 | 0.867 (±0.020) | 0.883 (±0.020) | 0.867 (±0.020) | **0.900 (±0.000)** | 0.883 (±0.020) | 0.900 (±0.000) | 0.900 (±0.000) |

Weather saturates hard at 0.550 — most of the N=15 column is ties. Movie at N=5 with a Llama policy is an AMC loss to Best-of-5 (0.783 vs 0.833). The authors' own summary is appropriately hedged: "AMC consistently matches or outperforms the Best-of-N and SMC (Zero-shot) baselines across these new domains" (p. 26).

### 5.3 GRPO comparison

Figure 3 (SciWorld). **Left:** GPT-5.1 policy + Qwen-2.5-7B value model, vs GRPO with a Qwen-2.5-7B backbone; x-axis 1/3/5 trajectories. **Right:** Qwen-2.5-3B for AMC policy *and* value model, vs GRPO on the same backbone; x-axis 1/5/10/15/20/25. GRPO numbers in the figure are taken from Xi et al. (2025b). Text findings (pp. 7–8):
- "When backed by a GPT-5.1 policy (Figure 3, left), AMC outperforms GRPO with only N = 5 trajectories, achieving a performance level that remains unattainable for Best-of-N given the same trajectory budget."
- "when using the same LLM backbone for the policy of both GRPO and AMC (Figure 3, right), we find that when scaling AMC to 25 trajectories it outperforms fully fine-tuning this policy with GRPO."
- Conclusions: "(i) AMC performs comparably to GRPO using the same prior policy given enough trajectories (but requiring fewer than Best-of-N), and (ii) it can benefit even more from better black-box priors."
- Hardware asymmetry: "all AMC experiments were performed on a workstation with two RTX 6000 Ada desktop GPUs, whereas GRPO experiments required a node with eight A100 GPUs."

(The exact per-point values in Fig. 3 are not recoverable from the text extraction; only the two annotations `0.09` (left) and `−0.15` (right) appear, evidently plot labels.)

**Table 11 — retrained GRPO baselines + full cost accounting** (Appendix D.4, p. 25). Important methodological note: "Since Xi et al. (2025b) did not release their GRPO models, we used a 8xA100 40GB node to fine-tune our own GRPO baselines for 2 epochs, **removing a leak of 30% of test tasks identified in the AgentGym-RL codebase.**" Costs use AWS on-demand rates as of April 2026: **USD 21.96/hr for 8×A100** (GRPO training only) and **USD 5.27/hr for an NVIDIA RTX 6000 96GB**.

| Training | Method | Policy | Value model | Score | Training cost (USD) | Inference cost (USD) |
|---|---|---|---|---|---|---|
| GRPO | ReAct | Qwen-2.5-7B | – | 0.183 (±0.001) | 702.64 | 10.54 |
| GRPO | Best-of-5 | Qwen-2.5-7B | – | 0.190 (±0.001) | 702.64 | 55.32 |
| GRPO | Best-of-20 | Qwen-2.5-7B | – | 0.194 (±0.001) | 702.64 | 231.80 |
| – | **AMC (N=5)** | **GPT-5.1** | Qwen-2.5-7B | **0.518 (±0.030)** | **138.39** | 57.97 |
| GRPO | ReAct | Qwen-2.5-3B | – | 0.182 (±0.001) | 395.24 | 5.27 |
| GRPO | Best-of-5 | Qwen-2.5-3B | – | 0.185 (±0.003) | 395.24 | 12.28 |
| GRPO | Best-of-20 | Qwen-2.5-3B | – | 0.187 (±0.001) | 395.24 | 50.05 |
| – | AMC (N=5) | Qwen-2.5-3B | Qwen-2.5-3B | 0.133 (±0.006) | 147.51 | 47.41 |
| – | **AMC (N=20)** | Qwen-2.5-3B | Qwen-2.5-3B | **0.216 (±0.004)** | 147.51 | 210.73 |

Read honestly: with a *matched 3B policy*, AMC at N=5 (0.133) is **worse** than plain GRPO-ReAct (0.182) and only overtakes GRPO-Best-of-20 (0.187) at N=20 (0.216) — and at that point AMC's inference cost ($210.73) is 4.2× GRPO-Best-of-20's ($50.05), though AMC's total (147.51 + 210.73 = $358.24) still undercuts GRPO's (395.24 + 50.05 = $445.29). The large win comes from swapping in a *better black-box prior* (GPT-5.1: 0.518 at N=5), not from the algorithm alone. "GRPO with Best-of-N provides only marginal improvements over GRPO across different policies, as the GRPO policy is already converged with a low standard error across 3 trials." (p. 25)

### 5.4 Test-time compute scaling (Figure 4, p. 9)

Best-of-N vs AMC at N ∈ {1, 5, 10, 15, 20}, all with matched policy/value backbones (WebShop & TextCraft: Llama-3.2-11B; SciWorld: Llama-3.1-8B). Findings (p. 8): "When the number of trajectories is limited to 5 or 10, AMC demonstrates performance gains over the baseline with the exception of TextCraft. This exception is likely due to the reduced diversity of visited states arising from TextCraft's lower relative task complexity, and is further hampered by AMC's resampling; increasing the trajectory count resolves this by promoting more state space exploration. As N increases, **AMC generally achieves performance parity with Best-of-20 using only N = 15 trajectories, thereby reducing test-time-compute by approximately 25%.**"

### 5.5 LLM-call / cost accounting

- **Policy calls per task are the same as Best-of-N at equal N:** up to `N × T` = 150 (WebShop) or 300 (SciWorld/TextCraft) policy calls at N = 15. AMC adds **no extra policy calls** relative to Best-of-N — this is the sharp distinction from MCTS-style search, and the reason for the 25% claim (AMC@15 ≈ BoN@20 ⇒ 25% fewer calls for equal score).
- **Value-model calls per task:** ~16 (WebShop, one resampling step) to ~31 (SciWorld, two), each a **single prefill, no decoding**. "its computational overhead is small compared to the cost of the LLM policy, which performs both prefill and decoding at every step" (p. 8).
- **Upfront data collection:** `M × G` rollouts of `π` per environment (≈4.2k / 3.3k / 352), plus 2–3 GPU-hours of LoRA training.

**Table 5 — small black-box policy + AMC vs large black-box policy + Best-of-15** (p. 8; costs from average input/output token counts and the OpenAI pricing page)

| Dataset | Policy | Method | Score | Cost (USD) |
|---|---|---|---|---|
| WebShop | GPT-5.1 | Best-of-15 | 0.519 (±0.013) | 0.39 |
| WebShop | GPT-4.1-mini | AMC | 0.488 (±0.020) | **0.14** |
| SciWorld | GPT-5.1 | Best-of-15 | 0.533 (±0.026) | 0.18 |
| SciWorld | GPT-4.1-mini | AMC | **0.673 (±0.009)** | **0.06** |
| TextCraft | GPT-5.1 | Best-of-15 | 0.889 (±0.000) | 0.45 |
| TextCraft | GPT-4.1-mini | AMC | 0.852 (±0.020) | **0.21** |

"GPT-4.1-mini paired with AMC maintains a performance level similar to GPT-5.1 while reducing total cost by at least 50%." (p. 8)

---

## 6. Ablations

**(a) Trained vs. prompted value function — Table 4 (p. 8).** AMC vs SMC (Zero-shot), i.e. the same pre-trained LLM prompted to emit a scalar value with no training.

| Dataset | Policy | Best-of-15 | SMC (Zero-shot) | AMC |
|---|---|---|---|---|
| WebShop | Llama-3.2-11B | 0.562 (±0.012) | 0.556 (±0.014) | **0.625 (±0.009)** |
| WebShop | GPT-4.1-mini | 0.403 (±0.016) | 0.418 (±0.014) | **0.488 (±0.020)** |
| WebShop | GPT-5.1 | 0.519 (±0.013) | 0.522 (±0.008) | **0.543 (±0.009)** |
| SciWorld | Llama-3.1-8B | 0.311 (±0.014) | 0.305 (±0.013) | **0.347 (±0.015)** |
| SciWorld | GPT-4.1-mini | 0.616 (±0.020) | 0.640 (±0.022) | **0.673 (±0.009)** |
| SciWorld | GPT-5.1 | 0.533 (±0.026) | 0.506 (±0.041) | **0.597 (±0.023)** |
| TextCraft | Llama-3.2-11B | 0.296 (±0.019) | 0.296 (±0.043) | **0.543 (±0.057)** |
| TextCraft | GPT-4.1-mini | 0.728 (±0.010) | 0.765 (±0.025) | **0.852 (±0.020)** |
| TextCraft | GPT-5.1 | **0.889 (±0.000)** | 0.815 (±0.026) | 0.790 (±0.021) |

Verdict (p. 8): "SMC (Zero-Shot) delivers inconsistent performance gain over the Best-of-N baseline, underscoring that raw pre-trained knowledge is insufficient for precise state-value estimation in these agentic environments." Training helps in 8/9 cells.

**(b) FoA prompted value (Table 1, WebShop only).** SMC (FoA-ReAct) 0.580 vs Best-of-15 0.562 vs AMC 0.625 — "SMC (FoA) provides only marginal improvements over Best-of-15, while AMC clearly outperforms all methods" (p. 6).

**(c) Fixed resampling-step configuration — Figures 5/6/7 (Appendix D.1.1, pp. 23–24), N = 5.** WebShop grid `{4, 6, 8}` and combinations; SciWorld and TextCraft grids `{4, 8, 12, 16}` and all combinations. Findings: "increasing resampling frequency does not inherently guarantee higher performance … in TextCraft, a dense 4-step resampling approach actually hindered performance compared to more focused 1-step or 2-step interventions. Peak efficiency is reached through task-specific timing: a single intervention at step 6 for WebShop, a dual-step approach at steps 4 and 12 for SciWorld, and an early correction at step 4 for TextCraft. These results highlight that AMC's effectiveness is driven by precision-targeted interventions rather than sheer computational volume."

**(d) Fixed-step vs ESS dynamic resampling — Table 8 (p. 24), N = 5.**

| Dataset | Policy | Best-of-5 | ESS (ρ=0.1) | ESS (0.5) | ESS (0.9) | Fixed-step |
|---|---|---|---|---|---|---|
| WebShop | Llama-3.2-11B | 0.385 (±0.015) | 0.387 (±0.015) | 0.402 (±0.017) | 0.435 (±0.013) | **0.454 (±0.009)** |
| SciWorld | Llama-3.1-8B | 0.206 (±0.010) | 0.211 (±0.007) | 0.216 (±0.006) | 0.228 (±0.006) | **0.255 (±0.008)** |

Fixed-step wins on score *and* compute (telescoping). Higher ρ (more frequent resampling) monotonically improves ESS variants but never catches fixed-step.

**(e) Eq. 5 vs Eq. 7 value objective — Table 9 (p. 24), N = 15, SciWorld, Llama-3.1-8B.** Eq. 7 (single trajectory): **0.347 (±0.015)**; Eq. 5 (3 trajectory estimates to approximate the log-sum-exp): 0.332 (±0.005). "the single trajectory approximation yields comparable performance to the multiple trajectory baseline." The cheap, biased estimator is not worse.

**(f) Value-model backbone sensitivity — Table 10 (p. 25), N = 5.**

| Dataset | Policy | Value model | Score |
|---|---|---|---|
| WebShop | Llama-3.2-11B | Llama-3.2-11B | 0.454 (±0.014) |
| | | Qwen-3-4B | 0.444 (±0.017) |
| SciWorld | Llama-3.1-8B | Llama-3.1-8B | 0.249 (±0.011) |
| | | Qwen-3-4B | 0.236 (±0.011) |
| TextCraft | Llama-3.2-11B | Llama-3.2-11B | 0.234 (±0.012) |
| | | Qwen-3-4B | 0.222 (±0.043)|

"This confirms the architectural generalizability of the value function." A 4B value model nearly matches an 11B one.

**(g) Prior surrogates / value-function transfer — Table 6 (p. 9).** Train `V_θ` on trajectories from a *cheap open-weight* surrogate instead of the expensive API prior, then deploy with GPT-4.1-mini as `π`.

| Dataset | Policy | Method | Surrogate | Score |
|---|---|---|---|---|
| WebShop | GPT-4.1-mini | Best-of-15 | – | 0.403 (±0.016) |
| | | AMC | Llama-3.2-11B | 0.467 (±0.028) |
| | | AMC | None (`π` itself) | **0.488 (±0.020)** |
| SciWorld | GPT-4.1-mini | Best-of-15 | – | 0.616 (±0.020) |
| | | AMC | Llama-3.1-8B | 0.645 (±0.023) |
| | | AMC | None | **0.673 (±0.009)** |
| TextCraft | GPT-4.1-mini | Best-of-15 | – | 0.728 (±0.010) |
| | | AMC | Llama-3.2-11B | 0.790 (±0.025) |
| | | AMC | None | **0.852 (±0.020)** |

"using a surrogate policy uniformly outperforms Best-of-N. Although sampling trajectories from π itself performs better than using a surrogate policy, these results still demonstrate transferability of V_θ as a viable way to reduce cost." Distribution shift costs ~2–6 points.

**(h) Average-trajectory metric — Table 12 (Appendix D.5, p. 26).** Mean reward of the **top 5 of 15** trajectories, Llama-based policies.

| Dataset | Best-of-15 (Top-5) | AMC (Top-5) |
|---|---|---|
| WebShop | 0.202 (±0.014) | **0.371 (±0.013)** |
| SciWorld | 0.183 (±0.010) | **0.226 (±0.014)** |
| TextCraft | 0.215 (±0.030) | **0.462 (±0.020)** |

"AMC significantly elevates the overall quality of the top-performing trajectories within the trajectory pool, rather than relying on isolated high-reward outliers."

**(i) Theory ablation — Appendix A.4 (pp. 15–16).** Simplified sparse binary-reward setting, `p_t^{(i)} = P(r(s_T^{(i)}) = 1 | s_t^{(i)})`, `β = 1`, `r(s_t) = 0` for `t ≠ T`, one resampling event at `t`, perfect value function. Then `V(s_t^{(i)}) = log[e p_t^{(i)} + (1 − p_t^{(i)})]`, unnormalized weights `w_t^{(i)} = ((e−1)p_t^{(i)} + 1) / ((e−1)p_0^{(i)} + 1)`, and since all particles share `s_0` the denominator cancels:
```
w̄_t^{(i)} = ((e−1) p_t^{(i)} + 1) / ( (e−1) Σ_j p_t^{(j)} + N )
E[Reward_AMC] = Σ_i w̄_t^{(i)} p_t^{(i)}
              = [ (e−1) Σ_i (p_t^{(i)})² + Σ_i p_t^{(i)} ] / [ (e−1) Σ_j p_t^{(j)} + N ]
              ≥ (1/N) Σ_i p_t^{(i)} = E[Reward_N]          (Cauchy–Schwarz)
```
"the inequality is strict unless all states have the same success probability (which is essentially never true in practice)." Crucially the same appendix concedes: "in this theoretical setup, AMC will not always have higher **maximum** reward, particularly when there already exist very strong trajectories (e.g., a two-trajectory binary success case where one trajectory is guaranteed to succeed, and one is guaranteed to fail)." The guarantee is over the *mean*, not the *max* — and the headline metric is the max.

**(j) Agent scaffold ablation.** ReAct vs ReflAct on SciWorld (Table 2): ReflAct is better as a base scaffold (0.051 vs 0.013 single-shot) and AMC compounds on top (0.376 vs 0.347), "highlighting the potential for compounding gains when combining AMC with more advanced decision-making agents" (p. 6).

**(k) Qualitative value-function diagnostics — Appendix E (pp. 26–29).** WebShop step 6: promising trajectory scored 0.6 → reward 1.0; failing trajectory (returned to the search page) scored 0.1 → reward 0.0. SciWorld step 12: 0.4 → 1.0 vs 0.1 (stuck in "No known action matches that input" loop) → 0.0. TextCraft step 4: 0.275 vs 0.191, where "both trajectories align with the optimal policy" but Case 1 is further down the crafting dependency tree — evidence of granular ordering, though the absolute values are compressed.

---

## 7. Limitations

### 7.1 Stated by the authors (§5, p. 9)

1. "our learned value function will never perfectly approximate the true value function." Proposed fixes: temporal-difference learning, reward shaping, or better approximating Eq. 5.
2. "the computational overhead that comes with having to sample multiple trajectories in parallel during inference." Proposed fix: distilling AMC or a component of it into a smaller network.
3. Resampling-criterion design is unresolved (see §9).
4. Missing theory: "AMC introduces potential error in the reward model or value function, and it would be useful to understand its interplay with the already-extensive body of work around SMC."
5. Performance inversion when the prior is already near-ceiling (TextCraft + GPT-5.1), §4.2, p. 7.
6. Upfront API cost of collecting `M × G` trajectories from `π` (mitigated but not eliminated by surrogates, Table 6).

### 7.2 Environment rewind — the central unstated requirement

**AMC requires the ability to fork/clone the environment mid-episode.** The paper never says this. Resampling (Algorithm 1 line 8) draws `N` trajectories *with replacement* from `{s̃_{0:t}^{(i)}}`. When particle `j` is duplicated `k > 1` times, `k` independent continuations must proceed from the *same* environment state `s_t^{(j)}`, while pruned particles' environments are discarded. Since footnote 2 folds the transition dynamics into `π`, the resampled object is a state trajectory, and the environment's internal state (WebShop cart/session, SciWorld world model, TextCraft inventory) must be materialized `k` times.

Practical implementations are: (a) snapshot/deep-copy the simulator state at each resampling step; or (b) spawn a fresh environment and **replay** the winning action prefix (only sound if the environment is deterministic given actions, which holds well enough for these three AgentGym simulators). Either way it is **simulator access, not merely API-only agent access** — which is a much stronger assumption than "black-box agent." The word "rewind", "clone", "snapshot", "checkpoint", or "fork" appears nowhere in the paper; the closest the paper comes is "the nature of many digital environments allows us to simulate agent trajectories in parallel" (p. 3). For a truly irreversible deployment target (a live checkout, a real email send, a production database), the resampling step is not implementable as described; AMC would degrade to a weighted-selection scheme over already-completed trajectories, i.e. Best-of-N with a learned reranker.

The `1 + N` value-call accounting has the same implication: it presumes the resampling step is a synchronization barrier across all `N` environment instances, so all particles must be advanced in lockstep, not run as independent asynchronous episodes.

### 7.3 Other unstated-but-material limitations

- **Reward is needed at test time**, twice over: inside `V_θ = f_θ + r` (Eq. 6) and for the final `argmax` selection. The paper offers fallbacks (parameterize `V_θ` directly; select by `w_T^{(i)}`) but never *evaluates* the reward-free variant. Every reported number therefore assumes a live reward oracle at inference — which many "real-world" black-box deployments (the paper's own motivation) do not have.
- **Optimism / risk-seeking bias from folding dynamics into `π`.** In control-as-inference, conditioning a trajectory posterior that includes transition probabilities on optimality also tilts the dynamics, producing risk-seeking behaviour (Levine, 2018, §4). AMC's resampling literally selects environment outcomes that turned out well. This is benign in near-deterministic simulators but would bias behaviour in genuinely stochastic environments. Not discussed.
- **Proposal ≠ prior.** Weights are derived assuming the proposal is exactly `π`, but sampling uses `top-p = 0.95` and a post-hoc-adjusted temperature. `β` is also identified with sampling temperature (Table 7), conflating the KL coefficient with the prior's sharpness.
- **Resampling steps are tuned per environment** using the benchmark's own performance (Figs. 5–7), i.e. a hyperparameter search on the evaluation distribution. Transfer of these schedules to a new environment is untested; the whole ablation is at `N = 5` while headline results are at `N = 15`.
- **Loss mismatch:** Table 7's "Exponentially weighted MSE" is not the plain MSE of Eq. 7 and is never defined.
- **`V_θ` is per-environment.** A new environment requires a new rollout collection and a new LoRA training run; there is no evidence of cross-environment transfer (Table 6 is cross-*prior* transfer within an environment).
- **Diversity collapse is the failure mode**, and it is intrinsic to SIR: resampling reduces particle diversity by construction, which is precisely the "sample impoverishment" that CriticSMC was designed to address (cited on p. 5) — AMC's mitigation is simply resampling rarely, at hand-tuned steps.
- **Scale of gains is modest against the right baseline.** Against Best-of-15 the median improvement is a few points; the largest wins (TextCraft/Llama 0.296→0.543) come where Best-of-N is weakest. Against a matched-backbone GRPO, AMC needs N = 20 to win (Table 11).
- **Small effect sizes vs. reported standard errors.** 3 seeds, and several deltas are ~1.5–3 SE (e.g. WebShop GPT-5.1: 0.519±0.013 → 0.543±0.009). No significance tests.
- **Value-model context grows with `t`** since the prompt includes the full `(STATE, ACTION)` history; long-horizon scaling of the value prefill is not analyzed.
- **`V_θ` never sees privileged environment state** — only text. That is a feature for generality but caps its accuracy in environments with hidden state.
- **No results on genuinely long-horizon or software-engineering agents**, despite the motivation naming software engineering; horizons are `T ∈ {10, 12, 20}`.
- **GRPO numbers in Fig. 3 come from Xi et al. (2025b)**, whose codebase the authors themselves found had "a leak of 30% of test tasks" (p. 25). The figure's GRPO line and Table 11's retrained GRPO line are therefore not comparable, and the paper's headline "outperforms GRPO" claim rests partly on the leaked-baseline figure.

---

## 8. Exact quotable claims

1. p. 1 (Abstract): "We propose Agentic Monte Carlo (AMC) to directly sample from the optimal policy of a black-box agent rather than training it through RL."
2. p. 1 (Abstract): "We employ Sequential Monte Carlo to sample from this posterior by learning a value function to steer the agent while leaving the underlying black-box model unchanged."
3. p. 1: "these approaches rely on a fundamental assumption: white-box access to model parameters to compute policy gradients."
4. p. 2: "instead of updating the parameters of the prior (which we cannot access), we can simply sample from the posterior distribution to recover optimal behaviour."
5. p. 2: "exact sampling from this optimal posterior is impossible for black-box agents, whose full log-probabilities are generally unavailable, and intractable even for white-box agents because of the high-dimensional action space and long time horizons inherent in agentic environments."
6. p. 3: "Equation 2 frames KL-regularized RL as a Bayesian inference problem where the reference policy's probabilities π(s0:T)—which serve as the prior—are modulated to upsample high-reward trajectories as encoded by the likelihood e^{r(s0:T)/β}."
7. p. 4: "In our setting, r(st) is available at test time, so we only need to learn future rewards."
8. p. 4: "we approximate the inner expectation by a single trajectory, thus accepting some bias through the logarithm to arrive at an estimator for the (non-soft) value function as the regression target."
9. p. 5: "these methods modulate the proposal distribution via access to the LLM's logits, a requirement that is impossible to satisfy with proprietary black-box models; in contrast, AMC is designed specifically for black-box settings."
10. p. 7: "we expect AMC to be most useful for model-task pairs where the model produces good but not uniformly perfect trajectories."
11. p. 7: "To clarify, AMC is not intended to replace GRPO, but to serve as a viable alternative when GRPO is not possible. We thus consider GRPO to be an oracle rather than a typical baseline."
12. p. 8: "all AMC experiments were performed on a workstation with two RTX 6000 Ada desktop GPUs, whereas GRPO experiments required a node with eight A100 GPUs."
13. p. 8: "SMC (Zero-Shot) delivers inconsistent performance gain over the Best-of-N baseline, underscoring that raw pre-trained knowledge is insufficient for precise state-value estimation in these agentic environments."
14. p. 8: "AMC generally achieves performance parity with Best-of-20 using only N = 15 trajectories, thereby reducing test-time-compute by approximately 25%."
15. p. 16: "in this theoretical setup, AMC will not always have higher maximum reward, particularly when there already exist very strong trajectories."
16. p. 23: "AMC's effectiveness is driven by precision-targeted interventions rather than sheer computational volume."
17. p. 24: "While ESS requires value network evaluation at every step, fixed-step allows the recursive update rule (Equation 4) to telescope, effectively skipping value estimation at most steps."
18. p. 25: "we used a 8xA100 40GB node to fine-tune our own GRPO baselines for 2 epochs, removing a leak of 30% of test tasks identified in the AgentGym-RL codebase."

---

## 9. Open problems named by the authors (§5, p. 9)

1. **Better value learning:** "Potential ways to improve the training procedure include temporal difference learning, (Sutton, 1988), reward shaping (Ng et al., 1999), or better approximating Equation 5."
2. **Inference-cost reduction by distillation:** "Greater efficiency may be attained, for example, by distilling AMC or some component of AMC into a smaller network (Hinton et al., 2015)."
3. **Multi-agent extension:** "AMC can also be extended to broader settings, like multi-agent black-box agents."
4. **Learned resampling triggers:** "developing auxiliary models trained to trigger resampling."
5. **LLM-judge-based resampling placement:** "leveraging LLM-based judges to identify specific trajectory states where resampling is likely to yield the highest marginal gain (Feng et al., 2026)."
6. **Adaptive resampling / HPO:** "employing adaptive resampling frameworks or hyperparameter optimization techniques to arrive at more sophisticated choices of resampling intervals (Doucet et al., 2001; Chopin et al., 2020)."
7. **Theory:** "there remains theory to be built around our method. AMC introduces potential error in the reward model (Huang et al., 2025) or value function, and it would be useful to understand its interplay with the already-extensive body of work around SMC (Crisan & Doucet, 2000; Moral, 2004; Marion et al., 2023)."
8. Implicit in §4.2: understanding and fixing the near-ceiling inversion, and the diversity-collapse coupling between resampling density and state-space exploration.

---

## Appendix: positioning against prior work (§3, p. 5)

- **Piché et al. (2019)** — SMC for planning in control-as-inference; continuous control. **Lioutas et al. (2023), CriticSMC** — learned soft Q-function to weight particles against sample impoverishment in sparse reward; deterministic transitions. "AMC adapts these principles to the discrete, semantic state space of black-box LLM agents, where gradients are inaccessible and state transitions are defined by opaque—possibly stochastic—tool interactions, rather than physics engines."
- **Lew et al. (2023); Zhao et al. (2024) twisted SMC; Loula et al. (2025)** — require logits; constrained *text generation* rather than agentic workflows.
- **Puri et al. (2025), "Rollout Roulette"** — SMC with pre-trained PRMs for test-time reasoning. AMC differs by targeting "multi-step, interactive agents where the state includes external environment observations, necessitating a critic trained on interaction history rather than a standard reasoning PRM," and by "viewing the critic not just as a heuristic verifier but as a learned proxy for the (soft) value function in a control-as-inference formulation."
- **LATS (Zhou et al., 2024), ExACT (Yu et al., 2025)** — MCTS-style; "often suffer from high latency due to sequential node expansion."
- **Fleet of Agents (Klein et al., 2025)** — genetic-style particle filter but "relies on static, heuristic value functions (e.g., lexical overlap or prompting)."
