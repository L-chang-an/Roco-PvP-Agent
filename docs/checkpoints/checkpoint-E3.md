# 里程碑 E3 检查点

日期：2026-08-25　状态：**☑ 通过**（Gate：valid_skills 校验 + VALID 真实对局 + 管理员接口拒绝非法规模；负责人 2026-08-26 确认）

## 目标（2026-08-25 负责人重新定义）
E3 的对局数据 = **全部精灵**（FULL 593 只，特性未实现的装白板 `default` 上场）+ **已实装效果的技能**（P1∪P2 = 179，合并进 `valid_skills.json`）。每只精灵可携带技能 **1–4 个**；对局规模与命数由**管理员接口**配置（3–6 只 / 1..team_size−1 命）。真实克制 / STAB 至此完整生效。

## 前置：P1/P2 技能效果池（先行交付，2026-08-25）
| 交付 | 内容 |
|---|---|
| `skillbook.compile_p1_effect` | 把 desc 固定模式编译成 `SkillEffect`：纯伤害 / 纯防御 / 纯六维状态。P1 125 个全命中 |
| `SkillEffect.stat_effects` | 状态系从单 stat 字段扩到 `(target, stat, mode, layers)` 列表（多目标/多维度/负层减益） |
| `battle_ready` 白名单 | 教学 `E0_EFFECTS` ∪ `P1_EFFECTS`（126 个）；P2 再扩到 **179 个** |
| `data/valid_skills.json` | 从 full_skills 过滤 battle_ready → 179 条（格式同 full_skills，`--check` 校验） |
| 测试 | `test_p1_effects`(16) / `test_p1_integration`(12) / `test_environment_effects`(10) / `test_p2_effects`(58) / `test_p2_mechanics`(29) / `test_p2_battle`(5) / `test_environment_dataset_full`(11) / `test_environment_team_full`(17) |

> **E0 教学数据与现有测试不动**：P1/P2 是增量能力（双源共存），E0 基线 digest 逐字节不变。

## 我做了什么
| 文件 | 改动 |
|---|---|
| `scripts/build_valid_skills.py` | 从 full_skills 过滤 `battle_ready` → `data/valid_skills.json`（179 条） |
| `src/environment/dataset.py` | 新增 `DataSource.VALID`：技能读 valid_skills.json、精灵同 FULL（593 只/家族/脏值） |
| `src/environment/teambuilder.py` | VALID 可学池 = FULL 池 ∩ 白名单；家族/首领/血脉规则同样生效；非白名单技能给「效果未实装」文案；`build_roster` 增加 `rules` 参数 |
| `src/environment/rules.py` | `skill_slots = 4`（1–4 技能） |
| `src/environment/battle_config.py` | **管理员接口**：`validate_team_size`（3–6）/ `validate_lives`（1..team_size−1）/ `build_battle_rules`（非法 → ValueError）。纯函数无全局状态 |
| `src/environment/__main__.py` | `--data VALID`；battle `--team-size`/`--lives`（经管理员接口）；p1 预设规模自适应（跨家族候选池） |
| `src/environment/presets.py` | `valid_spirit_candidates` / `p1_team` / `p1_preset`（CLI 与战斗页共用的单一来源，后抽自 __main__） |
| 测试 | `test_environment_dataset_valid`(5) / `test_battle_config`(9) / `test_environment_team_valid`(9) / `test_environment_e3`(4，4v4/3命完整局 + 白板特性) |

**可对战白名单的哲学**：真实技能里 443/553 带中文效果描述，而本计划不做效果引擎。策略是全量加载，但只把效果已实现（P1∪P2 白名单）的技能放进 `valid_skills.json`，其余登记为未支持并输出覆盖率报告——「还有多少没实现」永远是**可见数字**，不是静默 no-op。

## 验收命令与结果
- `uv run python scripts/build_valid_skills.py --check` → 179 / 全 battle_ready ✅
- `uv run python -m environment --data-report --data VALID` → 179 技能 + 593 精灵（同 FULL）
- `uv run python -m environment battle --data VALID --preset p1 --seed 7 --team-size 4 --lives 3` → 真实数据完整对局
- `uv run python -m environment battle --team-size 7` → ❌ 管理员接口拒绝（>6）
- `uv run python -m environment battle --lives 3 --team-size 3` → ❌ 拒绝（lives ≥ team_size）
- `uv run pytest tests/test_environment_e3.py -q` → 通过；全套 `uv run pytest -q` → 全绿

## 记录
- **管理员接口与 `BattleRules` 解耦**：`BattleRules` 本身无约束（测试用 team_size=1/2 直构），约束只落在这层接口——Web UI 能捕获 ValueError 回显，引擎测试不被卡死。
- **每只精灵可带 1–4 技能**（`skill_slots=4`）：P1/P2 预设队每只带前 4 个 battle_ready 默认技能，确定性、家族唯一、全部能开战。
- **白板特性**：特性未实现的精灵装 `default` 上场（`test_environment_e3` 钉死），特性目录与覆盖率经 `--data-report --effects` 可见。
- **`--data E0|FULL|VALID` 三源共存**：E0 教学基线 digest 逐字节不变（回归哨兵），FULL/VALID 是增量能力。

## 下一步
E4 迷雾观测 + E5 LLM 对战玩家（后合并为 E4+E5：迷雾 + 人类 vs 假LLM 对战页 + 无平局 + 轨迹持久化）。
