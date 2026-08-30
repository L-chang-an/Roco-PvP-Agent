"""冻结批 L1 技能测试（2026-08-30）：读冻结层读钩子（碎冰冰/冷凝/霜天）+
应对施状态（冰点/冰墙）+ 冻结固有副作用（每层全技能能耗+1）。

规则：碎冰冰敌方每层冻结威力+20；冷凝每层回1能量；霜天施1层冻结且敌方每层
冻结使其全技能能耗+1（固有副作用读钩子）；冰点应对防御额外+5层；冰墙应对攻击
施2层冻结；冰系免疫冻结（施冻拦截）。
"""

from __future__ import annotations

from environment.actions import Decision, recharge_action, skill_action
from environment.engine import execute_turn
from environment.models import BattleRng, BattleState, SideState, SkillInstance, Unit
from environment.primitives import skill_energy_cost
from environment.rules import DEFAULT_RULES
from environment.statuses import freeze_layers


def _unit(name: str, skills: list[tuple[str, str, str, int, int]],
          types=("普通",), max_hp: int = 300) -> Unit:
    u = Unit(name=name, types=list(types),
             stats={"hp": max_hp, "atk": 100, "sp_atk": 100, "def": 100, "sp_def": 100,
                    "speed": 100},
             max_hp=max_hp, current_hp=max_hp, energy=10)
    u.current_skills = [SkillInstance(name=n, desc="", type=t, kind=k, energy_cost=c,
                                      power=p) for n, t, k, c, p in skills]
    return u


def _state(a_skills, b_skills, a_types=("普通",), b_types=("普通",),
           seed: int = 7, turn: int = 2) -> BattleState:
    a = _unit("甲", a_skills, types=a_types)
    b = _unit("乙", b_skills, types=b_types)
    a.id = "a-0-甲"
    b.id = "b-0-乙"
    return BattleState(side_a=SideState(units=[a], lives=2),
                       side_b=SideState(units=[b], lives=2),
                       rng=BattleRng(seed), rules=DEFAULT_RULES, turn=turn)


def _freeze_record(u: Unit):
    return next((m for m in u.stat_mods if m.stat == "冻结"), None)


def _set_freeze(u: Unit, layers: int) -> None:
    from environment.models import StatModifier

    for m in u.stat_mods:
        if m.stat == "冻结":
            m.layers = layers
            return
    u.stat_mods.append(StatModifier(stat="冻结", mode="special", layers=layers,
                                    permanent=True, source="测试", kwargs={"pct": 5}))


# ── 碎冰冰：敌方每层冻结 → 威力 +20 ──
def test_shards_power_scales_with_freeze() -> None:
    s = _state([("碎冰冰", "冰", "魔攻", 1, 100)], [("抓挠", "普通", "物攻", 1, 30)])
    foe = s.active("b")
    base = [e for e in execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
            if e["type"] == "damage" and e["side"] == "a"][0]["damage"]
    # 给敌方 3 层冻结 → 威力 +60（power 100→160）
    _set_freeze(foe, 3)
    s.active("b").current_hp = s.active("b").max_hp
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    boosted = [e for e in events if e["type"] == "damage" and e["side"] == "a"][0]["damage"]
    assert boosted > base
    # 精确：atk=def=100、power 160 → int(160×0.9) = 144
    assert boosted == 144 and base == 90


# ── 冷凝：敌方每层冻结 → 自己回 1 能量 ──
def test_condensation_energy_per_freeze_layer() -> None:
    s = _state([("冷凝", "冰", "物攻", 1, 30)], [("抓挠", "普通", "物攻", 1, 30)])
    u = s.active("a")
    u.energy = 4
    _set_freeze(s.active("b"), 3)
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    gains = [e for e in events if e["type"] == "energy_gain" and e["side"] == "a"]
    assert gains and gains[0]["gained"] == 3
    assert u.energy == 6   # 4 − 1（技能能耗）+ 3


