# MySelfPlayAgent — 双 LLM 自博弈反思进化方案（R 线，Plan v1）

> **定位**：本文是 `mydocs/environment-plan.md`（E 线，E0a–E7 已全部 Gate 通过）的**下一阶段方案**。
> E 线交付了「确定性回合内核 + 迷雾 + 真实 LLM 玩家 + 自博弈编排 + 轨迹落盘/重放 + 观战」，
> 但打完每局轨迹只是"存下来了"——**没有任何环节把对局转成下次决策可用的经验**。
> 本文回答一个问题：**让两个 LLM 在不断的自我博弈对局中反思进化对战能力，如何实现。**
>
> **依据**：`mydocs/self-rl-plan/` 下的五份参考——`Self-Play-RL-Plan_v1/v2`、`GEPA.pdf`、
> `SkillOpt.pdf`、`MemRL.pdf`、`AMC.pdf`。四篇 PDF 已全文精读交叉核验（GEPA：实例级 Pareto 选择是最大收益组件、
> Merge 模型相关不稳定、验证预算占大头、**对手进化=非平稳需冻结基准库**；SkillOpt：`L_t` 有界本身是收益来源、
> 门禁双游标、慢更新+meta 是最大消融项 −22.5；MemRL：`k1=10/k2=3`、z-score 标准化是关键、Q 只更新被注入条目、
> 广播弱点是自认局限；AMC：`β=1` 固定、稀疏定点重采样优于 ESS、强先验饱和时 SMC 有害）。
>
> **原则**：沿用 E 线全部协作约定（负责人 Gate、one-command-to-verify、`mydocs/` 不入库、环境铁律
> `environment` 永不 import `rock_pvp_agent`、引擎零第三方依赖、确定性/迷雾/马尔可夫三条不变式）。
> 每个里程碑一个 Gate，负责人亲自跑验收命令并明确说"通过"才进下一步。

---

## 〇、一句话主线

> **冻结的模型是先验，变强的是围绕它的四件外挂。**
>
> 模型权重不可动（闭源 LLM），所以所有"变强"都沉淀在**模型之外的版本化工件**里：
> ① 一份被 **GEPA Pareto 前沿**引导、**SkillOpt 有界编辑**、严格门禁筛选过的多模块**战术手册（Playbook）**；
> ② 一个按**实际因果贡献**更新效用的**情境记忆库（Situation Memory）**，对应 **MemRL**；
> ③ 一个零梯度的**价值估计器**（`V_heuristic` 起步，档位 A 训练可选），供信度分配与不确定性门控；
> ④ 一个随对局不断进化的**对手池/构筑池**（对应 **AMC** 关键回合后验采样在 R7 可选接入）。
>
> **共同底线（三条铁律，不可协商）**：
> 1. **模型永不担任裁判。** 奖励只来自确定性引擎的胜负事实（`state_hash`/事件流），绝不来自 LLM 自评。
> 2. **构筑与对战分层交替优化。** 两个信号时间尺度差 2–3 个数量级，混在一起优化互相污染。
> 3. **打赢上一代不是目标，"降低可利用性"才是。** 本游戏存在非传递环（攻击/防御/状态三角 + 系别克制），
>    朴素自博弈会在环上打转并产生虚假进步。必须用**联赛 + Pareto 池 + 专职剥削者 + 历史回归门**度量真实强度。

---

## 一、为什么本项目的现有基础"正好"够用（先对齐地基）

E 线交付的每一块，恰好是自进化方案的最低前置。下表是**直接复用**清单：

| E 线资产 | 位置 | 自进化里干什么 |
|---|---|---|
| 确定性纯转移 | `models.BattleState` + `to_dict/from_dict/clone` + `step` | 反事实回放、SMC fork、轨迹重放分析——"替换一个动作重跑"在结构上可能 |
| 逐回合 `state_hash` | `match.TurnRecord.state_hash` | 轨迹指纹、回归门鉴权、报告纪律 |
| 轨迹重放 | `replay.replay_record` | 任何分析都能从 `(rules, rosters, seed, 提交序列)` 重放再算，轨迹存储不必改 |
| `Player` Protocol + 注入缝 | `environment/players.py` + `run_selfplay(players=...)` | 自进化编排直接构造带策略上下文的玩家注入，编排器零改动 |
| 迷雾收口 | `drive_turn` 只喂 `view()` + `filter_events_for` | LLM 玩家与反思管线自动只见白名单，信度分配/记忆检索天然不涉密 |
| 事件流 | `events.py` 13 类事件 | 是 GEPA `feedback_text`（评估痕迹）的**唯一原料**，确定性渲染 |
| 玩家历史 | `LLMPlayer._history` | Playbook/记忆的注入点（系统提示 + 每回合上下文） |
| 数据源 | `valid_skills.json`（179 已实装效果技能）+ `full_spirits.json`（593 只） | 构筑搜索的真实约束空间（技能池/系别/血脉/家族唯一） |

**核心结论**：本方案**不需要改引擎**。引擎保持零第三方依赖、确定性不变式不变；
所有 LLM 相关能力（反思/编辑/门禁）住在 agent 层新包 `battle/evolution/`，跨线只靠
`Player` Protocol 与纯函数分析模块。

---

## 二、本游戏的博弈结构（决定算法选型）

动手前先钉死结构，因为它决定了整套算法选择。结论与 v1 §2 一致，但落到本项目数值：

| 性质 | 本项目情况 | 对方案的约束 |
|---|---|---|
| 完美信息？ | **否**。对手技能/能量/后备/增减益部分隐藏（迷雾白名单） | 不能纯 minimax；需要信念状态（对手已揭示技能数/命数/能量档） |
| 同时行动？ | **是**（双方同时提交，应对三角靠同时声明） | 存在混合策略纳什均衡；确定性策略可被读穿 |
| 传递性？ | **弱**。攻击>状态>防御>攻击 + 18 系克制 + STAB | 朴素自博弈会循环；必须用 Pareto 池 + 联赛 + 回归门 |
| 可微？ | **否**（闭源权重） | 学习必须外化为文本/表格/小模型 |
| 可仿真？ | **是**（确定性引擎，固定 seed 逐字节复现） | 可无限生成对局 + 反事实重放，rollout 成本主要是 LLM token |
| 奖励密度？ | **稀疏**（终局胜负），可派生密集代理（命数差/血量比差/能量差） | 需要信度分配：把终局胜负归到具体回合 |
| 状态维度？ | **低**（每方 ≤6 只精灵 × 六维，迷雾特征 ~10 个标量） | 价值函数用手工特征足够（档位 A），不必上 3B 小模型 |

**正确技术组合**（v1 §2 结论，映射到本项目）：

```
信度分配：   校准偏差（battle_act 加 prediction，零成本）→ 价值落差（V_heuristic）
              → 反事实回放（代理尾策略 M=24，确定性）
文本进化：   GEPA 候选池（实例 = 对手×阵容族×种子）≡ 自博弈种群 + SkillOpt 有界编辑 + 两级门禁
部署期记忆： MemRL 两阶段检索（相似度门 → 效用重排）注入决策上下文
防坍缩：     Pareto 池自动保留多样性 + 专职剥削者 + 历史回归门 ≥45%
推理期增强： AMC 关键回合后验采样（R7 可选，门控启用）
```

---

## 三、学习对象：什么在"变强"（四件外挂，全部可版本化、可回滚）

模型不变，变的是下面这些工件。**每个都是不可变、带版本、可回滚**。

### 3.1 Playbook（战术手册，主要学习载体）

把 v2 §3.1 的 5 模块拆法照搬到本项目。每个模块是一份独立自然语言指令块，注入
`LLMPlayer.on_match_start` 的系统提示，作为冻结模型的可训练状态（SkillOpt 的
skill-as-trainable-state）。

