"""Reducer 表（v3 骨架·执行层）：唯一执行 Atom 的地方，产出 DomainEvent + 展示事件。

v3 核心（`mydocs/battle_docs.md` §4）：Reducer 是**唯一有权修改状态**的人——
夹取、判阵亡、判定边界都在这；Trigger 只返回新 Atom，绝不直接写状态。

骨架阶段的目标：**行为逐位等价**——对同一 Atom 序列，Reducer 产生的展示事件与旧
`resolve_skill` 完全一致（`tests/test_v3_sentinel.py` + 既有测试把关），同时把领域
事件挂进 `frame.domain_events`（Phase 3.1：由 `pipeline.run` 回传给 Trigger 消费，
未来接入印记/天气/DOT）。

**关键细节（与旧 engine 逐位一致，稍有不慎就 digest 漂移）**：
- 属性层用 `_add_stat_layers` 语义（按 stat/mode/permanent 合并，**忽略 source**），
  不是 `apply_stat_mod`（按 source 合并）——旧 `resolve_skill` 用前者。
- 能耗层用 `apply_energy_cost_mod`（按 stat/mode/permanent/trait/source 合并）。
- SpendEnergy 直接减（不夹 0）；付得起已由 actions 门控保证。
- Atom 持 Unit 对象引用（v3 骨架）；副作用仍走 damage/primitives 漏斗唯一入口。
- 展示事件沿用 `events.ev()` 形状（EVENT_TYPES 不变，visibility/前端零改动）。

本模块**自包含**：不 import engine（避免循环依赖），只依赖 atom/domain/damage/events/
models/modifiers/primitives。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .atom import (
    AddModifier, ApplyMark, BenchEnergy, ConsumeMarkLayers, DealDamage,
    FoeCostGain, GainEnergy, HealPct, Lifesteal, LoseEnergy, LoseHp,
    RevealSkill, SetWeather, SpendEnergy, StealEnergy, TraitGain,
)
from .damage import apply_heal, apply_hp_loss
from .domain import (DamageApplied, EnergyChanged, HpChanged, MarkChanged,
                     StatModChanged, WeatherChanged)
from .events import ev
from .modifiers import DamageQuery, compute
from .primitives import (
    apply_energy_cost_mod, apply_energy_gain, heal_pct, lifesteal_bonus,
)
from .models import StatModifier

if TYPE_CHECKING:
    from .atom import Atom
    from .domain import DomainEvent
    from .models import BattleState


@dataclass
class Frame:
    """一次技能结算的回合内上下文（v3 骨架）。不落 BattleState（回合内派生量）。

    `domain_events`（2026-08-30）：本技能结算过程中产生的领域事件累积——Reducer
    只在这里挂事件，**由 pipeline.run 回传**给 Trigger 调度器（fixpoint 反应循环）；
    这是「事件 → 反应」通道的物质基础（v3 骨架 Phase 3.1）。
    """

    total_damage: int = 0          # 本技能累计总伤害（吸血等读它）
    dealt_counter: bool = False    # 本技能是否造成克制伤害（特性「最好的伙伴」用）
    domain_events: list["DomainEvent"] = field(default_factory=list)


def reduce_all(state: "BattleState", atoms: list["Atom"], frame: Frame) -> list[dict]:
    """按序执行 Atom 列表，返回展示事件。frame 就地更新（total_damage / dealt_counter）。"""
    events: list[dict] = []
    for atom in atoms:
        handler = _DISPATCH.get(type(atom))
        if handler is None:
            raise ValueError(f"未知 Atom 类型：{type(atom).__name__}")
        events.extend(handler(state, atom, frame))
    return events


# ── 各 Atom reducer（行为与旧 resolve_skill 逐位一致）──
def _reduce_spend(state, atom: SpendEnergy, frame: Frame) -> list[dict]:
    atom.unit.energy -= atom.amount        # 直接减（不夹 0）；付得起已由 actions 门控保证
    return []


def _reduce_reveal(state, atom: RevealSkill, frame: Frame) -> list[dict]:
    side_state = state.side(atom.side)
    side_state.revealed.add((side_state.active, atom.skill))
    return []


def _reduce_damage(state, atom: DealDamage, frame: Frame) -> list[dict]:
    target = atom.target
    if target.fainted:
        return []                     # 连击途中目标阵亡 → 剩余段跳过
    dmg = compute(_query_for(state, atom.source, target, atom), state.rules)
    loss = apply_hp_loss(state, target, dmg, source=atom.skill)
    frame.total_damage += loss.applied
    if atom.effectiveness > 1.0:
        frame.dealt_counter = True
    frame.domain_events.append(DamageApplied(
        source_id=atom.source.id, target_id=target.id, amount=loss.applied,
        effectiveness=atom.effectiveness, skill=atom.skill, total=frame.total_damage,
        skill_type=atom.skill_type))
    return [ev(
        "damage", atom.side,
        attacker=atom.source.name, skill=atom.skill, target=target.name,
        damage=loss.applied, target_hp_left=target.current_hp,
        counter=atom.counter_cat, mult=atom.counter_mult, reduced=atom.reduction,
        eff=atom.effectiveness, stab=atom.stab, hit=atom.hit, hits=atom.total_hits,
    )]


def _reduce_heal_pct(state, atom: HealPct, frame: Frame) -> list[dict]:
    u = atom.unit
    hr = heal_pct(state, u, atom.pct, source=atom.source)
    frame.domain_events.append(HpChanged(u.id, u.current_hp - hr.applied, u.current_hp,
                                         atom.source))
    return [ev("heal", atom.side, unit=u.name, applied=hr.applied, overflow=hr.overflow,
               hp=u.current_hp, source=atom.source)]


def _add_stat_layers(unit, stat: str, mode: str, layers: int,
                     source: str, permanent: bool = False,
                     kwargs: dict | None = None) -> int:
    """属性增减益合并（**与旧 engine 逐位一致**）：按 (stat, mode, permanent) 合并，忽略 source。

    注意：不是 `primitives.apply_stat_mod`（它按 source 合并）——旧 `resolve_skill` 走
    `_add_stat_layers`，多条不同来源的同属性层会合并成一条。
    `kwargs`（纯负面 buff 扩展参数）在新建记录时写入；合并时保留首条记录的值。
    """
    for m in unit.stat_mods:
        if m.stat == stat and m.mode == mode and m.permanent == permanent:
            m.layers += layers
            return m.layers
    unit.stat_mods.append(StatModifier(stat=stat, mode=mode, layers=layers,
                                       permanent=permanent, source=source,
                                       kwargs=dict(kwargs or {})))
    return layers


def _reduce_add_modifier(state, atom: AddModifier, frame: Frame) -> list[dict]:
    u = atom.unit
    if atom.stat == "energy_cost":
        total = apply_energy_cost_mod(u, layers=atom.layers, permanent=False,
                                      trait=False, source=atom.source)
    else:
        total = _add_stat_layers(u, atom.stat, atom.mode, atom.layers, atom.source,
                                 kwargs=atom.kwargs)
    frame.domain_events.append(StatModChanged(
        u.id, atom.stat, atom.mode, atom.layers, total, atom.source))
    out = {"type": "stat_change", "side": atom.side, "unit": u.name, "skill": atom.source,
           "stat": atom.stat, "mode": atom.mode, "layers": atom.layers,
           "total_layers": total, "counter": atom.counter_cat}
    if atom.target:
        out["target"] = atom.target   # 旧 legacy 单条自身状态无 target 字段
    return [out]


def _reduce_gain_energy(state, atom: GainEnergy, frame: Frame) -> list[dict]:
    u = atom.unit
    gained = apply_energy_gain(u, atom.amount, energy_max=state.rules.energy_max)
    frame.domain_events.append(EnergyChanged(u.id, u.energy - gained, u.energy, atom.source))
    return [ev("energy_gain", atom.side, unit=u.name, gained=gained, energy=u.energy,
               source=atom.source, target=atom.target)]


def _reduce_bench_energy(state, atom: BenchEnergy, frame: Frame) -> list[dict]:
    events = []
    for bench in state.side(atom.side).units:
        if bench is atom.self_unit or bench.fainted:
            continue
        gained = apply_energy_gain(bench, atom.amount, energy_max=state.rules.energy_max)
        frame.domain_events.append(EnergyChanged(bench.id, bench.energy - gained,
                                                 bench.energy, atom.source))
        events.append(ev("energy_gain", atom.side, unit=bench.name, gained=gained,
                         energy=bench.energy, source=atom.source, target="bench"))
    return events


def _reduce_lifesteal(state, atom: Lifesteal, frame: Frame) -> list[dict]:
    u = atom.unit
    lifesteal = atom.pct + 100 * lifesteal_bonus(u)
    if lifesteal <= 0 or frame.total_damage <= 0:
        return []
    hr = apply_heal(state, u, int(frame.total_damage * lifesteal / 100), source=atom.source)
    frame.domain_events.append(HpChanged(u.id, u.current_hp - hr.applied, u.current_hp,
                                         atom.source))
    return [ev("heal", atom.side, unit=u.name, applied=hr.applied, overflow=hr.overflow,
               hp=u.current_hp, source=atom.source)]


def _reduce_steal(state, atom: StealEnergy, frame: Frame) -> list[dict]:
    u, foe = atom.unit, atom.foe
    gained = min(atom.amount, foe.energy)
    foe.energy -= gained
    u.energy = min(state.rules.energy_max, u.energy + gained)
    frame.domain_events.append(EnergyChanged(foe.id, foe.energy + gained, foe.energy,
                                             atom.source))
    frame.domain_events.append(EnergyChanged(u.id, u.energy - gained, u.energy, atom.source))
    return [ev("steal", atom.side, unit=u.name, gained=gained, energy=u.energy,
               foe=foe.name, foe_energy=foe.energy, source=atom.source)]


def _reduce_foe_cost(state, atom: FoeCostGain, frame: Frame) -> list[dict]:
    u, foe = atom.unit, atom.foe
    foe_cost = sum(s.energy_cost for s in foe.skills)   # 旧代码读 foe_unit.skills
    gained = apply_energy_gain(u, int(foe_cost * atom.ratio), energy_max=state.rules.energy_max)
    frame.domain_events.append(EnergyChanged(u.id, u.energy - gained, u.energy, atom.source))
    return [ev("energy_gain", atom.side, unit=u.name, gained=gained, energy=u.energy,
               source=atom.source, target="self", from_foe_cost=foe_cost)]


def _reduce_trait_gain(state, atom: TraitGain, frame: Frame) -> list[dict]:
    """特性增益：写 unit.trait.gains（与旧 hooks._apply_trait_effect 逐位一致）。

    不产生展示事件（旧行为）；产生 StatModChanged 领域事件（Phase 3.1：特性增益
    进入领域层，供未来监听「获得增益」的 Trigger 消费）。
    """
    u = atom.unit
    if u.trait is None:
        return []
    gains = u.trait.gains
    for m in gains:
        if (m.stat == atom.stat and m.mode == atom.mode and m.layers == atom.layers
                and m.permanent == atom.permanent and m.trait is True and m.source == atom.source):
            m.layers += atom.layers
            frame.domain_events.append(StatModChanged(
                u.id, atom.stat, atom.mode, atom.layers, m.layers, atom.source))
            return []
    gains.append(StatModifier(stat=atom.stat, mode=atom.mode, layers=atom.layers,
                              permanent=atom.permanent, trait=True, source=atom.source))
    frame.domain_events.append(StatModChanged(
        u.id, atom.stat, atom.mode, atom.layers, atom.layers, atom.source))
    return []


# ── 印记 / 天气 reducer（2026-08-30：状态写入口仍是 reducer 唯一职责）──
def _reduce_apply_mark(state, atom: ApplyMark, frame: Frame) -> list[dict]:
    from .primitives import apply_mark

    mark = apply_mark(state.side(atom.side), atom.name, atom.layers,
                      source=atom.source, space=atom.space)
    frame.domain_events.append(MarkChanged(side=atom.side, name=mark.name,
                                           layers=mark.layers, source=atom.source))
    return [ev("mark", atom.side, name=mark.name, layers=mark.layers,
               delta=atom.layers, source=atom.source)]


def _reduce_set_weather(state, atom: SetWeather, frame: Frame) -> list[dict]:
    from .weather import set_weather

    set_weather(state, atom.kind, atom.turns, atom.source)
    frame.domain_events.append(WeatherChanged(kind=atom.kind, turns_left=atom.turns,
                                              source=atom.source))
    return [ev("weather", "", kind=atom.kind, turns_left=atom.turns, source=atom.source)]


def _reduce_lose_hp(state, atom: LoseHp, frame: Frame) -> list[dict]:
    """印记/天气伤害：max_hp × pct% 经 apply_hp_loss 唯一漏斗；已阵亡跳过。"""
    u = atom.unit
    if u.fainted:
        return []
    amount = int(u.max_hp * atom.pct / 100)
    loss = apply_hp_loss(state, u, amount, source=atom.source)
    frame.domain_events.append(HpChanged(u.id, u.current_hp + loss.applied,
                                         u.current_hp, atom.source))
    return [ev("damage", atom.side, attacker=atom.source, skill=atom.source,
               target=u.name, damage=loss.applied, target_hp_left=u.current_hp)]


def _reduce_lose_energy(state, atom: LoseEnergy, frame: Frame) -> list[dict]:
    u = atom.unit
    lost = min(atom.amount, u.energy)
    u.energy -= lost
    frame.domain_events.append(EnergyChanged(u.id, u.energy + lost, u.energy, atom.source))
    return [ev("energy_loss", atom.side, unit=u.name, lost=lost, energy=u.energy,
               source=atom.source)]


def _reduce_consume_mark(state, atom: ConsumeMarkLayers, frame: Frame) -> list[dict]:
    from .primitives import consume_mark_layers

    mark = consume_mark_layers(state.side(atom.side), atom.name, atom.amount)
    if mark is None:
        return []
    frame.domain_events.append(MarkChanged(side=atom.side, name=mark.name,
                                           layers=mark.layers, source=atom.source))
    return [ev("mark", atom.side, name=mark.name, layers=mark.layers,
               delta=-atom.amount, source=atom.source)]


_DISPATCH: dict[type, object] = {
    SpendEnergy: _reduce_spend,
    RevealSkill: _reduce_reveal,
    DealDamage: _reduce_damage,
    HealPct: _reduce_heal_pct,
    AddModifier: _reduce_add_modifier,
    GainEnergy: _reduce_gain_energy,
    BenchEnergy: _reduce_bench_energy,
    Lifesteal: _reduce_lifesteal,
    StealEnergy: _reduce_steal,
    FoeCostGain: _reduce_foe_cost,
    TraitGain: _reduce_trait_gain,
    ApplyMark: _reduce_apply_mark,
    SetWeather: _reduce_set_weather,
    LoseHp: _reduce_lose_hp,
    LoseEnergy: _reduce_lose_energy,
    ConsumeMarkLayers: _reduce_consume_mark,
}


# ── 内部辅助（公式规范 2026-08-30：项由 damage.build_damage_terms 统一产出）──
def _query_for(state, attacker, defender, atom: DealDamage) -> DamageQuery:
    from .damage import build_damage_terms

    terms = build_damage_terms(state, attacker, defender, damage_kind=atom.damage_kind,
                               power=atom.power, counter_mult=atom.counter_mult,
                               side=atom.side, skill_type=atom.skill_type,
                               acted_first=atom.acted_first)
    return DamageQuery(
        attacker_id=attacker.id, defender_id=defender.id,
        damage_kind=atom.damage_kind, skill_type=atom.skill_type,
        attacker_types=list(attacker.types), defender_types=list(defender.types),
        power_term=terms.power_term, ratio_num=terms.ratio_num, ratio_den=terms.ratio_den,
        power_pct=terms.power_pct,
        stab=atom.stab, effectiveness=atom.effectiveness, weather=terms.weather,
        reduction=atom.reduction, base_atk=terms.atk, base_def=terms.defense,
    )
