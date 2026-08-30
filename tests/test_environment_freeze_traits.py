"""冻结批特性测试（2026-08-30）：L1 灵魂灼伤（SKILL_RESOLVE 施对侧状态）/
冰封（敌方能耗+1 读钩子）/冻土（冰系技能计数 → 地系威力）。

后续 Gate 补 L2（加个雪球/捉迷藏/冰钻）与 L3（抓到你了/结晶水/大雪球/吉利丁片/
月牙雪糕/冰雪魂魄）。
"""

from __future__ import annotations

from environment.actions import Decision, recharge_action, skill_action
from environment.engine import execute_turn
from environment.models import (BattleRng, BattleState, SideState, SkillInstance,
                                StatModifier, TraitState, Unit)
from environment.primitives import skill_energy_cost
from environment.rules import DEFAULT_RULES
from environment.statuses import freeze_layers, morph_layers


def _unit(name: str, skills, types=("普通",), trait: TraitState | None = None,
          max_hp: int = 300) -> Unit:
    u = Unit(name=name, types=list(types),
             stats={"hp": max_hp, "atk": 100, "sp_atk": 100, "def": 100, "sp_def": 100,
                    "speed": 100},
             max_hp=max_hp, current_hp=max_hp, energy=10, trait=trait)
    u.current_skills = list(skills)
    u.skills = list(skills)
    return u


def _sk(*specs) -> list[SkillInstance]:
    return [SkillInstance(name=n, desc="", type=t, kind=k, energy_cost=c, power=p)
            for n, t, k, c, p in specs]


def _state(a, b, seed: int = 7, turn: int = 2) -> BattleState:
    a.id, b.id = "a-0-甲", "b-0-乙"
    return BattleState(side_a=SideState(units=[a], lives=2),
                       side_b=SideState(units=[b], lives=2),
                       rng=BattleRng(seed), rules=DEFAULT_RULES, turn=turn)


def _trait(name: str) -> TraitState:
    return TraitState(name=name, desc="")


# ── 灵魂灼伤：冰系技能 → 敌方 +4 灼烧；火系技能 → 敌方 +2 冻结 ──
def test_soul_burn_ice_skill_burns_foe() -> None:
    a = _unit("甲", _sk(("碎冰冰", "冰", "魔攻", 1, 50)), trait=_trait("灵魂灼伤"))
    b = _unit("乙", _sk(("抓挠", "普通", "物攻", 1, 30)))
    s = _state(a, b)
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    burn = next((m for m in b.stat_mods if m.stat == "灼烧"), None)
    assert burn is not None and burn.layers == 2   # 施 4 层 → 回合末减半为 2


def test_soul_burn_fire_skill_freezes_foe() -> None:
    a = _unit("甲", _sk(("火苗", "火", "物攻", 0, 30)), trait=_trait("灵魂灼伤"))
    b = _unit("乙", _sk(("抓挠", "普通", "物攻", 1, 30)))
    s = _state(a, b)
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert freeze_layers(b) == 2


def test_soul_burn_other_skill_no_effect() -> None:
    a = _unit("甲", _sk(("抓挠", "普通", "物攻", 1, 30)), trait=_trait("灵魂灼伤"))
    b = _unit("乙", _sk(("抓挠", "普通", "物攻", 1, 30)))
    s = _state(a, b)
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert b.stat_mods == []


# ── 冰封：敌方在场精灵带冰封 → 我方全技能能耗 +1 ──
def test_ice_seal_raises_foe_cost() -> None:
    a = _unit("甲", _sk(("抓挠", "普通", "物攻", 3, 30)))
    b = _unit("乙", _sk(("抓挠", "普通", "物攻", 1, 30)), trait=_trait("冰封"))
    s = _state(a, b)
    assert skill_energy_cost(s, "a", a, 3, None) == 4     # 敌方冰封 → +1
    assert skill_energy_cost(s, "b", b, 1, None) == 1     # 甲方无冰封


# ── 冻土：每携带 1 个冰系技能，地系技能威力 +10% ──
def test_frozen_soil_ground_power_per_ice_skill() -> None:
    a = _unit("甲", _sk(("扬沙", "地", "物攻", 1, 100), ("寒光", "冰", "魔攻", 1, 50),
                        ("抓挠", "普通", "物攻", 1, 30)), trait=_trait("冻土"))
    b = _unit("乙", _sk(("抓挠", "普通", "物攻", 1, 30)))
    s = _state(a, b)
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    dmg = [e for e in events if e["type"] == "damage" and e["side"] == "a"][0]["damage"]
    # 1 个冰系技能 → 地系 +10%：int(100×0.9×1.1) = 99
    assert dmg == 99


