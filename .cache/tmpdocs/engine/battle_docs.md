## 回合状态信息
每个阵营都有一个战斗回合状态信息：

|阵营A|阵营B|
|-|-|
|A.a（A的宠物a状态信息）|B.a（B的宠物a状态信息）|
|A.b|B.b|
|A.c|B.c|
|A方印记|B方印记|
|A方道具|B方道具|
|天气系统||

每只宠物的状态信息包括：

|A.a|特性栏|
|-|-|
||技能栏|
||状态栏|
||能量值|
||六维属性|



# PVP回合对战逻辑
## 战斗前
精灵组队

技能搭配

精灵性格、努力值、血脉调整

战斗规格：3V3/2命~6V6/4命

## 战斗时
### 第零回合
首发精灵选取

### 回合结算逻辑
双方玩家全都提交决策后，战斗引擎会根据上一回合战斗状态信息+双方玩家的决策动作来结算下一回合的战斗状态信息

一般结算时按三个时段来分别建立原子动作队列：回合开始前、回合进行时、回合结束后。这里的回合指的是双方精灵释放技能。

一般在回合开始前的队列的有：道具使用（目前来说是这样，以后的道具可能），一些特性的结算可能会在回合开始前

回合进行时的队列就是双方的技能释放；

回合结束时的队列：中毒、灼烧、寄生以及天气等结算

### 战斗时机/事件
hook机制，比如什么时候触发什么效果

### 迷雾视角
战斗双方各自能看到的战斗信息

完全无法获取敌方的信息有：精灵血脉、携带的道具、精灵属性、精灵性格、精灵个体值

可以随着战斗过程中而揭示的信息有：敌方精灵技能信息

## 战斗后
胜利结算、回合上限结算、轨迹保存等。

# 战斗系统引擎
## 动作系统
对应回合战斗状态信息中的动作栏

攻击：连击、非连击、伤害计算

状态：引出buff\天气\印记系统

防御：必定应对攻击，回合级减伤，使用后至少需要冷却一回合

## 印记系统
对应回合战斗状态信息中的印记栏

【印记】：常驻在战场上的效果，**每一方（我方/敌方）同时只能存在1种正面印记和1种负面印记**，新印记会覆盖同类型旧印记，双方阵营印记独立计算。

### 正面印记
湿润印记：全技能能耗-1。
龙噬印记：释放3能耗技能时，获得双攻+30%，可叠加。
蓄势印记：全攻击技能威力+30%，且能耗+1。可叠加。
风起印记：先手攻击时，本次技能威力+20%。
蓄电印记：攻击技能获得迸发：本次威力+10。
光合印记：回合结束时获得1能量。
攻击印记：全技能威力+10%。
萌芽印记：获得增益时，会额外获得1层。

### 负面印记
减速印记：速度-10。
降灵印记：入场时失去1能量。
星陨印记：使用非幻系技能攻击拥有此印记的精灵时，会触发星陨印记，消耗全部星陨层数，对其造成额外的幻系伤害。可叠加
中毒印记：回合结束造成3%生命的毒系伤害，可叠加。
棘刺印记：入场时失去6%生命。
暗涌印记：持有印记的精灵离场后，更换入场的精灵获得随机5层属性减益。

## buff系统
对应回合战斗状态信息中的状态栏

5维属性buff：攻防 10% 为 1层，速度 10 为 1层

技能相关：能耗 -1 为1层，攻击技能威力 10 为 1层，连击数 +1 为 1层

纯负面buff：中毒、灼烧、萌化、冰冻、寄生、引雷

|中毒|回合结束时，造成3%生命的毒系伤害|
|-|-|
|灼烧|回合结束时，造成2%生命的火系伤害并且层数减半|
|寄生|回合结束时，从寄生来源吸收2%生命|
|冻结|冻结5%生命，若当前生命低于冻结比例，则力竭|
|引电|获得2层引电时，立即受到25%生命的电系伤害，并失去2层引电|
|萌化|种族资质退化到上一阶，但是特性不变|

纯正面buff：吸血 10% 为 1层；附加中毒 1 为1层；

其他类型（不算buff类，但是是buff的某种派生）：下次攻击翻倍/能耗-x/威力+x，一般就一层，特殊技能特殊对待。

## 特性系统
对应回合战斗状态信息中的特性栏

## 天气系统
对应回合战斗状态信息中的天气栏

|暴风雪|回合结束时，双方精灵各获得两层冻结|
|-|-|
|雨天|双方的水系技能威力+75%|
|沙暴|双方地系技能的能耗减半|
|雷鸣|为双方精灵各增加一层引电|

## 道具系统
对应回合战斗状态信息中的道具栏

当前已有的道具：

|草魔法|使当前场上精灵直接恢复50%生命值|
|-|-|
|愿力|使当前场上精灵的一号位技能替换成愿力冲击，下一回合换回原技能|
|首领进化|如果当前场上精灵为首领血脉精灵，则可以使用该道具使该精灵进化成其对应的首领精灵，特性和种族值资质也相应变化成首领精灵的。|

---

# 每回合战斗信息数据格式（E0b 重建 · 数据协议 v2 设计）

> 状态：**设计稿**（未实现，等负责人审阅拍板）　日期：2026-08-29
> 依据：本文档上文「回合状态信息 / PVP回合对战逻辑 / 战斗系统引擎」的愿景
> 对照现状：`src/environment/models.py` 的 `BattleState/SideState/Unit`（E0b 已落地核心）、
> `mydocs/mark-system-plan.md`（印记）、`mydocs/refactor-atomic-action-queue.md`（原子队列）
> 核心不变式：**马尔可夫快照** —— 下一回合状态只由（上一回合状态 + 双方决策）决定

## A. 设计原则（先立规矩，再定格式）

### A.1 马尔可夫性质 = 状态快照完备性

「上一回合战斗状态信息 + 玩家决策 → 引擎推出下一回合战斗状态信息」要成立，
等价于：**下一回合结算所需的一切跨回合事实都在这份状态里；回合内临时量一个都不许进状态**。

```
输入  S_t（回合 t 状态快照） + D_a（A 方决策） + D_b（B 方决策）
输出  S_{t+1}（新快照） + E（事件流）
S_{t+1} 完全由 (S_t, D_a, D_b) 决定      ← 无隐藏状态 / 无外部输入 / 无时间依赖
state_hash(S_t) 逐字节可复现             ← sha256(json.dumps(to_dict, sort_keys))
```

### A.2 四道闸门（新增任何状态字段都必须过）

1. **跨回合事实才进状态**：回合内派生量（应对关系 / 减伤比例 / 出手顺序 / 本回合队列）住引擎局部
   `TurnContext`（未来是 `TurnExecution`），回合结束自动消亡——状态里出现回合内字段 = 设计事故；
2. **序列化完备**：状态字段必须进 `to_dict/from_dict`（`revealed` 的教训：漏一个字段，快照重放当场失配）；
3. **确定性**：引擎 RNG 只暴露 `choice()`，状态只存 `{seed, calls}` 即可还原随机流；玩家 RNG 与引擎 RNG 分离；
4. **迷雾是视角不是状态**：状态存**全量**事实；玩家看到什么由 `view.py` 白名单 + `visibility.py` 事件过滤派生。
   唯一例外 `revealed`（技能已被揭示 = 对局事实，必须进状态）。

### A.3 静态数据：效果定义查表，技能详情入状态（2026-08-29 修订）

技能 / 特性 / 印记 / 天气的**效果定义**仍是静态数据（查表），状态只存运行时实例状态。
**修订**：技能**详情**（desc / type / kind / energy_cost / power）内嵌进 `Unit.skills` /
`Unit.current_skills`（负责人拍板——为支持愿力替换、萌化退化、冷却标记；见 §D）。
好处不变：效果定义查表，新增技能效果不迁移历史状态；技能详情随快照往返、无需查表即自足。

## B. 顶层 BattleState（一局对战的回合间事实全集）

对应上文「回合状态信息」总表：双方阵营 + 天气系统。

```json
{
  "battle_id": "selfplay-7",
  "turn": 1,
  "winner": null,
  "done": false,
  "rules": { "team_size": 3, "lives": 2, "energy_max": 10, "...": "BattleRules 全字段" },
  "rng": { "seed": 7, "calls": 0 },
  "weather": null,
  "side_a": { "...": "见 §C" },
  "side_b": { "...": "见 §C" }
}
```

| 字段 | 类型 | 落位状态 | 对应愿景 |
|---|---|---|---|
| battle_id | str | 【已落地】 | 轨迹标识 |
| turn | int | 【已落地】 | 回合号（全库唯一 `end_turn` 推进） |
| winner / done | str? / bool | 【已落地】 | 终局判定 |
| rules | BattleRules | 【已落地】 | 战斗规格（team_size / lives / 能量 / 回合上限…，见 §F.5） |
| rng | {seed, calls} | 【已落地】 | 引擎随机流（同 seed+calls 可还原） |
| **weather** | WeatherState \| null | 【设计预留】 | **天气系统**（全局、双方共享，见 §F.4） |
| side_a / side_b | SideState | 【已落地】 | A/B 阵营（精灵 / 印记 / 道具，见 §C） |

## C. SideState（一方阵营持久状态）

对应上文「回合状态信息」的每一行（A.a / A.b / A.c + A方印记 + A方道具）。

```json
{
  "lives": 2,
  "active": 0,
  "item_uses": { "草魔法": 1 },
  "revealed": [ [0, "闪光"] ],
  "positive_marks": [],
  "negative_marks": [],
  "exclusive_marks": [],
  "units": [ { "...": "见 §D" } ]
}
```

