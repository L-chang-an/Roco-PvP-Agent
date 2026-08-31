# 自进化博弈对战智能体：自我反思·记忆进化·持续提升的实现方案

> 目标：在 **Battle 模式**（人机对战 + 双 Agent 对战）下，Agent 能基于已有记忆**反思进化**、
> 对已有记忆做**抽象反思**、通过**自我反思**提升决策质量——在不断的模拟对战中**持续进化博弈水平**，
> 而不是每场打完零提升。
>
> 借鉴 `docs/self-rl-plan/` 下的 Self-Play-RL-Plan v1/v2、GEPA、SkillOpt、MemRL、AMC 五份参考。
> 本文给出与现有代码（`src/environment/` 对战内核 + `src/agent/` 智能体）**可直接落地**的 M1~M6 分阶段实施方案。

---

## 〇、一句话主线

> **冻结的模型是先验，变强的是围绕它的外挂。**
>
> 模型权重不可更新（闭源 LLM / 避免灾难性遗忘），因此所有"变强"都沉淀在**模型之外的版本化工件**里：
> ① 一份被 Pareto 前沿引导、有界编辑、严格门禁筛选过的**战术手册（Playbook）**；
> ② 一个按实际因果贡献更新效用的**情境记忆库（Situation Memory）**；
> ③ 一个随对战不断进化的**对手池/策略池**。
>
> 三者的共同底线：**奖励只来自确定性战斗引擎的胜负事实，模型永不当裁判。**

---

## 一、背景与目标

### 1.1 现状（为什么现在是"每场打完零提升"）

现有能力（已全部落地并测试通过）：

- **对局内核**：`src/environment/arena/`（`MatchRunner` 同步状态机 + `MatchSession` asyncio 层 +
  `LLMPlayer` 玩家抽象 + 三层信息隔离）。
- **双 Agent 自博弈**：`src/agent/ai_battle.py` 的 `AgentBattleSession` 单后台 task 逐回合推进，
  两个独立 `LLMPlayer` 互搏，轨迹经 `TrajectoryStore` 落盘。
- **人机对战**：`src/agent/battle.py` 的 `BattleAgent`（人类恒 side a，Agent 恒 side b）。
- **轨迹数据**：每局完整记录 `MatchTurnRecord`（动作/事件/重试/兜底）+ 双方 partial 观测 + 终局胜负。

**空缺**：每局结束，轨迹只是"存下来了"，没有任何环节把对局**转化成下次决策可用的经验**。
`arena_record_memory` / `arena_knowledge_turns` / `MEMORY_*` 等配置已声明但零消费者。

### 1.2 目标

构建**连续进化闭环**，让"对局 → 反思 → 记忆 → 决策注入 → 反馈 → 进化"形成回路：

```
对局 → 轨迹沉淀 → 赛后反思/提炼 → 记忆库 → 决策时检索注入 → 效用反馈 → 记忆→Playbook 固化 → 对手池/晋级门
```

- **Battle 模式**（人机）与 **AI 自对弈**都沉淀记忆、触发反思；
- **AI 自对弈是批量进化主通道**（可并行、可重放、可离线评测）；**人机对局是增量经验源**
  （含对人类选手的**对手建模**）；
- 终极目标：多次重复对局后持续变强——固定基准对手池上的**胜率曲线上升**、记忆**效用 Q 收敛**、
  **检索命中率上升**。

### 1.3 已确认的范围决策

| 决策 | 选择 | 含义 |
|---|---|---|
| 方案深度 | **分阶段 M1~M6** | 先做核心「反思→记忆→检索注入→效用反馈」闭环，再扩展 Playbook 固化 + 自博弈晋级门 |
| 记忆相似度 | **可插拔 Embedder + 确定性兜底** | 默认 `situation_key` 硬过滤 + 关键词/TF-IDF，离线可测；可后续接真实 embedding API |
| 沉淀范围 | **人机 + AI 自博弈都写** | 两者都触发赛后反思；AI 自博弈是主进化通道，人机是增量经验源 |

---

## 二、设计原则与红线（不可协商）

1. **模型永不担任裁判。** 奖励只来自确定性引擎的胜负事实（`engine.execute_full_turn`，
   `src/environment/battle/engine.py:424`），绝不来自 LLM 自评或反思文本打分。
   所有 `Q` 更新只以引擎事实为锚。
2. **部署期零额外模型调用。** 检索注入只是"把文本拼进决策上下文"（MemRL / SkillOpt 的
   skill-as-trainable-state 精神），推理链路不加任何新 LLM 调用。反思与进化是**异步离线批处理**，
   不阻塞对局。
3. **信息隔离不可破坏。** 不同策略版本各读**己方记忆命名空间**；记忆工具**侧锁**（不注册进
   `ALL_TOOLS`）；记忆文本过 `filter_events_for` 可见性过滤（`observation.py:59`）。
4. **三集隔离 D_tr / D_sel / D_test。** 反思只用 `D_tr`；门禁只测 `D_sel`；汇报只用 `D_test`。
   按阵容族/场景族隔离，不按对局隔离（防跨集泄漏）。
