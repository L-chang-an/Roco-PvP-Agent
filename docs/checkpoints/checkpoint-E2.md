# 里程碑 E2 检查点

日期：2026-08-25　状态：**☑ 通过**（Gate：克制表对照 md 校验 + 事件流核对克制/STAB + 确定性未破坏；负责人 2026-08-26 确认）

## 目标
让系别真正参与战斗：克制表 + STAB 乘子（纯数据）+ 多系别乘积封顶 ×3；并**澄清血脉语义**——`types` 恒为精灵自身系别（克制/STAB 的依据），血脉系别**不改写** types、只决定可携带的血脉技能是哪个系。E1 已跳过，验收改为「测试全绿 + 事件流里肉眼核对克制/STAB」。

## 我做了什么
| 文件 | 改动 |
|---|---|
| `scripts/build_type_chart.py` | 从 `mydocs/type_chart.md` 解析 Markdown 表格 → 生成 `CHART` 全矩阵（转录一致性的来源，`--check` 校验完整性） |
| `src/environment/types.py` | `CHART` 全矩阵（`CHART[防御方][攻击方]`，18×18，**不对称**）+ `normalize_type` + `type_effectiveness`（多系乘积封顶 ×3，抵抗正常乘算，一克制一抵抗互抵）+ `stab_multiplier`（本系 ×1.25） |
| `src/environment/damage.py` | `compute_damage` 新增 `effectiveness` / `stab` 两个显式入参，乘进同一个表达式（**仍只取整一次**） |
| `src/environment/engine.py` | `resolve_skill` 用 `type_effectiveness` / `stab_multiplier` 计算并传入，damage 事件带 `eff`/`stab` 字段 |
| `src/environment/teambuilder.py` | `build_roster` 产出 `types = 精灵自身系别`（**不改写**；血脉只校验并进 spec） |
| `src/environment/__main__.py` | damage 行渲染 `系别×N` / `本系×N` |
| `tests/test_environment_types.py` | 16 条：克制表对照 md 原文（表格）解析 / 单系克制抵抗 / 自克自抗 / 互克对 / **多系克制封顶 ×3** / 多系抵抗乘算 / 一克制一抵抗互抵 / STAB / 血脉不改写系别 / 引擎 eff 封顶 |

## 验收命令与结果
- `uv run python scripts/build_type_chart.py --check` → 18×18 矩阵完整性 ✅
- `uv run pytest tests/test_environment_types.py -q` → 通过（克制表对照 md 逐条钉死转录一致性）
- `uv run python -m environment battle --seed 5` → 事件流正常；`--repeat 2 --quiet` 同 seed 复现（确定性未破坏）
- 全套 `uv run pytest -q` → 全绿

## 用户的疑问 / 修改要求（2026-08-25）
1. **克制表返工**：负责人更新 `type_chart.md` 后按新表**重新转录**。① 存**全矩阵** `CHART[防御][攻击]` 而非 SUPER/RESIST 两个方向——新表不对称（武 克 普通，普通 对 武 中性）；② **多系别规则**：克制取乘积但**封顶 ×3**（火 打 草+虫 = 4 → 3），抵抗正常乘算（0.5×0.5=0.25），一克制一抵抗互抵（2×0.5=1）。
2. **血脉语义澄清**：血脉系别**不影响**精灵原系别——`types` 恒为自身系别，才是影响克制与否的属性；血脉只决定携带的血脉技能是哪个系。原「血脉改写 types」实现已更正。

## 记录
- **旧表手抄错误正是本次返工原因**：`build_type_chart.py` 从 md 生成 CHART，测试对照 md 原文逐条钉死，杜绝二次手抄漂移。
- **`compute_damage` 保持纯函数 + 显式入参**：`effectiveness`/`stab` 是显式数值，不让它读 `TurnContext` 或 `state.types`——表驱动单测 + 将来接修饰链不改调用方形状。
- **偏离原计划一处**：不改 `e0_skills.json`（E0 技能保持「普通」系）；克制/STAB 在 E0b 对战中已可见（E0 精灵有自然系别，如迪莫光）；真实克制在 FULL 数据（E3 后）才完整。**新表 普通 系效果也变**（不再克 火/地/机械，改克 武、被 幽 抗）→ E0 对战伤害数字变化，但确定性不变（同 seed 同 digest）。

## 下一步
P1/P2 技能效果池（先行交付，E3 的数据基础）→ E3 真实数据对战（FULL 精灵 + valid_skills 白名单 + 管理员对局配置）。
