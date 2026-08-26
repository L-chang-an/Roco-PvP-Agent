"""P2 机制行为测试：连击 / 先手优先级 / 吸血 / 能量 / 偷能量 / 场下回能量 / 每连击状态 / 虫鸣。

白盒构造 1v1/2v2 对局（execute_turn 直接驱动），逐机制验收 P2 技能的实际行为。
"""

from __future__ import annotations

from dataclasses import replace

from environment.actions import Decision, recharge_action, skill_action
from environment.engine import execute_turn
from environment.models import BattleRng, BattleState, SideState, build_unit
from environment.primitives import skill_energy_cost
from environment.rules import DEFAULT_RULES


def _mk(name: str, types: list[str], skills: list[str]) -> object:
    return build_unit({"name": name, "types": types,
                       "stats": {"hp": 300, "atk": 100, "sp_atk": 100,
                                 "def": 100, "sp_def": 100, "speed": 100},
                       "skills": skills, "trait": ""})


def _battle(a, b, *, team_size: int = 1):
    return BattleState(side_a=SideState(units=[a], lives=2),
                       side_b=SideState(units=[b], lives=2),
                       rng=BattleRng(7), rules=replace(DEFAULT_RULES, team_size=team_size))


def _turn(state, a_act, b_act=recharge_action()):
    return execute_turn(state, Decision(a_act), Decision(b_act))


def _damages(events, side="a"):
    return [e for e in events if e["type"] == "damage" and e["side"] == side]


# ── 连击伤害 ──
def test_multihit_damage_events() -> None:
    """乱打（5连击）：逐发发事件、逐发扣血；普通系自攻同系 → 本系 1.25。"""
    a, b = _mk("甲", ["普通"], ["乱打"]), _mk("乙", ["普通"], ["撞击"])
    s = _battle(a, b)
    events = _turn(s, skill_action(0))
    dmg = _damages(events)
    assert len(dmg) == 5 and dmg[0]["hits"] == 5 and dmg[-1]["hit"] == 5
    assert all(e["stab"] == 1.25 for e in dmg)
    assert b.current_hp == 300 - 5 * 28        # 每发 (100/100)*25*0.9*1.25 = 28


def test_multihit_stops_on_faint() -> None:
    """连击途中目标阵亡 → 剩余连击不再结算（回合在阵亡处暂停）。"""
    a = _mk("甲", ["普通"], ["双响炮"])          # 2连击
    b = build_unit({"name": "乙", "types": ["普通"],
                    "stats": {"hp": 20, "atk": 1, "sp_atk": 1, "def": 1, "sp_def": 1, "speed": 1},
                    "skills": ["撞击"], "trait": ""})
    s = _battle(a, b)
    events = _turn(s, skill_action(0))
    dmg = _damages(events)
    assert len(dmg) == 1                          # 第一发即 KO，第二发跳过
    assert any(e["type"] == "faint" for e in events)


def test_linguo_hit_truncation_per_hit() -> None:
    """单发 1连击（音波弹）：只发一条 damage 事件、hits=1。"""
    a, b = _mk("甲", ["普通"], ["音波弹"]), _mk("乙", ["普通"], ["撞击"])
    s = _battle(a, b)
    events = _turn(s, skill_action(0))
    dmg = _damages(events)
    assert len(dmg) == 1 and dmg[0]["hits"] == 1 and dmg[0]["hit"] == 1


# ── 连击数buff ──
def test_combo_flat_buff_adds_hits() -> None:
    """热身运动（连击数+3）：乱打 5连击 → 8 连击（常规 buff 1 层 = +1）。"""
    a, b = _mk("甲", ["普通"], ["热身运动", "乱打"]), _mk("乙", ["普通"], ["撞击"])
    s = _battle(a, b)
    _turn(s, skill_action(0))                       # 热身运动
    events = _turn(s, skill_action(1))              # 乱打
    assert _damages(events)[0]["hits"] == 8


def test_combo_pct_buff_doubles_hits() -> None:
    """暴风眼（连击数+100%）：乱打 5连击 → 10 连击。"""
    a, b = _mk("甲", ["普通"], ["暴风眼", "乱打"]), _mk("乙", ["普通"], ["撞击"])
    s = _battle(a, b)
    _turn(s, skill_action(0))
    events = _turn(s, skill_action(1))
    assert _damages(events)[0]["hits"] == 10


def test_combo_buff_only_boosts_combo_skills() -> None:
    """连击数buff 只加成「带有连击描述」的技能：撞击（无连击）不被加成。"""
    a, b = _mk("甲", ["普通"], ["热身运动", "撞击"]), _mk("乙", ["普通"], ["撞击"])
    s = _battle(a, b)
    _turn(s, skill_action(0))                       # 热身运动 +3
    events = _turn(s, skill_action(1))              # 撞击（combo_eligible=False）
    assert len(_damages(events)) == 1               # 仍是 1 连击


