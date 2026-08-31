"""R0 可观测度量 · evolution/{analysis,bench,league}：轨迹分析 + 配对评测 + Elo/α-rank。"""

from __future__ import annotations

from environment.players import RandomPlayer
from rock_pvp_agent.battle.evolution.analysis import analyze_record
from rock_pvp_agent.battle.evolution.bench import build_instances, paired_eval, wilson_ci
from rock_pvp_agent.battle.evolution.league import PayoffMatrix, alpha_rank, elo_update
from rock_pvp_agent.battle.selfplay import run_selfplay


# ---------- analysis ----------

def test_analyze_record_replays_selfplay():
    rec = run_selfplay(seed=7, team_size=3, lives=2)["record"]
    ta1 = analyze_record(rec)
    ta2 = analyze_record(rec)
    assert ta1.replay_ok and ta2.replay_ok
    assert ta1.turn_count == ta2.turn_count > 0
    assert ta1.digest() == ta2.digest()          # 确定性


# ---------- bench ----------

def test_wilson_ci_boundaries():
    lo, hi = wilson_ci(0, 10)
    assert lo == 0.0 and hi < 1.0                 # 0/n 不塌缩到 [0,0]
    lo2, hi2 = wilson_ci(10, 10)
    assert hi2 == 1.0 and lo2 > 0.0               # n/n 不塌缩到 [1,1]


def test_build_instances_counts():
    assert len(build_instances("d_sel")) == 60
    assert len(build_instances("d_test")) == 16


def test_paired_eval_random_vs_random_deterministic():
    insts = build_instances("d_sel")[:2]
    def mk(side, seed):
        return RandomPlayer(side, seed=seed)
    r1 = paired_eval(mk, mk, insts, seeds=2)
    r2 = paired_eval(mk, mk, insts, seeds=2)
    assert r1 == r2                                # 确定性
    assert 0.0 <= r1["winrate"] <= 1.0
    assert r1["n_games"] > 0                       # 镜像实例只跑 1 方向（去重），故不为 2×2×2


# ---------- league ----------

def test_elo_update_zero_sum():
    na, nb = elo_update(1500, 1500, 1.0)
    assert na > 1500 and nb < 1500
    assert abs((na - 1500) + (nb - 1500)) < 1e-9


def test_alpha_rank_dominance():
    M = [[0.5, 0.9], [0.1, 0.5]]                  # 策略 0 支配策略 1
    pi = alpha_rank(M)
    assert pi[0] > pi[1]


def test_alpha_rank_uniform_when_alpha_zero():
    M = [[0.5, 0.9, 0.1], [0.1, 0.5, 0.9], [0.9, 0.1, 0.5]]
    pi = alpha_rank(M, alpha=0)
    assert all(abs(p - 1 / 3) < 1e-6 for p in pi)


def test_payoff_matrix_roundtrip():
    m = PayoffMatrix()
    m.record("A", "B", 1.0)
    m.record("A", "B", 0.0)
    assert m.winrate("A", "B") == 0.5
    m2 = PayoffMatrix.from_dict(m.to_dict())
    assert m2.winrate("A", "B") == 0.5
