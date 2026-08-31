"""R1 记忆库 + 反思提取：两阶段检索边界 + roundtrip + Q 更新 + digest 隔离 + 轨迹提取。"""

from __future__ import annotations

import pytest

from roco_pvp_agent.battle.evolution.memory import (
    MemoryQuery,
    MemoryStore,
    make_entry_id,
    two_phase_search,
    update_q,
)
from roco_pvp_agent.battle.evolution.reflect import extract_experiences
from roco_pvp_agent.battle.selfplay import run_selfplay


def _entry(store, side="a", key="my2/foe2/迪莫/水蓝蓝/high/high/0/early/2/2",
           Q=0.0, action=None):
    action = action or {"type": "skill", "value": 0}
    return store.add({
        "entry_id": make_entry_id(side, key, action),
        "side": side,
        "lineage_family": "extract",
        "situation_key": key,
        "situation_text": "t",
        "experience_text": "换人吸伤再回能",
        "action": action,
        "Q": Q,
        "n_used": 0,
        "n_adopted": 0,
        "provenance": {"rules_version": "r", "data_digest": "d_abc", "source_type": "extract"},
    })


# ---------- memory 原语 ----------

def test_add_idempotent_and_roundtrip(tmp_path):
    store = MemoryStore(tmp_path)
    eid = _entry(store)
    assert _entry(store) == eid              # 幂等：同 entry_id 不重复
    assert store.count() == 1
    assert store.get(eid)["experience_text"] == "换人吸伤再回能"


def test_update_q(tmp_path):
    store = MemoryStore(tmp_path)
    eid = _entry(store, Q=0.0)
    q = update_q(store, eid, 0.5)
    assert q == pytest.approx(0.15)          # 0 + 0.3*(0.5 - 0)
    assert store.get(eid)["Q"] == pytest.approx(0.15)


def test_search_side_lock(tmp_path):
    store = MemoryStore(tmp_path)
    eid = _entry(store, side="a")
    q_b = MemoryQuery(situation_key="my2/foe2/迪莫/水蓝蓝/high/high/0/early/2/2",
                      side="b", data_digest="d_abc")
    assert two_phase_search(store, q_b) == []        # 侧锁不匹配 → 空
    q_a = MemoryQuery(situation_key="my2/foe2/迪莫/水蓝蓝/high/high/0/early/2/2",
                      side="a", data_digest="d_abc")
    assert [e["entry_id"] for e in two_phase_search(store, q_a)] == [eid]


def test_search_data_digest_isolated(tmp_path):
    store = MemoryStore(tmp_path)
    _entry(store, side="a")
    q = MemoryQuery(situation_key="my2/foe2/迪莫/水蓝蓝/high/high/0/early/2/2",
                    side="a", data_digest="d_xyz")   # 版本不同 → 硬过滤
    assert two_phase_search(store, q) == []


def test_search_delta_boundary(tmp_path):
    """δ=1 时只有完全同局面命中（相似度满分）；δ=0 时宽松。"""
    store = MemoryStore(tmp_path)
    _entry(store, side="a")
    same = MemoryQuery(situation_key="my2/foe2/迪莫/水蓝蓝/high/high/0/early/2/2",
                       side="a", data_digest="d_abc")
    assert two_phase_search(store, same, delta=1.0) != []      # 完全相同 → sim=1 ≥ 1
    diff = MemoryQuery(situation_key="my1/foe3/迪莫/水蓝蓝/high/high/0/early/2/2",
                       side="a", data_digest="d_abc")
    assert two_phase_search(store, diff, delta=1.0) == []      # 命数不同 → 硬过滤失败


# ---------- 轨迹提取 ----------

def test_extract_experiences_from_selfplay():
    record = run_selfplay(seed=7, team_size=3, lives=2)["record"]
    entries = extract_experiences(record)
    assert entries
    for e in entries:
        assert e["side"] in ("a", "b")
        assert e["situation_key"].startswith("my")
        assert e["Q"] == 0.0
        assert e["provenance"]["data_digest"] == record["data_digest"]   # 复用轨迹 stamp
        assert e["experience_text"]  # render_feedback 产出的机制文本非空
