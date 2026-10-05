# 记忆功能是否能支持持续提升 PVP 水平

审查日期：2026-09-22。代码基线：`b39a6fe0470d973bf1097a3df0d3390c739fac0c`；评估对象包括当前源码、相关测试与本地已有样本。没有调用真实 LLM，也没有开展新的实战胜率实验。

## 1. 判断

**部分符合基础设施需求，尚不符合完整的能力提升需求。** 项目已经做到“记录对局、存储经验、在部分对战入口检索并注入经验”。但“这条经验来自可信对局 → 决策时确实使用 → 使用后带来收益 → 在未见过的对手上仍有效 → 安全晋升为新版本”的链路存在缺陷和缺口。

因此，目前不能以记忆数量增加、Q 值上升、流程测试通过或自博弈单场胜负，证明 Agent 的 PVP 水平持续提升。也不能据此断言记忆完全无效：现有代码与样本支持它有发挥作用的可能，缺的是可靠归因与受控实验证据。

| 用户目标 | 当前判断 | 原因 |
|---|---|---|
| 从自博弈积累经验 | 部分支持 | `evolve battles` 可提取局部经验并更新 GlobalMem；`steps/epoch` 的主学习目标是 Playbook |
| 在后续决策中复用 | 已有能力 | LLMPlayer 能读取局部记忆及开局 GlobalMem；覆盖取决于入口与开关 |
| 从人机对战继续学习 | 关键闭环未实现 | UI 支持读取，却没有统一战后入库、实际使用日志、反馈归因任务 |
| 分清好经验与坏经验 | 不充分，且有错误归因 | 战后重新检索；槽位相等被视为采纳；胜负奖励缺少局部贡献区分 |
| 提高长期策略水平 | 有 Playbook/联赛骨架 | 记忆缺少经过验证的条件策略、反例与生命周期；历史 LLM 对手也未真实冻结 |
| 证明泛化提高 | 尚不支持该结论 | Playbook 门禁不带记忆；GlobalMem A/B 默认与训练实例重叠且只对 random |
| 迁移到真实游戏 | 本次不能判断 | 本项目模拟器内提升还须独立验证；本次未核对线上游戏最新规则与全部机制 |

## 2. 已实现且值得保留的部分

1. **可重放对局与玩家视角隔离。** `selfplay.py:96–145` 存 seed、阵容、动作、规则、玩家类型、state hash、首发及部分分析信息；`analysis.py:173–201` 生成逐回合视图。`reflect.py:87` 使用 `show_v=False`，避免直接把全状态价值估计作为在线事实注入。GlobalAnalyst 也分两方独立复盘。这些是学习可信性的正确基础。
2. **局部情境记忆。** `memory.py` 已有幂等 ID、存储、两阶段检索与 EMA Q；`reflect.py:59–104` 从轨迹提取经验；`player.py:254–264` 在决策前注入经验，明确标为历史而非当前事实。
3. **整局经验 GlobalMem。** 已有公开阵容特征检索、文本长度限制、版本替代关系、审计日志及实际加载 ID。`player.py:237–252` 和 `selfplay.py:128–132` 的 GlobalMem 加载 ID 是局部记忆可参考的实现起点。
4. **策略编辑和门禁骨架。** Playbook 有保护区、有界编辑、版本池、训练/选择/测试实例、历史回归与慢更新。它们可扩展成“完整 Agent 发布单元”的门禁，不必推翻重写。
5. **离线测试较充分。** 本次主回归 143 项通过；另有针对人机/顾问轨迹的 34 项测试通过。测试证明既定接口行为可运行，未证明真实 LLM 胜率增长。

以上是实现事实，不等于注释中提到的论文算法和效果已在本游戏复现。

## 3. 必须优先处理的实际问题

优先级约定：**P1** 为扩大无人值守学习前应处理的问题；**P2** 为保证长期质量与效率的下一阶段任务。以下“已复现”与“代码确认的能力缺口”分别标注，避免把未来设计要求误写成已发生故障。

### F1 / P1：学习分析器与标准重放器不一致，会拒绝合法对局【已复现】

`run_selfplay` 保存 `starters`，标准 `environment/replay.py:42–48` 会恢复首发及入场；但 `evolution/analysis.py:169–181` 从默认状态开始直接执行回合，`credit.py:173–185` 的反事实前缀重建同样缺少这一步。