5. **降级铁律。** 检索/反思任一失败**不影响对局**（`_safe` 风格 + `random_legal_action` 兜底，
   `observation.py:127`）；记忆是附加能力，存不进/读不出都只记日志。
6. **外部化工件版本化、可回滚。** Playbook、记忆库、选手池均为不可变版本化工件。

---

## 三、总体架构与数据流

```
┌────────────────────────────── 数据流 ──────────────────────────────┐
│                                                                    │
│  [对局层] MatchRunner ⇄ LLMPlayer（arena 内核，现有复用）            │
│     AI 自对弈：ai_battle.py  run_ai_battle()                       │
│     人机对战：battle.py     BattleAgent                            │
│        │  轨迹（situation / action / outcome）                     │
│        ▼                                                          │
│  [存储层] TrajectoryStore（trajectory.py，现有复用，不改）            │
│        │  终局后触发（同一同步块内）                                 │
│        ▼                                                          │
│  [反思层] ReflectionService（M2）                                   │
│     · 关键回合定位（§6.1 三信号）                                   │
│     · 双分析师：FailureAnalyst / SuccessAnalyst                    │
│     · 产物：ExperienceItem（z, e, Q=0）或 Playbook 编辑候选          │
│        │                                                        │
│        ▼                                                        │
│  [记忆层] MemoryStore（M1）← Embedder（可插拔 + 确定性兜底）          │
│     MemoryEntry(z, e, Q)；两阶段检索：A 相似门限 → B 效用重排        │
│        │  决策时检索（M3）                                        │
│        ▼                                                        │
│  [决策层] LLMPlayer._think / tactic_lookup 注入（侧锁只读工具）      │
│        │  回合结局反馈（M4）                                       │
│        ▼                                                        │
│  [效用层] credit_assign：r_entry = w_used·r_terminal + (1-w_used)·Δwinrate │
│  [固化层] Playbook（M5：GEPA Pareto 池 + SkillOpt 有界编辑 + 门禁）   │
│  [进化层] 对手池/晋级门（M6：PFSP 采样 + 历史池回归门 ≥45%）          │
└────────────────────────────────────────────────────────────────────┘
```

**关键设计决策**：记忆沉淀入口放在"轨迹保存完成之后、同一次同步块内"
（`ai_battle.py:198` 的 `_run()` finally 块已调用 `_save_trajectory()`，`ai_battle.py:297`）。
反思是**异步批处理**（不阻塞对局）：对局结束时产生"反思触发事件"入队，后台 worker 逐条消费。

### 为什么"每场打完有提升"成立

每场对局在结束瞬间产生三类**持久工件**，任何一场都不再是"零提升"：

| 工件 | 产生时机 | 对后续对局的作用 |
|---|---|---|
| 轨迹（`TrajectoryStore`） | 终局同步块 | 复盘素材、反思输入、检索索引 |
| 经验（`MemoryEntry`） | 赛后反思（M2） | 同类局面被检索注入，影响决策 |
| 效用（`Q`） | 赛后反馈（M4） | 让"正确经验"在检索中被优先召回 |
| Playbook / 对手池（M5/M6） | 定期进化 | 沉淀为可版本化的策略与晋级标准 |

---

## 四、核心数据结构

### 4.1 `ExperienceItem`（反思产物，一次性）

```python
@dataclass(frozen=True)
class ExperienceItem:
    exp_id: str                     # uuid
    trajectory_id: str              # 来源轨迹
    side: str                       # "a" | "b"（作者阵营，信息隔离用）
    lineage_family: str             # 阵容族标签（D_tr/D_sel/D_test 隔离键）
    situation_key: str              # 结构化局面键（确定性，§6.2）
    situation_text: str             # 局面自然语言摘要（z，检索用文本）
    action_text: str                # 该局面采取的动作描述（e）
    outcome_text: str               # 事件因果叙述（e 的一部分，含确定性反馈 μ_f）
    turn_no: int
    confidence: float               # 反思时模型自报，仅作检索重排辅助，不作奖励
    created_at: str
```

### 4.2 `MemoryEntry`（记忆库持久化，MemRL 三元组 (z, e, Q)）

```python
@dataclass
class MemoryEntry:
    entry_id: str
    side: str                       # 记忆归属方（隔离）
    lineage_family: str             # 阵容族
    situation_key: str              # 结构化硬过滤字段（确定性兜底检索依赖它）
    situation_text: str             # z：局面
    experience_text: str            # e：经验（= action + outcome 拼接）
    Q: float                        # 效用，初始化 0
    n_used: int                     # 被检索采纳次数（记忆→Playbook 阈值之一）
    n_adopted: int                  # 实际被采用且产生正反馈次数
    w_used: float                   # 采纳权重（M4 更新）
    last_seen_at: str
    lineage: str                    # 血缘链：由哪条记忆复制/合并而来
```

### 4.3 `MemoryStore` Protocol + 默认实现

```python
class MemoryStore(Protocol):
    def add(self, entries: list[MemoryEntry]) -> None: ...
    def search(self, query: MemoryQuery, *, top_k: int = 3) -> list[MemoryEntry]: ...
    def update_q(self, entry_id: str, reward: float, alpha: float) -> None: ...
    def mark_used(self, entry_id: str) -> None: ...
    def list_by_side(self, side: str) -> list[MemoryEntry]: ...
```

