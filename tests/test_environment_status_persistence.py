"""DOT 持久性与阵亡清理测试（2026-08-30 拍板）：

- 中毒/灼烧/寄生/引电 = **离场清空**的非永久 debuff；
- 萌化/冻结 = **永久 debuff**（离场保留，只靠技能/特性效果清除）；
- 免疫：火免疫灼烧 / 草免疫寄生 / 毒免疫中毒 / **冰免疫冻结**；
- **阵亡清层**：非永久 buff 与冻结层清除；永久层（萌化等）保留（复活特性）。
"""

from __future__ import annotations

from environment.actions import Decision, recharge_action, switch_action
from environment.atom import AddModifier
from environment.engine import resolve_turn, settle_faints
from environment.models import BattleRng, BattleState, SideState, StatModifier, Unit
from environment.pipeline import run
from environment.reducer import Frame
from environment.rules import DEFAULT_RULES
from environment.statuses import STATUS_TABLE, is_immune, status_kwargs


def _unit(name: str, types=("普通",), max_hp: int = 300) -> Unit:
    return Unit(name=name, types=list(types),
                stats={"hp": max_hp, "atk": 100, "sp_atk": 100, "def": 100, "sp_def": 100,
                       "speed": 100},
                max_hp=max_hp, current_hp=max_hp, energy=10)


def _state(a_units=("甲",), a_types=("普通",)) -> BattleState:
    a = [_unit(n, types=a_types) for n in a_units]
    b = [_unit("乙")]
    for i, u in enumerate(a):
        u.id = f"a-{i}-{u.name}"
    b[0].id = "b-0-乙"
    return BattleState(side_a=SideState(units=a, lives=2),
                       side_b=SideState(units=b, lives=2),
                       rng=BattleRng(7), rules=DEFAULT_RULES, turn=2)


def _apply(s: BattleState, side: str, stat: str, layers: int) -> list[dict]:
    u = s.active(side)
    events, _ = run(s, [AddModifier(side=side, unit=u, stat=stat,
                                    mode=STATUS_TABLE[stat][0], layers=layers,
                                    source="测试", kwargs=status_kwargs(stat))], Frame())
    return events


def _record(u: Unit, stat: str) -> StatModifier | None:
    return next((m for m in u.stat_mods if m.stat == stat), None)


# ── 属性免疫：冰免疫冻结 ──
def test_ice_immune_to_freeze() -> None:
    assert is_immune(_unit("x", types=("冰",)), "冻结")
    assert not is_immune(_unit("x", types=("水",)), "冻结")
    assert not is_immune(_unit("x", types=("冰",)), "萌化")     # 萌化无属性免疫

    s = _state(a_types=("冰",))
    _apply(s, "a", "冻结", 2)
    assert _record(s.active("a"), "冻结") is None              # 冰免疫冻结：不落层


# ── 持久性：冻结/萌化离场保留，DOT 离场清除 ──
def test_freeze_persists_through_switch() -> None:
    s = _state(a_units=("甲", "乙"), a_types=("水",))
    _apply(s, "a", "冻结", 2)
    assert _record(s.active("a"), "冻结").permanent            # 永久 debuff
    resolve_turn(s, Decision(switch_action(1)), Decision(recharge_action()))
    assert _record(s.side_a.units[0], "冻结").layers == 2       # 换人后保留


def test_morph_persists_through_switch() -> None:
    from environment.evolution import base_stats_of
    from environment.statline import calc_combat_stats

    base = base_stats_of("魔力猫")
    stats = calc_combat_stats(base, {}, "坦率")
    u = Unit(name="魔力猫", types=["草"], base_stats=dict(base), stats=dict(stats),
             nature="坦率", iv={}, max_hp=stats["hp"], current_hp=stats["hp"], energy=10)
    u.id = "a-0-x"
    bench = _unit("乙")
    bench.id = "a-1-乙"
    b = _unit("丙")
    b.id = "b-0-丙"
    s = BattleState(side_a=SideState(units=[u, bench], lives=2),
                    side_b=SideState(units=[b], lives=2),
                    rng=BattleRng(7), rules=DEFAULT_RULES, turn=2)
    _apply(s, "a", "萌化", 1)
    assert _record(s.active("a"), "萌化").permanent            # 永久 debuff
    resolve_turn(s, Decision(switch_action(1)), Decision(recharge_action()))
    assert _record(s.side_a.units[0], "萌化").layers == 1       # 换人后保留
    assert s.side_a.units[0].stats == calc_combat_stats(base_stats_of("喵呜"), {}, "坦率")


def test_dot_cleared_on_switch() -> None:
    s = _state(a_units=("甲", "乙"), a_types=("普通",))
    _apply(s, "a", "中毒", 2)
    _apply(s, "a", "引电", 1)
    assert not _record(s.active("a"), "中毒").permanent        # 非永久 debuff
    resolve_turn(s, Decision(switch_action(1)), Decision(recharge_action()))
    assert _record(s.side_a.units[0], "中毒") is None          # 离场清除
    assert _record(s.side_a.units[0], "引电") is None


# ── 阵亡清层：非永久 + 冻结清除，永久（萌化）保留 ──
def test_faint_clears_nonpermanent_and_freeze_keeps_permanent() -> None:
    s = _state(a_units=("甲", "乙"))
    u = s.active("a")
    u.stat_mods = [
        StatModifier(stat="atk", mode="pct", layers=2, permanent=False, source="x"),
        StatModifier(stat="def", mode="pct", layers=3, permanent=True, source="x"),
        StatModifier(stat="冻结", mode="special", layers=2, permanent=True, source="x",
                     kwargs={"pct": 5}),
        StatModifier(stat="萌化", mode="special", layers=1, permanent=True, source="x"),
    ]
    u.fainted = True
    events, need = settle_faints(s)
    assert need == "a" and any(e["type"] == "faint" for e in events)
    stats = {m.stat for m in u.stat_mods}
    assert stats == {"def", "萌化"}                            # 非永久/冻结清除，永久保留
