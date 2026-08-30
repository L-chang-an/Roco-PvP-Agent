# 里程碑 R2 检查点

日期：2026-08-30　状态：**☑ 通过**

## 目标
把「这局输了」归因到**具体回合**——三信号交集：①校准偏差（LLM 预测 vs 实际伤害）；②价值落差（v_heuristic 回合前后差）；③反事实回放（fork 状态 + 代理尾策略）。

## 我做了什么

| 文件 | 改动 |
|---|---|
| `evolution/credit.py`（新，308 行） | 从 main 移植：`calibration_miss`/`counterfactual`/`CounterfactualCache`/`mine_critical_turns` |
| `battle/player.py` | `battle_act` 加 `prediction` 参数；`LLMPlayer` 加 `_turn_log` + `_record_turn`（成功与兜底都记录） |
| `battle/selfplay.py` | record 加可选 `analysis_a/b`（从玩家 `_turn_log` 读，向后兼容） |
| `__main__.py` | 加 `evolve credit`（`--out`/`--repeat`/`--m`） |
| `tests/test_evolution_r2.py`（新） | 校准偏差边界 + 关键回合卡片确定性 |

## 验收命令与结果

```bash
uv run pytest tests/test_evolution_r2.py -q   # 2 passed ✅
uv run pytest -q   # 959 passed ✅（936 + 2 R2 + 负责人并行 M 线 ~21，零回归）
uv run python -m rock_pvp_agent evolve credit --traj runs/selfplay-11-1.json --m 4 --repeat
#   6 张卡片 · 确定性复现：✅ 逐位一致
```

## 用户的疑问 / 修改要求
- 面试模拟：询问 R2 设计理由、三信号流程、反事实代理尾选择——已在对话中讲解，无代码改动。

## 记录
- **feedback.py 已就绪**：R1 拷贝的已是 main 最新版（含 R2 参数），R2 无需再动。
- **`BattleSession(state.clone())` + `state.clone()` 本分支都有** → 反事实 fork 直接可行。
- **只移植 R2、不移植 R4**：main 的 `player.py` 已含 R4 的 `strategy` 参数，只移植了 R2 的 `prediction`/`_turn_log`，不带 R4。
- **反事实代理尾 = RandomPlayer**：确定性、便宜、有偏（代理≠LLM），作为一致信度信号足够，R7 才换 LLM 尾。
- **三信号分工**：校准（干净但依赖 LLM 主动给 prediction）+ 价值落差（广但粗糙）+ 反事实（精确但贵）——便宜信号定位候选、贵信号确认。
- **并行工作**：负责人并行做 M 线（agent.py + advisor/* + test_advisor_*），本次 commit **只 stage R2 文件**。

## 下一步
R3 反思与有界编辑（双分析师 + SkillOpt 纪律）：`evolution/{playbook,reflect(补全),editor,run}.py`。