def test_foe_combo_debuff_reduces_hits() -> None:
    """耀眼（敌方连击数-4）：敌方 5连击 → 1 连击（夹到 ≥1）。"""
    a, b = _mk("甲", ["普通"], ["耀眼"]), _mk("乙", ["普通"], ["乱打"])
    s = _battle(a, b)
    _turn(s, skill_action(0))                       # 耀眼 -4 在乙身上
    events = _turn(s, recharge_action(), skill_action(0))   # 乙用乱打
    dmg = _damages(events, side="b")
    assert len(dmg) == 1 and dmg[0]["hits"] == 1


def test_attack_foe_combo_debuff() -> None:
    """震击：造成物伤并给敌方连击数-3。"""
    a, b = _mk("甲", ["普通"], ["震击"]), _mk("乙", ["普通"], ["乱打"])
    s = _battle(a, b)
    _turn(s, skill_action(0))
    assert any(m.stat == "combo" and m.layers == -3 for m in b.stat_mods)


# ── 先手优先级 ──
def test_priority_plus_beats_speed() -> None:
    """先手+1（俯冲）虽慢仍先于快但普通优先级（撞击）的对手出手。"""
    a, b = _mk("甲", ["普通"], ["俯冲"]), _mk("乙", ["普通"], ["撞击"])
    s = _battle(a, b)
    s.side_a.units[0].stats["speed"] = 10
    s.side_b.units[0].stats["speed"] = 100
    events = _turn(s, skill_action(0), skill_action(0))
    order = [e["attacker"] for e in events if e["type"] == "damage"]
    assert order[0] == "甲"                          # 慢但先手+1 → 先出手


def test_priority_minus_loses_to_speed() -> None:
    """先手-1（后发制人）虽快仍后于普通优先级的对手。"""
    a, b = _mk("甲", ["普通"], ["后发制人"]), _mk("乙", ["普通"], ["撞击"])
    s = _battle(a, b)
    s.side_a.units[0].stats["speed"] = 100
    s.side_b.units[0].stats["speed"] = 10
    events = _turn(s, skill_action(0), skill_action(0))
    dmg = [e for e in events if e["type"] == "damage"]
    assert [e["attacker"] for e in dmg] == ["乙", "甲"]


def test_same_priority_then_speed() -> None:
    """同优先级比速度：双方都用先发制人（先手+1），快者先手。"""
    a, b = _mk("甲", ["普通"], ["先发制人"]), _mk("乙", ["普通"], ["先发制人"])
    s = _battle(a, b)
    s.side_a.units[0].stats["speed"] = 10
    s.side_b.units[0].stats["speed"] = 100
    events = _turn(s, skill_action(0), skill_action(0))
    assert [e["attacker"] for e in events if e["type"] == "damage"] == ["乙", "甲"]


# ── 吸血 ──
def test_lifesteal_direct() -> None:
    """汲取（吸血100%）：回复 = 本次造成伤害。"""
    a, b = _mk("甲", ["草"], ["汲取"]), _mk("乙", ["火"], ["撞击"])
    s = _battle(a, b)
    a.current_hp = 100
    events = _turn(s, skill_action(0))
    dmg = _damages(events)[0]["damage"]
    heal = [e for e in events if e["type"] == "heal" and e["side"] == "a"][0]
    assert heal["applied"] == dmg and a.current_hp == 100 + dmg


def test_lifesteal_buff_applies_to_attacks() -> None:
    """贪婪（自己获得100%吸血）：之后每次攻击都吸血 100%。"""
    a, b = _mk("甲", ["普通"], ["贪婪", "撞击"]), _mk("乙", ["普通"], ["撞击"])
    s = _battle(a, b)
    a.current_hp = 150
    _turn(s, skill_action(0))                       # 贪婪
    assert any(m.stat == "lifesteal" and m.layers == 1 for m in a.stat_mods)
    events = _turn(s, skill_action(1))              # 撞击
    heal = [e for e in events if e["type"] == "heal" and e["side"] == "a"][0]
    assert heal["applied"] == _damages(events)[0]["damage"]
    assert a.current_hp == 150 + heal["applied"]


# ── 能量 ──
def test_attack_self_energy_gain() -> None:
    """偷师（造成物伤+回1能量）：伤害后自己 +1 能量。"""
    a, b = _mk("甲", ["普通"], ["偷师"]), _mk("乙", ["普通"], ["撞击"])
    s = _battle(a, b)
    a.energy = 5                                    # 起始 5（energy_cost=0 + 回 1 → 6）
    events = _turn(s, skill_action(0))
    eg = [e for e in events if e["type"] == "energy_gain"][0]
    assert eg["gained"] == 1 and a.energy == 6

