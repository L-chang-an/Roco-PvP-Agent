"""Trigger（v3 骨架·事件驱动层）：看到 DomainEvent → 返回新 Atom（不直接改状态）。

v3 核心（`mydocs/battle_docs.md` §6）：Trigger 是**纯收集器**——`collect_reactions`
只返回新 Atom，由引擎（Reducer）执行。特性效果从「Hook 直接改状态」迁移到
「事件 → Trigger 返回 TraitGain Atom → Reducer 写 trait.gains」；骨架阶段保持行为
逐位等价（tests/test_v3_sentinel.py 把关）。

`hooks.emit` 现在委托本模块（Phase 3）：emit 构造 SkillResolved 事件 → collect_reactions
→ reduce_all，特性效果与旧直写路径逐位一致。`collect_reactions` 不依赖 state（只依赖
unit 与事件），测试可 state=None 调用。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

from .atom import TraitGain
from .domain import SkillResolved
from .primitives import apply_energy_gain, heal_pct

if TYPE_CHECKING:
    from .atom import Atom
    from .models import Unit


def collect_reactions(state, event, unit: "Unit", trait_defs=None,
                      energy_max: int = 10) -> list["Atom"]:
    """输入：DomainEvent + 施法者（+ 可选特性静态定义）；输出：新 Atom 列表。

    骨架阶段只接 SKILL_RESOLVE（SkillResolved 事件）→ 特性绑定。cond 匹配沿用
    hooks._CONDITIONS（dealt_counter / used_fire / used_grass / used_water）。
    `state` 可为 None（特性效果不依赖 state；测试 emit 直调时传 None）。
    """
    if not isinstance(event, SkillResolved):
        return []
    from .hooks import _cond_matches

    if trait_defs is None:
        from .traits import trait_defs_for
        trait_defs = trait_defs_for(unit)
    skill_type = event.skill_type or _skill_type(unit, event.skill)
    ctx = SimpleNamespace(unit=unit, skill=SimpleNamespace(type=skill_type),
                          dealt_counter=event.dealt_counter, energy_max=energy_max)
    atoms: list["Atom"] = []
    for tdef in trait_defs:
        for binding in tdef.bindings:
            if binding.hook != "skill_resolve" or not _cond_matches(binding.cond, ctx):
                continue
            for effect in binding.effects:
                atoms.extend(_effect_to_atoms(unit, effect, tdef.name, energy_max))
    return atoms


def _effect_to_atoms(unit: "Unit", effect, source: str, energy_max: int) -> list["Atom"]:
    """一条 Effect → Atom 列表（stat_mod / energy_cost_mod → TraitGain；资源类即时执行）。"""
    if effect.op == "stat_mod":
        return [TraitGain(unit=unit, stat=effect.stat, mode=effect.mode,
                          layers=effect.layers, source=source, permanent=effect.permanent)]
    if effect.op == "energy_cost_mod":
        return [TraitGain(unit=unit, stat="energy_cost", mode="flat",
                          layers=effect.layers, source=source, permanent=effect.permanent)]
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
