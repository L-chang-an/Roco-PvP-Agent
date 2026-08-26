"""E4 无平局判定测试：超过回合上限后按 ①命数 → ②血量百分比和 → ③随机硬币 定胜负。

`timeout_winner` 返回 (胜方, 判定依据)；硬币走引擎 RNG 流（calls+1），同 seed 可复现。
"""

from __future__ import annotations

from dataclasses import replace

from environment.actions import Decision, recharge_action, skill_action
from environment.engine import execute_turn
from environment.models import new_battle
from environment.rules import DEFAULT_RULES

from rosters import RULES_1V1, spec, tanky_pair


def _battle(a, b, *, rules) -> "new_battle":
    return new_battle(a, b, seed=7, rules=rules)


def _be_message(events) -> str:
    be = [e for e in events if e["type"] == "battle_end"][0]
    return be["message"]


def test_rule1_lives_decides() -> None:
    """命数多者胜：b 首回合阵亡掉 1 命（还有存活后备），a 命数领先 → a 胜。"""
    rules = replace(RULES_1V1, team_size=2, lives=2, max_turns=1)
    a = [spec("强攻", 500, 100, 100, 100, 100, 100, ["抓挠1"]),
         spec("强攻2", 500, 100, 100, 100, 100, 100, ["抓挠1"])]
    b = [spec("弱靶", 30, 1, 1, 1, 1, 50, ["撞击"]),
         spec("弱2", 30, 1, 1, 1, 1, 50, ["撞击"])]
    s = _battle(a, b, rules=rules)
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert s.done and s.winner == "a"
    assert "命数" in _be_message(events)


def test_rule2_hp_percent_sum_decides() -> None:
    """血量百分比和胜：双方命数相同、血量和 a 高（b 被打了 1 下）→ a 胜。"""
    rules = replace(RULES_1V1, max_turns=1)
    a = [spec("甲", 300, 100, 100, 100, 100, 100, ["抓挠"])]
    b = [spec("乙", 300, 100, 100, 100, 100, 90, ["撞击"])]
    s = _battle(a, b, rules=rules)
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert s.done and s.winner == "a"
    assert "血量百分比和" in _be_message(events)


def test_rule3_random_coin_decides() -> None:
    """命数/血量和都相同 → 随机硬币定胜（走引擎 RNG，calls+1）。"""
    rules = replace(RULES_1V1, max_turns=1)
    a = [spec("快攻", 5000, 1, 1, 500, 500, 200, ["抓挠"])]   # 速度互异 → 无出手顺序硬币
    b = [spec("慢防", 5000, 1, 1, 500, 500, 100, ["抓挠"])]
    s = _battle(a, b, rules=rules)
    events = execute_turn(s, Decision(skill_action(0)), Decision(skill_action(0)))
    assert s.done and s.winner in ("a", "b")
    assert "随机判定" in _be_message(events)
    assert s.rng.calls == 1        # 唯一一枚硬币来自超时判定（速度互异 → 顺序层面从不抽）


def test_rule3_deterministic_same_seed() -> None:
    """同 seed 两次超时判定：胜方一致（马尔可夫复现）。"""
    s1 = _battle(*tanky_pair(), rules=replace(RULES_1V1, max_turns=1))
    execute_turn(s1, Decision(skill_action(0)), Decision(skill_action(0)))
    s2 = _battle(*tanky_pair(), rules=replace(RULES_1V1, max_turns=1))
    execute_turn(s2, Decision(skill_action(0)), Decision(skill_action(0)))
    assert s1.winner == s2.winner


def test_rule3_replayable_via_from_dict() -> None:
    """从快照恢复后再判定，硬币结果一致（rng 位置在状态里）。"""
    from environment.models import BattleState
    s = _battle(*tanky_pair(), rules=replace(RULES_1V1, max_turns=1))
    execute_turn(s, Decision(skill_action(0)), Decision(skill_action(0)))
    s2 = BattleState.from_dict(s.to_dict())
    assert s2.winner == s.winner and s2.rng.calls == s.rng.calls


def test_timeout_winner_reflected_in_done_state() -> None:
    """超时定胜负写进 state.winner/done，且 digest 稳定（E0 digest 不变量由 match 测试兜住）。"""
    s = _battle(*tanky_pair(), rules=replace(RULES_1V1, max_turns=1))
    execute_turn(s, Decision(skill_action(0)), Decision(skill_action(0)))
    assert s.done and s.winner in ("a", "b")
    assert s.to_dict()["winner"] == s.winner
