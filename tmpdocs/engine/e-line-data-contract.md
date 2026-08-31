# E 线数据契约（Data Contract）

> 版本：v1.0　日期：2026-08-28　分支：`feat/e-line-v2` @ `19f5b0e`
> 定位：E 线（组队 → 战斗 → 轨迹）全部数据协议的一手参考；同时标注与 R 线的接缝。
> 配套：`docs/audit/00-baseline.md`、`docs/audit/04-findings.md`、`docs/audit/final-audit-report.md`

---

## 0. 全景

```
┌────────────┐   TeamPick    ┌────────────────────┐  roster spec  ┌─────────────┐
│  玩家意图   │ ───────────▶ │ validate_team 校验   │ ────────────▶ │ build_unit   │
│ (选精灵/技能│              │ build_roster 产出    │   (数据层↔引擎 │ → Unit       │
│  /血脉/性格│              │ 六维计算/家族/血脉规则 │    唯一一层缝) │              │
│  /个体值)  │              └────────────────────┘               └──────┬──────┘
└────────────┘                                                          │
                    ┌──────────────────────────────────────────────────▼────┐
                    │ BattleState（回合间事实）+ TurnContext（回合内派生量）   │
                    │  transition = f(state, decision_a, decision_b)        │
                    └──────────────────────────────────────────────────┬────┘
                                                                        │
                         ┌──────────────────────────────────────────────▼───┐
                         │ 轨迹 record：只存 (rules, roster spec, seed,      │
                         │  逐回合提交序列 + state_hash) → replay_record 重放  │
                         └──────────────────────────────────────────────────┘
```

**依赖方向铁律**：`environment` 零第三方依赖、永不 import `rock_pvp_agent`/`ui`；R 线与 UI 单向依赖 `environment`。

---

## 1. 组队数据协议

### 1.1 数据源（读取）

> E0 教学数据已于 2026-08-29 删除，数据源统一为 FULL（`DEFAULT_SOURCE = FULL`）。

| 源 | 文件 | 规模 | 用途 |
|---|---|---|---|
| `DataSource.FULL` | `data/full_skills.json` + `data/full_spirits.json` + `data/families.json` | 553 技能 / 594 精灵 | **唯一数据源**（配队 + 默认） |
| `DataSource.VALID` | `data/valid_skills.json`（E3 生成）+ FULL 精灵 | **179 个已实装效果技能** / 594 精灵 | 可开战白名单视图（未实装技能置灰不可选） |

