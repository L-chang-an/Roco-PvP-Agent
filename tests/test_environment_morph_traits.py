"""萌化批特性测试（2026-08-30）：无忧无虑（层数上限豁免）/自由飘（萌化→连击）/
守望者（防御应对施萌化）/拉拉队长（萌化中再获得→解除）/守护者（入场能耗减）/
迎宾（离场→入场精灵萌化）/化茧（致命免伤+萌化，最多 2 次）。
"""

from __future__ import annotations

from environment.actions import Decision, recharge_action, skill_action, switch_action
from environment.atom import AddModifier
from environment.engine import execute_turn, resolve_turn
from environment.evolution import base_stats_of
from environment.models import BattleRng, BattleState, SideState, SkillInstance, Unit
from environment.pipeline import run
from environment.reducer import Frame
from environment.rules import DEFAULT_RULES
from environment.statline import calc_combat_stats
from environment.statuses import morph_layers


def _morph_unit(name: str, skills, trait=None, hp_ratio: float = 1.0) -> Unit:
    base = base_stats_of(name)
    stats = calc_combat_stats(base, {}, "坦率")
    u = Unit(name=name, types=["草"], base_stats=dict(base), stats=dict(stats),
             nature="坦率", iv={}, max_hp=stats["hp"],
             current_hp=max(1, int(stats["hp"] * hp_ratio)), energy=10)
    u.current_skills = list(skills)
    u.skills = list(skills)
    if trait is not None:
        from environment.models import TraitState

        u.trait = TraitState(name=trait, desc="")
    return u


def _sk(*specs) -> list[SkillInstance]:
    return [SkillInstance(name=n, desc="", type=t, kind=k, energy_cost=c, power=p)
            for n, t, k, c, p in specs]


def _state(a, b, seed: int = 7, turn: int = 2) -> BattleState:
    a.id, b.id = "a-0-x", "b-0-y"
    return BattleState(side_a=SideState(units=[a], lives=2),
                       side_b=SideState(units=[b], lives=2),
                       rng=BattleRng(seed), rules=DEFAULT_RULES, turn=turn)


def _morph(s: BattleState, unit: Unit, layers: int) -> None:
    run(s, [AddModifier(side="a", unit=unit, stat="萌化", mode="special",
                        layers=layers, source="测试")], Frame())


# ── 无忧无虑：萌化层数不受限制（资质仍夹最低阶）──
def test_carefree_unlimited_morph_layers() -> None:
    a = _morph_unit("魔力猫", _sk(("抓挠", "普通", "物攻", 1, 30)), trait="无忧无虑")
    b = _morph_unit("魔力猫", _sk(("抓挠", "普通", "物攻", 1, 30)))
    s = _state(a, b)
    _morph(s, a, 5)
    assert morph_layers(a) == 5                        # 超过最大可退阶数 2 → 不受限制
    assert a.stats == calc_combat_stats(base_stats_of("喵喵"), {}, "坦率")  # 资质夹最低阶


def test_carefree_vs_normal_cap() -> None:
    a = _morph_unit("魔力猫", _sk(("抓挠", "普通", "物攻", 1, 30)))   # 无特性
    b = _morph_unit("魔力猫", _sk(("抓挠", "普通", "物攻", 1, 30)))
    s = _state(a, b)
    _morph(s, a, 5)
    assert morph_layers(a) == 2                        # 正常上限：夹到 2


# ── 自由飘：自己每有 1 层萌化 → 连击数 +3 ──
def test_free_float_combo_per_morph_layer() -> None:
    from environment.primitives import combo_bonus

    a = _morph_unit("魔力猫", _sk(("抓挠", "普通", "物攻", 1, 30)), trait="自由飘")
    b = _morph_unit("魔力猫", _sk(("抓挠", "普通", "物攻", 1, 30)))
    s = _state(a, b)
    assert combo_bonus(a)[0] == 0                      # 无萌化
    _morph(s, a, 2)
    assert combo_bonus(a)[0] == 6                      # 2 层萌化 → +6 连击


# ── 守望者：防御应对成功 → 敌方获得萌化 ──
def test_watchman_counter_morphs_foe() -> None:
    a = _morph_unit("魔力猫", _sk(("抓挠", "普通", "物攻", 1, 30)))
    b = _morph_unit("魔力猫", _sk(("防御", "普通", "防御", 1, 0)), trait="守望者")
    s = _state(a, b)
    execute_turn(s, Decision(skill_action(0)), Decision(skill_action(0)))
    assert morph_layers(a) == 1                        # b 防御应对 a 攻击 → a 获得萌化


