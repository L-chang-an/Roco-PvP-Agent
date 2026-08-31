# 里程碑 G3 检查点

日期：2026-08-31　状态：**☑ 通过**

## 目标
战后用分析型 LLM 分别从 a/b 视角分析成败 → 产出 GlobalMem 的 `update`/`create`/`skip` 决策并落库。

## 我做了什么

| 文件 | 内容 |
|---|---|
| `evolution/globalmem_analyst.py`（新） | `GLOBAL_ANALYST_SYSTEM`（三条硬约束）+ `render_battle_summary` + `GlobalAnalyst.analyze/analyze_both` + `apply_decision` |
| `tests/test_globalmem_analyst.py`（新，16 例） | 迷雾口径 / 双视角隔离 / 三分支落库 / 降级 / token 上限 / 审计 / 不可信轨迹拒绝 |

## 验收命令与结果

```bash
uv run pytest tests/test_globalmem_analyst.py -q   # 16 passed ✅
uv run pytest -q                                  # 1024 passed ✅（1008 + 16，零回归）
```

真实摘要样例（分析师的唯一输入）：
```
[视角] a 方
[终局] 胜 · 共 20 回合
[本局加载的已有经验] 无（本局未命中任何全局经验）
[逐回合]
[T1] 我方行动: 技能「猛烈撞击」（能量 10→9）
        对手行动: 技能（槽位 3）              ← 未揭示只给槽位
        a 迪莫 用「猛烈撞击」→ 水蓝蓝 伤害 71（剩76%）  ← 敌方血量百分比
```

## 记录（坑 / 决策）

- **双视角必须互不可见**（最重要的安全约束）：a 的分析师只看 a 的迷雾轨迹。测试断言
  `"[视角] b 方" not in llm.seen[0]`——绝不能把两侧摘要拼在一次调用里，否则产出的
  GlobalMem 会带上「我知道对手当时在想什么」的不实前提，下一场注入即泄漏。
- **`update` 只信 record 不信 LLM**：`update` 但 record 无该侧 `global_mem_*` → 自动降级
  `update→create`，防 LLM 乱指 entry_id。
- **`skip` 必须允许**：否则每局硬塞一条会让库膨胀 + 噪声。
- **不可信轨迹拒绝**：`replay_ok=False` → 拒绝分析（同 R1/R2 纪律）。
- **复用而非重造**：直接复用 R3 `ReflectionService` 的 `_parse_reflection_json`、`llm=` 注入缝、
  异常降级、`diagnostics` 四套既有纪律。
- 成本：每局 +2 次 LLM 调用（a/b 各一次），相比对战本身 ~40 次增量约 5%——这是整套设计
  相对 Playbook（每候选 864 局门禁）的核心优势。

## 下一步
G5：新编排 `run_battles`——完整流程（固定池选阵容 → 战斗 → GlobalMem/局部 mem 注入 →
双视角分析 → 落库 → Q 更新）+ 周期性 A/B 度量（开/关 GlobalMem 对比）。
（G4 = LLM 自选阵容，已拍板延后为未来工作。）