最小复现使用双方选择末位精灵首发：自博弈标准重放成功，`analyze_record` 首回合就失配，后续关键回合挖掘拒绝该轨迹。这意味着合法的策略探索可能无法进入经验库。附带还存在 `item_arg` 恢复口径不一致，应与首发统一修复；不能只修一处函数。

**建议：**用一个共享的、版本化的轨迹规范化与重放实现提供逐回合回调；学习、顾问、UI 与反事实共用。验收应覆盖非默认首发、入场特性、定向道具、补位、终局一致性。

依据：[analysis.py:169](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/roco_pvp_agent/battle/evolution/analysis.py:169)、[credit.py:173](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/roco_pvp_agent/battle/evolution/credit.py:173)。复现见 `learning-findings-evidence.json`。

### F2 / P1：局部记忆 Q 更新不能证明决策时使用过该记忆【已复现】

`LLMPlayer._record_turn` 只保存局面键、动作、道具、预测，没有保存当时检索/注入的记忆 ID。`apply_adoption` 在战后重新检索，并仅比较 `m.action == entry.action`。

更严重的是 `run_battles` 先提取本局全部记忆，再执行采纳更新。因此空库第一局也会“采纳”本局新生的经验。离线复现得到 **checked=112、adopted=38、updated=38**；39 条新记忆中 13 条 Q 大于 0，而决策时记忆库为空。这不是效果不确定，而是实际使用记录的归因错误。

`run_steps` 虽未先提取本局经验，战后重新检索也可能因前面回合的 Q 更新改变候选顺序，不能替代原始暴露日志。LLM 异常后随机兜底若碰巧动作相同，也没有标记排除。

**建议：**决策时落 `retrieved_ids / injected_ids / entry_revision / snapshot_id / decision_source`；战后仅消费这些事件，禁止重新检索充当历史事实。相同行动只叫“动作一致”，不能直接叫“采纳导致获胜”。奖励更新按 `(episode, side, memory_revision)` 幂等，避免同一局重复记账。

依据：[player.py:300](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/roco_pvp_agent/battle/player.py:300)、[memory_inject.py:25](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/roco_pvp_agent/battle/evolution/memory_inject.py:25)、[globalmem_run.py:253](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/roco_pvp_agent/battle/evolution/globalmem_run.py:253)。复现见 `core-findings-evidence.json`。

### F3 / P1：GlobalMem 原文不变的 update 会把自身从活跃库移除【已复现】

GlobalMem ID 由对局键、策略文本和 data digest 生成。若分析师选择 `update` 但没有改变文本，新旧 ID 相同。`supersede` 先 `add`（返回 exists），随后写 `superseded_by=自身 ID`，`active()` 便不再返回它。复现中唯一一条活跃经验变为 0 条。

另一个独立设计缺口是：真正改变文本的新经验立即可检索，并以 Q=0 开始；本局回报则更新旧的已加载 ID。把回报留给实际加载版本是正确的，但新版本缺少试用/评测门禁，不能据“成功落库”就立即替代经过验证的策略。

**建议：**相同内容 update 应为 no-op 或追加来源证据；禁止自环与替代环。策略内容修改生成候选修订，验证通过再替换 active；保留旧策略及其证据，不直接把旧 Q 作为新策略有效性证明。

依据：[globalmem.py:290](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/roco_pvp_agent/battle/evolution/globalmem.py:290)、[globalmem.py:314](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/roco_pvp_agent/battle/evolution/globalmem.py:314)、[globalmem_analyst.py:185](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/roco_pvp_agent/battle/evolution/globalmem_analyst.py:185)。复现见 `core-findings-evidence.json`。

### F4 / P1：人机对战只有读记忆，没有形成战后学习闭环【代码确认的能力缺口】

`routes_battle.py:236–254` 的 `memory=True` 会注入局部记忆与 GlobalMem；但路由保存仅写对局文件。`BattleController.record():169–191` 未保存玩家已有的 `_turn_log` 和 `loaded_global_mem_id`；同时也未记录策略版本、模型版本和随机兜底标记。回合结果进入 history 只影响当前局，不能视为跨局学习。