```
M1  team_reader     对手建模：从公开事件推断对手配置倾向/节奏（应对三角读法、能量威胁）
M2  action_selector 技能/换人选择（主决策模块）
M3  energy_planner  能量收支规划：何时回能、何时透支、何时换人白嫖一回合
M4  endgame         残局（任一方剩 1 命）：一击必杀线、换人吸伤、锁胜
M5  build_proposer  构筑提议（只在 R6 构筑阶段激活，对战期不调用）
```

- 硬上限：单模块 **800 token**，M1–M4 合计 **≤2,500 token**（SkillOpt 实测最终技能
  379–1,995 token，且**全部收益来自 1–4 次被接受的编辑**——设计目标不是写长手册）。
- `[PROTECTED]` 区域：只有 epoch 级慢更新能写，快速步进编辑不得覆写。
- 被拒绝编辑缓冲区：被门禁拒掉的编辑连同分数下降存档，作为下一轮反思的负面证据。
- 模块化附带收益：GEPA 的 system-aware Merge 可做模块级交叉（R6 之后可选）。

### 3.2 Situation Memory（情境记忆库，MemRL 底座）

Playbook 学**可泛化流程规则**；局面特异知识（"面对这个具体阵容这个开局，先换人再回能"）
进可检索记忆库。三元组 `(意图 z, 经验 e, 效用 Q)`：

```json
{
  "entry_id": "mem_0a1f",
  "side": "a",
  "lineage_family": "energy_denial",        // D_tr/D_sel/D_test 隔离键
  "situation_key": "my2/foe3/迪莫/火苗/low/mid/2/mid/1/1",
  "situation_text": "能量偏低、我方剩2命、场上被高速攻击型压制、对手后台疑有状态型",
  "experience_text": "换人（pivot）吸一回合伤害同时自然回能，比原地回能少挨一次高倍率攻击",
  "action": {"type": "switch", "value": 1}, // 采纳判定用：决策与它匹配 = 被采纳
  "Q": 0.41, "n_used": 17, "n_adopted": 9,
  "provenance": {"rules_version": "…", "playbook_version": "pb_v037", "data_digest": "…"}
}
```

**成功与失败都存**（MemRL 的脚本/反思模板二分）：失败记忆记录"这样出会被读穿"，
在同时行动博弈里尤其值钱。

### 3.3 价值函数 V（两档，唯一可能出现梯度的位置）

- **档位 A-0（零梯度，默认起步）**：`V_heuristic = 0.6·命数差 + 0.3·血量比差 + 0.1·能量差`
  （纯确定性代码，可单测）。用于价值落差定位、反事实回放的 delta 基线、SMC 门控。
- **档位 A-1（可选，R7 前不用）**：手工特征 + 梯度提升树在自博弈轨迹上回归胜率。
  特征 = 命数差/双方血量比/能量差/克制关系得分/对手已揭示技能数/后台存活数/回合序数。
  只在该模型中局胜率预测 AUC 长期 <0.75 时才启用。
- **明确不做档位 B（3B 小模型）**：本游戏状态维度低，手工特征够用；
  且 AMC 消融显示**纯 prompting 的价值估计不可靠**，但档位 A 是零梯度学习器，仍是学习器。

### 3.4 构筑池（Build Book）与对手池（Opponent Pool）

- **Build Book**：带元数据的阵容策略池（`archetype` / `counters` / `countered_by` /
  `meta_share`）。`counters` 等由联赛经验收益矩阵算出，**不由 LLM 断言**。
- **对手池**：Playbook 历史版本 + 当前 Champion + 专职剥削者 + 固定启发式/极端风格。
  构成与采样见 §七。

---

## 四、核心设计：Pareto 候选池 ≡ 自博弈种群（v2 §4 的最重要增量）

把 GEPA 的"实例级 Pareto 候选选择"与 PSRO 的"策略种群"合并成一个数据结构：

- **实例** = `(对手策略 o, 阵容族 f, 种子集)` 三元组。
- **候选的分数向量** = 对各实例的配对胜率向量。
  - "候选 A 支配候选 B" = A 对每个实例都不低于 B → 正是元博弈的策略支配关系。
  - "Pareto 前沿" = 不被任何单一策略全面压制的策略集合 → 非传递游戏必须保留的多样性。
  - "按领先实例数加权采样父代/对手" = 按"能打赢多少种对手"加权 → 近似元博弈支撑集权重。

三个免费好处（对 E 线方案尤其关键）：

1. **防策略循环免费**。三代前的候选只要还在某个对手上领先就不会被淘汰，自动留在池中
   继续被采样为父代和对手——v1 需要手工 PFSP 配比，这里对手分布由数据决定。
2. **对手分布不用手调**。手工配比降级为当前沿退化到 1–2 个成员时的兜底。
3. **非传递性从缺陷变成资产**。收益矩阵反对称分量高 → 前沿宽 → 多样性高 → 不易早熟；
   它同时是池健康度指标（掉到接近 0 说明池坍缩）。

池参数：前沿上限 **`P_max = 12`**；超出剔除"领先实例数最少且与另一成员胜率相关性 >0.9"的
成员（冗余而非弱），被剔除成员进**历史池 archive**（永不删除，仍作回归对手）。

---

## 五、单步优化循环（R 线主算法，一个 step 的完整流程）

```
输入: 候选池 P（每候选 = 5 模块文本 + D_sel 分数向量）
      被拒绝编辑缓冲区 R（epoch 局部）· Meta Playbook（只给优化器）

① 父代选择    c ← ParetoSelect(P)                      # GEPA Alg.2：按领先实例数加权
② 模块选择    m ← RoundRobin(M1..M4)                   # GEPA 模块轮询
③ 对手采样    o ← 从 Pareto 前沿按领先实例数加权采样    # §四；退化时 PFSP 兜底
④ Rollout     用 c 对 o 跑 B=8 场配对对局（双向先后手、固定种子、固定阵容族）
              → 轨迹落盘 + 逐回合 prediction + 事件流
⑤ 信度分配    → 关键回合卡片（§六），按 module_attribution 过滤出属于 m 的
⑥ 双分析师     FailureAnalyst（失败卡 + 被拒缓冲）· SuccessAnalyst（成功卡）
               → 结构化 add/delete/replace 编辑提案
⑦ 分层合并    失败侧内合并 → 成功侧内合并 → 两侧合并（失败优先）；去重、消解矛盾
⑧ 有界截断    按 (证据条数 × 平均 delta_winrate × 场景覆盖度) 排序截到 L_t 条
               L_t 默认 4，余弦退火到 1；不得覆写 [PROTECTED]
⑨ 廉价门      候选 c' 在同一 minibatch 重跑；未改进 → 写入 R，本 step 结束
⑩ 全量门      c' 在 D_sel 评测 → 分数向量
               入池条件：向量在至少一个实例上取得池内最优（进 Pareto 前沿）
               晋级条件：复合分严格超过当前 Champion（平手拒绝），且超过历史最优才更新
               best_skill（SkillOpt「双游标」：score_cur 推进，score_best 才封存）
```

**两级门禁的分工**（GEPA 宽 + SkillOpt 严，二者目标不同）：
- 入池门（宽）：只要在某个对手上做到池内最优就入池 → 保多样性，对抗非传递性。
- 晋级门（严）：成为线上 Champion 必须复合分**严格超过** → 防漂移；
  SkillOpt 明确指出严格不等号让"被拒绝的编辑成为有信息的负反馈，而不是隐藏状态"。

Champion 复合分：

