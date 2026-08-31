"""M2 trajectory：归一化 + 聚合 + 版本闸/重放闸 + 分开统计 的确定性测试。

自博弈记录用 `run_selfplay` 现场生成（不依赖 gitignored 的 runs/ 样例）；人机记录手工构造
TeamPick 形态 dict。聚合用直接构造的 `TrajectoryEvidence`。
"""

from __future__ import annotations

import json
from dataclasses import asdict, fields

import pytest

from environment.datafingerprint import data_digest as current_data_digest
from environment.datafingerprint import rules_digest as current_rules_digest
from environment.presets import p1_preset
from environment.rules import DEFAULT_RULES
from rock_pvp_agent.advisor.trajectory import (
    TrajectoryEvidence,
    _wilson,
    aggregate,
    discover,
    normalize,
    query_trajectory_evidence,
    team_key,
)
from rock_pvp_agent.battle.selfplay import run_selfplay
from rock_pvp_agent.battle.store import TrajectoryStore


def _rules_dict() -> dict:
    return {f.name: getattr(DEFAULT_RULES, f.name) for f in fields(DEFAULT_RULES)}


def _human_record(*, battle_id="b1", team_a=None, team_b=None, winner="a",
                  data_digest="unknown", rules_digest="unknown") -> dict:
    return {
        "version": 1, "battle_id": battle_id, "saved_at": "t", "seed": 1,
        "opponent": "fake_llm", "rules": _rules_dict(),
        "data_digest": data_digest, "rules_digest": rules_digest,
        "team_a": team_a or [{"spirit": "迪莫", "skills": ["闪光"], "bloodline": "",
                              "nature": "坦率", "iv": {}}],
        "team_b": team_b or [{"spirit": "喵喵", "skills": ["抓挠"], "bloodline": "",
                              "nature": "坦率", "iv": {}}],
        "winner": winner, "done": True, "turns": [],
    }


def _ev(*, kind="selfplay", team="teamA", opp="teamB", winner="a", replay_ok=True,
        dd="d_0123456789abcdef", rd="rules_x", seed=1) -> TrajectoryEvidence:
    return TrajectoryEvidence(
        kind=kind, evidence_id=f"{kind}:{dd[2:10]}:{seed}", battle_id=str(seed),
        team_key=team, opponent_key=opp, winner_side=winner,
        winner_key=(team if winner == "a" else opp if winner == "b" else None),
        rules_digest=rd, data_digest=dd, replay_ok=replay_ok, sample_seed=seed,
        team_names=("迪莫",),
    )


# ── 构筑指纹 ──

def test_team_key_unifies_pick_and_roster_shapes():
    pick_team = [{"spirit": "迪莫", "skills": ["闪光"], "bloodline": "", "nature": "坦率", "iv": {}}]
    roster_team = [{"name": "迪莫", "skills": ["闪光"], "bloodline": "", "nature": "坦率", "iv": {}}]
    assert team_key(pick_team) == team_key(roster_team)
    assert len(team_key(pick_team)) == 16


# ── 发现 ──

def test_discover_empty_and_bad_kind(tmp_path):
    assert discover("human", battles_dir=tmp_path) == []
    assert discover("selfplay", runs_dir=tmp_path) == []
    with pytest.raises(ValueError, match="未知 kind"):
        discover("bogus")


def test_discover_human_missing_dir(tmp_path):
    assert discover("human", battles_dir=tmp_path / "nope") == []


def test_discover_human_skips_bad_files(tmp_path):
    (tmp_path / "good.json").write_text(json.dumps(_human_record(), ensure_ascii=False),
                                        encoding="utf-8")
    (tmp_path / "bad.json").write_text("{broken", encoding="utf-8")
    (tmp_path / "notdict.json").write_text("[1,2,3]", encoding="utf-8")
    records = discover("human", battles_dir=tmp_path)
    assert [r["battle_id"] for r in records] == ["b1"]


# ── 归一化（selfplay：真实重放）──