也没有“人类针对某回合的纠正 → 绑定轨迹证据 → 待验证假设 → 训练/反事实检验 → 晋升”的路径。聊天历史保存、顾问只读经验查询，与对战策略长期更新是不同能力。

**建议：**统一 UI/selfplay 的 EpisodeRecord；终局落盘后投递幂等学习任务，读取已结束且重放通过的记录；人类纠正单独存成待验证证据，不直接改 active 策略。旧人机记录先做阵容格式转换与严格校验，不能原样送入现有自博弈分析器。

依据：[routes_battle.py:236](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/ui/routes_battle.py:236)、[battle.py:169](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/ui/battle.py:169)。复现中确认 UI 存档丢失玩家已有学习元数据，见 `human-findings-evidence.json`。

### F5 / P1：评测未覆盖最终使用的记忆组合【代码确认的验证缺口】

R 线 `run_steps` 的训练 rollout 传了 memory retriever，廉价门、全量门、历史回归及 D_test 却未传；它们验证的是 Playbook 改动，不能为“带记忆的完整 Agent”背书。且该路径没有调用 `store_experiences`，不能理解为每个 step 都自然新增局部经验。

G 线 `measure_ab` 确实会开/关 GlobalMem 比较，但仅使用 random 对手、没有局部记忆，默认复用训练实例；只返回效果报告，不控制经验发布或回滚。R 线有独立 D_tr/D_sel/D_test 骨架，G 线没有直接继承这一隔离保证。

历史 Playbook 对手通过 `PlaybookPlayer` 执行，其实现根据手册文本 hash 改变随机策略 seed，而不是让历史 LLM 执行旧手册。代码明确把它定义为离线替身，这本身可用于流程测试，但不能把这种回归当成对历史真实 Agent 的实力证明。`_OfflineGlobalMemPlayer` 同样明确不读经验文本，离线 A/B 不是能力实验。

**建议：**以冻结的 `AgentSnapshot`（模型配置＋提示＋Playbook＋局部库＋GlobalMem＋检索配置＋环境版本）为晋升单位。独立评估无记忆、局部、全局、两者组合与上一代快照，测试过程不写经验库；用真实历史模型/提示/记忆快照构成对手池。

依据：[run.py:452](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/roco_pvp_agent/battle/evolution/run.py:452)、[run.py:489](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/roco_pvp_agent/battle/evolution/run.py:489)、[globalmem_run.py:135](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/roco_pvp_agent/battle/evolution/globalmem_run.py:135)、[player.py:85](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/roco_pvp_agent/battle/player.py:85)。

### F6 / P1：局面特征和动作身份不足，会把不同战术视为相同【已复现碰撞，后果为设计风险】

局部 `situation_key` 包含命数、在场名字、能量档、已揭示技能数量、阶段、后备数量，却缺 HP/可见血线、状态、印记、天气、冷却、速度关系、具体已揭示技能与胜利条件。`KeywordEmbedder` 又不比较在场精灵名，所以完全不同精灵在其余字段一致时 similarity=1.0。

硬过滤已要求 8 个相似度字段中的 4 个相同，因此通过硬过滤的条目至少有 0.5 相似度，默认 delta=0.5 在这一阶段几乎不增加区分。Phase A 只留最前 10 条，在大量同分条目下还可能使后写入的更好经验进入不了 Q 重排。

与此同时，经验动作只有 `skill/switch + 槽位`：A 精灵的技能槽 0 与 B 精灵的技能槽 0不是同一个战术；道具及补位也未纳入采纳判定。GlobalMem 的对局键以系别/技能类别/速度等粗粒度特征为主，经验文字却可能包含具体精灵打法，仍须检查适用条件。

**建议：**用公开可见状态构造战术特征，区分未知与已知；保存规范化技能/单位/道具身份及完整决策，检索后检查当前合法性和前置条件。原始经验可仍保留槽位用于重放，策略复用使用语义动作映射。向量检索只能作召回补充，不能代替这些约束。

依据：[evaluate.py:75](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/environment/evaluate.py:75)、[memory.py:104](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/roco_pvp_agent/battle/evolution/memory.py:104)、[memory_inject.py:37](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/roco_pvp_agent/battle/evolution/memory_inject.py:37)。

### F7 / P1：未完赛与胜负校验不严，顾问战绩证据可失真【已复现】

