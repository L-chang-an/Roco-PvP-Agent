"""轨迹重放（E6）：`environment/replay.py` 按 (rules, roster, seed, 逐回合提交序列) 重建
全新 session，逐回合比对 `state_hash`——马尔可夫不变式的可执行验证。

与 `test_selfplay.py` 的分工：这里测**引擎层重放函数**（用纯手写 roster，不加载真实数据），
`test_selfplay.py` 测编排 + 落盘 + 迷雾隔离。CLI 冒烟复用既有 subprocess 模式。
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from dataclasses import asdict, fields
from pathlib import Path

import pytest

from environment.actions import Decision, skill_action
from environment.match import run_match
from environment.players import ScriptedPlayer
from environment.replay import replay_record
from environment.rules import DEFAULT_RULES
from environment.session import BattleSession

from rosters import spec


def _rules_dict(r) -> dict:
    """BattleRules → 纯 dict（`BattleRules(**d)` 原样还原）。"""
    return {f.name: getattr(r, f.name) for f in fields(r)}


def _ko_record() -> dict:
    """2v2 手写阵容：b 首回合 KO a 在场 → 记录含 replace_a（重放必经补位路径）。

    ScriptedPlayer 脚本 [skill_action(0)] 耗尽后回落聚能，纯确定；battle_id 固定 "ko"。
    """
    rules = dataclasses.replace(DEFAULT_RULES, team_size=2)
    roster_a = [spec("弱甲", 1, 1, 1, 1, 1, 1, ["撞击"]),
                spec("弱乙", 500, 100, 100, 100, 100, 100, ["撞击"])]
    roster_b = [spec("强乙", 500, 100, 100, 100, 100, 100, ["抓挠1"]),
                spec("强丙", 500, 100, 100, 100, 100, 100, ["抓挠1"])]
    session = BattleSession.start(roster_a, roster_b, seed=1, rules=rules, battle_id="ko")
    players = {"a": ScriptedPlayer("a", script=[Decision(skill_action(0))]),
               "b": ScriptedPlayer("b", script=[Decision(skill_action(0))])}
    result = run_match(session, players)
    assert any(t.replace_a is not None or t.replace_b is not None for t in result.turns), \
        "夹具应触发补位（覆盖重放的 replace 路径）"
    return {
        "version": 1, "battle_id": "ko", "saved_at": "t", "seed": 1,
        "players": {"a": "scripted", "b": "scripted"},
        "rules": _rules_dict(rules), "team_a": roster_a, "team_b": roster_b,
        "winner": result.winner, "done": result.done,
        "turns": [{"turn": t.turn, "decision_a": asdict(t.decision_a),
                   "decision_b": asdict(t.decision_b),
                   "replace_a": t.replace_a, "replace_b": t.replace_b,
                   "state_hash": t.state_hash}
                  for t in result.turns],
    }


def test_replay_all_match_with_replacements() -> None:
    """含补位的完整记录 → 逐回合 hash 全一致（重放路径覆盖 replace_a）。"""
    rec = _ko_record()
    out = replay_record(rec)
    assert out["ok"] and out["all_match"] and out["battle_id"] == "ko"
    assert len(out["turns"]) == len(rec["turns"])
    assert all(t["match"] for t in out["turns"])
    assert any(rec["turns"][i]["replace_a"] is not None for i in range(len(rec["turns"])))


def test_replay_mismatch_on_corrupt_decision() -> None:
    """篡改一条提交 → 该回合起失配并停止（状态已偏离，后续无意义）。"""
    rec = _ko_record()
    # 脚本耗尽后第 2 回合 a 是聚能；改成合法技能（能量充足，可过校验）→ 状态必然偏离
    assert rec["turns"][1]["decision_a"]["action"]["type"] == "recharge"
    rec["turns"][1]["decision_a"] = {"action": skill_action(0), "item": ""}
    out = replay_record(rec)
    assert not out["all_match"]
    assert out["turns"][0]["match"] is True     # 篡改前的回合仍一致
    assert out["turns"][1]["match"] is False    # 篡改回合失配
    assert len(out["turns"]) == 2               # 首个失配即停


def test_replay_battle_id_in_state_hash() -> None:
    """`state_hash` 含 battle_id：重放必须回传记录里的 battle_id，否则全失配。"""
    rec = _ko_record()
    rec["battle_id"] = "other"
    out = replay_record(rec)
    assert not out["all_match"]
    assert out["turns"][0]["match"] is False


def test_replay_rejects_missing_keys() -> None:
    """缺必需键 → ValueError（引擎「宁失败不抛」改为显式校验）。"""
    rec = _ko_record()
    del rec["turns"]
    with pytest.raises(ValueError, match="turns"):
        replay_record(rec)


def test_replay_cli_smoke(tmp_path) -> None:
    """`python -m environment replay <path>`：逐回合 hash 一致，退出码 0（可进 CI）。"""
    path = tmp_path / "ko.json"
    path.write_text(json.dumps(_ko_record(), ensure_ascii=False), encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src") + os.pathsep + env.get("PYTHONPATH", "")
    out = subprocess.run([sys.executable, "-m", "environment", "replay", str(path)],
                         capture_output=True, text=True, cwd=root, env=env, timeout=120)
    assert out.returncode == 0, out.stderr
    assert "全部一致 ✅" in out.stdout and "expected=" in out.stdout
