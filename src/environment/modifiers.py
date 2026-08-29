"""ModifierPipeline（v3 骨架·修正层）：纯函数数值修正，不修改状态。

v3 核心区分（`mydocs/battle_docs.md` §5）：
- **Modifier**：只参与计算（克制 / STAB / 应对乘子 / 减伤 / 天气威力 / 技能能耗…），
  是**纯函数**——改计算值，不产生副作用（绝不"顺手给敌人上灼烧"）。
- **Trigger**：产生新 Effect（副作用）。

骨架阶段先落**伤害管线**：把旧 `damage.compute_damage` 的乘子链（克制→STAB→应对→减伤）
抽成可组合的 `DamageQuery` 管线，行为逐位等价（digest 哨兵把关）。天气等读钩子
（ATTACK_POWER / SKILL_COST）将来挂在管线两端，不改管线形状。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .models import aggregate_stats
from .types import stab_multiplier, type_effectiveness


@dataclass(frozen=True)
class DamageQuery:
    """一次伤害计算的输入快照（纯数据）。"""

    attacker_id: str
    defender_id: str
    power: int
    damage_kind: str        # 物攻 / 魔攻
    skill_type: str
    attacker_types: list[str]
    defender_types: list[str]
    multiplier: float = 1.0     # 克制 × STAB × 应对乘子 … 的累积
    reduction: float = 0.0      # 防御方已武装减伤（0~1）
    base_atk: int = 0           # 攻击方攻击（物理/魔法，聚合后）
    base_def: int = 0           # 防御方防御（物理/魔法，聚合后）


def _build_query(state, attacker, defender, skill, *, counter_mult: float,
                 reduction: float, effectiveness: float, stab: float) -> DamageQuery:
    """从引擎对象构造 DamageQuery：六维聚合 + 克制/STAB 乘子折叠进 multiplier。"""
    physical = skill.kind == "物攻"
    atk_key = "atk" if physical else "sp_atk"
    def_key = "def" if physical else "sp_def"
    ag = aggregate_stats(attacker, state.rules)
    dg = aggregate_stats(defender, state.rules)
    return DamageQuery(
        attacker_id=attacker.id,
        defender_id=defender.id,
        power=skill.power,
        damage_kind=skill.kind,
        skill_type=skill.type,
        attacker_types=list(attacker.types),
        defender_types=list(defender.types),
        multiplier=effectiveness * stab * counter_mult,
        reduction=reduction,
        base_atk=max(1, ag[atk_key]),
        base_def=max(1, dg[def_key]),
    )


def apply_modifier(q: DamageQuery, *, multiplier: float | None = None,
                   reduction: float | None = None) -> DamageQuery:
    """管线修饰器：替换乘子 / 减伤（纯函数，返回新 Query 不修改入参）。"""
    return replace(q, multiplier=multiplier if multiplier is not None else q.multiplier,
                   reduction=reduction if reduction is not None else q.reduction)


def compute(q: DamageQuery, rules) -> int:
    """管线终点：`power × (atk/def) × rules.damage_coefficient × multiplier × (1−reduction)`。

    物理/魔法读对应攻防；atk/def 下限 1；全程 float、出口 int() 一次；
    `power <= 0` → 0；保底 `rules.min_damage`。
    """
    if q.power <= 0:
        return 0
    raw = (q.base_atk / q.base_def) * q.power * rules.damage_coefficient * q.multiplier \
        * (1 - q.reduction)
    return max(rules.min_damage, int(raw))


# 兼容旧入口：`damage.compute_damage` 用到的克制/STAB 计算也抽到这里，单一事实源。
def effectiveness(skill_type: str, defender_types: list[str]) -> float:
    """克制倍率（纯函数）。"""
    return type_effectiveness(skill_type, defender_types)


def stab(skill_type: str, attacker_types: list[str]) -> float:
    """本系加成（纯函数）。"""
    return stab_multiplier(skill_type, attacker_types)