| 字段 | 类型 | 落位状态 | 对应愿景 |
|---|---|---|---|
| units | list[Unit] | 【已落地】 | 本阵营精灵（A.a / A.b / A.c…） |
| lives | int | 【已落地】 | 本阵营剩余命数 |
| active | int | 【已落地】 | 当前在场下标（第零回合首发 = 组队序第一个，active=0） |
| item_uses | dict[str, int] | 【已落地】 | **道具栏**（道具名 → 剩余次数） |
| revealed | set[(int,str)] | 【已落地】 | 迷雾：已释放技能（对手视角可见），对局事实 |
| **positive_marks / negative_marks** | list[MarkState] | 【设计预留】 | **印记栏·普通空间**（正面 / 负面各至多 1 个，见 §F.3） |
| **exclusive_marks** | list[MarkState] | 【设计预留】 | 印记栏·独立空间（里拉鳐「吟游之弦」：共存、不顶替，见 §F.3） |

## D. Unit（单只精灵）

对应上文「每只宠物的状态信息」：特性栏 / 技能栏 / 状态栏 / 能量值 / 六维属性。
**2026-08-29 拍板改造**：① 新增种族值 `base_stats`；② 技能栏详情化（`skills` 初始携带 /
`current_skills` 当前回合，五要素 + 冷却）；③ 状态栏三合一（`energy_cost_mods`/`statuses`/
`cooldowns` 取消，全部进 `stat_mods`）；④ 特性增益转移到 `TraitState`（取消 `used_once`）。

```json
{
  "name": "迪莫",
  "types": ["光"],
  "base_stats": { "hp": 85, "atk": 60, "sp_atk": 60, "def": 50, "sp_def": 50, "speed": 48 },
  "stats": { "hp": 390, "atk": 210, "sp_atk": 210, "def": 180, "sp_def": 180, "speed": 170 },
  "skills": [
    { "name": "闪光", "desc": "对敌方精灵造成魔法伤害。", "type": "光",
      "kind": "魔攻", "energy_cost": 2, "power": 65 },
    { "name": "力量增效", "desc": "自己获得物攻+140%。", "type": "普通",
      "kind": "状态", "energy_cost": 0, "power": 0 }
  ],
  "current_skills": [
    { "name": "闪光", "desc": "…", "type": "光", "kind": "魔攻", "energy_cost": 2, "power": 65,
      "cooldown": 0 },
    { "name": "力量增效", "desc": "…", "type": "普通", "kind": "状态", "energy_cost": 0, "power": 0,
      "cooldown": 2 }
  ],
  "nature": "坦率",
  "bloodline": "",
  "iv": {},
  "max_hp": 390,
  "current_hp": 300,
  "energy": 8,
  "fainted": false,
  "stat_mods": [
    { "stat": "atk", "mode": "pct", "layers": 2, "permanent": false, "source": "力量增效" },
    { "stat": "中毒", "mode": "dot", "layers": 3, "permanent": false,
      "source": "毒刃", "kwargs": { "pct": 3 } },
    { "stat": "energy_cost", "mode": "flat", "layers": -1, "permanent": false, "source": "浸润" }
  ],
  "trait": { "name": "最好的伙伴",
             "desc": "造成克制伤害后，获得攻防速+20%，并回复2能量。",
             "kwargs": { "cond": "dealt_counter", "stat_layers": 2, "energy_gain": 2 },
             "gains": [] }
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| name / types | str / list[str] | 精灵名（数据表主键）/ 系别 |
| **base_stats** | dict[str,int] | **种族值**（新增：六维原始值；`萌化`资质退化作用于它） |
| stats | dict[str,int] | 当前六维（calc_combat_stats 基线，只读；聚合结果见 aggregate_stats） |
| **skills** | list[SkillInstance] | **初始携带技能详情**（整局基线；首领进化等永久变化改这里） |
| **current_skills** | list[SkillInstance] | **当前回合技能详情**（同五要素 + cooldown，见 §D.1） |
| nature / bloodline / iv | … | 性格 / 血脉 / 个体值（组队层产物） |
| max_hp / current_hp | int | 血量（apply_hp_loss / apply_heal 唯一写者） |
| energy | int | 能量值 |
| fainted | bool | 阵亡标记 |
| **stat_mods** | list[StatModifier] | **状态栏·全部 buff 合一**（属性/能耗/连击/吸血/威力/DOT/纯负面，见 §D.2） |
| **trait** | TraitState \| null | **特性栏**（name+desc+kwargs+gains；未实现 → default 白板，见 §D.3） |

### D.1 SkillInstance（技能详情：五要素 + 冷却）

```python
@dataclass
class SkillInstance:
    name: str
    desc: str          # 技能描述
    type: str          # 技能系别（克制/STAB 依据）
    kind: str          # 技能类别：物攻 / 魔攻 / 防御 / 状态
    energy_cost: int   # 技能能耗
    power: int         # 威力
    cooldown: int = 0  # 冷却剩余回合（0 = 不在冷却；**仅 current_skills 使用**）
```

- **skills（初始携带）**：组队时从 FULL 表拷贝五要素，整局不变（除非永久形态变化如首领进化）；
- **current_skills（当前回合）**：从 skills 派生的当前生效视图，`cooldown` 字段表达**哪个技能在冷却、
  冷却几回合**（值 = 剩余回合，0 = 未冷却）；承载：① 防御技冷却（TURN_END 递减）；② 愿力道具
  临时替换 1 号位（skills 保留原技能以便换回）；③ 未来技能形态变化；
- **结算一律读 current_skills**（能耗支付 / 威力 / 类别以当前回合视图为准）；`skills` 作为恢复基线。

### D.2 StatModifier（扩展后：满足所有 Buff 类型）

```python
@dataclass
class StatModifier:
    """通用 buff 记录：五维增减益 / 技能相关 / 纯负面 buff 全部统一到 stat_mods。"""
    stat: str          # atk/sp_atk/def/sp_def/speed | combo | lifesteal | attack_power |
                       # energy_cost | poison_attach | 中毒 | 灼烧 | 寄生 | 冻结 | 引电 | 萌化 | …
    mode: str          # pct（1层=10%）| flat（1层=+10）| dot（回合末结算）| special（特殊规则）
    layers: int        # 层数（有符号：energy_cost 负层 = 能耗−1）
    permanent: bool = False   # 离场是否保留
    source: str = ""         # 来源（技能名 / 特性名）
    desc: str = ""           # 描述文本（展示用）
    kwargs: dict = {}        # 扩展参数（DOT 百分比 / 冻结阈值 / 引电触发层数等）
```

**三合一去向**（`energy_cost_mods` / `statuses` / `cooldowns` 取消）：
- 能耗减益 → `stat=energy_cost, mode=flat, layers=-1`（读取 `base + Σ层` 夹到 0）；
- 纯负面 buff → `stat=中毒/…, mode=dot|special` + kwargs（见 §F.2）；
- 技能冷却 → **不进 stat_mods**，在 `current_skills[].cooldown`（用户指定落位）。

### D.3 TraitState（特性运行时实例：特性增益转移到此）

```python
@dataclass
class TraitState:
    """特性运行时实例。特性产生的增益由本模型维护，不写进 Unit.stat_mods。"""
    name: str
    desc: str = ""                    # 特性描述文本
    kwargs: dict = {}                 # 传参字典（条件/数值；once_per_battle 标记如 {"used": true}）
    gains: list[StatModifier] = []    # 本特性产生的增益层（复用 StatModifier；聚合时并入）
```

- **`used_once` 字段取消**：once_per_battle 状态改由 `kwargs` 表达（如 `{"used": true}`）；
- **特性增益全转移**：助燃双攻+20%、最好的伙伴攻防速+20%+回能 等特性产生的增益挂 `gains`，
  离场清除 / 驱散隔离 / 属性聚合（aggregate_stats 并入）都从 `TraitState.gains` 读，不再污染 stat_mods；
- `desc` 进状态 → 敌方视角可直接展示（迷雾白名单可查）；`kwargs` 供效果原语取参。

## E. 决策信息格式（Decision + 补位）

对应上文「回合结算逻辑」的「双方玩家的决策动作」。一回合每方一条。

```json
{ "action": { "type": "skill", "value": 0 }, "item": "" }
```

- `action.type` ∈ {`skill`, `switch`, `recharge`}；`action.value` = 槽位下标（skill / switch 用）；
- `item` = 道具名；`""` = 本回合不用（道具**不占主动作**，与主动作同回合生效、各自入队）；
- 阵亡后的**被动补位**（回合边界，见 v3 §9）也是决策：重放轨迹里单独成键 `replace_a / replace_b`（重放的必需输入）。

## F. 新系统落位（愿景 → 字段）

### F.1 buff 系统（状态栏合一：全部进 `stat_mods`，统一「层数」记账）

**2026-08-29 拍板：`energy_cost_mods` / `statuses` / `cooldowns` 三字段取消；能耗减益并入 stat_mods，
DOT 并入 stat_mods，冷却挂 current_skills。**

| 愿景条目（上文「buff系统」） | 落位（stat / mode） | 1 层单位 |
|---|---|---|
| 攻防 buff | stat=atk/sp_atk/def/sp_def/speed, mode=pct | +10% |
| 速度 buff | stat=speed, mode=flat | +10 |
| 全技能能耗 −1 | stat=energy_cost, mode=flat, **layers=1** | −1 能耗 |
| 连击数 +1 | stat=combo, mode=flat | +1 连击 |
| 吸血 +X | stat=lifesteal, mode=pct | +10% 吸血 |
| 攻击技能威力 +X | stat=attack_power, mode=flat | +10 威力 |
| 附加中毒 +1 | stat=poison_attach, mode=flat | +1 层（攻击附带中毒） |
| 下次攻击翻倍 / 能耗-x / 威力+x（一次性派生） | stat_mods 特殊 stat, mode=special | 1 层 |

**特性增益例外**：由特性产生的增益不写 stat_mods，进 `trait.gains`（见 §D.3）——属性聚合时并入。

### F.2 纯负面 buff + 防御冷却（并入 `stat_mods` + `current_skills`）

上文「纯负面 buff」六种，**2026-08-29 拍板并入 `stat_mods`**（不再单独 `statuses`）：
以 `stat=种类` + `mode=dot|special` + `kwargs` 携带参数表达；结算逻辑由引擎按 mode 分派
（dot → TURN_END 结算；special → 各自触发时机）。

```json
{ "stat": "中毒", "mode": "dot", "layers": 3, "permanent": false,
  "source": "毒刃", "kwargs": { "pct": 3 } }
