# S2 三只精灵特性实现（火花·助燃 / 喵喵·氧循环 / 水蓝蓝·浸润）

日期：2026-08-24　状态：已实施（290 测试全绿，91% 覆盖率）　前置：S1 特性基建

## 1. 背景与范围

S1 建好了统一效果架构的最小集（`Effect` 原语 + `Hook` 分发器 + `trait` 标志 + `TraitState`），
但**钩子还没接进引擎**，特性目录是空的。本里程碑 = **S2（引擎接线）+ 第一批三条特性**：

| 精灵 | 特性 | 触发时机 | 效果 | 叠层 |
|---|---|---|---|---|
| 火花 | **助燃** | 使用**火系**技能后 | 双攻（atk + sp_atk）各 +20% | ✅ 每层 +20% |
| 喵喵 | **氧循环** | 使用**草系**技能后 | 回复 max_hp 的 10% | 一次性，每次各回一次 |
| 水蓝蓝 | **浸润** | 使用**水系**技能后 | 全技能能耗 −1 | ✅ 每层 −1 |

三条都走**同一个 Hook（SKILL_RESOLVE）**，区别只在条件（哪一系）与效果原语——
这正是统一效果架构要证明的：来源互不感知，只声明绑定。

## 2. 引擎接线：SKILL_RESOLVE 发射点

`resolve_skill` 在技能结算完（攻击/防御/状态三支）**之后、返回事件之前**发射一次：

```python
emit(state, Hook.SKILL_RESOLVE,
     SimpleNamespace(unit=unit, skill=skill, dealt_counter=dealt_counter,
                     energy_max=state.rules.energy_max),
     trait_defs_for(unit))
```

要点：
- **一次技能恰好一次**：非法槽位在更早就 `return`，不会走到发射；防御白防 / 被应对都照发。
- **ctx 携带 `skill`**：条件谓词据此判断「用了哪一系」；`dealt_counter` 供迪莫类「克制才触发」
  使用（`eff > 1.0`，E2 语义，仅攻击分支计算）。
- **trait_defs_for(unit)**：`unit.trait.name → TRAIT_CATALOG[name]`，无特性 / 未注册 → `[]`（无事发生）。

## 3. 新状态片：全技能能耗减益（浸润）

浸润需要一个**持久、可叠层、非永久、免疫常规驱散**的「能耗−X」状态，与 `StatModifier`
平行地住进 `Unit`：

```python
@dataclass
class EnergyCostMod:
    layers: int
    permanent: bool = False   # 非永久 → 离场清除
    trait: bool = False       # 特性增益 → 免疫常规驱散
    source: str = ""          # 同源定位（叠层合并）
```

- **序列化**：`_unit_to_dict / _unit_from_dict` 增加 `energy_cost_mods`，旧快照 `.get` 兜底 `[]`
  → **Markov 不变式不破坏**（跨进程同 seed digest 一致，见 §7）。
- **唯一读点** `skill_energy_cost(unit, base) = max(0, base − Σ层)`——**门控与支付读同一个函数**，
  保证「付得起才算合法」与「实际扣多少」永远一致：

| 位置 | 改动 |
|---|---|
| [actions.py](src/environment/actions.py) `skill_block_reason` | 能耗门槛改用 `skill_energy_cost` → 浸润叠起来后原本付不起的技能会**合法化** |
| [engine.py](src/environment/engine.py) `resolve_skill` 支付 | `unit.energy -= skill_energy_cost(unit, skill.energy_cost)` |
| [engine.py](src/environment/engine.py) `resolve_switch` | 离场清非永久层（stat_mods + energy_cost_mods），`cleared_layers` 两处都计入 |
| [primitives.py](src/environment/primitives.py) `dispel_gains` | `scope="regular"` 只清 `trait=False` 的能耗层；`scope="all"` 全清 |

## 4. 新原语与新条件

**原语**（[primitives.py](src/environment/primitives.py)，封闭集合新增两个）：

| 原语 | 效果 | 对应特性 |
|---|---|---|
| `apply_energy_cost_mod(unit, *, layers, permanent, trait, source)` | 写入一条能耗减益层（同源合并） | 浸润 |
| `skill_energy_cost(unit, base_cost)` | 读取能耗实际值（夹到 0） | 浸润（读侧） |
| `heal_pct(state, unit, pct, *, source)` | 回复 max_hp 的 pct%（经 `apply_heal` 唯一入口） | 氧循环 |

**条件**（[hooks.py](src/environment/hooks.py) `_CONDITIONS` 注册表新增三个）：

```python
"used_fire":  lambda ctx: _skill_type_is(ctx, "火"),
"used_grass": lambda ctx: _skill_type_is(ctx, "草"),
"used_water": lambda ctx: _skill_type_is(ctx, "水"),
# _skill_type_is(ctx, t) = ctx.skill.type == t
```

与 S1 的 `dealt_counter` 并列——**条件也是声明式的**，特性只写 `cond="used_fire"`。

## 5. 特性定义（[traits.py](src/environment/traits.py)）

```python
DEFAULT_TRAIT_NAME = "default"     # 白板特性（负责人 2026-08-25 指定）

TRAIT_CATALOG = {
    # 白板：零绑定 → emit 遍历它时一个效果都不执行
    "default": TraitDef(name="default", bindings=()),
    "助燃":   TraitDef(..., bindings=(EffectBinding(Hook.SKILL_RESOLVE, cond="used_fire", effects=(
                  Effect("stat_mod", stat="atk",    mode="pct", layers=2, trait=True, permanent=False),
                  Effect("stat_mod", stat="sp_atk", mode="pct", layers=2, trait=True, permanent=False),
              )),),
    "氧循环": TraitDef(..., bindings=(EffectBinding(Hook.SKILL_RESOLVE, cond="used_grass", effects=(
                  Effect("heal_pct", value=10),
              )),),
    "浸润":   TraitDef(..., bindings=(EffectBinding(Hook.SKILL_RESOLVE, cond="used_water", effects=(
                  Effect("energy_cost_mod", layers=1, trait=True, permanent=False),
              )),),
}
```