# ── 拉拉队长：萌化状态下再获得萌化 → 解除 ──
def test_cheerleader_reapply_morph_removes() -> None:
    a = _morph_unit("魔力猫", _sk(("退化", "普通", "状态", 1, 0)), trait="拉拉队长")
    b = _morph_unit("魔力猫", _sk(("抓挠", "普通", "物攻", 1, 30)))
    s = _state(a, b)
    _morph(s, a, 1)                                    # 已有 1 层萌化
    # 用「退化」技能 → 敌方获得萌化？不，退化目标是敌方。用直接施萌化给 a
    _morph(s, a, 1)                                    # 再获得萌化 → 解除
    assert morph_layers(a) == 0
    assert a.stats == calc_combat_stats(base_stats_of("魔力猫"), {}, "坦率")


# ── 守护者：己方其他精灵每有 1 层萌化，入场时全技能能耗 −1 ──
def test_guardian_cost_on_enter() -> None:
    from environment.primitives import skill_energy_cost

    a1 = _morph_unit("魔力猫", _sk(("抓挠", "普通", "物攻", 1, 30)))
    a2 = _morph_unit("喵喵", _sk(("抓挠", "普通", "物攻", 1, 30)), trait="守护者")
    b = _morph_unit("魔力猫", _sk(("抓挠", "普通", "物攻", 1, 30)))
    a1.id, a2.id, b.id = "a-0-x", "a-1-z", "b-0-y"
    s = BattleState(side_a=SideState(units=[a1, a2], lives=2),
                    side_b=SideState(units=[b], lives=2),
                    rng=BattleRng(7), rules=DEFAULT_RULES, turn=2)
    _morph(s, a1, 2)                                   # 己方其他精灵 2 层萌化
    resolve_turn(s, Decision(switch_action(1)), Decision(recharge_action()))
    assert s.active("a").name == "喵喵"
    assert skill_energy_cost(s, "a", a2, 1, None) == 0  # base 1 − 2 层 → 夹 0
    assert skill_energy_cost(s, "a", a2, 5, None) == 3  # 5 − 2 = 3


# ── 迎宾：离场 → 入场精灵获得萌化 ──
def test_welcome_morphs_incoming() -> None:
    a1 = _morph_unit("魔力猫", _sk(("抓挠", "普通", "物攻", 1, 30)), trait="迎宾")
    a2 = _morph_unit("魔力猫", _sk(("抓挠", "普通", "物攻", 1, 30)))
    b = _morph_unit("魔力猫", _sk(("抓挠", "普通", "物攻", 1, 30)))
    a1.id, a2.id, b.id = "a-0-x", "a-1-z", "b-0-y"
    s = BattleState(side_a=SideState(units=[a1, a2], lives=2),
                    side_b=SideState(units=[b], lives=2),
                    rng=BattleRng(7), rules=DEFAULT_RULES, turn=2)
    resolve_turn(s, Decision(switch_action(1)), Decision(recharge_action()))
    assert morph_layers(a2) == 1                       # 迎宾持有者离场 → 入场精灵获得萌化


# ── 化茧：致命伤害 → 免伤 + 萌化（最多 2 次）──
def test_cocoon_blocks_lethal_twice() -> None:
    a = _morph_unit("魔力猫", _sk(("抓挠", "普通", "物攻", 1, 30)))
    b = _morph_unit("魔力猫", _sk(("抓挠", "普通", "物攻", 1, 30)), trait="化茧",
                    hp_ratio=0.05)
    s = _state(a, b)
    b.current_hp = 5                                   # 一击必杀的血量
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    immune = [e for e in events if e.get("immune") == "化茧"]
    assert immune and not b.fainted                    # 第 1 次免伤
    assert b.trait.kwargs.get("used") == 1
    assert morph_layers(b) == 1
    b.current_hp = 5
    events2 = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert not b.fainted                               # 第 2 次免伤
    assert b.trait.kwargs.get("used") == 2
    b.current_hp = 5
    events3 = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert b.fainted                                   # 第 3 次致命 → 不再免伤
