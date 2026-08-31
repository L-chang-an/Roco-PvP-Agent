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
    assert cost_mod.permanent is False                     # 非永久：换人即清除（2026-08-30 修正）
    # 冻结本身不含能耗副作用：base 1 + 捉迷藏 1 = 2（2026-08-30 修正）
    assert skill_energy_cost(s, "b", b, 1, None) == 2


def test_hide_and_seek_ignores_weather_freeze() -> None:
    """天气冻结（全局来源，非精灵自己直接造成）不触发捉迷藏——捉迷藏只在精灵
    自己直接施冻结时给敌方 +1 能耗（2026-08-30 修正）。"""
    from environment.engine import end_turn
    from environment.weather import set_weather

    a = _unit("甲", _sk(("抓挠", "普通", "物攻", 1, 30)), trait=_trait("捉迷藏"))
    b = _unit("乙", _sk(("抓挠", "普通", "物攻", 1, 30)))
    s = _state(a, b)
    set_weather(s, "暴风雪", 8, "冬至")
    end_turn(s)
    assert freeze_layers(b) == 2                            # 天气给双方冻结
    assert all(m.stat != "energy_cost" for m in b.stat_mods)  # 但捉迷藏不触发 → 无能耗层


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


# ── 抓到你了：入场 → 敌方 +2 冻结；施冻结 → 敌方能耗 +1 ──
def test_caught_you_enter_freezes_foe() -> None:
    from environment.actions import switch_action
    from environment.engine import resolve_turn

    a1 = _unit("甲", _sk(("抓挠", "普通", "物攻", 1, 30)))
    a2 = _unit("丙", _sk(("抓挠", "普通", "物攻", 1, 30)), trait=_trait("抓到你了"))
    b = _unit("乙", _sk(("抓挠", "普通", "物攻", 1, 30)))
    a1.id, a2.id, b.id = "a-0-甲", "a-1-丙", "b-0-乙"
    s = BattleState(side_a=SideState(units=[a1, a2], lives=2),
                    side_b=SideState(units=[b], lives=2),
                    rng=BattleRng(7), rules=DEFAULT_RULES, turn=2)
    resolve_turn(s, Decision(switch_action(1)), Decision(recharge_action()))
    assert freeze_layers(b) == 2                        # 入场 → 敌方 +2 冻结


# ── 大雪球：2 次不同冰系技能 → 敌方 +4 冻结并重置 ──
def test_big_snowball_two_different_ice_skills() -> None:
    a = _unit("甲", _sk(("碎冰冰", "冰", "魔攻", 1, 50), ("霜降", "冰", "状态", 1, 0)),
              trait=_trait("大雪球"))
    b = _unit("乙", _sk(("抓挠", "普通", "物攻", 1, 30)))
    s = _state(a, b)
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert freeze_layers(b) == 0                        # 第 1 个冰系技能：仅计数
    assert a.trait.kwargs.get("used_skills") == ["碎冰冰"]
    b.current_hp = b.max_hp
    execute_turn(s, Decision(skill_action(1)), Decision(recharge_action()))
    assert freeze_layers(b) == 8                        # 霜降 4 层 + 大雪球 +4 层
    assert a.trait.kwargs.get("used_skills") == []      # 特性重置


# ── 月牙雪糕：攻击技能 + 敌方每层冻结 → 施 1 层星陨印记 ──
def test_mooncake_attack_marks_star_meteor() -> None:
    a = _unit("甲", _sk(("抓挠", "普通", "物攻", 1, 30)), trait=_trait("月牙雪糕"))
    b = _unit("乙", _sk(("抓挠", "普通", "物攻", 1, 30)))
    s = _state(a, b)
    _set_freeze_layers(b, 3)
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    marks = [m for m in s.side_b.negative_marks if m.name == "星陨印记"]
    assert marks and marks[0].layers == 3               # 每层冻结 → 1 层星陨


