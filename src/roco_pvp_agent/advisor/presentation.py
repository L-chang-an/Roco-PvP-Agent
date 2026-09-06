"""Bounded public descriptions derived from tool facts, never provider reasoning."""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from ..tooling.models import ToolDispatchResult

NAMES = {
    "get_catalog_version": "查询数据版本", "search_spirits": "检索候选精灵",
    "get_spirit_profile": "查询精灵档案", "get_skill_profile": "查询技能档案",
    "get_build_options": "查询构筑选项", "validate_team": "校验队伍",
    "analyze_team": "分析队伍覆盖", "simulate_matchups": "模拟对局",
    "query_trajectory_evidence": "查询对局证据", "query_global_mem": "查询全局经验",
    "query_local_mem": "查询局部经验", "retrieve_team_skill": "检索组队流程",
    "tool_search": "加载工具能力", "sandbox_python_query": "查询授权数据",
    "submit_team_advice": "提交队伍建议", "final_answer": "整理最终回答",
}


class ToolPresentation(BaseModel):
    name: str
    display_name: str
    ok: bool
    domain_status: str = "unknown"
    error_code: str | None = None
    args_summary: str = ""
    result_summary: str = ""
    highlights: list[str] = Field(default_factory=list)
    retryable: bool = False
    retry_exhausted: bool = False
    truncated: bool = False
    details_available: bool = True


def short(value: Any, limit=120) -> str:
    return " ".join(str(value).split())[:limit]


def args_summary(name: str, args: dict) -> str:
    if name in ("submit_team_advice", "final_answer"):
        return NAMES[name]
    if "name" in args:
        return short(args["name"])
    if "team" in args:
        text = f"检查 {len(args['team'])} 只精灵"
        if name == "validate_team":
            text += f"；目标 {args.get('team_size', 3)} 人；道具 {len(args.get('items') or [])} 件"
        if name == "simulate_matchups":
            text += f"；{len(args.get('opponents', []))} 个对手，{len(args.get('seeds', []))} 个 seed"
        return short(text)
    if name == "sandbox_python_query":
        return short("授权数据集：" + "、".join(args.get("dataset_ids", [])))
    if name == "tool_search":
        return short("、".join(args.get("tool_names") or args.get("queries") or []))
    if name == "query_trajectory_evidence":
        return short(f"{args.get('kind', '未知来源')}；筛选 {args.get('team_filter') or '全部匹配队伍'}")
    if name == "search_spirits":
        return short("筛选条件：" + json.dumps(args.get("filters", []), ensure_ascii=False))
    if name.startswith("query_") and name.endswith("_mem"):
        return "查询历史经验，非当前局面事实"
    return short(args.get("query", "查询当前数据"))


def _describe(name, value, args, result):
    """Return business status, short description and highlights from known shapes."""
    if name == "submit_team_advice":
        validation = dict(result.details.get("validation") or {})
        valid = bool(result.final_result and result.final_result.kind == "team_advice"
                     and validation.get("ok") is True)
        errors = validation.get("errors") or result.details.get("errors") or []
        if valid:
            alternatives = (result.final_result.advice or {}).get("alternatives", [])
            return "valid", "主队建议已通过校验" + ("；备选队伍尚未校验" if alternatives else ""), []
        return "invalid", "队伍建议校验失败" + ("，修复次数已耗尽" if result.retry_exhausted else "，需要修复"), [short(e.get("message", e)) for e in errors[:2]]
    if name == "final_answer":
        return "completed", "最终回答已整理", []
    if value is None:
        return "not_found", "未找到匹配结果", []
    if isinstance(value, str) and "未启用" in value:
        return "disabled", "该查询能力未启用", []
    if name == "search_spirits" and isinstance(value, list):
        return ("found" if value else "not_found"), f"返回 {len(value)} 个候选（本次返回数量）", [short(v.get("name", "未知")) for v in value[:3]]
    if name == "retrieve_team_skill" and isinstance(value, list):
        return ("found" if value else "not_found"), f"命中 {len(value)} 个 Agent 工作流程", [short(v.get("name", "未知")) for v in value[:3]]
    if not isinstance(value, dict):
        return "unknown", "查询已完成；详细结果可展开查看", []
    if name == "validate_team":
        errors = value.get("errors", [])
        if value.get("ok") is True:
            if not args.get("team"):
                return "empty", "未提供队伍，本次没有可校验成员", []
            return "valid", "校验通过，0 项错误", []
        return "invalid", f"发现 {len(errors)} 项配置问题，需要修复", [short(e.get("message", e)) for e in errors[:2]]
    if name == "get_catalog_version":
        return "found", f"数据 {str(value.get('data_digest', '未知'))[:8]}，规则 {str(value.get('rules_digest', '未知'))[:8]}；{value.get('spirit_count', '?')} 只精灵，{value.get('skill_count', '?')} 项技能", []
    if name == "get_spirit_profile":
        return "found", f"属性：{'、'.join(value.get('types', []))}；特性：{value.get('trait_name', '未知')}；可学技能 {len(value.get('learnable_skills', []))} 项", []
    if name == "get_skill_profile":
        implemented = {True: "已实装", False: "未实装"}.get(value.get("implemented"), "实装状态未知")
        return "found", f"{value.get('type', '未知')}系／{value.get('kind', '未知')}；能耗 {value.get('energy_cost', '未知')}；{implemented}", []
    if name == "get_build_options":
        return "found", f"可学技能 {len(value.get('learnable_skills', []))} 项；IV 上限 {value.get('iv_max', '未知')}，最多 {value.get('iv_dims', '未知')} 个维度", []
    if name == "analyze_team":
        gaps = value.get("role_gaps", [])
        return "analyzed", f"已生成 {len(value.get('speed_tiers', []))} 项速度分层、{len(value.get('offensive_coverage', {}))} 项进攻覆盖；{len(gaps)} 项角色缺口（启发式）", [short(v) for v in gaps[:3]]
    if name == "simulate_matchups":
        matchups = value.get("matchups", {})
        return "simulated", f"{value.get('label', '模拟')}；方法 {value.get('strategy', '未知')}；{len(matchups)} 组对局，非实战胜率", [short(f"{'、'.join(v.get('opponent', []))}：{v.get('games', 0)} 局") for v in list(matchups.values())[:3]]
    if name == "query_trajectory_evidence":
        return "found" if value.get("total_games") else "not_found", f"{value.get('kind', '未知来源')} 匹配 {value.get('total_games', 0)} 局；达到样本门槛的构筑 {len(value.get('by_team', {}))} 个；排除版本不符 {value.get('version_mismatch_count', 0)} 局", []
    if name in ("query_global_mem", "query_local_mem"):
        hits = value.get("hits", [])
        return "found" if hits else "not_found", f"命中 {len(hits)} 条历史经验，非当前局面事实", []
    if name == "tool_search":
        matches, missing = value.get("matches", []), value.get("missing", [])
        cached = sum(m.get("load_state") == "cached" for m in matches)
        return "found" if matches else "not_found", f"加载 {len(matches) - cached} 项，缓存命中 {cached} 项，未找到 {len(missing)} 项", [short(m.get("name", "")) for m in matches[:3]]
    if name == "sandbox_python_query":
        data = value.get("result", value)
        counts = [f"{k}：{v}" for k, v in data.items() if k in ("count", "total", "rows", "columns") and isinstance(v, (int, float))] if isinstance(data, dict) else []
        return "completed", "授权数据查询完成" + ("；" + "；".join(counts) if counts else "；结果可展开查看"), []
    return "unknown", "查询已完成；详细结果可展开查看", []


