"""DOT/异常状态结算测试（2026-08-30）：中毒/灼烧/寄生（TURN_END）+ 引电（即时）
+ 属性免疫 + 伤害克制 + TURN_END 顺序 + 致死兜底。

已拍板口径：火免疫灼烧/草免疫寄生/毒免疫中毒（施加拦截，中毒印记不受影响）；
灼烧（火）/中毒（毒）/引电（电）伤害吃属性克制、寄生真实伤害；灼烧减半向下取整
归零移除；引电达 2 层立即 25% 电伤扣 2 层余层保留。
"""

from __future__ import annotations

from environment.actions import Decision, recharge_action
from environment.atom import AddModifier
from environment.engine import apply_replacement, end_turn, execute_turn, resolve_turn
from environment.models import BattleRng, BattleState, SideState, StatModifier, Unit
from environment.pipeline import run
from environment.primitives import apply_mark
from environment.reducer import Frame
from environment.rules import DEFAULT_RULES
from environment.statuses import STATUS_TABLE, is_immune, status_kwargs


def _unit(name: str, types=("普通",), max_hp: int = 300, energy: int = 10) -> Unit:
    return Unit(name=name, types=list(types),
                stats={"hp": max_hp, "atk": 100, "sp_atk": 100, "def": 100, "sp_def": 100,
                       "speed": 100},
                max_hp=max_hp, current_hp=max_hp, energy=energy)


def _state(turn: int = 2, a_types=("普通",), b_types=("普通",), seed: int = 7,
           a_units=("甲",), b_units=("乙",)) -> BattleState:
    a = [_unit(n, types=a_types) for n in a_units]
    b = [_unit(n, types=b_types) for n in b_units]
    for i, u in enumerate(a):
        u.id = f"a-{i}-{u.name}"
    for i, u in enumerate(b):
        u.id = f"b-{i}-{u.name}"
    return BattleState(side_a=SideState(units=a, lives=2),
                       side_b=SideState(units=b, lives=2),
                       rng=BattleRng(seed), rules=DEFAULT_RULES, turn=turn)


def _apply(s: BattleState, side: str, stat: str, layers: int) -> list[dict]:
    """经管道施加状态层（走 AddModifier reducer 漏斗——含免疫拦截）。"""
    u = s.active(side)
    kwargs = status_kwargs(stat)
    events, _ = run(s, [AddModifier(side=side, unit=u, stat=stat,
                                    mode=STATUS_TABLE[stat][0], layers=layers,
                                    source="测试", kwargs=kwargs)], Frame())
    return events


def _record(u: Unit, stat: str) -> StatModifier | None:
    return next((m for m in u.stat_mods if m.stat == stat), None)


# ── 中毒：3%×层，毒系克制 ──
def test_poison_damage_neutral_and_effectiveness() -> None:
    s = _state()
    _apply(s, "a", "中毒", 2)
    events = end_turn(s)
    dmg = next(e for e in events if e["type"] == "damage")
    assert dmg["attacker"] == "中毒" and dmg["damage"] == 18       # 3%×2×300（毒打普通 1.0）
    assert s.active("a").current_hp == 282

    s2 = _state(b_types=("草",))
    _apply(s2, "b", "中毒", 2)
    events2 = end_turn(s2)
    dmg2 = next(e for e in events2 if e["type"] == "damage")
    assert dmg2["damage"] == 36                                   # 毒打草 ×2 → 18×2


# ── 灼烧：2%×层 + 向下取整减半、归零移除 ──
def test_burn_damage_and_floor_halving() -> None:
    s = _state()
    _apply(s, "a", "灼烧", 5)
    events = end_turn(s)
    dmg = next(e for e in events if e["type"] == "damage")
    assert dmg["attacker"] == "灼烧" and dmg["damage"] == 30       # 2%×5×300
    assert _record(s.active("a"), "灼烧").layers == 2              # 5//2

    s2 = _state()
    _apply(s2, "a", "灼烧", 1)
    end_turn(s2)
    assert _record(s2.active("a"), "灼烧") is None                 # 1//2=0 → 移除


def test_burn_halving_chain() -> None:
    s = _state()
    _apply(s, "a", "灼烧", 15)                                     # 累计 26×2% = 52% 不致死
    chain = []
    for _ in range(5):
        end_turn(s)
        chain.append(_record(s.active("a"), "灼烧").layers
                     if _record(s.active("a"), "灼烧") else 0)
    assert chain == [7, 3, 1, 0, 0]                                # 15→7→3→1→0


# ── 寄生：真实伤害 + 对手在场回复 ──
def test_parasite_true_damage_and_drain_heals_foe() -> None:
    s = _state()
    s.active("b").current_hp = 100                                 # 对手留出回复空间
    _apply(s, "a", "寄生", 2)
    events = end_turn(s)
    dmg = next(e for e in events if e["type"] == "damage")
    assert dmg["attacker"] == "寄生" and dmg["damage"] == 12       # 4%×300（真实伤害）
    heal = next(e for e in events if e["type"] == "heal")
    assert heal["unit"] == "乙" and heal["applied"] == 12          # 回复量 = 固定扣血量
    assert s.active("b").current_hp == 112