实现 `JsonMemoryStore` **直接复用** `JsonTrajectoryStore` 的原子写模式
（`trajectory.py:45`：tmp + `os.replace` + append-only `index.jsonl`）；`__init__` 不 mkdir
（避免 import 副作用，`trajectory.py:14-16` 的既有约束）。工厂 `create_memory_store(settings)`
按 `memory_backend` fail-fast 分支（镜像 `create_trajectory_store`，`trajectory.py:117`）。

### 4.4 `Embedder` Protocol（可插拔 + 确定性兜底）

```python
class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def similarity(self, a: list[float], b: list[float]) -> float: ...

class KeywordEmbedder:   # 默认兜底：situation_key 硬过滤 + TF-IDF 余弦，零外部依赖
    ...
```

离线确定性兜底三层（全部纯代码，可单测、可复现）：

1. **Phase 0 硬过滤**：`situation_key` 精确匹配（§6.2）；
2. **结构化标签打分**：命数档/能量档/克制关系等维度相似度；
3. **TF-IDF 词面相似度**：`situation_text` 的余弦相似度。

线上可替换为真实 Embedder（`memory_embedder="openai"|"similary"`），Protocol 保证接口不变。

### 4.5 `Playbook` 与 `OpponentEntry`

```python
@dataclass
class Playbook:
    version: str                    # "pb_v037"（不可变版本）
    source_memory_ids: list[str]    # 血缘：固化了哪些记忆
    text: str                       # 自然语言战术手册（5 模块，见 §5.5）
    protected: list[str]            # [PROTECTED] 区域（epoch 慢更新）
    rejected_edits: list[RejectedEdit]  # 被门禁拒绝的编辑 + 分数下降，负面证据

@dataclass
class OpponentEntry:
    agent_key: str                  # 历史选手标识（策略版本）
    fingerprint: str                # 行为指纹（供回归检测）
    wins: int; losses: int          # 池内战绩
```

---

## 五、M1~M6 分阶段详细设计

### M1 — 记忆库 + 两阶段检索（MemRL 底座）

**目标**：打通"对局轨迹 → 反思产物 → 记忆条目 → 检索"。此时检索结果先落文件/日志，暂不注入决策（M3 接线）。

**新增模块**（全部在 `src/agent/memory/`）：

| 文件 | 内容 |
|---|---|
| `schemas.py` | `ExperienceItem` / `MemoryEntry` / `MemoryQuery` |
| `store.py` | `MemoryStore` Protocol + `JsonMemoryStore` + `create_memory_store` 工厂 |
| `embedder.py` | `Embedder` Protocol + `KeywordEmbedder` + `two_phase_search` |

**关键接口**：

```python
# embedder.py —— MemRL 两阶段检索
def two_phase_search(
    store: MemoryStore, query: MemoryQuery, *,
    delta: float = 0.5, k1: int = 20, lam: float = 0.5, k2: int = 3,
) -> list[MemoryEntry]:
    # Phase A: 相似度门限 sim(z_i, z_query) > delta → top-k1
    # Phase B: score = (1-lam)*sim + lam*sigmoid(Q) 重排 → top-k2
    ...
```

**改动现有代码**：无（纯新增）。可在 `config.py` 消费 `MEMORY_*`（`config.py:222-228`）并新增 `memory_*` 配置（§7）。

**测试** `tests/test_memory.py`：
- `KeywordEmbedder` 同义局面召回 > 无关局面；
- `two_phase_search` 在 `delta=0`（全过）/ `lam=1`（纯效用）/ `lam=0`（纯相似度）三种边界下行为正确；
- `JsonMemoryStore` roundtrip / 原子写 / Q 更新持久化（复用 `tests/test_trajectory.py` 的 fake 模式）。

---

### M2 — 赛后反思与提炼（双分析师）

**目标**：把轨迹蒸馏成 `ExperienceItem`（失败向 + 成功向分开），并产出后续 Playbook 编辑的**候选诊断**（不直接改 Playbook，M5 才固化）。

**新增模块** `src/agent/memory/reflect.py`：

```python
class ReflectionService:
    def __init__(self, llm_factory, store: MemoryStore, settings): ...
    def reflect(self, trajectory: dict) -> list[ExperienceItem]:
        # 1) 关键回合定位（§6.1 三信号）
        # 2) 双分析师：失败卡 → FailureAnalyst；成功卡 → SuccessAnalyst
        # 3) 结构化输出 JSON schema 校验；失败 → 降级为确定性摘要（从
        #    events / retries / fallback 提取，见 §6.4）
```

**双分析师提示结构**（`src/agent/memory/prompts.py`，借鉴 MemRL 附录 I 的三槽模板）：