def present_tool(result: ToolDispatchResult) -> tuple[dict, dict]:
    name = result.call.name
    args = dict(result.logged_arguments or {})
    code = getattr(result.error_code, "value", result.error_code)
    p = ToolPresentation(name=name, display_name=NAMES.get(name, short(name)), ok=result.ok,
        error_code=code, retryable=result.retryable, retry_exhausted=result.retry_exhausted,
        truncated=result.truncated)
    try:
        p.args_summary = args_summary(name, args)
        if name == "submit_team_advice":
            status, text, highlights = _describe(name, None, args, result)
        elif not result.ok:
            status, text, highlights = "error", f"调用未成功：{code or 'execution_error'}", []
        elif result.truncated:
            status, text, highlights = "truncated", "结果超过大小限制，已截断；无法完整概括", []
        else:
            try:
                value = json.loads(result.content)
            except (ValueError, TypeError):
                value = result.content
            status, text, highlights = _describe(name, value, args, result)
        p.domain_status, p.result_summary = status, short(text)
        p.highlights = [short(h) for h in highlights[:3]]
    except Exception:
        p.domain_status = "unknown" if result.ok else "error"
        p.result_summary = "摘要暂不可用；可查看调用状态与详情"
    if name in ("submit_team_advice", "final_answer"):
        details = {"status": p.domain_status, "summary": p.result_summary, "errors": p.highlights}
    else:
        details = {"args": args, "result": result.content[:20_000], "truncated": result.truncated}
    # Bound arguments too, including malformed/unknown calls.
    encoded = json.dumps(details, ensure_ascii=False, default=str)
    if len(encoded) > 24_000:
        details = {"preview": encoded[:24_000], "truncated": True}
    return p.model_dump(), details


def present_round(tools: list[dict], status: str) -> dict:
    warnings = [t for t in tools if t.get("domain_status") in
                ("invalid", "error", "not_found", "disabled", "truncated", "empty")
                or t.get("status") in ("skipped", "failed", "timed_out", "cancelled")]
    if status != "completed":
        title = {"cancelled": "本轮已停止", "timed_out": "本轮执行超时", "failed": "本轮执行失败", "interrupted": "本轮因服务退出中断"}.get(status, "本轮已结束")
    elif warnings:
        title = f"本轮已结束，{len(warnings)} 项需注意"
    elif not tools:
        title = "已完成回答整理"
    elif any(t.get("domain_status") == "valid" for t in tools):
        title = "已完成主队校验与结果整理"
    else:
        names = list(dict.fromkeys(t.get("display_name", "查询") for t in tools))
        title = "已完成" + "、".join(names[:2])
    summaries = [t.get("result_summary", "") for t in [*warnings, *[t for t in tools if t not in warnings]]][:3]
    remaining, bounded = 240, []
    for text in summaries:
        bounded.append(text[:remaining])
        remaining -= len(bounded[-1])
    return {"title": title[:24], "summary": bounded or [title], "has_warnings": bool(warnings), "tool_count": len(tools)}
