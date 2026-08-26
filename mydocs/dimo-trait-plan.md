# 迪莫特性「最好的伙伴」实现 Plan（基于 E2）

日期：2026-08-24　状态：✅ 已实现（2026-08-25 注册进 TRAIT_CATALOG，语义点全部确认）

## 1. 背景：依赖链已打通

迪莫特性 `最好的伙伴`：**造成克制伤害后，获得攻防速+20%，并回复2能量。**

依赖链（前序里程碑已交付）：
- **E2 克制表** ✅：`types.type_effectiveness(atk_type, def_types) > 1.0` 即「克制伤害」；`damage` 事件已携带 `eff`/`stab` 字段——**判定「克制」无需重算**，直接读事件。
- **统一效果架构**（mark-system-plan.md 方案）：Effect 原语 + Hook 分发器 + 状态切片——特性是其中一个来源类型。
- **特性系统架构**（trait-system-plan.md 方案）：特性增益 vs 常规增益、非永久离场消失、可叠层、驱散免疫。

本方案 = 把上面架构**落到迪莫这一条特性**的最小实现路径，设计成可泛化到全部 227 个特性。

## 2. 特性语义落点（已确认，回顾）

| 语义 | 载体 |
|---|---|
| 特性增益免疫常规驱散 | `StatModifier.trait=True` + `dispel_gains(scope="regular")` 只清 `trait=False` |
| 非永久离场消失 | `StatModifier.permanent=False` + `on_exit` 清非永久层 |
| 可叠层 | 每次触发追加一层，`aggregate_stats` 求和 = 单值×层数 |
| 即时效果（能量） | `energy_gain` 原语，触发即执行、不叠层 |

## 3. 「造成克制伤害后」的精确判定（已确认）

用 E2 已有能力，**零重算**：

```python
# damage 事件已带 eff（resolve_skill 在 E2 已算好）
# 「克制伤害」 = eff > 1.0（已确认：克制系数大于 1 才算克制；eff==1.0 的中性/互抵不算）
# 触发频率（已确认）：一次技能攻击算一次，连击不算 → 用 SKILL_RESOLVE 钩子（按技能结算一次，
#   聚合该技能全部命中），而非 DEAL_DAMAGE（逐发）。
```

- **触发钩子**：`SKILL_RESOLVE`（技能结算后，**每个攻击技能恰好一次**）。
- **条件**：`ctx.dealt_counter == True`——本次技能攻击的伤害里**至少有一发** `eff > 1.0`。
- **连击不算多次**：连击技能（将来 P2 有）多发命中，`dealt_counter` 聚合整次技能——命中 3 发全是克制也只触发一次。
- **非攻击技能**：状态/防御技能不造成伤害 → `dealt_counter=False`，不触发。
- **触发者**：攻击方 = 迪莫（特性绑定的单位）。

## 4. 所需引擎基建（在 E2 之上新增的最小集）

E2 已给 `types.py` + damage 事件 `eff`。迪莫特性还需要：

| 新增 | 位置 | 用途 |
|---|---|---|
| `StatModifier.trait: bool` | models.py | 特性增益标记（驱散豁免 + 同源刷新定位） |
| `Unit.trait: TraitState | None` | models.py | 绑定特性（build_unit 时从精灵表读） |
| `TraitDef` + `TRAIT_CATALOG` | 新 `traits.py` | 特性静态目录（bindings/stackable/once） |
| `TraitState` | models.py | 每单位特性实例（once 标志、叠层计数） |
| Hook 分发器 `emit` | 新 `hooks.py` | `SKILL_RESOLVE` / `STAT_CALC` / `EXIT` 三个钩子（最小集） |
| `dispel_gains(scope)` | 新 `primitives.py` | 驱散区分常规/特性增益 |
| `on_exit` 清非永久层 | engine.py | 换人/脱离时清 `permanent=False`（现有 resolve_switch 已清，扩展统一） |

**设计要点**：钩子最小集只开「迪莫这条特性需要」的三个（SKILL_RESOLVE / STAT_CALC / EXIT），但接口与 mark-plan 的完整 Hook 枚举一致——后续特性/印记按需加钩子，不改骨架。`SKILL_RESOLVE` 在 `resolve_skill` 结算后 emit 一次，ctx 聚合该技能全部命中（`dealt_counter`），天然满足「一次技能算一次、连击不算」。

## 5. 迪莫「最好的伙伴」精确定义（已确认）

**攻防速 = 物攻+魔攻+物防+魔防+速度（5 维，不含生命）**。每次技能攻击若造成克制伤害 → 五维各 +1 层（+20%×层）+ 能量 +2。

