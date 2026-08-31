# 精灵特性系统设计方案（以迪莫为例）

日期：2026-08-24　状态：方案文档（待负责人确认后并入统一效果架构实施）

## 1. 背景与目标

实现精灵特性（trait）系统。特性是精灵的常驻被动效果，触发时机多样（造成克制伤害后、入场时、回合结束时…）。本方案：

1. 定义**特性增益**与**常规增益**的语义与区分。
2. 设计特性系统架构，复用「统一效果架构」（Effect 原语 + Hook 分发器，见 `mark-system-plan.md`）。
3. 以**迪莫「最好的伙伴」**为例，演示如何用该架构实现一个「非永久、可叠加」的特性增益。

## 2. 特性增益的语义（已确认）

| 维度 | 语义 |
|---|---|
| **特性增益 vs 常规增益** | 特性描述里的增益效果 = **特性增益**；技能描述里的增益效果 = **常规增益**。 |
| **驱散免疫** | 特性增益**不受**常规「驱散增益」技能影响；常规增益则会被驱散。 |
| **永久 / 非永久** | 特性增益分永久与非永久；**非永久增益随精灵离场而消失**，永久则保留。 |
| **叠层** | 多次触发特性效果可叠层（前提：该特性可叠加），结算 = **单特性描述加成值 × 层数**。 |

### 迪莫「最好的伙伴」

> desc：**造成克制伤害后，获得攻防速+20%，并回复2能量。**

- 触发：**造成克制伤害后**（依赖克制表 → E2 类型克制）。
- 增益：攻防速+20%（**非永久**、**可叠加**）+ 回复2能量（即时，不叠层）。
- 两次触发 → 攻防速+40%（2 层 × +20%）+ 共 +4 能量；离场 → 攻防速层数清空，能量不存（已消费）。

## 3. 统一架构中的特性系统

特性不是独立系统——它是「统一效果架构」里的**一个来源类型**（与技能、印记并列）：

```
来源（声明式绑定）→ 调度（Hook 分发器）→ 原语（单一职责）→ 状态（纯数据）
   技能：SkillInstance.effects     emit(hook, ctx)     stat_mod / energy_gain    Unit.stat_mods
   印记：MarkDef.bindings                              dispel / ...
   特性：TraitDef.bindings  ← 本方案                              ↑ 加 trait 标志
```

### 3.1 TraitDef（静态目录，声明式）

```python
@dataclass(frozen=True)
class TraitDef:
    name: str
    bindings: tuple[EffectBinding, ...]   # hook → effects（与技能/印记同一绑定表）
    stackable: bool = True                # 多次触发是否叠层（迪莫 = True）
    once_per_battle: bool = False         # 每场战斗 1 次（如不死鸟）

TRAIT_CATALOG: dict[str, TraitDef] = { ... }   # 227 个唯一特性（按名，见 trait-batches/）
```

**特性增益的「永久/非永久」由 Effect 原语的参数决定**，不写在 TraitDef 上：
`Effect(op="stat_mod", permanent=False, trait=True, ...)`。

### 3.2 特性的叠加与一次性

- **可叠加**：每次触发 `stat_mod` 原语**追加一层**（同源同维记录），`aggregate_stats` 求和 → 单值 × 层数。
- **不可叠加**：再次触发时**同源刷新**（找 `trait=True and source==特性名` 的记录，layers 重置），不叠层。
- **once_per_battle**：`TraitState.used_once` 标志，首次触发后置位；随 `to_dict` 序列化。

## 4. StatModifier 扩展：`trait` 标志（特性增益的载体）

现有 `Unit.stat_mods` 是唯一的增减益存储，特性增益与常规增益**共用它**，靠标志区分：

```python
@dataclass
class StatModifier:
    stat: str
    mode: str            # pct / flat
    layers: int
    permanent: bool = False   # 离场是否保留（False = 离场消失）——已存在
    source: str = ""          # 来源标签（技能名 / 特性名）
    trait: bool = False       # ★ NEW：True = 特性增益（不受常规驱散；用于同源刷新定位）
```

- `aggregate_stats` 一律折叠（特性/常规同等参与计算）——**一条聚合路径**，高内聚。
- **离场清除**（现有 `resolve_switch` 清非永久）：扩展到所有离场原因（换人/脱离/返场），按 `permanent=False` 清——特性非永久增益自然消失。
- **驱散区分**：`dispel` 原语带 scope：
  ```python
  def dispel_gains(unit, scope="regular"):
      # scope="regular"（默认）：移除 trait=False 的常规增益
      # scope="all"：同时移除特性增益（假设的「驱散特性增益」技能）
  ```
  常规「驱散增益」技能 → `scope="regular"`，特性增益免疫。

**为什么用标志而不是独立列表**：特性/常规增益在计算上无差别（都进 `aggregate_stats`），区别只在「驱散豁免」与「来源定位」——一个布尔 + 一个来源标签足够。独立列表会分裂聚合路径，违背高内聚。

## 5. 迪莫「最好的伙伴」实现

### 5.1 绑定表（声明式）