人机存档允许保存进行中对局。顾问轨迹证据路径没有先排除 `done=False`；标准 `replay_record` 主要验证回合 state hash，不验证顶层 winner/done 一致性，零回合还会通过空集合的 all 检查。复现中零回合未完赛被算入 1 局 0 胜；修改已结束对局的 winner 字段仍能通过该 replay gate。

另复现了镜像队伍队名标识相同的胜率聚合偏差：实际 b 获胜，却为按 a 方队伍标签查询的记录报 1 局 1 胜。这影响顾问推荐证据；若未来据此生成对战经验，也会污染学习目标。该问题不能泛化为所有非镜像统计都错。

**建议：**统一 `completed / draw / aborted / invalid` 状态，重放终局必须与记录匹配；未完赛可保存过程事实，但不得进入终局回报和胜率分母。镜像以角色/玩家视角计分，或明确按双席位统计并记录口径，不通过相同队伍哈希猜胜方。

依据：[trajectory.py:145](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/roco_pvp_agent/advisor/trajectory.py:145)、[replay.py:79](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/environment/replay.py:79)。详见 `human-findings-evidence.json`。

### F8 / P1：版本隔离已有基础，但不足以防止跨规则污染【代码确认，缺失 digest 已复现】

局部与全局检索都只在条目和查询“同时有 data_digest”时才比较；缺失来源指纹会放行。旧记录提取还会把缺失指纹回填成当前指纹。当前 data digest 虽已覆盖默认规则及 VALID 数据，比仅比较名称可靠，但 `rules_digest()` 使用 `DEFAULT_RULES`，不是实际对局的规则参数；技能/特性实现源码或行为 ABI 也不在指纹内。

因此“数据文件未变”并不等于“这条经验仍适用于当前引擎与赛制”。已有队伍规模/命数特征过滤不能代替全部规则版本校验。

**建议：**存实际规则参数 hash、数据源及数据 hash、引擎行为版本、观测 schema、动作 schema；未知版本进入隔离区，不能自动盖当前版本章。迁移通过重放和语义验证后生成明确迁移记录。

依据：[datafingerprint.py:33](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/environment/datafingerprint.py:33)、[datafingerprint.py:57](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/environment/datafingerprint.py:57)、[memory.py:133](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/roco_pvp_agent/battle/evolution/memory.py:133)、[globalmem.py:425](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/roco_pvp_agent/battle/evolution/globalmem.py:425)、[reflect.py:33](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/roco_pvp_agent/battle/evolution/reflect.py:33)。

## 4. 面向持续变强的设计缺口

### F9 / P2：存了很多战报片段，还没有稳定的证据到战术链

局部提取是每回合每方一条、Q=0；provenance 只有规则、数据和 source_type，没有 battle_id/turn、独立对局集合、是否胜负反例等。ID 按粗局面与槽位动作合并，遇到同 ID 直接跳过，会丢失“多次独立支持”和“相同表象但结论相反”的证据。

GlobalMem 有 source_battle_ids，比局部库好，但每次生成新条目只写当前 battle_id，文本校验只检查 decision 枚举及非空，再加 token 限制。尚没有强制的证据锚点、适用条件、失败边界、多局支持、反例验证；提示中写“不得编造隐藏技能”不等于程序已经验证该约束。

`credit.py:98–143` 使用完整状态 fork＋随机策略尾部估计候选动作价值，可用于筛查；这不是当前 LLM 策略下的动作增益，也不是迷雾信息集下的最优响应。不能把它直接升级为“已证明正确的战术”。新方案应分别保存终局反馈、启发式落差、代理反事实结果和人工意见及其可信度。

### F10 / P2：指标、配置与容量尚未支持长期运营

- `n_used/n_adopted` 初始化后未在局部采纳路径更新；`health.py:29–30` 的 forgetting_rate 固定 0、retrieval_hits 为 None。因此不能从该报表判断记忆有效性或遗忘。
- `_make_memory_retriever` 没传 Settings 内的 delta/k1/k2/lam/embedder，局部 Q 更新也固定默认 alpha。应只暴露实际生效的配置并存入实验清单。
- JSONL 每次查询读全库，每次更新读改写全库；这是明确串行的本地实现，原子替换并不提供多训练进程的事务隔离。长期大库/并行自博弈时需要索引、批量写入与事务。
- `LLMPlayer` 的回合历史持续追加，每次检索文本也留在后续上下文。缺少去重、总预算和已失效经验的移除策略；局部经验条数上限不等于整局提示成本上限。
- 首发当前仍随机；补位虽可由 LLM 决策，但未建立与主动作相同的记忆证据/归因通路。若目标是全面 PVP 水平，长期应覆盖阵容、首发、换人/补位、能量规划、残局与对手风格，而不局限于下一技能选择。

