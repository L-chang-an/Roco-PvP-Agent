# 里程碑 R0 检查点

日期：2026-08-30　状态：**☑ 通过**

## 目标
「先能测量强弱，再谈提升」——建立确定性局面特征（situation_key/v_heuristic/ko_thresholds）、轨迹重放分析、配对评测协议（D_sel 实例 + Wilson 95% CI）、Elo/α-rank 基架，加 `evolve` CLI。

## 我做了什么

| 文件 | 改动 |
|---|---|
| `src/environment/evaluate.py`（新） | 从 `main` 移植：`situation_key`/`v_heuristic`/`ko_thresholds`/`hits_to_ko` 纯函数。适配 2 处：① `models.BattleRules`→`rules.BattleRules`；② `hits_to_ko` 的 state proxy 补 `weather=None, side=stub`（本分支 compute_damage 接入印记/天气） |
| `src/rock_pvp_agent/battle/evolution/{__init__,analysis,bench,league}.py`（新） | 从 `main` 移植：`analyze_record`（重放+逐回合快照）/ `paired_eval`+`build_instances`（双向配对+Wilson CI）/ `elo_update`+`alpha_rank`+`PayoffMatrix` |
| `src/rock_pvp_agent/__main__.py` | 加 `evolve eval`（配对评测）/ `evolve reflect`（轨迹分析）子命令 |
| `tests/test_environment_evaluate.py`（新） | situation_key 形状/确定性 + v_heuristic 反对称 + ko_thresholds 值域 |
| `tests/test_evolution_r0.py`（新） | analyze_record 重放确定性 + wilson_ci 边界 + build_instances 规模 + paired_eval 确定性 + Elo/α-rank/PayoffMatrix |

## 验收命令与结果

```bash
uv run pytest tests/test_environment_evaluate.py tests/test_evolution_r0.py -q   # 12 passed ✅
uv run pytest -q   # 837 passed ✅（825 + 12，零回归）
uv run python -m rock_pvp_agent evolve eval --a random --b random --games 2 --seed 7   # 确定性可测 ✅
uv run python -m rock_pvp_agent evolve reflect --traj runs/selfplay-11-1.json          # replay_ok=✅ ✅
```

核心不变量：`ko_thresholds` 166 攻击精灵、`build_instances('d_sel')` 60 实例、`analyze_record` 对真实 selfplay record 复现成功。

## 用户的疑问 / 修改要求
- 询问了「强弱测量」原理（Wilson CI 依据、双向配对为何、非传递性应对）——已在对话中讲解，无代码改动。
- 明确「R0 通过后进入 R1」——本检查点通过即 commit。

## 记录
- `hits_to_ko` state proxy：本分支 `compute_damage` 接入印记/天气批，`SimpleNamespace(rules=rules)` 不够，补 `weather=None, side=stub` 退化为「无印记无天气」裸伤害（与 R0 语义一致）。
- `analyze_record` 的 replay 循环无需适配（`BattleSession.start` 自动选首发）。
- 测试两处签名坑：`RandomPlayer` 的 `seed` 是 keyword-only；镜像实例只跑 1 方向故 n_games≠2×2×2。
- 已知缺口（用户指出）：`evolve eval` 只打印不落盘，无「冻结基线」持久化——留给 R4 门禁/R8 汇报时补。
- 真实 LLM Gate（`--a llm`）需真实 key，由负责人亲自跑；`--a llm --b llm` 是双 LLM 对称检查（最贵，216 局串行），R0 验收标准姿势是 `--a llm --b random`。

## 下一步
R1 记忆库与两阶段检索（MemRL 底座）：`evolution/{memory,reflect,health}.py` + `memory_enabled` 配置，先「记录不改变决策」。
