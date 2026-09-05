# advisor — 组队顾问底座（M 线）

本目录是「受约束的组队顾问」的全部底座：让 Agent 只能通过**白名单 DSL** 读引擎数据、通过**硬闸**
校验阵容、通过**固定模板**拒答越界，最终输出**可溯源、可回归**的组队建议。全部只对标当前引擎
（`data_digest` 钉死），不标榜「真实环境最优」。

## 模块导览

| 里程碑 | 模块 | 职责 |
|---|---|---|
| M1 | `catalog.py` | 白名单查询 DSL（`search_spirits`）+ 精灵/技能档案 + 合法构筑项 + 版本指纹 |
| M1 | `validate.py` | `validate_team` 结构化硬闸（19 条错误分支 → 16 个错误码） |
| M2 | `trajectory.py` | 轨迹证据：归一化 + 聚合 + 版本闸/重放闸，人机/自博弈分开统计 |
| M3 | `advice.py` | `TeamAdviceSchema` + `submit_team_advice`（LegalityGate / VersionGate / EvidenceGate） |
| M3 | `analysis.py` | `analyze_team`（攻防覆盖 / 速度分层 / 角色缺口，纯计算） |
| M3 | `simulate.py` | `simulate_matchups`（贪心换边多 seed 胜率下限） |
| M3 | `agent.py` | `TeamAdvisorAgent` + 顾问工具集 |
| M3 | `tool_schemas.py` | 全部顾问工具的严格 Pydantic 入参契约（含嵌套结构与枚举） |
| M3 | `prompt.py` | 顾问 System Prompt（事实优先级 + 工作流程 + 纪律） |
| M4 | `scope.py` | ScopeGate：确定性领域判定 + 越界/注入/模糊/欢迎固定模板 |
| M4 | `skills.py` | 顾问 Skill 注册表（版本/哈希/工具白名单，新 Skill 默认 probationary） |
| M5 | `audit.py` | 回答审计日志（全摘要，无原文/思维链） |
| M5 | `eval/` | 回答行为评测集（七类失败签名 + 确定性门） |

## 六道闸（安全护栏）

1. **ScopeGate**（`scope.py`）：越界/注入问题在进 LLM 前被固定模板拦截。
2. **VersionGate**（`trajectory.py` + `advice.py`）：`data_digest`/`rules_digest` 不匹配的证据绝不采用。
3. **ReplayGate**（`trajectory.py`）：`replay_ok=false` 的轨迹不得成为证据。
4. **LegalityGate**（`validate.py` + `advice.py`）：未过 `validate_team` 的阵容绝不输出为推荐。
5. **EvidenceGate**（`advice.py`）：每条理由必须关联 `evidence_id` 或标注不确定性。
6. **VisibilityGate**：顾问只消费已结束轨迹 + 公开图鉴，不读进行中对局。

## 设计要点

- **纯确定性、可单测**：`catalog` / `validate` / `trajectory` / `analysis` / `simulate` / `advice` / `scope` / `skills` / `audit` / `eval` 均不依赖 LLM，核心功能无 key 可跑。
- **DSL 不执行代码**：`search_spirits` 只编译到已审计的 `dataset.*` / `teambuilder.*` 只读函数。
- **输入契约闭合**：工具参数均为 `strict=True`、`extra="forbid"`；Dispatcher 在 handler 前用同一份
  Pydantic schema 强制校验，拼错字段、隐式类型转换、非法枚举和错误嵌套不会进入业务代码。
- **安全并发**：图鉴、校验、轨迹、分析、模拟、Skill 与记忆查询显式标记为只读并发安全；只有
  整个模型调用批次都满足该策略时才进入线程池，混入终结/串行/未知调用即整批保守串行。
- **延迟加载**：高频图鉴与合法性工具立即暴露完整 schema；低频轨迹、分析、模拟、Skill 和记忆
  工具启动时只展示名称与用途，经 `tool_search` 命中后从下一模型轮次起可见、可执行。加载缓存按
  CLI/Web 会话隔离，重置会话时一并清除。
- **单一工具清单**：工具定义、schema、handler 与治理策略只在顾问 Registry 装配；模型视图、
  Dispatcher、延迟目录以及 Skill 权限均由该实例派生，不维护第二份全局工具名白名单。
- **Skill 是数据不是指令**：`body`/`trigger` 只作流程建议；`allowed_tools` 是每个 Skill 声明的
  最小权限，并在返回前与当前顾问 Registry 求交。
- **有界收敛**：按需取证、同轮并行独立查询，目标 3 轮/最多 4 轮；55 秒墙钟预算覆盖进行中的 LLM 与工具调用。
- **可见但不泄露思维链**：CLI/Web 展示阶段进度、工具名与结果摘要；原始 reasoning 不写入回复或审计。
- **失败也有终结**：模型异常、超时、轮次耗尽均返回已核实的阶段结果，SSE 异常路径也保证发出 `done`。

详见 `tmpdocs/milestones/chatmode/` 下的 M1–M5 详设。
