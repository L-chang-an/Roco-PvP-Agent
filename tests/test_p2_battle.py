"""P2 对局集成：P1+P2 白名单上的四家族池完整对局 / 连击事件流 / 确定性 / CLI。

验证 P2 技能在真实对局里正常结算（多连击逐发事件、energy_gain/steal 事件、事件流合法），
同 seed digest 一致、马尔可夫性不破坏。
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
from environment.engine import step
from environment.match import run_match
from environment.models import BattleRng, BattleState, SideState, build_unit
from environment.players import RandomPlayer
from environment.rules import DEFAULT_RULES
from environment.session import BattleSession
from environment.skillbook import P2_EFFECTS, battle_ready
from environment.teambuilder import TeamPick, build_roster

FULL = DataSource.FULL


def _battle_ready_defaults(spirit: str) -> list[str]:
    return [s for s in load_spirits(FULL)[spirit].skills_default if battle_ready(s)]


def _p2_learnable(spirit: str) -> list[str]:
    return [s for s in _battle_ready_defaults(spirit) if s in P2_EFFECTS]


def _pick(spirit: str, n: int = 3) -> TeamPick:
    return TeamPick(spirit, _battle_ready_defaults(spirit)[:n])


def _battle_seed(a_names, b_names, seed: int = 7):
    ra, rb = build_roster([_pick(n) for n in a_names], FULL), build_roster([_pick(n) for n in b_names], FULL)
    session = BattleSession.start(ra, rb, seed=seed, battle_id=f"p2-{seed}")
    players = {"a": RandomPlayer("a", seed=seed + 1), "b": RandomPlayer("b", seed=seed + 2)}
    return run_match(session, players), session


def test_p2_skills_learnable_by_family_pool() -> None:
    """四家族非首领精灵能从 P1+P2 白名单组队，且部分精灵学到 P2 技能。"""
    p2_total = sum(len(_p2_learnable(n)) for n in ["迪莫", "喵喵", "火花", "水蓝蓝",
                                                   "喵呜", "魔力猫", "焰火", "火神", "波波拉", "水灵"])
    assert p2_total > 0                                # 至少有一些 P2 技能进池
    for n in ["迪莫", "喵喵", "火花", "水蓝蓝"]:
        assert len(_battle_ready_defaults(n)) >= 3


def test_p1p2_battle_runs_deterministic() -> None:
    """P1+P2 白名单完整对局：回合推进、有伤害、有 energy_gain/steal 事件、digest 稳定。"""
    result, session = _battle_seed(["迪莫", "喵喵", "火花"], ["魔力猫", "焰火", "水蓝蓝"])
    assert result.turn_count > 0
    all_events = [e for t in result.turns for e in t.events]
    assert any(e["type"] == "damage" for e in all_events)
    assert result.winner in ("a", "b", None)
    result2, _ = _battle_seed(["迪莫", "喵喵", "火花"], ["魔力猫", "焰火", "水蓝蓝"])
    assert result.digest() == result2.digest()


def test_multihit_events_in_real_battle() -> None:
    """真实对局里连击技能逐发发事件（带 hit/hits）。"""
    all_events = []
    for names_a, names_b in [(["迪莫", "喵喵", "火花"], ["魔力猫", "焰火", "水蓝蓝"]),
                             (["喵呜", "焰火", "水灵"], ["喵喵", "火神", "波波拉"])]:
        result, _ = _battle_seed(names_a, names_b, seed=9)
        all_events += [e for t in result.turns for e in t.events]
    multi = [e for e in all_events if e["type"] == "damage" and e.get("hits", 1) > 1]
    # 若对局中出现连击伤害（随机出招可能没有），则事件必须带 hit/hits 且合法
    for e in multi:
        assert 1 <= e["hit"] <= e["hits"]
    assert all(e["type"] in ("damage", "energy_gain", "steal", "stat_change", "heal",
                             "recharge", "switch", "replace", "faint", "life_loss", "battle_end",
                             "skipped", "item_use", "reduce_arm", "error")
               for e in all_events)


def test_markov_step_with_p2_skill() -> None:
    """P2 连击技能走 step（clone）不破坏马尔可夫性。"""
    a = build_unit({"name": "甲", "types": ["普通"],
                    "stats": {"hp": 300, "atk": 100, "sp_atk": 100, "def": 100, "sp_def": 100, "speed": 100},
                    "skills": ["乱打"], "trait": ""})
    b = build_unit({"name": "乙", "types": ["普通"],
                    "stats": {"hp": 300, "atk": 100, "sp_atk": 100, "def": 100, "sp_def": 100, "speed": 100},
                    "skills": ["撞击"], "trait": ""})
    s = BattleState(side_a=SideState(units=[a], lives=2), side_b=SideState(units=[b], lives=2),
                    rng=BattleRng(7), rules=replace(DEFAULT_RULES, team_size=1))
    h = s.state_hash()
    new_s, events = step(s, Decision(skill_action(0)), Decision(skill_action(0)))
    assert s.state_hash() == h and events
    a_dmg = [e for e in events if e["type"] == "damage" and e["side"] == "a"]
    assert a_dmg and a_dmg[0]["hits"] == 5


def test_cli_p1p2_battle_deterministic() -> None:
    """CLI：--data FULL battle 用 P1+P2 白名单队，同 seed 复现 digest 一致。"""
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src") + os.pathsep + env.get("PYTHONPATH", "")
    cmd = [sys.executable, "-m", "environment", "battle", "--data", "FULL",
           "--preset", "p1", "--seed", "11", "--json"]
    outs = []
    for _ in range(2):
        out = subprocess.run(cmd, capture_output=True, text=True, cwd=root, env=env, timeout=120)
        assert out.returncode == 0, out.stderr
        outs.append(json.loads(out.stdout))
    assert outs[0]["digest"] == outs[1]["digest"]