```
score = 0.70 · 对 Pareto 前沿按 meta_share 加权的配对胜率
      + 0.15 · (1 − 可利用性)              # 专职剥削者攻破率
      + 0.10 · 预测校准 (1 − Brier)        # prediction vs 实际
      + 0.05 · 合法率与降级率健康度
```

---

## 六、三个关键算法（本方案最重要的工程投入）

### 6.1 反馈函数 μ_f：暴露机制而非结论（GEPA 的核心，必须确定性渲染）

`feedback_text` **完全由事件流确定性渲染**，绝不由 LLM 撰写或评分。模板（输入引擎事件 +
逐回合 prediction + 反事实结果）：

```
[T23] 我方行动: skill(抓挠1, 槽0) 能量 5→2 · 预测: "造成约30伤害并逼换"
      对手行动: skill(防御)         [同时行动，我方被读中]
      结算: 伤害 21 → 被减伤至 6；对手血量 61%→55%
      校准: 预测 30 vs 实际 6，偏差 -80%  ★认知错误
      反事实: 若换人(槽1) → 代理尾胜率 +0.19（M=24）
      跨回合后效: 我方能量 2，下回合付不起 抓挠1(3)，被迫聚能或换人
[T24] 我方被迫 recharge，对手 skill 造成 22 伤害 → 免费挨打回合
[终局] 败（我方 0 命 / 对手 2 命，共 41 回合）
```

设计要点：① 写"克制倍率/偏差数字"而不是"这步不好"，结论让反思器自己推（才能推出可泛化
规则）；② 暴露反事实存在（当时的合法替代动作）；③ **暴露预测校准偏差**——比胜负干净，
它区分"运气差"与"算错了"；④ 暴露跨回合后效（能量透支的代价 1–2 回合后兑现）。

### 6.2 信度分配：三个信号，交集优先

1. **校准偏差 ★（免费，精度最高）**：`battle_act` 增加可选参数 `prediction`（预期结果文本），
   由 LLMPlayer 每回合记录，离线与结算比对。零额外成本。
2. **价值落差（便宜，覆盖广）**：`V_heuristic` 扫全场，标 `V(s_t) − V(s_{t+1})` 下降最大的 top-k。
3. **反事实回放（贵，产生真标签）**——本项目的关键适配：**代理尾策略**。

> **代理尾策略（本项目相对论文的关键工程决策）**：反事实回放不能在候选回合之后继续用
> "记录中的其余决策"——换掉一个动作后状态发散，记录决策可能全部非法。而用真实 LLM 跑尾局
> 每回合 2 次调用，M=24 次回放成本不可接受。
> 方案：在候选回合 fork `state.clone()`，用**固定的代理尾策略**（默认 `RandomPlayer`，
> 固定 seed 集）驱动双方打完剩余回合，M=24 次不同 seed。`delta_winrate =
> E[outcome(替代动作)] − E[outcome(原动作)]`（原动作也走同一代理尾，是自洽基线）。
> 这是**局面级**动作评估：不完美（代理 ≠ LLM）但确定、可复现、便宜；作为一致的
> 信度信号足够。R7 才考虑用 LLM 自身做尾策略（SMC，成本换胜率）。

关键回合卡片（反思器与记忆库的统一结构）：

```json
{
  "trajectory_id": "...", "turn_no": 23, "side": "a",
  "situation_key": {"my_lives":2,"foe_lives":3,"my_active":"迪莫","foe_active":"火苗",
                    "my_energy_band":"low","foe_energy_band":"mid",
                    "foe_revealed_skills":2,"phase":"mid","my_bench":1,"foe_bench":2},
  "chosen": {"type":"skill","value":0},
  "counterfactual_better": {"type":"switch","value":1},
  "delta_winrate": 0.19, "n_replays": 24,
  "signals": ["calibration_miss", "value_drop", "counterfactual_confirmed"],
  "feedback_text": "…(§6.1 渲染)…",
  "module_attribution": "M2 action_selector"
}
```

### 6.3 Situation Key 与两阶段检索（MemRL，部署期零新增 LLM 调用）

`situation_key` 从**迷雾 view()** 确定性提取（己方全见 + 敌方白名单），JSON 序列化 hash：

```
(己方命数, 对手命数, 己方在场精灵, 对手在场精灵, 己方能量档, 对手能量档,
 对手已揭示技能数, 阶段, 己方存活后备数, 对手存活后备数)
```

两阶段检索（MemRL 原文校准）：Phase A 相似度门 `sim ≥ δ` 取 top-`k1=10`，加**硬过滤**
（命数档/能量档/`data_digest` 必须匹配，跨版本记忆不可信）；Phase B `score =
(1−λ)·z(sim) + λ·z(Q)` 取 top-`k2=3`。默认 `δ=0.5`（原文按任务对相似度分布取上 20% 分位数，
自博弈里可在 D_sel 上标定）、`λ=0.5`（原文凹曲线最优点）。**z-score 标准化是关键**（原文：
去掉它 Forgetting Rate 从 0.041 飙到 0.073）。兜底 `KeywordEmbedder`
（situation_key 硬过滤 + 结构化标签 + TF-IDF），零外部依赖、离线可测；可插拔真实嵌入。
**只有被注入（top-k2）的条目参与 Q 更新**，未被检索到的不动（MemRL 检索级门控）。

**Q 更新（修正 MemRL 广播弱点）**：

```
被检索且被采纳（决策 == 记忆.action） → r_entry = 0.3·r_terminal + 0.7·delta_winrate(t)
被检索但未采纳                        → 不更新（无归因依据，防噪声污染）
采纳但无反事实数据                    → 用 r_terminal，且半衰 α' = α/2
Q ← Q + α(r_entry − Q)，α=0.3，新条目 Q_init=0，只增不删
```

`delta_winrate` 来自 §6.2 反事实回放（这正是替代 MemRL 均匀广播的修正——v2 §9.4，
本方案唯一对参考论文的实质性算法修正）。

**记忆 → Playbook 固化阈值**：跨 **≥3 阵容族**、`n_used ≥ 10`、`Q ≥ 0.3` → 提名为
`EditCandidate` 进 §五的门禁。反向不成立（Playbook 规则不下沉为记忆）。

---

## 七、双 LLM 的注入与演进方式（本方案直接回答"两个 LLM"怎么进化）

两个 LLM 玩家 = 同一个基座模型 + **各自带策略上下文**的 `LLMPlayer`。演进方式分两档：

### 7.1 单池模式（默认，推荐起步）

- **一个 Playbook 池**（§四的 Pareto 池）是唯一的进化对象；两侧是"槽位"，各从池中取一份
  Playbook（对局用 `champion` vs `sampled`，评估用 `champion` vs `历史池`）。
- **共享情境记忆库，按 `side` 更新 Q**：a 的决策只更新 a 侧条目、b 只更新 b 侧条目；
  检索时侧锁（`tactic_lookup` 不进 `ALL_TOOLS`，文本按归属过滤）。
- 自我对局中一方的表现反哺池子，池子进化同时提升两侧——这是最省成本且最贴论文的形态。
- **注入点**（改 `battle/player.py`）：
  - `on_match_start`：系统提示 = `BATTLE_PLAYER_SYSTEM_PROMPT` + `[战术手册 v037]` 块；
  - `decide`：观测后追加 `[记忆]` 块（检索 top-3 记忆渲染，标注"历史经验，非当前局面"）；
  - `battle_act` 加可选 `prediction` 参数 → `LLMPlayer` 记录到 `self._turn_log`（喂校准信号）；
  - `on_turn_result`：事件摘要不变；记忆采纳判定由离线管线用 `_turn_log` + 决策序列做。

### 7.2 独立双代理模式（可选扩展，成本约翻倍）

