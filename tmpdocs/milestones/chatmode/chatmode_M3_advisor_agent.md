# M3 — 结构化终结 + 属性/模拟分析 + 顾问 Agent（详细实施计划）

> 上级：`chatmode_plan_v2.md` §5 M3　|　依赖：M1（catalog/validate）、M2（trajectory）　|　产出给：M4（ScopeGate 接入）、M5（回答评测）
> 分支前提：`feat/e-line-v2`；分析/模拟**不依赖** `hits_to_ko`/`search_l3`（本分支没有），直接用 `types`/`prediction`/`match`。

## 1. 目标

把 M1/M2 的确定性底座接成一个**真正能回答组队问题的顾问 Agent**：

1. **结构化终结** `submit_team_advice`（pydantic schema）替代自由文本 `final_answer`，并在终结处做 `EvidenceGate` 校验（失败允许修复一次 → 仍失败安全降级）。
2. **`analyze_team`**：属性攻防覆盖 / 速度分层 / 角色缺口（纯 `environment` 计算）。
3. **`simulate_matchups`**：确定性成对换边模拟（`match.run_match` + 固定 seed）。
4. **`TeamAdvisorAgent`**：复用 `ChatAgent` 工具循环，换 system_prompt + 工具集 + 结构化终结 + 关闭思维链外显。

## 2. 前置与依赖

- M1：`catalog.*`、`validate.validate_team`（结构化错误码）、`fingerprint.data_digest`。
- M2：`trajectory.query_trajectory_evidence`。
- 已存在（本分支）：`environment.types.type_effectiveness/stab_multiplier`、`environment.prediction.predict_damage/predictions_for`、`environment.match.run_match/drive_turn`、`environment.session.BattleSession`、`environment.teambuilder.build_roster`、`rock_pvp_agent.agent.ChatAgent`、`rock_pvp_agent.llm.build_chat_llm`。

## 3. 交付物

| 文件 | 职责 |
|---|---|
| `advisor/prompt.py` | `ADVISOR_SYSTEM_PROMPT` |
| `advisor/advice.py` | `TeamAdviceSchema` + `submit_team_advice` + `EvidenceGate` |
| `advisor/analysis.py` | `analyze_team` |
| `advisor/simulate.py` | `simulate_matchups` |
| `advisor/agent.py` | `TeamAdvisorAgent` |
| 改 `rock_pvp_agent/agent.py` | 三个可注入缝：终结判定 hook、工具集注入、thinking 发射开关（均默认行为不变） |
| `tests/test_advisor_advice.py`、`tests/test_advisor_analysis.py`、`tests/test_advisor_simulate.py`、`tests/test_advisor_agent.py` | 测试 |

## 4. 详细设计

### 4.1 `advice.py` — 结构化终结 + EvidenceGate

```python
class UnitAdvice(BaseModel):
    spirit: str
    skills: list[str]              # 1–4
    bloodline: str = ""
    nature: str = "坦率"
    iv: dict[str, int] = Field(default_factory=dict)
    role: str = ""                 # 队内分工
    rationale: str = ""            # 选择理由（必须能溯源）
    evidence_ids: list[str] = Field(default_factory=list)

class EvidenceSummary(BaseModel):
    catalog: dict = Field(default_factory=dict)     # get_catalog_version 输出
    human: dict = Field(default_factory=dict)       # query_trajectory_evidence("human")
    selfplay: dict = Field(default_factory=dict)    # query_trajectory_evidence("selfplay")
    simulation: dict = Field(default_factory=dict)  # simulate_matchups 输出

class TeamAdviceSchema(BaseModel):
    rules_used: dict                 # {team_size, lives, source}
    assumptions: list[str] = Field(default_factory=list)
    data_digest: str
    team: list[UnitAdvice]           # len == rules.team_size
    synergy: str = ""
    strengths: list[str] = Field(default_factory=list)
    weak_matchups: list[str] = Field(default_factory=list)
    evidence: EvidenceSummary
    uncertainty: str = ""            # 必须写清“理论构筑/启发式/样本不足”等
    alternatives: list[list[UnitAdvice]] = Field(default_factory=list)  # 0–2 个
```

`submit_team_advice(payload: TeamAdviceSchema) -> TeamAdviceSchema` 的**终结时校验（EvidenceGate）**：

