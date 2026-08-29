"""印记系统测试（2026-08-30）：施加规则 + 14 种印记效果 + 读钩子 + 循环防护。

印记是阵营级（三槽）；效果作用于触发时该方在场精灵。所有行为只在印记存在时
生效——既有 621 测试（无印记对局）是零行为变化的硬闸。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from environment.actions import Decision, recharge_action, skill_action, switch_action
from environment.engine import apply_replacement, build_queue, build_turn_context, end_turn
from environment.engine import execute_turn, resolve_turn
from environment.events import ev
from environment.models import BattleRng, BattleState, SideState, Skill, SkillInstance
from environment.models import StatModifier, Unit
from environment.pipeline import run
from environment.primitives import apply_mark, consume_mark_layers, dispel_marks
from environment.primitives import skill_energy_cost
from environment.reducer import Frame
from environment.rules import DEFAULT_RULES
from environment.skillbook import P1_EFFECTS, P2_EFFECTS, SkillCategory


def _unit(name: str, types=("普通",), stats=None, max_hp: int = 300, energy: int = 10,
          base_stats=None) -> Unit:
    stats = stats or {"hp": max_hp, "atk": 100, "sp_atk": 100, "def": 100, "sp_def": 100,
                      "speed": 100}
    u = Unit(name=name, types=list(types), stats=stats, base_stats=dict(base_stats or {}),
             max_hp=max_hp, current_hp=max_hp, energy=energy)
    return u


def _state(turn: int = 2, a_units=("甲",), b_units=("乙",), seed: int = 7) -> BattleState:
    """1v1 默认；units 传入名字元组。unit_id 按 `{side}-{idx}-{name}` 生成。"""
    a = [_unit(n) for n in a_units]
    b = [_unit(n) for n in b_units]
    for i, u in enumerate(a):
        u.id = f"a-{i}-{u.name}"
    for i, u in enumerate(b):
        u.id = f"b-{i}-{u.name}"
    return BattleState(side_a=SideState(units=a, lives=2),
                       side_b=SideState(units=b, lives=2),
                       rng=BattleRng(seed), rules=DEFAULT_RULES, turn=turn)


def _real_attack(type_=None) -> tuple[str, str, str]:
    """P1∪P2 里找一条攻击技能 → (名, 系别, kind)。"""
    from environment.dataset import load_skills

    for name, eff in {**P1_EFFECTS, **P2_EFFECTS}.items():
        if eff.category != SkillCategory.ATTACK:
            continue
        raw = load_skills().get(name)
        if raw is None:
            continue
        if type_ is None or raw.type == type_:
            return name, raw.type, raw.kind
    raise RuntimeError(f"找不到系别 {type_} 的攻击技能")


def _equip(u: Unit, name: str, type_: str, kind: str, energy_cost: int, power: int) -> None:
    u.current_skills = [SkillInstance(name=name, desc="", type=type_, kind=kind,
                                      energy_cost=energy_cost, power=power)]


# ── 施加/消耗/驱散规则 ──
def test_apply_same_name_stacks_and_diff_name_replaces() -> None:
    s = _state()
    ss = s.side_a
    apply_mark(ss, "攻击印记", 1, source="战歌")
    apply_mark(ss, "攻击印记", 2, source="战歌")     # 同种叠加
    assert ss.positive_marks[0].layers == 3
    apply_mark(ss, "光合印记", 1, source="x")        # 异种顶替（每极性至多 1）
    assert len(ss.positive_marks) == 1 and ss.positive_marks[0].name == "光合印记"


def test_apply_negative_and_positive_are_independent_slots() -> None:
    s = _state()
    ss = s.side_a
    apply_mark(ss, "攻击印记", 1)
    apply_mark(ss, "减速印记", 2)
    assert ss.positive_marks[0].name == "攻击印记"
    assert ss.negative_marks[0].name == "减速印记" and ss.negative_marks[0].layers == 2


def test_apply_exclusive_space_coexists() -> None:
    s = _state()
    ss = s.side_a
    apply_mark(ss, "攻击印记", 1)
    apply_mark(ss, "湿润印记", 2, space="exclusive")   # 独立空间：不顶替、共存
    assert ss.positive_marks[0].name == "攻击印记" and ss.positive_marks[0].layers == 1
    assert ss.exclusive_marks[0].name == "湿润印记"
    apply_mark(ss, "湿润印记", 3, space="exclusive")   # 独立空间内同种叠加
    assert ss.exclusive_marks[0].layers == 5


def test_apply_unknown_mark_raises() -> None:
    with pytest.raises(ValueError):
        apply_mark(_state().side_a, "不存在的印记", 1)


def test_dispel_marks_scopes() -> None:
    s = _state()
    ss = s.side_a
    apply_mark(ss, "攻击印记", 2)
    apply_mark(ss, "减速印记", 3)
    apply_mark(ss, "湿润印记", 4, space="exclusive")
    assert dispel_marks(ss, scope="normal") == 5
    assert ss.positive_marks == [] and ss.negative_marks == []
    assert len(ss.exclusive_marks) == 1                # normal 不清独立空间
    assert dispel_marks(ss, scope="all") == 4
    assert ss.exclusive_marks == []


def test_consume_mark_layers_partial_and_full() -> None:
    s = _state()
    ss = s.side_a
    apply_mark(ss, "中毒印记", 5)
    assert consume_mark_layers(ss, "中毒印记", 2).layers == 3
    assert consume_mark_layers(ss, "中毒印记", 3) is None   # 归零 → 移除
    assert ss.negative_marks == []


# ── 读钩子：能耗（湿润/蓄势/沙暴）──
def test_runshi_reduces_all_skill_cost() -> None:
    s = _state()
    apply_mark(s.side_a, "湿润印记", 2)
    name, type_, kind = _real_attack()
    _equip(s.active("a"), name, type_, kind, energy_cost=3, power=50)
    assert skill_energy_cost(s, "a", s.active("a"), 3, s.active("a").current_skills[0]) == 1


def test_xushi_increases_attack_cost_only() -> None:
    s = _state()
    apply_mark(s.side_a, "蓄势印记", 1)
    atk = s.active("a")
    name, type_, kind = _real_attack()
    _equip(atk, name, type_, kind, energy_cost=3, power=50)
    assert skill_energy_cost(s, "a", atk, 3, atk.current_skills[0]) == 4   # 攻击 +1
    # 状态技能不受蓄势能耗影响
    st = SkillInstance(name=name, desc="", type=type_, kind="状态", energy_cost=3, power=0)
    assert skill_energy_cost(s, "a", atk, 3, st) == 3


# ── 读钩子：伤害公式（攻击/蓄势/蓄电/风起）──
def _dmg_with_mark(state, mark_name, layers, *, acted_first=False, power=100,
                   kind="物攻", type_="火"):
    from environment.damage import build_damage_terms

    apply_mark(state.side_a, mark_name, layers)
    attacker, defender = state.active("a"), state.active("b")
    terms = build_damage_terms(state, attacker, defender, damage_kind=kind,
                               power=power, counter_mult=1.0, side="a",
                               skill_type=type_, acted_first=acted_first)
    from environment.damage import formula

    return formula(atk=terms.atk, defense=terms.defense, power_term=terms.power_term,
                   ratio_num=terms.ratio_num, ratio_den=terms.ratio_den,
                   power_pct=terms.power_pct, stab=1.0, effectiveness=1.0,
                   weather=terms.weather, reduction=0.0, coefficient=0.9, min_damage=1)


def test_gongji_mark_boosts_power_pct() -> None:
    base = _dmg_with_mark(_state(), "攻击印记", 0)          # 无印记 90
    boosted = _dmg_with_mark(_state(), "攻击印记", 2)       # +20% → 108
    assert base == 90 and boosted == 108


def test_xushi_mark_boosts_attack_power() -> None:
    assert _dmg_with_mark(_state(), "蓄势印记", 1) == 117   # +30% → 117


def test_xudian_mark_adds_flat_power() -> None:
    assert _dmg_with_mark(_state(), "蓄电印记", 2) == 108   # +10×2 威力 → 108


def test_fengqi_mark_requires_acted_first() -> None:
    assert _dmg_with_mark(_state(), "风起印记", 1, acted_first=False) == 90
    assert _dmg_with_mark(_state(), "风起印记", 1, acted_first=True) == 108   # +20%


# ── 读钩子：减速印记（出手顺序）──
def test_jiansu_mark_swaps_turn_order() -> None:
    s = _state()
    s.active("a").stats["speed"] = 100
    s.active("b").stats["speed"] = 95
    apply_mark(s.side_a, "减速印记", 1)                     # a 速度 100→90 < 95
    dec_a, dec_b = Decision(skill_action(0)), Decision(skill_action(0))
    _equip_attacks(s)
    ctx, _ = build_turn_context(s, dec_a, dec_b)
    order = [e.side for e in build_queue(s, ctx)]
    assert order[0] == "b" and order[1] == "a"


def _equip_attacks(s) -> None:
    name, type_, kind = _real_attack()
    for side in ("a", "b"):
        _equip(s.active(side), name, type_, kind, energy_cost=1, power=30)


# ── TURN_END：光合 / 中毒印记 ──
def test_guanghe_grants_energy_at_turn_end() -> None:
    s = _state()
    apply_mark(s.side_a, "光合印记", 2)
    s.active("a").energy = 5
    end_turn(s)
    assert s.active("a").energy == 7


def test_guanghe_skips_empty_field() -> None:
    s = _state()
    apply_mark(s.side_a, "光合印记", 1)
    s.active("a").fainted = True
    end_turn(s)   # 不崩溃、无效果（阵亡单位不可回复能量）
    assert s.active("a").energy == 10


def test_zhongdu_mark_damages_active_at_turn_end() -> None:
    s = _state()
    apply_mark(s.side_a, "中毒印记", 2)                     # 3%×2 = 6% × 300 = 18
    events = end_turn(s)
    assert s.active("a").current_hp == 282
    assert any(e["type"] == "damage" and e["attacker"] == "中毒印记" for e in events)


def test_zhongdu_mark_faint_handled_by_next_turn_guard() -> None:
    """回合末中毒致死 → 下一回合开场兜底复用补位暂停流。"""
    s = _state(a_units=("甲", "乙"))
    apply_mark(s.side_a, "中毒印记", 34)                    # 102% → 必死
    s.active("a").max_hp = s.active("a").current_hp = 100
    end_turn(s)
    assert s.active("a").fainted
    events, need = resolve_turn(s, Decision(recharge_action()), Decision(recharge_action()))
    assert any(e["type"] == "faint" for e in events)
    assert need == "a"                                      # 有存活后备 → 等补位
    events = apply_replacement(s, "a", 1)
    assert s.active("a").name == "乙"


# ── ENTER：降灵 / 棘刺 ──
def test_jiangling_costs_energy_on_switch_in() -> None:
    s = _state(a_units=("甲", "乙"))
    apply_mark(s.side_a, "降灵印记", 2)
    s.side_a.units[1].energy = 10
    events = resolve_turn(s, Decision(switch_action(1)), Decision(recharge_action()))[0]
    assert s.active("a").name == "乙" and s.active("a").energy == 8
    assert any(e["type"] == "energy_loss" and e["lost"] == 2 for e in events)


def test_jici_costs_hp_on_switch_in() -> None:
    s = _state(a_units=("甲", "乙"))
    apply_mark(s.side_a, "棘刺印记", 1)
    s.side_a.units[1].max_hp = s.side_a.units[1].current_hp = 300
    events = resolve_turn(s, Decision(switch_action(1)), Decision(recharge_action()))[0]
    assert s.active("a").current_hp == 282                 # −6% × 300
    assert any(e["type"] == "damage" and e["attacker"] == "棘刺印记" for e in events)


# ── 暗涌：EXIT 换人 / 补位入场 ──
def test_anyong_debuffs_incoming_on_switch_out() -> None:
    s = _state(a_units=("甲", "乙"), seed=7)
    apply_mark(s.side_a, "暗涌印记", 1)
    calls_before = s.rng.calls
    resolve_turn(s, Decision(switch_action(1)), Decision(recharge_action()))
    incoming = s.side_a.units[1]
    total = sum(m.layers for m in incoming.stat_mods if m.mode == "pct")
    assert s.active("a").name == "乙"
    assert total == -5                                    # 5×1 层逐层随机分配
    assert s.rng.calls == calls_before + 5                # 每层一次引擎 RNG 抽取（确定性）


def test_anyong_debuffs_incoming_on_replacement() -> None:
    s = _state(a_units=("甲", "乙"), seed=7)
    apply_mark(s.side_a, "暗涌印记", 2)
    s.active("a").fainted = True                          # 阵亡离场 → 补位入场承接
    apply_replacement(s, "a", 1)
    incoming = s.side_a.units[1]
    total = sum(m.layers for m in incoming.stat_mods if m.mode == "pct")
    assert total == -10                                   # 5×2 层


# ── 龙噬：3 能耗技能 → 双攻 +30% ──
def test_longshi_triggers_on_energy_3_skill() -> None:
    s = _state()
    apply_mark(s.side_a, "龙噬印记", 1)
    name, type_, kind = _real_attack()
    _equip(s.active("a"), name, type_, kind, energy_cost=3, power=50)
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    a = s.side_a.units[0]
    atk = next(m for m in a.stat_mods if m.stat == "atk")
    assert atk.layers == 3 and atk.mode == "pct"
    assert any(m.stat == "sp_atk" and m.layers == 3 for m in a.stat_mods)


def test_longshi_not_triggered_on_other_cost() -> None:
    s = _state()
    apply_mark(s.side_a, "龙噬印记", 1)
    name, type_, kind = _real_attack()
    _equip(s.active("a"), name, type_, kind, energy_cost=2, power=50)
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert s.side_a.units[0].stat_mods == []


# ── 萌芽：获得增益额外 +1×层（source 守卫防循环）──
def test_mengya_grants_extra_layer_and_no_loop() -> None:
    s = _state()
    apply_mark(s.side_a, "萌芽印记", 2)
    # 直接走管道：一次自身增益 → 萌芽追加同 stat/mode 额外 2 层（1×层）
    u = s.active("a")
    frame = Frame()
    from environment.atom import AddModifier

    events, _ = run(s, [AddModifier(side="a", unit=u, stat="atk", mode="pct", layers=2,
                                    source="力量增效", target="self")], frame)
    atk = next(m for m in u.stat_mods if m.stat == "atk")
    assert atk.layers == 4                                 # 2 原始 + 2 萌芽
    # 萌芽追加的层不再触发萌芽（source 守卫）——管道已终止即证无循环


def test_mengya_ignores_debuffs_and_own_source() -> None:
    s = _state()
    apply_mark(s.side_a, "萌芽印记", 1)
    u = s.active("a")
    frame = Frame()
    from environment.atom import AddModifier

    run(s, [AddModifier(side="a", unit=u, stat="atk", mode="pct", layers=-2,
                        source="敌方减益", target="self")], frame)
    assert u.stat_mods[0].layers == -2                     # 减益不触发萌芽
    run(s, [AddModifier(side="a", unit=u, stat="def", mode="pct", layers=1,
                        source="萌芽印记", target="self")], frame)
    assert next(m for m in u.stat_mods if m.stat == "def").layers == 1   # 自身来源不追加


# ── 星陨：非幻攻击 → 额外幻伤 + 全清 ──
def _star_battle(n: int = 2, attacker_base=None) -> BattleState:
    s = _state()
    apply_mark(s.side_b, "星陨印记", n)
    name, type_, kind = _real_attack("火")
    _equip(s.active("a"), name, "火", kind, energy_cost=1, power=100)
    _equip(s.active("b"), name, "普通", kind, energy_cost=1, power=30)
    if attacker_base:
        s.active("a").base_stats = attacker_base
    return s


def test_star_meteor_damage_and_consume() -> None:
    """N=2：额外幻伤威力 = 4+24 = 28 → 28×0.9 = 25；层数全清；主伤害在前。"""
    s = _star_battle(2)
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    damages = [(e["skill"], e["damage"]) for e in events if e["type"] == "damage"]
    assert damages[0][0] != "星陨印记" and damages[0][1] == 90    # 主伤害 100×0.9
    assert damages[1] == ("星陨印记", 25)
    assert s.side_b.negative_marks == []                    # 层数全清


def test_star_meteor_uses_higher_base_stat_kind() -> None:
    """攻击方种族值 sp_atk(80) > atk(50) → 魔攻路径（sp_atk=100/sp_def=100 → 25）。"""
    s = _star_battle(2, attacker_base={"hp": 100, "atk": 50, "sp_atk": 80, "def": 50,
                                       "sp_def": 50, "speed": 50})
    # 物攻若生效：28×(200/100)×0.9 = 50 —— 断言 25 证明走的是魔攻
    s.active("a").stats["atk"] = 200
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    star = next(e for e in events if e["type"] == "damage" and e["skill"] == "星陨印记")
    assert star["damage"] == 25


def test_star_meteor_not_triggered_by_psychic_skill() -> None:
    s = _state()
    apply_mark(s.side_b, "星陨印记", 3)
    name, type_, kind = _real_attack("幻")
    _equip(s.active("a"), name, "幻", kind, energy_cost=1, power=100)
    _equip(s.active("b"), name, "普通", kind, energy_cost=1, power=30)
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert all(e["skill"] != "星陨印记" for e in events if e["type"] == "damage")
    assert s.side_b.negative_marks[0].layers == 3            # 未消耗


def test_star_meteor_fires_once_per_attack() -> None:
    """双响炮（火，2 连击）：首击触发并全清 → 次击不再触发。"""
    s = _state()
    apply_mark(s.side_b, "星陨印记", 1)
    _equip(s.active("a"), "双响炮", "火", "物攻", energy_cost=1, power=30)
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    star_hits = [e for e in events if e["type"] == "damage" and e["skill"] == "星陨印记"]
    assert len(star_hits) == 1 and s.side_b.negative_marks == []
