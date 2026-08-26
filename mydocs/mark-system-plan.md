# 印记系统设计方案（含统一效果架构）

日期：2026-08-24　状态：方案文档（待负责人确认语义后实施）

## 1. 背景与目标

数据源 `mydocs/mark.md` 定义了 13 种印记（8 正面 + 5 负面）。印记是「常驻战场」的阵营级效果。本方案的目标：

1. **实现 13 种印记的全部效果**（施加/叠加/覆盖/触发）。
2. **设计一个高内聚、低耦合的统一效果架构**，让印记、特性（trait）、技能效果、增减益 buff 四类系统共用同一套机制，不各自为政。

## 2. 印记规则解读（已确认）

原文档规则 + 负责人确认，形成以下语义：

| 规则 | 确认后的语义 |
|---|---|
| 阵营级 | 印记挂在 **SideState** 上，**除非被其他印记替代或被驱散，否则一直保留**。换人后印记留在阵营上，效果作用于触发时该方在场精灵。 |
| 同种叠加 | 相同印记**叠加层数**，结算时按「单个印记效果 × 层数」。 |
| 异种顶替 | 不相同的印记、同属正面（或同属负面）→ **后来的顶替旧的**。 |
| 每方上限 | 每方各自最多同时存在 **1 种正面 + 1 种负面**。**例外**：特性可放宽该上限（见下「里拉鳐」）。 |
| 可叠加 | **当前 13 种印记都是可叠加的**（都有层数，效果 × 层数）。后期若出现不可叠加印记，见 §4.2 扩展设计。 |

### 里拉鳐例外（独立印记空间，已确认）

`里拉鳐` 特性「吟游之弦」：**赋予的印记不会替换其他印记，而是同时生效**。负责人确认：

1. **里拉鳐作为施加方时**，它赋予的印记进入**独立印记空间**（`exclusive_marks`），与其他印记**共存**。
2. **双向隔离**：里拉鳐施加的印记**不影响**阵营方的正常印记；阵营方施加的印记也**不影响**里拉鳐的印记。
3. **只进不出（除非驱散）**：里拉鳐的印记**只能被带驱散印记效果的技能消除**，不能被顶替、不因换人消失。

架构含义：印记容器从「单槽」扩展为「**普通空间 + 独立空间**」两个容器，施加方按特性路由到不同空间（见 §4）。

## 3. 统一效果架构（核心：高内聚 · 低耦合）

### 3.1 一个核心洞察

印记、特性、技能效果、增减益 buff 的本质是同一件事：**「某触发时机，对某目标，施加某效果」**。因此它们可以共用一套模型，而不必各自写一套 if/else。

### 3.2 四个构件

```
                    ┌─────────────┐
   来源（声明式）     │  来源们       │   skill / trait / mark / passive
                    │ 只声明绑定，不互知 │
                    └──────┬──────┘
                           │ 全部是 EffectBinding 列表
                    ┌──────▼──────┐
   调度（统一）       │ Hook 分发器    │   引擎在关键时机 emit(hook, ctx)
                    │ 收集绑定→跑原语  │
                    └──────┬──────┘
                    ┌──────▼──────┐
   原语（单一职责）    │ Effect 原语    │   伤害/回复/能量/增减益/印记/异常/驱散
                    │ 每个只做一件事   │
                    └──────┬──────┘
                    ┌──────▼──────┐
   状态（纯数据）     │ 状态切片       │   stat_mods / marks / statuses / weather
                    │ 可序列化·可往返  │
                    └─────────────┘
```

**单向依赖**：来源 → 调度 → 原语 → 状态。上层不认识下层细节，下层不反向依赖上层。

### 3.3 构件一：Effect 原语（效果词汇表，封闭集合）

每个原语只做一件事，是引擎认识的「效果最小单位」：