- a / b 各自维护**独立的 Playbook 候选谱系**与**独立记忆命名空间**，真正双代理共同进化。
- 防循环不靠运气：两个谱系都进同一个 Pareto 池按同一套门禁晋级，历史回归门照样卡。
- 何时启用：单池模式跑通、需要"两个身份差异演化"（如 a 偏防守 b 偏进攻）时。

### 7.3 迷雾隔离（不可破坏）

记忆/Playbook 注入的文本**全部经过 `filter_events_for`/`view()` 白名单口径**：
记忆条目在写入时只含白名单字段；检索注入走侧锁；整场守卫测试（`_fog_roster` 式）断言
a 注入的文本不含 b 未暴露技能名。E6.5 已把 `on_turn_result` 事件改成过滤后事件，
本方案在其之上只加文本，不加信息面。

---

## 八、数据划分（先立规矩，否则后面所有数字都不可信）

| 划分 | 别名 | 用途 | 禁止 |
|---|---|---|---|
| `D_tr` | 训练轨迹湖 | 自博弈对局池 → 反思证据、记忆来源 | — |
| `D_sel` | 门禁/实例集 | **固定 60 个实例**（阵容族×场景×8 个固定种子的配对对局）→ 分数向量/Pareto/门禁 | 实例内容不进反思上下文（只给分数） |
| `D_test` | 隐藏集 | 人工反套路场景 + 未公开阵容 → 只用于汇报 | 不进任何优化输入；CI 不可读；每季度换血 30% |

**按阵容族/场景族隔离，不按对局隔离**（否则同一阵容对局跨集泄漏）。实例构造：
从 `valid_spirit_candidates()` 里按家族生成阵容族对（≥5 族 × 场景标签：能量压制/高速强攻/
耐久消耗/状态控制/镜像），每个配 8 个固定 seed 的**双向先后手配对对局**。实例规模是
主要成本旋钮（GEPA 教训：大部分预算花在验证而非学习信号），初期可缩到 20 个再动态子采样。

> **非平稳性对策（GEPA 未覆盖、必须自己补）**：GEPA 假设任务分布固定，但自博弈里**对手也在进化**。
> 若 D_sel 实例跟着对手池滚动更新，选择信号会漂移，Pareto 支配关系失真。
> 对策：**D_sel 使用冻结的基准对手库**（人工 + 历史池 + 极端风格，`data_digest` 钉死），
> 滚动进化的新对手**只进 D_feedback**（学习信号），不进 D_sel（选择信号）。
> 每季度（或引擎/模型升级时）人工 re-baseline 重打分一次。同样约束加在 D_test。

---

## 九、防坍缩（成败关键）

| 失败模式 | 表现 | 防御 |
|---|---|---|
| 策略循环 | 每代打赢上一代，v10 打不过 v3 | **Pareto 池自动保留**（§四）；手工 PFSP 降级为前沿退化兜底 |
| 过拟合自身 | 只会打镜像，遇陌生打法崩盘 | **专职剥削者**（唯一可利用性度量）+ 风格对手池（纯攻击/纯状态/纯能量压制） |
| 评测污染 | 分数涨、真实强度不涨 | 三集隔离 + 优化器只见 `D_sel` 分数不见实例 + `D_test` 换血 |
| 记忆噪声 | 记忆 Q 被洗成随机排序 | §6.3 归因修正 + Forgetting Rate 监控 + `data_digest` 隔离 |

**专职剥削者**：单独候选分支，唯一目标是打爆当前 Champion。不进主线、不参与晋级，
只做两件事：① 胜率 = Champion 的**可利用性**（进复合分 0.15）；② 对局注入对手池。
每 2 个 epoch 从 Champion fork 重置，避免它自己过拟合。

**历史回归门**：Champion 晋级前对历史池（被剔除成员 + 历次 Champion）全部跑一遍，
**对任一历史成员胜率 ≥ 45%**——这是"真实变强而非循环"的硬检验。

**健康度看板**（异常即暂停自博弈）：动作熵（低会读穿）/ 换人率 / 能量利用率 /
对局长度分布 / Pareto 前沿宽度 + 收益矩阵反对称分量 / 编辑接受率 / 记忆 Q 分布 + Forgetting Rate。

---

## 十、成本模型（自博弈的主要成本是 LLM token）

| 手段 | 说明 | 阶段 |
|---|---|---|
| 对手用便宜档位 | 只有被优化方用前沿模型，对手方用快模型或启发式 Bot | R0 起 |
| 提示缓存 | Playbook + 记忆静态前缀每回合重复，必须缓存 | R1 起 |
| 两级门禁的廉价门 | 绝大多数候选死在 8 场 minibatch 上，不付全量 `D_sel` | R3 起 |
| 反事实回放前缀复用 | `CounterfactualCache` 按 `(trajectory_id, turn_no)` 共享 | R2 起 |
| SPRT 早停 | 强候选早停晋级、弱候选早停拒绝 | R4 起 |
| 优化器与目标可不同档 | SkillOpt：target-matched optimizer 仍回收 56–74% 收益；预算紧是可接受降级 | R3 起 |

按 step 记账：单 step = `B=8` 场 rollout + 关键回合挖掘（含回放）+ 反思调用 + 门禁评测。
**少量高质量编辑**——学到的技能往往只需 1–4 次被接受的编辑就能产生显著提升，
不要期待也不要允许大规模重写。

---

## 十一、里程碑总览（R 线，R0–R5 是核心，R6–R8 是扩展）

| 里程碑 | 一句话目标 | 核心验收命令 | 状态 |
|---|---|---|---|
| **R0 可观测度量** | 先能测量强弱，再谈提升 | `evolve eval --bench d_sel` | ☐ |
| **R1 记忆库与检索** | MemRL 底座 + 注入（先记录不改变决策） | `evolve reflect --games N` | ☐ |
| **R2 信度分配** | 定位该改哪一回合 | `evolve credit --traj <path>` | ☐ |
| **R3 反思与编辑** | 双分析师 + 有界编辑跑通 | `evolve step --seed 7` | ☐ |
| **R4 池与晋级** | GEPA 池 + 两级门 + 剥削者 + 回归门 | `evolve steps --n 4` | ☐ |
| **R5 慢更新与收敛** | [PROTECTED] + Meta + D_test 汇报 | `evolve epoch --n 8` | ☐ |
| **R6 构筑元游戏**（扩展） | PSRO 外层：Build Oracle + σ* | `evolve league --rounds 2` | ☐ |
| **R7 推理期增强**（扩展） | AMC/SMC 关键回合后验采样 | `evolve eval --smc` | ☐ |
| **R8 持续运行**（扩展） | 后台长跑 + 汇报纪律 + 回滚 | `evolve run --until-epoch 50` | ☐ |

---

## Milestone R0 — 可观测度量（先能测量，再谈提升）

**目标**：建立「强弱可测量」的完整基架：确定性局面特征（situation_key / V_heuristic /
伤害阈值表）、轨迹重放分析、配对评测协议、启发式/极端风格对手、Elo/α-rank 基架、D_sel/D_test。
这一步做完，任何"提升了 X%"才有可信口径。

**文件清单**：