def test_normalize_selfplay(tmp_path):
    run_selfplay(seed=7, out_dir=tmp_path, battle_id="sp-7", saved_at="t")
    records = discover("selfplay", runs_dir=tmp_path)
    assert len(records) == 1
    ev = normalize(records[0], "selfplay")
    assert ev.kind == "selfplay" and ev.battle_id == "sp-7"
    assert ev.replay_ok is True
    assert ev.data_digest.startswith("d_") and ev.rules_digest.startswith("rules_")
    assert not ev.team_key.startswith("unknown:")
    assert not ev.opponent_key.startswith("unknown:")
    assert ev.evidence_id == f"selfplay:{ev.data_digest[2:10]}:sp-7"
    assert ev.team_names and len(ev.team_names) == 3
    assert ev.winner_side in ("a", "b") and ev.winner_key


# ── 归一化（human：TeamPick 形态）──

def test_normalize_human_team_pick():
    record = _human_record()
    ev = normalize(record, "human")
    assert ev.kind == "human"
    assert ev.data_digest == "unknown" and ev.rules_digest == "unknown"
    assert ev.evidence_id == "human:unknown:b1"
    assert ev.team_key == team_key(record["team_a"])
    assert ev.team_names == ("迪莫",)
    assert ev.winner_side == "a" and ev.winner_key == ev.team_key


def test_normalize_spirit_none_unknown():
    record = _human_record(team_a=[{"spirit": None, "skills": [], "bloodline": "",
                                    "nature": "坦率", "iv": {}}])
    ev = normalize(record, "human")
    assert ev.team_key == "unknown:b1"
    assert ev.team_names == ()
    assert ev.replay_ok is False  # build_roster(spirit=None) 抛错 → 重放闸关闭


def test_normalize_winner_b_and_none():
    ev_b = normalize(_human_record(winner="b"), "human")
    assert ev_b.winner_side == "b" and ev_b.winner_key == ev_b.opponent_key
    ev_none = normalize(_human_record(winner=None), "human")
    assert ev_none.winner_side is None and ev_none.winner_key is None


def test_normalize_human_replay_ok():
    """human TeamPick→roster→replay 完整路径：3 只合法精灵（空 turns）→ replay_ok=True。"""
    picks_a, picks_b = p1_preset(3)
    record = _human_record(
        team_a=[asdict(p) for p in picks_a],
        team_b=[asdict(p) for p in picks_b],
        data_digest=current_data_digest(), rules_digest=current_rules_digest(),
    )
    ev = normalize(record, "human")
    assert ev.replay_ok is True


# ── 重放闸 ──

def test_replay_gate_corrupted(tmp_path):
    out = run_selfplay(seed=7, out_dir=tmp_path, battle_id="sp-7", saved_at="t")
    rec = out["record"]
    rec["turns"][0]["state_hash"] = "corrupted"
    assert normalize(rec, "selfplay").replay_ok is False


# ── 聚合 ──

def test_aggregate_win_rate_and_ci():
    evs = [_ev(seed=1, winner="a"), _ev(seed=2, winner="a"),
           _ev(seed=3, winner="a"), _ev(seed=4, winner="b")]
    bucket = aggregate(evs)["selfplay"]
    assert bucket["total_games"] == 4
    t = bucket["by_team"]["teamA"]
    assert t["games"] == 4 and t["wins"] == 3
    assert t["win_rate"] == pytest.approx(0.75)
    assert 0.0 <= t["ci95_low"] <= t["win_rate"] <= t["ci95_high"] <= 1.0
    assert t["opponents"] == {"teamB": 4}
    assert len(t["evidence_ids"]) == 4
    assert bucket["replay_ok_rate"] == 1.0
    assert bucket["digest_unknown_count"] == 0


def test_aggregate_separates_kinds():
    out = aggregate([_ev(kind="human", seed=1), _ev(kind="selfplay", seed=2)])
    assert set(out.keys()) == {"human", "selfplay"}
    assert out["human"]["by_team"]["teamA"]["games"] == 1
    assert out["selfplay"]["by_team"]["teamA"]["games"] == 1


