"""M5 audit：回答审计日志（隐私 + 复算）的测试。"""

from __future__ import annotations

import hashlib

from environment.dataset import DataSource
from rock_pvp_agent.advisor.audit import AnswerAudit, log_answer, revalidate
from rock_pvp_agent.advisor.validate import validate_team
from environment.teambuilder import TeamPick

_LEGAL_TEAM = [
    {"spirit": "迪莫", "skills": ["闪光"], "bloodline": "", "nature": "坦率", "iv": {}},
    {"spirit": "喵喵", "skills": ["抓挠"], "bloodline": "", "nature": "坦率", "iv": {}},
    {"spirit": "火花", "skills": ["火苗"], "bloodline": "", "nature": "坦率", "iv": {}},
]


def _audit(**kw) -> AnswerAudit:
    defaults = dict(
        message="帮我组队", scope_verdict="in_scope",
        versions={"data_digest": "d_x", "rules_digest": "rules_y"},
        tool_calls=[{"name": "get_catalog_version", "args": {}, "result": "..."}],
        evidence_ids=["selfplay:abc:1"],
        candidate_teams=_LEGAL_TEAM,
        validation={"ok": True, "errors": []},
        final_answer="建议用迪莫",
    )
    defaults.update(kw)
    return log_answer(**defaults)


def test_log_answer_fields_complete():
    a = _audit()
    assert isinstance(a, AnswerAudit)
    assert a.scope_verdict == "in_scope"
    assert a.message_digest and a.final_answer_digest
    assert a.tool_calls[0]["name"] == "get_catalog_version"
    assert set(a.tool_calls[0]) == {"name", "args_digest", "result_digest"}


def test_log_answer_digests_not_raw():
    message = "帮我组队"
    a = _audit(message=message)
    assert a.message_digest == hashlib.sha256(message.encode("utf-8")).hexdigest()
    assert a.message_digest != message
    # 审计对象字段不含原文/思维链
    dump = a.model_dump()
    assert "message" not in dump and "final_answer" not in dump
    assert "reasoning_content" not in dump


def test_revalidate_recomputes_validation():
    tv = validate_team([TeamPick(**p) for p in _LEGAL_TEAM], [], source=DataSource.VALID)
    a = _audit(candidate_teams=_LEGAL_TEAM, validation={"ok": tv.ok, "errors": tv.errors})
    recomputed = revalidate(a)
    assert recomputed["ok"] == a.validation["ok"] is True
    assert recomputed["errors"] == a.validation["errors"] == []


def test_revalidate_detects_illegal_team():
    illegal = [dict(_LEGAL_TEAM[0], skills=["抓挠"]), *_LEGAL_TEAM[1:]]  # 迪莫不可学抓挠
    a = _audit(candidate_teams=illegal, validation={"ok": False, "errors": []})
    assert revalidate(a)["ok"] is False