def test_parasite_true_damage_ignores_type() -> None:
    """寄生真实伤害：水系持有者照常 −4%×2（寄生的伤害系别为空串 → 无克制交互）。"""
    s = _state(a_types=("水",))
    _apply(s, "a", "寄生", 2)
    end_turn(s)
    assert s.active("a").current_hp == 288                         # 300 − 12


def test_parasite_foe_fainted_heals_zero() -> None:
    s = _state()
    s.active("b").fainted = True
    _apply(s, "a", "寄生", 1)
    events = end_turn(s)
    assert not any(e["type"] == "heal" for e in events)


# ── 引电：即时触发、余层保留 ──
def test_conduct_triggers_immediately_at_2_layers() -> None:
    s = _state()
    events = _apply(s, "a", "引电", 2)
    dmg = [e for e in events if e["type"] == "damage"]
    assert len(dmg) == 1 and dmg[0]["attacker"] == "引电" and dmg[0]["damage"] == 75
    assert s.active("a").current_hp == 225                          # 25%×300
    assert _record(s.active("a"), "引电") is None                   # 2−2=0 → 移除


def test_conduct_4_layers_triggers_twice() -> None:
    s = _state()
    _apply(s, "a", "引电", 4)
    assert s.active("a").current_hp == 150                          # 两次 25%
    assert _record(s.active("a"), "引电") is None


def test_conduct_thunder_weather_chain() -> None:
    """雷鸣天气 TURN_END +1 恰好到 2 层 → 同一反应循环内即时结算。"""
    from environment.weather import set_weather

    s = _state()
    _apply(s, "a", "引电", 1)
    set_weather(s, "雷鸣", 8, "惊雷")
    events = end_turn(s)
    assert any(e["type"] == "damage" and e["attacker"] == "引电" for e in events)
    assert s.active("a").current_hp == 225
    assert _record(s.active("a"), "引电") is None


# ── 属性免疫（施加拦截；中毒印记不受影响）──
def test_type_immunity_blocks_application() -> None:
    assert is_immune(_unit("x", types=("火",)), "灼烧")
    assert is_immune(_unit("x", types=("草",)), "寄生")
    assert is_immune(_unit("x", types=("毒",)), "中毒")
    assert not is_immune(_unit("x", types=("水",)), "灼烧")
    assert not is_immune(_unit("x", types=("火",)), "引电")

    s = _state(a_types=("火",), b_types=("草",))
    _apply(s, "a", "灼烧", 10)
    _apply(s, "b", "寄生", 3)
    _apply(s, "b", "中毒", 2)                                      # 草系不免疫中毒
    assert _record(s.active("a"), "灼烧") is None                  # 火免疫灼烧
    assert _record(s.active("b"), "寄生") is None                  # 草免疫寄生
    assert _record(s.active("b"), "中毒").layers == 2              # 中毒照常


def test_poison_mark_not_blocked_by_immunity() -> None:
    s = _state(a_types=("毒",))
    apply_mark(s.side_a, "中毒印记", 2)
    assert s.side_a.negative_marks[0].name == "中毒印记"           # 印记路径不拦截


# ── TURN_END 顺序：DOT → 印记 → 天气 ──
def test_turn_end_order_dot_then_marks_then_weather() -> None:
    from environment.weather import set_weather

    s = _state()
    apply_mark(s.side_a, "光合印记", 1)                            # 印记
    set_weather(s, "暴风雪", 8, "冬至")                            # 天气
    _apply(s, "a", "中毒", 1)                                      # DOT
    events = end_turn(s)
    kinds = []
    for e in events:
        if e["type"] == "damage":
            kinds.append(("dot", e["attacker"]))
        elif e["type"] == "energy_gain":
            kinds.append(("mark", e["source"]))
        elif e["type"] == "stat_change" and e["stat"] == "冻结":
            kinds.append(("weather", e["skill"]))
    assert [k[0] for k in kinds] == ["dot", "mark", "weather", "weather"]  # 暴风雪双方各一次


# ── DOT 致死 → 下一回合开场兜底 ──
def test_dot_faint_handled_by_next_turn_guard() -> None:
    s = _state(a_units=("甲", "乙"))
    s.active("a").max_hp = s.active("a").current_hp = 100
    _apply(s, "a", "中毒", 34)                                     # 102% → 必死
    end_turn(s)
    assert s.active("a").fainted
    events, need = resolve_turn(s, Decision(recharge_action()), Decision(recharge_action()))
    assert any(e["type"] == "faint" for e in events)
    assert need == "a"
    apply_replacement(s, "a", 1)
    assert s.active("a").name == "乙"