| 路径 | 用途 |
|---|---|
| `src/environment/evaluate.py`（新） | `situation_key(view)` / `v_heuristic(state, side)` / `ko_thresholds(rules)`（一击必杀/两回合击杀线，纯引擎穷举）——**纯函数、零依赖、可单测** |
| `src/rock_pvp_agent/battle/evolution/__init__.py` + `analysis.py`（新） | `analyze_record(record)`：重放一条轨迹，逐回合产出 `TurnAnalysis`（situation_key ×2、V ×2、decisions、events、prediction） |
| `src/rock_pvp_agent/battle/evolution/feedback.py`（新） | `render_feedback(ta) -> str`：§6.1 的 μ_f 确定性渲染（本轮先渲染事件机制部分） |
| `src/rock_pvp_agent/battle/evolution/bench.py`（新） | `build_instances(kind)`（d_sel/d_test 实例）+ `paired_eval(build_player, instances)`（双向先后手配对 + 95% CI） |
| `src/rock_pvp_agent/battle/evolution/league.py`（新，部分） | Elo + α-rank 基架（收益矩阵占位） |
| `src/rock_pvp_agent/__main__.py`（改） | `evolve` 子命令（`eval` / `reflect` / `credit` / `step` / `steps` / `epoch` / `league` / `health` 分派） |
| `tests/test_evaluate.py` / `tests/test_analysis.py` / `tests/test_bench.py`（新） | 特征手算断言 / 重放分析确定性 / 配对评测统计 |

**关键签名**：

```python
# environment/evaluate.py —— 纯函数，供 analysis/valuefn/smc 复用
def situation_key(view: dict) -> str: ...      # 迷雾 view() → 定长哈希键（§6.3）
def v_heuristic(state, side: str) -> float: ... # 0.6·Δ命 + 0.3·Δ血量比 + 0.1·Δ能量（确定性）
def ko_thresholds(rules) -> dict: ...           # 每技能对每精灵的击杀线（穷举 compute_damage）

# evolution/bench.py
@dataclass(frozen=True)
class Instance:
    name: str            # "energy_denial_vs_stall_s1"
    roster_a: list; roster_b: list
    seeds: tuple[int, ...]   # 8 个固定种子（双向先后手已内建）
def paired_eval(builder, instances) -> dict: ...  # {"winrate":…, "ci95":…, "n_games":…}
```

**验收命令（负责人亲自运行）**：

```bash
cd ~/workspace/MySelfPlayAgent
uv run python -m rock_pvp_agent evolve eval --bench d_sel --a llm --b random --games 8 --seed 7
#   实例级配对胜率表：每行 instance / winrate(95%CI) / n_games
#   LLM(无playbook) vs random：配对胜率 95%CI 下界 > 0.5  ✅（证明 LLM 优于随机、评测口径可信）
uv run python -m rock_pvp_agent evolve eval --bench d_sel --a llm --b llm --games 8 --seed 7
#   双 LLM 镜像对局胜率 ~0.5（对称性检查）；确定性：同 seed 两次结果逐位相同
uv run python -m rock_pvp_agent evolve reflect --traj runs/selfplay-7-1.json --dump feedback.txt
#   人工抽检 50 份 feedback_text：每一份都能定位到具体机制（克制倍率/能量后效/校准偏差）✅
uv run pytest tests/test_evaluate.py tests/test_analysis.py tests/test_bench.py -q   # 全绿
```

**用户 Gate（R0）**：确认"LLM 显著优于随机且测量可信"、50 份 feedback 可定位机制、
D_sel/D_test 三集已建立。明确说"通过" → commit `feat: r0 observable metrics`.

---

## Milestone R1 — 记忆库与两阶段检索（MemRL 底座）

**目标**：打通「轨迹 → 反思产物 → 记忆条目 → 检索」。此时检索结果先落日志、不改变决策
（R3 才接线改变决策）。

**文件清单**：

| 路径 | 用途 |
|---|---|
| `src/rock_pvp_agent/battle/evolution/memory.py`（新） | `MemoryEntry` / `MemoryStore`(JSON，复用 `TrajectoryStore` 原子写模式) / `KeywordEmbedder` / `two_phase_search` / `update_q` |
| `src/rock_pvp_agent/battle/evolution/reflect.py`（新，最小版） | `extract_experiences(record)`：从轨迹重放产出初始 `MemoryEntry`（确定性摘要，不调 LLM） |
| `src/rock_pvp_agent/battle/evolution/health.py`（新） | 记忆健康度：Q 分布 / Forgetting Rate / 检索命中率 |
| `src/rock_pvp_agent/config.py`（改） | `memory_enabled=False`（默认关，关时全链路 no-op）+ `memory_*` 配置（§十四） |
| `tests/test_memory.py`（新） | 检索边界（δ=0/λ=1/λ=0 三种）/ roundtrip / Q 更新 / 原子写 |

**关键签名**：

```python
def two_phase_search(store, query: MemoryQuery, *, delta=0.5, k1=20, lam=0.5, k2=3) -> list[MemoryEntry]:
    # A: sim ≥ δ 且硬过滤（命数/能量档/data_digest）→ top-k1
    # B: (1-lam)*z(sim) + lam*z(sigmoid(Q)) → top-k2
def update_q(store, entry_id, reward, *, alpha=0.3): ...   # Q ← Q + α(r − Q)
```

**验收命令**：

```bash
uv run python -m rock_pvp_agent evolve reflect --traj runs/selfplay-7-1.json --out artifacts/memory/a
#   产出记忆条目（确定性摘要版），条目带 side / situation_key / data_digest
uv run python -m rock_pvp_agent evolve health --memory artifacts/memory/a
#   Q 分布（全 0 起步）/ Forgetting Rate=0 / 命中率 n/a
uv run pytest tests/test_memory.py -q    # 检索三边界 + roundtrip + 原子写
```

**用户 Gate（R1）**：确认记忆能沉淀、能检索、能更新 Q、`memory_enabled=False` 时全链路 no-op
（既有 585 测试全绿，不回归）。明确说"通过" → commit `feat: r1 situation memory`.

---

## Milestone R2 — 信度分配（定位该改哪一回合）

**目标**：把"这局输了"归到具体回合。三个信号（校准偏差 / 价值落差 / 反事实回放）取交集。

**文件清单**：

| 路径 | 用途 |
|---|---|
| `src/rock_pvp_agent/battle/player.py`（改） | `battle_act` 加可选 `prediction` 参数；`LLMPlayer` 记录 `self._turn_log`（回合 → situation_key/decision/prediction） |
| `src/rock_pvp_agent/battle/selfplay.py`（改） | `run_selfplay` 把 `_turn_log` 写入 record 的可选字段 `analysis_a/b`（向后兼容，`replay_record` 忽略未知键） |
| `src/rock_pvp_agent/battle/evolution/credit.py`（新） | `mine_critical_turns(ta_list) -> list[Card]`（价值落差 top-k + 校准偏差★ + 反事实回放）+ `CounterfactualCache` |
| `src/rock_pvp_agent/battle/evolution/feedback.py`（补全） | 渲染校准/反事实/跨回合后效（§6.1 全模板） |
| `tests/test_credit.py`（新） | 两信号一致性 / 反事实确定性 / 代理尾可复现 / 缓存命中 |

**关键签名**：

```python
def counterfactual(session_snapshot, turn_no, side, chosen, alternatives,
                   *, proxy="random", M=24, seeds=...) -> dict:
    """fork 候选回合前状态 → 用代理尾策略驱动剩余回合 M 次 → 每替代动作返回 delta_winrate。"""
def mine_critical_turns(ta_list) -> list[dict]:  # 卡片（§6.2 形状），module_attribution 初判
```

**验收命令**：

```bash
uv run python -m rock_pvp_agent evolve credit --traj runs/selfplay-7-1.json --out artifacts/cards.jsonl
#   输出关键回合卡片（JSONL）：每张含 signals 列表 + delta_winrate + counterfactual_better
uv run python -m rock_pvp_agent evolve credit --traj runs/selfplay-7-1.json --repeat 2
#   同 seed 反事实回放逐位复现（代理尾确定性）✅
uv run pytest tests/test_credit.py -q
#   一致性检验：回放确认的错误决策中 ≥60% 同时被校准偏差标记
```