```
[FAILURE_REFLECTION] 只给失败关键回合卡片 + 被拒绝编辑缓冲区：
  - ROOT CAUSE:         一句话机制假设（不是怪运气）
  - PATTERN TO AVOID:   可检索的反面模式（situation_key 提取基础）
  - CORRECT APPROACH:   该局面下应选动作
[SUCCESS_REFLECTION] 只给成功关键回合卡片：
  - SCRIPT:             3~5 个高层步骤（供"固化"而非"新增"）
```

> 分开理由（v2 §5.2 / SkillOpt §3.3）：混在一起时**失败信号会主导、成功经验被稀释**；
> 成功侧规则需要"保护"（防止被后续无界重写误删）而非"新增"。minibatch 级反思优于单条轨迹
> ——"单条轨迹常产生零散的 anecdotal 修补，而 minibatch 暴露可复用的程序性错误"。

**改动现有代码**（最小侵入）：

- `src/agent/ai_battle.py`：`_save_trajectory()`（line 297）调用后追加反思触发；
  `_run()`（line 176）的 finally 块（line 198）所在同步块内入队；`run_ai_battle()`（line 555）透传
  `reflect: ReflectionService | None = None`——测试注入 `reflect=None` 保持现状。
- `src/agent/battle.py`：`BattleAgent`（line 40）终局后走同一 `ReflectionService`——**人机对局也沉淀**
  （Agent 侧经验 + 对人类对手的对手建模，均来自引擎事实，不读人类字节）。
- 反思 LLM 走 `build_chat_llm`（`src/agent/llm.py:64`）+ 独立 `reflection_model` 档位（默认可更便宜）。

**测试** `tests/test_reflect.py`：
- Fake 反思 LLM 返回合法/非法 JSON；非法 JSON 降级为确定性摘要；
- 成功/失败分桶断言；关键回合定位命中 `retries`/`fallback` 标记的回合。

---

### M3 — 决策时检索注入（tactic_lookup + 上下文注入）

**目标**：检索到的记忆在**部署期零新增 LLM 调用**的前提下进入决策上下文。

**新增模块** `src/environment/arena/memory_tools.py`（side-locked 只读工具）：

```python
def build_memory_tools(store: MemoryStore, side: str) -> list[Callable]:
    @tool
    def tactic_lookup(situation: str) -> str:
        # 仅返回该 side 的记忆；文本已按记忆所属方过滤（信息隔离）
        return render(two_phase_search(store, MemoryQuery(situation)), top_k=3)
    return [tactic_lookup]
```

**改动现有代码**：

- `src/environment/arena/tools.py`：`build_player_tools()`（line 27）增加可选参数 `memory_tools`，
  把 `tactic_lookup` 挂进侧工具列表——**不注册进 `ToolRegistry`/`ALL_TOOLS`**，保持 side-locked 纪律。
- `src/environment/arena/players.py`：
  - 工具在构造期经 `_make_llm(settings, self._tools, ...)` 的 `bind_tools` 绑定
    （`players.py:111/131`），因此 `tactic_lookup` 必须在 `_make_player` 构造时并入工具列表；
  - `_think()`（line 218）决策循环入口：注入一次 `[memory]` 标记块（同 `_view_text` 的
    `[battle-log]` 风格，`players.py:305`）；
  - **记忆只出现于引用区，绝不用记忆覆盖 `battle_observe` 实时视图**
    （遵守 `prompts.py:37` 既有约定：历史经验是过去，当前局面只看观测）。
- `src/environment/arena/models.py`：`config_from_settings()`（line 129）透传 `memory_store_ref` 到
  `MatchConfig`（新增字段，默认 `None`）；受 `arena_knowledge_turns`（`config.py:264`）约束注入预算。

**测试** `tests/test_memory_injection.py`：
- 用 `ActPlanLLM`（`tests/test_ai_battle.py:75`）断言系统提示里出现 `[memory]` 块；
- **隔离守卫**：整场对局用 `_fog_roster()`（slot1 重击从未释放 → 对手渲染 `???`），断言双方注入的
  记忆文本**不含对方未暴露技能名**；对齐 `tests/test_arena_isolation.py` 的整场守卫模式。

---

### M4 — 效用反馈与信度分配（修正 MemRL 广播弱点）

**目标**：让 `Q` 收敛到"真实提升胜率的经验"上，而非"所有被检索经验的平均奖励"。

> MemRL 原版弱点（论文自认）：**"终局奖励被均匀广播给本次检索到的所有条目"**。自博弈下该噪声会
> 被巨量对局放大——v2 §9.4 正是要修掉这一点：按**实际采纳与实际因果贡献**归因。

**新增模块** `src/agent/memory/feedback.py`：

```python
def credit_assign(trajectory: dict, store: MemoryStore, *,
                  w_used: float = 0.3, alpha: float = 0.3, M: int = 24) -> None:
    # 1) 关键回合定位（§6.1 三信号）
    # 2) 每回合：
    #    被检索且被采纳 → r_entry = w_used·r_terminal + (1-w_used)·Δwinrate(t)
    #    被检索但未采纳   → 不更新 Q（防噪音污染）
    #    采纳但无反事实   → alpha_eff = alpha/2 半衰保守更新
    # 3) Q ← Q + alpha_eff * (r_entry - Q)   （EMA，alpha=0.3，窗口 ≈3.3 次样本）
```

