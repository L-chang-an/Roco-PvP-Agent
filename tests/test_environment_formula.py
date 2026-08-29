"""伤害公式规范（2026-08-30 拍板）回归锚：比值项 / 威力加成 / flat 归属 / 应对先乘后加 / 预估体系。

旧 600 测试对「跨侧同向修正」等新旧公式有差异的场景零覆盖（实施后 600 全绿即证），
本文件把差异场景与预估体系钉死——新规则的唯一回归锚。
"""

from __future__ import annotations

from environment.damage import build_damage_terms, compute_damage, formula
from environment.models import (BattleState, SideState, Skill, SkillInstance,
                                StatModifier, Unit)
from environment.prediction import predict_damage, predict_power, predictions_for
from environment.rules import DEFAULT_RULES
from environment.skillbook import P1_EFFECTS, SkillCategory, SkillEffect

_EFFECT = next(iter(P1_EFFECTS.values()))
_P1_NAME = next(iter(P1_EFFECTS.keys()))


def _unit(name: str = "测试", types=("普通",), mods=(), stats=None) -> Unit:
    stats = stats or {"hp": 300, "atk": 100, "sp_atk": 100, "def": 100, "sp_def": 100, "speed": 100}
    u = Unit(name=name, types=list(types), stats=stats, max_hp=300, current_hp=300, energy=10)
    u.stat_mods = list(mods)
    return u


def _state(a_mods=(), b_mods=()) -> BattleState:
    return BattleState(side_a=SideState(units=[_unit("甲", mods=a_mods)], lives=2),
                       side_b=SideState(units=[_unit("乙", mods=b_mods)], lives=2),
                       rng=None, rules=DEFAULT_RULES)


def _skill(power: int = 100, kind: str = "物攻", type_: str = "普通",
           effect: SkillEffect | None = None) -> Skill:
    return Skill(name="测试", kind=kind, type=type_, power=power, energy_cost=1,
                 effect=effect or _EFFECT)


# ── 实际伤害：公式规范 ──
def test_ratio_cross_side_additive() -> None:
    """跨侧同向修正：我方 atk+2 层、敌方 def−2 层 → 比值 (1+0.2+0.2)/1 = 1.4。

    钉死两点：① 新规则已切换（不是旧乘性 135）；② 浮点顺序求值 int() 的边界值——
    ratio_num 的浮点值略低于 1.4（1.3999…），0.9×100×1.3999… = 125.999… → int 125
    （实数值 126，边界 −1，与旧引擎同类浮点截断行为一致）。
    """
    s = _state(a_mods=[StatModifier(stat="atk", mode="pct", layers=2, source="x")],
               b_mods=[StatModifier(stat="def", mode="pct", layers=-2, source="y")])
    dmg = compute_damage(s, s.active("a"), s.active("b"), _skill())
    assert dmg == 125
    assert dmg != 135   # 旧乘性 (1.2/0.8)=1.5 的伤害值——规则切换的对照锚


def test_single_side_buff_same_as_legacy() -> None:
    """单侧 buff：新旧公式逐位一致（比值 (1+0.2)/1 与乘进属性等价）。"""
    s = _state(a_mods=[StatModifier(stat="atk", mode="pct", layers=2, source="x")])
    assert compute_damage(s, s.active("a"), s.active("b"), _skill()) == 108


def test_attack_power_flat_and_pct() -> None:
    """attack_power 激活：flat 3 层（+30 威力）+ pct 2 层（+20%）→ (100+30)×0.9×1.2 = 140.4 → 140。"""
    s = _state(a_mods=[StatModifier(stat="attack_power", mode="flat", layers=3, source="x"),
                       StatModifier(stat="attack_power", mode="pct", layers=2, source="x")])
    assert compute_damage(s, s.active("a"), s.active("b"), _skill()) == 140


def test_flat_layers_stay_in_attribute() -> None:
    """flat 层留在属性：atk flat +2 层 → atk = 100+20 → 120×0.9 = 108（不进比值项）。"""
    s = _state(a_mods=[StatModifier(stat="atk", mode="flat", layers=2, source="x")])
    assert compute_damage(s, s.active("a"), s.active("b"), _skill()) == 108