**用户 Gate（R2）**：确认关键回合定位在真实 LLM 对局上"看起来对"（卡片能归因到机制）、
反事实回放确定可复现。明确说"通过" → commit `feat: r2 credit assignment`.

---

## Milestone R3 — 反思与有界编辑（双分析师 + SkillOpt 纪律）

**目标**：把关键回合卡片变成**结构化编辑提案**，并实现编辑纪律。这一环是 GEPA/SkillOpt
的核心，也是"反思进化"的字面落点。

**文件清单**：

| 路径 | 用途 |
|---|---|
| `src/rock_pvp_agent/battle/evolution/playbook.py`（新） | `Playbook`（5 模块 + `[PROTECTED]` + 版本）/ `PlaybookModule` / token 上限校验 |
| `src/rock_pvp_agent/battle/evolution/reflect.py`（补全） | `ReflectionService`：Failure/Success 双分析师（独立提示、只喂一类证据）+ JSON schema 校验 + 失败降级确定性摘要 |
| `src/rock_pvp_agent/battle/evolution/editor.py`（新） | `bounded_edit(pb, candidates, L_t=4)`（append/insert_after/replace/delete）+ rejected buffer + `edit_apply_report.jsonl` + `[PROTECTED]` 保护 |
| `src/rock_pvp_agent/battle/evolution/run.py`（新，最小） | `run_step(seed, pool_state)`：rollout → credit → reflect → edit（暂不接池，产 edit_apply_report） |
| `tests/test_reflect.py` / `tests/test_editor.py`（新） | 双分析师分桶 / 非法 JSON 降级 / L_t 截断 / PROTECTED 不被覆写 / 平手拒绝 |

**关键签名**：

```python
# 双分析师提示结构（借鉴 MemRL 附录 I 三槽模板）
FAILURE_REFLECTION: ROOT CAUSE / PATTERN TO AVOID / CORRECT APPROACH   # 只喂失败卡 + 被拒缓冲
SUCCESS_REFLECTION: SCRIPT（3~5 高层步骤，供固化而非新增）               # 只喂成功卡
def bounded_edit(pb: Playbook, candidates: list[EditCandidate], *, Lt: int = 4) -> Playbook:
    # 编辑操作：append / insert_after / replace / delete（各带 support_count 与 source_type）
    # 按 (证据条数 × 平均 delta_winrate × 场景覆盖度) 排序截断；不得覆写 [PROTECTED]
    # L_t 默认 4、余弦退火、地板 2（SkillOpt 实测：预算大小不敏感，有界本身才是收益）
def promote(pb_candidate, d_sel) -> bool:  # 复合分严格 ">" 才接受，平手拒绝（R4 完整实现）
```

**验收命令**：

```bash
uv run python -m rock_pvp_agent evolve step --seed 7 --out artifacts/
#   打印：本 step 产出的编辑提案 + 编辑报告（接受/跳过/拒绝 + 原因）
#   edit_apply_report.jsonl：逐条编辑可追溯到证据（审计性）✅
uv run pytest tests/test_reflect.py tests/test_editor.py -q
#   L_t 截断 / PROTECTED 不被覆写 / 非法 JSON 降级确定性摘要
```

**用户 Gate（R3）**：确认双分析师分桶合理、编辑有界且保护区和审计完整。**此时编辑还没有
D_sel 门禁**（R4 接）——Gate 重点是"编辑纪律本身对"。（若预算紧张，R3 可与 R4 合并 Gate。）
明确说"通过" → commit `feat: r3 reflection and bounded editing`.

---

## Milestone R4 — Pareto 池与晋级门禁（GEPA 池 + SkillOpt 门 + 剥削者 + 回归门）

**目标**：把 R3 的编辑接进完整进化闭环——候选池、两级门禁、Champion 晋级、专职剥削者、
历史回归门。这一环做完，"反思 → 编辑 → 验证 → 晋级"闭环闭合。

**文件清单**：

| 路径 | 用途 |
|---|---|
| `src/rock_pvp_agent/battle/evolution/pool.py`（新） | `PlaybookPool`：实例级 Pareto 选择（GEPA Alg.2）/ 入池门（某实例池内最优）/ 复合分晋级门 / `P_max=12` 剪枝 → archive |
| `src/rock_pvp_agent/battle/evolution/league.py`（补全） | 对手采样（Pareto 领先实例数权重；前沿退化 → PFSP `P(o) ∝ (1−wr)^2` 兜底）/ 专职剥削者 / 历史回归门 ≥45% / 收益矩阵 + Elo + α-rank |
| `src/rock_pvp_agent/battle/evolution/run.py`（补全） | `run_steps`：逐 step rollout（champion vs sampled）→ credit → reflect → edit → 廉价门 → 全量门 → 池更新 → 健康度 |
| `src/rock_pvp_agent/battle/player.py`（改） | `strategy` 注入：系统提示加 `[战术手册]`、decide 加 `[记忆]`（R1 已备，这里真正接线） |
| `src/rock_pvp_agent/__main__.py`（改） | `evolve steps --n --seed --out --a-version champion --b-version sampled` |
| `tests/test_pool.py` / `tests/test_league.py` / `tests/test_evolve_cli.py`（新） | 支配剪枝 / 平手拒绝 / 回归门 45% 边界 / 剥削者 fork / 采样分布 / CLI 冒烟 |

**关键签名**：

```python
class PlaybookPool:
    def per_instance_scores(self, pb) -> dict[str, float]: ...
    def winners(self) -> list[Playbook]: ...       # GEPA Alg.2（每实例最高分的候选集合）
    def pareto_front(self) -> list[Playbook]: ...  # 剔除被支配者；P_max=12
    def add_candidate(self, pb, scores) -> bool: ...  # 入池门：某实例池内最优
    def champion(self) -> Playbook: ...             # 复合分严格最高（平手拒绝晋级）
    def best(self) -> Playbook: ...                 # 双游标：历史最优（仅超越才封存 best_skill）
def sample_opponent(pool, current) -> str: ...      # 领先实例数加权；退化 → PFSP 兜底
def promotion_gate(candidate, history) -> bool: ... # 对任一历史成员胜率 ≥45% + SPRT
```

**验收命令**：

```bash
uv run python -m rock_pvp_agent evolve steps --n 4 --seed 7 --out artifacts/
#   step#1..4 逐行：champion / 采样对手 / 本 step 入池数 / 晋级? / 复合分 / 健康度指标
#   至少出现一次「入池」与一次「拒绝」；若出现「晋级」需打印新 champion 的 D_sel 复合分
uv run python -m rock_pvp_agent evolve steps --n 4 --seed 7 --out artifacts/ --health-dashboard
#   动作熵 / 换人率 / 能量利用率 / 前沿宽度 / 反对称分量 / 编辑接受率
uv run pytest tests/test_pool.py tests/test_league.py tests/test_evolve_cli.py -q   # 全绿
uv run pytest -q --cov=rock_pvp_agent --cov=environment    # 两包都 ≥90%（既有 585 不回归）
```

**用户 Gate（R4）**：确认闭环真的转起来——至少一次「入池 + 拒绝」（晋级非必须，取决于
真实模型表现），健康度指标可读，历史回归门 ≥45% 生效。明确说"通过" → commit `feat: r4 pareto pool and gates`.

---

## Milestone R5 — 慢更新与收敛（[PROTECTED] + Meta Playbook + D_test 汇报）

**目标**：长程一致性。每 `E=8` step 做一次慢更新：提炼被反复验证的规则进 `[PROTECTED]`、
更新只给优化器的 Meta Playbook、重训 V 档位、在 `D_test` 汇报（不回流任何优化决策）。

**文件清单**：

