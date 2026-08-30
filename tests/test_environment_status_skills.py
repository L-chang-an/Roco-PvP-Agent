"""DOT 技能入口测试（2026-08-30）：编译模式 + ST 白名单钉死 + 集成对局。

A 类施加（获得N层X / 伤害+获得N层X）+ 连击逐击状态（易燃物质/连续毒针/打喷嚏）；
应对/条件/驱散/转化类不匹配任何模式 → 自动排除（下批）。
"""

from __future__ import annotations

from environment.actions import Decision, recharge_action, skill_action
from environment.engine import end_turn, execute_turn
from environment.models import BattleRng, BattleState, SideState, SkillInstance, Unit
from environment.rules import DEFAULT_RULES
from environment.skillbook import ST_EFFECTS, SkillCategory, battle_ready, compile_effect
from environment.dataset import DataSource, load_skills
from environment.statuses import STATUS_TABLE


def _unit(name: str, types=("普通",)) -> Unit:
    return Unit(name=name, types=list(types),
                stats={"hp": 300, "atk": 100, "sp_atk": 100, "def": 100, "sp_def": 100,
                       "speed": 100},
                max_hp=300, current_hp=300, energy=10)


def _state(turn: int = 2, seed: int = 7, a_types=("普通",), b_types=("普通",)) -> BattleState:
    a, b = _unit("甲", types=a_types), _unit("乙", types=b_types)
    a.id, b.id = "a-0-甲", "b-0-乙"
    return BattleState(side_a=SideState(units=[a], lives=2),
                       side_b=SideState(units=[b], lives=2),
                       rng=BattleRng(seed), rules=DEFAULT_RULES, turn=turn)


def _equip(u: Unit, *skills: tuple[str, str, str, int, int]) -> None:
    u.current_skills = [SkillInstance(name=n, desc="", type=t, kind=k, energy_cost=c,
                                      power=p) for n, t, k, c, p in skills]


def _status_record(u: Unit, stat: str):
    return next((m for m in u.stat_mods if m.stat == stat), None)


# ── 编译模式 ──
def test_compile_pure_status_apply() -> None:
    eff = compile_effect(load_skills(DataSource.FULL)["引燃"])
    assert eff is not None and eff.category == SkillCategory.STATUS
    se = eff.stat_effects[0]
    assert (se.target, se.stat, se.mode, se.layers) == ("foe", "灼烧", "dot", 10)
    assert se.kwargs == {"pct": 2, "halve": True}


def test_compile_attack_with_status() -> None:
    eff = compile_effect(load_skills(DataSource.FULL)["毒针"])
    assert eff.category == SkillCategory.ATTACK
    assert eff.stat_effects[0].stat == "中毒" and eff.stat_effects[0].mode == "dot"


def test_compile_combo_status() -> None:
    eff = compile_effect(load_skills(DataSource.FULL)["易燃物质"])
    assert eff.hits == 2 and eff.combo_eligible
    assert eff.stat_effects[0].stat == "灼烧" and eff.stat_effects[0].layers == 2
    eff2 = compile_effect(load_skills(DataSource.FULL)["打喷嚏"])
    assert eff2.hits == 3 and eff2.category == SkillCategory.STATUS
    assert eff2.stat_effects[0].stat == "冻结" and eff2.stat_effects[0].mode == "special"


def test_st_whitelist_pinned() -> None:
    assert len(ST_EFFECTS) == 21
    assert set(ST_EFFECTS) == {
        "退化", "孢子", "引燃", "霜降", "毒孢子", "毒针", "腐蚀酸液", "烈焰风暴",
        "花火", "暴风雪", "通电", "易燃物质", "连续毒针", "打喷嚏",
        # 冻结批 L1（2026-08-30）
        "碎冰冰", "冷凝", "霜天", "冰点", "冰墙",
        # 冻结批 L2/L3（2026-08-30）
        "滚雪球", "极寒领域",
    }


def test_battle_ready_boundary() -> None:
    assert battle_ready("毒针") and battle_ready("引燃") and battle_ready("打喷嚏")
    assert not battle_ready("天火")       # 应对子句：下批
    assert not battle_ready("毒雾")       # 转化类：下批
    assert not battle_ready("毒液渗透")   # 按敌方层数缩放：下批


# ── 集成对局 ──
def test_duzhen_poison_settles_at_turn_end() -> None:
    """毒针（0 能耗）→ 敌方 1 层中毒 → 回合末 3% 扣血。"""
    s = _state()
    _equip(s.active("a"), ("毒针", "毒", "物攻", 0, 20))
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert _status_record(s.active("b"), "中毒") is not None
    hp_before = s.active("b").current_hp
    end_turn(s)
    assert s.active("b").current_hp == hp_before - 9        # 3%×300


def test_yinran_burn_chain() -> None:
    """引燃 10 层灼烧 → 回合末 20% 扣血 → 5 → 2 → 1 → 0。"""
    s = _state()
    _equip(s.active("a"), ("引燃", "火", "状态", 2, 0))
    # execute_turn 已含回合末：施加后同回合末即结算一次（10×2% = 60，层数 → 5）
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert s.active("b").current_hp == 240
    assert _status_record(s.active("b"), "灼烧").layers == 5
    end_turn(s)
    assert _status_record(s.active("b"), "灼烧").layers == 2


def test_baozi_parasite_drains_and_heals() -> None:
    """孢子寄生 → 回合末 b 方 −6 且 a 方回复等量。"""
    s = _state()
    _equip(s.active("a"), ("孢子", "草", "状态", 3, 0))
    s.active("a").current_hp = 200                          # 留出回复空间
    s.active("b").current_hp = 100
    # execute_turn 已含回合末：寄生同回合末即结算（持有者 b −6，对手 a +6）
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert _status_record(s.active("b"), "寄生") is not None
    assert s.active("b").current_hp == 94                   # 100 − 6（持有者扣血）
    assert s.active("a").current_hp == 206                  # 200 + 6（对手回复）


def test_tongdian_plus_thunder_weather() -> None:
    """通电 1 层引电 + 雷鸣天气 +1 → 即时 25% 电伤。"""
    from environment.weather import set_weather

    s = _state()
    _equip(s.active("a"), ("通电", "电", "物攻", 3, 75))
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert _status_record(s.active("b"), "引电") is not None
    hp_after_hit = s.active("b").current_hp
    set_weather(s, "雷鸣", 8, "惊雷")
    end_turn(s)                                             # 雷鸣 +1 → 2 层 → 即时 75 伤害
    assert s.active("b").current_hp == hp_after_hit - 75
    assert _status_record(s.active("b"), "引电") is None


def test_yiran_combo_applies_per_hit() -> None:
    """易燃物质 2 连击 → 每击 2 层灼烧 = 4 层。"""
    s = _state()
    _equip(s.active("a"), ("易燃物质", "火", "魔攻", 3, 30))
    # 2 连击 × 每击 2 层 = 4 层；execute_turn 的回合末已减半一次 → 2
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert _status_record(s.active("b"), "灼烧").layers == 2


def test_immunity_via_skill() -> None:
    """毒针打毒系精灵 → 免疫：不落层。"""
    s = _state(b_types=("毒",))
    _equip(s.active("a"), ("毒针", "毒", "物攻", 0, 20))
    execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert _status_record(s.active("b"), "中毒") is None
