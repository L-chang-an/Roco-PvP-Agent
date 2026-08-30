"""技能编译器（v3 骨架·编译层）：把 SkillEffect（旧大字段袋）展开成有序 Atom 列表。

`compile_skill` 是 `resolve_skill` 的「声明化」：旧逻辑在 engine 里用 20 个字段各一个
if 分派，这里把**同一份语义**变成显式 Atom 序列（SpendEnergy → 伤害段 / 状态层 → 资源
效果 → 特性触发点）。Reducer 逐个执行，事件流/state_hash 与旧引擎逐位一致
（`tests/test_v3_sentinel.py` 把关）。

`dealt_counter`（克制伤害标志，特性「最好的伙伴」用）由 Reducer 在伤害段累计进 frame，
resolve_skill 在 Atom 全部执行后读它触发特性。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .atom import (
    AddModifier, ApplyMark, BenchEnergy, DealDamage, FoeCostGain, GainEnergy,
    HealPct, Lifesteal, RevealSkill, SetCooldown, SetModLayers, SetWeather,
    SpendEnergy, StealEnergy,
)
from .modifiers import effectiveness as eff_of
from .modifiers import stab as stab_of
from .primitives import skill_energy_cost
from .skillbook import SkillCategory

if TYPE_CHECKING:
    from .atom import Atom
    from .engine import TurnContext
    from .models import BattleState, Skill, Unit


def compile_skill(state: "BattleState", ctx: "TurnContext", unit: "Unit",
                  skill: "Skill", side: str, acted_first: bool = False) -> list["Atom"]:
    """输入：state / TurnContext / 施法者 / 技能 / 归属方 / acted_first（本回合执行
    顺序先于对手——风起印记读钩子）；输出：有序 Atom 列表。

    顺序与旧 `resolve_skill` 完全一致：扣能量 → 揭示 → 类别分支（攻击逐段伤害 /
    状态逐层增减益 / 防御减伤已在 DECLARE 武装）→ 一次性资源效果（回能/回血/吸血/
    偷能/场下回能/连击·吸血 buff）→（特性触发由 resolve_skill 在 frame 上收尾）。
    """
    effect = skill.effect
    foe = "b" if side == "a" else "a"
    target = state.active(foe)
    atoms: list["Atom"] = [
        SpendEnergy(unit=unit, amount=skill_energy_cost(state, side, unit,
                                                        skill.energy_cost, skill),
                    source=skill.name),
        RevealSkill(unit=unit, side=side, skill=skill.name),
    ]

    if effect.category == SkillCategory.ATTACK:
        mult = effect.counter_damage_mult if ctx.counters(side) else 1.0
        reduced = ctx.reduction(foe)
        eff = eff_of(skill.type, target.types)
        stab = stab_of(skill.type, unit.types)
        hits = _effective_hits(state, unit, skill, side)
        counter_cat = ctx.category(foe).value if ctx.counters(side) else ""
        # 冻结批（2026-08-30）：敌方冻结层 → 本次威力加成（碎冰冰每层 / 极寒领域有冻结即加）
        from .statuses import freeze_layers

        power = skill.power + effect.freeze_power_per_layer * freeze_layers(target)
        if effect.freeze_power_if_frozen and freeze_layers(target) > 0:
            power += effect.freeze_power_if_frozen
        for i in range(1, hits + 1):
            atoms.append(DealDamage(
                side=side, source=unit, target=target, skill=skill.name,
                power=power, skill_type=skill.type, damage_kind=skill.kind,
                hit=i, total_hits=hits, counter_mult=mult, reduction=reduced,
                effectiveness=eff, stab=stab, counter_cat=counter_cat,
                acted_first=acted_first,
            ))
            # 每连击状态附加（DOT 批 2026-08-30：毒针/易燃物质类——逐击施加）
            for se in effect.stat_effects:
                tgt = unit if se.target == "self" else target
                atoms.append(AddModifier(side=side, unit=tgt, stat=se.stat,
                                         mode=se.mode, layers=se.layers,
                                         source=skill.name, target=se.target,
                                         counter_cat=counter_cat,
                                         kwargs=dict(se.kwargs)))
        if effect.counter_status_effects and ctx.counters(side):
            # 冻结批（2026-08-30）：应对状态额外施冻结（滚雪球）
            for se in effect.counter_status_effects:
                tgt = unit if se.target == "self" else target
                atoms.append(AddModifier(side=side, unit=tgt, stat=se.stat, mode=se.mode,
                                         layers=se.layers, source=skill.name, target=se.target,
                                         counter_cat=counter_cat, kwargs=dict(se.kwargs)))
        if effect.freeze_double_on_counter and ctx.counters(side):
            # 极寒领域：应对状态 → 敌方冻结层翻倍
            n = freeze_layers(target)
            if n > 0:
                atoms.append(SetModLayers(unit=target, stat="冻结", mode="special",
                                          layers=n * 2, source=skill.name))
        if effect.freeze_energy_gain_per_layer:
            # 冻结批：敌方每层冻结 → 自己回 N 能量（冷凝）
            n = freeze_layers(target)
            if n > 0:
                atoms.append(GainEnergy(side=side, unit=unit,
                                        amount=n * effect.freeze_energy_gain_per_layer,
                                        source=skill.name, target="self"))
        if effect.self_energy_gain:
            atoms.append(GainEnergy(side=side, unit=unit, amount=effect.self_energy_gain,
                                    source=skill.name, target="self"))
        if effect.heal_pct_self:
            atoms.append(HealPct(side=side, unit=unit, pct=effect.heal_pct_self,
                                 source=skill.name))
        # 吸血：总是生成 Lifesteal atom——即使技能自身无吸血，也要吃「吸血 buff」
        #（贪婪给自己 100% 吸血）；reducer 内 lifesteal<=0 或 total_damage<=0 时返回空。
        atoms.append(Lifesteal(side=side, unit=unit, pct=effect.lifesteal_pct,
                               source=skill.name))
        if effect.bench_energy_gain:
            atoms.append(BenchEnergy(side=side, self_unit=unit,
                                     amount=effect.bench_energy_gain, source=skill.name))
        for se in effect.buff_effects:
            tgt = unit if se.target == "self" else target
            atoms.append(AddModifier(side=side, unit=tgt, stat=se.stat, mode=se.mode,
                                     layers=se.layers, source=skill.name, target=se.target,
                                     counter_cat=counter_cat))
    elif effect.category == SkillCategory.STATUS:
        counter_cat = ctx.category(foe).value if ctx.counters(side) else ""
        if effect.stat_effects:
            # P1/P2 状态系：每连击应用 stat_effects（花炮/冰捆缚/缓一缓…；
            # DOT 批：状态施加同样走此路径，kwargs 随记录进 stat_mods）
            hits = _effective_hits(state, unit, skill, side)
            for _ in range(hits):
                for se in effect.stat_effects:
                    tgt = unit if se.target == "self" else target
                    atoms.append(AddModifier(side=side, unit=tgt, stat=se.stat,
                                             mode=se.mode, layers=se.layers,
                                             source=skill.name, target=se.target,
                                             counter_cat=counter_cat,
                                             kwargs=dict(se.kwargs)))
        elif effect.stat or effect.layers:
            # E0 教学：单条自身状态
            layers = effect.layers + (effect.counter_extra_layers if ctx.counters(side) else 0)
            atoms.append(AddModifier(side=side, unit=unit, stat=effect.stat,
                                     mode=effect.mode, layers=layers, source=skill.name,
                                     target="", counter_cat=counter_cat))
        for se in effect.buff_effects:
            tgt = unit if se.target == "self" else target
            atoms.append(AddModifier(side=side, unit=tgt, stat=se.stat, mode=se.mode,
                                     layers=se.layers, source=skill.name, target=se.target,
                                     counter_cat=counter_cat))
        if effect.energy_gain:
            atoms.append(GainEnergy(side=side, unit=unit, amount=effect.energy_gain,
                                    source=skill.name, target="self"))
        if effect.heal_pct_self:
            atoms.append(HealPct(side=side, unit=unit, pct=effect.heal_pct_self,
                                 source=skill.name))
        if effect.steal_energy:
            atoms.append(StealEnergy(side=side, unit=unit, foe=target,
                                     amount=effect.steal_energy, source=skill.name))
        if effect.energy_foe_cost_ratio > 0:
            atoms.append(FoeCostGain(side=side, unit=unit, foe=target,
                                     ratio=effect.energy_foe_cost_ratio, source=skill.name))
        if effect.bench_energy_gain:
            atoms.append(BenchEnergy(side=side, self_unit=unit,
                                     amount=effect.bench_energy_gain, source=skill.name))
        if effect.mark_effects:
            # 印记施加：每连击应用（普通施加以 hits=1；星链类按连击数逐次施印）
            hits = _effective_hits(state, unit, skill, side)
            for _ in range(hits):
                atoms.extend(_mark_atoms(side, unit, skill, effect.mark_effects))
        if effect.counter_status_effects and ctx.counters(side):
            # 冻结批（2026-08-30）：应对命中时施加状态（冰点：应对防御额外施冻结）
            for se in effect.counter_status_effects:
                tgt = unit if se.target == "self" else target
                atoms.append(AddModifier(side=side, unit=tgt, stat=se.stat, mode=se.mode,
                                         layers=se.layers, source=skill.name, target=se.target,
                                         counter_cat=counter_cat, kwargs=dict(se.kwargs)))
    # DEFENSE：减伤已在 build_turn_context 武装；应对命中时施加印记/状态（印记/天气批、
    # 冻结批）；使用防御技能 → 该精灵所有防御技冷却一回合（2026-08-30 拍板，规则声明化）
    if effect.counter_mark_effects and ctx.counters(side):
        atoms.extend(_mark_atoms(side, unit, skill, effect.counter_mark_effects))
    if effect.counter_status_effects and ctx.counters(side) \
            and effect.category == SkillCategory.DEFENSE:
        # 冻结批（2026-08-30）：防御应对命中施状态（冰墙：敌方获得 2 层冻结）
        for se in effect.counter_status_effects:
            atoms.append(AddModifier(side=side, unit=target, stat=se.stat, mode=se.mode,
                                     layers=se.layers, source=skill.name, target=se.target,
                                     counter_cat="攻击", kwargs=dict(se.kwargs)))
    if effect.category == SkillCategory.DEFENSE:
        atoms.append(SetCooldown(side=side, unit=unit, turns=1, source=skill.name))
    # 天气设置（落雨/沙涌/冬至/惊雷）
    if effect.set_weather:
        atoms.append(SetWeather(kind=effect.set_weather, turns=effect.weather_turns,
                                source=skill.name))
    return atoms


def _mark_atoms(side: str, unit: "Unit", skill: "Skill", effects) -> list["Atom"]:
    """印记施加原子：target=foe → 敌方阵营；space 按施法者特性路由
    （吟游之弦 → exclusive 独立空间，共存不顶替）。"""
    foe = "b" if side == "a" else "a"
    space = "exclusive" if (unit.trait and unit.trait.name == "吟游之弦") else "normal"
    return [ApplyMark(side=side if me.target == "self" else foe, name=me.name,
                      layers=me.layers, source=skill.name, space=space) for me in effects]


def _effective_hits(state, unit, skill, side) -> int:
    """技能实际连击数（与 engine.effective_hits 同语义，独立实现避免循环依赖）。"""
    effect = skill.effect
    base = effect.hits
    if effect.combo_per_team_skill:
        base += sum(1 for u in state.side(side).units
                    if any(s.name == effect.combo_per_team_skill for s in u.skills))
    if not effect.combo_eligible:
        return max(1, base)
    from .primitives import combo_bonus
    flat, pct = combo_bonus(unit)
    return max(1, int((base + flat) * (1 + 0.10 * pct)))