```

| stat | mode | 效果（上文定义） | kwargs | 触发时机 |
|---|---|---|---|---|
| 中毒 | dot | 造成 3% 生命毒系伤害（×层） | {pct:3} | 回合结束 |
| 灼烧 | dot | 造成 2% 生命火系伤害（×层），**且层数减半** | {pct:2, halve:true} | 回合结束 |
| 寄生 | dot | 从寄生来源吸收 2% 生命（×层） | {pct:2} | 回合结束 |
| 冻结 | special | 冻结 5% 生命，低于冻结比例则力竭 | {pct:5} | 受击 / 结算判定 |
| 引电 | special | 达 2 层 → 立即 25% 生命电系伤害并失去 2 层 | {pct:25, at:2} | 获得层数即时 |
| 萌化 | special | 种族资质退化到上一阶，特性不变 | {} | 常驻（资质修正） |

**防御冷却**：`cooldowns` 字段取消，改挂 `current_skills[].cooldown`（见 §D.1）——
哪个技能在冷却（该技能详情的 cooldown>0）、冷却几回合（值 = 剩余回合），TURN_END 统一递减。

### F.3 印记系统（`positive_marks / negative_marks / exclusive_marks`，设计预留）

上文「印记系统」：**每一方同时只能存在 1 种正面 + 1 种负面印记，新印记覆盖同类型旧印记，双方独立** →
阵营级、按极性各一槽（与 `mark-system-plan.md` §4 一致；`exclusive_marks` 为里拉鳐「吟游之弦」独立空间，
共存不顶替、仅全量驱散可移除）。

```json
{ "name": "攻击印记", "layers": 1, "source": "战歌" }
```

效果定义挂在未来 `marks.py` 目录（仿 `traits.py`），数据只存 `{name, layers, source}`。
正面（湿润 / 龙噬 / 蓄势 / 风起 / 蓄电 / 光合 / 攻击 / 萌芽）、负面（减速 / 降灵 / 星陨 / 中毒 / 棘刺 / 暗涌）。

### F.4 天气系统（`weather`，设计预留）

上文「天气系统」是**全局**（不分阵营）→ 挂在 `BattleState` 顶层，双方共享。

```json
{ "kind": "雨天", "turns_left": 3, "source": "祈雨" }
```

| kind | 效果（上文定义） | hook 落位 |
|---|---|---|
| 暴风雪 | 回合结束时双方各 +2 冻结层 | TURN_END |
| 雨天 | 双方水系技能威力 +75% | ATTACK_POWER |
| 沙暴 | 双方地系技能能耗减半 | SKILL_COST |
| 雷鸣 | 为双方精灵各 +1 引电 | TURN_END |

### F.5 战斗规格

上文「战斗规格：3V3/2命~6V6/4命」→ `BattleRules.team_size / lives` 承载。
**已拍板（2026-08-29）：team_size 只允许 {3, 6}**（3V3 / 6V6），lives ∈ [1, team_size)。数据格式不关心。

## G. 回合转移契约（三时段队列与数据格式的关系）

上文「回合结算逻辑」的「三个时段原子动作队列」是**引擎内部结算流程**（局部 `TurnContext` / 未来
`TurnExecution`，见 `refactor-atomic-action-queue.md`），**不是状态字段**。数据格式的职责：
保证「回合结束后」时段结算所需的事实**都在快照里** —— 于是 `F` 是纯函数，马尔可夫性成立。

```
DECLARE（读双方决策，定应对关系 / 武装减伤）            → TurnContext（局部）
  ├─ 回合进行中：双方根动作（skill/switch/recharge/item） → priority → speed → 硬币（唯一 RNG）
  │     阵亡 → 该方场视为空，本回合对空场操作跳过（回合不中断）
  ├─ 回合结束后：stat_mods 的 dot/special 结算（中毒/灼烧/寄生…）/ 天气 / 印记 TURN_END
  │            / current_skills[].cooldown 递减 → end_of_turn()
  ├─ 下一回合前状态计算 + 若阵亡 → 被动补位（回合边界，见 v3 §9）
  └─ 终局：命归零 / 无存活 → battle_end
