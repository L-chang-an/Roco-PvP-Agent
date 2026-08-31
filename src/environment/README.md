# environment — E 线：确定性回合制战斗引擎

本目录是项目的**冻结地基**：一个纯 Python、零第三方依赖的回合制精灵对战引擎。它不 import 任何
`roco_pvp_agent` / `ui` 代码（隔离不变量），只提供数据加载、组队校验、回合结算、重放与观测。

## 设计原则

1. **确定性**：注入式种子 RNG（`rng.BattleRng`），同 seed 逐字节复现整局。引擎 RNG 与玩家 RNG 流分离。
2. **迷雾**：`visibility.filter_events_for` / `view.observe` 只给玩家白名单视角，不泄漏敌方隐藏配置。
3. **可重放**：轨迹只存 `(rules, 阵容, seed, 逐回合提交序列)`，`replay.replay_record` 逐回合比对 `state_hash`。
4. **数据指纹隔离**：`datafingerprint` 对「数据 + 规则」做摘要，跨版本轨迹/记忆自动隔离。

## 核心不变式（审计已验证）

- **确定性**：同 seed 同输入 → 同事件流。
- **迷雾**：玩家视角事件不含敌方绝对血量等隐藏字段。
- **重放**：一条轨迹可被 `replay_record` 完美重放（马尔可夫不变式）。

## 数据模型

- **原始数据**（`dataset.py`）：`RawSpirit` / `RawSkill`，从 `data/*.json` 加载归一化。593 只精灵、
  200+ 已实装技能、18 系、进化链/家族/首领/形态字段。
- **组队**（`teambuilder.py`）：`TeamPick`（玩家意图）→ `validate_team`（硬闸）→ `build_roster`（引擎 roster spec）。
- **回合状态**（`models.py`）：`BattleState` / `SideState` / `Unit`，只含「回合间事实」，回合内派生量不落盘。
- **规则**（`rules.py`）：`BattleRules`（frozen dataclass，全部可调数值唯一事实源）。

## 模块导览（按关注点分组）

| 关注点 | 模块 |
|---|---|
| 数据 | `dataset` 加载归一化 · `datafingerprint` 指纹 · `skillbook` 技能白名单 |
| 组队 | `teambuilder`（TeamPick / validate / build_roster）· `statline`（属性/性格公式）· `presets` 预设阵容 |
| 数值 | `types`（18 系克制表）· `damage`（伤害结算）· `prediction`（确定性预估）· `compiler`（技能编译） |
| 回合 | `session`（对局门面：submit/resolve/补位）· `engine`（回合结算）· `match`（整局编排 drive_turn/run_match）· `actions`（动作空间/合法性） |
| 机制 | `effects`（技能效果）· `traits`（特性）· `statuses`（状态）· `marks`（印记）· `weather`（天气）· `evolution`（进化/首领化）· `modifiers`（增减益）· `hooks` / `triggers` / `primitives` / `atom` / `pipeline` / `reducer`（引擎内部机制） |
| 观测/重放 | `view`（观测）· `visibility`（迷雾过滤）· `replay`（重放自检）· `events`（事件） |
| 玩家 | `players`（Player 协议 + ScriptedPlayer / RandomPlayer） |
| 度量 | `evaluate`（R0 可观测度量：situation_key / v_heuristic） |
| 配置 | `rules`（BattleRules）· `battle_config`（管理员对局配置）· `__main__`（数据报告 CLI） |

## 如何扩展

- **加一个技能效果**：在 `skillbook` 的效果表注册，实现结算逻辑（`effects`/`engine`），扩 `data/valid_skills.json` 白名单。改动会改变 `data_digest`，历史轨迹/记忆自动重基线。
- **加一个特性/状态/天气/印记**：在对应模块实现 + 注册，补确定性/迷雾/重放三不变式测试。
- **本模块不 import `roco_pvp_agent`**：引擎与 Agent 层解耦，扩展时保持这一隔离。

详见 `tmpdocs/engine/` 下的引擎设计文档。