```python
@dataclass(frozen=True)
class Effect:
    op: str            # 原语类型（封闭集合）
    target: str        # self / foe / side / incoming / field
    value: int | float = 0        # 数值
    stat: str = ""                # 增减益维度（atk/sp_atk/def/sp_def/speed）
    mode: str = ""                # pct / flat
    layers: int = 1
    duration: int = 0             # 0 = 永久
    mark_id: str = ""             # 印记原语参数
    status_id: str = ""           # 异常原语参数
    cond: str = ""                # 条件（先用简单谓词，如 skill.energy==3）
```

原语集合（封闭、可枚举）：
`damage` / `heal` / `lose_hp` / `energy_gain` / `energy_loss` / `stat_mod` / `mark` /
`status` / `dispel` / `power_mult` / `power_add` / `cost_change` / `counter` /
`consume_mark_layers` / `switch` / `retreat` / `weather` …

每个原语是一个独立函数（如 `apply_stat_mod(state, ctx, effect)`），可单独单测。

### 3.4 构件二：Hook 触发点（时机，封闭集合）

引擎在固定位置发事件，分发器收集该时机的全部绑定：

```python
class Hook:
    # 事件钩子（副作用）
    TURN_START = "turn_start"
    TURN_END = "turn_end"
    ENTER = "enter"            # 精灵入场（补位/换人/返场/开战）
    EXIT = "exit"              # 精灵离场（换人/脱离）
    SWITCH = "switch"
    SKILL_USE = "skill_use"    # 使用技能（支付能量后）
    DEAL_DAMAGE = "deal_damage"
    TAKE_DAMAGE = "take_damage"
    FATAL = "fatal"            # 受到致命伤害（归零前拦截，可保命）
    KO = "ko"                  # 击杀
    COUNTER = "counter"        # 应对成功
    # 读钩子（值变换，被动修正）
    STAT_CALC = "stat_calc"    # 属性计算：读全部修正，折叠进 aggregate_stats
    SKILL_COST = "skill_cost"  # 技能能耗：读修正
    ATTACK_POWER = "attack_power"  # 攻击威力/伤害系数：读修正
    GAIN_BUFF = "gain_buff"    # 获得增益后：追加层数
```

**事件钩子**跑效果（改状态）；**读钩子**折叠修正（改计算结果）。两者都由同一绑定表驱动。

### 3.5 构件三：EffectBinding 与来源（声明式，低耦合关键）

**一切来源都只是「绑定集合」**：

```python
@dataclass(frozen=True)
class EffectBinding:
    hook: str
    effects: tuple[Effect, ...]
    cond: str = ""          # 条件谓词（可先用字符串键查表，后换表达式）

# 技能（静态定义 + 每单位可变实例）
@dataclass
class SkillInstance:
    name: str; kind: str; type: str
    power: int; energy_cost: int; priority: int
    effects: tuple[EffectBinding, ...]    # 使用时触发
    passive: tuple[EffectBinding, ...] = ()  # 携带时挂的钩子（永久成长/蓄势等）

# 特性（纯被动）
@dataclass(frozen=True)
class Trait:
    name: str
    bindings: tuple[EffectBinding, ...]
    once_per_battle: bool = False

# 印记（阵营级，声明式）
@dataclass(frozen=True)
class MarkDef:
    mark_id: str
    polarity: str            # "positive" / "negative"
    stackable: bool = False
    bindings: tuple[EffectBinding, ...]   # 全部钩子触发效果
```

**低耦合的体现**：技能不知道印记存在，印记不知道特性存在。它们只声明「我在这几个时机做什么」；引擎的 Hook 分发器统一收集、统一执行。**新增一种印记/特性/技能效果 = 加一行绑定表，不改引擎**。

### 3.6 构件四：状态切片（纯数据，可序列化）

| 切片 | 载体 | 生命周期 |
|---|---|---|
| 增减益 buff | `Unit.stat_mods: list[StatModifier]`（已有） | 永久/持续N回合，读钩子折叠 |
| 印记 | `SideState.positive_marks / negative_marks`（普通空间，上限 1）+ `exclusive_marks`（里拉鳐独立空间，无上限） | 施加/叠加/顶替/共存/被驱散 |
| 异常状态 | `Unit.statuses: dict[str, StatusState]` | 回合末 tick |
| 天气 | `BattleState.weather: WeatherState | None` | 回合末递减 |