def test_state_energy_gain() -> None:
    """徒长（自己回复10能量）：夹到 energy_max。"""
    a, b = _mk("甲", ["普通"], ["徒长"]), _mk("乙", ["普通"], ["撞击"])
    s = _battle(a, b)
    a.energy = 5
    _turn(s, skill_action(0))
    assert a.energy == 10


def test_steal_energy() -> None:
    """勾魂（偷取敌方3能量）：敌方 -3、自己 +3（先付后得，夹到 max）。"""
    a, b = _mk("甲", ["普通"], ["勾魂"]), _mk("乙", ["普通"], ["撞击"])
    s = _battle(a, b)
    s.side_a.units[0].stats["speed"] = 100           # a 先手
    s.side_b.units[0].stats["speed"] = 10
    b.energy = 5
    events = _turn(s, skill_action(0), skill_action(0))
    st = [e for e in events if e["type"] == "steal"][0]
    assert st["gained"] == 3
    assert b.energy == 0                              # 5-3(偷) -2(撞击能耗)
    assert a.energy == 10                             # 10-1(勾魂能耗)+3=12 → 夹到 10


def test_bench_energy_status() -> None:
    """富养化：为场下每个（自己队伍）精灵回复3能量。"""
    rules = replace(DEFAULT_RULES, team_size=2)
    a0, a1 = _mk("甲0", ["普通"], ["富养化"]), _mk("甲1", ["普通"], ["撞击"])
    b0, b1 = _mk("乙0", ["普通"], ["撞击"]), _mk("乙1", ["普通"], ["撞击"])
    s = BattleState(side_a=SideState(units=[a0, a1], lives=2),
                    side_b=SideState(units=[b0, b1], lives=2),
                    rng=BattleRng(7), rules=rules)
    a1.energy = 5
    _turn(s, skill_action(0))
    assert a1.energy == 8                            # 场下 a1 加 3
    assert a0.energy == 7                            # 自己 a0：10-3(能耗)，不加


def test_bench_energy_attack() -> None:
    """养分回流：造成魔伤 + 场下每只回1能量。"""
    rules = replace(DEFAULT_RULES, team_size=2)
    a0, a1 = _mk("甲0", ["普通"], ["养分回流"]), _mk("甲1", ["普通"], ["撞击"])
    b0, b1 = _mk("乙0", ["普通"], ["撞击"]), _mk("乙1", ["普通"], ["撞击"])
    s = BattleState(side_a=SideState(units=[a0, a1], lives=2),
                    side_b=SideState(units=[b0, b1], lives=2),
                    rng=BattleRng(7), rules=rules)
    a1.energy = 5
    events = _turn(s, skill_action(0))
    assert a1.energy == 6 and _damages(events)       # 有伤害 + 场下回 1


# ── 状态回血 / 能量 + 属性 ──
def test_state_heal_pct() -> None:
    """休息回复：自己回复30%生命。"""
    a, b = _mk("甲", ["普通"], ["休息回复"]), _mk("乙", ["普通"], ["撞击"])
    s = _battle(a, b)
    a.current_hp = 100
    _turn(s, skill_action(0))
    assert a.current_hp == 190                       # 300 × 30% = 90


def test_attack_heal_pct() -> None:
    """丰收：造成物伤 + 自己回复20%生命。"""
    a, b = _mk("甲", ["草"], ["丰收"]), _mk("乙", ["火"], ["撞击"])
    s = _battle(a, b)
    a.current_hp = 100
    events = _turn(s, skill_action(0))
    heal = [e for e in events if e["type"] == "heal" and e["side"] == "a"][0]
    assert heal["applied"] == 60 and a.current_hp == 160   # 300×20%


def test_state_energy_and_stats() -> None:
    """氧输送：自己回复4能量 + 魔攻+70%（先付能耗 2）。"""
    a, b = _mk("甲", ["普通"], ["氧输送"]), _mk("乙", ["普通"], ["撞击"])
    s = _battle(a, b)
    a.energy = 5
    _turn(s, skill_action(0))
    assert a.energy == 7                              # 5 - 2(能耗) + 4
    assert any(m.stat == "sp_atk" and m.layers == 7 for m in a.stat_mods)


def test_state_multi_effects() -> None:
    """缓一缓：回1能量 + 回10%生命 + 魔攻魔防+10% + 速度+10。"""
    a, b = _mk("甲", ["普通"], ["缓一缓"]), _mk("乙", ["普通"], ["撞击"])
    s = _battle(a, b)
    a.current_hp, a.energy = 100, 5
    events = _turn(s, skill_action(0))
    assert a.current_hp == 130 and a.energy == 4      # 100+30；5-2(能耗)+1
    mods = {m.stat: m.layers for m in a.stat_mods}
    assert mods == {"sp_atk": 1, "sp_def": 1, "speed": 1}