**关键设计**：
- 终局 `r_terminal = +1 / -1`（唯一事实锚，红线 1）；
- `Δwinrate(t)` 由**引擎侧反事实回放**给出：固定其余输入、替换动作重跑 `M=24` 次（不同种子），
  缓存到 `CounterfactualCache`（key = `(trajectory_id, turn_no)`，同局面复用前缀控制成本）；
- `w_used = 0.3`：采纳度权重——**被检索且被采纳**的经验才真正承担回报。

**改动现有代码**：无核心改动；`ReflectionService.reflect` 同一批处理内调用 `credit_assign`；
`run_ai_battle` 增加 `feedback=None` 关闭点（测试用）。

**测试** `tests/test_feedback.py`：
- 合成轨迹：一条经验"采纳→赢"、一条"采纳→输"、一条"检索未采纳"；断言 Q 分别**上升/下降/不变**；
- 无反事实路径走 `alpha/2`，收敛速度断言。

---

### M5 — 记忆 → Playbook 固化（GEPA 池 + SkillOpt 有界编辑 + 门禁）

**目标**：把反复验证的记忆提炼成**可版本化、可门禁的 Playbook**，避免记忆库无限膨胀、噪声累积。

**新增模块** `src/agent/playbook/`：

```python
# pool.py —— GEPA 式 Pareto 候选池（实例级 Pareto，§6.5）
class PlaybookPool:
    def winners(self) -> list[Playbook]: ...      # per-instance 最优选择（GEPA Alg.2）
    def add_candidate(self, pb: Playbook) -> None: ...  # 支配剪枝，P_max=12

# edit.py —— SkillOpt 有界编辑（文本学习率）
def bounded_edit(pb: Playbook, candidate: EditCandidate, *, Lt: int = 4) -> Playbook:
    # 编辑操作: append / insert_after / replace / delete（各带 support_count 与来源）
    # [PROTECTED] 区域只允许 epoch 级慢更新写入
    # 被门禁拒绝的编辑存入 rejected_edits（负面证据，进下次反思）

# gate.py —— 留出集门禁
def promote(pb_candidate: Playbook, d_sel) -> bool:
    # score = 0.70·配对胜率(冠军+历史池) + 0.15·(1-可利用性)
    #       + 0.10·预测校准(1-Brier) + 0.05·健康度
    # 严格 ">" 才接受；平手一律拒绝（防无声漂移）
```

**记忆 → Playbook 合并阈值**（默认值，见 §7）：跨 **≥3 阵容族**、`n_used ≥ 10`、`Q ≥ 0.3`
→ 组装 `EditCandidate` 进门禁评测。

**改动现有代码**：
- `run_ai_battle()`（`ai_battle.py:555`）赛后追加"合并检查"：从 `MemoryStore` 聚合达标记忆走 `gate.promote`；
- 冠军 Playbook 注入 `LLMPlayer.on_match_start`（`players.py:179`），与 M3 的 `[memory]` 并列但标识 `[playbook]`。

**测试** `tests/test_playbook.py`：
- 阈值单测（合成记忆到/不到 3 阵容族 × 10 使用 × Q0.3）；
- Gate 平手拒绝断言（构造严格等分对手）；
- 有界编辑 `Lt` 截断 + `[PROTECTED]` 不被快速编辑覆写。

---

### M6 — 对手池与晋级门（扩展 run_ai_battle）

**目标**：让"变强"是**对历史池的可度量提升**，而非"打赢上一代"——防非传递循环、防过拟合镜像。

**新增模块** `src/agent/playbook/pool_league.py`：

```python
def sample_opponent(current: str, history: list[OpponentEntry], *, p: float = 2.0) -> str:
    # PFSP 优先级虚构自博弈：P(o) ∝ (1 - winrate(current, o))^p，越难打越常被采样
    # 构成: 40% PFSP 历史池 / 20% 当前冠军镜像 / 20% 专职剥削者 / 10% 固定启发式 / 10% 极端风格

def promotion_gate(candidate, history) -> bool:
    # 必要条件: 对每个历史冠军胜率 ≥ 45%（回归无严重退化）+ SPRT 早停省对局成本
```

**改动现有代码**：
- `run_ai_battle()`（`ai_battle.py:555`）增加 `opponent_pool: OpponentPool | None` 参数；
  `AgentBattleRegistry`（`ai_battle.py:375`）按 `sample_opponent` 选择对手；终局更新 `OpponentEntry` 战绩并跑 `promotion_gate`；
- 对手建模同样受信息隔离约束（`MatchConfig.history_window`，`models.py:69`）。

**测试** `tests/test_league.py`：
- PFSP 采样分布统计断言（胜率低者被采概率高）；
- 晋级门 45% 边界断言（合成历史战绩）；
- 全链路：`_tanky_roster`（`tests/test_agent_ai_battle_cli.py` 的 hp9999/atk1）连败 vs 连胜的池内成绩变化。

---

## 六、关键算法细节

### 6.1 关键回合定位（信度分配，M2/M4 共用）

三种信号按成本从低到高，**交集优先**：