入口：`load_skills(source)` / `load_spirits(source)`（`lru_cache(maxsize=4)`，[dataset.py:236-318](src/environment/dataset.py#L236)）。

### 1.2 归一化（数据处理）

`RawSkill.from_dict` / `RawSpirit.from_dict`（[dataset.py:95-177](src/environment/dataset.py#L95)）：

- **中文六维 → 英文 key**：`生命→hp 物攻→atk 魔攻→sp_atk 物防→def 魔防→sp_def 速度→speed`；
- **`_to_int` 纪律**：数值字段绝不写 `x or default`（`"0"`/`0` 合法而 falsy）；`strong: null` → `power=0`（不是 30）；
- **技能拆池**：`默认 / 血脉 / 技能石 / 传说`；
- **FULL 派生**：`family_key`（进化链首编号一致 = 同族）、`is_boss`、`family_lowest`；
- **脏记录跳过**：`_should_skip`（技能表空 / 六维缺失），全库仅"学院呱呱"。

### 1.3 玩家输入形状（TeamPick）

```python
TeamPick(spirit: str, skills: list[str], bloodline: str = "",
         nature: str = "坦率", iv: dict[str, int] = {})
```

约束：`skills` 1–`rules.skill_slots`（默认 4）个、都在该精灵**可学池**；`iv` 每项 0–`iv_max`(10)、**最多 3 维有投入**；`nature` ∈ 30 种非中性（"加X减Y"）+ 中性"坦率"。**队伍规模仅允许 3 或 6**（2026-08-29 拍板，`battle_config.ALLOWED_TEAM_SIZES = (3, 6)`）。

**可学池** `learnable_skills`（[teambuilder.py:41](src/environment/teambuilder.py#L41)）：
- FULL：`默认 ∪ 技能石 ∪ 传说 +（血脉技能按 系别==所选血脉 过滤）`；
- VALID：FULL 池 ∩ 已实装效果白名单。

### 1.4 校验（validate_team，一次报全）

公共：规模==`team_size`、技能数 1–`skill_slots`、技能可学、精灵存在、性格合法、IV 键/值/维度数、道具名。
FULL/VALID 额外三条（[teambuilder.py:110-172](src/environment/teambuilder.py#L110)）：
1. **同家族只入队一只**（链首编号一致）；
2. **血脉技能系别 == 所选血脉**（无血脉禁带血脉技能）；
3. **首领形态不可入队**。

### 1.5 输出形状（roster spec = 数据层 ↔ 引擎的唯一一层缝）

`build_roster(picks, source, rules)` 产出，`build_unit` 直接吃（不重算六维）：

```json
{ "name": "迪莫",
  "types": ["光"],
  "stats": {"hp": 390, "atk": 210, "sp_atk": 210, "def": 180, "sp_def": 180, "speed": 170},
  "skills": ["抓挠1", "加物攻"],
  "nature": "加攻击减速度",
  "bloodline": "",
  "iv": {"atk": 5},
  "trait": "最好的伙伴" }
```

字段说明：

| 字段 | 类型 | 语义 |
|---|---|---|
| `name` | str | 精灵名（数据表主键） |
| `types` | list[str] | **恒为精灵自身系别**（克制/STAB 依据；血脉不改写） |
| `stats` | dict[str,int] | `calc_combat_stats` 输出（六维已算好） |
| `skills` | list[str] | 携带的技能名（1–4） |
| `nature` | str | 性格名 |
| `bloodline` | str | 血脉（FULL 下任意 18 系） |
| `iv` | dict[str,int] | 个体值（0–10，≤3 维） |
| `trait` | str | 图鉴特性名（引擎层再解析成实际装备，未实现 → 白板 `default`） |

**六维公式**（[statline.py:77](src/environment/statline.py#L77)）：
- 生命 `= (1.7 × (种族 + iv×3) + 70) × 性格修正 + 100`
- 其他五维 `= (1.1 × (种族 + iv×3) + 50) × 性格修正 + 50`
- 顺序：先向下取整 → 乘性格修正（升 +20% / 降 −10% / 其余 ×1.0）→ 加平值 → 整体取整。

---

## 2. 战斗过程数据协议

### 2.1 战斗信息 schema（回合间事实全集）

**核心不变式**：`BattleState` 只含回合间事实；回合内派生量（应对关系/减伤/出手顺序）一律住引擎局部 `TurnContext`，**绝不落状态**（跨回合泄漏结构上不可能）。

```python
BattleState:
    side_a: SideState | side_b: SideState
    rng: BattleRng(seed: int, calls: int)   # 只暴露 choice()；seed+calls 可还原随机流
    rules: BattleRules                      # frozen dataclass，全部数值常量
    turn: int = 1
    winner: str | None = None
    done: bool = False
    battle_id: str = ""

SideState:
    units: list[Unit]
    lives: int                              # 每阵亡一只 -1
    active: int                             # 当前在场单位下标
    item_uses: dict[str, int]               # 道具名 → 剩余次数
    revealed: set[tuple[int, str]]          # E4 迷雾：(队内下标, 技能名) 已释放集，对手视角可见

Unit:
    name: str | types: list[str] | stats: dict[str,int]   # 只读基线
    skills: list[Skill] | nature: str | bloodline: str | iv: dict[str,int]
    max_hp: int | current_hp: int | energy: int | fainted: bool
    stat_mods: list[StatModifier]           # 1 层 = 10%(pct) 或 +10(flat)；trait=True 免疫常规驱散
    energy_cost_mods: list[EnergyCostMod]   # 全技能能耗 -1/层
    trait: TraitState | None                # name 指向 traits.TRAIT_CATALOG 静态定义

BattleRules（关键字段）:
    team_size=3 | skill_slots=4 | iv_max=10
    lives=2 | energy_max=10 | energy_start=10 | recharge_amount=5 | max_turns=20
    switch_priority=99 | item_priority=99 | item_before_main_action=True
    stat_pct_per_layer=0.10 | stat_flat_per_layer=10 | stat_layer_cap=99
    damage_coefficient=0.9 | min_damage=1
```

**TurnContext**（[engine.py:35](src/environment/engine.py#L35)，回合内局部对象）：`decision_a/b`、`skill_a/b`、`category_a/b`、`reduction_a/b`。

### 2.2 状态转移：上一回合 → 下一回合

由 `BattleSession` 驱动（`submit → resolve → (阵亡?) submit_replacement → end_turn`），核心 `resolve_turn`（[engine.py:557](src/environment/engine.py#L557)）：

```
输入：state + decision_a + decision_b
 ① DECLARE   build_turn_context：读技能类别 → 防御方武装减伤（对手声明攻击才生效）
 ② ORDER     build_queue：priority 降序 → speed 降序 → 同方道具先 → 跨方平手抽硬币（唯一 RNG 点）
 ③ ACT       resolve_entry 分派：item / skill / switch / recharge
 ④ 阵亡      settle_faints → faint + life_loss →（有后备则暂停，等阵亡方玩家补位）
 ⑤ 收尾      end_turn：唯一 turn+=1 与 battle_end 发射；超时按 命数→血量百分比和→随机 定胜负
输出：事件流 + 新 state
```

**三条不变式**（重放/复现的地基）：
1. **纯转移**：输出只由 `(state.to_dict(), decision_a, decision_b)` 决定；
2. **玩家 RNG 流与引擎 RNG 流分离**（各持独立 `BattleRng`）；
3. 于是 `(提交序列 + seed)` 足以逐字节复现。

事件类型全集（`EVENT_TYPES`，[events.py:12](src/environment/events.py#L12)）：`damage / heal / item_use / stat_change / reduce_arm / recharge / switch / replace / faint / life_loss / skipped / battle_end / error / energy_gain / steal`。每条必带 `type` 与 `side`。

### 2.3 轨迹 schema（保存的战斗信息）

**引擎/自博弈轨迹**（`TrajectoryStore` + `run_selfplay`，只存"能重放的最小声学"）：

```json
{ "version": 1,
  "battle_id": "selfplay-7",
  "saved_at": "2026-08-28T08:00:00+00:00",
  "seed": 7,
  "players": {"a": "fake_llm", "b": "random"},
  "rules": {"team_size": 3, "lives": 2, "max_turns": 20, "...": "BattleRules 全字段"},
  "team_a": [ "roster spec..." ],
  "team_b": [ "roster spec..." ],
  "winner": "a",
  "done": true,
  "turns": [
    { "turn": 1,
      "decision_a": {"action": {"type": "skill", "value": 0}, "item": ""},
      "decision_b": {"action": {"type": "recharge"}, "item": ""},
      "replace_a": null, "replace_b": 0,
      "state_hash": "9f8e..." } ] }
```

要点：
- **不存整份状态**——只存 `(rules, 双方 roster spec, seed, 逐回合提交序列 + state_hash)`；
- `replace_a/b` 是补位选择，**重放的必需输入**；
- 重放 `replay_record`（[replay.py:27](src/environment/replay.py#L27)）：按配置重建全新 session → 逐回合重放 → 逐回合比对 `state_hash`，首个失配即停；
- 必需键契约在 `store.py` 与 R 线 `analysis.py` **双端校验**（改格式必须同步两端）。

**人机对战轨迹**（`BattleController.record()`，[battle.py:144](src/ui/battle.py#L144)）额外含 `events`（人类视角展示轨迹）；**已知缺口**：缺 `strategy`/`model`/`data_digest`（见 `docs/audit/04-findings.md` AUD-E-002）。

### 2.4 迷雾口径（信息边界）

- **观测**：`observe(state, viewer, "partial")` → `{me 全量, opponent 白名单}`；`mode="global"` = `state.to_dict()`。
- **敌方白名单**：精灵名 / 系别 / 血量百分比(`hp_pct`) / 能量 / 命数 / 特性描述 / 已揭示技能 / 增减益层数；**隐藏**：六维/性格/血脉/IV/绝对血量/道具次数/未揭示技能。
- **事件**：`filter_events_for(side, events, state)` 展示级变换（`damage.target_hp_left`→`target_hp_pct`、`heal` 去数值、`item_use` 去次数）；**fail-closed**：未登记事件类型丢弃并告警。
- **收口点**：`drive_turn`（match.py）对玩家统一喂 `view()` + 过滤后事件。⚠️ **人机对战 `BattleController` 未收口（AUD-E-001，P0 潜伏）**。

---

## 3. 与 R 线的接缝（本契约的消费方）

| E 线能力 | R 线消费方 | 说明 |
|---|---|---|
| `situation_key`/`v_heuristic`/`hits_to_ko`（`evaluate.py`，R0 语义但物理在 E 包） | analysis / valuefn / smc / build | 本分支已剥离（纯 E 世界） |
| 轨迹 record 契约 | `analysis.analyze_record` | 双端 `_RECORD_REQUIRED` 必须一致 |
| `run_match`/`drive_turn`（迷雾收口） | `run_selfplay` / 评测 | 玩家只见白名单 |
| `BattleSession.start`/`state.clone()` | 反事实 fork / SMC fork | `clone()` = `from_dict(to_dict())` |
| `Player` Protocol | 8 种 R 玩家实现 | 接口改动 = 全 R 断链 |
| `build_roster`/`validate_team`/`TeamPick` | D_sel/D_test 实例、R6 构筑 | 签名冻结 |

**改动必守三条不变式**（详见 `docs/audit/04-findings.md` 的冻结/可改清单）：
1. 确定性（同 seed 同提交序列逐字节可复现）；
2. 迷雾（新字段 → `view.py` 白名单 + `visibility.py` 过滤双登记）；
3. 轨迹契约（store ↔ analysis 必需键不漂移）。