# ── 每连击状态（花炮 / 冰捆缚）──
def test_per_hit_stat_stacks() -> None:
    """花炮（2连击，每次连击魔攻+60%）：两击各 +6 层 → 合计 +12 层。"""
    a, b = _mk("甲", ["普通"], ["花炮"]), _mk("乙", ["普通"], ["撞击"])
    s = _battle(a, b)
    events = _turn(s, skill_action(0))
    sc = [e for e in events if e["type"] == "stat_change" and e["side"] == "a"]
    assert len(sc) == 2 and sc[0]["total_layers"] == 6 and sc[1]["total_layers"] == 12
    assert any(m.stat == "sp_atk" and m.layers == 12 for m in a.stat_mods)


def test_per_hit_foe_energy_cost() -> None:
    """冰捆缚（2连击，每次敌方全技能能耗+1）：两击 → 敌能耗 +2。"""
    a, b = _mk("甲", ["普通"], ["冰捆缚"]), _mk("乙", ["普通"], ["撞击"])
    s = _battle(a, b)
    _turn(s, skill_action(0))
    assert skill_energy_cost(b, 2) == 4              # 撞击 base 2 → 4
    assert [m.layers for m in b.energy_cost_mods] == [-2]


# ── 虫鸣（动态连击）──
def test_chongming_dynamic_combo() -> None:
    """虫鸣：队伍中每携带 1 个虫鸣，本次连击数 +1（含自己 → 2 连击）。"""
    rules = replace(DEFAULT_RULES, team_size=2)
    a0 = _mk("甲0", ["普通"], ["虫鸣"])
    a1 = _mk("甲1", ["普通"], ["虫鸣"])               # 队伍里 2 个虫鸣
    b0, b1 = _mk("乙0", ["普通"], ["撞击"]), _mk("乙1", ["普通"], ["撞击"])
    s = BattleState(side_a=SideState(units=[a0, a1], lives=2),
                    side_b=SideState(units=[b0, b1], lives=2),
                    rng=BattleRng(7), rules=rules)
    events = _turn(s, skill_action(0))               # a0 用虫鸣
    dmg = _damages(events)
    assert dmg[0]["hits"] == 3                        # 1 + 2(队内虫鸣数) = 3


# ── 人工裁决技能（2026-08-25）──
def test_sanlianpo_per_hit_atk_buff() -> None:
    """三连破：3连击 × 物攻+3层（每次连击 +30%）→ 合计 +90%。"""
    a, b = _mk("甲", ["普通"], ["三连破"]), _mk("乙", ["普通"], ["撞击"])
    s = _battle(a, b)
    events = _turn(s, skill_action(0))
    sc = [e for e in events if e["type"] == "stat_change" and e["side"] == "a"]
    assert len(sc) == 3                              # 每连击一条
    assert any(m.stat == "atk" and m.layers == 9 for m in a.stat_mods)


def test_sanlianpo_eats_combo_buff() -> None:
    """三连破 吃连击数buff：热身运动(+3) → 6连击 × +3层 = +18层。"""
    a, b = _mk("甲", ["普通"], ["热身运动", "三连破"]), _mk("乙", ["普通"], ["撞击"])
    s = _battle(a, b)
    _turn(s, skill_action(0))                        # 热身运动 +3 连击
    _turn(s, skill_action(1))                        # 三连破
    assert sum(m.layers for m in a.stat_mods if m.stat == "atk") == 18


def test_dianlibaopo_per_hit_foe_debuff() -> None:
    """电离爆破：2连击 × 敌方（魔攻-2层、速度-2层）→ 敌方魔攻-40%、速度-40。"""
    a, b = _mk("甲", ["普通"], ["电离爆破"]), _mk("乙", ["普通"], ["撞击"])
    s = _battle(a, b)
    _turn(s, skill_action(0))
    mods = {m.stat: m.layers for m in b.stat_mods}
    assert mods == {"sp_atk": -4, "speed": -4}


def test_wuqi_huanrao_energy_from_foe_cost() -> None:
    """雾气环绕：回复 = 敌方当前在场精灵全部技能能耗的一半。"""
    a, b = _mk("甲", ["普通"], ["雾气环绕"]), _mk("乙", ["普通"], ["撞击", "能量炮", "地震"])
    s = _battle(a, b)
    a.energy = 3
    events = _turn(s, skill_action(0))
    foe_cost = sum(sk.energy_cost for sk in b.skills)     # 2+3+10 = 15
    eg = [e for e in events if e["type"] == "energy_gain"][0]
    assert eg["gained"] == foe_cost // 2                   # 7
    assert a.energy == 3 - 1 + 7                           # 先付能耗 1 + 回 7 = 9
