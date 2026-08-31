"""R2 信度分配：校准偏差 + 反事实回放确定性 + 关键回合卡片。"""

from __future__ import annotations

from rock_pvp_agent.battle.evolution.analysis import analyze_record
from rock_pvp_agent.battle.evolution.credit import calibration_miss, mine_critical_turns
from rock_pvp_agent.battle.selfplay import run_selfplay


def test_calibration_miss():
    events = [{"type": "damage", "side": "a", "damage": 71}]
    assert calibration_miss("造成约30伤害", events, "a") is True    # |30-71|/71 = 58% > 30%
    assert calibration_miss("造成约71伤害", events, "a") is False   # 偏差 0
    assert calibration_miss("应该能赢", events, "a") is False       # 无数字 → 不 miss
    assert calibration_miss("造成约30伤害", [], "a") is True        # 预测伤害但实际 0 → miss


def test_mine_critical_turns_deterministic():
    record = run_selfplay(seed=7, team_size=3, lives=2)["record"]
    analysis = analyze_record(record)
    cards1 = mine_critical_turns(analysis, record, M=4)
    cards2 = mine_critical_turns(analysis, record, M=4)
    assert cards1 == cards2                                # 反事实确定性：同 seed 逐位复现
    for c in cards1:
        assert "turn_no" in c and "side" in c and "signals" in c
        assert "delta_winrate" in c and "feedback_text" in c and c["feedback_text"]