输出 = 新状态快照（S_{t+1}）+ 事件流
```

「回合结束后」所需的状态字段 = `stat_mods` 的 dot/special 记录（中毒 / 灼烧 / 寄生…）、
`current_skills[].cooldown`（冷却递减）、`weather`（暴风雪 / 雷鸣）、`marks`（光合 / 中毒印记）——
**全部在快照里**，这就是马尔可夫性的物质基础。

## H. 序列化与兼容性纪律

1. 新增字段一律进 `to_dict/from_dict`（`revealed` 的教训：漏一个字段，快照重放当场失配）；
2. **2026-08-29 Unit 改造**：新增 `base_stats` / `current_skills` / `trait.desc` / `trait.kwargs` /
   `trait.gains`；`skills` 从字符串数组改详情对象（旧快照容错：字符串按名从 FULL 表补详情）；
   **移除** `energy_cost_mods` / `statuses` / `cooldowns` / `trait.used_once`；
3. 新字段容错：`from_dict` 用 `.get(k, 默认)`（base_stats→stats、current_skills→按 skills 派生、
   trait.kwargs→{}、trait.gains→[]）；
4. 技能 / 特性 / 印记 / 天气**效果定义**仍查表（技能详情入状态，效果定义不入），升级效果不迁移历史状态；
5. `state_hash` = sha256(to_dict, sort_keys) 是马尔可夫不变式的可执行验证（`step` / `replay_record`）。

## I. 完整示例（3v3 第二回合开始前的快照）

```json
{
  "battle_id": "e0b-demo",
  "turn": 2,
  "winner": null,
  "done": false,
  "rules": { "team_size": 3, "lives": 2, "skill_slots": 4, "iv_max": 10,
             "energy_max": 10, "energy_start": 10, "recharge_amount": 5, "max_turns": 20,
             "switch_priority": 99, "item_priority": 99, "item_before_main_action": true,
             "stat_pct_per_layer": 0.1, "stat_flat_per_layer": 10, "stat_layer_cap": 99,
             "damage_coefficient": 0.9, "min_damage": 1 },
  "rng": { "seed": 7, "calls": 0 },
  "weather": { "kind": "雨天", "turns_left": 2, "source": "祈雨" },
  "side_a": {
    "lives": 2, "active": 0,
    "item_uses": { "草魔法": 0 },
    "revealed": [ [0, "闪光"] ],
    "positive_marks": [ { "name": "攻击印记", "layers": 1, "source": "战歌" } ],
    "negative_marks": [],
    "exclusive_marks": [],
    "units": [
      { "name": "迪莫", "types": ["光"],
        "base_stats": { "hp": 85, "atk": 60, "sp_atk": 60, "def": 50, "sp_def": 50, "speed": 48 },
        "stats": { "hp": 390, "atk": 210, "sp_atk": 210, "def": 180, "sp_def": 180, "speed": 170 },
        "skills": [
          { "name": "闪光", "desc": "对敌方精灵造成魔法伤害。", "type": "光", "kind": "魔攻",
            "energy_cost": 2, "power": 65 },
          { "name": "力量增效", "desc": "自己获得物攻+140%。", "type": "普通", "kind": "状态",
            "energy_cost": 0, "power": 0 } ],
        "current_skills": [
          { "name": "闪光", "desc": "…", "type": "光", "kind": "魔攻", "energy_cost": 2, "power": 65,
            "cooldown": 0 },
          { "name": "力量增效", "desc": "…", "type": "普通", "kind": "状态", "energy_cost": 0, "power": 0,
            "cooldown": 2 } ],
        "nature": "坦率", "bloodline": "", "iv": {},
        "max_hp": 390, "current_hp": 300, "energy": 8, "fainted": false,
        "stat_mods": [
          { "stat": "中毒", "mode": "dot", "layers": 2, "permanent": false,
            "source": "毒刃", "kwargs": { "pct": 3 } } ],
        "trait": { "name": "最好的伙伴",
                   "desc": "造成克制伤害后，获得攻防速+20%，并回复2能量。",
                   "kwargs": { "cond": "dealt_counter", "stat_layers": 2, "energy_gain": 2 },
                   "gains": [] } }
    ]
  },
  "side_b": { "lives": 2, "active": 0, "item_uses": { "草魔法": 1 }, "revealed": [],
               "positive_marks": [], "negative_marks": [], "exclusive_marks": [], "units": [] }
}
```

## J. 下一步（实现顺序建议，待负责人拍板）

1. **纯负面 buff 结算**（stat_mods 的 dot/special，先做 DOT 三件套：中毒 / 灼烧 / 寄生）；
2. **天气 `weather`**（雨天 / 沙暴先做 —— hook 已有 ATTACK_POWER / SKILL_COST 时机）；
3. **印记 `marks`**（正负槽 + 覆盖规则 + 首批 2–3 种，按 `mark-system-plan.md` S2/S3）；
4. **防御冷却**（`current_skills[].cooldown` 递减）；
5. **愿力 / 首领进化道具**（涉及 Unit 技能临时替换与基线变更，最后做）。

---

# 战斗引擎结算代码框架设计（E0b 重建 · 结算架构 v2）—— ⚠️ 已被 v3 取代

> ⚠️ 2026-08-29：本方案（原子动作队列 + 万能 Hook）已被下文「回合结算逻辑 v3 设计」取代，
> 保留作演进记录。v3 沿用其三个有效内核：三时段保留为生命周期边界、统一效果架构（声明→调度→原语）、
> 迁移哨兵思想（digest 不变）。
> 状态：**已归档**（原设计稿）
> 承接：上文「每回合战斗信息数据格式」（数据协议）→ 本设计（结算代码框架）
> 对照：`mydocs/refactor-atomic-action-queue.md`（原子队列）、`mydocs/mark-system-plan.md`（统一效果架构）
> 一句话目标：**结算代码不臃肿、可扩展** = 引擎核心只理解【时机】与【原子】，不理解任何具体效果；
> 一切效果（技能 / 特性 / 印记 / 天气 / 道具）是数据驱动的【绑定集合】，挂进引擎埋好的时机点。

## 0. 现状诊断（为什么现在会臃肿）

| 症状 | 根源 | 后果 |
|---|---|---|
| `resolve_skill` ~140 行，攻击/防御/状态三支 + 20 个效果字段各一个 if | 效果参数直接驱动 engine 分派 | 每加一种技能效果就要改引擎核心 |
| 攻击分支「一次性效果」与状态分支「一次性资源效果」大量重复 | 同一种效果被两份代码各写一遍 | 改一处漏一处 |
| 全局单队列 ≤4 条，无动态追加、无换人拆解 | 没有「原子动作」一等对象 | 特性/印记「追加攻击/连锁」表达不了 |
| Hook 12 个时机只有 SKILL_RESOLVE 接线 | 声明层不完整 | 特性/印记/天气只能往 engine 里塞 if |

**根因一句话**：现在「效果」住在引擎里（字段 → if 分支），而不是住在数据里（声明 → 绑定）。

## 1. 核心架构：三明治分层

```
┌─ 声明层（数据驱动，日常扩展 90% 场景在这里）──────────────────────┐
│  skillbook.py  技能效果 → SkillEffect(字段) → 逐步迁移成 EffectBinding │
│  traits.py     TraitDef.bindings                                    │
│  marks.py      MarkDef.bindings      （未来）                      │
│  weather.py    WeatherDef.bindings   （未来）                      │
│  items.py      ItemDef.bindings      （未来）                      │
├─ 调度层（唯一分派逻辑，几乎不改）──────────────────────────────────┤
│  hooks.py      Hook 枚举 + emit(hook, ctx, sources)                 │
│                emit 返回 hook 追加的原子动作 → 进对应方队列尾        │
├─ 原语层（封闭集合，每个做一件事、可单测）───────────────────────────┤
│  primitives.py apply_stat_mod / apply_hp_loss / apply_heal /         │
│                apply_energy_gain / apply_mark / apply_status /       │
│                dispel / power_mult / cost_change …（按需增）         │
└──────────────────────────────────────────────────────────────────────┘
┌─ 流程层（回合骨架，唯一理解「回合/阵亡/补位/终局」）─────────────────┐
│  engine.py     DECLARE → TURN_START → MAIN(原子流水线) → 补位 → end_turn │
│                （end_turn 内 end_of_turn = TURN_END 结算）           │
└──────────────────────────────────────────────────────────────────────┘
```

**单向依赖**：声明层 → 调度层 → 原语层 → 状态；流程层只调调度层。
**扩展铁律**：加新效果 = 声明层加绑定 +（必要时）原语层加一个原语；**流程层零改动**。

## 2. 对「三时段」想法的完善（一个关键纠正）

你的三时段心智模型是**对的**（回合开始前 / 回合进行时 / 回合结束后），但「三个时段**分别建立**原子动作队列」会让引擎变臃肿——三套队列系统、每套都要处理阵亡中断。**纠正为**：

> **只有「回合进行时」（MAIN）是原子动作队列；「回合开始前」（TURN_START）和「回合结束后」（TURN_END）是纯 hook 时段（没有队列）。**

理由：

1. **队列只服务于「行动先后顺序」**——技能/道具/换人之间要排序，所以排队。TURN_START / TURN_END 的效果（特性回合开始、DOT/天气）没有「对手动作」要排序，按注册顺序直接结算即可，不需要队列。
2. **「道具先于主动作」不需要单独一个 pre 队列**——用 priority 表达：道具 priority=99（与换人同级），在 MAIN 流水线里天然排在最前。这正是现状代码已验证的方式（585 测试全绿）。
3. **三时段在代码里 = 回合骨架的三个明确入口**，保留你的心智模型，但不是三个串行队列系统：

```
DECLARE     读决策 → 定应对/减伤/费用（只读，不执行）          [ctx 局部]
TURN_START  emit(TURN_START) —— 特性/印记的回合开始效果        [纯 hook，无队列]
MAIN        原子流水线：item+主动作+追加，priority→speed→硬币  [唯一 RNG]
TURN_END    end_turn → end_of_turn —— DOT/天气/印记/冷却       [纯 hook，无队列]
```

## 3. 回合结算骨架（伪代码）

```python
# engine.py —— 流程层，唯一理解「回合」的文件
def resolve_turn(state, dec_a, dec_b) -> tuple[list[dict], str | None]:
    """一个回合的结算骨架：DECLARE → TURN_START → MAIN →（补位）→ end_turn。"""
    if state.done:
        return [ev("error", "", message="对局已结束。")], None
    ex = TurnExecution(state, dec_a, dec_b)      # 回合内局部对象（ctx + 双方队列，绝不落 state）
    events = []

    # ① 回合开始前：纯 hook 时段（特性/印记的 TURN_START 效果），无队列
    emit(state, Hook.TURN_START, ex.ctx, collect_sources(state))

    # ② + ③ 回合进行中：决策拆原子入队 → 原子流水线交错执行（唯一 RNG）
    ex.initialize_queues()
    events += ex.run_main(state)
    if ex.need_replacement:
        return events, ex.need_replacement       # 阵亡：暂停等补位（队列保留在 ex）

    return events, None
    # ④ 回合结束后：session 调 end_turn → end_of_turn（TURN_END 结算）→ turn+=1 / battle_end
```

```python
# TurnExecution（engine.py 内，回合内局部对象）
class TurnExecution:
    def run_main(self, state) -> list[dict]:
        """MAIN 原子流水线：一直取下一个原子执行，直到队列空或阵亡中断。"""
        events = []
        while self.any_pending():
            action = self.schedule_next(state)             # priority→speed→硬币（唯一 RNG）
            events += self.resolve_atom(state, action)     # 执行一个原子
            if action.kind is SWITCH_OUT: self.pair_locked = action.side
            if action.kind is SWITCH_IN:  self.pair_locked = None    # 绑定对相邻
            faint_events, need = settle_faints(state)      # 每个原子后查阵亡
            events += faint_events
            if need:
                self.need_replacement = need
                self.cancel_for_fainted(state)             # 取消针对阵亡单位的待执行原子
                if check_winner(state) is not None:
                    state.winner, state.done = check_winner(state), True
                return events                              # 中断（终局 or 等补位）
        return events

    def resolve_atom(self, state, action) -> list[dict]:
        """执行一个原子：① 引擎自身效果 ② emit 该原子的 hook 时机 ③ 追加原子入队尾。"""
        if action.receptor != -1 and state.side(action.side).units[action.receptor].fainted:
            return [ev("skipped", action.side, kind=action.kind, reason="受体已阵亡")]  # 已拍板：受体不存在即跳过
        events = dispatch_kind(state, self, action)        # 按 kind 分派（封闭集合，见 §4）
        emit(state, hook_for(action), ctx_for(self, action), collect_sources(state))
        for atom in emit_returned_atoms: self.enqueue(atom) # hook 追加的原子进对应方队尾
        return events
```

**关键**：`dispatch_kind` 分派的是**原子 kind 的封闭集合**（MAIN_SKILL / ITEM / RECHARGE / SWITCH_OUT / SWITCH_IN / DEATH_IN / BONUS_*），**不是技能效果的 20 个字段**。技能效果全部走 emit（见 §6），引擎不再逐字段 if。

## 4. 原子动作模型

```python
@dataclass(frozen=True)
class AtomicAction:
    side: str        # 发起方（a/b）
    kind: str        # MAIN_SKILL / ITEM / RECHARGE / SWITCH_OUT / SWITCH_IN / DEATH_IN / BONUS_*
    receptor: int    # 受体：队内下标（-1 = 无受体/场上）
    initiator: int   # 发起单位下标（通常 active）
    parent: str      # 父决策类型：skill/switch/recharge/item/death_replacement/bonus
    priority: int    # 调度键（入队时算好，执行时不漂移）
    speed: int       # 调度键（入队时 aggregate_stats 算好）