def test_counter_mult_applied_before_flat_bonus() -> None:
    """应对倍率先乘基础威力、再加威力绝对值：100×1.5+30 = 180 → 162。"""
    s = _state(a_mods=[StatModifier(stat="attack_power", mode="flat", layers=3, source="x")])
    assert compute_damage(s, s.active("a"), s.active("b"), _skill(), counter_mult=1.5) == 162


def test_ratio_own_buff_and_foe_buff_cancel() -> None:
    """我方 atk+2 与敌方 def+2：比值 1.2/1.2 = 1 → 90（与旧乘性一致）。"""
    s = _state(a_mods=[StatModifier(stat="atk", mode="pct", layers=2, source="x")],
               b_mods=[StatModifier(stat="def", mode="pct", layers=2, source="y")])
    assert compute_damage(s, s.active("a"), s.active("b"), _skill()) == 90


def test_magic_uses_sp_atk_sp_def_ratio() -> None:
    """魔攻选边：sp_atk/sp_def 走同构比值项。"""
    s = _state(a_mods=[StatModifier(stat="sp_atk", mode="pct", layers=2, source="x")],
               b_mods=[StatModifier(stat="sp_def", mode="pct", layers=-2, source="y")])
    dmg = compute_damage(s, s.active("a"), s.active("b"), _skill(kind="魔攻"))
    assert dmg == 125   # 与物攻同构的边界值


def test_formula_power_term_zero_returns_zero() -> None:
    """power_term ≤ 0 → 0（不享受 min_damage 保底，与旧语义一致）。"""
    assert formula(atk=100, defense=100, power_term=0, ratio_num=1.0, ratio_den=1.0,
                   power_pct=1.0, stab=1.0, effectiveness=1.0, weather=1.0,
                   reduction=0.0, coefficient=0.9, min_damage=1) == 0


def test_formula_min_damage_floor() -> None:
    assert formula(atk=1, defense=900, power_term=10, ratio_num=1.0, ratio_den=1.0,
                   power_pct=1.0, stab=1.0, effectiveness=1.0, weather=1.0,
                   reduction=0.0, coefficient=0.9, min_damage=1) == 1


# ── 预估体系 ──
def test_predict_power_excludes_counter_mult() -> None:
    """预估威力不含应对倍率与连击数：atk+2/def−2 比值 1.4 → 100×1.4 = 140（浮点近似）。

    用火系技能（攻击方普通系）：eff=1、无 STAB，保持数值干净。
    """
    s = _state(a_mods=[StatModifier(stat="atk", mode="pct", layers=2, source="x")],
               b_mods=[StatModifier(stat="def", mode="pct", layers=-2, source="y")])
    p = predict_power(s, s.active("a"), s.active("b"), _skill(type_="火"))
    assert abs(p - 140.0) < 1e-9


def test_predict_damage_includes_deterministic_hits() -> None:
    """预估伤害 = int(atk/def × 0.9 × 预估威力 × 确定连击数)：3 连击 → int(0.9×100×3)=270。"""
    effect = SkillEffect(category=SkillCategory.ATTACK, hits=3, combo_eligible=True)
    s = _state()
    # 火 vs 普通：eff=1、无 STAB，保持数值干净
    dmg = predict_damage(s, s.active("a"), s.active("b"), _skill(type_="火", effect=effect), "a")
    assert dmg == 270


def test_predictions_for_shape_and_determinism() -> None:
    """观测提示：在场精灵逐技能预估（slot/skill/power/damage），两次调用逐位一致。"""
    s = _state()
    s.side_a.units[0].current_skills = [SkillInstance(name=_P1_NAME, desc="", type="普通",
                                                      kind="物攻", energy_cost=1, power=50)]
    r1 = predictions_for(s, "a")
    r2 = predictions_for(s, "a")
    assert r1 == r2
    assert len(r1) == 1
    assert set(r1[0]) == {"slot", "skill", "power", "damage"}
    assert r1[0]["slot"] == 0 and r1[0]["skill"] == _P1_NAME


def test_build_damage_terms_counter_mult_position() -> None:
    """build_damage_terms：应对倍率作用在基础威力上（先乘后加绝对值）。"""
    s = _state(a_mods=[StatModifier(stat="attack_power", mode="flat", layers=3, source="x")])
    t = build_damage_terms(s, s.active("a"), s.active("b"), damage_kind="物攻",
                           power=100, counter_mult=1.5)
    assert t.power_term == 180.0
    assert t.atk == 100 and t.defense == 100
