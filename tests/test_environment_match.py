"""E0b 整局测试：马尔可夫性 / 序列化 / 确定性 / 编排（约 12 组）。

马尔可夫性是本文件最重要的一条：`from_dict(to_dict(s))` 往返后再跑同一回合，
事件流必须逐字节相同、state_hash 相同——这是核心不变式的机械证明。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from environment.actions import Decision, legal_actions, legal_items, recharge_action, skill_action
from environment.engine import step
from environment.match import run_match
from environment.models import BattleState, new_battle
from environment.players import RandomPlayer, ScriptedPlayer
from environment.rules import DEFAULT_RULES
from environment.session import BattleSession

from rosters import RULES_1V1, duel, fast_slow, mirror_pair, spec, strong_weak, tanky_pair


def _rand_pair(seed: int) -> dict:
    return {"a": RandomPlayer("a", seed=seed + 1), "b": RandomPlayer("b", seed=seed + 2)}


# ── 马尔可夫性（最重要）─────────────────────────────────────────────────────
def test_markov_property() -> None:
    """快照往返后跑同一回合：事件流逐字节相同、state_hash 相同。"""
    session = BattleSession.start(*mirror_pair(), seed=42)
    pa, pb = RandomPlayer("a", seed=43), RandomPlayer("b", seed=44)
    for _ in range(3):
        da = pa.decide(session.observe("a"), session.legal_actions("a"), session.legal_items("a"))
        db = pb.decide(session.observe("b"), session.legal_actions("b"), session.legal_items("b"))
        assert session.submit("a", da)["ok"] and session.submit("b", db)["ok"]
        session.resolve()
    s1 = session.state
    s2 = BattleState.from_dict(s1.to_dict())
    da = pa.decide(s1.to_dict(), legal_actions(s1, "a"), legal_items(s1, "a"))
    db = pb.decide(s1.to_dict(), legal_actions(s1, "b"), legal_items(s1, "b"))
    st1, ev1 = step(s1, da, db)
    st2, ev2 = step(s2, da, db)
    assert ev1 == ev2                       # 事件流逐字节相同
    assert st1.state_hash() == st2.state_hash()  # 落到同一份新状态


def test_clone_independent() -> None:
    s = new_battle(*mirror_pair(), seed=9)
    c = s.clone()
    c.active("a").current_hp = 1
    assert s.active("a").current_hp != 1


def test_step_does_not_mutate_input() -> None:
    s = new_battle(*mirror_pair(), seed=9)
    h = s.state_hash()
    _, events = step(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert s.state_hash() == h and events


# ── 序列化 ──────────────────────────────────────────────────────────────────
def test_to_dict_json_native_no_default() -> None:
    s = new_battle(*mirror_pair(), seed=9)
    json.dumps(s.to_dict())   # 不许传 default= 也能过，否则当场炸


def test_from_dict_roundtrip_hash() -> None:
    s = new_battle(*mirror_pair(), seed=9)
    assert BattleState.from_dict(s.to_dict()).state_hash() == s.state_hash()


def test_rules_roundtrip() -> None:
    from dataclasses import replace
    rules = replace(DEFAULT_RULES, max_turns=3, team_size=1, lives=5)
    s = new_battle(*strong_weak(), seed=9, rules=rules)
    restored = BattleState.from_dict(s.to_dict())
    assert restored.rules == rules and restored.rules.max_turns == 3


def test_rng_position_roundtrip() -> None:
    s = new_battle(*mirror_pair(), seed=11)
    s.rng.choice([0, 1])                       # 消耗 1 次抽取
    restored = BattleState.from_dict(s.to_dict())
    assert s.rng.choice(["x", "y"]) == restored.rng.choice(["x", "y"])
    assert s.rng.calls == restored.rng.calls


# ── 确定性 ──────────────────────────────────────────────────────────────────
def test_same_seed_same_digest_and_decisions() -> None:
    def play(seed: int):
        session = BattleSession.start(*mirror_pair(), seed=seed)
        result = run_match(session, _rand_pair(seed))
        return result, [t.decision_a for t in result.turns]

    r1, d1 = play(20260823)
    r2, d2 = play(20260823)
    assert r1.digest() == r2.digest()
    assert d1 == d2


def test_diff_seed_diff_digest() -> None:
    r1 = run_match(BattleSession.start(*mirror_pair(), seed=1), _rand_pair(1))
    r2 = run_match(BattleSession.start(*mirror_pair(), seed=2), _rand_pair(2))
    assert r1.digest() != r2.digest()


def test_subprocess_digest_matches_inprocess() -> None:
    """跨进程 digest 与进程内 digest 相同——证明没有环境相关的非确定性。"""
    from environment.__main__ import _battle_picks
    from environment.dataset import DataSource
    from environment.teambuilder import build_roster
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src") + os.pathsep + env.get("PYTHONPATH", "")
    out = subprocess.run(
        [sys.executable, "-m", "environment", "battle", "--seed", "20260823",
         "--preset", "mirror", "--json"],
        capture_output=True, text=True, cwd=root, env=env, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    cli_digest = json.loads(out.stdout)["digest"]
    pa, pb = _battle_picks("mirror", DataSource.E0)
    # battle_id 进 state_hash，必须与 CLI 一致（CLI 固定用 f"cli-{seed}"）
    session = BattleSession.start(build_roster(pa), build_roster(pb), seed=20260823,
                                  battle_id="cli-20260823")
    result = run_match(session, _rand_pair(20260823))
    assert result.digest() == cli_digest


def test_no_order_coins_when_speeds_differ() -> None:
    """速度互异跑完整局：玩家 RNG 流与引擎 RNG 流独立，引擎**不出手顺序**硬币。

    E4 取消平局后：若对局拖到回合上限，超时判定会抽**一枚决胜硬币**（命数/血量和都相同），
    故断言 `calls <= 1`——唯一允许的一枚来自超时定胜负，而不是出手顺序。
    """
    session = BattleSession.start(*fast_slow(), seed=3, rules=RULES_1V1)
    result = run_match(session, _rand_pair(3))
    assert result.done and session.state.rng.calls <= 1


# ── 编排 ────────────────────────────────────────────────────────────────────
def test_turn_numbers_consecutive() -> None:
    session = BattleSession.start(*mirror_pair(), seed=5)
    result = run_match(session, _rand_pair(5))
    assert [t.turn for t in result.turns] == list(range(1, result.turn_count + 1))
    assert result.turn_count == result.turns[-1].turn


def test_decisive_turn_in_turns() -> None:
    """决胜回合在 result.turns 里，且 battle_end 是最后一条事件。"""
    session = BattleSession.start(*strong_weak(), seed=5, rules=RULES_1V1)
    result = run_match(session, _rand_pair(5))
    assert result.winner == "a" and result.done
    last = result.turns[-1]
    assert last.events[-1]["type"] == "battle_end"


def test_random_matches_terminate() -> None:
    """for seed in range(50) 全部终止，且不是全平局。"""
    winners: list[str | None] = []
    for seed in range(50):
        session = BattleSession.start(*mirror_pair(), seed=seed)
        result = run_match(session, _rand_pair(seed))
        assert result.done
        winners.append(result.winner)
    assert any(w is not None for w in winners), "50 局全是平局？"


def test_scripted_exhausts_to_recharge() -> None:
    session = BattleSession.start(*duel(), seed=2, rules=RULES_1V1)
    players = {
        "a": ScriptedPlayer("a", script=[Decision(skill_action(0)), Decision(skill_action(0))]),
        "b": ScriptedPlayer("b", script=[Decision(skill_action(0))]),
    }
    result = run_match(session, players)
    assert any(t.decision_a.action == recharge_action() for t in result.turns)


def test_illegal_submit_no_change() -> None:
    session = BattleSession.start(*mirror_pair(), seed=1)
    turn_before = session.state.turn
    h_before = session.state.state_hash()
    r = session.submit("a", Decision({"type": "teleport"}))
    assert r["ok"] is False and "未知 action type" in r["error"]
    assert session.pending_sides() == []
    assert session.state.turn == turn_before
    assert session.state.state_hash() == h_before


def test_resolve_without_both_rejects() -> None:
    session = BattleSession.start(*mirror_pair(), seed=1)
    session.submit("a", Decision(recharge_action()))
    h_before = session.state.state_hash()
    r = session.resolve()
    assert r["ok"] is False and "未齐备" in r["error"]
    assert session.state.state_hash() == h_before
    assert session.pending_sides() == ["a"]


def test_battle_cli_smoke(monkeypatch, capsys) -> None:
    import environment.__main__ as main_mod
    monkeypatch.setattr(sys, "argv", ["environment", "battle", "--seed", "20260823", "--quiet"])
    assert main_mod.main() == 0
    out = capsys.readouterr().out
    assert "winner=" in out and "digest=" in out


# ── 交互式补位（判断 8：阵亡后由玩家决定换哪只）──────────────────────────────
def _ko_setup(seed: int = 7, lives: int = 2):
    """a 先手一回合 KO b 在场，b 有替补 → resolve 需要补位。"""
    from environment.models import new_battle
    from dataclasses import replace
    from rosters import RULES_1V1, spec
    a = [spec("甲", 500, 100, 100, 100, 100, 100, ["抓挠1"]),
         spec("甲2", 500, 100, 100, 100, 100, 100, ["抓挠1"]),
         spec("甲3", 500, 100, 100, 100, 100, 100, ["抓挠1"])]
    b = [spec("乙1", 30, 1, 1, 1, 1, 50, ["撞击"]),
         spec("乙2", 300, 1, 1, 1, 1, 50, ["撞击"]),
         spec("乙3", 300, 1, 1, 1, 1, 50, ["撞击"])]
    return new_battle(a, b, seed=seed, rules=replace(RULES_1V1, team_size=3, lives=lives))


def _ko_session(seed: int = 7, lives: int = 2):
    session = BattleSession(_ko_setup(seed, lives))
    session.submit("a", Decision(skill_action(0)))
    session.submit("b", Decision(skill_action(0)))
    return session


def test_resolve_pauses_for_replacement() -> None:
    """阵亡且有存活后备 → resolve 暂停返回 need_replacement，回合未推进、阵亡未处理。"""
    session = _ko_session()
    res = session.resolve()
    assert res["ok"] and res["need_replacement"] == "b"
    assert session.state.turn == 1
    assert session.state.active("b").fainted   # 等补位，还没换
    assert session.replacement_options("b") == [1, 2]


def test_submit_replacement_completes_turn() -> None:
    """玩家补位后：replace 事件 + 回合推进；无 battle_end（对局未结束）。"""
    session = _ko_session()
    res = session.resolve()
    assert res["need_replacement"] == "b"
    r = session.submit_replacement("b", 1)
    assert r["ok"] and r["need_replacement"] is None
    assert session.state.active("b").name == "乙2"
    assert session.state.turn == 2
    assert [e["type"] for e in r["events"]] == ["replace"]


def test_replacement_validation() -> None:
    """补位非法一律拒绝且不消耗回合：不是等待方 / 自己 / 已倒下 / 越界 / 错类型。"""
    from environment.actions import validate_replacement
    session = _ko_session()
    session.resolve()
    # 不是等待补位的一方
    assert session.submit_replacement("a", 1)["ok"] is False
    # 现在等 b：校验谓词逐条拒
    assert validate_replacement(session.state, "b", 0) is not None      # 自己（当前在场）
    assert validate_replacement(session.state, "b", 9) is not None      # 越界
    assert validate_replacement(session.state, "b", True) is not None   # bool 错类型
    ss = session.state.side("b")
    ss.units[2].fainted = True
    assert validate_replacement(session.state, "b", 2) is not None      # 已倒下
    # 非法补位后回合未推进、state 未变
    h = session.state.state_hash()
    assert session.submit_replacement("b", 9)["ok"] is False
    assert session.state.turn == 1 and session.state.state_hash() == h


def test_game_over_at_faint_no_replacement() -> None:
    """无存活后备（1v1）→ 阵亡即终局，不询问补位。"""
    from rosters import strong_weak
    session = BattleSession.start(*strong_weak(), seed=5, rules=RULES_1V1)
    session.submit("a", Decision(skill_action(0)))
    session.submit("b", Decision(skill_action(0)))
    res = session.resolve()
    assert res["ok"] and res["need_replacement"] is None
    assert res["done"] and res["winner"] == "a"
    assert [e["type"] for e in res["events"]][-1] == "battle_end"


def test_run_match_applies_player_replacement() -> None:
    """run_match 驱动补位：非终局阵亡 → 玩家 choose_replacement → replace 事件。"""
    from environment.players import ScriptedPlayer
    a = [spec("甲", 500, 100, 100, 100, 100, 100, ["抓挠1"]),
         spec("甲2", 500, 100, 100, 100, 100, 100, ["抓挠1"]),
         spec("甲3", 500, 100, 100, 100, 100, 100, ["抓挠1"])]
    b = [spec("乙1", 30, 1, 1, 1, 1, 50, ["撞击"]),
         spec("乙2", 30, 1, 1, 1, 1, 50, ["撞击"]),
         spec("乙3", 30, 1, 1, 1, 1, 50, ["撞击"])]
    session = BattleSession.start(a, b, seed=3, rules=replace(RULES_1V1, team_size=3, lives=2))
    players = {
        "a": ScriptedPlayer("a", script=[Decision(skill_action(0))] * 6),
        "b": ScriptedPlayer("b", script=[Decision(recharge_action())] * 10),
    }
    result = run_match(session, players)
    all_types = [e["type"] for t in result.turns for e in t.events]
    assert "replace" in all_types      # 中间阵亡经过玩家补位
    assert all_types.count("faint") == 2
    assert result.winner == "a" and result.done
