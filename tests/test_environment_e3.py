"""E3 集成：FULL 全部精灵 + valid_skills 白名单技能 + 管理员对局规则跑完整局。

验证：① 已实现特性（迪莫·最好的伙伴等）绑定真实特性、未实现特性的精灵装白板零效果；
② 管理员规则 6v6/5命 完整对局（回合/伤害/补位/胜负）；③ 确定性 + 马尔可夫。
"""

from __future__ import annotations

from dataclasses import replace

from environment.actions import Decision, recharge_action, skill_action
from environment.battle_config import build_battle_rules
from environment.dataset import DataSource, load_spirits
from environment.engine import execute_turn, step
from environment.match import run_match
from environment.models import BattleRng, BattleState, SideState, build_unit
from environment.players import RandomPlayer
from environment.presets import valid_spirit_candidates
from environment.session import BattleSession
from environment.skillbook import battle_ready
from environment.teambuilder import TeamPick, build_roster
from environment.traits import DEFAULT_TRAIT_NAME

VALID = DataSource.VALID


def _pick(spirit: str, n: int = 4) -> TeamPick:
    sp = load_spirits(VALID)[spirit]
    return TeamPick(spirit, [s for s in sp.skills_default if battle_ready(s)][:n])


def test_e3_6v6_5lives_full_battle() -> None:
    """管理员规则 6v6/5命：完整对局，有伤害、有胜负、digest 稳定。"""
    rules = build_battle_rules(team_size=6, lives=5)
    cands = valid_spirit_candidates()
    names_a, names_b = cands[:6], cands[6:12]          # 每队 6 个不同家族
    picks_a = [_pick(n) for n in names_a]
    picks_b = [_pick(n) for n in names_b]
    ra, rb = build_roster(picks_a, VALID, rules=rules), build_roster(picks_b, VALID, rules=rules)
    session = BattleSession.start(ra, rb, seed=7, rules=rules, battle_id="e3-7")
    players = {"a": RandomPlayer("a", seed=8), "b": RandomPlayer("b", seed=9)}
    result = run_match(session, players)
    assert result.turn_count > 0
    all_events = [e for t in result.turns for e in t.events]
    assert any(e["type"] == "damage" for e in all_events)
    assert result.winner in ("a", "b", None)
    assert session.state.side("a").lives <= 5 and session.state.side("b").lives <= 5
    # 同参数再来一局 → digest 一致
    session2 = BattleSession.start(ra, rb, seed=7, rules=rules, battle_id="e3-7")
    result2 = run_match(session2, {"a": RandomPlayer("a", seed=8), "b": RandomPlayer("b", seed=9)})
    assert result.digest() == result2.digest()


def test_e3_whiteboard_trait_spirit_fields() -> None:
    """已实现特性（迪莫·最好的伙伴/喵喵/火花）绑真实特性；未实现特性的精灵装白板、零效果。"""
    rules = build_battle_rules(team_size=3, lives=2)
    picks = [_pick("迪莫"), _pick("喵喵"), _pick("火花")]
    roster = build_roster(picks, VALID, rules=rules)
    units = {u.name: u for u in [build_unit(r) for r in roster]}
    assert units["迪莫"].trait.name == "最好的伙伴"     # 已实现 → 真实特性
    assert units["喵喵"].trait.name == "氧循环"
    assert units["火花"].trait.name == "助燃"
    # 未实现特性的精灵（茂盛 → 白板）：装白板、对战斗零影响
    blank = build_unit({"name": "白板测试", "types": ["草"],
                        "stats": {"hp": 300, "atk": 100, "sp_atk": 100,
                                  "def": 100, "sp_def": 100, "speed": 100},
                        "skills": ["抓挠"], "trait": "茂盛"})
    assert blank.trait.name == DEFAULT_TRAIT_NAME
    # 迪莫(光)用闪光打 喵喵(草)：eff 0.5 非克制 → 特性不触发 → 零增益
    s = BattleState(
        side_a=SideState(units=[units["迪莫"]], lives=2),
        side_b=SideState(units=[units["喵喵"]], lives=2),
        rng=BattleRng(7), rules=replace(rules, team_size=1),
    )
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    assert units["迪莫"].stat_mods == [] and units["迪莫"].energy_cost_mods == []
    assert any(e["type"] == "damage" for e in events)


def test_e3_markov_step() -> None:
    """E3 白名单队走 step（clone）不破坏马尔可夫性。"""
    rules = build_battle_rules(team_size=3, lives=2)
    a = build_unit({"name": "甲", "types": ["火"],
                    "stats": {"hp": 300, "atk": 100, "sp_atk": 100, "def": 100,
                              "sp_def": 100, "speed": 100},
                    "skills": ["火焰切割"], "trait": ""})
    b = build_unit({"name": "乙", "types": ["草"],
                    "stats": {"hp": 300, "atk": 100, "sp_atk": 100, "def": 100,
                              "sp_def": 100, "speed": 100},
                    "skills": ["叶绿光束"], "trait": ""})
    s = BattleState(side_a=SideState(units=[a], lives=2), side_b=SideState(units=[b], lives=2),
                    rng=BattleRng(7), rules=replace(rules, team_size=1))
    h = s.state_hash()
    new_s, events = step(s, Decision(skill_action(0)), Decision(skill_action(0)))
    assert s.state_hash() == h and events
    a_dmg = [e for e in events if e["type"] == "damage" and e["side"] == "a"]
    assert a_dmg and a_dmg[0]["eff"] == 2.0 and a_dmg[0]["stab"] == 1.25   # 火克草 + 本系


def test_e3_team_size_limits_on_cli() -> None:
    """CLI 管理员参数：非法规模（4/5/7）被 battle_config 拒绝（返回非 0）。"""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for bad_size in ("4", "5", "7"):
        out = subprocess.run(
            [sys.executable, "-m", "environment", "battle", "--team-size", bad_size],
            capture_output=True, text=True, cwd=root, timeout=60,
        )
        assert out.returncode != 0
        assert "3 或 6" in (out.stderr + out.stdout)
