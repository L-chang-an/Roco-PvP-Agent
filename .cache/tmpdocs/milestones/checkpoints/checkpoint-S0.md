# 里程碑 S0 检查点

日期：2026-08-30　状态：**☑ 通过**

## 目标
为 M/R 两线提供共同前置：数据指纹（VersionGate 锚点）+ 修复 P0 迷雾泄漏（AUD-E-001）+ 轨迹 stamp 指纹（AUD-E-002）。

## 我做了什么

| 文件 | 改动 |
|---|---|
| `src/environment/datafingerprint.py`（新） | `data_digest()`/`rules_digest()`——语义口径：hash 加载后的归一化数据（RawSpirit/RawSkill 全字段，排除 `desc`/`trait_desc`）+ rules + families + evolution_chains |
| `src/ui/battle.py` | ① `on_turn_result` 改 `filter_events_for("b", ...)` 修 AUD-E-001；② `record()` 加 `data_digest`/`rules_digest` |
| `src/rock_pvp_agent/battle/selfplay.py` | record dict 加 `data_digest`/`rules_digest` |
| `tests/test_environment_datafingerprint.py`（新） | 确定性/敏感性/精确性三条不变式 |
| `tests/test_ui_battle_fog.py`（新） | AUD-E-001 回归：对手收到的敌方伤害事件无 `target_hp_left` |

## 验收命令与结果

```bash
uv run pytest tests/test_environment_datafingerprint.py tests/test_ui_battle_fog.py -q  # 6 passed ✅
uv run pytest -q   # 825 passed ✅（基线 819 + 6 新增，零回归）
```

端到端 stamp（`run_selfplay`）：`replay_ok=True`，record 含 `data_digest`/`rules_digest`，重放不受影响。

## 用户的疑问 / 修改要求
无。按推荐（语义口径 + S0 三子项一次交付）直接通过。

## 记录
- 迷雾测试首版断言写宽（把「打到 b 自己单位」的合法绝对血量也判为泄漏），修正为按 `target == 敌方` 过滤断言。
- 指纹口径：语义（排除 desc/trait_desc 展示字段），比 main 旧版 `_data_digest`（仅名字列表）更强，覆盖 E 线 families/evolution_chains/is_boss/精灵技能池。
- `replay_record` 忽略新键已确认（只读 6 必需键 + items），stamp 后重放仍 `all_match=True`。

## 下一步
R0 里程碑（可观测度量）：从 `main` 移植 `environment/evaluate.py` + `evolution/{analysis,bench,league}.py`，按 E 线数据适配。
