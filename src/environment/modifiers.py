"""ModifierPipeline（v3 骨架·修正层）：纯函数数值修正，不修改状态。

v3 核心区分（`mydocs/battle_docs.md` §5）：
- **Modifier**：只参与计算（克制 / STAB / 应对乘子 / 减伤 / 天气威力 / 技能能耗…），
  是**纯函数**——改计算值，不产生副作用（绝不"顺手给敌人上灼烧"）。
- **Trigger**：产生新 Effect（副作用）。

**2026-08-30 公式规范**：`compute` 是伤害管线的终点，**一行委托** `damage.formula`
（唯一伤害公式的家在 damage.py）；`DamageQuery` 承载结构化输入项（威力项 / 比值项 /
威力百分比 / STAB / 克制 / 天气 / 减伤），不再是单个折叠乘子——天气等读钩子
（ATTACK_POWER）将来挂在项上，不改管线形状。

依赖方向：`modifiers → damage → models`（无环）。克制/STAB 纯函数留在此处
（`compiler` 的单一事实源）。
"""

from __future__ import annotations

from dataclasses import dataclass

from .types import stab_multiplier, type_effectiveness


@dataclass(frozen=True)
class DamageQuery:
    """一次伤害计算的输入快照（纯数据，公式规范 2026-08-30）。

    `power_term/ratio_num/ratio_den/power_pct` 由 `damage.build_damage_terms` 产出；
    `stab/effectiveness/weather/reduction` 由调用方（reducer 的 atom）提供。
    """

    attacker_id: str
    defender_id: str
    damage_kind: str        # 物攻 / 魔攻
    skill_type: str
    attacker_types: list[str]
    defender_types: list[str]
    power_term: float = 1.0     # 基础威力 × 应对倍率 + 威力绝对值加成
    ratio_num: float = 1.0      # 1 + 0.1×(我方atk增益pct + 敌方def减益pct)
    ratio_den: float = 1.0      # 1 + 0.1×(我方atk减益pct + 敌方def增益pct)
    power_pct: float = 1.0      # 1 + 0.1×attack_power pct 层
    stab: float = 1.0           # 本系加成
    effectiveness: float = 1.0  # 克制倍率
    weather: float = 1.0        # 天气影响（天气效果未实现，恒 1.0）
    reduction: float = 0.0      # 防御方已武装减伤（0~1）
    base_atk: int = 0           # 攻击方攻击（基线 + flat 层，下限 1）
    base_def: int = 0           # 防御方防御（基线 + flat 层，下限 1）


def compute(q: DamageQuery, rules) -> int:
    """管线终点：委托 `damage.formula`（唯一伤害公式）。

    顺序求值、出口 int() 一次；`power_term<=0 → 0`；保底 `rules.min_damage`。
    """
    from .damage import formula

    return formula(atk=q.base_atk, defense=q.base_def, power_term=q.power_term,
                   ratio_num=q.ratio_num, ratio_den=q.ratio_den, power_pct=q.power_pct,
                   stab=q.stab, effectiveness=q.effectiveness, weather=q.weather,
                   reduction=q.reduction, coefficient=rules.damage_coefficient,
                   min_damage=rules.min_damage)


def effectiveness(skill_type: str, defender_types: list[str]) -> float:
    """克制倍率（纯函数）。"""
    return type_effectiveness(skill_type, defender_types)


def stab(skill_type: str, attacker_types: list[str]) -> float:
    """本系加成（纯函数）。"""
    return stab_multiplier(skill_type, attacker_types)
