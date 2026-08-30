"""M5 eval：回答行为评测集（七类覆盖 + 确定性门 + ILLEGAL_SKILL 红绿）的测试。"""

from __future__ import annotations

from rock_pvp_agent.advisor.eval.cases import CATEGORIES, CASES, EvalCase
from rock_pvp_agent.advisor.eval.run import cluster_failures, gate, run_case, run_eval

_ILLEGAL_TEAM = [
    {"spirit": "迪莫", "skills": ["抓挠"], "bloodline": "", "nature": "坦率", "iv": {}},
    {"spirit": "喵喵", "skills": ["抓挠"], "bloodline": "", "nature": "坦率", "iv": {}},
    {"spirit": "火花", "skills": ["火苗"], "bloodline": "", "nature": "坦率", "iv": {}},
]
_LEGAL_TEAM = [
    {"spirit": "迪莫", "skills": ["闪光"], "bloodline": "", "nature": "坦率", "iv": {}},
    {"spirit": "喵喵", "skills": ["抓挠"], "bloodline": "", "nature": "坦率", "iv": {}},
    {"spirit": "火花", "skills": ["火苗"], "bloodline": "", "nature": "坦率", "iv": {}},
]


def test_every_category_has_held_in_and_out():
    for cat in CATEGORIES:
        splits = {c.split for c in CASES if c.category == cat}
        assert splits == {"held_in", "held_out"}, f"{cat} 缺 held-in/held-out 用例"


def test_run_eval_deterministic_green():
    result = run_eval(CASES)
    assert result["failed"] == 0
    assert result["needs_model"] == 2  # EVIDENCE_MISMATCH 两条


def test_illegal_skill_red_then_green():
    """单表面改进演示：非法阵容 → 红；换合法阵容 → 绿。"""
    red = run_case(EvalCase("tmp_illegal", "held_in", "ILLEGAL_SKILL", "帮我组队", "in_scope",
                            ("validate_legal",), {"candidate_team": _ILLEGAL_TEAM}))
    assert red["passed"] is False

    green = run_case(EvalCase("tmp_legal", "held_in", "ILLEGAL_SKILL", "帮我组队", "in_scope",
                              ("validate_legal",), {"candidate_team": _LEGAL_TEAM}))
    assert green["passed"] is True


def test_cluster_failures_by_category():
    cases = [
        EvalCase("a", "held_in", "ILLEGAL_SKILL", "帮我组队", "in_scope",
                 ("validate_legal",), {"candidate_team": _ILLEGAL_TEAM}),
        EvalCase("b", "held_in", "ILLEGAL_SKILL", "帮我组队", "in_scope",
                 ("validate_legal",), {"candidate_team": _ILLEGAL_TEAM}),
    ]
    clusters = cluster_failures([run_case(c) for c in cases])
    assert clusters == {"ILLEGAL_SKILL": ["a", "b"]}


def test_evidence_mismatch_needs_model():
    evm = next(c for c in CASES if c.category == "EVIDENCE_MISMATCH")
    r = run_case(evm)
    assert r["needs_model"] is True
    assert r["passed"] is None


def test_gate_ok_and_red():
    assert gate(run_eval(CASES)["results"])["ok"] is True
    red = run_eval([EvalCase("x", "held_in", "ILLEGAL_SKILL", "帮我组队", "in_scope",
                             ("validate_legal",), {"candidate_team": _ILLEGAL_TEAM})])
    assert gate(red["results"])["ok"] is False