```

- **换人 = 绑定对 `[SWITCH_OUT, SWITCH_IN]`**：out 执行后锁本侧（pair_locked），强制下一步执行 in，不许他侧插入；对外仍合成一条 `switch` 事件（前端/迷雾/重放零改动，已默认）。
- **追加能力**：`EffectBinding` 增加 `atoms` 字段（hook 声明要追加的原子），`emit` 返回、进对应方队尾——「追加攻击/连锁」由此可表达。
- **死循环防御**：回合级原子上限（建议 32）+ 决策级（建议 16），超限丢弃追加 + `warnings.warn`；同源防重（source_guard）。

## 5. Hook 时机全集与接线点

| Hook | 接线点 | 现状 | 愿景例子 |
|---|---|---|---|
| TURN_START | resolve_turn ①（回合开始前，纯 hook） | 预留 | 特性回合开始效果 |
| TURN_END | end_turn → end_of_turn（回合结束后） | 预留 | 光合/中毒印记、暴风雪、DOT、冷却递减 |
| ENTER | SWITCH_IN / DEATH_IN 原子后 | 预留 | 棘刺/降灵印记 |
| EXIT | SWITCH_OUT 原子后 | 预留 | 暗涌印记 |
| SKILL_RESOLVE | MAIN_SKILL 结算后 | ✅ 已接线 | 最好的伙伴 / 助燃 / 氧循环 / 浸润 |
| DEAL_DAMAGE | 伤害原语后 | 预留 | 风起/蓄电印记、毒刃附加中毒 |
| TAKE_DAMAGE | 受击原语后 | 预留 | 星陨印记 |
| KO | 击杀时 | 预留 | 待定 |
| COUNTER | 应对命中 | 预留 | 待定 |
| GAIN_BUFF | 获得增益后 | 预留 | 萌芽印记 |
| STAT_CALC | aggregate_stats（读钩子） | 预留 | 减速印记 |
| SKILL_COST | 能量支付（读钩子） | 预留 | 湿润/蓄势印记、沙暴 |
| ATTACK_POWER | compute_damage（读钩子） | 预留 | 雨天/攻击印记/蓄势 |

## 6. 效果挂载的三个例子（证明「引擎零改动」）

**例子 A：加技能「毒刃：造成物理伤害 + 给对手 2 层中毒」**
- 声明层：`compile_effect` 产出 `SkillEffect(ATTACK)` + `EffectBinding(hook=DEAL_DAMAGE, effects=(status(中毒, layers=2),))`
- 原语层：新增 `apply_status(unit, "中毒", 2, source="毒刃")`（可单测）
- 引擎：**零改动**（DEAL_DAMAGE 接线点已存在）
- 对比现状：要在 `resolve_skill` 攻击分支再塞一个 if

**例子 B：加印记「攻击印记：全技能威力 +10%」**
- 声明层：marks.py 加 `MarkDef("攻击", polarity="positive", bindings=(ATTACK_POWER → power_mult(0.10),))`
- 引擎：零改动（ATTACK_POWER 接线点在 compute_damage）
- 原语：`power_mult`（读钩子，改伤害系数）

**例子 C：加天气「雨天：双方水系威力 +75%」**
- 声明层：weather.py 加 `WeatherDef("雨天", bindings=(ATTACK_POWER → power_mult(0.75, cond=水系),))`
- 引擎：零改动

## 7. 扩展性规则清单

| 想加的东西 | 改哪里 | 引擎动吗 |
|---|---|---|
| 新技能效果 | 声明层 + 原语（若新原语） | **否** |
| 新特性 | traits.py 加 TraitDef | **否** |
| 新印记 | marks.py 加 MarkDef | **否** |
| 新天气 | weather.py 加 WeatherDef | **否** |
| 新道具 | items.py 加 ItemDef | **否** |
| 新动作类型 | 原子 kind + dispatch 分支 | 是（罕见） |
| 新 hook 时机 | 流程层埋 emit + 接线 | 是（罕见） |

## 8. 阵亡 / 补位 / 终局（已拍板语义，流水线内的位置）

```
MAIN 流水线每执行完一个原子 → settle_faints：
  ① 有阵亡 → 取消针对该单位（受体不存在）的待执行原子（已拍板：受体不存在即跳过）
  ② 有存活后备 → 中断，保留剩余队列，返回 need_replacement（回合未结束，等补位）
  ③ 无存活 / 命归零 → check_winner 直接终局（无补位可问）
submit_replacement → DEATH_IN 原子插队首（新精灵立即在场，触发 ENTER hook）→ 恢复 MAIN 流水线
MAIN 完成 → session 调 end_turn → end_of_turn（TURN_END：DOT/天气/印记/冷却）→ turn+=1 / battle_end
```

## 9. 迁移路径（现状 → 目标，哨兵把关）

| 阶段 | 内容 | 行为变化 | 哨兵 |
|---|---|---|---|
| **Phase 0** | Hook 层扩展：emit 返回追加原子、ctx 加 `action`、EffectBinding 加 `atoms` | 无 | 既有 585 全绿 |
| **Phase 1** | 引擎引入原子流水线（MAIN）；switch 拆 SWITCH_OUT/IN（对外仍合成）；接线 ENTER/EXIT | **无** | 无 hook 追加时与现状同 seed digest 逐位一致 |
| **Phase 2** | 阵亡新语义（已拍板：恢复剩余原子 + DEATH_IN 插队首 + 受体不存在即跳过） | 有（新语义） | 新测试 + 旧语义对照记录 |
| **Phase 3** | 技能效果从「字段 if 分支」迁移到「绑定声明」；原语按需扩展（mark/status/power_mult/cost_change） | 无（对局行为等价） | digest 不变哨兵 |
| **Phase 4** | 填充 marks / weather / items 目录 | 按效果逐个 | 每效果测试 + 覆盖率 |

## 10. 待拍板决策点

1. **三时段纠正方案**：只有 MAIN 是队列，TURN_START/TURN_END 纯 hook——是否认可？（核心决策）
2. **技能效果迁移**：先做「SkillEffect 字段 → 绑定」翻译器（渐进，不动 176 个技能编译）还是直接重写编译输出（彻底）？
3. **TURN_END 结算顺序**：建议固定表（冷却递减 → DOT（中毒→灼烧→寄生）→ 天气（暴风雪/雷鸣）→ 印记回合末），保证确定性。
4. **原子循环上限**：回合 32 / 决策 16（建议起点）。
5. **换人拆解对外事件**：仍合成一条 `switch`（已默认），确认不拆。

---

# 回合结算逻辑 v3 设计（根动作 → 效果 → 事件 → 触发器）

> 状态：**设计稿**（等负责人审阅拍板）　日期：2026-08-29
> 定位：**E0b 回合结算逻辑的正式重新设计**。取代上文「结算架构 v2」（原子动作队列方案已归档）。
> 承接：上文「每回合战斗信息数据格式」（数据协议 v2，§A–§J 仍有效；本设计为其增加 unit_id /
> TargetRef / DomainEvent 等增量，见 §2；**2026-08-29 修正：阵亡补位在回合边界，已取消 EngineSnapshot / ResolutionFrame**）。
> 一句话目标：**引擎只做「调度—执行—发事件—继续调度」；不认任何具体精灵 / 技能 / 效果的名字。**

## 0. 为什么从「原子动作队列」改为「根动作 + 类型化效果 + 事件驱动」

v2 的「原子动作队列 + 万能 Hook」有五处硬伤（负责人 2026-08-29 修正意见）：

| v2 问题 | 后果 | v3 修正 |
|---|---|---|
| 把整个技能结算当「一个原子动作」MAIN_SKILL（实际含扣能/揭示/多段伤害/回血/回能/属性/特性） | 无法定义「每次命中后触发」「第三段伤害前目标阵亡」「伤害后立即反伤」 | 根动作（玩家意图）与效果（引擎指令）分离；根动作执行时展开成真正的原子效果 |
| Hook 直接修改状态（hooks.py emit 内联改 stat_mods） | 效果一多，执行顺序/调试/重放困难 | 事件是已发生的事实不能被执行；Trigger 只「看事件 → 返回新 Effect」 |
| Hook 同时承担「改数值」与「产生副作用」 | 「读取属性时触发回血」这类难查问题 | 数值修正走纯函数 ModifierPipeline；副作用走 Trigger |
| 回合中交互补位要保存执行现场，复杂且易错 | 补位语义与回合结算耦合 | **修正（2026-08-29）：补位两类分治**——阵亡 → 回合末被动补位（空场跳过，无现场）；主动换人 / 技能脱离 → 回合执行中**中断询问玩家补位**（不空场，需保存结算现场以恢复）（§9） |
| 目标只用队伍下标 `receptor:int` | 「目标阵亡补位后剩余伤害打谁」无法回答 | EntityTarget / ActiveTarget / SelfTarget / FieldTarget 显式目标（§2.3） |

## 1. 六大概念与职责边界

| 概念 | 它是什么 | 它自己是否改状态 |
|---|---|---|
| 根动作 RootAction | 玩家这一回合「想做什么」（使用技能 / 换人 / 道具 / 聚能） | 否 |
| 效果 Effect | 引擎接下来要执行的一条具体指令（类型化，如 SpendEnergy / DealDamage） | 否，只是数据 |
| Reducer | 执行 Effect、维护状态不变量的唯一入口（夹取 / 判阵亡 / 发事件） | **是** |
| Trigger | 看到某个事件后，生成新的 Effect | 否 |
| Modifier | 计算过程中修改数值 / 规则（纯函数：克制 / STAB / 减伤 / 天气 / 费用） | 否 |
| Continuation | 回合边界等待补位的「待办标记」（无执行现场需要保存） | 不直接改战斗状态 |

```
双方决策 → 根动作 → 展开 → 效果 → Modifier 计算修正 → Reducer 执行 → 更新 BattleState + 产生 DomainEvent
       ↗ 生成新效果 ← Trigger 匹配 ← 事件 ── 回合内若阵亡 → 场视为空 → 本回合对空场操作跳过
       ── 回合末 → 被动补位（交互，下一回合开始前）→ 进入下一回合
