"""M3 simulate：确定性贪心模拟 + 换边胜率下限 的测试。

引擎只支持 3v3/6v6（`build_battle_rules`），故用 3v3 阵容（强/弱）验证换边确定性。
"""

from __future__ import annotations

from rock_pvp_agent.advisor.simulate import _play, simulate_matchups

from rosters import mirror_pair, spec


def _strong_team() -> list[dict]:
    return [spec(f"强{i}", 500, 100, 100, 100, 100, 100, ["抓挠"]) for i in range(3)]


def _weak_team() -> list[dict]:
    return [spec(f"弱{i}", 50, 20, 20, 20, 20, 50, ["抓挠"]) for i in range(3)]


def _weak_team2() -> list[dict]:
    return [spec(f"靶{i}", 60, 25, 25, 25, 25, 55, ["抓挠"]) for i in range(3)]


def test_play_deterministic_same_seed():
    a, b = mirror_pair()
    assert _play(a, b, seed=7) == _play(a, b, seed=7)


def test_simulate_games_and_label():
    a, _ = mirror_pair()
    result = simulate_matchups(a, [_weak_team()], seeds=[1, 2, 3])
    assert result["strategy"] == "greedy"
    assert "贪心" in result["label"]
    m = next(iter(result["matchups"].values()))
    assert m["games"] == 2 * 3
    assert 0 <= m["wins"] <= m["games"]
    assert m["win_rate"] == m["wins"] / m["games"]
    assert m["seeds"] == [1, 1, 2, 2, 3, 3]


def test_simulate_strong_beats_weak_after_swap():
    """强 3v3 vs 弱 3v3：换边后仍以强胜，wins == games。"""
    result = simulate_matchups(_strong_team(), [_weak_team()], seeds=[1, 2, 3])
    m = next(iter(result["matchups"].values()))
    assert m["wins"] == m["games"] == 6


def test_simulate_multiple_opponents():
    result = simulate_matchups(_strong_team(), [_weak_team(), _weak_team2()], seeds=[1, 2])
    assert len(result["matchups"]) == 2
    for m in result["matchups"].values():
        assert m["games"] == 4
