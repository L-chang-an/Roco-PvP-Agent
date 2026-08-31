"""回答审计日志（M5）：可复算的摘要与结构，**不存原文/思维链**。

隐私硬约束：`message_digest` / `final_answer_digest` / `args_digest` / `result_digest`
全是 sha256 摘要；`AnswerAudit` 字段不含用户消息原文、`reasoning_content`、`llm_reply`。
`candidate_teams` 存 pick dict（可被 `revalidate` 重跑 `validate_team` 复算当时结论）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from pydantic import BaseModel

from environment.dataset import DataSource
from environment.teambuilder import TeamPick

from roco_pvp_agent.advisor.validate import validate_team


def _digest(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AnswerAudit(BaseModel):
    """一条回答的可审计日志（全摘要，无原文/无思维链）。"""

    ts: str
    scope_verdict: str                 # M4 判定结果
    message_digest: str                # 用户消息 sha256（不存原文）
    versions: dict                     # {system_prompt_hash, data_digest, rules_digest, skill_versions}
    tool_calls: list[dict]             # [{name, args_digest, result_digest}]
    evidence_ids: list[str]
    candidate_teams: list[dict]        # 候选阵容（pick dict，可被 validate 复算）
    validation: dict                   # {ok, errors:[{code,...}]}
    final_answer_digest: str           # 最终答案 sha256
    feedback: str = ""


def log_answer(*, message: str, scope_verdict: str, versions: dict,
               tool_calls: list[dict], evidence_ids: list[str],
               candidate_teams: list[dict], validation: dict,
               final_answer: str, feedback: str = "") -> AnswerAudit:
    """落一条回答审计日志：只存摘要与结构，不存原文/思维链。"""
    return AnswerAudit(
        ts=_now(),
        scope_verdict=scope_verdict,
        message_digest=_digest(message),
        versions=dict(versions),
        tool_calls=[
            {
                "name": tc.get("name", ""),
                "args_digest": _digest(json.dumps(tc.get("args", {}), ensure_ascii=False, sort_keys=True)),
                "result_digest": _digest(str(tc.get("result", ""))),
            }
            for tc in tool_calls
        ],
        evidence_ids=list(evidence_ids),
        candidate_teams=list(candidate_teams),
        validation=dict(validation),
        final_answer_digest=_digest(final_answer),
        feedback=feedback,
    )


def revalidate(audit: AnswerAudit, *, source: DataSource = DataSource.VALID) -> dict:
    """对 `candidate_teams` 重跑 `validate_team`，返回 `{ok, errors}`（离线复算比对）。"""
    picks = [
        TeamPick(
            spirit=p.get("spirit", ""),
            skills=list(p.get("skills") or []),
            bloodline=p.get("bloodline", ""),
            nature=p.get("nature", "坦率"),
            iv=dict(p.get("iv") or {}),
        )
        for p in audit.candidate_teams
    ]
    tv = validate_team(picks, [], source=source)
    return {"ok": tv.ok, "errors": tv.errors}
