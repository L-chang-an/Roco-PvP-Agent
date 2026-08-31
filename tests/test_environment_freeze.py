"""冻结力竭判定测试（2026-08-30）：血量低于冻结层数×5% → 力竭阵亡。

规则：只要某只精灵血量低于「冻结层数 × 5% × max_hp」即立刻力竭（非伤害，不触发
受击类效果）。触发于血量变化（受击 / DOT / 印记）与冻结层数变化（施加 / 增加），
严格小于才力竭（血量 == 阈值不力竭）。
"""

from __future__ import annotations

from environment.actions import Decision, recharge_action
from environment.atom import AddModifier, DealDamage, LoseHp
from environment.engine import apply_replacement, resolve_turn
from environment.models import BattleRng, BattleState, SideState, Unit
from environment.pipeline import run
from environment.reducer import Frame
from environment.rules import DEFAULT_RULES
from environment.statuses import STATUS_TABLE, status_kwargs


def _unit(name: str, max_hp: int = 300, energy: int = 10) -> Unit:
    return Unit(name=name, types=["普通"],
                stats={"hp": max_hp, "atk": 100, "sp_atk": 100, "def": 100, "sp_def": 100,
                       "speed": 100},
                max_hp=max_hp, current_hp=max_hp, energy=energy)


def _state(turn: int = 2, a_units=("甲",), b_units=("乙",)) -> BattleState:
    a = [_unit(n) for n in a_units]
    b = [_unit(n) for n in b_units]
    for i, u in enumerate(a):
        u.id = f"a-{i}-{u.name}"
    for i, u in enumerate(b):
        u.id = f"b-{i}-{u.name}"
    return BattleState(side_a=SideState(units=a, lives=2),
                       side_b=SideState(units=b, lives=2),
                       rng=BattleRng(7), rules=DEFAULT_RULES, turn=turn)


def _apply(s: BattleState, side: str, stat: str, layers: int) -> list[dict]:
    """经管道施加状态层（走 AddModifier reducer 漏斗）。"""
    u = s.active(side)
    events, _ = run(s, [AddModifier(side=side, unit=u, stat=stat,
                                    mode=STATUS_TABLE[stat][0], layers=layers,
                                    source="测试", kwargs=status_kwargs(stat))], Frame())
    return events


# ── 冻结力竭：施加时血量低于阈值 → 立即力竭 ──
def test_freeze_faint_on_apply_when_hp_below_threshold() -> None:
    s = _state()
    s.active("a").current_hp = 10                     # 1 层冻结阈值 = 300×5% = 15
    _apply(s, "a", "冻结", 1)
    assert s.active("a").fainted
    assert s.active("a").current_hp == 0


def test_freeze_no_faint_when_hp_above_threshold() -> None:
    s = _state()
    _apply(s, "a", "冻结", 1)                          # 满血 300 > 15，不力竭
    assert not s.active("a").fainted
    assert s.active("a").current_hp == 300


def test_freeze_faint_boundary_strict() -> None:
    s = _state()
    s.active("a").current_hp = 15                     # 恰等于阈值 → 严格小于才力竭
    _apply(s, "a", "冻结", 1)
    assert not s.active("a").fainted


# ── 血量变化触发力竭（受击 DamageApplied / DOT HpChanged）──
def test_freeze_faint_on_damage_applied() -> None:
    s = _state()
    _apply(s, "a", "冻结", 1)                          # 满血不力竭
    s.active("a").current_hp = 10                     # 直接设低（不触发事件）
    run(s, [DealDamage(side="b", source=s.active("b"), target=s.active("a"),
                       skill="测试", power=1, skill_type="普通", damage_kind="物攻",
                       effectiveness=1.0, stab=1.0)], Frame())
    assert s.active("a").fainted
    assert s.active("a").current_hp == 0


def test_freeze_faint_on_hp_changed() -> None:
    s = _state()
    _apply(s, "a", "冻结", 1)                          # 满血不力竭
    s.active("a").current_hp = 20                     # 20 > 15，尚未力竭
    run(s, [LoseHp(side="a", unit=s.active("a"), pct=6, source="测试")], Frame())  # −18 → 2
    assert s.active("a").fainted


def test_no_freeze_no_faint() -> None:
    s = _state()
    s.active("a").current_hp = 1
    run(s, [LoseHp(side="a", unit=s.active("a"), pct=0, source="测试")], Frame())
    assert not s.active("a").fainted                  # 无冻结层 → 永不力竭
    assert s.active("a").current_hp == 1


# ── 多层冻结阈值叠加 ──
def test_freeze_threshold_scales_with_layers() -> None:
    s = _state()
    s.active("a").current_hp = 40                     # 2 层阈值 = 30；3 层 = 45
    _apply(s, "a", "冻结", 2)                          # 40 > 30，不力竭
    assert not s.active("a").fainted
    _apply(s, "a", "冻结", 1)                          # 累计 3 层 → 40 < 45 → 力竭
    assert s.active("a").fainted


# ── 力竭 → 下一回合开场兜底补位（与常规阵亡同一路径）──
def test_freeze_faint_handled_by_next_turn_guard() -> None:
    s = _state(a_units=("甲", "乙"))
    s.active("a").current_hp = 10
    _apply(s, "a", "冻结", 1)                          # 立即力竭
    assert s.active("a").fainted
    events, need = resolve_turn(s, Decision(recharge_action()), Decision(recharge_action()))
    assert any(e["type"] == "faint" for e in events)
    assert need == "a"
    apply_replacement(s, "a", 1)
    assert s.active("a").name == "乙"