```

**一句话记忆**：根动作 = 玩家要做什么；Effect = 具体要执行什么；Modifier = 执行前怎么算；
Reducer = 实际怎样修改状态；Trigger = 执行后又引发什么；Continuation = 回合末补位等在哪里。

## 2. 回合状态信息 → 数据模型增量

### 2.1 严格输入保持 `BattleState + Decisions`（阵亡在回合末补位；主动换人路径有暂停点例外）

**2026-08-29 修正**：阵亡补位推迟到回合边界（见 §9），**阵亡不打断回合结算**——转移函数输入保持马尔可夫形式：

```
输入  S_t（回合 t 状态快照）+ D_a + D_b   →   输出 S_{t+1} + 事件流
```

- 回合内若某方阵亡：该方**场上视为空**，本回合一切指向空场的操作跳过，**回合不中断**，结算到回合末；
- 回合末（TURN_END 之后）统一检查阵亡 → **被动补位**（等待该方玩家选后备，作为「下一回合开始前的操作」）；
- **阵亡路径**：补位等待只是 session 层一个简单状态（`pending_replacement`），**无执行现场需要序列化**，
  马尔可夫性、重放契约都不变；
- **主动换人 / 技能脱离路径（例外，2026-08-29 拍板）**：回合执行中**中断询问玩家补位决策**，需保存
  「进行中的结算现场」（剩余根动作队列 + sequence）以便补位后恢复——这是该路径特有的暂停点（见 §9.2）。

### 2.2 稳定单位 id（unit_id）—— **已拍板 2026-08-29**

现有 Unit 以 `name + 队内下标` 标识，换人 / 阵亡后下标会变。v3 要求每只精灵一个**局内稳定 `unit_id`**，
Effect / Event / Target 全部用它。
**命名规则（负责人拍板）：`{side}-{槽位}-{精灵名称}`**，如 `"a-0-迪莫"`、`"b-2-喵喵"`——开战即定、永不漂移。

### 2.3 目标引用 TargetRef（替换 receptor:int）

```python
EntityTarget(unit_id="b-2")       # 固定精灵，换人后仍指原精灵
ActiveTarget(side="b")            # 执行时取当前在场精灵
SelfTarget()
FieldTarget()                     # 场上（全体 / 场地）
```

- 固定目标与动态目标都是合理机制，但**必须由技能定义明确指定**，不能由引擎猜测；
- 例：「闪击折返」换人后剩余动作打谁 → 由 ActiveTarget（打现在在场的）还是 EntityTarget（打原目标）决定；
- **空场语义（2026-08-29 修正）**：**空场仅由「阵亡」造成**——阵亡瞬间起该方场视为空，本回合内一切指向
  空场的 Effect 在执行时跳过，直至回合结束，回合末被动补位。**主动换人 / 技能脱离不产生空场**：
  脱离后立即在回合执行中补入新精灵（见 §9.2）。

### 2.4 DomainEvent（内部事件）与展示事件分开

- **DomainEvent**（引擎事实，Trigger 的输入）：`DamageApplied(source_id, target_id, amount)`、
  `UnitEntered(unit_id)`、`StatusAdded(target_id, status_id)`、`TurnEndTick`、`UnitExited`、`SwitchCompleted`…；
- **展示事件**（对外，给玩家 / 前端）：沿用现有 `EVENT_TYPES`（damage / heal / ...），由引擎把
  DomainEvent 映射 / 聚合为展示事件，仍过 `visibility.filter_events_for`。

## 3. 类型化 Effect 与 ScheduledEffect

### 3.1 类型化 Effect（不要大参数袋）

现有 effects.py 的通用 `Effect` 含大量可选字段，会越来越像参数袋。v3 用类型化联合：

```python
@dataclass(frozen=True)
class SpendEnergy:
    unit_id: str
    amount: int
    source: str

@dataclass(frozen=True)
class DealDamage:
    source_id: str
    target: TargetRef
    skill_id: str
    power: int
    skill_type: str
    damage_kind: str      # 物攻 / 魔攻
    hit: int = 1
    total_hits: int = 1

@dataclass(frozen=True)
class Heal:
    source_id: str
    target: TargetRef
    amount: int

@dataclass(frozen=True)
class AddModifier:
    target_id: str
    stat: str             # atk/sp_atk/def/sp_def/speed/...
    ratio: float
    source: str

@dataclass(frozen=True)
class AddStatus:
    source_id: str
    target: TargetRef
    status_id: str        # 中毒 / 灼烧 / 寄生 / 冻结 / 引电 / 萌化
    stacks: int = 1
    duration: int | None = None

@dataclass(frozen=True)
class GainEnergy:
    target_id: str
    amount: int
    source: str

Effect: TypeAlias = SpendEnergy | DealDamage | Heal | AddModifier | AddStatus | GainEnergy | ...
```

### 3.2 ScheduledEffect（携带执行信息）

```python
@dataclass(frozen=True)
class ScheduledEffect:
    effect: Effect
    phase: Phase          # TURN_START / ACTION / TURN_END
    timing: Timing        # BEFORE_ACTION / IMMEDIATE / AFTER_EFFECT / STATE_CHECK / AFTER_ACTION
    priority: int
    sequence: int         # 稳定递增序号
    action_id: str        # 所属根动作
    cause_event_id: str | None = None
```

## 4. Reducer：唯一有权修改状态

每个 Effect 对应一个 reducer，负责夹取、判定边界、产生 DomainEvent。

```python
@dataclass(frozen=True)
class ApplyResult:
    events: tuple[DomainEvent, ...]

def reduce_gain_energy(state, effect: GainEnergy) -> ApplyResult:
    unit = state.unit(effect.target_id)
    before = unit.energy
    unit.energy = min(state.rules.energy_max, unit.energy + effect.amount)
    return ApplyResult(events=(EnergyChanged(unit.id, before, unit.energy, ...),))
```

- 请求回复 2、实际只能 1、夹到上限 —— 由 Reducer 决定，Trigger 绝不写 `unit.energy += 2`；
- 伤害 Reducer 负责：HP 不下 0 / 阵亡后不可再受正常伤害 / 过量伤害记录 / 是否产生 `UnitReachedZeroHp`；
- 所有 HP 修改只经过这里（延续现有 damage.py 漏斗：apply_hp_loss / apply_heal）。

## 5. ModifierPipeline：纯函数数值修正

Modifier 不表示「发生一件事」，只参与计算。典型：克制倍率、STAB、防御减伤、天气威力、技能能耗、
速度、伤害免疫、回合末效果是否执行。

```python
@dataclass(frozen=True)
class DamageQuery:
    attacker_id: str
    defender_id: str
    skill_id: str
    power: int
    multiplier: float = 1.0
    reduction: float = 0.0
```

伤害管线示例：

```
基础伤害 → 克制 ×2 → STAB ×1.25 → 防御减伤 ×0.3 → 天气加成 ×1.2 → 最终伤害
```

- 纯函数：`apply_fire_effectiveness(query, context) -> query'`，**不顺手给敌人上灼烧**（灼烧是 Effect）；
- 与 Trigger 严格分离：Modifier 改计算值，Trigger 产生副作用——避免「读取属性时触发回血」。

## 6. Trigger：事件 → 新 Effect（不直接改状态）

迪莫「最好的伙伴」：

```python
TriggerBinding(
    event_type="DamageApplied",
    condition=And(EventSourceIsOwner(), FieldGreaterThan("effectiveness", 1.0)),
    repeat="once_per_root_action",
    effects=(
        AddModifier("self", "atk", 0.20, "最好的伙伴"),
        AddModifier("self", "sp_atk", 0.20, "最好的伙伴"),
        AddModifier("self", "def", 0.20, "最好的伙伴"),
        AddModifier("self", "sp_def", 0.20, "最好的伙伴"),
        AddModifier("self", "speed", 0.20, "最好的伙伴"),
        GainEnergy("self", 2, "最好的伙伴"),
    ),
)
```

- 监听 `DamageApplied` → 生成 6 个新 Effect → **仍交给 Reducer 执行**；Trigger 自身零副作用；
- `collect_reactions()` 只返回新 Effect（替代 hooks.py 内联改状态）；
- 特性 / 印记 / 天气 / 装备统一实现为 EffectProvider：

```python
class EffectProvider(Protocol):
    def modifiers(self, context) -> Iterable[Modifier]: ...
    def triggers(self, event_type) -> Iterable[TriggerBinding]: ...
```

### 6.1 Hook 循环保护（不能只靠「最多 32 个原子静默丢弃」）

静默丢弃会直接改变胜负。同时采用：

- `once_per_event` / `once_per_action` / `once_per_turn` 触发策略；
- `(binding_id, cause_event_id, target_id)` 去重（多段攻击中同一特性对不同 DamageApplied 正常触发，
  不应被粗暴「同源防重」误杀）；
- 最大触发深度 + 每回合反应预算；
- 超限 → 产生确定性 `EngineFault`：开发环境直接失败，正式服终止并记录异常对局。

## 7. 引擎主循环 resolve()（流程编排，不含具体技能逻辑）

```python
def resolve(state, dec_a, dec_b) -> TransitionResult:
    frame = planner.create_frame(state, dec_a, dec_b)

    # ── 阶段一：根动作（skill / switch / recharge / item，按速度快照排序）──
    while item := frame.scheduler.pop_next():
        if not preconditions.can_execute(state, item):   # 执行时二次校验
            frame.record_skipped(item)
            continue
        prepared = modifiers.prepare(state, item)          # 纯计算修正
        result = reducer.apply(state, prepared)            # 唯一状态写入口
        frame.record_events(result.events)
        for event in result.events:                        # Trigger 只返回新 Effect
            frame.scheduler.enqueue(triggers.collect(state, event, frame.context))
        # 阵亡不中断：该方场视为空；本回合剩余指向空场的 Effect 在执行时自动跳过
        # 主动换人 / 技能脱离：回合中立即补位（换人复合动作，§9.2），不产生空场；
        #   若需玩家选后备 → 中断执行、保存剩余结算现场、返回 need_input（补位后恢复，见 §9.2）

    # ── 阶段二：回合结束（TURN_END）效果 + 下一回合前状态计算 ──
    events_end = end_of_turn(state)                        # DOT / 天气 / 印记 / 冷却递减，可能再造阵亡

    # ── 阶段三：回合边界被动补位（交互，补位是「下一回合开始前的操作」）──
    need = who_needs_replacement(state)
    if need:
        return TransitionResult.need_replacement(state, frame.events + events_end, need)
    if state.done:
        return TransitionResult.finished(state, frame.events + events_end)
    return TransitionResult.resolved(state, frame.events + events_end)
```

