"""回答行为评测运行器（M5）：确定性断言 + 失败签名聚类 + 门。

确定性部分（ScopeGate 误拒/误答、ILLEGAL_SKILL、STALE_TRAJECTORY、LOW_SAMPLE_WINRATE）
零 LLM、零 key 可跑；`EVIDENCE_MISMATCH` 标 `needs_model`，交用户真实 Gate。
"""

from __future__ import annotations

from environment.datafingerprint import data_digest as _current_data_digest
from environment.dataset import DataSource
from environment.teambuilder import TeamPick

from rock_pvp_agent.advisor.eval.cases import NEEDS_MODEL_CATEGORIES, EvalCase
from rock_pvp_agent.advisor.scope import classify
from rock_pvp_agent.advisor.validate import validate_team


def _to_pick(p: dict) -> TeamPick:
    return TeamPick(
        spirit=p.get("spirit", ""),
        skills=list(p.get("skills") or []),
        bloodline=p.get("bloodline", ""),
        nature=p.get("nature", "坦率"),
        iv=dict(p.get("iv") or {}),
    )


# ── 确定性断言（每个返回 (passed, reason)）──

def _check_scope(case: EvalCase) -> tuple[bool, str]:
    verdict = classify(case.input).value
    return verdict == case.expected_scope, f"scope={verdict} 期望={case.expected_scope}"


def _check_validate_illegal(case: EvalCase) -> tuple[bool, str]:
    team = [_to_pick(p) for p in case.data.get("candidate_team", [])]
    tv = validate_team(team, [], source=DataSource.VALID)
    return (not tv.ok), f"非法阵容应被拒绝，实际 ok={tv.ok}"


def _check_validate_legal(case: EvalCase) -> tuple[bool, str]:
    team = [_to_pick(p) for p in case.data.get("candidate_team", [])]
    tv = validate_team(team, [], source=DataSource.VALID)
    return tv.ok, f"合法阵容应通过，实际 {[e['code'] for e in tv.errors]}"


def _check_stale_excluded(case: EvalCase) -> tuple[bool, str]:
    stale = case.data.get("digest", "")
    return stale != _current_data_digest(), f"digest={stale} 未过期"


def _check_sufficient_sample(case: EvalCase) -> tuple[bool, str]:
    games = case.data.get("games", 0)
    min_games = case.data.get("min_games", 3)
    return games >= min_games, f"样本不足 games={games} < {min_games}"


def _check_web_disabled(case: EvalCase) -> tuple[bool, str]:
    """M6 网页搜索尚未启用，占位恒真。"""
    return True, ""


_ASSERTIONS = {
    "scope": _check_scope,
    "validate_illegal": _check_validate_illegal,
    "validate_legal": _check_validate_legal,
    "stale_excluded": _check_stale_excluded,
    "sufficient_sample": _check_sufficient_sample,
    "web_disabled": _check_web_disabled,
}


def run_case(case: EvalCase) -> dict:
    """跑一条用例。needs_model 的类别跳过确定性断言、标 user_run。"""
    if case.category in NEEDS_MODEL_CATEGORIES:
        return {"id": case.id, "category": case.category, "split": case.split,
                "passed": None, "reason": "需真实模型（user_run）", "needs_model": True}
    for name in case.assertions:
        fn = _ASSERTIONS.get(name)
        if fn is None:
            return {"id": case.id, "category": case.category, "split": case.split,
                    "passed": False, "reason": f"未知断言 {name}", "needs_model": False}
        ok, reason = fn(case)
        if not ok:
            return {"id": case.id, "category": case.category, "split": case.split,
                    "passed": False, "reason": reason, "needs_model": False}
    return {"id": case.id, "category": case.category, "split": case.split,
            "passed": True, "reason": None, "needs_model": False}


def run_eval(cases: list[EvalCase], *, split: str | None = None) -> dict:
    """跑一批用例（可按 split 过滤）。"""
    if split is not None:
        cases = [c for c in cases if c.split == split]
    results = [run_case(c) for c in cases]
    return {
        "total": len(results),
        "passed": sum(1 for r in results if r["passed"] is True),
        "failed": sum(1 for r in results if r["passed"] is False),
        "needs_model": sum(1 for r in results if r["needs_model"]),
        "results": results,
    }


def cluster_failures(results: list[dict]) -> dict:
    """失败按 category 聚类（同签名聚在一起）。"""
    out: dict[str, list[str]] = {}
    for r in results:
        if r.get("passed") is False:
            out.setdefault(r["category"], []).append(r["id"])
    return out


def gate(results: list[dict]) -> dict:
    """门：确定性断言全绿才通过；needs_model 不计入门、单列。"""
    failed = [r for r in results if r["passed"] is False]
    return {
        "ok": not failed,
        "failed": failed,
        "clusters": cluster_failures(results),
        "needs_model": [r["id"] for r in results if r.get("needs_model")],
    }
