"""v3 骨架·行为等价哨兵：锁定重构前基线，v3 各阶段必须逐位复现。

护栏 = 两层指纹，任一漂移即说明重构破坏行为：
1. `digest`：整局 state_hash 序列指纹（确定性闸门）。
2. `event_sig`：每回合事件流的关键字段签名（比 state_hash 更早暴露"事件形状"漂移）。

四个代表性场景覆盖：3v3 随机全局（mirror）、1v1 脚本技能（duel，攻击+状态+防御）、
1v1 碾压（strong_weak）、1v1 速度悬殊（fast_slow）。v3 重构后此文件必须原样通过。
"""

from __future__ import annotations

import hashlib

from environment.match import run_match
from environment.players import RandomPlayer, ScriptedPlayer
from environment.rules import DEFAULT_RULES
from environment.session import BattleSession

from rosters import RULES_1V1, duel, fast_slow, mirror_pair, strong_weak

# 事件签名关心的关键字段（只取影响行为的，忽略展示装饰）。
_SIG_FIELDS = (
    "type", "side", "attacker", "skill", "target", "stat", "mode", "layers",
    "damage", "gained", "energy", "applied", "overflow", "hp", "hit", "hits",
    "eff", "stab", "counter", "reduced", "mult", "item", "unit", "out", "in",
    "cleared_layers",
)


def event_sig(events: list[dict]) -> str:
    """事件流 → 稳定签名：逐条 (type, side, 关键字段) 摘要。"""
    parts = []
    for e in events:
        k = [str(e.get("type")), str(e.get("side"))]
        for f in _SIG_FIELDS:
            if f in e:
                k.append(f"{f}={e[f]}")
        parts.append("|".join(k))
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def _run(ra, rb, seed, rules, players):
    session = BattleSession.start(ra, rb, seed=seed, rules=rules)
    result = run_match(session, players)
    return result.digest(), [event_sig(t.events) for t in result.turns][:3]


def _mirror_random():
    return _run(*mirror_pair(), seed=7, rules=DEFAULT_RULES,
                players={"a": RandomPlayer("a", seed=8), "b": RandomPlayer("b", seed=9)})


def _duel_script():
    return _run(*duel(), seed=7, rules=RULES_1V1,
                players={"a": ScriptedPlayer("a", []), "b": ScriptedPlayer("b", [])})


def _strong_weak_script():
    return _run(*strong_weak(), seed=7, rules=RULES_1V1,
                players={"a": ScriptedPlayer("a", []), "b": ScriptedPlayer("b", [])})


def _fast_slow_script():
    return _run(*fast_slow(), seed=7, rules=RULES_1V1,
                players={"a": ScriptedPlayer("a", []), "b": ScriptedPlayer("b", [])})


def test_sentinel_mirror_random() -> None:
    d, sigs = _mirror_random()
    assert d == "4a7ab3f7abdfd4f9b6b83cdf975014138e99172163c26847da83e7ad98a1eb3a"
    assert sigs == ["7d98944f33125af8", "f8992b046d52361c", "10cf09b5a748305f"]


def test_sentinel_duel_script() -> None:
    d, sigs = _duel_script()
    assert d == "ac65e1116c5ca359d78f088fcf326c7567ce3005b60dacca81eee56f71866c9a"
    assert sigs == ["3dccc05ad6be61e0", "3dccc05ad6be61e0", "3dccc05ad6be61e0"]


def test_sentinel_strong_weak() -> None:
    d, sigs = _strong_weak_script()
    assert d == "0b1eb458237768c26c7b5fc051617037577261a4e150cc9a34b590ee32982a19"
    assert sigs == ["0dc8ceef315d5b89", "0dc8ceef315d5b89", "0dc8ceef315d5b89"]


def test_sentinel_fast_slow() -> None:
    d, sigs = _fast_slow_script()
    assert d == "61e59168991f3a42d78629290f1e471097294bb9771146e223aac34cf2357d80"
    assert sigs == ["7dc67c90eba87087", "7dc67c90eba87087", "7dc67c90eba87087"]
