"""结构化终结 + EvidenceGate（M3）。

`TeamAdviceSchema` 是顾问的唯一终稿形状（替代自由文本 final_answer）。`submit_team_advice`
在终结处做三道代码级闸：
1. **LegalityGate**：team → [TeamPick] → `validate_team(source=VALID)`，未过绝不输出为推荐。
2. **VersionGate**：`data_digest` 必须等于当前。
3. **EvidenceGate**：每条 rationale 要么带 evidence_ids，要么 uncertainty 标注
   「理论构筑/启发式/样本不足」。human/selfplay 分离由 `EvidenceSummary` 结构保证。
"""

from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError

from environment.datafingerprint import data_digest
from environment.dataset import DataSource
from environment.rules import DEFAULT_RULES, BattleRules
from environment.teambuilder import TeamPick

from roco_pvp_agent.advisor.validate import validate_team

_UNCERTAINTY_MARKERS = ("理论构筑", "启发式", "样本不足")


class UnitAdvice(BaseModel):
    """单只精灵的组队建议（== TeamPick + 分工/理由/证据）。"""

    spirit: str
    skills: list[str]
    bloodline: str = ""
    nature: str = "坦率"
    iv: dict[str, int] = Field(default_factory=dict)
    role: str = ""
    rationale: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class EvidenceSummary(BaseModel):
    """证据摘要：catalog / human / selfplay / simulation 四段分离（不提供合并字段）。"""

    catalog: dict = Field(default_factory=dict)
    human: dict = Field(default_factory=dict)
    selfplay: dict = Field(default_factory=dict)
    simulation: dict = Field(default_factory=dict)


class TeamAdviceSchema(BaseModel):
    """顾问结构化终稿。"""

    rules_used: dict            # {team_size, lives, source}
    assumptions: list[str] = Field(default_factory=list)
    data_digest: str
    team: list[UnitAdvice]
    synergy: str = ""
    strengths: list[str] = Field(default_factory=list)
    weak_matchups: list[str] = Field(default_factory=list)
    evidence: EvidenceSummary
    uncertainty: str = ""
    alternatives: list[list[UnitAdvice]] = Field(default_factory=list)


def _evidence_check(advice: TeamAdviceSchema) -> list[dict]:
    """EvidenceGate：rationale 必须有证据或明确标注不确定性。"""
    errors: list[dict] = []
    labeled = any(m in advice.uncertainty for m in _UNCERTAINTY_MARKERS)
    for i, u in enumerate(advice.team, start=1):
        if u.rationale and not u.evidence_ids and not labeled:
            errors.append({
                "code": "EVIDENCE_MISSING",
                "message": f"第{i}只「{u.spirit}」的选择理由缺 evidence_ids 且未标注不确定性。",
                "pick_index": i,
            })
    return errors


def submit_team_advice(payload: dict, *, source: DataSource = DataSource.VALID) -> dict:
    """解析并校验结构化建议。返回 `{"ok": bool, "advice"|"errors"}`。

    三道闸：LegalityGate（阵容合法）→ VersionGate（data_digest 匹配）→ EvidenceGate（理由可溯源）。
    """
    try:
        advice = TeamAdviceSchema(**payload)
    except ValidationError as exc:
        return {"ok": False, "errors": [{"code": "SCHEMA_INVALID", "message": str(exc), "pick_index": None}]}

    # LegalityGate：把 UnitAdvice 转 TeamPick → validate_team（实战推荐只用 VALID）
    rules = BattleRules(
        team_size=advice.rules_used.get("team_size", DEFAULT_RULES.team_size),
        lives=advice.rules_used.get("lives", DEFAULT_RULES.lives),
    )
    picks = [TeamPick(spirit=u.spirit, skills=list(u.skills), bloodline=u.bloodline,
                      nature=u.nature, iv=dict(u.iv)) for u in advice.team]
    tv = validate_team(picks, [], rules=rules, source=source)
    if not tv.ok:
        return {"ok": False, "errors": tv.errors}

    # VersionGate
    if advice.data_digest != data_digest():
        return {"ok": False, "errors": [{
            "code": "DATA_DIGEST_MISMATCH",
            "message": f"data_digest 不匹配（应为当前 {data_digest()}）。",
            "pick_index": None,
        }]}

    # EvidenceGate
    errors = _evidence_check(advice)
    if errors:
        return {"ok": False, "errors": errors}

    return {"ok": True, "advice": advice}