```python
# traits.py
TRAIT_CATALOG["最好的伙伴"] = TraitDef(
    name="最好的伙伴",
    stackable=True,                          # 可叠加
    once_per_battle=False,
    bindings=(
        EffectBinding(
            hook="SKILL_RESOLVE",            # 每个攻击技能结算后恰好一次
            cond="dealt_counter",            # 本次技能至少一发 eff > 1.0
            effects=(
                # 攻防速+20%（5 维）：特性增益、非永久、每层 1
                Effect("stat_mod", "self", stat="atk",    mode="pct", layers=1, trait=True, permanent=False),
                Effect("stat_mod", "self", stat="sp_atk", mode="pct", layers=1, trait=True, permanent=False),
                Effect("stat_mod", "self", stat="def",    mode="pct", layers=1, trait=True, permanent=False),
                Effect("stat_mod", "self", stat="sp_def", mode="pct", layers=1, trait=True, permanent=False),
                Effect("stat_mod", "self", stat="speed",  mode="pct", layers=1, trait=True, permanent=False),
                # 回复2能量：即时
                Effect("energy_gain", "self", value=2),
            ),
        ),
    ),
)
```

**触发流程**（迪莫用攻击技能打出一记克制伤害）：
1. E2 的 `resolve_skill` 算好每发 `eff`，发出 `damage` 事件（已含 `eff`）。
2. 引擎在 `resolve_skill` 结算后 `emit(Hook.SKILL_RESOLVE, ctx)` 一次，ctx 携带 attacker/defender/skill 与 **`dealt_counter`**（本次技能是否至少一发 eff>1.0；连击聚合，不算多次）。
3. 分发器收集迪莫的 `TraitState` → 命中「最好的伙伴」绑定，`cond="dealt_counter"` 通过。
4. 依次执行 effects：
   - 5 个 `stat_mod` → 迪莫 `stat_mods` 追加 5 条 `trait=True, permanent=False` 层（五维各 +1 层）。
   - 1 个 `energy_gain` → `unit.energy = min(energy_max, energy+2)`。
5. **第二次克制技能攻击** → 再叠 1 层 → `aggregate_stats`：五维 = 基础 × (1 + 20%×2) = +40%。
6. **迪莫离场**（换人/脱离）→ `on_exit` 清 `permanent=False` 层 → 五维归零。
7. **敌方「驱散增益」** → `dispel_gains(scope="regular")` → 只清 `trait=False`，迪莫五维层保留。

## 6. 实施步骤（每步可测）

- **S1 基建**：`StatModifier.trait` + `TraitDef`/`TraitState` + `TRAIT_CATALOG` + Hook 分发器（`SKILL_RESOLVE`/`STAT_CALC`/`EXIT`）+ `dispel_gains(scope)` + 序列化。测试：trait 标志往返、dispel scope。
- **S2 绑定**：`build_unit` 从精灵表读 trait → `Unit.trait`；`SKILL_RESOLVE` 钩子接进 `resolve_skill` 结算后（聚合 `dealt_counter`）；`STAT_CALC` 接进 `aggregate_stats`；`EXIT` 接进 `resolve_switch`/`apply_replacement`。测试：迪莫上场即有 trait。
- **S3 迪莫特性生效**：挂入「最好的伙伴」绑定。测试（见 §7）。
- **S4 泛化检查**：确认同一套绑定可表达其余克制类特性（裁决/点燃/滋养/净化，6 条「造成克制伤害后」同族特性）——一行绑定的差异。

## 7. 测试清单

- **克制判定（已确认口径）**：一次技能攻击 `eff > 1.0` → 触发；`eff == 1.0`（中性/互抵）→ 不触发；`eff < 1.0`（抵抗）→ 不触发。
- **触发频率（已确认）**：一次技能攻击算一次——连击（多命中全克制）只触发一次；非攻击技能不触发。
- **叠层**：两次克制技能攻击 → 五维 +40%；非克制攻击不触发。
- **五维**：攻/魔攻/物防/魔防/速度 各 +20%/层；生命不变。
- **能量**：每次触发 +2，夹到 energy_max。
- **非永久**：迪莫换人 → 五维层清空；permanent=True 的对比特性保留。
- **驱散免疫**：挂常规增益 + 特性增益 → 驱散常规 → 特性保留。
- **与 E2 联动**：火血脉迪莫（types=[火]）打草系精灵 → 克制触发特性；打光系 → 不触发。
- **确定性**：带特性叠层的快照 to_dict→from_dict 往返，事件流/hash 不变。
- **回归**：E0 无特性精灵（stat_mods 无 trait 标记）行为不变，全套 256+ 测试绿。

## 8. 已确认（负责人 2026-08-24）

1. **「攻防速」** = 物攻 + 魔攻 + 物防 + 魔防 + 速度（5 维，不含生命）。
2. **触发频率** = 一次技能攻击算一次；连击不算（每技能结算一次，聚合全部命中）。
3. **克制口径** = 克制系数 > 1 才算克制（`eff > 1.0`）；中性/互抵（=1.0）与抵抗（<1.0）不算。
