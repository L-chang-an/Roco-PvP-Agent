# E 线数据协议 v2（数据模型施工版）

> 状态：**已施工**（2026-08-29）　代码：`src/environment/models.py`
> 配套：`mydocs/battle_docs.md`（设计源头：数据协议 v2 §B–§F / 结算 v3 / 轨迹 v3）
> 定位：本文件是**数据模型层**的一手参考——每个数据模型的保存内容、每个字段的意义、具体样例。
> 旧版：`docs/e-line-data-contract.md`（v1，本次改造后需同步）。

---

## 0. 全景与设计原则

```
roster spec（组队，build_roster 产出）
    │  build_unit（唯一构造路径，吃 roster spec）
    ▼
BattleState ──► side_a / side_b ──► units ──► Unit（单只精灵）
   │  │  └─ weather（天气，全局）
   │  └─ rng / rules / turn / winner / done / battle_id
   ▼
SideState ──► lives / active / item_uses / revealed / marks（印记槽）
```

**三条设计原则**（来自 `mydocs/battle_docs.md` §A）：

1. **马尔可夫快照**：`S_{t+1} = F(S_t, D_a, D_b)`——下一回合所需的一切跨回合事实都在这份状态里；
   回合内派生量（应对关系 / 减伤 / 出手顺序 / 本回合队列）住引擎局部 `TurnContext`，**绝不落进 BattleState**。
2. **序列化完备**：新增状态字段必须进 `to_dict/from_dict`（`revealed` 的教训：漏一个字段，快照重放当场失配）。
3. **静态定义查表，运行时实例入状态**：技能五要素 / 特性定义 / 印记定义是静态数据；
   状态里存「技能详情（五要素）」与「运行时实例（层数 / 剩余回合 / source）」。

---

## 1. 组队数据协议（roster spec）

`build_roster(picks, source, rules)` 产出，`build_unit` 直接吃（不重算六维）。这是**数据层 ↔ 引擎的唯一一层缝**。

| 字段 | 类型 | 意义 |
|---|---|---|
| `name` | str | 精灵名（数据表主键） |
| `types` | list[str] | 精灵**自身**系别（克制 / STAB 依据；血脉不改写） |
| `base_stats` | dict[str,int] | **种族值**（六维原始值，`build_unit` 绑到 `Unit.base_stats`；`萌化`退化作用于它） |
| `stats` | dict[str,int] | `calc_combat_stats` 输出（六维已算好，只读基线） |
| `skills` | list[str] | 携带技能名（1–4，`build_unit` 按名补全五要素） |
| `nature` | str | 性格名（30 非中性 + 中性「坦率」） |
| `bloodline` | str | 血脉（FULL 下任意 18 系；不改写 types） |
| `iv` | dict[str,int] | 个体值（0–10，≤3 维） |
| `trait` | str | 图鉴特性名（`build_unit` 解析成实际装备：未实现 → 白板 `default`） |

**样例**（迪莫，FULL 源）：

```json
{ "name": "迪莫",
  "types": ["光"],
  "base_stats": {"hp": 120, "atk": 80, "sp_atk": 80, "def": 105, "sp_def": 105, "speed": 92},
  "stats": {"hp": 374, "atk": 148, "sp_atk": 148, "def": 175, "sp_def": 175, "speed": 161},
  "skills": ["闪光", "力量增效"],
  "nature": "坦率", "bloodline": "", "iv": {},
  "trait": "最好的伙伴" }
```

---

## 2. 战斗状态数据协议（BattleState 全树）

### 2.1 BattleState（一局对战的回合间事实全集）

| 字段 | 类型 | 意义 |
|---|---|---|
| `side_a` / `side_b` | SideState | 双方阵营状态（§2.2） |
| `rng` | `{seed, calls}` | 引擎随机流（只暴露 `choice()`；seed+calls 可还原随机流） |
| `rules` | BattleRules 全字段 | 一局全部数值常量（team_size / lives / energy_max / max_turns / …） |
| `turn` | int | 回合号（全库唯一 `end_turn` 推进） |
| `winner` | str \| null | 胜方（"a"/"b"/null） |
| `done` | bool | 是否终局 |
| `battle_id` | str | 轨迹标识 |
| `weather` | WeatherState \| null | **天气**（全局、双方共享，§2.8） |