1. **事件异常信号**（零成本，现有数据）：`Decision.retries` / `fallback`（`models.py:45`）、
   `_decide_both` 的 wait_for 超时→随机兜底（`ai_battle.py:200-220`）、引擎的 `recharge_fallback` /
   `error` 事件。这些回合的决策质量天然存疑，反思优先级最高。
2. **价值落差法**（代码级启发式价值函数，替代未来 `V_θ`）：`V_heuristic(s) = 0.6·命数差 + 0.3·血量比差 + 0.1·能量差`
   （纯确定性代码，可单测），标记 `V(s_t) − V(s_{t+1})` 下降最大的 top-k。
3. **校准偏差 ★**：模型自报高置信但结算大幅偏离的事件（以为命中、实际被防御减伤 `defense`/`defense_ready`
   ——`engine.py:117`；或 `faint` 判断错误）。指向**认知错误而非运气**，反思价值最高。
4. **反事实回放**（贵，但产生真标签）：在候选关键回合固定其余输入、替换动作重跑 `M=24` 次；
   替代动作胜率显著更高 → 确认错误决策，并得到 `Δwinrate`（供 M4 的 `r_entry`）。

### 6.2 `situation_key` 结构化局面键（确定性兜底检索的基石）

从 `partial_observe` 输出（`observation.py:44` → `view.py:127` 的 `ui_view` partial 分支）提取固定特征向量，
JSON 序列化后取 hash：

```
(己方命数, 对手命数, 己方当前精灵, 对手当前精灵, 己方能量档, 对手能量档, 对手已揭示技能集)
```

- 覆盖 `_mask_pokemon`（`view.py:53`）对对手的隐藏语义：`revealed` 集合是整局累积、双方已知的**公开信息**，不泄漏；
- 该键用于检索 **Phase 0 硬过滤**，保证隔离、可复现、离线可测。

### 6.3 两阶段检索参数（MemRL 默认）

`delta = 0.5`（Phase A 相似度门限）、`k1 = 20`、`λ = 0.5`（Phase B 效用重排系数）、`k2 = 3`。
效用项用 `sigmoid(Q)` 归一化，避免负 Q 排挤。

> MemRL 消融：λ→1 会"波动 + 上下文脱离"，只按相似度会召回"很像但当时打输了"的经验；
> 只按效用会召回"很好但场景不符"的经验。λ=0.5 是最优点。

### 6.4 反馈函数 μ_f（确定性渲染，绝不 LLM 生成）

`feedback_text` 从事件流**确定性渲染**（`feedback.py`，输入引擎 events），暴露**机制而非结论**：

- 暴露反事实：`"若在 turn 23 选择 switch 而非 skill，胜率 +0.19（M=24）"`；
- 暴露校准偏差 ★：`"turn 11 你预测命中，实际被防御减伤 40%"`；
- 暴露跨回合后果：`"turn 3 的换人导致 turn 12 能量枯竭"`（由 `switch`/`recharge`/`recharge_fallback`
  事件链推导）。

该文本只进反思/门禁上下文，**不作为奖励**（红线 1）。坏 JSON / 反思 LLM 失败时，反思降级为
确定性摘要（从 `events`/`retries`/`fallback` 直接提取，保证链路不断）。

### 6.5 GEPA 实例级 Pareto 池（= 自博弈种群）

> v2 §4 最核心的增量：**"GEPA 的实例就是对手，实例级 Pareto 前沿自动等价于元博弈的非支配策略集合"**。
> 同一个数据结构同时承担"反思式文本进化的父代池"和"PSRO 的策略种群"两个角色——**防策略循环免费**。

- 对每个验证实例求所有候选的最高分 → 达标的候选记为该实例"优胜者"；
- 汇总所有优胜者剔除被支配者得 Pareto 前沿（`P_max = 12`）；
- 按各候选**领先的实例数**为权重采样父代（贪心选最优会陷入局部最优，GEPA 消融证实）；
- 被淘汰成员进历史池 archive（**永不物理删除**，仍作回归对手）。

### 6.6 SkillOpt 有界编辑与门禁纪律

- `L_t`（编辑预算）默认 4，余弦退火到 1；"**任何适度有界预算都优于无预算重写**"；
- **严格 `>` 才接受，平手拒绝**——"被拒绝的编辑成为有信息的负反馈，而不是隐藏状态"；
- 被拒编辑缓冲 `rejected_edits`（零部署成本、必做，移除掉 −1.6/−4.6/−2.4）；
- 慢更新 + `[PROTECTED]` 区 + meta 是权重最大的组件（同时移除 −22.5）；
- 每次 step 记录 `edit_apply_report.json`（可审计）；
- 全部收益来自 **1~4 次被接受的编辑**——目标不是写长手册。

---

## 七、配置项

新增到 `Settings`（`config.py:212`），全部默认关闭，**关闭时 M1~M5 全部 no-op**：

