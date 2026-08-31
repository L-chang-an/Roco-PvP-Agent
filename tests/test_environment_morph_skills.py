"""萌化批技能测试（2026-08-30）：读钩子（拆礼物）/ 应对施萌化（捧杀）/「获得萌化：
X」施萌化+条件附加（超级糖果/赤子之心/示弱/撒娇/甜心续航）/ 队伍级连击（月光合奏）/
条件施萌化（转圈圈）/ 转移（反弹）。

口径：施萌化成功（未达最低阶上限）→ 附加效果；最低阶拦截 → 无附加。
蹦跶（选择机制）与寒潮（巧变）不实现。
"""

from __future__ import annotations

from environment.actions import Decision, recharge_action, skill_action, switch_action
from environment.engine import execute_turn, resolve_turn
from environment.evolution import base_stats_of
from environment.models import BattleRng, BattleState, SideState, SkillInstance, Unit
from environment.rules import DEFAULT_RULES
from environment.statline import calc_combat_stats
from environment.statuses import morph_layers


def _morph_unit(name: str, skills, hp_ratio: float = 1.0) -> Unit:
    base = base_stats_of(name)
    stats = calc_combat_stats(base, {}, "坦率")
    u = Unit(name=name, types=["草"], base_stats=dict(base), stats=dict(stats),
             nature="坦率", iv={}, max_hp=stats["hp"],
             current_hp=max(1, int(stats["hp"] * hp_ratio)), energy=10)
    u.current_skills = list(skills)
    u.skills = list(skills)
    return u


def _sk(*specs) -> list[SkillInstance]:
    return [SkillInstance(name=n, desc="", type=t, kind=k, energy_cost=c, power=p)
            for n, t, k, c, p in specs]


def _state(a, b, seed: int = 7, turn: int = 2) -> BattleState:
    a.id, b.id = "a-0-x", "b-0-y"
    return BattleState(side_a=SideState(units=[a], lives=2),
                       side_b=SideState(units=[b], lives=2),
                       rng=BattleRng(seed), rules=DEFAULT_RULES, turn=turn)


def _target(u: Unit) -> Unit:
    """魔力猫（可退 2 阶）作为敌方——萌化技能的目标。"""
    return u


# ── 拆礼物：敌方有萌化 → 威力 +100 ──
def test_gift_power_if_foe_morphed() -> None:
    a = _morph_unit("魔力猫", _sk(("拆礼物", "萌", "物攻", 1, 100)))
    b = _morph_unit("魔力猫", _sk(("抓挠", "普通", "物攻", 1, 30)))
    s = _state(a, b)
    base = [e for e in execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
            if e["type"] == "damage" and e["side"] == "a"][0]["damage"]
    assert base == 108                                  # 无萌化 → power 100（atk179/def149）
    b.stat_mods.append(type(b.stat_mods[0]) if b.stat_mods else None) if False else None
    # 给敌方 1 层萌化
    from environment.models import StatModifier

    b.stat_mods.append(StatModifier(stat="萌化", mode="special", layers=1,
                                    permanent=True, source="测试"))
    b.current_hp = b.max_hp
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    boosted = [e for e in events if e["type"] == "damage" and e["side"] == "a"][0]["damage"]
    assert boosted == 216                               # 有萌化 → power 200


# ── 捧杀：防御应对攻击 → 敌方 +1 层萌化 ──
def test_flatter_counter_applies_morph() -> None:
    a = _morph_unit("魔力猫", _sk(("抓挠", "普通", "物攻", 1, 30)))
    b = _morph_unit("魔力猫", _sk(("捧杀", "萌", "防御", 1, 0)))
    s = _state(a, b)
    execute_turn(s, Decision(skill_action(0)), Decision(skill_action(0)))
    assert morph_layers(a) == 1                         # b 防御应对 a 的攻击 → a 获得 1 层萌化


# ── 超级糖果：施萌化+成功则本次威力+60 ──
def test_candy_power_when_morph_applied() -> None:
    a = _morph_unit("魔力猫", _sk(("超级糖果", "萌", "物攻", 1, 100)))
    b = _morph_unit("魔力猫", _sk(("抓挠", "普通", "物攻", 1, 30)))
    s = _state(a, b)
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    dmg = [e for e in events if e["type"] == "damage" and e["side"] == "a"][0]["damage"]
    assert dmg == 149                                   # 施萌化成功 → power 160，但资质已退（atk155/def149）
    assert morph_layers(a) == 1


def test_candy_no_bonus_at_lowest() -> None:
    a = _morph_unit("喵喵", _sk(("超级糖果", "萌", "物攻", 1, 100)))
    b = _morph_unit("魔力猫", _sk(("抓挠", "普通", "物攻", 1, 30)))
    s = _state(a, b)
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    dmg = [e for e in events if e["type"] == "damage" and e["side"] == "a"][0]["damage"]
    assert dmg == 79                                    # 最低阶 → 无附加威力（喵喵 atk132）
    assert morph_layers(a) == 0                         # 不落层