| 路径 | 用途 |
|---|---|
| `src/rock_pvp_agent/battle/evolution/editor.py`（补全） | epoch 慢更新（上一/当前 Champion 同批 20 实例跑四类：改进/回退/持续失败/稳定成功）+ 慢更新候选**同样过门禁**（SkillOpt：不是免检通道） |
| `src/rock_pvp_agent/battle/evolution/meta.py`（新） | Meta Playbook（只给优化器：哪类编辑常被拒 / 哪类诊断反复奏效 / 哪些失败跨 epoch 持续） |
| `src/rock_pvp_agent/battle/evolution/valuefn.py`（新） | `V_heuristic` 上线 + 可选档位 A 训练（特征 → 轨迹回归，中局 AUC ≥0.75 才启用） |
| `src/rock_pvp_agent/battle/evolution/run.py`（补全） | epoch 调度 + `D_test` 汇报评测 |
| `tests/test_epoch.py`（新） | 慢更新过门禁 / 关慢更新 A/B 可复现退化（SkillOpt：同时移除 meta+慢更新是最大消融，−22.5 分） |

**验收命令**：

```bash
uv run python -m rock_pvp_agent evolve epoch --n 8 --seed 7 --out artifacts/
#   连续 3 个 epoch 在 D_test 上单调不降（winrate 曲线 + 95%CI）；D_test 数字只进汇报
uv run python -m rock_pvp_agent evolve epoch --n 8 --no-slow-update --out artifacts/ablate/
#   A/B：关掉慢更新后 D_test 明显退化（复现 SkillOpt 消融）→ 证明慢更新必要
uv run pytest tests/test_epoch.py -q
```

**用户 Gate（R5）**：确认"连续 3 epoch 单调不降"与"关慢更新可复现退化"两个结论都成立。
明确说"通过" → commit `feat: r5 slow update and convergence`，此时**核心闭环（R0–R5）完成**。

---

## Milestone R6 — 构筑元游戏（PSRO 外层，扩展）

**目标**：让阵容本身也进化（Build Oracle），而不只是对战策略。对应 v1/v2 §6 的三级分解：

| 级别 | 空间 | 谁来搜 |
|---|---|---|
| L1 战术原型 | ~20 个原型（能量压制/高速强攻/耐久消耗/状态控制…） | **LLM 提议**（Playbook 的 M5 模块，本身也过 GEPA） |
| L2 精灵与技能 | 给定原型下 3–6 只 × 各 1–4 技能 | **LLM 提议 + `validate_team` 约束校验** |
| L3 数值分配 | IV（0–10，≤3 维）、性格、血脉系别 | **纯代码搜索**（局部搜索 + `ko_thresholds` 剪枝）——LLM 手算必错 |

**文件清单**：

| 路径 | 用途 |
|---|---|
| `src/rock_pvp_agent/battle/evolution/build.py`（新） | `BuildOracle`（L1/L2 LLM 提议 + schema/内容包校验 + L3 数值搜索）+ 收益矩阵 + α-rank 元求解 `σ*` |
| `src/environment/teambuilder.py`（改，仅补校验口） | 复用 `validate_team`/`build_roster`；不出新依赖 |
| `tests/test_build.py`（新） | L3 搜索到一击必杀阈值 / L2 违规被拒 / 一轮 PSRO 出新阵容入池并改变 σ* |

**验收命令**：

```bash
uv run python -m rock_pvp_agent evolve league --rounds 2 --seed 7 --out artifacts/
#   收益矩阵 + α-rank σ*；Build Oracle 每轮提议 → 门禁 → 入池/拒
#   至少一轮 PSRO 产出新阵容入池并改变 σ*（σ* 支撑集变化可见）✅
uv run pytest tests/test_build.py -q
```

---

## Milestone R7 — 关键回合后验采样（AMC/SMC，扩展，成本换胜率）

**目标**：在关键回合把冻结先验往"赢"的方向偏移：`π*(s_{0:T}) ∝ π(s_{0:T})·e^{r/β}`，
实现 = 序列重要性重采样（N=15 粒子、固定重采样点）。**必须门控**（AMC 反转证据：
强先验在简单任务饱和时 SMC 有害——GPT-5.1/TextCraft Best-of-15 0.889 vs AMC 0.790）。

- 固定重采样点：换人决策点 / 能量归零点 / 任一方剩 1 命 / 对手首次暴露新技能后的下一回合。
- **`β=1` 固定，不网格标定**（AMC 原文：训练设 β=1、事后调采样温度；权重更新里的 `r/β` 直接用 `r`）。
- **价值函数优先用引擎重放回归目标**（本项目独有的便宜 V）：确定性引擎可 fork 重放，
  `V_θ(s_t) = f_θ(s_t) + r(s_t)` 中当前态 `r(s_t)` 可 fork 立即结算（精确获得），
  `f_θ` 只学"剩余回合收益"——用基于引擎的轨迹回归替代纯 LM 回归，数据更便宜、偏差更小。
- 门控三条（全满足才启用）：① 命中重采样点；② `V_heuristic` 不确定性高（Top-2 候选价值差 < τ，
  τ 从 D_sel 标定）；③ 本场 SMC 预算未耗尽（每场上限 4 次）。
- 前提：环境可 fork（`state.clone()` + `step` 已具备）；尾策略 = LLM 自身（成本高，所以只在关键点）。
- **顺序纪律**：先 Playbook（R3–R5）后 SMC——Playbook 变好会**缩小** SMC 收益空间，
  反过来先上 SMC 会污染 Playbook 的评测信号。

**验收命令**：

```bash
uv run python -m rock_pvp_agent evolve eval --smc --bench d_sel --a llm --b random
#   SMC 分支的单位成本胜率 > 同成本 Best-of-N 的胜率（不等式反转即关闭 SMC）
uv run python -m rock_pvp_agent evolve eval --smc --scenario saturate
#   门控在饱和局面正确关闭（对手剩 1 命且我一击必杀时 SMC 调用数为 0）
```

---

## Milestone R8 — 持续运行与运维（扩展）

**目标**：后台长跑 + 汇报纪律 + 一键回滚。

- `evolve run --until-epoch 50`：后台持续自博弈，逐 step/epoch 落盘、健康度异常即暂停。
- 汇报纪律：任何"提升了 X%"必须附样本量/种子范围/95%CI/对手构成/`data_digest`/Playbook 版本/
  V 版本。`D_test` 只在 epoch 边界跑，结果不得回流任何优化决策。
- 注册表：每次晋级归档 5 模块文本 + `edit_apply_report` + 分数向量，保留一键回滚。
- 模型升级（更换基座）触发全量重评。

---

## 十二、文件结构与集成复用清单（完成态）

```
MySelfPlayAgent/
├─ src/environment/
│  ├─ evaluate.py            # R0：situation_key / v_heuristic / ko_thresholds（纯函数，零依赖）
│  └─ …（其余 E 线文件**一行不改**）
├─ src/rock_pvp_agent/
│  ├─ battle/
│  │  ├─ player.py           # 改：strategy 注入（playbook + 记忆）+ prediction 记录
│  │  ├─ selfplay.py         # 改：analysis_a/b 可选字段进 record（向后兼容）
│  │  ├─ store.py            # 不改（未知键忽略，replay 不读）
│  │  ├─ prompts.py          # 改：playbook/memory 块渲染
│  │  └─ evolution/          # R 线新包（agent 层，可 import environment）
│  │     ├─ analysis.py  feedback.py  credit.py  valuefn.py          # R0/R2
│  │     ├─ memory.py  reflect.py  editor.py  playbook.py  meta.py   # R1/R3/R5
│  │     ├─ pool.py  league.py  build.py  smc.py  health.py  run.py  # R4/R6/R7/R8
│  │     └─ bench.py                                                 # R0
│  └─ __main__.py            # 改：evolve 子命令
├─ artifacts/                # 不入库：playbooks/ memory/{a,b}/ d_sel/ d_test/ payoff/ meta-playbook/
├─ runs/                     # 既有 E6 轨迹目录（R 线复用）
└─ tests/ test_evaluate.py test_analysis.py test_bench.py test_memory.py
           test_credit.py test_reflect.py test_editor.py test_pool.py
           test_league.py test_evolve_cli.py test_epoch.py test_build.py
```