所有特性增益带 `trait=True`（免疫常规驱散）+ `permanent=False`（离场消失），沿用已确认语义。

### 5.1 白板特性 `default`（未实现特性的占位）

真实数据有 227 个唯一特性，目前实现 3 个。其余精灵一律**装备白板**，于是能正常上场对战、
且对战斗过程零影响：

| 关注点 | 实现 |
|---|---|
| 目录 | `TRAIT_CATALOG["default"] = TraitDef(name="default", bindings=())`——零绑定 |
| 解析（唯一点） | `resolve_trait_name(name)`：`trait_implemented(name)` 为假（未实现 / 空名 / 就是 default）→ `"default"` |
| 装备 | `build_unit` 调 `resolve_trait_name(spec["trait"])` → `Unit.trait = TraitState("default")`。**`Unit.trait` 不再是 None**（旧快照 `trait: null` 仍兼容） |
| 分发 | `trait_defs_for`：查不到的名字回退白板（不炸）；白板零绑定 → `emit` 一个效果都不执行 |
| 可见性 | 不是静默 no-op——`Unit.trait.name == "default"` 一眼可见；`--data-report --data FULL --effects` 打印「特性目录 3/227 已实现 · 真实特性精灵 9 只 · 装白板 584 只」；`--team-report` 每只显示 `特性 最好的伙伴 → 白板「default」（未实现，零效果）` |

> **一次性快照变化**：E0 教学数据 6 只精灵的特性全部未实现 → `Unit.trait.name` 从原名改成
> `"default"`，`to_dict` 内容随之改变，**E0 基线 digest 一次性变更**
> （`ab08a1ed…` → `85bcf6b8…`）。战斗行为本身没变（这些特性以前也不触发），确定性照旧
> （同 seed 复现一致）。roster spec 仍携带**真实特性名**，数据层无信息损失。

## 6. 数据链路：roster → build_unit

- [teambuilder.py](src/environment/teambuilder.py) `build_roster` 新增 `"trait": sp.trait_name`（E0/FULL 都带；
  E0 精灵数据也有 trait 字段，如迪莫 = "最好的伙伴"）。
- [models.py](src/environment/models.py) `build_unit` 读 `spec.get("trait")` → 绑定 `TraitState`；空串 → `None`。
- **惰性保证**：`trait_defs_for` 对未注册特性返回 `[]` → E0 迪莫虽携带"最好的伙伴"但战斗中完全无效果，
  直到 S3 注册它才点亮。

## 7. 验证

```
uv run pytest -q                    # 290 passed（原 266 + 新增 24）
uv run pytest --cov                 # 91% 总；primitives / traits / effects 100%
uv run python -m environment battle --seed 7 --json  # 跨进程 digest 一致（f8a25bc…）
```

新增 [tests/test_environment_traits.py](tests/test_environment_traits.py) 24 个，覆盖四层：

1. **目录/查找**：三特性注册；`trait_defs_for` 空与未知。
2. **emit 分发**：匹配系别才触发；助燃可叠层（2 次 → 4 层）；氧循环每次草系各回一次；
   浸润可叠层且能耗夹 0。
3. **引擎白盒**：`type=火/草/水` 的 Skill 直接构造对局（`execute_turn`）验证发射；
   浸润第二发能耗 3−1=2；换人清非永久能耗层；浸润打开能量门控；特性不破坏马尔可夫性。
4. **序列化/驱散**：`energy_cost_mods` 往返；旧快照兜底 `[]`；驱散 scope 覆盖能耗层。

## 8. 三个设计判断（待负责人确认）

| # | 判断 | 我的实现 |
|---|---|---|
| 1 | **氧循环不回"层"** | 回血是一次性的，每次草系各回 10% 一次，不累加层数（叠层语义对一次性回血无意义）。若要"第 N 次草系回 N×10%"的叠层口径，需加计数器。 |
| 2 | **浸润能耗夹到 0** | −1 叠到底为 0（免费放招），不为负。 |
| 3 | **双攻 = atk + sp_atk** | 各 2 层 pct（20%），按规则"单次描述值 × 层数"结算。 |

## 9. 边界与下一步

- **E0 教学数据**：默认 E0 对局技能全为普通系 → 三条特性在**教学对战**里不触发（数据限制，非接线问题）。
- **P1 真实对战已点亮**（2026-08-25 起）：`battle --data FULL --preset p1` 用 P1 批次技能（火/草/水系真实类型）
  → 助燃 / 氧循环 / 浸润在四家族真数据对战中**实际触发**（火花·火焰切割→助燃叠层、喵喵·叶绿光束→氧循环回血等）。
- **迪莫「最好的伙伴」✅ 已实现（S3，2026-08-25）**：注册进 `TRAIT_CATALOG`（SKILL_RESOLVE +
  `dealt_counter` 条件）——造成克制伤害后，攻防速（物攻/魔攻/物防/魔防/速度 5 维）+20% +
  回复 2 能量，非永久可叠层。`resolve_trait_name` 自动把迪莫从白板改绑真实特性，无需改数据。
- **下一步候选**：S3 挂迪莫 / S4 泛化（ENTER/EXIT/STAT_CALC 等钩子接入其他批次特性）；特性触发的事件可观测性
  （`trait_trigger` 事件 + E4 `EVENT_VISIBILITY` 注册）留待印记系统时一并设计。
