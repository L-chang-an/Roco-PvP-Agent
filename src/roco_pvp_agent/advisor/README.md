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
- **有界收敛**：按需取证、同轮并行独立查询，默认最多 100 轮；555 秒墙钟预算覆盖进行中的 LLM 与工具调用。
- **可见但不泄露思维链**：CLI/Web 展示阶段进度、工具名与结果摘要；原始 reasoning 不写入回复或审计。
- **失败也有终结**：模型异常、超时、轮次耗尽均返回已取得的阶段结果。新 Web 协议在持久化完成后发出 `reply/done`；写盘失败明确报错，不假报保存成功。

## 每轮公开思考摘要

顾问仍关闭旧 `emit_thinking` 原文通道。主模型每次响应在公开 text 开头给出
`<round_summary>本轮目标、判断依据、待验证问题或下一步</round_summary>`，与本轮
工具调用同时返回；最多 3 条、240 字。`round.summary` 独立发布，工具结果由确定性
展示层另行概括。缺失或格式异常不重试模型，卡片显示未提供摘要。

正文提取排除 provider reasoning/thinking 块。摘要块不混入最终回答；模型说明也不
作为业务校验成功的依据。完整主队人数必须等于声明的 3/6 人；6 人任务在
`validate_team` 中显式传 `team_size=6`。合法主队的备选仍标为未验证。

`AssistantResult` 显式区分文本、合法队伍建议、阶段结果和错误。合法建议通过类型化
字段独立于工具文本大小限制传递；`final_answer` 中出现 JSON 不会自动获得合法队伍身份。

详见 `tmpdocs/milestones/chatmode/` 下的 M1–M5 详设。

Web 队伍终稿 message 使用简短说明，完整建议只通过 artifact 展示；旧 CLI reply 字符串保持兼容。
建议载荷最多 128 KiB、6 个备选；备选需显式校验才获得独立保存资格。Web 续聊采用有版本的
ConversationScopeContext/checkpoint，按完整 Turn 和字符预算裁剪，用户约束与模型假设分开。
