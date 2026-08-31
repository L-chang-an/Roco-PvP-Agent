"""M3 advice：TeamAdviceSchema + submit_team_advice 三道闸（合法性/版本/证据）测试。"""

from __future__ import annotations

from environment.datafingerprint import data_digest
from environment.dataset import DataSource, load_spirits
from roco_pvp_agent.advisor.advice import TeamAdviceSchema, submit_team_advice

_BOSS = next(s.name for s in load_spirits(DataSource.VALID).values() if s.is_boss)


def _unit(spirit, skills, **kw) -> dict:
    u = {"spirit": spirit, "skills": skills, "bloodline": "", "nature": "坦率",
         "iv": {}, "role": "强攻", "rationale": "克制水系",
         "evidence_ids": ["selfplay:deadbeef:1"]}
    u.update(kw)
    return u


def _valid_team() -> list[dict]:
    return [
        _unit("迪莫", ["闪光"]),
        _unit("喵喵", ["抓挠"]),
        _unit("火花", ["火苗"]),
    ]


def _payload(**overrides) -> dict:
    p = {
        "rules_used": {"team_size": 3, "lives": 2, "source": "VALID"},
        "assumptions": [],
        "data_digest": data_digest(),
        "team": _valid_team(),
        "synergy": "",
        "strengths": [],
        "weak_matchups": [],
        "evidence": {"catalog": {}, "human": {}, "selfplay": {"total_games": 1}, "simulation": {}},
        "uncertainty": "",
        "alternatives": [],
    }
    p.update(overrides)
    return p


def test_legal_advice_ok():
    result = submit_team_advice(_payload())
    assert result["ok"] is True
    assert isinstance(result["advice"], TeamAdviceSchema)
    assert len(result["advice"].team) == 3


def test_illegal_spirit_not_found():
    team = _valid_team()
    team[0]["spirit"] = "不存在的精灵"
    result = submit_team_advice(_payload(team=team))
    assert result["ok"] is False
    codes = [e["code"] for e in result["errors"]]
    assert "SPIRIT_NOT_FOUND" in codes


def test_boss_not_allowed():
    team = _valid_team()
    team[0]["spirit"] = _BOSS
    result = submit_team_advice(_payload(team=team))
    assert result["ok"] is False
    assert "BOSS_NOT_ALLOWED" in [e["code"] for e in result["errors"]]


def test_version_gate_mismatch():
    result = submit_team_advice(_payload(data_digest="d_wrong"))
    assert result["ok"] is False
    assert "DATA_DIGEST_MISMATCH" in [e["code"] for e in result["errors"]]


def test_evidence_missing():
    team = _valid_team()
    for u in team:
        u["evidence_ids"] = []
        u["rationale"] = "这是未溯源的理由"
    result = submit_team_advice(_payload(team=team, uncertainty=""))
    assert result["ok"] is False
    assert "EVIDENCE_MISSING" in [e["code"] for e in result["errors"]]


def test_evidence_ok_when_uncertainty_labeled():
    team = _valid_team()
    for u in team:
        u["evidence_ids"] = []
    result = submit_team_advice(_payload(team=team, uncertainty="样本不足，属启发式建议"))
    assert result["ok"] is True


def test_schema_invalid():
    result = submit_team_advice({"team": "not-a-list"})
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "SCHEMA_INVALID"
