"""印记/天气技能入口测试（2026-08-30）：编译模式 + battle_ready 扩张 + 集成对局。

施加类技能（获得N层印记 / 应对施印 / 连击施印 / 设置天气）→ MW_EFFECTS 白名单；
驱散/偷取/转化/条件类不匹配任何模式 → 自动排除（下批）。
"""

from __future__ import annotations

from environment.actions import Decision, recharge_action, skill_action
from environment.engine import execute_turn
from environment.models import BattleRng, BattleState, SideState, SkillInstance, TraitState, Unit
from environment.rules import DEFAULT_RULES
from environment.skillbook import (MW_EFFECTS, SkillCategory, SkillMarkEffect, battle_ready,
                                   compile_effect)
from environment.dataset import DataSource, load_skills


def _unit(name: str, types=("普通",), trait=None) -> Unit:
    u = Unit(name=name, types=list(types),
             stats={"hp": 300, "atk": 100, "sp_atk": 100, "def": 100, "sp_def": 100,
                    "speed": 100},
             max_hp=300, current_hp=300, energy=10,
             trait=TraitState(name=trait) if trait else None)
    return u


def _state(turn: int = 2, seed: int = 7, a_trait=None) -> BattleState:
    a, b = _unit("甲", trait=a_trait), _unit("乙")
    a.id, b.id = "a-0-甲", "b-0-乙"
    return BattleState(side_a=SideState(units=[a], lives=2),
                       side_b=SideState(units=[b], lives=2),
                       rng=BattleRng(seed), rules=DEFAULT_RULES, turn=turn)


def _equip(u: Unit, *skills: tuple[str, str, str, int, int]) -> None:
    u.current_skills = [SkillInstance(name=n, desc="", type=t, kind=k, energy_cost=c,
                                      power=p) for n, t, k, c, p in skills]


# ── 编译模式 ──
def test_compile_plain_mark_grant() -> None:
    eff = compile_effect(load_skills(DataSource.FULL)["主场优势"])
    assert eff is not None and eff.category == SkillCategory.STATUS
    assert eff.mark_effects == (SkillMarkEffect("self", "攻击印记", 1),)


def test_compile_foe_mark_grant() -> None:
    eff = compile_effect(load_skills(DataSource.FULL)["速冻"])
    assert eff.mark_effects == (SkillMarkEffect("foe", "减速印记", 2),)


def test_compile_defense_counter_mark() -> None:
    eff = compile_effect(load_skills(DataSource.FULL)["潮汐"])
    assert eff is not None and eff.category == SkillCategory.DEFENSE
    assert eff.reduction_pct == 0.6
    assert eff.counter_mark_effects == (SkillMarkEffect("self", "湿润印记", 1),)


def test_compile_combo_mark() -> None:
    eff = compile_effect(load_skills(DataSource.FULL)["星链"])
    assert eff.hits == 2 and eff.combo_eligible
    assert eff.mark_effects == (SkillMarkEffect("foe", "星陨印记", 1),)


def test_compile_weather() -> None:
    eff = compile_effect(load_skills(DataSource.FULL)["落雨"])
    assert eff.set_weather == "雨天" and eff.weather_turns == 8


def test_mw_whitelist_pinned() -> None:
    """MW 白名单钉死：20 条印记施加 + 4 条天气设置（驱散/偷取/转化类不在内）。"""
    assert len(MW_EFFECTS) == 24
    assert set(MW_EFFECTS) == {
        "主场优势", "光合作用", "冥想", "冬至", "冰蛋壳", "加油", "增程电池", "委屈",
        "惊雷", "打湿", "星轨裂变", "星链", "棘刺", "沙涌", "潮汐", "疫病吐息",
        "纺纱", "落雨", "蓄势待发", "超维投射", "速冻", "降灵", "风起", "龙威",
    }


