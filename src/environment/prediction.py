"""确定性预估（公式规范 2026-08-30）：预估威力 / 预估伤害纯函数。

「下一回合开始前」（end_turn 后、提交决策前）给玩家的确定性提示；也供 TURN_START
特性（「回合开始就通过预估伤害触发」）使用——预估**不含应对倍率与减伤**（对手决策
未知），含克制 / STAB / 天气 / 比值项（这些由当前快照确定）。

纯函数：零 RNG、零状态写入；预估是派生量，**不进 BattleState**（马尔可夫不变式不破）。

公式（负责人拍板）：
- 预估威力 = [基础威力 + attack_power flat] × 比值项 × [1 + attack_power pct]
  × STAB × 克制 × 天气
- 预估伤害 = int( (atk/def) × 0.9 × 预估威力 × 确定连击数 )——无 min_damage 保底
  （按公式字面）；确定连击数 = compiler._effective_hits（含连击 buff，确定性）。

依赖：damage / models / compiler / types——均为已存在模块，无新环。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .compiler import _effective_hits
from .damage import build_damage_terms
from .models import skill_from_instance
from .types import stab_multiplier, type_effectiveness

if TYPE_CHECKING:
    from .models import BattleState, Skill, Unit


def _outer_multipliers(attacker: "Unit", defender: "Unit", skill: "Skill") -> tuple[float, float]:
    """STAB / 克制（天气乘子由 build_damage_terms 的 terms.weather 提供）。"""
    eff = type_effectiveness(skill.type, defender.types)
    stab = stab_multiplier(skill.type, attacker.types)
    return stab, eff


def _terms_and_mult(state: "BattleState", attacker: "Unit", defender: "Unit",
                    skill: "Skill") -> tuple:
    """项计算（counter_mult=1.0：预估不含应对倍率）+ STAB/克制。

    印记/天气修正随 state 派生（双方可见的确定性事实）；`acted_first=False`——
    先手未知，预估不含风起（与不含应对倍率同理）。
    """
    from .models import side_of

    terms = build_damage_terms(state, attacker, defender, damage_kind=skill.kind,
                               power=skill.power, counter_mult=1.0,
                               side=side_of(state, attacker), skill_type=skill.type,
                               acted_first=False)
    return terms, _outer_multipliers(attacker, defender, skill)


def predict_power(state: "BattleState", attacker: "Unit", defender: "Unit",
                  skill: "Skill") -> float:
    """预估威力 = [基础威力 + attack_power flat] × 比值项 × [1 + attack_power pct
    + 印记威力] × STAB × 克制 × 天气。不含应对倍率（对手决策未知）与连击数。"""
    terms, (stab, eff) = _terms_and_mult(state, attacker, defender, skill)
    return terms.power_term * (terms.ratio_num / terms.ratio_den) * terms.power_pct \
        * stab * eff * terms.weather


def predict_damage(state: "BattleState", attacker: "Unit", defender: "Unit",
                   skill: "Skill", side: str) -> int:
    """预估伤害 = int( (atk/def) × 0.9 × 预估威力 × 确定连击数 )。

    无 min_damage 保底（按公式字面）；`_effective_hits` 需 side（虫鸣类
    队伍技能计数按归属方）。"""
    terms, (stab, eff) = _terms_and_mult(state, attacker, defender, skill)
    power = terms.power_term * (terms.ratio_num / terms.ratio_den) * terms.power_pct \
        * stab * eff * terms.weather
    hits = _effective_hits(state, attacker, skill, side)
    return int((terms.atk / terms.defense) * state.rules.damage_coefficient * power * hits)


def predictions_for(state: "BattleState", side: str) -> list[dict]:
    """一方视角：当前在场精灵每个技能的预估威力/预估伤害（观测提示与 TURN_START 事件用）。

    纯函数；`current_skills` 逐槽预估（含未实装技能的槽位跳过）。"""
    foe_side = "b" if side == "a" else "a"
    attacker = state.active(side)
    defender = state.active(foe_side)
    out: list[dict] = []
    for idx, inst in enumerate(attacker.current_skills):
        skill = skill_from_instance(inst)
        if skill is None:
            continue
        out.append({
            "slot": idx,
            "skill": inst.name,
            "power": predict_power(state, attacker, defender, skill),
            "damage": predict_damage(state, attacker, defender, skill, side),
        })
    return out
