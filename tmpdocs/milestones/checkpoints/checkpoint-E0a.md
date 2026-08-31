# 里程碑 E0a 检查点

日期：2026-08-23　状态：**☑ 通过**（Gate：三条验收命令 + 性格/公式/校验三次修改要求全部落地）

## 目标
E0a 组队与数据层：双方能自由组队（选精灵 / 1–3 个技能 / 血脉 / 个体值 / 性格），校验通过后算出最终六维、打印出来。全程零回合逻辑、零第三方依赖。

## 我做了什么
| 文件 | 改动 |
|---|---|
| `src/environment/rules.py` | `BattleRules`（team_size=3 / skill_slots=3 / iv_max=10）+ `E0_ITEMS` |
| `src/environment/data/e0_skills.json` | 从 `mydocs/E0_skills.json` 原样拷入（14 条，逐字节一致） |
| `src/environment/data/e0_spirits.json` | 手写 6 只精灵（迪莫/圣水迪莫/布布种子/小火猴/水蓝蓝/猫老大），形状对齐真实数据 |
| `src/environment/dataset.py` | `_to_int`（防 `0.0 is falsy`）+ 中文六维归一 + `load_skills`/`load_spirits`（lru_cache） |
| `src/environment/skillbook.py` | 14 条 `SkillEffect` 显式效果表，引擎永不读 desc |
| `src/environment/statline.py` | 30 种性格（加X减Y）+ 真实属性公式 + `NEUTRAL_NATURE`/`is_valid_nature` |
| `src/environment/teambuilder.py` | `TeamPick`/`learnable_skills`/`validate_team`/`build_roster` |
| `src/environment/__main__.py` | `--data-report` / `--team-report` / `--probe-errors` |
| `pyproject.toml` | wheel packages += `src/environment` + data force-include |
| `tests/test_environment_dataset.py` | 归一 / 效果表双向断言 / 性格完备性 / 公式手算 / CLI 冒烟 |
| `tests/test_environment_team.py` | 组队校验每一条拒绝理由 + roster spec 形状 |
| `docs/checkpoints/checkpoint-E0a.md` | 本文件 |

## 验收命令与结果
- `uv run python -m environment --data-report` → 技能 14 条（攻击 6 / 防御 3 / 状态 5）· 效果表 14 条 · 键集合一致 ✅；精灵 6 只 · 六维已归一 · 可学池最小 4 最大 9；`防御.power = 0`（不是 30）✅
- `uv run python -m environment --team-report` → 两队六维对着真实公式手算核对 ✅；`校验：两队合法 ✅`
- `uv run python -m environment --probe-errors` → 非法阵容 11 条错误一次报全（含技能数 1–3、个体值 ≤10/≤3 维度）
- `uv run pytest tests/test_environment_dataset.py tests/test_environment_team.py -q` → 通过
- 全套 `uv run pytest -q` → **133 passed**；`--cov=environment` → **96%**（statline/teambuilder 100%）；`rock_pvp_agent` 回归哨兵一行未动

## 用户的疑问 / 修改要求（三次迭代，全部落地）
1. **性格扩充**：`NATURE_BONUS` 从 9 种改为 30 种非中性（提升六维之一 × 降低另外五维之一，6×5 全覆盖），命名固定「加『某维』减『另一维』」；提升 ×1.20、降低 ×0.90。旧 8 个名字（勇敢/固执/…）整体替换。
2. **真实属性公式**（替代占位）：生命 `(1.7×(种族+iv×3)+70)×性格修正+100`；其他五维 `(1.1×(种族+iv×3)+50)×性格修正+50`。占位公式的「中性恒等」性质取消（中性值 ≠ 种族值）。
3. **校验条件**：技能数改为 **1–3 个**（至少 1、至多 3）；个体值每项 ≤10（`iv_max=10`）、**最多 3 个维度有投入**。

## 记录
- **`0.0 is falsy`**：`_to_int` 绝不写 `x or default`（只写 `default if x is None or x=="" else int(x)`）；`防御.strong="0"` 第一天就撞这个坑。
- **血脉语义（E0）**：血脉 = 系别名；每只精灵用 `bloodlines` 字段声明（真实 `spirits_details.json` 没有此列表，E0 手写数据的小扩展）；选**合法**血脉 → 可学池并入 `skills.血脉`。**非法血脉不给任何好处**——修掉初版「非法血脉偷渡血脉技能」的漏洞。
- **「坦率」不进性格表**：作为 `NEUTRAL_NATURE` + 未知回退（`NATURE_BONUS.get` 落空 → `("","")`）存在；`validate_team` 必须用 `is_valid_nature` 判断，否则 `TeamPick.nature` 默认值「坦率」会让所有默认队伍被当非法。
- **占位公式的隔离兑现**：`calc_combat_stats` 隔离在数据层，真实公式替换时只换了函数体，调用方 `build_roster` 与测试的形状一行未动。
- **个体值维度计数坑**：`ev_dims` 初版用 `val > 0` 直接比较，遇到测试故意塞的字符串 `"31"` 会 `TypeError`；改成单趟 `elif` 链，非 int 值先报「越界」、不参与维度计数。测试助手的 `skills or default` 会吞掉 `[]`，改 `if skills is None`。
- **术语已定（2026-08-23）**：只有个体值（`iv`），**没有努力值**。字段保持 `iv`，不做重命名；真实参考项目里的 `ev_config`（努力值）与本项目无关。`iv_max` 注释已同步改为「每点折合 +3，满值 10 → 加 30」。
- ~~遗留 TODO：`iv_max` 注释「满值给 +10%」过时~~ → 已随术语定稿一并修正（2026-08-23）。

## 下一步
E0b 回合内核：`BattleRng`（注入式种子 RNG）+ 领域模型（`BattleState`/`Unit`/`SideState` + `to_dict`/`from_dict`/`state_hash`）+ 动作/伤害漏斗（`skill_block_reason`/`compute_damage`/`apply_hp_loss`/`apply_heal`）+ `execute_turn` 回合循环（局部 `TurnContext`）+ 随机策略自己打完一局，同 seed 逐字节复现。