def test_battle_ready_includes_mw_and_excludes_dispel() -> None:
    assert battle_ready("打湿") and battle_ready("落雨") and battle_ready("星链")
    assert not battle_ready("焚烧烙印")   # 驱散类：下批


# ── 集成对局 ──
def test_dashi_grants_runshi_and_reduces_cost() -> None:
    """打湿 → 自己 1 层湿润 → 后续技能能耗 −1。"""
    from environment.primitives import skill_energy_cost

    s = _state()
    _equip(s.active("a"), ("打湿", "水", "状态", 4, 0), ("泡沫", "水", "魔攻", 4, 50))
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert s.side_a.positive_marks[0].name == "湿润印记"
    inst = s.active("a").current_skills[1]
    assert skill_energy_cost(s, "a", s.active("a"), 4, inst) == 3


def test_luoyu_sets_rain_and_boosts_water() -> None:
    """落雨 → 雨天 8 回合（回合末递减为 7）→ 水系攻击 ×1.75。"""
    s = _state()
    _equip(s.active("a"), ("落雨", "水", "状态", 5, 0), ("水花四溅", "水", "物攻", 1, 100))
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert s.weather is not None and s.weather.kind == "雨天" and s.weather.turns_left == 7
    events = execute_turn(s, Decision(skill_action(1)), Decision(recharge_action()))
    dmg = next(e for e in events if e["type"] == "damage")
    assert dmg["damage"] == 157                      # 100×0.9×1.75（水花四溅 4 连击按段）


def test_chaowei_star_full_chain() -> None:
    """超维投射 → 敌方 4 层星陨 → 火系攻击触发：威力 4²+24×3 = 88 → 79 幻伤 + 全清。"""
    s = _state()
    _equip(s.active("a"), ("超维投射", "幻", "状态", 4, 0), ("流火", "火", "魔攻", 1, 50))
    _equip(s.active("b"), ("抓挠", "普通", "物攻", 1, 30))
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert s.side_b.negative_marks[0].name == "星陨印记" and s.side_b.negative_marks[0].layers == 4
    events = execute_turn(s, Decision(skill_action(1)), Decision(recharge_action()))
    star = [e for e in events if e["type"] == "damage" and e["skill"] == "星陨印记"]
    assert len(star) == 1 and star[0]["damage"] == 79     # 88×0.9 = 79.2 → 79
    assert s.side_b.negative_marks == []


def test_xinglian_combo_grants_per_hit() -> None:
    """星链 2 连击 → 每次连击敌方 +1 层星陨 → 共 2 层。"""
    s = _state()
    _equip(s.active("a"), ("星链", "幻", "状态", 3, 0))
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert s.side_b.negative_marks[0].name == "星陨印记"
    assert s.side_b.negative_marks[0].layers == 2


def test_chaoxi_counter_grants_mark_only_vs_attack() -> None:
    """潮汐：应对攻击命中 → 自己 1 层湿润；对手非攻击 → 不施印。"""
    s = _state()
    _equip(s.active("a"), ("潮汐", "水", "防御", 4, 0))
    _equip(s.active("b"), ("抓挠", "普通", "物攻", 1, 30))
    execute_turn(s, Decision(skill_action(0)), Decision(skill_action(0)))
    assert s.side_a.positive_marks and s.side_a.positive_marks[0].name == "湿润印记"

    s2 = _state()
    _equip(s2.active("a"), ("潮汐", "水", "防御", 4, 0))
    execute_turn(s2, Decision(skill_action(0)), Decision(recharge_action()))
    assert s2.side_a.positive_marks == []


def test_lila_route_to_exclusive_space() -> None:
    """吟游之弦：施加的印记进独立空间（不顶替、共存）。"""
    s = _state(a_trait="吟游之弦")
    _equip(s.active("a"), ("主场优势", "普通", "状态", 3, 0))
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert s.side_a.positive_marks == []
    assert s.side_a.exclusive_marks[0].name == "攻击印记"