`MarkState = (mark_id, layers)`——JSON 原生，随 `to_dict`/`from_dict` 往返，马尔可夫性测试继续成立。

### 3.7 Hook 分发器（统一调度）

```python
def emit(state, hook, ctx, sources) -> None:
    """收集该时机全部来源的绑定并执行。来源由调用方给出：技能实例 / 在场双方特性 / 双方印记。"""
    for binding in collect(state, hook, ctx, sources):
        if binding_matches(binding, ctx):
            for effect in binding.effects:
                apply_effect(state, ctx, effect)   # 分发到对应原语
```

`collect` 的规则（低耦合但可控）：
- `ENTER/EXIT/SWITCH`：触发方该精灵的 trait + 双方阵营的印记（如棘刺/降灵挂在入场方）。
- `TURN_END`：双方在场精灵的 trait + 双方印记（如光合/中毒）。
- `DEAL/TAKE_DAMAGE`：攻击方与受击方 trait + 双方印记（如星陨/风起）。
- `SKILL_USE`：使用者的技能实例 + trait + 双方印记（如龙噬/蓄势）。
- 读钩子（STAT_CALC/SKILL_COST/ATTACK_POWER）：在场双方的所有被动修正折叠。

## 4. 印记数据模型

**容器 = 每极性一个普通印记列表 + 一个独立印记空间**（里拉鳐专属）：

```python
@dataclass
class MarkState:
    mark_id: str
    layers: int = 1

class SideState:
    ...
    positive_marks: list[MarkState]   # 普通空间（默认上限 1，0 号是「当前生效槽」）
    negative_marks: list[MarkState]   # 普通空间（默认上限 1）
    exclusive_marks: list[MarkState]  # 独立空间：里拉鳐专属，无上限、共存、仅驱散可移除

def apply_mark(state, side_state, mark_id, layers, *, source_unit, polarity):
    """按施加方路由到印记空间；核心不写「里拉鳐」，靠特性标记（如吟游之弦）判定。"""
    if source_unit 有「印记独立空间」特性（读钩子 MARK_SPACE 判定）:
        _apply_exclusive(side_state, mark_id, layers)
    else:
        _apply_normal(side_state, mark_id, layers, polarity)

def _apply_normal(side_state, mark_id, layers, polarity):
    """确认后的规则：同种叠层；异种同极性后来顶替旧的；普通空间每极性上限 1。"""
    marks = side_state.positive_marks if polarity == "positive" else side_state.negative_marks
    for m in marks:
        if m.mark_id == mark_id:
            if MARK_CATALOG[mark_id].stackable:
                m.layers += layers          # 同种可叠加 → 加层
            else:
                m.layers = layers           # 同种不可叠加 → 刷新（见 §4.2）
            return
    if len(marks) >= 1:
        marks[0] = MarkState(mark_id, layers)   # 满 1 顶替旧的
    else:
        marks.append(MarkState(mark_id, layers))

def _apply_exclusive(side_state, mark_id, layers):
    """独立空间：同种叠层、异种追加共存，永不顶替；无上限。"""
    for m in side_state.exclusive_marks:
        if m.mark_id == mark_id:
            if MARK_CATALOG[mark_id].stackable:
                m.layers += layers
            else:
                m.layers = layers
            return
    side_state.exclusive_marks.append(MarkState(mark_id, layers))

def dispel_mark(side_state, scope="normal", polarity=None, mark_id=None) -> int:
    """驱散印记：
    - scope="normal"：清普通空间（默认，绝大多数驱散效果走这里）；
    - scope="all"   ：同时清独立空间（「驱散双方所有印记」类技能）；
    返回清除的总层数。独立空间的印记**只能**经 scope="all" 的驱散移除。"""
```

