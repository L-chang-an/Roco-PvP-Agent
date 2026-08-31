# M5 — 回答行为评测集 + 持续改进闭环（详细实施计划）

> 上级：`chatmode_plan_v2.md` §5 M5　|　依赖：M1–M4 全部　|　分支前提：`feat/e-line-v2`；用 pytest 门起步，**不依赖** R 线 `run_epochs`。

## 1. 目标

1. 建立**回答行为评测集**（held-in / held-out），按失败签名量化顾问质量。
2. 每次回答落一条**可审计日志**（工具调用 + evidence_id + 校验结果 + 最终答案摘要 + 反馈，**不含原始思维链**）。
3. 建立**单表面改进闭环**：每次只改一个可审计表面，改动在 held-in/held-out 上不退化，安全/隐私/合法性**零回归**。

## 2. 前置与依赖

- M1（validate 硬闸 / catalog / fingerprint）、M2（轨迹证据）、M3（结构化终结 + EvidenceGate）、M4（ScopeGate / Skill 注册表）。
- 项目纪律：环境不 import agent；真实 LLM Gate 由用户自己跑，评测的**确定性部分**无 key 可跑。

## 3. 交付物

| 文件 | 职责 |
|---|---|
| `advisor/audit.py` | 回答审计日志模型 + `log_answer()` |
| `advisor/eval/cases.py` | 回答行为评测集（held-in / held-out） |
| `advisor/eval/run.py` | 评测运行器 + 失败签名聚类 + 门 |
| `tests/test_advisor_eval.py` | 评测本身的可测性 |

## 4. 详细设计

### 4.1 审计日志（`audit.py`）

每次回答记录：

```python
class AnswerAudit(BaseModel):
    ts: str
    scope_verdict: str                 # M4 判定结果
    message_digest: str                # 用户消息的 sha256（**不存原文**）
    versions: dict                     # {system_prompt_hash, data_digest, rules_digest, skill_versions}
    tool_calls: list[dict]             # [{name, args_digest, result_digest}]（不存原文/不存思维链）
    evidence_ids: list[str]
    candidate_teams: list[dict]        # 候选阵容（可被 validate 复算）
    validation: dict                   # {ok, errors:[{code,...}]}
    final_answer_digest: str           # 最终答案 sha256
    feedback: str = ""                 # 用户反馈（若提供）
```

硬约束：**不存**用户原始消息、**不存** `reasoning_content`、**不存**其他用户数据；存的是可复算的摘要与结构。审计日志可离线重放校验（对 `candidate_teams` 重跑 `validate_team` 验证当时结论）。

### 4.2 回答行为评测集（`cases.py`）

```python
class EvalCase:
    id: str
    split: str                    # "held_in" | "held_out"
    category: str                 # 失败签名（见 4.3）
    input: str
    expected_scope: str           # 期望 ScopeVerdict
    assertions: list[str]         # 期望断言（确定性可验的部分）
```

失败签名分类（对齐 `chatmode_plan_v2.md` §5 M5）：

| category | 说明 | 确定性可验？ |
|---|---|---|
| `ILLEGAL_SKILL` | 推荐了非法/未实装技能 | ✅（重跑 validate） |
| `STALE_TRAJECTORY` | 旧版本轨迹污染结论 | ✅（digest/replay 过滤断言） |
| `LOW_SAMPLE_WINRATE` | 样本太少仍下“强/稳”结论 | ✅（样本量阈值断言） |
| `WEB_INJECTION` | 网页内容提示注入 | ✅（M6 护栏断言，先占位） |
| `OUT_OF_SCOPE_MISS` | 越界问题被误答 | ✅（ScopeGate 确定性） |
| `IN_SCOPE_FALSE_REFUSE` | 正常问题被误拒 | ✅（ScopeGate 确定性） |
| `EVIDENCE_MISMATCH` | 理由与工具证据不一致 | ⚠️ 需模型输出，用户跑 |

- **held-in**：开发期每次改动必跑，覆盖已修复的回归用例 + 关键不变量。
- **held-out**：不参与开发，只在 Gate 时跑，防止“过拟合到评测集”。

### 4.3 单表面改进闭环（`run.py`）

规则：

1. **一次只改一个可审计表面**：System Prompt / 工具 schema / 检索规则 / Skill / 输出校验器（五选一）。
2. 改动必须：held-in 全绿 + held-out 不退化 + 安全/隐私/合法性**零回归**（这三类用确定性断言硬卡）。
3. 确定性部分（ScopeGate 误拒/误答、ILLEGAL_SKILL、STALE_TRAJECTORY、LOW_SAMPLE_WINRATE、EVIDENCE 的结构一致性）**无 key 可跑**；`EVIDENCE_MISMATCH` 这类需真实模型的，交给用户按纪律跑真实 Gate。
4. 失败签名聚类：同一 `category` 的失败聚在一起，产出“下一个该修哪个表面”的建议，而不是散点修补。

### 4.4 Skill 晋级门（probationary → active）

闭环 M4 的「新 Skill 默认 probationary」——晋级必须过 held-out 验证，而不是一次胜局直接晋级：

- 每个 probationary Skill 关联一组 held-out 用例（“遵循该 Skill 的流程 → 产出的队伍全部通过 `validate_team` 且每条理由有 evidence_id”）。
- 在 held-out 上连续通过（如 N 局不产出非法/无证据队伍）才允许 `status` 提升为 `active`。
- 晋级由 `eval/run.py` 判定并落 `AnswerAudit` 可审计记录；安全/隐私/合法性指标零回归是晋级的前置门槛。

## 5. 边界与护栏

- **审计日志不存原始思维链**：这是 M3「关闭思维链外显」的落库对应物。
- **不恢复整场事件流 golden**（对齐 `chatmode_plan_v2.md` §11 对 checkpoint E1 的判断），只重建“回答行为评测集”。
- **隐私零回归**：评测断言里显式校验“日志无 message 原文、无 reasoning_content、无其他用户数据”。

## 6. 测试计划

- `audit`：`log_answer` 输出字段齐全、`message_digest` 非原文、可离线重放 validate 复算。
- `cases`：每个 `category` 至少有 held-in 与 held-out 各 N 条用例。
- `run`：确定性门无 key 可跑；注入一条 `ILLEGAL_SKILL` 用例 → 门红；修复后 → 门绿；held-out 不退化。

## 7. 验收 Gate

- [ ] 七个失败签名分类下均有评测用例（held-in/held-out）。
- [ ] 审计日志满足隐私约束（无原文/无思维链）。
- [ ] 确定性门无 key 可跑、全绿；真实模型评测由用户跑并留记录。
- [ ] 单表面改进规则可被演示（一次改动只碰一个表面）。
- [ ] 隔离不变量、全量 pytest 绿。
- [ ] Gate 报告 → 用户「通过」→ commit。

## 8. 风险与回滚

- **风险**：评测集本身过拟合 → held-out 独立于开发，Gate 时才跑。
- **风险**：EVIDENCE_MISMATCH 无法离线验 → 明确划给用户真实 Gate，不假装自动化。
- **回滚**：纯新增评测模块，`git revert` 可退。