def test_frozen_soil_no_ice_skill_no_bonus() -> None:
    a = _unit("甲", _sk(("扬沙", "地", "物攻", 1, 100)), trait=_trait("冻土"))
    b = _unit("乙", _sk(("抓挠", "普通", "物攻", 1, 30)))
    s = _state(a, b)
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    dmg = [e for e in events if e["type"] == "damage" and e["side"] == "a"][0]["damage"]
    assert dmg == 90                                    # 无冰系技能 → 无加成


def test_frozen_soil_only_ground_type() -> None:
    a = _unit("甲", _sk(("抓挠", "普通", "物攻", 1, 100), ("寒光", "冰", "魔攻", 1, 50)),
              trait=_trait("冻土"))
    b = _unit("乙", _sk(("抓挠", "普通", "物攻", 1, 30)))
    s = _state(a, b)
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    dmg = [e for e in events if e["type"] == "damage" and e["side"] == "a"][0]["damage"]
    assert dmg == 112                                   # 非地系不吃加成（普通系 STAB 1.25）


# ── 加个雪球：使敌方获得冻结时，额外 +2 层 ──
def test_snowball_extra_freeze_on_apply() -> None:
    a = _unit("甲", _sk(("霜降", "冰", "状态", 1, 0)), trait=_trait("加个雪球"))
    b = _unit("乙", _sk(("抓挠", "普通", "物攻", 1, 30)))
    s = _state(a, b)
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert freeze_layers(b) == 6                        # 霜降 4 层 + 加个雪球 2 层


def test_snowball_no_infinite_loop() -> None:
    """source 守卫：加个雪球自身施加的冻结不再触发自身。"""
    a = _unit("甲", _sk(("霜降", "冰", "状态", 1, 0)), trait=_trait("加个雪球"))
    b = _unit("乙", _sk(("抓挠", "普通", "物攻", 1, 30)))
    s = _state(a, b)
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    # 只 +2 一次（若循环，层数会远超 6 且烧反应预算）
    assert freeze_layers(b) == 6
    assert all(e["type"] != "error" for e in events)


# ── 捉迷藏：使敌方获得冻结时，敌方全技能能耗 +1 ──
def test_hide_and_seek_cost_on_freeze() -> None:
    a = _unit("甲", _sk(("霜降", "冰", "状态", 1, 0)), trait=_trait("捉迷藏"))
    b = _unit("乙", _sk(("抓挠", "普通", "物攻", 1, 30)))
    s = _state(a, b)
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    cost_mod = next((m for m in b.stat_mods if m.stat == "energy_cost"), None)
    assert cost_mod is not None and cost_mod.layers == 1   # 敌方能耗 +1
    # base 1 + 捉迷藏 1 + 冻结固有副作用 4 = 6
    assert skill_energy_cost(s, "b", b, 1, None) == 6


# ── 冰钻：敌方技能栏总能耗每有 1 点，自己攻击威力 +10% ──
def test_ice_drill_power_per_foe_total_cost() -> None:
    a = _unit("甲", _sk(("扬沙", "地", "物攻", 1, 100)), types=("冰",), trait=_trait("冰钻"))
    b = _unit("乙", _sk(("抓挠", "普通", "物攻", 2, 30), ("音爆", "普通", "魔攻", 3, 130)))
    s = _state(a, b)
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    dmg = [e for e in events if e["type"] == "damage" and e["side"] == "a"][0]["damage"]
    # 敌方技能总能耗 2+3=5 → +50% 威力：int(100×0.9×1.5) = 135
    assert dmg == 135


def test_ice_drill_no_trait_no_bonus() -> None:
    a = _unit("甲", _sk(("扬沙", "地", "物攻", 1, 100)), types=("冰",))
    b = _unit("乙", _sk(("抓挠", "普通", "物攻", 2, 30), ("音爆", "普通", "魔攻", 3, 130)))
    s = _state(a, b)
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    dmg = [e for e in events if e["type"] == "damage" and e["side"] == "a"][0]["damage"]
    assert dmg == 90                                    # 无冰钻 → 无加成
