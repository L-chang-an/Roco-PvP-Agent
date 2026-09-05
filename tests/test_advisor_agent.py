"""M3 agent：TeamAdvisorAgent 结构化终结 + 修复降级 + 关闭思维链 测试。"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage

from environment.datafingerprint import data_digest
from roco_pvp_agent.agent import ChatAgent
from roco_pvp_agent.advisor.agent import (
    TeamAdvisorAgent,
    _build_advisor_registry,
)
from roco_pvp_agent.tooling import ToolOutcome

from fakes import ScriptedLLM, tool_call


def _valid_payload() -> dict:
    return {
        "rules_used": {"team_size": 3, "lives": 2, "source": "VALID"},
        "assumptions": [],
        "data_digest": data_digest(),
        "team": [
            {"spirit": "迪莫", "skills": ["闪光"], "bloodline": "", "nature": "坦率", "iv": {},
             "role": "强攻", "rationale": "克制水系", "evidence_ids": ["selfplay:deadbeef:1"]},
            {"spirit": "喵喵", "skills": ["抓挠"], "bloodline": "", "nature": "坦率", "iv": {},
             "role": "强攻", "rationale": "高速", "evidence_ids": ["selfplay:deadbeef:2"]},
            {"spirit": "火花", "skills": ["火苗"], "bloodline": "", "nature": "坦率", "iv": {},
             "role": "强攻", "rationale": "火系打击", "evidence_ids": ["selfplay:deadbeef:3"]},
        ],
        "synergy": "", "strengths": [], "weak_matchups": [],
        "evidence": {"catalog": {}, "human": {}, "selfplay": {"total_games": 1}, "simulation": {}},
        "uncertainty": "", "alternatives": [],
    }


def _illegal_payload() -> dict:
    p = _valid_payload()
    p["team"][0]["spirit"] = "不存在的精灵"
    return p


def test_advisor_happy_path(agent_settings):
    llm = ScriptedLLM([
        AIMessage(content="", tool_calls=[tool_call("get_catalog_version", {}, "c1")]),
        AIMessage(content="", tool_calls=[tool_call("submit_team_advice", {"payload": _valid_payload()}, "c2")]),
    ])
    reply = TeamAdvisorAgent(agent_settings, llm=llm).chat("帮我组个队")
    assert reply.offline is False
    assert "迪莫" in reply.reply                    # 终稿是结构化建议 JSON
    assert reply.rounds == 2
    assert [tc["name"] for tc in reply.tool_calls] == ["get_catalog_version"]


def test_submit_team_advice_is_real_handler_with_registry_policy():
    registry = _build_advisor_registry()
    entry = registry.get("submit_team_advice")

    assert entry is not None
    assert entry.terminal_on_success is True
    assert entry.retry_limit == 1
    result = entry.tool.invoke({"payload": _valid_payload()})
    assert isinstance(result, ToolOutcome)
    assert result.ok is True
    assert "迪莫" in result.content


def test_advisor_retry_state_is_local_to_each_chat_call(agent_settings):
    """同一共享 Agent 连续服务会话时，前一轮失败次数不能污染后一轮。"""

    llm = ScriptedLLM([
        # 第一次 chat：连续两次失败，触发降级。
        AIMessage(content="", tool_calls=[
            tool_call("submit_team_advice", {"payload": _illegal_payload()}, "a1")]),
        AIMessage(content="", tool_calls=[
            tool_call("submit_team_advice", {"payload": _illegal_payload()}, "a2")]),
        # 第二次 chat：第一次失败仍应获得一次修复机会，随后成功。
        AIMessage(content="", tool_calls=[
            tool_call("submit_team_advice", {"payload": _illegal_payload()}, "b1")]),
        AIMessage(content="", tool_calls=[
            tool_call("submit_team_advice", {"payload": _valid_payload()}, "b2")]),
    ])
    agent = TeamAdvisorAgent(agent_settings, llm=llm)

    first = agent.chat("组队")
    second = agent.chat("组队")

    assert "degraded" in first.reply
    assert "迪莫" in second.reply and "degraded" not in second.reply
    assert not hasattr(agent, "_advice_failed")


def test_advisor_fix_once_then_success(agent_settings):
    llm = ScriptedLLM([
        AIMessage(content="", tool_calls=[tool_call("submit_team_advice", {"payload": _illegal_payload()}, "c1")]),
        AIMessage(content="", tool_calls=[tool_call("submit_team_advice", {"payload": _valid_payload()}, "c2")]),
    ])
    reply = TeamAdvisorAgent(agent_settings, llm=llm).chat("组队")
    assert "迪莫" in reply.reply and "degraded" not in reply.reply


def test_advisor_degrade_after_two_failures(agent_settings):
    llm = ScriptedLLM([
        AIMessage(content="", tool_calls=[tool_call("submit_team_advice", {"payload": _illegal_payload()}, "c1")]),
        AIMessage(content="", tool_calls=[tool_call("submit_team_advice", {"payload": _illegal_payload()}, "c2")]),
    ])
    reply = TeamAdvisorAgent(agent_settings, llm=llm).chat("组队")
    assert "degraded" in reply.reply and reply.rounds == 2


def test_advisor_accepts_free_text(agent_settings):
    """IN_SCOPE 消息可用 final_answer 自由文本终结（不再强制 submit_team_advice）。"""
    llm = ScriptedLLM([AIMessage(content="", tool_calls=[tool_call("final_answer", {"text": "建议试试迪莫"}, "c1")])])
    reply = TeamAdvisorAgent(agent_settings, llm=llm).chat("帮我组队")
    assert reply.reply == "建议试试迪莫"


def test_advisor_no_thinking_emitted(agent_settings):
    llm = ScriptedLLM([
        AIMessage(content="内部推理", tool_calls=[tool_call("get_catalog_version", {}, "c1")],
                  additional_kwargs={"reasoning_content": "先查版本"}),
        AIMessage(content="", tool_calls=[tool_call("submit_team_advice", {"payload": _valid_payload()}, "c2")]),
    ])
    events: list[dict] = []
    reply = TeamAdvisorAgent(agent_settings, llm=llm).chat("组队", event_sink=events.append)
    assert all(e["event"] != "thinking" for e in events)
    assert reply.thinking == []

    # 对照：默认 ChatAgent 仍发射 thinking（回归保障）
    chat_llm = ScriptedLLM([
        AIMessage(content="思考中", tool_calls=[tool_call("echo", {"text": "1+1"}, "c1")]),
        AIMessage(content="", tool_calls=[tool_call("final_answer", {"text": "2"}, "c2")]),
    ])
    chat_events: list[dict] = []
    ChatAgent(agent_settings, llm=chat_llm).chat("1+1", event_sink=chat_events.append)
    assert any(e["event"] == "thinking" for e in chat_events)


def test_advisor_tools_invoke(tmp_path):
    """工具包装器端到端可 invoke（@tool schema 含嵌套 list 类型）。"""
    tools = _build_advisor_registry(
        battles_dir=tmp_path, runs_dir=tmp_path).model_tools()
    by_name = {getattr(t, "name", t.name): t for t in tools}
    team = [
        {"spirit": "迪莫", "skills": ["闪光"]},
        {"spirit": "喵喵", "skills": ["抓挠"]},
        {"spirit": "火花", "skills": ["火苗"]},
    ]
    assert '"data_digest"' in by_name["get_catalog_version"].invoke({})
    assert "火" in by_name["search_spirits"].invoke(
        {"filters": [[{"field": "type", "op": "in", "value": "火"}]]})
    assert "迪莫" in by_name["get_spirit_profile"].invoke({"name": "迪莫"})
    assert "闪光" in by_name["get_skill_profile"].invoke({"name": "闪光"})
    assert "valid_natures" in by_name["get_build_options"].invoke({"name": "迪莫"})
    assert '"ok": true' in by_name["validate_team"].invoke({"team": team, "items": []})
    assert '"total_games": 0' in by_name["query_trajectory_evidence"].invoke({"kind": "selfplay"})
    assert "offensive_coverage" in by_name["analyze_team"].invoke({"team": team})
    assert "greedy" in by_name["simulate_matchups"].invoke(
        {"team": team, "opponents": [team], "seeds": [1]})
    skills = json.loads(by_name["retrieve_team_skill"].invoke({"query": "帮我组队"}))
    assert skills
    assert set(skills[0]["allowed_tools"]).issubset(by_name)