> 注：`end_of_turn`（回合结束效果）与 `turn += 1`（回合推进）分离——`turn += 1` 由 session 在
> 「被动补位完成」后执行（见 §9），补位不打断当前回合结算。

**执行时二次校验是必须的**（不能靠「提交时验证过」回避）。**负责人 2026-08-29 拍板能量规则**：
- **提交时**能量不足 → 拒绝该决策（现状 `validate_decision` 已如此，禁止提交）；
- **提交后**因实时计算（敌方行动 / 效果改变了能耗）导致执行时能量不足 → **仍执行原决策**，
  该方在场精灵能量直接夹取为 0（不转聚能、不判定技能失败）。

## 8. 三时段保留为生命周期边界 + 行动排序

### 8.1 三时段（TURN_START / ACTION / TURN_END）

- 顶层阶段只做生命周期边界；**道具不硬编码进 TURN_START**，每个道具自己声明 phase + priority：

```python
ItemDefinition(
    id="healing_potion",
    phase=Phase.ACTION,
    priority=100,
    effects=(Heal(...),),
)
```

- 回合开始类特性 / 天气 / 印记监听 `TurnStarted`；中毒完整结算链：

```
TurnEnded → 中毒 Trigger 命中 → LoseHp → Reducer 改 HP → HpChanged → STATE_CHECK → UnitFainted
```

- 引擎不需要 `if poisoned: ... if burned: ...`——中毒 / 灼烧 / 寄生 / 天气 / 印记 / 特性统一是 EffectProvider；
- **回合末收尾顺序（2026-08-29 修正）**：根动作 → TURN_END 效果 → 下一回合前状态计算 →
  若某方阵亡 → 被动补位（交互，下一回合开始前）→ turn+=1 进入下一回合；补位**不打断**当前回合结算（见 §9）。

### 8.2 根动作排序（回合开始锁定）

```
阶段 → 优先级 → 回合开始时速度快照 → RNG 平手裁决
```

- **已拍板（2026-08-29）：根动作排序按回合开始时速度快照锁定，中途变化不重排**（除非机制明确「改变本回合行动顺序」）；
- Trigger 产生的效果不开放任意优先级数字，用有限时机：

```
REPLACE_EFFECT   替换当前效果
IMMEDIATE        当前效果后立即处理
AFTER_ACTION     当前根动作结算完成后
END_PHASE        当前阶段末
NEXT_TURN        下回合开始
```

### 8.3 换人：复合动作，不是两个可被插入的全局原子

```
BeforeSwitch → Exit → 更新 active → Enter → AfterSwitch
```

默认**不允许其他玩家根动作插入 Exit 与 Enter 之间**（避免「场上没有精灵」的非法中间状态）；
对外仍合成一条 `switch` 展示事件。
**主动换人（决策 switch）与技能「自己脱离」（闪击折返）都在回合执行中立即完成补位，不产生空场**——
空场仅由阵亡造成（见 §9）。

## 9. 补位与 Continuation —— **2026-08-29 修正：两类补位分治**

补位分两类，触发时机与空场语义**不同**：

| 补位类型 | 触发 | 时机 | 空场？ |
|---|---|---|---|
| **阵亡被动补位** | 精灵阵亡（生命归零 / DOT 致死） | **回合结束后**（回合边界） | **是**：阵亡瞬间起场视为空，本回合对空场操作跳过 |
| **主动换人补位** | 决策 switch / 技能「自己脱离」（闪击折返） | **回合执行过程中**（立即） | **否**：脱离后立即补入新精灵，不产生强制空场 |

### 9.1 阵亡被动补位（回合结束后）

**修正理解**：回合中某方精灵阵亡 → 该方**场上视为空**；由于不能对空对象操作，
本回合一切指向空场的操作**跳过**，**回合不中断**，结算到回合末；**补位是下一回合开始前的操作**。

**回合末收尾顺序**：
```
执行根动作 → 执行回合结束时的效果（TURN_END：DOT/天气/印记/冷却递减）
           → 执行下一回合开始前的一些状态计算
           → 若某方阵亡 → 被动补位（等待该方玩家选后备）
           → turn += 1，进入下一回合
```

- **空场**：阵亡方在场精灵视为空——本回合内一切指向该空场的 Effect（攻击 / 吸血 / 状态 / 触发）
  在执行时**跳过**（不能对空对象操作）；
- **补位时机**：回合末统一检查，由阵亡方玩家选择后备（交互补位）；补位是「下一回合开始前的操作」；
- **不打断结算**：补位发生在回合边界，**无进行中执行现场需要保存** → 不需要 ResolutionFrame /
  EngineSnapshot；session 只需一个 `pending_replacement` 标记（哪方在等补位）；
- **终局判定**：阵亡后若命归零 / 无存活后备 → 直接判负，无补位可问。

```python
# session 层（回合末收尾，仅阵亡路径）
def end_turn(state):
    end_of_turn(state)                    # TURN_END 效果 + 下回合前状态计算（可能再造阵亡）
    need = who_needs_replacement(state)   # 检查双方场空（仅阵亡造成空场）
    if need:
        pending_replacement = need        # 等该方玩家补位（交互），不推进回合
        return
    state.turn += 1                       # 补位完成后才进入下一回合
```

### 9.2 主动换人 / 技能脱离补位（回合执行中，中断询问，不空场）

- **决策 switch**：`action.value` 直接指定目标槽位，回合内换人复合动作立即完成
  （BeforeSwitch → Exit → Enter → AfterSwitch，见 §8.3），**不产生空场**；
- **技能「自己脱离」（闪击折返）**：技能结算后触发脱离 → **强制在回合执行过程中补位**——
  新精灵立即上场，**场上不空**，后续动作（含对手攻击）正常指向新在场精灵；
- **后备来源（2026-08-29 拍板）**：**不采用决策预告后备**——需要选后备时，**中断回合执行，询问玩家
  补位决策**；玩家选择后新精灵立即上场（不空场），**恢复回合执行**（剩余动作正常结算）；
- **中断-恢复（暂停点）**：中断点需保存「进行中的结算现场」（剩余根动作队列 + 当前动作内部剩余效果 +
  sequence），补位完成后从此处恢复——**只属于主动换人路径**；阵亡路径（回合末补位）不需要；
- **终局判定**：中断询问时若该方无存活后备 / 命归零 → 直接终局，无补位可问。

## 10. 第零回合与对局状态机

首发选择是**对局状态机的一部分**，不是普通战斗回合：

```
WAITING_FOR_LEADS → TURN_INPUT → RESOLVING → END_OF_TURN → (PENDING_REPLACEMENT →) TURN_INPUT → FINISHED
```

- 双方首发若需互相隐藏 → 像普通决策一样先缓存，双方提交后同时公开并产生两个 `UnitEntered`；
- **已拍板（2026-08-29 修正）**：补位两类分治（见 §9）——阵亡 → `PENDING_REPLACEMENT`（回合末 END_OF_TURN
  触发，session 持 `pending_replacement` 标记，补位完成后进入下一回合）；主动换人 / 技能脱离 → 回合执行中
  **中断询问玩家补位**（暂停点，保存结算现场后恢复），**不经过** `PENDING_REPLACEMENT`；
- 现有 `BattleState + RNG 游标 + clone/step + replay hash` 基础保留（尤其 RNG 状态序列化）。

## 11. 场景走查（数值示例）

### 场景一：迪莫使用火焰箭（克制伤害触发特性）

A 迪莫（物攻80 速度92 能量10）vs B 森巨人（草系 生命144 物防132 速度70 能量5）。
A 决策火焰箭（火系物攻 威力80 能耗2），B 决策聚能。

```
① 根动作：A UseSkill(速度快照92)，B Recharge(70) → 迪莫先行动
② 展开：SpendEnergy(2) → RevealSkill → DealDamage(→ActiveTarget(b), 火/物攻/80)
③ 扣能：10 → 8
④ Modifier：火克草 ×2，无 STAB → 伤害 = int((80/132)×80×0.9×2) = 87
⑤ Reducer：森巨人 144 → 57，发 DamageApplied(eff=2)
⑥ Trigger「最好的伙伴」→ 5 个 AddModifier(+20%) + GainEnergy(2) → 迪莫能量 8→10、五维+20%
⑦ 森巨人聚能：5 → 10
```

**加速在行动后生效，本回合根动作顺序不按新速度重排。**

### 场景二：回合结束效果与「额外触发」

小灵菇（毒蘑菇：回合结束偷敌方在场全部精灵 1 能量）vs 粉耳星兔（双向光速：双方回合末效果额外触发 1 次）。

- 「双向光速」设计为 **Modifier**（`EndTurnScheduleModifier(extra_repetitions=1)`）而非 Trigger，
  否则「复制回合结束 → 新回合结束又触发」会无限循环；
- ① 先算执行次数：基础 1 + 双向光速 1 = 2；② 第一次 TurnEndTick：小灵菇 3→4、粉耳星兔 6→5；
  ③ 第二次：小灵菇 4→5、粉耳星兔 5→4；
- 「落陨星兔」（双方回合末效果不触发）→ 更高优先级 `EndTurnScheduleModifier(suppress=True, priority=1000)`，
  执行次数直接 0，**不在每个中毒/寄生/天气里分别判断**。

### 场景三：闪击折返 + 回合执行中中断询问补位（不空场，2026-08-29 修正语义）

电咩咩（快充：离场回 10 能量）用「闪击折返」（威力45 能耗5 2连击 自己脱离）→ 梦游（后手拍击）。
电咩咩速度快于梦游，先行动。