| 配置 | 默认 | 语义 |
|---|---|---|
| `memory_enabled` | `False` | 总开关（消费现有 `MEMORY_ENABLED`，`config.py:223`） |
| `memory_backend` | `"json"` | `json` / `tencent`(future) |
| `memory_dir` | `<data>/memory` | `JsonMemoryStore` 目录 |
| `memory_embedder` | `"keyword"` | `keyword`(兜底) / `openai` / `similary`(optional) |
| `memory_embed_model` | `""` | 真实嵌入模型名（optional） |
| `memory_delta` / `memory_k1` / `memory_lam` / `memory_k2` | `0.5 / 20 / 0.5 / 3` | 两阶段检索参数 |
| `memory_alpha` | `0.3` | Q EMA 系数 |
| `memory_w_used` | `0.3` | 采纳权重 |
| `memory_counterfactual_M` | `24` | 反事实回放场次 |
| `memory_promote_families` / `memory_promote_min_used` / `memory_promote_min_q` | `3 / 10 / 0.3` | 记忆→Playbook 阈值 |
| `playbook_pool_max` | `12` | GEPA P_max |
| `playbook_edit_lt` | `4` | SkillOpt 有界编辑 L_t |
| `league_pfsp_p` | `2.0` | PFSP 对手采样指数 |
| `league_promote_min_winrate` | `0.45` | 历史池晋级门（对任一历史冠军） |
| `reflection_model` | 与对战模型同档 | 反思 LLM 档位（建议更便宜） |
| `arena_memory_inject` | 跟随 `memory_enabled` | 决策期注入开关（M3） |

**消费现有零消费者字段**：`arena_record_memory`（`config.py:259`，扩展为"记录+沉淀"总语义）、
`arena_knowledge_turns`（`config.py:264`，注入预算）、`arena_allow_search/history`（`config.py:262-263`）。

---

## 八、集成与复用清单

| 现有件 | 位置 | 本次角色 |
|---|---|---|
| `TrajectoryStore` Protocol | `trajectory.py:34-42` | 反思输入源（复用，不改造） |
| `JsonTrajectoryStore` 原子写模式 | `trajectory.py:45` | `JsonMemoryStore` 同款实现 |
| `create_trajectory_store` fail-fast | `trajectory.py:117` | 新 `create_memory_store` 同款 |
| `partial_observe` / `filter_events_for` | `observation.py:44/59` | situation_key 提取 + 记忆可见性过滤 |
| `build_player_tools` side-locked | `tools.py:27` | 挂载 `tactic_lookup`（不注册 ALL_TOOLS） |
| `LLMPlayer._think` / `on_match_start` | `players.py:218/179` | 记忆与 Playbook 注入点 |
| `MatchConfig.record_memory` | `models.py:70` | 现有开关，扩展为"记录+沉淀" |
| `run_ai_battle` | `ai_battle.py:555` | M6 对手池/晋级门扩展点 |
| `_save_trajectory` 调用点 | `ai_battle.py:198/297` | M2 反思触发点 |
| `BattleAgent` | `battle.py:40` | 人机对局沉淀 + 人类对手建模 |
| 引擎事件 schema | `engine.py`（`resolve_action`/`setup_defense_marks`/`apply_item` 产出） | `μ_f` 确定性渲染输入 |
| `agent_settings` fixture | `tests/conftest.py` | 离线测试设置复用 |
| `ActPlanLLM` / `BoomLLM` | `tests/test_ai_battle.py:75/91` | 假 LLM 范式 |

---

## 九、测试与验证

### 9.1 Fake LLM 范式（沿用现有）

- `ActPlanLLM`（奇偶 invoke `battle_observe`/`battle_act`）→ 扩展为"注入记忆后仍能正常对局"的回归；
- `BoomLLM`（抛异常 → `random_legal_action` 兜底）→ 验证记忆注入链路在 LLM 失败时不崩溃；
- Fake 反思 LLM → 验证 M2 合法/非法 JSON 与降级；
- 所有测试用离线 Settings（`api_key=""`、`base_url="http://test.invalid"`，`tests/conftest.py` 的 `agent_settings`）。

### 9.2 隔离守卫（贯穿每个含记忆的测试）

- `_fog_roster()` 整场断言：side "a" 注入的记忆/`tactic_lookup` 文本**不含对方未释放技能名**（`???` 语义保持）；
- 双 `LLMPlayer` 实例各持独立 `_history`（`players.py`），断言互不污染；
- `tactic_lookup` 不在 `ALL_TOOLS`/`REGISTRY` 中（对齐 `tests/test_arena_isolation.py:175` 的禁止名单）。

### 9.3 进化度量（验证"持续变强"）

| 度量 | 定义 | 验证目标 |
|---|---|---|
| winrate 曲线 | 固定基准对手池上的配对胜率（双向先后手） | 连续 3 个 epoch 单调上升 |
| Q 收敛 | 记忆条目的 Q 分布（正 Q 占比上升、方差收敛） | 检索命中分布稳定 |
| 检索命中率 | 决策回合检索采纳比例（`n_adopted / n_used`） | 采纳后胜率相关（Δwinrate > 0） |
| 门禁拒绝率 | `rejected / 总候选` | 有界编辑约束生效 |
| 回归门 | 对历史冠军胜率 ≥ 45% | M6 不倒退 |

### 9.4 三集隔离

