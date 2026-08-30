"""Trigger（v3 骨架·事件驱动层）：看到 DomainEvent → 返回新 Atom（不直接改状态）。

v3 核心（`mydocs/battle_docs.md` §6）：Trigger 是**纯收集器**——`collect_reactions`
只返回新 Atom，由引擎（Reducer）执行。特性效果从「Hook 直接改状态」迁移到
「事件 → Trigger 返回 TraitGain Atom → Reducer 写 trait.gains」；骨架阶段保持行为
逐位等价（tests/test_v3_sentinel.py 把关）。

特性收集时机（2026-08-30 扩展）：
- `SkillResolved` → 施法者特性（skill_resolve）；冰系技能计数（结晶水）；
- `StatModChanged` → 施法者特性（status_applied，source 守卫防循环）；
- `UnitEntered` → 入场精灵特性（enter）+ 结晶水入场回能；
- `UnitExited` → 离场精灵特性（exit，吉利丁片对入场精灵施增益）。
`pipeline.run` 是唯一调用方；`collect_reactions` 不依赖 state（只依赖 unit 与事件），
测试可 state=None 调用。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

from .atom import AddModifier, ApplyMark, GainEnergy, TraitGain
from .domain import (SkillResolved, StatModChanged, UnitEntered, UnitExited)
from .primitives import apply_energy_gain, heal_pct
from .weather import WEATHER_SOURCE

if TYPE_CHECKING:
    from .atom import Atom
    from .models import BattleState, Unit


def _unit_by_id(state: "BattleState", unit_id: str) -> "Unit | None":
    for s in ("a", "b"):
        for u in state.side(s).units:
            if u.id == unit_id:
                return u
    return None


def collect_reactions(state, event, unit: "Unit" | None = None, trait_defs=None,
                      energy_max: int = 10) -> list["Atom"]:
    """输入：DomainEvent + 施法者（+ 可选特性静态定义）；输出：新 Atom 列表。

    **多源收集**：特性（SKILL_RESOLVE / STATUS_APPLIED / ENTER / EXIT）→ DOT（statuses）
    → 印记（marks）→ 天气（weather）——TURN_END 固定序 = DOT → 印记 → 天气
    （2026-08-30 拍板：保持印记先于天气，DOT 插到最前）。`unit` 可为 None
    （TURN_END 等无单一施法者的事件）。
    """
    atoms: list["Atom"] = []
    if isinstance(event, (SkillResolved, StatModChanged)) and unit is not None:
        atoms += _trait_atoms(state, event, unit, trait_defs, energy_max)
        if isinstance(event, SkillResolved) and state is not None:
            atoms += _record_ice_skill(state, event, unit)
    if state is not None and isinstance(event, (UnitEntered, UnitExited)):
        ent = _unit_by_id(state, event.unit_id)
        if ent is not None:
            atoms += _trait_atoms(state, event, ent, None, energy_max)
            if isinstance(event, UnitEntered):
                atoms += _crystal_water_gain(state, ent, energy_max)
                atoms += _guardian_cost(state, ent)
            else:
                atoms += _welcome_morph(state, event)
    if state is not None:
        from .marks import collect as collect_marks
        from .statuses import collect as collect_statuses
        from .weather import collect as collect_weather

        atoms += collect_statuses(state, event)
        atoms += collect_marks(state, event)
        atoms += collect_weather(state, event)
    return atoms


def _record_ice_skill(state: "BattleState", event: SkillResolved,
                      unit: "Unit") -> list["Atom"]:
    """结晶水计数（2026-08-30）：本场战斗己方阵营使用冰系技能的次数。"""
    from .models import side_of

    if _skill_type(unit, event.skill) != "冰":
        return []
    side = side_of(state, unit)
    state.ice_skills_used[side] = state.ice_skills_used.get(side, 0) + 1
    return []


def _crystal_water_gain(state: "BattleState", unit: "Unit",
                        energy_max: int) -> list["Atom"]:
    """结晶水入场回能（2026-08-30）：回 3×（入场前己方使用冰系技能次数）。"""
    from .models import side_of

    if unit.trait is None or unit.trait.name != "结晶水":
        return []
    side = side_of(state, unit)
    count = state.ice_skills_used.get(side, 0)
    if count <= 0:
        return []
    return [GainEnergy(side=side, unit=unit, amount=3 * count, source="结晶水", target="self")]


def _guardian_cost(state: "BattleState", unit: "Unit") -> list["Atom"]:
    """守护者（2026-08-30）：己方其他精灵每有 1 层萌化，自己入场时全技能能耗 −1。"""
    from .models import side_of
    from .statuses import morph_layers

    if unit.trait is None or unit.trait.name != "守护者":
        return []
    side = side_of(state, unit)
    total = sum(morph_layers(u) for u in state.side(side).units if u is not unit)
    if total <= 0:
        return []
    return [AddModifier(side=side, unit=unit, stat="energy_cost", mode="flat",
                        layers=-total, source="守护者")]


def _welcome_morph(state: "BattleState", event: UnitExited) -> list["Atom"]:
    """迎宾（2026-08-30）：自己或其他精灵离场时，更换入场的精灵获得萌化。

    口径：离场精灵所在阵营有存活「迎宾」持有者 → 入场精灵施 1 层萌化
    （最低阶拦截走 AddModifier reducer 漏斗）。"""
    from .models import side_of

    exited = _unit_by_id(state, event.unit_id)
    incoming_id = getattr(event, "incoming_id", "")
    if exited is None or not incoming_id:
        return []
    side = side_of(state, exited)
    holder = any(u.trait is not None and u.trait.name == "迎宾" and not u.fainted
                 for u in state.side(side).units)
    if not holder:
        return []
    incoming = _unit_by_id(state, incoming_id)
    if incoming is None:
        return []
    return [AddModifier(side=side, unit=incoming, stat="萌化", mode="special",
                        layers=1, source="迎宾", target="self")]


def _trait_atoms(state, event, unit: "Unit", trait_defs, energy_max: int) -> list["Atom"]:
    from .hooks import _cond_matches

    if trait_defs is None:
        from .traits import trait_defs_for
        trait_defs = trait_defs_for(unit)
    etype = type(event).__name__
    if etype == "SkillResolved":
        skill_type = event.skill_type or _skill_type(unit, event.skill)
        ctx = SimpleNamespace(unit=unit, skill=SimpleNamespace(type=skill_type),
                              dealt_counter=event.dealt_counter, energy_max=energy_max,
                              event=event, countered=getattr(event, "countered", False))
        hook_value = "skill_resolve"
    elif etype == "StatModChanged":
        ctx = SimpleNamespace(unit=unit, energy_max=energy_max, event=event)
        hook_value = "status_applied"
    elif etype == "UnitEntered":
        ctx = SimpleNamespace(unit=unit, energy_max=energy_max, event=event)
        hook_value = "enter"
    elif etype == "UnitExited":
        ctx = SimpleNamespace(unit=unit, energy_max=energy_max, event=event)
        hook_value = "exit"
    else:
        return []
    atoms: list["Atom"] = []
    for tdef in trait_defs:
        # source 守卫（2026-08-30）：① 特性自身施加的状态不再触发自身（防循环）；
        # ② 天气（全局来源，非该精灵直接造成）施加的冻结/引电不触发「自己直接造成」
        # 类特性（捉迷藏/加个雪球/抓到你了）——天气冻结 ≠ 该精灵直接造成的冻结。
        if getattr(event, "source", "") in (tdef.name, WEATHER_SOURCE):
            continue
        for binding in tdef.bindings:
            if binding.hook != hook_value or not _cond_matches(binding.cond, ctx):
                continue
            for effect in binding.effects:
                atoms.extend(_effect_to_atoms(state, unit, event, effect, tdef.name,
                                              energy_max))
    return atoms


def _effect_to_atoms(state, unit: "Unit", event, effect, source: str,
                     energy_max: int) -> list["Atom"]:
    """一条 Effect → Atom 列表（stat_mod / energy_cost_mod → TraitGain；资源类即时执行；
    foe_status → 对敌方在场施状态；enter_stat_mod → 对入场精灵 TraitGain（吉利丁片）；
    snowball_record / star_meteor_mark → 冻结批 L3 特殊 op，2026-08-30）。"""
    if effect.op == "stat_mod":
        return [TraitGain(unit=unit, stat=effect.stat, mode=effect.mode,
                          layers=effect.layers, source=source, permanent=effect.permanent)]
    if effect.op == "energy_cost_mod":
        return [TraitGain(unit=unit, stat="energy_cost", mode="flat",
                          layers=effect.layers, source=source, permanent=effect.permanent)]
    if effect.op == "foe_status":
        if state is None:
            return []
        from .models import side_of
        from .statuses import STATUS_TABLE, status_kwargs

        side = side_of(state, unit)
        foe_side = "b" if side == "a" else "a"
        foe = state.active(foe_side)
        if foe is None or foe.fainted:
            return []
        return [AddModifier(side=foe_side, unit=foe, stat=effect.stat,
                            mode=STATUS_TABLE[effect.stat][0], layers=effect.layers,
                            source=source, target="foe",
                            kwargs=status_kwargs(effect.stat))]
    if effect.op == "foe_energy_cost_mod":
        # 捉迷藏/抓到你了（2026-08-30）：敌方获得冻结时 → 敌方全技能能耗 +N
        if state is None:
            return []
        from .models import side_of

        side = side_of(state, unit)
        foe_side = "b" if side == "a" else "a"
        foe = state.active(foe_side)
        if foe is None or foe.fainted:
            return []
        return [AddModifier(side=foe_side, unit=foe, stat="energy_cost", mode="flat",
                            layers=effect.layers, source=source, target="foe")]
    if effect.op == "enter_stat_mod":
        # 吉利丁片（2026-08-30）：离场 → 对更换入场的精灵 TraitGain（含「免疫冻结」标记）
        incoming_id = getattr(event, "incoming_id", "")
        incoming = _unit_by_id(state, incoming_id) if state is not None and incoming_id else None
        if incoming is None:
            return []
        return [TraitGain(unit=incoming, stat=effect.stat, mode=effect.mode,
                          layers=effect.layers, source=source, permanent=effect.permanent)]
    if effect.op == "snowball_record":
        # 大雪球（2026-08-30）：使用 2 次不同的冰系技能 → 敌方 +4 层冻结并重置
        if state is None or unit.trait is None:
            return []
        from .models import side_of
        from .statuses import STATUS_TABLE, status_kwargs

        used = set(unit.trait.kwargs.get("used_skills", []))
        used.add(getattr(event, "skill", ""))
        if len(used) >= 2:
            unit.trait.kwargs["used_skills"] = []
            side = side_of(state, unit)
            foe_side = "b" if side == "a" else "a"
            foe = state.active(foe_side)
            if foe is None or foe.fainted:
                return []
            return [AddModifier(side=foe_side, unit=foe, stat="冻结",
                                mode=STATUS_TABLE["冻结"][0], layers=4,
                                source=source, target="foe",
                                kwargs=status_kwargs("冻结"))]
        unit.trait.kwargs["used_skills"] = sorted(used)
        return []
    if effect.op == "star_meteor_mark":
        # 月牙雪糕（2026-08-30）：使用攻击技能时，敌方每有 1 层冻结 → 施 1 层星陨印记
        if state is None:
            return []
        from .models import side_of
        from .statuses import freeze_layers

        side = side_of(state, unit)
        foe_side = "b" if side == "a" else "a"
        foe = state.active(foe_side)
        if foe is None or foe.fainted:
            return []
        n = freeze_layers(foe)
        if n <= 0:
            return []
        return [ApplyMark(side=foe_side, name="星陨印记", layers=n, source=source)]
    # 资源类（energy_gain / heal_pct）：即时执行（旧 emit 同语义，不产生展示事件）
    if effect.op == "energy_gain":
        apply_energy_gain(unit, effect.value, energy_max=energy_max)
    elif effect.op == "heal_pct":
        heal_pct(state=None, unit=unit, pct=int(effect.value), source=source)
    return []


def _skill_type(unit: "Unit", skill_name: str) -> str:
    """技能系别（Trigger 条件 used_fire/used_grass/used_water 读它）。"""
    for s in unit.current_skills:
        if s.name == skill_name:
            return s.type
    return ""
