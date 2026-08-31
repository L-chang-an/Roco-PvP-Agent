"""R3 反思与有界编辑：Playbook + bounded_edit + ReflectionService。"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage

from rock_pvp_agent.battle.evolution.editor import EditCandidate, bounded_edit
from rock_pvp_agent.battle.evolution.playbook import Playbook
from rock_pvp_agent.battle.evolution.reflect import (
    ReflectionService,
    _normalize_module,
    _parse_reflection_json,
)
from rock_pvp_agent.config import Settings


def _cand(**kw):
    base = dict(module_key="M2 action_selector", op="append", text="新规则",
                support_count=1, avg_delta=0.5, coverage=1, source_type="failure")
    base.update(kw)
    return EditCandidate(**base)


def _fake_llm(reply: str):
    class _F:
        def invoke(self, messages):
            return AIMessage(content=reply)
    return _F()


# ---------- Playbook ----------

def test_playbook_initial_and_versions():
    pb = Playbook.initial()
    assert len(pb.modules) == 5
    assert pb.validate() == []
    assert Playbook.next_version("pb_v000") == "pb_v1"   # int() 丢前导零（main 行为）
    assert pb.text()


def test_playbook_roundtrip():
    pb = Playbook.initial()
    assert Playbook.from_dict(pb.to_dict()).text() == pb.text()


# ---------- editor ----------

def test_bounded_edit_append():
    pb = Playbook.initial()
    new_pb, reports = bounded_edit(pb, [_cand()])
    assert new_pb.version == "pb_v1"
    assert [r.status for r in reports] == ["applied"]
    assert "新规则" in new_pb.module("M2 action_selector").text


def test_bounded_edit_lt_bound():
    pb = Playbook.initial()
    cands = [_cand(text=f"规则{i}") for i in range(6)]
    new_pb, reports = bounded_edit(pb, cands, lt=3)
    assert sum(r.status == "applied" for r in reports) == 3


def test_bounded_edit_reject_protected():
    pb = Playbook.initial()
    pb.modules[1].protected = True            # M2 进 [PROTECTED] 保护区
    new_pb, reports = bounded_edit(pb, [_cand()])
    assert reports[0].status == "rejected"
    assert "PROTECTED" in reports[0].reason


def test_bounded_edit_reject_multiline():
    pb = Playbook.initial()
    new_pb, reports = bounded_edit(pb, [_cand(text="a\nb")])
    assert reports[0].status == "rejected"


# ---------- reflect ----------

def test_parse_reflection_json_robust():
    text = '```json\n{"target_module":"M2","edit_op":"append","proposed_edit":"先手聚能"}\n```'
    d = _parse_reflection_json(text)
    assert d["proposed_edit"] == "先手聚能"


def test_normalize_module():
    assert _normalize_module("M2") == "M2 action_selector"
    assert _normalize_module("action_selector") == "M2 action_selector"
    assert _normalize_module("  m2 action selector ") == "M2 action_selector"
    assert _normalize_module("bogus") is None


def test_reflection_service_degraded_on_bad_json():
    svc = ReflectionService(Settings(), llm=_fake_llm("这不是 JSON"))
    cards = [{"signals": ["counterfactual_confirmed"], "turn_no": 1, "side": "a",
              "delta_winrate": 0.3, "feedback_text": "x",
              "counterfactual_better": {"type": "switch"}}]
    assert svc.reflect(cards) == []            # 坏 JSON → 降级 → 空候选
    assert any(d["status"] == "degraded" for d in svc.diagnostics)


def test_reflection_service_produces_candidates():
    reply = json.dumps({"target_module": "M2 action_selector", "edit_op": "append",
                        "proposed_edit": "能量不足时优先聚能", "evidence": ["T1"]})
    svc = ReflectionService(Settings(), llm=_fake_llm(reply))
    cards = [{"signals": ["counterfactual_confirmed"], "turn_no": 1, "side": "a",
              "delta_winrate": 0.3, "feedback_text": "x",
              "counterfactual_better": {"type": "switch"}}]
    out = svc.reflect(cards)
    assert len(out) == 1 and out[0].text == "能量不足时优先聚能"
