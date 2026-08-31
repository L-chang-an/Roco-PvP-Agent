"""首领化道具测试（2026-08-30）：一阶进化 / 多分支选择 / 门控 / 入场触发 / 保留语义。

已拍板口径：首领化 = **boss 上一阶**的一阶进化（多分支只有迪莫 4/魔力猫 2，其余与
地区形态一一对应）；萌化层数 > 0 → 不可首领化；原地替换 name/types/base_stats/
stats/trait（unit_id 不变、技能/stat_mods/能量保留、HP 同比例缩放）；发
UnitEntered(from_boss=True) 入场触发。
"""

from __future__ import annotations

from environment.actions import Decision, boss_evolution_options, recharge_action
from environment.actions import skill_action, validate_decision
from environment.atom import AddModifier
from environment.engine import execute_turn
from environment.evolution import base_stats_of
from environment.models import (BattleRng, BattleState, SideState, SkillInstance,
                                Unit)
from environment.pipeline import run
from environment.primitives import apply_mark
from environment.reducer import Frame
from environment.rules import DEFAULT_RULES
from environment.statline import calc_combat_stats


def _boss_unit(name: str, hp_ratio: float = 1.0) -> Unit:
    base = base_stats_of(name)
    stats = calc_combat_stats(base, {}, "坦率")
    u = Unit(name=name, types=["草"], base_stats=dict(base), stats=dict(stats),
             nature="坦率", bloodline="首领", iv={}, max_hp=stats["hp"],
             current_hp=max(1, int(stats["hp"] * hp_ratio)), energy=10)
    u.current_skills = [SkillInstance(name="抓挠", desc="", type="普通", kind="物攻",
                                      energy_cost=0, power=35)]
    return u


def _state(unit: Unit) -> BattleState:
    unit.id = "a-0-x"
    foe = Unit(name="迪莫", types=["光"],
               stats={"hp": 300, "atk": 100, "sp_atk": 100, "def": 100, "sp_def": 100,
                      "speed": 100},
               max_hp=300, current_hp=300, energy=10)
    foe.id = "b-0-y"
    return BattleState(side_a=SideState(units=[unit], lives=2,
                                        item_uses={"首领进化": 1}),
                       side_b=SideState(units=[foe], lives=2),
                       rng=BattleRng(7), rules=DEFAULT_RULES, turn=2)


def _morph(s: BattleState, layers: int) -> None:
    u = s.active("a")
    run(s, [AddModifier(side="a", unit=u, stat="萌化", mode="special",
                        layers=layers, source="退化")], Frame())


# ── 一阶进化 ──
def test_boss_single_branch_evolves() -> None:
    s = _state(_boss_unit("火神"))
    u = s.active("a")
    old_id, old_skills = u.id, [sk.name for sk in u.current_skills]
    events = execute_turn(s, Decision(skill_action(0), item="首领进化"),
                          Decision(recharge_action()))
    assert any(e["type"] == "boss_evolution" and e["to"] == "烈火战神" for e in events)
    assert u.name == "烈火战神"
    assert u.id == old_id                             # unit_id 不漂移
    assert [sk.name for sk in u.current_skills] == old_skills   # 技能保留
    assert u.base_stats == base_stats_of("烈火战神")
    assert s.side_a.item_uses["首领进化"] == 0


def test_boss_multi_branch_with_arg() -> None:
    s = _state(_boss_unit("魔力猫"))
    execute_turn(s, Decision(skill_action(0), item="首领进化", item_arg="武斗酷猫"),
                 Decision(recharge_action()))
    assert s.active("a").name == "武斗酷猫"


def test_boss_multi_branch_other_branch() -> None:
    s = _state(_boss_unit("迪莫"))
    execute_turn(s, Decision(skill_action(0), item="首领进化", item_arg="圣水迪莫"),
                 Decision(recharge_action()))
    assert s.active("a").name == "圣水迪莫"


# ── 门控 ──
def test_boss_multi_branch_requires_arg() -> None:
    s = _state(_boss_unit("魔力猫"))
    reason = validate_decision(s, "a", Decision(skill_action(0), item="首领进化"))
    assert reason is not None and "分支" in reason


