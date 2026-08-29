"""v3 反应管道（pipeline.run）测试：fixpoint 循环 / 领域事件回传 / 保护闸 / 顺序契约。

pipeline 是全库唯一运行「执行 → 发事件 → 收集反应 → 再执行」循环的地方；
本文件用假 collector 注入验证循环语义（真实 collector = triggers.collect_reactions，
行为等价由既有 600 测试 + 4 哨兵把关）。
"""

from __future__ import annotations

import pytest

from environment.atom import GainEnergy, TraitGain
from environment.domain import EnergyChanged, SkillResolved, StatModChanged
from environment.models import BattleState, BattleRng, SideState, TraitState, Unit
from environment.pipeline import run
from environment.reducer import Frame
from environment.rules import DEFAULT_RULES


def _unit(name: str = "测试") -> Unit:
    return Unit(name=name, types=["普通"],
                stats={"hp": 300, "atk": 100, "sp_atk": 100, "def": 100, "sp_def": 100,
                       "speed": 100},
                max_hp=300, current_hp=300, energy=10)


def _state() -> BattleState:
    return BattleState(side_a=SideState(units=[_unit("甲")], lives=2),
                       side_b=SideState(units=[_unit("乙")], lives=2),
                       rng=BattleRng(7), rules=DEFAULT_RULES)


def _resolved(u: Unit, dealt_counter: bool = False) -> SkillResolved:
    return SkillResolved(unit_id=u.id, skill="测试技能", dealt_counter=dealt_counter,
                         skill_type="火")


def test_run_returns_display_and_domain_events() -> None:
    """GainEnergy atom：展示事件 + 领域事件（EnergyChanged 挂 Frame 并随返回值回传）。"""
    s = _state()
    u = s.active("a")
    u.energy = 5
    frame = Frame()
    events, domain = run(s, [GainEnergy(side="a", unit=u, amount=3, source="x")], frame)
    assert events[0]["type"] == "energy_gain" and events[0]["gained"] == 3
    assert u.energy == 8
    assert any(isinstance(e, EnergyChanged) and e.unit_id == u.id and e.after == 8
               for e in domain)
    assert domain == frame.domain_events


def test_run_reactions_until_fixpoint() -> None:
    """假 collector：首次返回 TraitGain、之后返回 [] → 反应执行且循环终止（不无限）。"""
    s = _state()
    u = s.active("a")
    u.trait = TraitState(name="t")
    calls: list[str] = []

    def collector(state, event, unit, trait_defs, energy_max):
        calls.append(type(event).__name__)
        if len(calls) == 1:
            return [TraitGain(unit=u, stat="atk", mode="pct", layers=2, source="t")]
        return []

    events, domain = run(s, [], Frame(), unit=u, trait_defs=[],
                         collector=collector, after=lambda f: [_resolved(u)])
    assert calls == ["SkillResolved", "StatModChanged"]   # 反应产生的领域事件再触发一轮
    assert events == []                                    # 特性增益不发展示事件（旧行为）
    assert u.trait.gains[0].stat == "atk" and u.trait.gains[0].layers == 2
    assert any(isinstance(e, StatModChanged) and e.stat == "atk" for e in domain)


def test_run_after_events_processed_after_atom_domain_events() -> None:
    """顺序契约：after 事件（SkillResolved）排在原子领域事件（EnergyChanged）之后。"""
    s = _state()
    u = s.active("a")
    u.energy = 5
    seen: list[str] = []

    def collector(state, event, unit, trait_defs, energy_max):
        seen.append(type(event).__name__)
        return []

    run(s, [GainEnergy(side="a", unit=u, amount=1, source="x")], Frame(),
        collector=collector, after=lambda f: [_resolved(u)])
    assert seen == ["EnergyChanged", "SkillResolved"]


def test_run_budget_exceeded_warns_and_stops() -> None:
    """保护闸：反应原子数超 budget → 确定性 warn + 停止（丢弃剩余事件）。"""
    s = _state()
    u = s.active("a")
    u.trait = TraitState(name="t")

    def collector(state, event, unit, trait_defs, energy_max):
        return [TraitGain(unit=u, stat="atk", mode="pct", layers=1, source="t")]

    frame = Frame()
    with pytest.warns(UserWarning, match="反应预算超限"):
        run(s, [], frame, unit=u, collector=collector,
            after=lambda f: [_resolved(u)], budget=2)
    # budget=2：两轮反应各执行 1 个 TraitGain（同源合并 → 2 层），第三轮被闸停
    assert u.trait.gains[0].layers == 2


def test_run_max_events_guard_warns() -> None:
    """兜底闸：处理事件数超 max_events → warn + 停止（即使原子数没超预算）。"""
    s = _state()
    u = s.active("a")
    u.trait = TraitState(name="t")

    def collector(state, event, unit, trait_defs, energy_max):
        return []   # 不产生原子，但事件队列被 after 之外持续注入的场景不可达——
    # 用不产生原子但制造事件的方式模拟：直接给 frame 预注入大量领域事件。

    frame = Frame()
    from environment.domain import StatModChanged as SMC
    frame.domain_events = [SMC(u.id, "atk", "pct", 1, 1, "t") for _ in range(10)]
    with pytest.warns(UserWarning, match="反应预算超限"):
        events, domain = run(s, [], frame, collector=collector, max_events=3)
    assert len(domain) == 10


def test_trait_gain_emits_stat_mod_changed() -> None:
    """TraitGain atom 直执行：frame.domain_events 含 StatModChanged（特性增益进入领域层）。"""
    s = _state()
    u = s.active("a")
    u.trait = TraitState(name="t")
    frame = Frame()
    events, domain = run(s, [TraitGain(unit=u, stat="atk", mode="pct", layers=2,
                                       source="t")], frame)
    assert events == []   # 无展示事件（旧行为）
    assert any(isinstance(e, StatModChanged) and e.stat == "atk"
               and e.total_layers == 2 and e.source == "t" for e in domain)