# ── 示弱：施萌化+成功则速度永久+150 ──
def test_show_weakness_permanent_speed() -> None:
    a = _morph_unit("魔力猫", _sk(("示弱", "萌", "状态", 1, 0)))
    b = _morph_unit("魔力猫", _sk(("抓挠", "普通", "物攻", 1, 30)))
    s = _state(a, b)
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    speed_mod = next((m for m in a.stat_mods if m.stat == "speed"), None)
    assert speed_mod is not None and speed_mod.layers == 15 and speed_mod.permanent
    assert morph_layers(a) == 1


# ── 赤子之心：施萌化+成功则能耗永久-3 ──
def test_childlike_permanent_cost() -> None:
    a = _morph_unit("魔力猫", _sk(("赤子之心", "萌", "状态", 1, 0)))
    b = _morph_unit("魔力猫", _sk(("抓挠", "普通", "物攻", 1, 30)))
    s = _state(a, b)
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    cost_mod = next((m for m in a.stat_mods if m.stat == "energy_cost"), None)
    assert cost_mod is not None and cost_mod.layers == -3 and cost_mod.permanent
    assert morph_layers(a) == 1


# ── 撒娇：3 连击 + 施萌化+成功则威力永久+20 ──
def test_coquettish_permanent_power() -> None:
    a = _morph_unit("魔力猫", _sk(("撒娇", "萌", "魔攻", 1, 30)))
    b = _morph_unit("魔力猫", _sk(("抓挠", "普通", "物攻", 1, 30)))
    s = _state(a, b)
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    dmg = [e for e in events if e["type"] == "damage" and e["side"] == "a"]
    assert len(dmg) == 3                                 # 3 连击
    pw = next((m for m in a.stat_mods if m.stat == "attack_power"), None)
    assert pw is not None and pw.layers == 2 and pw.permanent   # +20 威力
    assert morph_layers(a) == 1


# ── 甜心续航：自己+敌方独立施萌化，自己成功则回 40% ──
def test_sweet_heal_both_apply_independent() -> None:
    a = _morph_unit("魔力猫", _sk(("甜心续航", "萌", "状态", 1, 0)))
    b = _morph_unit("喵喵", _sk(("抓挠", "普通", "物攻", 1, 30)))   # 敌方最低阶 → 失败
    s = _state(a, b)
    a.current_hp = a.max_hp // 2
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert morph_layers(a) == 1                         # 自己成功
    assert morph_layers(b) == 0                         # 敌方最低阶失败（不影响自己）
    assert a.current_hp > a.max_hp // 2                 # 回复 40%


# ── 月光合奏：双方队伍每层萌化 → 连击 +1 ──
def test_moonlight_combo_per_team_morph() -> None:
    a = _morph_unit("魔力猫", _sk(("月光合奏", "萌", "物攻", 1, 30)))
    b = _morph_unit("魔力猫", _sk(("抓挠", "普通", "物攻", 1, 30)))
    s = _state(a, b)
    from environment.models import StatModifier

    a.stat_mods.append(StatModifier(stat="萌化", mode="special", layers=1,
                                    permanent=True, source="测试"))
    b.stat_mods.append(StatModifier(stat="萌化", mode="special", layers=2,
                                    permanent=True, source="测试"))
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    dmg = [e for e in events if e["type"] == "damage" and e["side"] == "a"]
    assert len(dmg) == 4                                 # 1 基础 + 3 层队伍萌化


# ── 转圈圈：敌方本回合换人 → 敌方获得萌化 ──
def test_spin_morph_if_foe_switched() -> None:
    a = _morph_unit("魔力猫", _sk(("转圈圈", "萌", "魔攻", 1, 30)))
    b1 = _morph_unit("魔力猫", _sk(("抓挠", "普通", "物攻", 1, 30)))
    b2 = _morph_unit("喵喵", _sk(("抓挠", "普通", "物攻", 1, 30)))
    a.id = "a-0-x"
    b1.id, b2.id = "b-0-y", "b-1-z"
    s = BattleState(side_a=SideState(units=[a], lives=2),
                    side_b=SideState(units=[b1, b2], lives=2),
                    rng=BattleRng(7), rules=DEFAULT_RULES, turn=2)
    resolve_turn(s, Decision(skill_action(0)), Decision(switch_action(1)))
    # 敌方换人：入场精灵 b2（喵喵，最低阶）→ 施萌化拦截 → 0 层
    assert morph_layers(b1) == 0
    assert morph_layers(b2) == 0


# ── 反弹：将自己的萌化转移给敌方 ──
def test_rebound_transfers_morph() -> None:
    a = _morph_unit("魔力猫", _sk(("反弹", "萌", "状态", 1, 0)))
    b = _morph_unit("魔力猫", _sk(("抓挠", "普通", "物攻", 1, 30)))
    s = _state(a, b)
    from environment.models import StatModifier

    a.stat_mods.append(StatModifier(stat="萌化", mode="special", layers=2,
                                    permanent=True, source="测试"))
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert morph_layers(a) == 0                         # 转移：自己移除
    assert morph_layers(b) == 2                         # 敌方获得同层