### 2.2 SideState（一方阵营）

| 字段 | 类型 | 意义 |
|---|---|---|
| `units` | list[Unit] | 本阵营精灵（§2.3） |
| `lives` | int | 剩余命数（每阵亡一只 −1） |
| `active` | int | 当前在场下标（第零回合首发 = 组队序第一个） |
| `item_uses` | dict[str,int] | **道具栏**（道具名 → 剩余次数） |
| `revealed` | list[(int,str)] | **迷雾**：`(队内下标, 技能名)` 已释放集（对手视角可见） |
| `positive_marks` | list[MarkState] | **印记栏·正面**（普通空间，至多 1 个） |
| `negative_marks` | list[MarkState] | **印记栏·负面**（普通空间，至多 1 个） |
| `exclusive_marks` | list[MarkState] | **印记栏·独立空间**（里拉鳐「吟游之弦」：共存、不顶替） |

### 2.3 Unit（单只精灵）

| 字段 | 类型 | 意义 |
|---|---|---|
| `id` | str | **unit_id**：`"{side}-{槽位}-{精灵名}"`（如 `"a-0-迪莫"`），开战即定、永不漂移 |
| `name` | str | 精灵名 |
| `types` | list[str] | 系别 |
| `base_stats` | dict[str,int] | **种族值**（六维原始值） |
| `stats` | dict[str,int] | 当前六维基线（`calc_combat_stats` 输出，只读；聚合见 `aggregate_stats`） |
| `skills` | list[SkillInstance] | **初始携带技能详情**（整局基线；首领进化等永久变化改这里） |
| `current_skills` | list[SkillInstance] | **当前回合技能详情**（同五要素 + cooldown；结算一律读它） |
| `nature` / `bloodline` / `iv` | str / str / dict | 性格 / 血脉 / 个体值（组队层产物） |
| `max_hp` / `current_hp` | int | 血量（`apply_hp_loss` / `apply_heal` 唯一写者） |
| `energy` | int | 能量值 |
| `fainted` | bool | 阵亡标记 |
| `stat_mods` | list[StatModifier] | **状态栏·全部 buff 合一**（属性 / 能耗 / 连击 / 吸血 / 威力 / DOT / 纯负面，§2.5） |
| `trait` | TraitState \| null | **特性栏**（name + desc + kwargs + gains；未实现 → 白板 `default`，§2.6） |

### 2.4 SkillInstance（技能详情：五要素 + 冷却）

| 字段 | 类型 | 意义 |
|---|---|---|
| `name` | str | 技能名 |
| `desc` | str | **技能描述**（展示 / 提示词用，引擎不解析） |
| `type` | str | **技能系别**（克制 / STAB 依据） |
| `kind` | str | **技能类别**：物攻 / 魔攻 / 防御 / 状态 |
| `energy_cost` | int | **技能能耗** |
| `power` | int | **威力**（状态 / 防御技能为 0） |
| `cooldown` | int | 冷却剩余回合（0 = 不在冷却；**仅 `current_skills` 使用**） |

- `skills`（初始携带）：组队时从 FULL 表拷贝五要素，整局不变（除非首领进化等永久变化）；
- `current_skills`（当前回合）：从 `skills` 派生的当前生效视图，`cooldown` 表达**哪个技能在冷却、冷却几回合**；
  承载：① 防御技冷却 ② 愿力道具临时替换 1 号位（`skills` 保留原技能以便换回）；
- **防御冷却（2026-08-30 拍板机制，取代早期「TURN_END 递减」口径）**：使用防御技能 →
  该精灵**所有防御类技能** cd=1；递减在**回合结算入口**（仅在场精灵，先于本回合换人动作）；
  - 规则 1：被禁回合换下场 → 换回时冷却已减 1（被禁回合开始时在场，已经历冷却回合）；
  - 规则 2：释放防御技能的当回合即离场 → 不递减，回场后第一回合仍被禁；
  - 规则 3：阵亡补位入场的精灵若有冷却 → 立即减 1；
- **结算一律读 `current_skills`**（能耗支付 / 威力 / 类别 / 冷却门控以当前回合视图为准），效果按技能名查 P1∪P2∪MW∪ST 表。

### 2.5 StatModifier（通用 buff 记录：满足所有 Buff 类型）