附属运行缺陷：可选 `ValueFn.value` 调用 `state.view(side)`，但分析器传入的 BattleState 没有该方法，已离线复现 AttributeError。默认启发式路径不受这一错误影响；启用拟合价值函数前应修复并验证，不宜提前把它纳入奖励设计。

依据：[reflect.py:77](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/roco_pvp_agent/battle/evolution/reflect.py:77)、[globalmem_analyst.py:151](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/roco_pvp_agent/battle/evolution/globalmem_analyst.py:151)、[health.py:12](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/roco_pvp_agent/battle/evolution/health.py:12)、[run.py:111](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/roco_pvp_agent/battle/evolution/run.py:111)、[valuefn.py:113](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/roco_pvp_agent/battle/evolution/valuefn.py:113)。

### F11 / P2：长程评测状态尚有完整性问题【已复现编排边界】

续跑时只比较实例名称，未校验其阵容、seed、规则、模型和记忆快照；同名实例改变 seed 后仍接受旧池并沿用旧分数。应使用完整 evaluation manifest 指纹，不一致则拒绝沿用分数或显式重评。

另在默认 60 实例池上，慢更新仅评估前 20 实例；当产生慢更新候选且通过非降级门、历史回归门、准备应用时，却将这 20 项分数交给要求完整分数向量的池接口。通过 mock 昂贵对战、保留实际慢更新编排的边界复现，得到“分数向量缺实例（40 个）”。这可能在满足上述条件时中断长期进化，并非每次运行必然报错，也不是实际胜率实验；修复为“20 实例预筛，完整实例评测后才入池”。

依据：[run.py:428](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/roco_pvp_agent/battle/evolution/run.py:428)、[run.py:673](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/roco_pvp_agent/battle/evolution/run.py:673)、[pool.py:371](/Users/liuzhanyu/workspace/MySelfPlayAgent/src/roco_pvp_agent/battle/evolution/pool.py:371)。完整复现见 `learning-findings-evidence.json`。

## 5. 本地数据能支持什么结论

只读快照见 `repository-snapshot.json`：

| 数据 | 观察 | 可得结论 |
|---|---|---|
| 局部记忆 | 144 条，Q 0～0.9176，使用/采纳累计都为 0 | Q 已变化，但使用指标没有同步；不能据此估计实际采用率 |
| GlobalMem | 10 条活跃经验，Q 0～0.3，n_used 合计 4 | 存在经验与加载更新痕迹，样本不足以说明稳定收益 |
| `artifacts/gm_run` | 5 局记录标记双方为 llm | 存在标注为真实 LLM 路径的历史样本；不是本次重新验证的学习效果实验 |
| `artifacts/llm_probe` | 2 局 llm 对风格玩家 | 表明做过小规模探测，不能代表未见对手泛化 |
| `battles` | 6 条，只有 1 条 done=true | 人机数据有较多未完赛记录，必须先清洗终局口径 |
| `runs` | 2 局 fake_llm | 适合重放与编排验证，不能作真实 LLM 提升证据 |

本次没有读取密钥，没有修改上述库或对局。无法从这些文件推断用户在其他机器/目录做过什么实验，也不能据此给出整个项目的真实胜率。

## 6. 建议路线

先做“可信轨迹＋真实使用日志＋终局人机回流＋冻结评测”，再改检索与战术抽象。只有在独立对手/阵容/种子上的受控比较支持提升，才把候选经验发布为 active。具体接口、分期与验收条件见 [改进实施方案](./improvement-plan.zh-CN.md)。

设计参考使用本地 [MSCE memory/skills 技能](/Users/liuzhanyu/.agents/skills/msce-memory-skills/SKILL.md) 中“证据、条件策略、环境知识分层”和“验证后晋升”的思路；没有把论文报告的阈值或效果当成本项目结论。核心判断均来自当前代码与本次离线复现。
