"""R0 可观测度量 · environment/evaluate.py 纯函数：situation_key / v_heuristic / ko_thresholds。"""

from __future__ import annotations

import dataclasses

from environment.evaluate import ko_thresholds, situation_key, v_heuristic
from environment.rules import DEFAULT_RULES
from environment.session import BattleSession
from rosters import spec


def _duel_session():
    """1v1 对局（a 速 100 > b 速 90），供 situation_key / v_heuristic 测试。"""
    rules = dataclasses.replace(DEFAULT_RULES, team_size=1)
    a = [spec("甲", 300, 100, 100, 100, 100, 100, ["抓挠"])]
    b = [spec("乙", 300, 100, 100, 100, 100, 90, ["抓挠"])]
    return BattleSession.start(a, b, seed=1, rules=rules, battle_id="t")


def test_situation_key_shape():
    key = situation_key(_duel_session().view("a"))
    assert key.startswith("my")
    assert "/" in key
    # 十个分量：命数×2 / 在场×2 / 能量档×2 / 已揭示 / 阶段 / 后备×2
    assert len(key.split("/")) == 10


def test_situation_key_deterministic():
    s = _duel_session()
    assert situation_key(s.view("a")) == situation_key(s.view("a"))


def test_v_heuristic_antisymmetric():
    s = _duel_session()
    va = v_heuristic(s.state, "a")
    vb = v_heuristic(s.state, "b")
    assert abs(va + vb) < 1e-9


def test_ko_thresholds_populated_and_deterministic():
    t1 = ko_thresholds()
    t2 = ko_thresholds()
    assert t1 == t2
    assert len(t1) > 0
    # 值只可能是 1（一击）或 2（两回合击杀）
    for row in t1.values():
        for sub in row.values():
            for hits in sub.values():
                assert hits in (1, 2)