| 字段 | 类型 | 意义 |
|---|---|---|
| `stat` | str | 维度：`atk/sp_atk/def/sp_def/speed`（六维）\| `combo`（连击）\| `lifesteal`（吸血）\| `attack_power`（攻击威力）\| `poison_attach`（附加中毒）\| `energy_cost`（全技能能耗）\| 纯负面中文名（`中毒/灼烧/寄生/冻结/引电/萌化`） |
| `mode` | str | `pct`（1层=10%）\| `flat`（1层=+10）\| `dot`（回合末结算）\| `special`（特殊规则） |
| `layers` | int | 层数（**有符号**：`energy_cost` 负层 = 能耗 −1，正层 = 能耗 +1） |
| `permanent` | bool | 离场是否保留（非永久 → 离场消失） |
| `source` | str | 来源标签（技能名 / 特性名；同源刷新与驱散区分） |
| `desc` | str | 描述文本（展示用） |
| `kwargs` | dict | 扩展参数（DOT 百分比 `{pct}`、冻结阈值、引电触发层数等） |
| `trait` | bool | 特性增益标记（免疫常规驱散） |

**状态栏三合一去向**（`energy_cost_mods` / `statuses` / `cooldowns` 已取消）：
- 能耗减益 → `stat="energy_cost", mode="flat", layers=-1`（读取 `base + Σ层` 夹到 0）；
- 纯负面 buff → `stat="中毒/…", mode="dot|special"` + kwargs（见下表）；
- 技能冷却 → **不进 stat_mods**，在 `current_skills[].cooldown`。

| stat | mode | 效果（回合末结算） | kwargs |
|---|---|---|---|
| 中毒 | dot | 造成 3% 生命毒系伤害（×层） | `{pct:3}` |
| 灼烧 | dot | 造成 2% 生命火系伤害（×层），且层数减半 | `{pct:2, halve:true}` |
| 寄生 | dot | 从寄生来源吸收 2% 生命（×层） | `{pct:2}` |
| 冻结 | special | 冻结 5% 生命，低于冻结比例则力竭（力竭判定已实现 2026-08-30） | `{pct:5}` |
| 引电 | special | 达 2 层 → 立即 25% 生命电系伤害并失去 2 层 | `{pct:25, at:2}` |
| 萌化 | special | 种族资质退化到上一阶，特性不变 | `{}` |

### 2.6 TraitState（特性运行时实例：特性增益转移至此）

| 字段 | 类型 | 意义 |
|---|---|---|
| `name` | str | 特性名（指向 `traits.TRAIT_CATALOG` 静态定义；未实现 → `default` 白板） |
| `desc` | str | **特性描述文本**（图鉴；进状态 → 敌方视角可直接展示） |
| `kwargs` | dict | 传参字典（条件 / 数值；once_per_battle 标记如 `{"used": true}`） |
| `gains` | list[StatModifier] | **本特性产生的增益层**（`trait=True`；离场清除 / 驱散隔离 / 属性聚合都从这读，不污染 `stat_mods`） |

> `used_once` 字段已取消：once_per_battle 状态改由 `kwargs` 表达。

### 2.7 MarkState（印记实例：阵营级）

| 字段 | 类型 | 意义 |
|---|---|---|
| `name` | str | 印记名（指向未来 `marks.py` 目录静态定义） |
| `layers` | int | 层数（可叠加；结算按「单效果 × 层数」） |
| `source` | str | 来源（技能名 / 特性名） |

### 2.8 WeatherState（天气：全局）

| 字段 | 类型 | 意义 |
|---|---|---|
| `kind` | str | 雨天 / 沙暴 / 暴风雪 / 雷鸣 |
| `turns_left` | int | 剩余回合（TURN_END 递减） |
| `source` | str | 来源 |

---

## 3. 决策信息（Decision）

| 字段 | 类型 | 意义 |
|---|---|---|
| `action` | `{type, value}` | 主动作：`skill` / `switch` / `recharge`（value = 槽位下标，skill/switch 用） |
| `item` | str | 道具名；`""` = 本回合不用（道具不占主动作，同回合生效） |

```json
{ "action": { "type": "skill", "value": 0 }, "item": "" }
```

> 阵亡后的**被动补位**（回合边界）也是决策：重放轨迹里单独成键 `replace_a / replace_b`（见轨迹记录 v3）。

