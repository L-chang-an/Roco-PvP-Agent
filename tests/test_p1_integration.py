"""E0~E2 全栈集成验证：batch-P1 技能池 + 四家族（迪莫/喵喵/火花/水蓝蓝）精灵池。

验证三件事在真实数据上成立：
- **E0a 组队**：四家族池可组合法 P1 队（家族唯一 / 首领禁入 / 血脉系别 / battle_ready）；
- **E0b 引擎**：P1 队能真正开局打完整局（伤害 / 阵亡补位 / 确定性 / 马尔可夫）；
- **E2 克制/STAB**：damage 事件带 eff/stab（含 STAB_MULT=1.25、抵抗 0.5）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from environment.actions import Decision, recharge_action, skill_action
from environment.dataset import DataSource, load_spirits
from environment.engine import execute_turn, step
from environment.match import run_match
from environment.models import BattleRng, BattleState, SideState, build_unit
from environment.players import RandomPlayer
from environment.rules import DEFAULT_RULES
from environment.session import BattleSession
from environment.skillbook import battle_ready
from environment.teambuilder import TeamPick, build_roster, validate_team

FULL = DataSource.FULL
_ANCHORS = {"迪莫", "喵喵", "火花", "水蓝蓝"}


def _family_pool() -> dict[str, list[str]]:
    """四家族全部成员：family_key → 成员名。"""
    sp = load_spirits(FULL)
    keys = {sp[a].family_key for a in _ANCHORS}
    out: dict[str, list[str]] = {}
    for n, s in sp.items():
        if s.family_key in keys:
            out.setdefault(s.family_key, []).append(n)
    return out


def _non_boss() -> list[str]:
    sp = load_spirits(FULL)
    return [n for n, s in sp.items() if s.family_key in {sp[a].family_key for a in _ANCHORS}
            and not s.is_boss]


def _p1_learnable(spirit: str) -> list[str]:
    return [s for s in load_spirits(FULL)[spirit].skills_default if battle_ready(s)]


def _p1_pick(spirit: str, n: int = 3) -> TeamPick:
    return TeamPick(spirit, _p1_learnable(spirit)[:n])


def _build_p1_team(names: list[str]) -> list[dict]:
    return build_roster([_p1_pick(n) for n in names], source=FULL)


# ── 池形状 ──
def test_four_family_pool_shape() -> None:
    """四家族共 18 只：非首领 10 只可入队，首领 8 只被规则 3 排除。"""
    pool = _family_pool()
    assert len(pool) == 4 and sum(len(v) for v in pool.values()) == 18
    sp = load_spirits(FULL)
    non_boss, boss = [], []
    for names in pool.values():
        for n in names:
            (boss if sp[n].is_boss else non_boss).append(n)
    assert len(non_boss) == 10 and len(boss) == 8
    for n in non_boss:
        assert len(_p1_learnable(n)) >= 3, f"{n} P1 可学不足 3 个"   # 能组 3 技能队


# ── E0a 组队校验 ──
def test_legal_p1_team_passes_validation() -> None:
    picks = [_p1_pick("迪莫"), _p1_pick("喵喵"), _p1_pick("火花")]     # 三个不同家族
    assert validate_team(picks, items=[], source=FULL) == []
    roster = build_roster(picks, source=FULL)
    units = [build_unit(r) for r in roster]                           # 引擎能建起来
    assert [u.name for u in units] == ["迪莫", "喵喵", "火花"]


def test_family_uniqueness_rejects_same_family() -> None:
    errors = validate_team([_p1_pick("喵喵"), _p1_pick("魔力猫"), _p1_pick("火花")],
                           items=[], source=FULL)
    assert any("同一家族只能入队一只" in e for e in errors)


def test_boss_rejected() -> None:
    errors = validate_team([TeamPick("圣光迪莫", ["闪光"]), _p1_pick("喵喵"), _p1_pick("火花")],
                           items=[], source=FULL)
    assert any("首领形态不可入队" in e for e in errors)


def test_bloodline_skill_type_mismatch_rejected() -> None:
    # 折线冲击（光系血脉技）配火血脉 → 规则 2 拒绝
    errors = validate_team([TeamPick("迪莫", ["折线冲击"], bloodline="火"),
                            _p1_pick("喵喵"), _p1_pick("火花")], items=[], source=FULL)
    assert any("与所选血脉" in e for e in errors)


def test_build_unit_rejects_non_battle_ready() -> None:
    """借用（效果未实现）→ build_unit 拒绝（清晰文案）。"""
    import pytest
    with pytest.raises(ValueError, match="可对战白名单"):
        build_unit({"name": "火花", "types": ["火"],
                    "stats": {"hp": 300, "atk": 100, "sp_atk": 100, "def": 100,
                              "sp_def": 100, "speed": 100},
                    "skills": ["借用"]})


# ── E0b 引擎：完整对局 ──
def _battle_seed(a_names: list[str], b_names: list[str], seed: int = 7):
    ra, rb = _build_p1_team(a_names), _build_p1_team(b_names)
    session = BattleSession.start(ra, rb, seed=seed, battle_id=f"p1-{seed}")
    players = {"a": RandomPlayer("a", seed=seed + 1), "b": RandomPlayer("b", seed=seed + 2)}
    return run_match(session, players), session


def test_full_battle_runs_on_p1_pool() -> None:
    """四家族 P1 队能打完整局：回合推进、有伤害、有胜负、digest 稳定。"""
    result, session = _battle_seed(["迪莫", "喵喵", "火花"], ["魔力猫", "焰火", "水蓝蓝"])
    assert result.turn_count > 0
    assert any(e["type"] == "damage" for t in result.turns for e in t.events)
    assert result.winner in ("a", "b", None)
    # 同参数再来一局 → digest 逐字节一致（确定性）
    result2, _ = _battle_seed(["迪莫", "喵喵", "火花"], ["魔力猫", "焰火", "水蓝蓝"])
    assert result.digest() == result2.digest()


def test_markov_step_does_not_mutate_p1_state() -> None:
    s = BattleState(
        side_a=SideState(units=[build_unit({"name": "火花", "types": ["火"],
                                            "stats": {"hp": 300, "atk": 100, "sp_atk": 100,
                                                      "def": 100, "sp_def": 100, "speed": 100},
                                            "skills": ["火焰切割"]})], lives=2),
        side_b=SideState(units=[build_unit({"name": "喵喵", "types": ["草"],
                                            "stats": {"hp": 300, "atk": 100, "sp_atk": 100,
                                                      "def": 100, "sp_def": 100, "speed": 100},
                                            "skills": ["叶绿光束"]})], lives=2),
        rng=BattleRng(7), rules=replace(DEFAULT_RULES, team_size=1),
    )
    h = s.state_hash()
    new_s, events = step(s, Decision(skill_action(0)), Decision(skill_action(0)))
    assert s.state_hash() == h and events and new_s is not s


# ── E2 克制 / STAB ──
def _unit_spec(name: str, types: list[str], skills: list[str]) -> dict:
    return {"name": name, "types": types,
            "stats": {"hp": 300, "atk": 100, "sp_atk": 100, "def": 100, "sp_def": 100, "speed": 100},
            "skills": skills, "trait": ""}


def test_e2_eff_and_stab_in_p1_battle() -> None:
    """火花(火)用火焰切割(火)打 喵喵(草)：eff 2.0 + stab 1.25；反向 草打火：eff 0.5。"""
    s = BattleState(
        side_a=SideState(units=[build_unit(_unit_spec("火花", ["火"], ["火焰切割"]))], lives=2),
        side_b=SideState(units=[build_unit(_unit_spec("喵喵", ["草"], ["叶绿光束"]))], lives=2),
        rng=BattleRng(7), rules=replace(DEFAULT_RULES, team_size=1),
    )
    events = execute_turn(s, Decision(skill_action(0)), Decision(skill_action(0)))
    dmg = {e["side"]: e for e in events if e["type"] == "damage"}
    assert dmg["a"]["eff"] == 2.0 and dmg["a"]["stab"] == 1.25   # 火克草 + 本系
    assert dmg["b"]["eff"] == 0.5 and dmg["b"]["stab"] == 1.25   # 草被火抗 + 本系


def test_p1_status_multi_stat_self() -> None:
    """丰饶：自己 物攻+魔攻 各 +14 层。"""
    a = build_unit(_unit_spec("迪莫", ["光"], ["丰饶"]))
    b = build_unit(_unit_spec("水蓝蓝", ["水"], ["拍击"]))
    s = BattleState(side_a=SideState(units=[a], lives=2),
                    side_b=SideState(units=[b], lives=2),
                    rng=BattleRng(7), rules=replace(DEFAULT_RULES, team_size=1))
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    sc = [e for e in events if e["type"] == "stat_change" and e["side"] == "a"]
    assert {e["stat"] for e in sc} == {"atk", "sp_atk"} and all(e["layers"] == 14 for e in sc)
    assert {m.stat: m.layers for m in a.stat_mods} == {"atk": 14, "sp_atk": 14}


def test_p1_status_foe_target() -> None:
    """雪球：对敌方 速度-9 层（flat 减益，target=foe）。"""
    a = build_unit(_unit_spec("喵喵", ["草"], ["雪球"]))
    b = build_unit(_unit_spec("火花", ["火"], ["火焰切割"]))
    s = BattleState(side_a=SideState(units=[a], lives=2),
                    side_b=SideState(units=[b], lives=2),
                    rng=BattleRng(7), rules=replace(DEFAULT_RULES, team_size=1))
    events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
    sc = [e for e in events if e["type"] == "stat_change" and e["side"] == "a"][0]
    assert sc["target"] == "foe" and sc["stat"] == "speed" and sc["layers"] == -9
    assert any(m.stat == "speed" and m.layers == -9 for m in b.stat_mods)


# ── CLI：--data FULL --preset p1 ──
def test_cli_p1_battle_deterministic() -> None:
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src") + os.pathsep + env.get("PYTHONPATH", "")
    cmd = [sys.executable, "-m", "environment", "battle", "--data", "FULL",
           "--preset", "p1", "--seed", "7", "--json"]
    outs = []
    for _ in range(2):
        out = subprocess.run(cmd, capture_output=True, text=True, cwd=root, env=env, timeout=120)
        assert out.returncode == 0, out.stderr
        outs.append(json.loads(out.stdout))
    assert outs[0]["digest"] == outs[1]["digest"]
    assert outs[0]["winner"] in ("a", "b", None)
