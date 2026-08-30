"""R5 慢更新与收敛：MetaPlaybook + ValueFn + run_epochs 冒烟。"""

from __future__ import annotations

from rock_pvp_agent.battle.evolution.bench import build_instances
from rock_pvp_agent.battle.evolution.meta import MetaPlaybook
from rock_pvp_agent.battle.evolution.run import run_epochs
from rock_pvp_agent.battle.evolution.valuefn import v_provider


def test_meta_playbook_empty():
    assert MetaPlaybook().render() == ""


def test_meta_playbook_observes_and_persistent_failures():
    mp = MetaPlaybook()
    mp.observe_step({
        "reports": [{"op": "append", "module_key": "M2 action_selector", "status": "applied"}],
        "reflection_diagnostics": [{"analyst": "failure", "status": "ok"}],
    })
    assert "编辑接受率" in mp.render()
    mp.mark_persistent_failure("某规则", epochs=1)
    assert mp.persistent_failures() == ()           # 单次被拒是噪声
    mp.mark_persistent_failure("某规则", epochs=1)
    assert mp.persistent_failures() == ("某规则",)  # 连续 ≥2 epoch 才返回


def test_value_fn_provider_degrades_without_data():
    assert v_provider([]) is None
    assert v_provider(None) is None


def test_run_epochs_smoke(tmp_path):
    insts = build_instances("d_sel")[:2]
    dtest = build_instances("d_test")[:1]
    out = run_epochs(n=1, seed=7, E=2, instances=insts, dtest_instances=dtest,
                     seeds_per_instance=1, minibatch_seeds=1, M=4,
                     out_dir=str(tmp_path))
    assert out["n"] == 1
    assert len(out["epochs"]) == 1