---

## 4. 序列化与兼容性纪律

1. 新增字段一律进 `to_dict/from_dict`；`state_hash = sha256(json.dumps(to_dict, sort_keys))` 是马尔可夫不变式的可执行验证。
2. **旧快照容错**（`_unit_from_dict`）：
   - `skills` 旧字符串数组 → 按名从 FULL 表补成 SkillInstance；
   - `current_skills` 缺失 → 从 `skills` 派生；
   - `energy_cost_mods` → 并入 `stat_mods`（`stat="energy_cost", layers=-旧层`）；
   - `statuses` dict → 并入 `stat_mods`（`mode="dot"`）；
   - `base_stats` 缺失 → fallback 到 `stats`；`trait` 缺失 → None。
3. 静态定义查表（技能效果 / 特性 / 印记 / 天气），状态只存引用 + 运行时实例 → 升级效果不迁移历史状态。
4. **预估是派生量**（2026-08-30）：预估威力/预估伤害按需计算、不入状态（TURN_START 事件携带），
   `to_dict/from_dict` 不涉及。

---

## 5. 完整样例（3v3 真实对局快照）

以下为 `new_battle(迪莫/喵喵/火花 × 双方, seed=7)` 的 `to_dict()`（人工注入：迪莫带 2 层中毒、闪光冷却 1、A 方「攻击印记」、天气「雨天」，以便展示新字段）：

```json
{
  "side_a": { "units": [ {
      "id": "a-0-迪莫",
      "name": "迪莫",
      "types": ["光"],
      "base_stats": { "hp": 120, "atk": 80, "sp_atk": 80, "def": 105, "sp_def": 105, "speed": 92 },
      "stats": { "hp": 374, "atk": 148, "sp_atk": 148, "def": 175, "sp_def": 175, "speed": 161 },
      "skills": [
        { "name": "闪光", "desc": "对敌方精灵造成魔法伤害。", "type": "光", "kind": "魔攻", "energy_cost": 1, "power": 60, "cooldown": 0 },
        { "name": "力量增效", "desc": "自己获得物攻+100%。", "type": "普通", "kind": "状态", "energy_cost": 1, "power": 0, "cooldown": 0 } ],
      "current_skills": [
        { "name": "闪光", "desc": "对敌方精灵造成魔法伤害。", "type": "光", "kind": "魔攻", "energy_cost": 1, "power": 60, "cooldown": 1 },
        { "name": "力量增效", "desc": "自己获得物攻+100%。", "type": "普通", "kind": "状态", "energy_cost": 1, "power": 0, "cooldown": 0 } ],
      "nature": "坦率", "bloodline": "", "iv": {},
      "max_hp": 374, "current_hp": 374, "energy": 10, "fainted": false,
      "stat_mods": [ { "stat": "中毒", "mode": "dot", "layers": 2, "permanent": false,
                       "source": "毒刃", "desc": "", "kwargs": { "pct": 3 }, "trait": false } ],
      "trait": { "name": "最好的伙伴",
                 "desc": "造成克制伤害后，获得攻防速+20%，并回复2能量。",
                 "kwargs": {}, "gains": [] }
    }, { "id": "a-1-喵喵", "...": "同构" }, { "id": "a-2-火花", "...": "同构" } ],
    "lives": 2, "active": 0,
    "item_uses": { "草魔法": 1 },
    "revealed": [],
    "positive_marks": [ { "name": "攻击印记", "layers": 1, "source": "战歌" } ],
    "negative_marks": [], "exclusive_marks": [] },
  "side_b": { "...": "同构（unit_id 前缀 b-）" },
  "rng": { "seed": 7, "calls": 3 },
  "rules": { "team_size": 3, "lives": 2, "skill_slots": 4, "iv_max": 10,
             "energy_max": 10, "energy_start": 10, "recharge_amount": 5, "max_turns": 20,
             "switch_priority": 99, "item_priority": 99, "item_before_main_action": true,
             "stat_pct_per_layer": 0.1, "stat_flat_per_layer": 10, "stat_layer_cap": 99,
             "damage_coefficient": 0.9, "min_damage": 1 },
  "turn": 1, "winner": null, "done": false, "battle_id": "dc-1",
  "weather": { "kind": "雨天", "turns_left": 2, "source": "祈雨" }
}
```