# ── 吉利丁片：离场 → 入场精灵双防+20% 且免疫冻结 ──
def test_gelatin_enter_buff_and_freeze_immunity() -> None:
    from environment.actions import switch_action
    from environment.engine import resolve_turn

    a1 = _unit("甲", _sk(("抓挠", "普通", "物攻", 1, 30)), trait=_trait("吉利丁片"))
    a2 = _unit("丙", _sk(("抓挠", "普通", "物攻", 1, 30)), trait=_trait("default"))
    b = _unit("乙", _sk(("霜降", "冰", "状态", 1, 0)))
    a1.id, a2.id, b.id = "a-0-甲", "a-1-丙", "b-0-乙"
    s = BattleState(side_a=SideState(units=[a1, a2], lives=2),
                    side_b=SideState(units=[b], lives=2),
                    rng=BattleRng(7), rules=DEFAULT_RULES, turn=2)
    resolve_turn(s, Decision(switch_action(1)), Decision(recharge_action()))
    gains = a2.trait.gains
    assert any(g.stat == "def" and g.layers == 2 for g in gains)      # 双防 +20%
    assert any(g.stat == "免疫冻结" for g in gains)
    # 免疫冻结：霜降施冻结 → 拦截
    execute_turn(s, Decision(recharge_action()), Decision(skill_action(0)))
    assert freeze_layers(a2) == 0


# ── 冰雪魂魄：暴风雪天气 + 敌方队伍冻结层 → 冰系威力加成 ──
def test_ice_soul_blizzard_power_bonus() -> None:
    from environment.weather import set_weather

    a = _unit("甲", _sk(("碎冰冰", "冰", "魔攻", 1, 100)), types=("冰",),
              trait=_trait("冰雪魂魄"))
    b = _unit("乙", _sk(("抓挠", "普通", "物攻", 1, 30)))
    s = _state(a, b)
    set_weather(s, "暴风雪", 8, "冬至")
    _set_freeze_layers(b, 2)
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    dmg = [e for e in events if e["type"] == "damage" and e["side"] == "a"][0]["damage"]
    # 碎冰冰威力 +20×2 层 → 140；冰雪魂魄 +10%×2 层 → ×1.2；冰系 STAB ×1.25
    assert dmg == 189


# ── 结晶水：初始能量 0 + 入场回 3×己方冰系技能次数 ──
def test_crystal_water_energy_zero_and_refund() -> None:
    from environment.actions import switch_action
    from environment.engine import resolve_turn
    from environment.models import build_unit

    spec = {"id": "a-0-水蓝蓝", "name": "水蓝蓝", "types": ["水"],
            "base_stats": {"hp": 100, "atk": 80, "sp_atk": 80, "def": 80, "sp_def": 80,
                           "speed": 80},
            "stats": {"hp": 300, "atk": 100, "sp_atk": 100, "def": 100, "sp_def": 100,
                      "speed": 100},
            "skills": ["抓挠"], "nature": "坦率", "bloodline": "", "iv": {},
            "trait": "结晶水"}
    a1 = _unit("甲", _sk(("碎冰冰", "冰", "魔攻", 1, 50)))
    a2 = build_unit(spec)
    b = _unit("乙", _sk(("抓挠", "普通", "物攻", 1, 30)))
    a1.id = "a-0-甲"
    b.id = "b-0-乙"
    s = BattleState(side_a=SideState(units=[a1, a2], lives=2),
                    side_b=SideState(units=[b], lives=2),
                    rng=BattleRng(7), rules=DEFAULT_RULES, turn=2)
    assert a2.energy == 0                               # 初始能量 0
    # 甲用 2 次冰系技能 → 计数 2
    for _ in range(2):
        execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert s.ice_skills_used.get("a") == 2
    # 结晶水换上场 → 回 3×2 = 6 能量
    a2.energy = 0
    resolve_turn(s, Decision(switch_action(1)), Decision(recharge_action()))
    assert a2.energy == 6


def _set_freeze_layers(u: Unit, layers: int) -> None:
    from environment.models import StatModifier

    for m in u.stat_mods:
        if m.stat == "冻结":
            m.layers = layers
            return
    u.stat_mods.append(StatModifier(stat="冻结", mode="special", layers=layers,
                                    permanent=True, source="测试", kwargs={"pct": 5}))
