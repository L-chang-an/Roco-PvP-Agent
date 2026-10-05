# 验证记录

日期：2026-09-22；Python 3.12.13；Git 基线 `b39a6fe0470d973bf1097a3df0d3390c739fac0c`。全部命令从项目根目录运行。未调用真实 LLM/API，未改业务源码及现有对局/记忆库。

## 既有回归

主回归：

```bash
.venv/bin/python -m pytest \
  tests/test_evolution_r0.py tests/test_evolution_r1.py \
  tests/test_evolution_r2.py tests/test_evolution_r3.py \
  tests/test_evolution_r4.py tests/test_evolution_r5.py \
  tests/test_memory_inject.py tests/test_globalmem.py \
  tests/test_globalmem_analyst.py tests/test_globalmem_inject.py \
  tests/test_globalmem_run.py tests/test_advisor_memory.py \
  tests/test_battle_player.py tests/test_selfplay.py -q
```

结果：`143 passed in 52.09s`。

人机/顾问补充回归：

```bash
.venv/bin/python -m pytest -q \
  tests/test_advisor_memory.py tests/test_advisor_trajectory.py \
  tests/test_ui_battle_fog.py tests/test_chat_sessions.py
```

结果：`34 passed in 2.21s`。其中 `test_advisor_memory.py` 与主回归重叠，因此不把 143 与 34 简单相加声称独立测试总数。

并行核心审查还运行了 `test_evolution_r1.py`、`test_memory_inject.py`、四个 GlobalMem 文件及 `test_advisor_memory.py`，结果 `72 passed in 40.41s`，与主回归重叠，仅记录，不另累计。

## 最小复现

```bash
.venv/bin/python tmp/memory-review-2026-09-22/reproduce_core_findings.py
.venv/bin/python tmp/memory-review-2026-09-22/reproduce_learning_findings.py
.venv/bin/python tmp/memory-review-2026-09-22/reproduce_human_findings.py
```

各脚本使用临时对局/记忆目录，将结果写到旁边的 JSON 文件。部分脚本用断言确认问题存在，因此源码修复后可能按预期失败；应将其转换为“正确行为”的正式回归测试，不要保持缺陷断言。

| 证据文件 | 验证方式 | 主要结果 |
|---|---|---|
| `core-findings-evidence.json` | 真实存储与离线确定性对局 | 空库首局产生 38 次采纳/更新；相同文本自替代后 active=0；重复记账 Q 0.30→0.51；特征碰撞与缺失版本放行 |
| `learning-findings-evidence.json` | 首发/价值函数走真实对象；昂贵编排使用明确标注的 mock/spy | 正规重放成功但学习分析失败；只有训练传记忆；历史策略是随机代理；慢更新分数缺失；续跑检查不足 |
| `human-findings-evidence.json` | 真实 UI controller、真实引擎、元数据属性探针 | 未完赛计入样本；镜像胜负归属偏差；winner 篡改未被闸拦截；UI 丢失已有学习元数据 |

Mock 分数只用于验证分支接线和接口边界，不能用于宣称胜率或训练收益。

## 证据边界

- 以上是工程审查，不是对真实 LLM 的新学习实验，不包含任何新的 PVP 胜率提升结论。
- 原有样本数量与玩家类型详见 `repository-snapshot.json`；记录中标注 llm 不等于本次独立复核过远端调用和模型身份。
- 未检查全部实时游戏规则、模拟器所有机制或全部项目测试。本报告有关“真实游戏水平”的结论限定为尚需验证，不能由本地模拟器回归替代。
- 文档中的新模块、字段、统计方法与阶段验收条件均为改进建议；本次没有实施这些业务变更。
