"""假 LLM 对战玩家测试（roco_pvp_agent/battle/player.py）。

FakeLLMPlayer = 固定回复前缀 + 随机动作（委托 RandomPlayer，独立 RNG 流）。
真实 LLM 玩家以后实现同一个 Player Protocol；本测试钉死假玩家的行为契约。
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")  # 未装 ui extra 时优雅跳过（该模块依赖路径用不到 fastapi，但保持惯例）

from environment.actions import Decision, validate_decision  # noqa: E402
from environment.session import BattleSession  # noqa: E402
from roco_pvp_agent.battle.player import FIXED_REPLY_PREFIX, FakeLLMPlayer  # noqa: E402

from rosters import RULES_1V1, duel  # noqa: E402


def test_kind_and_side() -> None:
    p = FakeLLMPlayer("b", seed=1)
    assert p.kind == "fake_llm" and p.side == "b"


def test_decide_returns_legal_action_and_fixed_reply() -> None:
    s = BattleSession.start(*duel(), seed=1, rules=RULES_1V1)
    p = FakeLLMPlayer("b", seed=1)
    dec = p.decide(s.view("b"), s.legal_actions("b"), s.legal_items("b"))
    assert isinstance(dec, Decision)
    assert validate_decision(s.state, "b", dec) is None        # 永远给出合法动作
    assert p.last_reply.startswith(FIXED_REPLY_PREFIX)          # 固定回复前缀
    assert len(p.last_reply) > len(FIXED_REPLY_PREFIX)


def test_reply_reflects_decision() -> None:
    s = BattleSession.start(*duel(), seed=1, rules=RULES_1V1)
    p = FakeLLMPlayer("b", seed=1)
    p.decide(s.view("b"), s.legal_actions("b"), s.legal_items("b"))
    # 只断言回复非空且带动作描述关键词（随机决定是哪一类）
    assert any(k in p.last_reply for k in ("技能", "换人", "聚能", "行动"))


def test_decide_deterministic_given_seed() -> None:
    s = BattleSession.start(*duel(), seed=1, rules=RULES_1V1)
    legal, items = s.legal_actions("b"), s.legal_items("b")
    p1 = FakeLLMPlayer("b", seed=99)
    p2 = FakeLLMPlayer("b", seed=99)
    assert p1.decide(s.view("b"), legal, items) == p2.decide(s.view("b"), legal, items)


def test_choose_replacement_from_bench() -> None:
    s = BattleSession.start(*duel(), seed=1, rules=RULES_1V1)
    p = FakeLLMPlayer("b", seed=2)
    bench = [0, 1]
    assert p.choose_replacement(s.view("b"), bench) in bench


def test_noop_hooks() -> None:
    p = FakeLLMPlayer("b", seed=1)
    p.on_match_start({})         # 不抛
    p.on_turn_result({}, [])     # 不抛