- `D_sel`：固定 200 局面 + 60 场固定种子配对（`tests/d_sel/`）；
- `D_test`：人工反套路场景 + 隐藏阵容，CI 不可读，仅汇报；
- 按阵容族隔离（M1 的 `lineage_family` 字段承载）。

---

## 十、成本控制与可选增强（均标注 optional / future）

| 项 | 说明 | 阶段 |
|---|---|---|
| 对手用便宜档位 | `run_ai_battle` 两侧模型可不对称（冠军用前沿，对手用快模型）——`AgentBattleSession` 已支持 `llm_factory(side)` 每侧独立实例（`ai_battle.py:121`） | M6 默认建议 |
| 提示缓存 | Playbook + 记忆静态前缀每回合重复，必须缓存 | M3/M5 |
| 反事实回放前缀复用 | `CounterfactualCache` 按 `(trajectory_id, turn_no)` 共享 | M4 |
| SPRT 早停 | 强候选早停晋级、弱候选早停拒绝，省对局成本 | M6 |
| `V_θ` 小模型 | 替代 §6.1 启发式价值函数，用于价值落差定位与不确定性门控（参考 AMC：`V_θ(s_t)=f_θ(s_t)+r(s_t)`，单轨迹 return-to-go 回归，LoRA rank 8） | optional |
| AMC 关键回合后验采样 | `π* ∝ π·e^{r/β}`，N=15 粒子、固定重采样点（换人点/能量归零点/对手剩 1 命）；**必须门控**（强先验饱和时 SMC 有害，GPT-5.1 反转证据）；前提是环境可 fork 重放（本确定性引擎天然满足，需显式快照接口） | optional |
| 真实 Embedder | `memory_embedder="openai"|"similary"` 替换 `KeywordEmbedder`，Protocol 不变 | optional |
| Tencent Memory 集成 | `memory_backend="tencent"` 分支挂到 `MEMORY_*` 配置（`config.py:222-228`）——注意该库是"个人助理长期记忆"，L1 类型固定 7 类不可扩展，**不承担竞技策略语义**（结论同 `memory_selfplay_plan.md` §7），只作底层存储+检索 | future |

---

## 十一、风险与降级

| 风险 | 影响 | 缓解 |
|---|---|---|
| LLM 反思失败（坏 JSON/超时） | 反思队列卡死 | JSON schema 校验 + 降级为确定性摘要（事件/retries/fallback 提取）；反思批处理与对局解耦，失败不阻塞下一场 |
| 检索噪声注入决策 | 上下文污染、决策退化 | Phase A 相似度门限硬过滤；记忆文本标记 `[memory]` 并声明"历史经验，非当前局面"（对齐 `prompts.py:37`）；门禁"严格 >"拒绝平手 |
| 奖励滥用（reward hacking） | 学到刷代理指标而非赢 | 红线 1：奖励只来自确定性引擎终局；`w_used` 采纳权重 + 反事实校正防广播污染 |
| 策略循环（打赢上一代=假进步） | winrate 假涨 | M6 PFSP 对手池 + 历史池回归门 ≥45% + 专职剥削者度量可利用性 |
| 记忆污染/重复膨胀 | Q 不可信、检索慢 | 记忆→Playbook 固化阈值（3 阵容族/10 使用/Q0.3）+ 血缘链 `lineage` + 冗余合并；被拒编辑缓冲回灌反思 |
| 隔离破坏（记忆泄漏对手信息） | 不公平、信息泄漏 | `tactic_lookup` side-locked（不进 ALL_TOOLS）+ 每 side 独立 store + `filter_events_for` 可见性 + `_fog_roster` 整场守卫测试 |
| 三集泄漏 | 门禁分数失真 | 按阵容族隔离 + D_test 定期换血 + 只允许 D_test 汇报 |
| 模型行为版本变化 | 历史结论失效 | 固定模型 ID/参数入策略版本；升级触发全量重评（v2 §17） |

---

## 十二、参考文档映射（docs/self-rl-plan/）

| 参考 | 本文对应 |
|---|---|
| `Self-Play-RL-Plan_v2.md` | 总体框架：Playbook（M5）、Situation Memory（M1/M4）、候选池即种群（M6）、双分析师（M2）、μ_f 反馈（§6.4）、红线 |
| `Self-Play-RL-Plan_v1.md` | 三集隔离、关键回合三种信度分配、PFSP + 专职剥削者、SPRT |
| `MemRL.pdf` | 两阶段检索（§6.3）、(z,e,Q) 三元组、Q EMA 更新、成功脚本/失败反思模板（M2）、广播弱点修正（M4） |
| `GEPA.pdf` | 反思式提示进化、实例级 Pareto 选择（M5/§6.5）、模块轮询 |
| `SkillOpt.pdf` | 有界编辑 L_t、严格门禁、被拒编辑缓冲、`[PROTECTED]` 慢更新（M5/§6.6） |
| `AMC.pdf` | 关键回合后验采样 + 门控 + 环境回放前提（§10 optional） |
| `memory_selfplay_plan.md` | Tencent Memory 定位结论（§10 future）；P1~P5 的目标架构参考 |