def test_boss_rejected_for_non_prev_stage() -> None:
    s = _state(_boss_unit("喵喵"))                    # 喵喵不是 boss 上一阶
    reason = validate_decision(s, "a", Decision(skill_action(0), item="首领进化"))
    assert "无首领血脉" in reason
    assert boss_evolution_options(s, "a") == []


def test_boss_rejected_for_non_boss_bloodline() -> None:
    """血脉不是「首领」（如「草」系别血脉）→ 不能首领化（2026-08-30）。"""
    s = _state(_boss_unit("魔力猫"))
    s.active("a").bloodline = "草"                     # 系别血脉，非首领血脉
    reason = validate_decision(s, "a", Decision(skill_action(0), item="首领进化",
                                                item_arg="武斗酷猫"))
    assert "血脉不是「首领」" in reason
    assert boss_evolution_options(s, "a") == []


def test_boss_rejected_while_morphed() -> None:
    s = _state(_boss_unit("魔力猫"))
    _morph(s, 1)                                      # 萌化 1 层 → 资质已退化
    reason = validate_decision(s, "a", Decision(skill_action(0), item="首领进化",
                                                item_arg="武斗酷猫"))
    assert reason is not None and "萌化" in reason
    assert boss_evolution_options(s, "a") == []


def test_boss_rejected_bad_branch() -> None:
    s = _state(_boss_unit("魔力猫"))
    reason = validate_decision(s, "a", Decision(skill_action(0), item="首领进化",
                                                item_arg="叶冕魔力猫x"))
    assert reason is not None and "分支" in reason


# ── 保留语义 ──
def test_boss_keeps_stat_mods() -> None:
    s = _state(_boss_unit("魔力猫"))
    u = s.active("a")
    run(s, [AddModifier(side="a", unit=u, stat="atk", mode="pct", layers=2,
                        source="测试")], Frame())
    execute_turn(s, Decision(skill_action(0), item="首领进化", item_arg="叶冕魔力猫"),
                 Decision(recharge_action()))
    assert any(m.stat == "atk" and m.layers == 2 for m in u.stat_mods)   # 增减益保留


def test_boss_hp_scales_proportionally() -> None:
    s = _state(_boss_unit("火神", hp_ratio=0.5))
    u = s.active("a")
    old_max, old_cur = u.max_hp, u.current_hp
    execute_turn(s, Decision(skill_action(0), item="首领进化"), Decision(recharge_action()))
    assert u.current_hp == max(1, int(u.max_hp * (old_cur / old_max)))


# ── 入场触发 ──
def test_boss_triggers_enter_effects() -> None:
    s = _state(_boss_unit("火神"))
    apply_mark(s.side_a, "棘刺印记", 1)               # 入场失 6% 生命
    events = execute_turn(s, Decision(skill_action(0), item="首领进化"),
                          Decision(recharge_action()))
    assert any(e["type"] == "damage" and e.get("attacker") == "棘刺印记" for e in events)


# ── 首领化后萌化（路线唯一）──
def test_boss_form_can_morph_down() -> None:
    s = _state(_boss_unit("魔力猫"))
    execute_turn(s, Decision(skill_action(0), item="首领进化", item_arg="叶冕魔力猫"),
                 Decision(recharge_action()))
    u = s.active("a")
    assert u.name == "叶冕魔力猫"
    _morph(s, 1)
    assert u.stats == calc_combat_stats(base_stats_of("魔力猫"), {}, "坦率")
    _morph(s, -1)                                     # 解除 → 回升首领资质
    assert u.stats == calc_combat_stats(base_stats_of("叶冕魔力猫"), {}, "坦率")


# ── 序列化 ──
def test_boss_serialization_roundtrip() -> None:
    s = _state(_boss_unit("魔力猫"))
    execute_turn(s, Decision(skill_action(0), item="首领进化", item_arg="武斗酷猫"),
                 Decision(recharge_action()))
    r = BattleState.from_dict(s.to_dict())
    assert r.active("a").name == "武斗酷猫"
    assert r.state_hash() == s.state_hash()