def test_aggregate_unknown_team_not_in_by_team():
    ev = _ev(seed=1)
    unknown = TrajectoryEvidence(
        kind="selfplay", evidence_id="selfplay:x:2", battle_id="2", team_key="unknown:2",
        opponent_key="teamB", winner_side=None, winner_key=None, rules_digest="rules_x",
        data_digest="d_0123456789abcdef", replay_ok=True, sample_seed=2, team_names=())
    bucket = aggregate([ev, unknown])["selfplay"]
    assert bucket["total_games"] == 2               # 不可用样本仍计数
    assert "unknown:2" not in bucket["by_team"]     # 但不进胜率


def test_wilson_edges():
    assert _wilson(0, 0) == (None, None)
    lo, hi = _wilson(0, 4)
    assert 0.0 <= lo <= hi <= 1.0
    lo, hi = _wilson(4, 4)
    assert hi == 1.0 and lo >= 0.0


# ── 查询：版本闸 + team_filter + min_games ──

def test_query_bad_kind(tmp_path):
    with pytest.raises(ValueError, match="未知 kind"):
        query_trajectory_evidence("bogus")


def test_query_returns_hard_evidence(tmp_path):
    run_selfplay(seed=7, out_dir=tmp_path, battle_id="sp-7", saved_at="t")
    r = query_trajectory_evidence("selfplay", runs_dir=tmp_path, min_games=1)
    assert r["kind"] == "selfplay"
    assert r["total_games"] == 1
    assert len(r["by_team"]) == 1
    t = next(iter(r["by_team"].values()))
    assert t["games"] == 1 and t["win_rate"] in (0.0, 1.0)
    assert t["evidence_ids"]
    assert r["replay_ok_rate"] == 1.0
    assert r["digest_unknown_count"] == 0
    assert r["version_mismatch_count"] == 0


def test_query_version_gate_wrong_digest(tmp_path):
    run_selfplay(seed=7, out_dir=tmp_path, battle_id="sp-7", saved_at="t")
    r = query_trajectory_evidence("selfplay", runs_dir=tmp_path,
                                  data_digest="d_deadbeefdeadbeef", min_games=1)
    assert r["total_games"] == 0
    assert r["version_mismatch_count"] == 1


def test_query_excludes_unknown_digest(tmp_path):
    out = run_selfplay(seed=7, saved_at="t")  # 不落盘，只取 record
    rec = out["record"]
    rec.pop("data_digest", None)
    rec.pop("rules_digest", None)
    TrajectoryStore(tmp_path).save(rec)       # 写一条「旧记录」（无 stamp）
    r = query_trajectory_evidence("selfplay", runs_dir=tmp_path, min_games=1)
    assert r["total_games"] == 0              # unknown digest 排除出硬证据
    assert r["digest_unknown_count"] == 1
    assert r["version_mismatch_count"] == 1


def test_query_team_filter_and_min_games(tmp_path):
    out = run_selfplay(seed=7, out_dir=tmp_path, battle_id="sp-7", saved_at="t")
    run_selfplay(seed=8, out_dir=tmp_path, battle_id="sp-8", saved_at="t")
    target = out["record"]["team_a"][0]["name"]
    r = query_trajectory_evidence("selfplay", runs_dir=tmp_path,
                                  team_filter=[target], min_games=1)
    assert r["total_games"] == 2              # 两条固定阵容都含 target

    r2 = query_trajectory_evidence("selfplay", runs_dir=tmp_path,
                                   team_filter=["不存在的精灵"], min_games=1)
    assert r2["total_games"] == 0

    r3 = query_trajectory_evidence("selfplay", runs_dir=tmp_path, min_games=3)
    assert r3["by_team"] == {}                # 样本不足 3 被滤掉


# ── stamp 回归（S0.③ 已交付，钉住不退化）──

def test_selfplay_record_has_digest_stamp(tmp_path):
    out = run_selfplay(seed=7, out_dir=tmp_path, battle_id="sp-7", saved_at="t")
    assert out["record"]["data_digest"] == current_data_digest()
    assert out["record"]["rules_digest"] == current_rules_digest()
