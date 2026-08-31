"""G3 GlobalAnalyst：迷雾口径 / 双视角互不可见 / update-create-skip / 降级 / 审计。"""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage

from environment.dataset import DataSource
from roco_pvp_agent.battle.evolution.analysis import analyze_record
from roco_pvp_agent.battle.evolution.globalmem import (
    DEFAULT_MAX_STRATEGY_TOKENS,
    GlobalMemStore,
    make_global_entry_id,
    matchup_key,
)
from roco_pvp_agent.battle.evolution.globalmem_analyst import (
    GlobalAnalyst,
    apply_decision,
    render_battle_summary,
)
from roco_pvp_agent.battle.selfplay import run_selfplay
from roco_pvp_agent.config import Settings

_DIGEST = "d_test"


def _record():
    return run_selfplay(seed=7, team_size=3, lives=2)["record"]


class _ScriptLLM:
    """按脚本回 JSON 的假分析师；记录每次收到的 user 文本（供隔离断言）。"""

    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.seen: list[str] = []

    def invoke(self, messages):
        self.seen.append(messages[-1].content)
        p = self._payloads.pop(0) if self._payloads else {}
        return AIMessage(content=json.dumps(p, ensure_ascii=False))


class _BoomLLM:
    def invoke(self, messages):
        raise RuntimeError("网关炸了")


def _decision(dec="create", text="开局压能量，残局换人吸伤"):
    return {"outcome": "win", "root_cause": "抢速成功", "decision": dec,
            "strategy_text": text, "reason": "r"}


def _key_for(record, side):
    rules = record["rules"]
    mine, foe = ("team_a", "team_b") if side == "a" else ("team_b", "team_a")
    return matchup_key(record[mine], record[foe], team_size=rules["team_size"],
                       lives=rules["lives"], source=DataSource.VALID)


# ---------- 迷雾口径 ----------

def test_summary_excludes_full_state_value():
    """摘要用 show_v=False → 不含 full-state 价值（那是离线优化器口径）。"""
    rec = _record()
    text = render_battle_summary(analyze_record(rec), "a")
    assert "价值" not in text and "full-state" not in text


def test_summary_marks_side_and_outcome():
    rec = _record()
    text = render_battle_summary(analyze_record(rec), "a")
    assert "[视角] a 方" in text and "[终局]" in text and "[逐回合]" in text


def test_summary_mentions_loaded_strategy_or_none():
    rec = _record()
    with_loaded = render_battle_summary(analyze_record(rec), "a", loaded_strategy="旧经验X")
    assert "旧经验X" in with_loaded
    without = render_battle_summary(analyze_record(rec), "a")
    assert "无（本局未命中" in without


# ---------- 双视角互不可见 ----------

def test_analyze_both_calls_twice_with_isolated_inputs():
    """a/b 各调一次；且每次输入只含该侧视角（不能把两侧摘要拼在一起）。"""
    rec = _record()
    llm = _ScriptLLM([_decision(), _decision()])
    out = GlobalAnalyst(Settings(), llm=llm).analyze_both(rec)
    assert set(out) == {"a", "b"}
    assert len(llm.seen) == 2
    assert "[视角] a 方" in llm.seen[0] and "[视角] b 方" not in llm.seen[0]
    assert "[视角] b 方" in llm.seen[1] and "[视角] a 方" not in llm.seen[1]


# ---------- 降级 ----------

def test_llm_exception_degrades_to_none():
    rec = _record()
    a = GlobalAnalyst(Settings(), llm=_BoomLLM())
    assert a.analyze(rec, "a") is None
    assert a.diagnostics[-1]["status"] == "degraded"


def test_bad_json_degrades_to_none():
    rec = _record()

    class _Junk:
        def invoke(self, messages):
            return AIMessage(content="我觉得应该更激进一点")

    assert GlobalAnalyst(Settings(), llm=_Junk()).analyze(rec, "a") is None


def test_invalid_decision_rejected():
    rec = _record()
    llm = _ScriptLLM([{"decision": "bogus", "strategy_text": "x"}])
    assert GlobalAnalyst(Settings(), llm=llm).analyze(rec, "a") is None


