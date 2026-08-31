"""印记效果目录 + 收集器 + 读钩子数据（2026-08-30 施工）。

印记是**阵营级**（`SideState.positive/negative/exclusive_marks` 三槽）：
- 同种叠加层数、异种顶替（每极性至多 1）；`exclusive_marks` 独立空间共存不顶替
  （里拉鳐「吟游之弦」，路由见 compiler._mark_space）；
- 效果作用于**触发时该方在场精灵**（场空 → 跳过）。

本模块是纯声明 + 纯函数：`MARK_CATALOG` 声明「事件 → 条件 → 效果」（沿用
`effects.EffectBinding`，Effect op 扩展 lose_hp_pct / energy_loss / star_meteor /
consume_mark / random_debuff / extra_layer）；`collect` 由 triggers.collect_reactions
调用，产出 Atom 交 reducer 执行（**不改状态**）；读钩子数据（power_pct/power_flat/
cost/speed）供 damage / primitives / engine 读取。

已拍板口径（2026-08-30）：星陨 = 攻击方额外幻伤（威力 N²+24(N−1)，类别=攻击方
较高种族值）；暗涌 = 5×层逐层随机五维（引擎 RNG）；风起 = 执行顺序先手；蓄势能耗
+1 仅攻击技能；龙噬按原始能耗==3；中毒/棘刺按最大生命。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .atom import (AddModifier, ApplyMark, ConsumeMarkLayers, DealDamage,
                   GainEnergy, LoseEnergy, LoseHp)
from .effects import Effect, EffectBinding

if TYPE_CHECKING:
    from .atom import Atom
    from .models import BattleState, SideState, Unit


@dataclass(frozen=True)
class MarkDef:
    """一个印记的静态定义。bindings = 事件 → 条件 → 效果（EffectBinding）。"""

    name: str
    polarity: str            # "positive" | "negative"
    bindings: tuple[EffectBinding, ...] = ()


# 事件名 = DomainEvent 类名（"TurnEnded" / "UnitEntered" / "UnitExited" /
# "DamageApplied" / "SkillResolved" / "StatModChanged"）。
MARK_CATALOG: dict[str, MarkDef] = {
    # ── 正面印记 ──
    "湿润印记": MarkDef(name="湿润印记", polarity="positive", bindings=()),   # SKILL_COST 读钩子
    "蓄势印记": MarkDef(name="蓄势印记", polarity="positive", bindings=()),   # 读钩子 ×2
    "攻击印记": MarkDef(name="攻击印记", polarity="positive", bindings=()),   # 读钩子
    "蓄电印记": MarkDef(name="蓄电印记", polarity="positive", bindings=()),   # 读钩子
    "风起印记": MarkDef(name="风起印记", polarity="positive", bindings=()),   # 读钩子（acted_first）
    "光合印记": MarkDef(name="光合印记", polarity="positive", bindings=(
        EffectBinding(hook="TurnEnded", cond="", effects=(
            Effect(op="energy_gain", value=1),
        )),
    )),
    "龙噬印记": MarkDef(name="龙噬印记", polarity="positive", bindings=(
        EffectBinding(hook="SkillResolved", cond="energy_cost_3", effects=(
            Effect(op="stat_mod", stat="atk", mode="pct", layers=3),
            Effect(op="stat_mod", stat="sp_atk", mode="pct", layers=3),
        )),
    )),
    "萌芽印记": MarkDef(name="萌芽印记", polarity="positive", bindings=(
        EffectBinding(hook="StatModChanged", cond="positive_gain", effects=(
            Effect(op="extra_layer", value=1),
        )),
    )),
    # ── 负面印记 ──
    "减速印记": MarkDef(name="减速印记", polarity="negative", bindings=()),   # build_queue 读钩子
    "降灵印记": MarkDef(name="降灵印记", polarity="negative", bindings=(
        EffectBinding(hook="UnitEntered", cond="", effects=(
            Effect(op="energy_loss", value=1),
        )),
    )),
    "棘刺印记": MarkDef(name="棘刺印记", polarity="negative", bindings=(
        EffectBinding(hook="UnitEntered", cond="", effects=(
            Effect(op="lose_hp_pct", value=6),
        )),
    )),
    "暗涌印记": MarkDef(name="暗涌印记", polarity="negative", bindings=(
        EffectBinding(hook="UnitExited", cond="", effects=(
            Effect(op="random_debuff", value=5),
        )),
        EffectBinding(hook="UnitEntered", cond="from_faint", effects=(
            Effect(op="random_debuff", value=5),
        )),
    )),
    "星陨印记": MarkDef(name="星陨印记", polarity="negative", bindings=(
        EffectBinding(hook="DamageApplied", cond="non_psychic_hit", effects=(
            Effect(op="star_meteor"),
            Effect(op="consume_mark", value=0),   # 0 = 全清（amount 在 _effect_atoms 里按层数算）
        )),
    )),
    "中毒印记": MarkDef(name="中毒印记", polarity="negative", bindings=(
        EffectBinding(hook="TurnEnded", cond="", effects=(
            Effect(op="lose_hp_pct", value=3),
        )),
    )),
}


# ── 读钩子数据（供 damage.build_damage_terms / primitives.skill_energy_cost /
#    engine.build_queue 读取；层数 = 三槽合计）──
def _all(side_state: "SideState") -> list:
    return list(side_state.positive_marks) + list(side_state.negative_marks) \
        + list(side_state.exclusive_marks)


def _layers(side_state: "SideState", name: str) -> int:
    return sum(m.layers for m in _all(side_state) if m.name == name)


def power_pct_bonus(side_state: "SideState", *, acted_first: bool = False) -> float:
    """攻击威力百分比修正（攻击/蓄势/风起印记）：叠加到公式 power_pct 项。"""
    bonus = 0.0
    bonus += 0.1 * _layers(side_state, "攻击印记")
    bonus += 0.3 * _layers(side_state, "蓄势印记")
    if acted_first:
        bonus += 0.2 * _layers(side_state, "风起印记")
    return bonus


def power_flat_bonus(side_state: "SideState") -> int:
    """攻击威力绝对值修正（蓄电印记「迸发」）：叠加到公式 power_term 项。"""
    return 10 * _layers(side_state, "蓄电印记")


def cost_adjust(side_state: "SideState", *, kind: str = "") -> int:
    """全技能能耗修正：湿润 −1×层（全技能）；蓄势 +1×层（仅攻击，2026-08-30 拍板）。"""
    adj = -_layers(side_state, "湿润印记")
    if kind in ("物攻", "魔攻"):
        adj += _layers(side_state, "蓄势印记")
    return adj


def speed_penalty(side_state: "SideState") -> int:
    """速度惩罚（减速印记 −10×层），build_queue 读。"""
    return 10 * _layers(side_state, "减速印记")


# ── 收集器：事件 → 双方印记反应 Atom ──
_RANDOM_STATS: tuple[str, ...] = ("atk", "sp_atk", "def", "sp_def", "speed")


def _side_of(state: "BattleState", unit: "Unit") -> str:
    """unit 所属阵营（委托 models.side_of：unit_id 前缀优先、线性查找兜底）。"""
    from .models import side_of

    return side_of(state, unit)


def _unit_by_id(state: "BattleState", unit_id: str) -> "Unit" | None:
    for s in ("a", "b"):
        for u in state.side(s).units:
            if u.id == unit_id:
                return u
    return None


def _active_or_none(state: "BattleState", side: str) -> "Unit" | None:
    """该方在场精灵；场空（阵亡）→ None（效果跳过）。"""
    u = state.active(side)
    return None if u.fainted else u


def _relevant(state: "BattleState", event):
    """事件 → [(side, 目标单位, 事件关联单位)]：哪些阵营的印记参与反应。"""
    etype = type(event).__name__
    if etype == "TurnEnded":
        return [(s, _active_or_none(state, s), None) for s in ("a", "b")]
    if etype == "UnitEntered":
        u = _unit_by_id(state, event.unit_id)
        if u is None:
            return []
        return [(_side_of(state, u), u, u)]
    if etype == "UnitExited":
        u = _unit_by_id(state, event.unit_id)
        if u is None:
            return []
        incoming = _unit_by_id(state, event.incoming_id) if event.incoming_id else None
        return [(_side_of(state, u), incoming, u)]
    if etype == "DamageApplied":
        tgt = _unit_by_id(state, event.target_id)
        if tgt is None:
            return []
        return [(_side_of(state, tgt), tgt, _unit_by_id(state, event.source_id))]
    if etype in ("SkillResolved", "StatModChanged"):
        u = _unit_by_id(state, event.unit_id)
        if u is None:
            return []
        return [(_side_of(state, u), u, u)]
    return []


def _cond_holds(cond: str, state, event, unit, side: str) -> bool:
    if not cond:
        return True
    if cond == "non_psychic_hit":
        return event.skill_type != "幻"
    if cond == "from_faint":
        return bool(event.from_faint)
    if cond == "energy_cost_3":
        for s in (unit.current_skills if unit else []):
            if s.name == event.skill:
                return s.energy_cost == 3
        return False
    if cond == "positive_gain":
        # 增益（正层）且非萌芽自身追加（source 守卫防循环）
        return event.layers > 0 and event.source != "萌芽印记"
    return False


def _effect_atoms(state: "BattleState", side: str, mark, effect, event, target, ev_unit):
    """一条 Effect → Atom 列表（效果值 ×印记层数；event/ev_unit 供特殊 op 读取）。"""
    from .modifiers import effectiveness as eff_of
    from .modifiers import stab as stab_of

    n = mark.layers
    op = effect.op
    if op == "energy_gain":
        return [GainEnergy(side=side, unit=target, amount=int(effect.value) * n,
                           source=mark.name, target="self")]
    if op == "energy_loss":
        return [LoseEnergy(side=side, unit=target, amount=int(effect.value) * n,
                           source=mark.name)]
    if op == "lose_hp_pct":
        return [LoseHp(side=side, unit=target, pct=int(effect.value) * n, source=mark.name)]
    if op == "stat_mod":
        return [AddModifier(side=side, unit=target, stat=effect.stat, mode=effect.mode,
                            layers=effect.layers * n, source=mark.name,
                            kwargs=dict(getattr(effect, "kwargs", {})))]
    if op == "random_debuff":
        # 暗涌：5×层逐层随机分配五维（引擎 RNG，确定性）
        counts = {s: 0 for s in _RANDOM_STATS}
        for _ in range(int(effect.value) * n):
            counts[state.rng.choice(_RANDOM_STATS)] += 1
        return [AddModifier(side=side, unit=target, stat=s, mode="pct", layers=-c,
                            source=mark.name) for s, c in counts.items() if c]
    if op == "star_meteor":
        # 星陨：攻击方（ev_unit）对持有者额外幻伤；威力 = N²+24(N−1)；类别 = 攻击方
        # 物攻/魔攻**种族值**较高项（2026-08-30 拍板）；主伤害后结算（事件序保证）。
        power = n * n + 24 * (n - 1)
        base = ev_unit.base_stats or ev_unit.stats
        kind = "物攻" if base.get("atk", 0) >= base.get("sp_atk", 0) else "魔攻"
        eff = eff_of("幻", target.types)
        stab = stab_of("幻", ev_unit.types)
        return [DealDamage(side=side, source=ev_unit, target=target, skill="星陨印记",
                           power=power, skill_type="幻", damage_kind=kind,
                           effectiveness=eff, stab=stab, hit=1, total_hits=1)]
    if op == "consume_mark":
        return [ConsumeMarkLayers(side=side, name=mark.name, amount=n, source=mark.name)]
    if op == "extra_layer":
        # 萌芽：获得增益时额外 +1×层（同 stat/mode；source 守卫在 cond 里）
        return [AddModifier(side=side, unit=target, stat=event.stat, mode=event.mode,
                            layers=1 * n, source=mark.name, target="self")]
    return []


def collect(state, event) -> list["Atom"]:
    """事件 → 双方印记反应 Atom（状态只读；Atom 由 reducer 执行）。"""
    if state is None:
        return []
    atoms: list = []
    for side, target, ev_unit in _relevant(state, event):
        if target is None:
            continue
        for mark in _all(state.side(side)):
            mdef = MARK_CATALOG.get(mark.name)
            if mdef is None:
                continue
            for binding in mdef.bindings:
                if binding.hook != type(event).__name__:
                    continue
                if not _cond_holds(binding.cond, state, event, ev_unit, side):
                    continue
                for eff in binding.effects:
                    atoms.extend(_effect_atoms(state, side, mark, eff, event, target, ev_unit))
    return atoms