- **里拉鳐「吟游之弦」落地**：`apply_mark` 用读钩子 `MARK_SPACE` 判定施加方是否有「印记独立空间」特性；有 → 走 `_apply_exclusive`（共存、不入普通槽、不被普通顶替）。普通来源只操作普通空间，天然不影响里拉鳐的印记；里拉鳐的印记也只在「驱散所有印记」时被清掉。**核心不写「里拉鳐」三个字**。
- **结算按层数**：分发器把该印记当前 `layers` 传入 ctx，绑定里的 `damage(3%×layers)` / `power_mult(30%×layers)` 等按层放大（见 §5 效果表）。

### 4.2 未来不可叠加印记的扩展与兼容

当前 13 种全部可叠加，但 `MarkDef.stackable` 字段已存在，为后期预留：

| 场景 | 现状 | 扩展后 |
|---|---|---|
| 同种重复施加 | 可叠加 → `layers += N` | 不可叠加 → `layers = N`（**刷新**） |
| 结算 | `单效果 × layers` | `单效果 × 1`（layers 恒 1） |
| 驱散 | 按层数返回 | 语义不变 |

实现上：`apply_mark` 的叠层分支已经按 `MARK_CATALOG[mark_id].stackable` 分流，新增一个不可叠加印记只需在目录里把 `stackable=False`；引擎、效果表、序列化**零改动**。这保证了「以后出现不可叠加印记再说」的成本是**一行配置**。

## 5. 13 个印记效果表（MarkDef 目录）

**已确认：当前 13 种全部可叠加**——同种施加加层，结算按「单效果 × 层数」。

### 正面印记

| mark_id | 可叠加 | 绑定（hook → effects，×层 = 层数） |
|---|---|---|
| 湿润 | 是 | `SKILL_COST → [cost_change(self, -1×层)]` |
| 龙噬 | 是 | `SKILL_USE → [if skill.energy==3: stat_mod(self, 双攻, pct, +30%×层)]` |
| 蓄势 | 是 | `SKILL_COST → [cost_change(self, +1×层)]`；`ATTACK_POWER → [power_mult(self, +30%×层)]` |
| 风起 | 是 | `DEAL_DAMAGE → [if 先手攻击: power_mult(self, +20%×层)]` |
| 蓄电 | 是 | `DEAL_DAMAGE → [power_add(self, +10×层)]`（迸发：本次威力+10×层） |
| 光合 | 是 | `TURN_END → [energy_gain(self, 1×层)]` |
| 攻击 | 是 | `ATTACK_POWER → [power_mult(self, +10%×层)]` |
| 萌芽 | 是 | `GAIN_BUFF → [stat_mod(extra, +1×层)]` |

### 负面印记

| mark_id | 可叠加 | 绑定 |
|---|---|---|
| 减速 | 是 | `STAT_CALC → [stat_mod(self, speed, flat, -10×层)]` |
| 降灵 | 是 | `ENTER → [energy_loss(self, 1×层)]` |
| 星陨 | 是 | `TAKE_DAMAGE → [if 攻击技能非幻系: damage(self, 幻, N×层); consume_mark_layers(self)]` |
| 中毒 | 是 | `TURN_END → [damage(self, 毒, 3%×层 生命)]` |
| 棘刺 | 是 | `ENTER → [lose_hp(self, 6%×层)]` |
| 暗涌 | 是 | `EXIT → [stat_mod(incoming, 随机五维, 减益, 5×层)]` |

> 注：印记效果表里的「双攻」「随机五维」「非幻系」「先手」等需要读钩子能拿到的最小上下文（当前在场精灵、技能系别、是否先手、伤害值），这些都放 `ctx`。未来若出现不可叠加印记，只需把对应 `MarkDef.stackable` 置 False（见 §4.2）。

## 6. 引擎改动点