# ── 霜天：施冻结 + 冻结固有副作用（每层全技能能耗 +1）──
def test_frost_sky_applies_freeze_and_cost_hook() -> None:
    s = _state([("抓挠", "普通", "物攻", 3, 30)], [("抓挠", "普通", "物攻", 3, 30)])
    foe = s.active("b")
    _set_freeze(foe, 2)
    assert skill_energy_cost(s, "b", foe, 3, None) == 5   # 3 + 2 层冻结
    _set_freeze(foe, 0)
    assert skill_energy_cost(s, "b", foe, 3, None) == 3
    # 霜天施加 1 层 → 固有副作用自然生效
    s2 = _state([("霜天", "冰", "状态", 1, 0)], [("抓挠", "普通", "物攻", 1, 30)])
    execute_turn(s2, Decision(skill_action(0)), Decision(recharge_action()))
    assert freeze_layers(s2.active("b")) == 1
    assert skill_energy_cost(s2, "b", s2.active("b"), 1, None) == 2


# ── 冰点：基础 5 层 + 应对防御额外 5 层 ──
def test_ice_point_counter_extra_layers() -> None:
    s = _state([("冰点", "冰", "状态", 1, 0)], [("防御", "普通", "防御", 1, 0)])
    execute_turn(s, Decision(skill_action(0)), Decision(skill_action(0)))
    assert freeze_layers(s.active("b")) == 10   # 5 + 应对防御额外 5


def test_ice_point_no_counter_base_layers() -> None:
    s = _state([("冰点", "冰", "状态", 1, 0)], [("抓挠", "普通", "物攻", 1, 30)])
    execute_turn(s, Decision(skill_action(0)), Decision(skill_action(0)))
    assert freeze_layers(s.active("b")) == 5    # 未应对防御 → 只基础 5


# ── 冰墙：防御应对攻击 → 敌方 2 层冻结 ──
def test_ice_wall_counter_applies_freeze() -> None:
    s = _state([("抓挠", "普通", "物攻", 1, 30)], [("冰墙", "冰", "防御", 1, 0)])
    execute_turn(s, Decision(skill_action(0)), Decision(skill_action(0)))
    assert freeze_layers(s.active("a")) == 2    # b 用冰墙应对 a 的攻击 → a 获得 2 层冻结


# ── 冰系免疫冻结：施冻拦截 ──
def test_ice_immune_blocks_freeze_skills() -> None:
    s = _state([("霜天", "冰", "状态", 1, 0)], [("抓挠", "普通", "物攻", 1, 30)],
               b_types=("冰",))
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert freeze_layers(s.active("b")) == 0    # 冰系免疫冻结


# ── 滚雪球：基础施冻 + 应对状态额外施冻与威力翻倍 ──
def test_snowball_base_and_counter() -> None:
    s = _state([("滚雪球", "冰", "物攻", 1, 30)], [("抓挠", "普通", "物攻", 1, 30)])
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert freeze_layers(s.active("b")) == 2    # 基础 2 层（未应对状态）

    s2 = _state([("滚雪球", "冰", "物攻", 1, 30)], [("力量增效", "普通", "状态", 1, 0)])
    events = execute_turn(s2, Decision(skill_action(0)), Decision(skill_action(0)))
    assert freeze_layers(s2.active("b")) == 4   # 2 + 应对状态额外 2
    dmg = [e for e in events if e["type"] == "damage" and e["side"] == "a"][0]
    assert dmg["mult"] == 2.0                   # 应对状态 → 威力翻倍


# ── 极寒领域：有冻结威力+60 + 应对状态冻结翻倍 ──
def test_polar_realm_power_if_frozen() -> None:
    s = _state([("极寒领域", "冰", "魔攻", 1, 100)], [("抓挠", "普通", "物攻", 1, 30)])
    base = [e for e in execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
            if e["type"] == "damage" and e["side"] == "a"][0]["damage"]
    assert base == 90                           # 无冻结 → power 100
    _set_freeze(s.active("b"), 1)
    s.active("b").current_hp = s.active("b").max_hp
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    boosted = [e for e in events if e["type"] == "damage" and e["side"] == "a"][0]["damage"]
    assert boosted == 144                       # 有冻结 → power 160


def test_polar_realm_counter_doubles_freeze() -> None:
    s = _state([("极寒领域", "冰", "魔攻", 1, 100)], [("力量增效", "普通", "状态", 1, 0)])
    _set_freeze(s.active("b"), 3)
    execute_turn(s, Decision(skill_action(0)), Decision(skill_action(0)))
    assert freeze_layers(s.active("b")) == 6    # 应对状态 → 冻结翻倍 3→6
