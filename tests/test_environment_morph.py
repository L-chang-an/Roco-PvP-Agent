"""萌化结算测试（2026-08-30）：退化重算 / 最低阶拦截 / 解除回升 / HP 比例缩放。

已拍板口径：萌化 = 沿进化链往低一阶退种族值资质（x 层 = x 阶）；实际资质已最低阶
→ 不再获得层数；解除 x 层沿链回升；**名字/特性/技能不变**（只改资质与六维）。
"""

from __future__ import annotations

from environment.atom import AddModifier, SetModLayers
from environment.evolution import base_stats_of
from environment.models import BattleRng, BattleState, SideState, Unit
from environment.pipeline import run
from environment.reducer import Frame
from environment.rules import DEFAULT_RULES
from environment.statline import calc_combat_stats


def _expect_stats(name: str) -> dict[str, int]:
    return calc_combat_stats(base_stats_of(name), {}, "坦率")


def _morph_unit(name: str, hp_ratio: float = 1.0) -> Unit:
    base = base_stats_of(name)
    stats = _expect_stats(name)
    return Unit(name=name, types=["草"], base_stats=dict(base), stats=dict(stats),
                nature="坦率", iv={}, max_hp=stats["hp"],
                current_hp=max(1, int(stats["hp"] * hp_ratio)), energy=10)


def _state(unit: Unit) -> BattleState:
    unit.id = "a-0-x"
    foe = Unit(name="迪莫", types=["光"],
               stats={"hp": 300, "atk": 100, "sp_atk": 100, "def": 100, "sp_def": 100,
                      "speed": 100},
               max_hp=300, current_hp=300, energy=10)
    foe.id = "b-0-y"
    return BattleState(side_a=SideState(units=[unit], lives=2),
                       side_b=SideState(units=[foe], lives=2),
                       rng=BattleRng(7), rules=DEFAULT_RULES, turn=2)


def _apply_morph(s: BattleState, layers: int) -> list[dict]:
    u = s.active("a")
    events, _ = run(s, [AddModifier(side="a", unit=u, stat="萌化", mode="special",
                                    layers=layers, source="退化")], Frame())
    return events


def _set_morph(s: BattleState, layers: int) -> list[dict]:
    u = s.active("a")
    events, _ = run(s, [SetModLayers(unit=u, stat="萌化", mode="special",
                                     layers=layers, source="解除")], Frame())
    return events


# ── 退化重算 ──
def test_morph_degrade_one_step() -> None:
    s = _state(_morph_unit("魔力猫"))
    _apply_morph(s, 1)
    u = s.active("a")
    assert u.stats == _expect_stats("喵呜")        # 种族值退一阶
    assert u.name == "魔力猫"                       # 名字不变（只改资质）


def test_morph_degrade_two_steps() -> None:
    s = _state(_morph_unit("魔力猫"))
    _apply_morph(s, 2)
    assert s.active("a").stats == _expect_stats("喵喵")


def test_morph_clamped_to_lowest() -> None:
    s = _state(_morph_unit("魔力猫"))
    _apply_morph(s, 5)                              # 越界 → 夹到最低阶
    assert s.active("a").stats == _expect_stats("喵喵")


# ── 最低阶拦截 ──
def test_morph_lowest_blocked() -> None:
    s = _state(_morph_unit("喵喵"))                 # 链最低阶
    events = _apply_morph(s, 1)
    assert events == []                             # 拦截：不落层、不发事件
    assert s.active("a").stats == _expect_stats("喵喵")
    assert s.active("a").stat_mods == []


def test_morph_magic_cat_blocked_at_two_layers() -> None:
    s = _state(_morph_unit("魔力猫"))
    _apply_morph(s, 2)                              # 已退到最低阶
    _apply_morph(s, 1)                              # 再加 → 拦截
    assert s.active("a").stats == _expect_stats("喵喵")
    assert s.active("a").stat_mods[0].layers == 2


# ── 解除回升 ──
def test_morph_layers_partially_removed() -> None:
    s = _state(_morph_unit("魔力猫"))
    _apply_morph(s, 2)
    _set_morph(s, 1)                                # 解除 1 层 → 回升一阶
    assert s.active("a").stats == _expect_stats("喵呜")


def test_morph_fully_removed_restores() -> None:
    s = _state(_morph_unit("魔力猫"))
    _apply_morph(s, 2)
    _set_morph(s, 0)                                # 全解除 → 回升到原资质
    assert s.active("a").stats == _expect_stats("魔力猫")
    assert all(m.stat != "萌化" for m in s.active("a").stat_mods)


# ── HP 同比例缩放（下限 1）──
def test_morph_hp_scales_proportionally() -> None:
    s = _state(_morph_unit("魔力猫", hp_ratio=0.5))
    u = s.active("a")
    old_max, old_cur = u.max_hp, u.current_hp
    new_max = _expect_stats("喵呜")["hp"]
    _apply_morph(s, 1)
    assert u.max_hp == new_max
    assert u.current_hp == max(1, int(new_max * (old_cur / old_max)))


def test_morph_heal_restores_hp_proportionally() -> None:
    s = _state(_morph_unit("魔力猫", hp_ratio=0.25))
    u = s.active("a")
    old_max, old_cur = u.max_hp, u.current_hp
    new_max = _expect_stats("喵喵")["hp"]
    _apply_morph(s, 2)
    assert u.max_hp == new_max
    assert u.current_hp == max(1, int(new_max * (old_cur / old_max)))