1. **Hook 分发器**（新增 `effects.py` 或 `hooks.py`）：`emit()` + 绑定表。
2. **原语实现**（新增 `primitives.py`）：`apply_stat_mod` / `apply_mark` / `apply_damage` / … 每个可单测。
3. **`SideState` 加印记槽**，`to_dict`/`from_dict` 序列化。
4. **引擎关键点埋 `emit`**：`resolve_skill`（SKILL_USE / DEAL_DAMAGE / TAKE_DAMAGE / COUNTER）、`apply_hp_loss`（FATAL 拦截点）、`settle_faints`（KO）、`resolve_switch`/`apply_replacement`（ENTER / EXIT）、`end_of_turn`（TURN_END，含异常/印记/天气 tick）。
5. **读钩子接入计算点**：`aggregate_stats`（STAT_CALC）、能量支付（SKILL_COST）、`compute_damage`（ATTACK_POWER）。
6. **状态切片序列化**，马尔可夫性测试覆盖。

## 7. 与特性/技能/增减益的兼容（本方案的核心价值）

| 系统 | 在统一架构里的角色 |
|---|---|
| 技能效果 | `SkillInstance.effects`（使用时）+ `passive`（携带时） |
| 特性 | `Trait.bindings`（常驻被动，事件钩子 + 读钩子） |
| 印记 | `MarkDef.bindings`（阵营级，事件钩子 + 读钩子） |
| 增减益 buff | 不是来源，是**原语的产物**：`stat_mod` 原语写入 `Unit.stat_mods`；读钩子 `STAT_CALC` 折叠它 |

**四者共用的东西**：Effect 原语、Hook 时机、分发器、状态切片。**四者各自的差异**只在「声明在哪、何时触发」：
- 技能：主动（使用技能时）。
- 特性：常驻（单位在场即挂）。
- 印记：阵营级（槽位 + 触发）。
- buff：纯数据（被读钩子消费）。

**高内聚**：每个原语/时机/状态切片的职责单一，边界清晰。
**低耦合**：新增印记/特性/技能效果 = 加绑定表 + 原语（若新原语），不动引擎核心；来源互不感知。

## 8. 实施步骤

- **S1 骨架**：Effect 原语集 + Hook 枚举 + `emit` 分发器 + 状态切片字段 + 序列化 + 马尔可夫性测试。
- **S2 印记槽与规则**：`apply_mark` 覆盖/叠加/驱散 + 单测（含「每方 1 正 1 负」「异种替换」「可叠加」）。
- **S3 印记效果逐批挂**：先读钩子类（湿润/蓄势/攻击/减速/风起/蓄电）→ 事件钩子类（光合/降灵/棘刺/中毒/星陨/暗涌/龙噬/萌芽）。每批配引擎测试。
- **S4 兼容验证**：特性/技能效果走同一分发器，各系统互不干扰的回归测试。

## 9. 测试与验收

- `apply_mark` 规则表驱动单测（覆盖/叠加/替换/驱散/双方独立）。
- 每个印记的效果单测（如：挂中毒 2 层 → 回合末扣 6% 生命；挂星陨 → 被非幻攻击触发额外幻伤且清层）。
- 马尔可夫性：快照往返后带印记再跑一回合，事件流/hash 不变。
- 与 E0b 现有 213+ 测试兼容：印记槽默认空列表，不改变无印记对局的任何行为。
- 覆盖率：`--data-report` 增加「印记效果覆盖 X/13」。

## 10. 已确认（负责人 2026-08-24）

1. 印记**阵营级**，除非被替代或驱散，否则一直保留。
2. 相同印记**叠加层数**，结算 = 单效果 × 层数。
3. 异种同极性 → 后来顶替旧的。
4. 每方正负各至多 1；**里拉鳐「吟游之弦」独立印记空间**（§2/§4）：
   - 里拉鳐作为施加方 → 印记进独立空间，共存；
   - **双向隔离**：里拉鳐的印记不影响阵营方印记，阵营方印记也不影响里拉鳐的印记；
   - 里拉鳐的印记**只能被带驱散印记效果的技能消除**（scope="all" 驱散），不被顶替、不因换人消失。
5. 当前 13 种全部可叠加；不可叠加印记后期再说（架构已留 `stackable` 开关，§4.2）。
