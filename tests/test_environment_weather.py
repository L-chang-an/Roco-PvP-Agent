"""天气系统测试（2026-08-30）：设置/刷新/递减/过期 + 4 种天气效果 + TURN_END 顺序。

天气是全局（BattleState.weather）。所有行为只在天气存在时生效——既有 621 测试
（无天气对局）是零行为变化的硬闸。
"""

from __future__ import annotations

from environment.actions import Decision, recharge_action, skill_action
from environment.engine import end_turn, execute_turn
from environment.models import BattleRng, BattleState, SideState, SkillInstance, Unit
from environment.primitives import skill_energy_cost
from environment.rules import DEFAULT_RULES
from environment.skillbook import P1_EFFECTS, P2_EFFECTS, SkillCategory
from environment.weather import WEATHER_SOURCE, power_multiplier, set_weather, tick


def _unit(name: str, types=("普通",)) -> Unit:
    return Unit(name=name, types=list(types),
                stats={"hp": 300, "atk": 100, "sp_atk": 100, "def": 100, "sp_def": 100,
                       "speed": 100},
                max_hp=300, current_hp=300, energy=10)


def _state(turn: int = 2, seed: int = 7) -> BattleState:
    a, b = _unit("甲"), _unit("乙")
    a.id, b.id = "a-0-甲", "b-0-乙"
    return BattleState(side_a=SideState(units=[a], lives=2),
                       side_b=SideState(units=[b], lives=2),
                       rng=BattleRng(seed), rules=DEFAULT_RULES, turn=turn)


def _real_attack(type_=None) -> tuple[str, str, str]:
    from environment.dataset import load_skills

    for name, eff in {**P1_EFFECTS, **P2_EFFECTS}.items():
        if eff.category != SkillCategory.ATTACK:
            continue
        raw = load_skills().get(name)
        if raw is not None and (type_ is None or raw.type == type_):
            return name, raw.type, raw.kind
    raise RuntimeError(f"找不到系别 {type_} 的攻击技能")


def _equip(u: Unit, name: str, type_: str, kind: str, energy_cost: int, power: int) -> None:
    u.current_skills = [SkillInstance(name=name, desc="", type=type_, kind=kind,
                                      energy_cost=energy_cost, power=power)]


# ── 设置 / 刷新 / 递减 / 过期 ──
def test_set_weather_and_refresh() -> None:
    s = _state()
    set_weather(s, "雨天", 8, "落雨")
    assert s.weather.kind == "雨天" and s.weather.turns_left == 8
    set_weather(s, "雨天", 8, "落雨")          # 同种刷新
    assert s.weather.turns_left == 8
    set_weather(s, "沙暴", 8, "沙涌")          # 异种覆盖
    assert s.weather.kind == "沙暴" and s.weather.turns_left == 8


def test_tick_decrements_and_expires() -> None:
    s = _state()
    set_weather(s, "雨天", 2, "落雨")
    tick(s)
    assert s.weather.turns_left == 1
    tick(s)
    assert s.weather is None                    # 归零过期


def test_turn_end_applies_effect_then_ticks() -> None:
    """turns_left=1：TURN_END 效果先结算（雷鸣 +1 引电），再递减过期。"""
    s = _state()
    set_weather(s, "雷鸣", 1, "惊雷")
    end_turn(s)
    assert s.weather is None
    assert s.active("a").stat_mods[0].stat == "引电" and s.active("a").stat_mods[0].layers == 1


# ── 雨天：水系威力 ×1.75 ──
def test_rain_boosts_water_power() -> None:
    s = _state()
    set_weather(s, "雨天", 8, "落雨")
    name, _, kind = _real_attack("水")
    _equip(s.active("a"), name, "水", kind, energy_cost=1, power=100)
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    dmg = next(e for e in events if e["type"] == "damage")
    assert dmg["damage"] == 157                  # 100×0.9×1.75 = 157.5 → 157


def test_rain_does_not_boost_other_types() -> None:
    s = _state()
    set_weather(s, "雨天", 8, "落雨")
    name, _, kind = _real_attack("火")
    _equip(s.active("a"), name, "火", kind, energy_cost=1, power=100)
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    dmg = next(e for e in events if e["type"] == "damage")
    assert dmg["damage"] == 90                   # 火不受雨天加成


def test_power_multiplier_direct() -> None:
    s = _state()
    assert power_multiplier(s, "水") == 1.0
    set_weather(s, "雨天", 8, "落雨")
    assert power_multiplier(s, "水") == 1.75
    assert power_multiplier(s, "火") == 1.0


# ── 沙暴：地系能耗减半 ──
def test_sandstorm_halves_ground_skill_cost() -> None:
    s = _state()
    set_weather(s, "沙暴", 8, "沙涌")
    name, _, kind = _real_attack("地")
    _equip(s.active("a"), name, "地", kind, energy_cost=3, power=30)
    inst = s.active("a").current_skills[0]
    assert skill_energy_cost(s, "a", s.active("a"), 3, inst) == 1   # 3//2 = 1


def test_sandstorm_gate_opens_ground_skill() -> None:
    """沙暴让 1 能量的地系技能可释放（3 能耗 → 1）。"""
    from environment.actions import skill_block_reason

    s = _state()
    set_weather(s, "沙暴", 8, "沙涌")
    name, _, kind = _real_attack("地")
    _equip(s.active("a"), name, "地", kind, energy_cost=3, power=30)
    s.active("a").energy = 1
    assert skill_block_reason(s, "a", s.active("a"), 0) is None


# ── 暴风雪 / 雷鸣：TURN_END 施加层 ──
def test_blizzard_grants_freeze_layers() -> None:
    s = _state()
    set_weather(s, "暴风雪", 8, "冬至")
    end_turn(s)
    for side in ("a", "b"):
        m = s.active(side).stat_mods[0]
        assert m.stat == "冻结" and m.mode == "special" and m.layers == 2
        assert m.kwargs == {"pct": 5}


def test_weather_status_source_is_global() -> None:
    """天气是全局的：状态施加 source 一律是「天气」（非精灵名/技能名/阵营）——
    供 STATUS_APPLIED 类特性据此识别「非自己直接造成」并跳过（2026-08-30 修正）。"""
    s = _state()
    set_weather(s, "暴风雪", 8, "冬至")
    end_turn(s)
    for side in ("a", "b"):
        m = s.active(side).stat_mods[0]
        assert m.source == WEATHER_SOURCE


def test_thunder_grants_conduct_layers() -> None:
    s = _state()
    set_weather(s, "雷鸣", 8, "惊雷")
    end_turn(s)
    for side in ("a", "b"):
        m = s.active(side).stat_mods[0]
        assert m.stat == "引电" and m.mode == "special" and m.layers == 1
        assert m.kwargs == {"pct": 25, "at": 2}


def test_blizzard_skips_fainted() -> None:
    s = _state()
    set_weather(s, "暴风雪", 8, "冬至")
    s.active("b").fainted = True
    end_turn(s)                                  # 不崩溃
    assert s.active("a").stat_mods[0].stat == "冻结"
    assert s.active("b").stat_mods == []
