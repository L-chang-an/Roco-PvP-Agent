"""G5 GlobalMem 完整编排：全链路闭环 / Q 更新 / append-only / A/B / 确定性 / 开关。"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage

from roco_pvp_agent.battle.evolution.bench import build_instances
from roco_pvp_agent.battle.evolution.globalmem import GlobalMemStore
from roco_pvp_agent.battle.evolution.globalmem_run import measure_ab, run_battles
from roco_pvp_agent.battle.evolution.memory import MemoryStore
from roco_pvp_agent.config import Settings


def _insts(n=2):
    return build_instances("d_sel")[:n]


def _run(tmp_path, **kw):
    base = dict(n=2, seed=7, out_dir=str(tmp_path / "out"), settings=Settings(),
                instances=_insts(), globalmem_dir=str(tmp_path / "gm"),
                memory_dir=str(tmp_path / "mem"), fake_analyst=True)
    base.update(kw)
    return run_battles(**base)


# ---------- 全链路闭环 ----------

def test_full_chain_creates_then_loads_and_updates(tmp_path):
    """首局无可加载 → create；后续局命中 → update（走 supersede）。"""
    out = _run(tmp_path, n=4)
    rows = out["battles"]
    assert rows[0]["loaded"] == {"a": None, "b": None}          # 冷启动必然 miss
    assert all(v["action"] == "create" for v in rows[0]["globalmem"].values())
    later = [r for r in rows[1:] if any(r["loaded"].values())]
    assert later, "后续局应命中已沉淀的 GlobalMem"
    assert any(v["action"] == "update" for v in later[0]["globalmem"].values())


def test_both_stores_populated(tmp_path):
    out = _run(tmp_path, n=3)
    assert out["globalmem"]["active"] > 0
    assert out["memory"]["entries"] > 0
    assert GlobalMemStore(tmp_path / "gm").count() == out["globalmem"]["active"]
    assert MemoryStore(tmp_path / "mem").count() == out["memory"]["entries"]


def test_append_only_total_exceeds_active_after_updates(tmp_path):
    """update 走 supersede：总条目数 > active（旧文本留库可回滚）。"""
    out = _run(tmp_path, n=4)
    assert out["globalmem"]["total"] > out["globalmem"]["active"]


def test_q_updated_for_loaded_entry(tmp_path):
    """本局加载过的条目会被 Q 更新（n_used 增加）。"""
    out = _run(tmp_path, n=4)
    store = GlobalMemStore(tmp_path / "gm")
    used = [e for e in store.all() if e.get("n_used", 0) > 0]
    assert used, "至少应有一条被加载并更新 Q"
    assert any("q_after" in v for r in out["battles"] for v in r["globalmem"].values())


def test_local_memory_adoption_recorded(tmp_path):
    """局部记忆的采纳判定链路也跑通（offline 玩家带 _turn_log）。"""
    out = _run(tmp_path, n=3)
    adoptions = [r["memory"].get("adoption") for r in out["battles"]]
    assert all(a is not None for a in adoptions)
    assert any(a["checked"] > 0 for a in adoptions if a)


def test_trajectories_saved(tmp_path):
    out = _run(tmp_path, n=2)
    files = list((tmp_path / "out").glob("gm-*.json"))
    assert len(files) == 2                       # 每局一条轨迹落盘
    assert all(r["replay_ok"] for r in out["battles"])


# ---------- 开关 ----------

def test_no_globalmem_dir_disables_globalmem(tmp_path):
    out = _run(tmp_path, n=2, globalmem_dir=None)
    assert out["globalmem"]["active"] == 0
    assert all(r["globalmem"] == {} for r in out["battles"])
    assert all(r["analyst"] == "skipped" for r in out["battles"])
    assert out["memory"]["entries"] > 0           # 局部记忆仍工作


def test_no_memory_dir_disables_local_memory(tmp_path):
    out = _run(tmp_path, n=2, memory_dir=None)
    assert out["memory"]["entries"] == 0
    assert out["globalmem"]["active"] > 0         # GlobalMem 仍工作


def test_without_analyst_skips_analysis(tmp_path):
    """无 key、未注入、未开 --fake-analyst → 跳过分析，链路其余环节照跑。"""
    out = _run(tmp_path, n=2, fake_analyst=False)
    assert all(r["analyst"] == "skipped" for r in out["battles"])
    assert out["globalmem"]["active"] == 0
    assert out["memory"]["entries"] > 0


def test_injected_analyst_llm_is_used(tmp_path):
    """analyst_llm 注入缝：假分析师的 JSON 决策被采纳。"""
    payload = {"outcome": "win", "root_cause": "r", "decision": "create",
               "strategy_text": "注入分析师产出的经验", "reason": "x"}

    class _LLM:
        def invoke(self, messages):
            return AIMessage(content=json.dumps(payload, ensure_ascii=False))

    out = _run(tmp_path, n=1, fake_analyst=False, analyst_llm=_LLM())
    assert all(r["analyst"] == "ran" for r in out["battles"])
    texts = [e["strategy_text"] for e in GlobalMemStore(tmp_path / "gm").active()]
    assert "注入分析师产出的经验" in texts


# ---------- A/B ----------

def test_ab_runs_at_cadence(tmp_path):
    out = _run(tmp_path, n=4, ab_every=2, ab_instances=_insts(2), ab_seeds=1)
    assert len(out["ab"]) == 2
    assert [a["after_battle"] for a in out["ab"]] == [2, 4]
    ab = out["ab"][0]
    assert set(ab) >= {"on", "off", "delta"}
    assert ab["on"]["n_games"] == ab["off"]["n_games"] > 0


def test_ab_off_by_default(tmp_path):
    out = _run(tmp_path, n=2)
    assert out["ab"] == []


def test_measure_ab_standalone_is_read_only(tmp_path):
    """measure_ab 只读 store：调用前后条目数不变。"""
    _run(tmp_path, n=2)
    store = GlobalMemStore(tmp_path / "gm")
    before = len(store.all())
    ab = measure_ab(instances=_insts(2), globalmem_dir=str(tmp_path / "gm"),
                    settings=Settings(), seeds=1)
    assert len(GlobalMemStore(tmp_path / "gm").all()) == before
    assert ab["on"]["n_games"] > 0 and "delta" in ab


# ---------- 确定性 ----------

def test_offline_run_is_deterministic(tmp_path):
    """离线路径同 seed 两次：逐局胜负/回合数/决策动作一致。"""
    a = _run(tmp_path / "r1", n=3)
    b = _run(tmp_path / "r2", n=3)

    def sig(out):
        return [(r["battle"], r["winner"], r["turns"],
                 sorted((s, v["action"]) for s, v in r["globalmem"].items()))
                for r in out["battles"]]

    assert sig(a) == sig(b)
