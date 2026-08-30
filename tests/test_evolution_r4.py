"""R4 池与门禁：PlaybookPool + promotion_gate + run_steps 冒烟。"""

from __future__ import annotations

from rock_pvp_agent.battle.evolution.bench import build_instances
from rock_pvp_agent.battle.evolution.league import promotion_gate
from rock_pvp_agent.battle.evolution.playbook import Playbook
from rock_pvp_agent.battle.evolution.pool import PlaybookPool
from rock_pvp_agent.battle.evolution.run import run_steps


def _pool():
    return PlaybookPool(["inst_a", "inst_b"])


def test_pool_add_enters_and_sets_champion():
    pool = _pool()
    pb = Playbook.initial()
    out = pool.add(pb, {"inst_a": 0.6, "inst_b": 0.5})
    assert out["entered"] is True
    assert pool.champion().playbook.version == pb.version      # 首个成员即 Champion


def test_pool_add_rejects_no_best_on_instance():
    pool = _pool()
    Playbook.initial()
    # 已有成员在实例上更强 → 新成员未在任何实例池内最优 → 拒入池
    p1 = Playbook.initial()
    pool.add(p1, {"inst_a": 0.9, "inst_b": 0.9})
    p2 = Playbook(version="pb_v1", modules=p1.modules)
    out = pool.add(p2, {"inst_a": 0.5, "inst_b": 0.5})
    assert out["entered"] is False


def test_promotion_gate_regression_floor():
    ok, rows = promotion_gate(Playbook.initial(), [], lambda c, h: 0.5)
    assert ok is True and rows == []                           # 空历史 → 空真通过
    hist = [Playbook.initial()]
    ok2, rows2 = promotion_gate(Playbook.initial(), hist, lambda c, h: 0.4)
    assert ok2 is False                                        # 胜率 < 0.45 → 拒绝


def test_run_steps_smoke(tmp_path):
    insts = build_instances("d_sel")[:2]
    out = run_steps(n=1, seed=7, instances=insts, seeds_per_instance=1,
                    minibatch_seeds=1, M=4, out_dir=str(tmp_path))
    assert out["n"] == 1
    assert out["final"]["champion"] is not None
    assert len(out["steps"]) == 1