**复用清单**（对齐 E 线既有缝）：

| 现有件 | 位置 | R 线角色 |
|---|---|---|
| `Player` Protocol + `players=` 注入缝 | `environment/players.py` / `selfplay.run_selfplay` | R4 编排直接构造带 strategy 的玩家注入 |
| `to_dict/from_dict/clone/step` | `models.py` | 反事实回放 fork、SMC、重放分析 |
| `replay_record` | `environment/replay.py` | R0 轨迹分析基座 |
| 原子写 + append index 模式 | `battle/store.py` | `MemoryStore` 同款实现 |
| 迷雾收口 `drive_turn` | `match.py` | 记忆/Playbook 注入自动得正确口径 |
| `view()` / `filter_events_for` | `view.py` / `visibility.py` | situation_key 提取 + 记忆可见性 |
| `build_chat_llm`（缓存 + llm= 注入缝） | `rock_pvp_agent/llm.py` | 反思/编辑 LLM（独立档位） |
| 事件 schema | `environment/events.py` | μ_f 确定性渲染输入 |
| `valid_spirits/valid_skills` | `dataset.py` + `valid_skills.json` | 构筑搜索真实约束空间 |
| 配对评测/确定性测试范式 | `tests/test_selfplay.py` / `test_environment_match.py` | 评测协议与统计口径 |

---

## 十三、风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 反事实回放的代理尾 ≠ 真实 LLM | 信度信号有偏 | 代理尾固定且可复现（同 seed 同结果），作为**一致**信号而非绝对真相；R7 用 LLM 尾交叉验证 |
| 记忆检索噪声注入决策 | 上下文污染 | Phase A 硬过滤 + `[记忆]` 标注"历史经验非当前局面" + 门禁平手拒绝 |
| 双 LLM 互相过拟合（只会打镜像） | 假进步 | Pareto 池保留多样性 + 专职剥削者 + 历史回归门 ≥45% + 风格对手池 |
| 对手进化导致选择信号漂移（GEPA 非平稳性） | Pareto 支配关系失真、评测污染 | `D_sel`/`D_test` 用**冻结基准对手库**（`data_digest` 钉死），新对手只进 `D_feedback`；季度 re-baseline |
| 价值函数有偏/无法区分近似态 | SMC 权重失真、关键回合定位偏 | 档位 A-0 确定性可复现；档位 A-1 用反事实回放交叉验证定位质量；SMC 门控 + 对比 Best-of-N |
| `D_sel` 评测成本失控 | 预算被验证吃光 | 两级门禁廉价门 + 实例动态子采样 + SPRT 早停 |
| Playbook 膨胀 | 延迟/成本上升、注意力稀释 | 硬 token 上限（单模块 800/合计 2500）+ 有界编辑 + 定期压缩（压缩也过门禁） |
| 记忆 Q 被噪声洗成随机 | 检索失效 | §6.3 归因修正（只更新被采纳条目）+ `data_digest` 隔离 + Forgetting Rate 监控 |
| LLM 反思失败（坏 JSON/超时） | 反思队列卡死 | JSON schema 校验 + 降级确定性摘要 + 反思与对局解耦（失败不阻塞下一场） |
| SMC 在饱和局面有害 | 花钱变弱 | §R7 三条件门控 + 持续对比同成本 Best-of-N |
| 模型行为版本变化 | 历史结论失效 | 固定模型 ID/参数进策略版本；升级触发全量重评 |
| 门禁集过拟合 | `D_sel` 涨 `D_test` 不涨 | 三集隔离 + 优化器只见分数不见实例 + `D_test` 季度换血 30% |

---

## 十四、配置项（新增，默认全部关闭，关闭时 R0–R8 全链路 no-op）

| 配置 | 默认 | 语义 |
|---|---|---|
| `memory_enabled` | `False` | 总开关（关时记忆/反思/编辑全 no-op） |
| `memory_dir` | `<data>/memory` | `MemoryStore` 目录 |
| `memory_embedder` | `"keyword"` | `keyword`(兜底) / 真实嵌入(可选) |
| `memory_delta/k1/lam/k2` | `0.5/10/0.5/3` | 两阶段检索参数（MemRL 原文校准） |
| `memory_alpha` | `0.3` | Q EMA 系数 |
| `memory_w_used` | `0.3` | 采纳权重 |
| `memory_counterfactual_M` | `24` | 反事实回放场次 |
| `memory_promote_families/min_used/min_q` | `3/10/0.3` | 记忆→Playbook 固化阈值 |
| `playbook_pool_max` | `12` | GEPA P_max |
| `playbook_edit_lt` | `4` | SkillOpt 有界编辑 L_t |
| `league_pfsp_p` | `2.0` | PFSP 对手采样指数 |
| `league_promote_min_winrate` | `0.45` | 历史回归门 |
| `reflection_model` | 与对战同档 | 反思/编辑 LLM 档位（建议更便宜） |
| `battle_rollout_b` | `8` | 单 step rollout 场数（配对对局） |
| `v_tier` | `"a0"` | 价值函数档位（a0 启发式 / a1 训练模型） |

---

## 十五、启动前需确认的问题

1. **优化器模型档位**：反思/编辑 LLM 是否允许与对战模型不同档（更便宜或更强）？
   SkillOpt 实测强优化器收益更大且不增部署成本——需确认账号与合规。
2. **预算上限**：整个 R 线的总 token 预算？决定 `B`（rollout 场数）、`M`（回放场次）、
   `D_sel` 实例数。建议先给一个"能跑通一个完整 epoch"的最小预算。
3. **`battle_act` 加 `prediction` 参数**：改变工具 schema（向后兼容，可选参数）——
   是否接受？这是免费校准信号的来源。
4. **D_test 物理隔离**：是否需要独立目录/加密，确保 CI 与优化循环都读不到？
5. **对局规模基准**：R0 评测默认用 3v3/2 命还是 6v6/4 命？规模影响每局时长与 token 成本。
6. **是否要求 Playbook 作为人类可读攻略产出**（影响可解释性优先级与文档形式）。
7. **双代理模式（§7.2）是否需要**：默认单池模式起步，双代理留作扩展。
8. **`memory_embedder` 是否接受零外部依赖兜底**：默认 `keyword`（确定性、离线可测），
   后续可换真实嵌入（Protocol 不变）。

---

## 十六、一句话总结

**冻结的模型是先验；变强的是围绕它的四件外挂**——一份被 GEPA Pareto 前沿引导、SkillOpt 有界
编辑、严格门禁筛选过的多模块战术手册；一个按实际因果贡献更新效用的情境记忆库；一个零梯度
起步的价值估计器；以及一个随对局不断进化的对手池/构筑池。其中前三件永久生效且零推理开销，
第四件（SMC）是显式的成本换胜率交易。奖励永远只来自确定性引擎的胜负事实。

**R0–R5 是核心闭环**（度量 → 记忆 → 信度分配 → 反思编辑 → 池与门禁 → 慢更新收敛）；
R6–R8 是可选扩展（构筑元游戏 / SMC / 持续运行）。在 E 线的确定性、迷雾、马尔可夫三条不变式
之上，这套方案让"两个 LLM 打完的每一局都不再归零"。