def test_non_skip_without_text_rejected():
    rec = _record()
    llm = _ScriptLLM([_decision(text="   ")])
    assert GlobalAnalyst(Settings(), llm=llm).analyze(rec, "a") is None


def test_bad_side_raises():
    with pytest.raises(ValueError):
        GlobalAnalyst(Settings(), llm=_ScriptLLM([])).analyze(_record(), "c")


# ---------- 落库三分支 ----------

def test_apply_create(tmp_path):
    rec, store = _record(), GlobalMemStore(tmp_path)
    out = apply_decision(store, _decision("create"), record=rec, side="a",
                         data_digest=_DIGEST, source=DataSource.VALID)
    assert out["action"] == "create" and out["ok"] and store.count() == 1
    e = store.active()[0]
    assert e["matchup_key"] == _key_for(rec, "a")
    assert e["provenance"]["side"] == "a"


def test_apply_skip_is_noop(tmp_path):
    rec, store = _record(), GlobalMemStore(tmp_path)
    out = apply_decision(store, _decision("skip"), record=rec, side="a",
                         data_digest=_DIGEST, source=DataSource.VALID)
    assert out["action"] == "skip" and store.count() == 0


def test_apply_none_decision_is_noop(tmp_path):
    rec, store = _record(), GlobalMemStore(tmp_path)
    out = apply_decision(store, None, record=rec, side="a", data_digest=_DIGEST,
                         source=DataSource.VALID)
    assert out["action"] == "none" and store.count() == 0


def test_apply_update_supersedes_loaded_entry(tmp_path):
    """update 走 supersede（append-only）：旧条目留库被标记，active 只剩新的。"""
    rec, store = _record(), GlobalMemStore(tmp_path)
    key = _key_for(rec, "a")
    old = {"entry_id": make_global_entry_id(key, "旧经验", _DIGEST), "matchup_key": key,
           "my_roster": [], "foe_roster": [], "strategy_text": "旧经验",
           "Q": 0.0, "n_used": 0, "n_wins": 0,
           "provenance": {"data_digest": _DIGEST}}
    store.add(old)
    rec["global_mem_a"] = old["entry_id"]
    out = apply_decision(store, _decision("update", "新经验"), record=rec, side="a",
                         data_digest=_DIGEST, source=DataSource.VALID)
    assert out["action"] == "update" and out["ok"]
    assert store.count() == 1
    assert store.get(old["entry_id"])["superseded_by"] == out["entry_id"]
    assert store.get(old["entry_id"])["strategy_text"] == "旧经验"     # 旧文本没被销毁


def test_apply_update_without_loaded_downgrades_to_create(tmp_path):
    """update 但本局没加载过（record 无 global_mem_a）→ 降级 create，不信 LLM 乱指。"""
    rec, store = _record(), GlobalMemStore(tmp_path)
    rec.pop("global_mem_a", None)
    out = apply_decision(store, _decision("update", "新经验"), record=rec, side="a",
                         data_digest=_DIGEST, source=DataSource.VALID)
    assert out["action"] == "update→create" and out["ok"] and store.count() == 1


def test_apply_respects_token_limit_and_audits(tmp_path):
    """超 token 上限 → Store 拒绝（不截断），审计有记录。"""
    rec = _record()
    store = GlobalMemStore(tmp_path)
    long_text = "策" * (DEFAULT_MAX_STRATEGY_TOKENS * 2 + 10)
    out = apply_decision(store, _decision("create", long_text), record=rec, side="a",
                         data_digest=_DIGEST, source=DataSource.VALID)
    assert out["ok"] is False and "超上限" in out["reason"]
    assert store.count() == 0
    assert "rejected" in store.report_path.read_text(encoding="utf-8")


def test_analyze_rejects_untrusted_trajectory():
    """replay_ok=False（不可信轨迹）→ 拒绝分析（同 R1/R2 纪律）。"""
    rec = _record()
    rec["turns"][1]["state_hash"] = "tampered"
    a = GlobalAnalyst(Settings(), llm=_ScriptLLM([_decision()]))
    assert a.analyze(rec, "a") is None
    assert a.diagnostics[-1]["reason"] == "replay_ok=False"