1. **LegalityGate**：把 `payload.team` 每个 `UnitAdvice` 转 `TeamPick` → `M1.validate_team(..., source=VALID)`；任何错误 → 返回结构化错误对象给模型（带 `code` + 位置），触发**修复一次**。
2. **VersionGate**：`payload.data_digest` 必须 == 当前 `data_digest()`。
3. **EvidenceGate**：`team` 里每条 `rationale` 要么带 `evidence_ids`，要么 `uncertainty` 里明确标注“理论构筑/启发式/样本不足”；`evidence.human` 与 `evidence.selfplay` 必须分别出现（不能只给一个合并数）。
4. **修复一次后仍失败** → 返回**安全降级答案**：说明“无法给出通过校验的阵容”并列出失败 code，绝不把非法阵容当推荐输出。

### 4.2 `analysis.py` — `analyze_team`

```python
def analyze_team(roster: list[dict], *, source: DataSource = VALID) -> dict:
    # roster = build_roster 输出（含 types/stats/skills/bloodline/nature/iv/trait）
    # 返回：
    # {
    #   offensive_coverage: {技能系别: [被该系 ≥2x 克制的防守系别...]},
    #   defensive_gaps:     {本队系别: [克制它的攻击系别...]},
    #   speed_tiers:        [(精灵名, speed_stat)...],   # 降序
    #   role_gaps:          [缺失角色提示...],            # 启发式：无恢复/无状态→缺耐久/控制
    #   energy_hint:        {精灵名: 技能平均 energy_cost},
    # }
```

实现要点（全部确定性、无 LLM）：