---

## 6. 施工变更记录（2026-08-29）

| 变更 | 落点 |
|---|---|
| Unit 新增 `id`（unit_id `{side}-{槽位}-{名}`）、`base_stats`（种族值）、`current_skills`（+cooldown） | models.py Unit / new_battle / build_unit |
| 技能栏详情化：`skills` 从 list[str] → list[SkillInstance]（五要素） | models.py SkillInstance / build_unit |
| 状态栏三合一：`energy_cost_mods`/`statuses`/`cooldowns` 取消，全部进 `stat_mods`（能耗 = `stat="energy_cost"` 负层；DOT = `mode="dot|special"`） | models.py StatModifier / primitives.py / engine.py |
| 特性增益转移 `TraitState.gains`（取消 `used_once`，加 `desc`/`kwargs`）；`aggregate_stats`/`buff_layers` 读 stat_mods + trait.gains | models.py / hooks.py / traits.py |
| SideState 新增印记槽 `positive/negative/exclusive_marks`；BattleState 新增 `weather` | models.py MarkState / WeatherState |
| roster spec 新增 `base_stats`；build_roster 输出种族值 | teambuilder.py |
| 引擎从 `current_skills` 构建 combat Skill（effect 按名查 P1∪P2） | engine.py `_combat_skill` |
| 旧快照容错（energy_cost_mods / statuses / v1 字符串 skills） | models.py `_unit_from_dict` |
| 公式口径统一：`_STAT_GROWTH_BASE=10`（docstring 由 50 改为 10） | statline.py（⚠️ 待负责人最终确认） |
| **伤害公式规范（2026-08-30 拍板）**：唯一公式 `damage.formula`——顺序求值出口 int() 一次；flat 层留属性、pct 层进比值项；attack_power 激活（flat→威力绝对值、pct→威力百分比）；应对倍率先乘后加；天气项落位（恒 1.0） | damage.py / modifiers.py / reducer.py |
| **预估体系（2026-08-30）**：预估威力/预估伤害纯函数；view me 侧 `predictions` 提示；预估是**派生量不进状态**（不入 to_dict/from_dict，state_hash 不变） | prediction.py / view.py |
| **反应管道 + 回合边界事件（2026-08-30）**：`pipeline.run` fixpoint 循环（Frame.domain_events 回传）；TurnStarted（携预估）/ TurnEnded | pipeline.py / domain.py / engine.py |
| **印记与天气效果层（2026-08-30）**：MarkState/WeatherState 字段**零变更**（全复用 v2 落地字段）；marks.py/weather.py 效果目录 + 读钩子接线；印记槽规则（同种叠加/异种顶替/独立空间） | marks.py / weather.py / primitives.py |
| **印记/天气技能入口（2026-08-30）**：MW_EFFECTS 白名单 24 条（P1∪P2∪MW = 203）；valid_skills.json 重新生成（`scripts/build_valid_skills.py`，check 口径改动态） | skillbook.py / scripts/ |
| **DOT 结算（2026-08-30 拍板）**：六状态层施加与结算（statuses.py 单一事实源）；属性免疫（火免疫灼烧/草免疫寄生/毒免疫中毒，施加层拦截，**中毒印记不受影响**）；灼烧（火）/中毒（毒）/引电（电）伤害吃属性克制、寄生真实伤害吸血；灼烧减半向下取整归零移除；引电达 2 层即时 25% 扣 2 留余 | statuses.py / reducer.py |
| **DOT 技能入口（2026-08-30）**：ST_EFFECTS 白名单 14 条（P1∪P2∪MW∪ST = 217）；valid_skills.json 重新生成 | skillbook.py / scripts/ |
| **冻结力竭判定（2026-08-30 拍板）**：血量低于冻结层数×5% → 力竭阵亡（非伤害）；`Faint` 原子 + `damage.apply_faint`（current_hp 唯一写点纪律）；statuses.collect 监听 DamageApplied / HpChanged / StatModChanged(冻结)；严格小于才力竭 | statuses.py / atom.py / reducer.py / damage.py |

**待负责人确认**：① roster spec 是否携带 `base_stats` 由数据源版本锁定（`data_digest` 属轨迹层，不在本文件）。