```
① 展开：SpendEnergy(5) → DealDamage(hit 1/2) → DealDamage(hit 2/2) → RequestSwitch(自己脱离)
② 中断回合执行，询问 A 方玩家补位决策（保存剩余结算现场：梦游拍击待执行）
③ 玩家选恶魔叮 → 电咩咩脱离（UnitExit）→ 立即换入恶魔叮（UnitEnter）→ SwitchCompleted——**不产生空场**
④ 恢复回合执行：梦游拍击（ActiveTarget(a)）→ 目标 = 现在在场的恶魔叮 → **正常命中**（不是空挥）
⑤ 电咩咩离场「快充」Trigger → GainEnergy(电咩咩,10)：5→10（离场仍是有效实体）
⑥ 恶魔叮入场「渴求」→ 吸血+50%；梦游「做噩梦」监听 SwitchCompleted → LoseEnergy(恶魔叮,3) → 10→7
⑦ 恶魔叮承受梦游拍击；本回合结束，恶魔叮继续在场（无阵亡，不走回合末被动补位）
```

## 12. 迁移路径（现状 → v3，哨兵把关）

| 步 | 内容 | 行为变化 | 哨兵 |
|---|---|---|---|
| 1 | Effect 改类型化联合；区分内部 DomainEvent 与对外展示事件 | 无 | 既有 585 全绿 |
| 2 | 拆分 resolve_skill →「技能编译器 + Effect Reducer」；暂保持现有行动顺序与阵亡语义 | 无 | digest 不变 |
| 3 | Hook 改「事件 → 返回 Effect」collect_reactions，禁止直接改状态 | 无 | digest 不变 |
| 4 | 新增独立 ModifierPipeline | 无 | digest 不变 |
| 5 | 接入状态 / 印记 / 天气等统一 EffectProvider | 按效果逐个 | 每效果测试 + 覆盖率 |
| 6 | 阵亡语义切换为「回合边界被动补位」：空场跳过 + 回合末统一补位 + pending_replacement（无需 ResolutionFrame） | 有（新语义） | 新测试 + 旧语义对照 |
| 7 | 用现有黄金轨迹与 replay hash 保证无规则变更阶段行为完全一致 | — | replay 全绿 |

## 13. 已拍板决策记录 + 影响面

**已拍板（负责人 2026-08-29）**：

1. **补位（2026-08-29 修正）——两类分治**：
   - **阵亡被动补位**：回合结束后（回合边界）——阵亡方场视为空、本回合对空场操作跳过、回合不中断；
     回合末统一检查、玩家选后备；不需要 ResolutionFrame。→ §9.1
   - **主动换人 / 技能脱离补位**：回合执行中**中断询问玩家补位决策**（已拍板，不用决策预告后备）——
     不产生强制空场、新精灵马上在场；中断点保存结算现场、补位后恢复执行。→ §9.2
2. **执行时能量不足**：提交时不足 → 拒绝提交；提交后实时计算不足 → 仍执行原决策、能量夹取为 0。→ §7
3. **根动作排序**：按回合开始速度快照锁定，中途变化不重排。→ §8.2
4. **Trigger 时机封闭集**：REPLACE_EFFECT / IMMEDIATE / AFTER_ACTION / END_PHASE / NEXT_TURN（默认采纳）。→ §8.2
5. **Hook 循环保护**：once_per_* + (binding_id, cause_event_id, target_id) 去重 + 深度 / 预算 + EngineFault
   确定性失败（默认采纳）。→ §6.1
6. **unit_id 命名**：`{side}-{槽位}-{精灵名称}`（如 `"a-0-迪莫"`）。→ §2.2

**影响面**：

- engine.py：resolve_skill 拆解、resolve 主循环、dispatch_kind → reducer 表、阵亡「场空跳过」检查、回合末被动补位；
- hooks.py：emit 直接改状态 → collect_reactions 返回 Effect（EffectBinding 语义保留但禁止写状态）；
- effects.py / skillbook.py：大字段袋 → 类型化 Effect + 技能编译器；
- models.py：Unit 加 unit_id；TargetRef 新类型；SideState 加 pending_replacement 标记；
- traits.py / marks.py / weather.py / items.py：统一 EffectProvider 协议；
- session.py：回合末被动补位（end_of_turn → 查场空 → pending_replacement → 补位后 turn+=1）；
- replay.py / store.py：轨迹契约不变（补位在回合边界，replace_a/b 仍是必需输入；无 ResolutionFrame）；
- view.py / visibility.py：展示事件从 DomainEvent 映射，白名单登记不变。

---

# 战斗状态保存与完美复现（轨迹记录 v3）

> 状态：**设计稿**（2026-08-29）　承接：v3 结算逻辑（两类补位 / 中断询问已拍板）
> 目标：保存的信息足够从零重放，逐回合 `state_hash` 逐字节一致（**完美复现**）
> 对照现状：`src/environment/replay.py`（replay_record）、`rock_pvp_agent/battle/store.py` ↔ analysis.py 双端契约

## A. 完美复现的定义与依据

完美复现 = 用记录从零重建对局，逐回合 `state_hash` 序列与原始对局**逐字节一致**。

依据（马尔可夫 + 确定性）：

```
S_{t+1} = F(S_t, 本回合全部玩家输入)
```

只要 F（引擎代码）不变、S₁ 能由记录确定重建，则 F 的**一切输出（新状态、事件流）确定可推导**。
因此记录**不保存任何派生量**——事件、中间状态、暂停点现场都可以由 F 重新推导。

## B. 最小复现集（必须保存）

| 类别 | 字段 | 为什么必需 |
|---|---|---|
| 初始条件 | rules（BattleRules 全字段） | 重建 S₁ 的规则常量 |
| 初始条件 | team_a / team_b（roster spec，build_roster 产物） | 引擎唯一吃的数据层产物，确定性重建单位 |
| 初始条件 | seed（引擎 RNG 种子） | 引擎 RNG 流从种子确定性重放 |
| 初始条件 | battle_id | 标识 |
| 数据版本 | **data_digest**（FULL/VALID 数据文件哈希） | 数据文件变化会导致重建单位漂移——锁定数据版本 |
| 引擎版本 | engine_version / schema version | 诊断用；完美复现仅在同一引擎代码内保证 |
| 每回合输入 | turns[].inputs（有序列表，见 §C） | 玩家决策 + 补位选择，按引擎询问顺序 |
| 校验 | turns[].state_hash（回合末状态指纹） | 逐回合比对，首个失配即停 |

**为什么不保存完整 BattleState**：状态由（初始条件 + 输入）确定性重建（`to_dict/from_dict` 往返 +
`replay_record` 验证的就是这件事），保存它是冗余。

## C. 每回合输入的有序列表（v3 关键变化）

v3 引入两类补位（阵亡回合末 / 主动换人中断询问）后，一回合内可能出现**多个玩家输入**，
必须按引擎询问顺序记录成**有序列表**：

```json
"turns": [
  { "turn": 1,
    "inputs": [
      { "kind": "decision",    "side": "a", "action": {"type": "skill", "value": 0}, "item": "" },
      { "kind": "decision",    "side": "b", "action": {"type": "recharge"},          "item": "" },
      { "kind": "replacement", "side": "a", "choice": 2 },   // 中断询问（主动换人 / 技能脱离）
      { "kind": "replacement", "side": "b", "choice": 1 }    // 阵亡回合末补位
    ],
    "state_hash": "9f8e..." } ]
```

- `decision`：双方提交的根动作（skill / switch / recharge + item）；
- `replacement`：补位选择（choice = 后备槽位）——**无论回合末阵亡补位还是回合中中断询问，都按实际发生顺序进同一列表**；
- 顺序即引擎询问顺序：先双方 decision → 中断 replacement（回合执行中）→ 阵亡 replacement（回合末边界）；
- 同一回合可同时出现多种输入（如「闪击折返中断 + 对方阵亡」）。

## D. 重放算法

```
session = BattleSession.start(team_a, team_b, seed, rules, battle_id)
for tr in record.turns:
    for inp in tr.inputs:                      # 按序喂入
        if inp.kind == "decision":    session.submit(side, Decision(...))
        else:                          session.submit_replacement(side, inp.choice)
    actual = session.state.state_hash()
    assert actual == tr.state_hash              # 首个失配即停（已偏离，后续无意义）
```

- **暂停点现场（剩余队列 + sequence）不需要记录**：重放时引擎重跑到中断点，现场被确定性重建，
  `state_hash` 验证到该点一致；
- 阵亡 replacement 挂在阵亡发生的回合下（它是该回合结算的产物，重放 resolve 后自然到达边界询问）。

## E. 与「会话现场」的区别（两种保存，别混）

| | **轨迹记录**（本设计，复现用） | **会话现场**（实时 / 崩溃恢复用） |
|---|---|---|
| 用途 | 离线从零**完美复现** | 断线重连 / 实时中断续玩 |
| 内容 | 最小复现集（初始条件 + inputs + hash） | BattleState 全量快照（to_dict）+ **暂停点现场**（剩余队列 + sequence） |
| 复现方式 | 重跑 F | 从快照 + 现场**继续** F |
| 何时需要 | 对局结束保存 | 仅中断发生时（主动换人中断 / 服务器重启） |

> 主动换人的「暂停点」属于**会话现场**层（实时续玩需要），**不进轨迹记录**（复现不需要）。

## F. 兼容性与双端契约

- **v3 形状变化**：turns 从 `{decision_a, decision_b, replace_a, replace_b, state_hash}` 改为
  `{inputs: [...], state_hash}`——`store.py` ↔ `analysis.py` 双端 `_RECORD_REQUIRED` 必须同步（迁移点）；
- **展示事件**（观战 / 人类轨迹）是派生量，**不进复现记录**（可另存展示副本，见 BattleController.record 展示变体）；
- **玩家 RNG 流**不进记录（决策本身就是输入）；自博弈重跑玩家需要 player rng seed（仅元数据）；
- **data_digest / engine_version** 是复现的隐式前提：数据或引擎代码变更后，历史轨迹只能"尽力重放"
  （失配即停并告警），不能保证逐字节复现——这与「代码不变 → 复现不变」的纪律一致。