- 技能系别：`dataset.load_skills(source)[skill].type`。
- 攻防覆盖：`types.type_effectiveness(atk_type, defender_types)`（[types.py:61](src/environment/types.py#L61)）；`types.stab_multiplier` 只用于提示，不参与硬结论。
- 速度分层：直接读 roster 里已算好的 `stats["speed"]`（`build_roster` 已含 `calc_combat_stats` 输出）。
- 角色缺口是**启发式标签**，输出里显式标 `heuristic: true`，M3 不据此下“必带某角色”的硬结论。

### 4.3 `simulate.py` — `simulate_matchups`

```python
def simulate_matchups(roster: list[dict], opponents: list[list[dict]], *,
                      seeds: list[int], source: DataSource = VALID,
                      max_turns: int | None = None) -> dict:
    # 每对 (roster, opp)：每个 seed 跑两次（换边），a/b 各用确定性贪心玩家
    # 返回 {matchup_key: {games, wins, win_rate, seeds, replay_ok}}
```

- 引擎入口：`build_roster` 转 roster → `BattleSession.start(...)`。**不用 `run_match` 的 `Player` 协议驱动贪心玩家**——`Player.decide(observation, legal, items)` 只拿迷雾 dict、不持有 `session`/`BattleState`（[players.py](src/environment/players.py) 明确“玩家不持有 session 句柄”），而 `prediction.predictions_for(state, side)` 需要 `BattleState`。故 `simulate.py` **直接驱动 session**：每回合读 `session.state` → `predictions_for(state, side)` 得候选伤害 → 与 `legal`/`skill_block_reason` 交叉取最高伤害合法攻击 → `session.submit/resolve/submit_replacement`；无合法攻击则聚能/换人兜底。
- **确定性贪心玩家需新实现**：现有 `ScriptedPlayer`（固定 Decision 序列）、`RandomPlayer`（随机）均非贪心，不能“直接用现有”；新贪心玩家给定 seed 逐位可复现。
- **换边**：同 seed 下 `roster↔opp` 对调再跑一局，抵消先后手偏差。
- **结论标签**：结果是“**模拟（贪心策略）胜率下限**”，不是“真实最优胜率”，输出必须带此标签——M5 的评测集专门有一类失败签名盯这个（§4.4）。

### 4.4 `agent.py` — `TeamAdvisorAgent`

**最小重构 `ChatAgent`**（不重写循环）：

- 把 [agent.py:190](src/rock_pvp_agent/agent.py#L190) 的终结分支抽成可注入 hook：
  ```python
  def _handle_terminal(self, name: str, args: dict, call_id: str) -> tuple[str, bool]:
      # 默认实现 = 现 final_answer 逻辑；返回 (reply_text, terminal)
  ```
  `ChatAgent.__init__` 增加 `terminal_tool: str = FINAL_ANSWER_TOOL` 与 hook；**默认行为不变**（现有测试零回归）。
- **工具集注入缝**：`ChatAgent.__init__` 现在写死 `self._tools = build_agent_tools()`（[agent.py:91](src/rock_pvp_agent/agent.py#L91)，仅 `[calculator, final_answer]`）。需新增 `tools` 注入参数（默认仍 `build_agent_tools()`）或允许子类重设 `self._tools`，否则顾问无法替换工具集。
- `TeamAdvisorAgent(ChatAgent)` 覆盖：
  - `terminal_tool = "submit_team_advice"`；
  - `_handle_terminal` = 解析 `args` → `TeamAdviceSchema` → `EvidenceGate` 校验 → 通过则回 `payload`（最终答案）、失败则回结构化错误文本（**不置 terminal**，让模型修复一次）→ 第二次仍失败 → 回安全降级答案并置 terminal；
  - `system_prompt = ADVISOR_SYSTEM_PROMPT`；
  - 工具集 = M1 catalog/validate + M2 query_trajectory_evidence + analyze_team + simulate_matchups + submit_team_advice。

**关闭思维链外显**：`_run_online` 有**两处** thinking 发射——`reasoning_content`（[agent.py:164](src/rock_pvp_agent/agent.py#L164) 捕获、167–169 发射）与 content-as-thinking（[agent.py:177](src/rock_pvp_agent/agent.py#L177) 起）。需新增一个可注入开关（如 `emit_thinking: bool = True`，顾问置 `False`），**同时**抑制这两处，只保留工具调用事件（[agent.py:199](src/rock_pvp_agent/agent.py#L199)，天然就是“查了哪些证据”）+ 最终校验结果。展示“过了哪些校验”，不展示原始思维链。

### 4.5 `prompt.py` — 顾问 System Prompt

采用 `chatmode_plan_v2.md` §7 骨架，关键三条：

1. 事实优先级：① 当前 `data_digest` 对应的本地库与引擎 → ② replay_ok 且版本匹配的轨迹 → ③ 允许网站（M6）→ ④ Skill 程序性建议；冲突以 ① 为准并说明差异。
2. 工作流程显式列出工具调用顺序（查版本 → 查精灵/技能 → 查轨迹 → analyze/simulate → validate → submit）。
3. 明令“不输出思维链；未过 `validate_team` 的阵容绝不输出为推荐；证据不足只能标 理论构筑/启发式/样本不足”。

## 5. 边界与护栏

- **结构化终结是硬要求**：顾问模式不接受“无工具调用的自由文本终稿”（对应 [agent.py:172](src/rock_pvp_agent/agent.py#L172) 的兜底分支在顾问模式要改成“提示必须调用 submit_team_advice”）。
- **模拟结论不冒充真实胜率**：`simulate_matchups` 输出强制带“贪心策略/种子/样本”标签。
- **修复上限一次**：避免无限校验循环烧 token。
- **不暴露隐藏战局**：analyze/simulate 只用公开 roster 数据；不接 VisibilityGate 之外的观战数据。

## 6. 测试计划

- `advice`：合法 `TeamAdviceSchema` → 通过；非法阵容（首领/血脉不符/未实装技能）→ EvidenceGate 返回 code；修复一次通过 → 通过；两次失败 → 安全降级。
- `analysis`：已知小型 roster 的攻防覆盖与手算 `type_effectiveness` 一致；`role_gaps` 带 `heuristic` 标记。
- `simulate`：同 seed 两次跑结果一致（确定性）；换边后 `games=2*len(seeds)`。
- `agent`：注入 fake LLM 走完“查库→validate→submit”链；fake LLM 直接给非法阵容 → 触发修复 → 降级；顾问模式不发射 reasoning_content（事件流断言）。

## 7. 验收 Gate

- [ ] 顾问模式拒绝自由文本终稿，必须走 `submit_team_advice`。
- [ ] 未过 `validate_team` 的阵容绝不作为推荐输出（EvidenceGate 代码级保证）。
- [ ] 分析/模拟纯确定，模拟结论带“贪心策略”标签。
- [ ] `ChatAgent` 默认行为零回归（现有测试全绿）。
- [ ] 隔离不变量、全量 pytest 绿、覆盖率不降。
- [ ] Gate 报告 → 用户「通过」→ commit。

## 8. 风险与回滚

- **风险**：`ChatAgent` 重构引入回归 → hook 默认实现与现逻辑逐字等价，先跑现有测试。
- **风险**：贪心模拟玩家低估真实强度 → 只作“下限 + 换边 + 多 seed”，结论标签写死。
- **风险**：结构化 schema 过大 → 单条 `UnitAdvice.rationale` 限长、`alternatives` 上限 2。
- **回滚**：`advisor/*` 纯新增；`agent.py` 的 hook 默认等价，`git revert` 可退。