```python
TRAIT_CATALOG["最好的伙伴"] = TraitDef(
    name="最好的伙伴",
    stackable=True,                       # 可叠加
    bindings=(
        EffectBinding(
            hook=Hook.DEAL_DAMAGE,        # 造成伤害后
            cond="克制伤害",               # 条件：本次伤害是克制伤害（读钩子判定）
            effects=(
                Effect("stat_mod", "self", stat="atk",    mode="pct", layers=1,
                       trait=True, permanent=False),      # 攻 +20%·特性·非永久
                Effect("stat_mod", "self", stat="sp_atk", mode="pct", layers=1,
                       trait=True, permanent=False),      # 攻 +20%
                Effect("stat_mod", "self", stat="speed",  mode="pct", layers=1,
                       trait=True, permanent=False),      # 速 +20%
                Effect("energy_gain", "self", value=2),  # 回复2能量（即时）
            ),
        ),
    ),
)
```

> 注：`攻防速` 的映射（物攻/物防/速度 三围，或含魔攻的更多维度）待与负责人核对游戏原意；此处以「攻 + 速」示例，映射改一处即可。

### 5.2 触发流程

1. 迪莫用技能造成伤害 → 引擎 `DEAL_DAMAGE` 钩子触发。
2. 分发器收集来源：迪莫的 `TraitState` → 命中「最好的伙伴」绑定。
3. 条件「克制伤害」：读钩子判定本次伤害是否为克制（依赖 E2 克制表）。
4. 命中 → 依次执行 effects：
   - 4 个 `stat_mod` → 在 `Unit.stat_mods` 追加/叠加 4 条 `trait=True, permanent=False` 层（攻/魔攻/速各 +1 层）。
   - 1 个 `energy_gain` → `unit.energy += 2`（夹到上限）。
5. **第二次克制伤害** → 再叠 1 层 → `aggregate_stats` 里 攻/魔攻/速 = 基础 × (1 + 20%×2) = +40%。
6. **迪莫离场**（换人/脱离）→ `on_exit` 清 `permanent=False` 层 → 攻防速层消失，特性增益归零。
7. **敌方「驱散增益」** → `dispel_gains(scope="regular")` → 只清 `trait=False` 的常规增益，迪莫的攻防速层**保留**。

## 6. 引擎改动点

1. **`StatModifier` 加 `trait: bool`**（默认 False，向后兼容）。
2. **`Unit` 加 `trait: TraitState | None`**（build_unit 时从精灵表绑定特性）。
3. **`TRAIT_CATALOG` 静态目录**（227 个，按 `trait-batches/` 分批挂）。
4. **Hook 分发器**（复用 mark 方案）：`emit` 收集在场双方特性绑定。
5. **读钩子**：`DEAL_DAMAGE` 的「克制伤害」条件、`STAT_CALC`、`MARK_SPACE` 等。
6. **`dispel` 原语**：scope 参数（regular/all）。
7. **`on_exit` 清非永久**：`resolve_switch` 已有，扩展到 `apply_replacement`（阵亡补位不涉及离场层，但要统一）与将来的 `retreat`/`脱离`。
8. **序列化**：`TraitState` + `trait` 标志随 `to_dict`/`from_dict` 往返，马尔可夫性保持。

## 7. 兼容性：特性 / 印记 / 技能 / 增减益

| 系统 | 来源 | 产生的状态 |
|---|---|---|
| 技能 | `SkillInstance.effects`（主动） | 常规增益（`trait=False`）+ 即时效果 |
| 特性 | `TraitDef.bindings`（常驻被动） | 特性增益（`trait=True`）+ 即时效果 |
| 印记 | `MarkDef.bindings`（阵营级） | 读钩子修正 + 即时效果（增益默认 `trait=False`） |
| 增减益 | —— | 全部进 `Unit.stat_mods`，一条聚合路径 |

**共用**：Effect 原语、Hook 时机、分发器、`Unit.stat_mods` 存储、`aggregate_stats`。
**差异**：只有触发方式（主动/常驻/阵营级）和来源标志（`trait`）。

## 8. 测试与验收

- 语义单测：特性增益免疫常规驱散（挂常规增益 + 特性增益 → 驱散常规 → 特性保留）。
- 叠层单测：两次触发 → 攻防速 +40%、能量 +4（迪莫）。
- 非永久单测：迪莫离场 → 攻防速层清空；permanent 特性增益 → 保留。
- 不可叠加特性：二次触发刷新不叠层。
- once_per_battle：不死鸟保命后 `used_once=True`。
- 马尔可夫性：带特性增益快照往返，事件流/hash 不变。
- 与 E0b 现有测试兼容：`trait` 标志默认 False，常规增益行为不变。

## 9. 依赖与边界

- **迪莫特性依赖 E2 克制表**（「造成克制伤害后」）——克制表未落地前，该特性不可用（登记为「依赖克制表」）；其余无克制依赖的特性可先行。
- **攻防速的精确维度**待核对。
- **特性增益的驱散**：常规驱散技能只清常规增益；若将来出现「驱散特性增益」技能，走 `scope="all"`。
- **印记来源的增益**默认按常规增益（`trait=False`）处理——若需与特性增益同样豁免驱散，届时加标志即可，架构已留口。

## 10. 实施步骤

- **S1**：`StatModifier.trait` 标志 + `dispel_gains(scope)` + 序列化。
- **S2**：`TraitDef` 目录 + `Unit.trait` 绑定 + 分发器接入 DEAL_DAMAGE / ENTER / TURN_END 等钩子。
- **S3**：按 `trait-batches/` 分批挂特性，先无克制依赖的（能量/回复/回合末类）。
- **S4**：E2 克制表落地后，接上「克制伤害」类特性（迪莫等 6 个克制后触发特性）。
