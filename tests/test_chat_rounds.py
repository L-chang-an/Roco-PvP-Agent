"""Real loop/dispatcher contracts; no real provider or extra summary calls."""
import json
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from roco_pvp_agent.agent import ChatAgent, EMPTY_REPLY
from roco_pvp_agent.advisor.agent import TeamAdvisorAgent, _build_advisor_registry
from roco_pvp_agent.advisor.presentation import present_tool
from roco_pvp_agent.events import split_round_summary
from roco_pvp_agent.tooling import DispatchContext, ToolDispatcher, ToolEntry, ToolRegistry, ToolConcurrency, ToolVisibility
from fakes import ScriptedLLM, tool_call


@pytest.mark.parametrize("content,summary,answer", [
    ("<round_summary>核对档案</round_summary>终稿", "核对档案", "终稿"),
    ("普通答案", None, "普通答案"),
    ("<round_summary>没有闭合", None, ""),
    ("<round_summary>a</round_summary><round_summary>b</round_summary>答", None, "答"),
    ("<round_summary>\n一\n二\n三\n四\n</round_summary>", "一\n二\n三", ""),
    ("<round_summary>" + "字" * 300 + "</round_summary>", "字" * 240, ""),
])
def test_summary_parser(content, summary, answer):
    assert split_round_summary(content) == (summary, answer)


def test_three_rounds_same_response_summaries_and_no_reasoning(agent_settings):
    llm = ScriptedLLM([
        AIMessage(content=[{"type": "thinking", "thinking": "PRIVATE_SENTINEL"},
                           {"type": "text", "text": "<round_summary>核对数据版本和档案。</round_summary>"}],
                  additional_kwargs={"reasoning_content": "PRIVATE_SENTINEL"},
                  tool_calls=[tool_call("get_catalog_version", {}, "same"),
                              tool_call("get_spirit_profile", {"name": "迪莫"}, "same")]),
        AIMessage(content="<round_summary>查询技能属性。</round_summary>",
                  tool_calls=[tool_call("get_skill_profile", {"name": "闪光"})]),
        AIMessage(content="<round_summary>证据已取得，整理回答。</round_summary>最终回答"),
    ])
    events, legacy = [], []
    reply = TeamAdvisorAgent(agent_settings, llm=llm).chat("帮我配招", execution_observer=events.append, event_sink=legacy.append)
    assert llm.invocations == reply.rounds == 3
    assert reply.reply == "最终回答"
    assert [e.round_index for e in events if e.event == "round.started"] == [1, 2, 3]
    assert len([e for e in events if e.event == "round.completed"]) == 3
    assert len([e for e in events if e.event == "round.summary"]) == 3
    assert "PRIVATE_SENTINEL" not in repr(events) + repr(legacy) + reply.reply
    completed = [e for e in events if e.event == "tool.completed"]
    assert len({e.payload["tool_execution_id"] for e in completed}) == 3
    assert [m.tool_call_id for m in reply.history if m.type == "tool"][:2] == ["same", "same"]


def test_summary_only_empty_and_thinking_only_are_not_final_answers(agent_settings):
    for content in ("<round_summary>分析目标</round_summary>", [{"type": "thinking", "thinking": "PRIVATE_SENTINEL"}]):
        llm = ScriptedLLM([AIMessage(content=content)])
        reply = TeamAdvisorAgent(agent_settings, llm=llm).chat("帮我配招")
        assert reply.reply == EMPTY_REPLY
        assert reply.final_result.status == "degraded"
        assert llm.invocations == 1


def test_parallel_completion_is_live_but_results_stay_ordered():
    slow_entered, release = threading.Event(), threading.Event()

    @tool
    def slow() -> str:
        """Wait for a controlled release."""
        slow_entered.set()
        assert release.wait(2)
        return "slow"

    @tool
    def fast() -> str:
        """Finish while the earlier call is still running."""
        assert slow_entered.wait(2)
        return "fast"

    registry = ToolRegistry()
    for entry in (slow, fast):
        registry.register(ToolEntry(tool=entry, concurrency=ToolConcurrency.CONCURRENT_SAFE))
    events, results = [], []
    fast_complete = threading.Event()
    def observer(e):
        events.append(e)
        if e.event == "tool.completed" and e.result.call.name == "fast":
            fast_complete.set()
    thread = threading.Thread(target=lambda: results.extend(ToolDispatcher(registry).dispatch_many(
        [tool_call("slow", {}), tool_call("fast", {})], DispatchContext(observer=observer))))
    thread.start()
    try:
        assert fast_complete.wait(1)
        assert thread.is_alive()
        assert [e.result.call.name for e in events if e.event == "tool.completed"] == ["fast"]
    finally:
        release.set(); thread.join(2)
    assert [r.call.name for r in results] == ["slow", "fast"]


def test_terminal_skips_and_invalid_calls_have_no_started_event():
    registry = _build_advisor_registry()
    events = []
    results = ToolDispatcher(registry).dispatch_many([
        tool_call("unknown", {}), tool_call("get_catalog_version", {"extra": 1}),
        tool_call("final_answer", {"text": "done"}), tool_call("get_catalog_version", {}),
    ], DispatchContext(observer=events.append))
    assert len([e for e in events if e.event == "tool.started"]) == 1
    completed = [e for e in events if e.event == "tool.completed"]
    assert [e.payload["status"] for e in completed] == ["failed", "failed", "completed", "skipped"]
    assert all(completed[i].payload["duration_ms"] is None for i in (0, 1, 3))
    assert len(results) == 4


def test_cancel_during_model_wait_closes_one_round(agent_settings):
    entered, release, cancel = threading.Event(), threading.Event(), threading.Event()
    class Waiting:
        def invoke(self, messages):
            entered.set(); release.wait(2)
            return AIMessage(content="late")
    events, replies = [], []
    agent = TeamAdvisorAgent(agent_settings, llm=Waiting())
    thread = threading.Thread(target=lambda: replies.append(agent.chat("帮我组队", cancel_event=cancel, execution_observer=events.append)))
    thread.start(); assert entered.wait(1)
    cancel.set(); thread.join(0.5); release.set()
    assert not thread.is_alive()
    assert replies[0].final_result.status == "cancelled"
    assert [e.payload["status"] for e in events if e.event == "round.completed"] == ["cancelled"]
    assert "late" not in replies[0].reply


def test_full_default_budget_and_no_phantom_101st_round(agent_settings):
    class Loop:
        n = 0
        def invoke(self, messages, **kwargs):
            self.n += 1
            assert kwargs["timeout"] > 60
            return AIMessage(content="<round_summary>继续核对。</round_summary>", tool_calls=[tool_call("get_catalog_version", {})])
    llm, events = Loop(), []
    reply = TeamAdvisorAgent(agent_settings, llm=llm).chat("帮我组队", execution_observer=events.append)
    assert llm.n == reply.rounds == 100
    assert len([e for e in events if e.event == "round.started"]) == 100
    assert reply.final_result.reason_code == "rounds"


def test_555_seconds_includes_inflight_wait_and_allows_past_60(agent_settings, monkeypatch):
    import roco_pvp_agent.agent as module
    clock = [1000.0]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    class Timed:
        n = 0
        def invoke(self, messages, **kwargs):
            self.n += 1
            assert kwargs["timeout"] == pytest.approx(555 - (self.n - 1) * 70)
            clock[0] += 70
            return AIMessage(content="", tool_calls=[tool_call("get_catalog_version", {})])
    llm, events = Timed(), []
    agent = TeamAdvisorAgent(agent_settings, llm=llm)
    monkeypatch.setattr(agent._dispatcher, "dispatch_many", lambda calls, context: [])
    reply = agent.chat("帮我组队", execution_observer=events.append)
    assert llm.n == reply.rounds == 8
    assert reply.final_result.status == "timed_out"
    assert [e for e in events if e.event == "round.completed"][-1].payload["status"] == "timed_out"


@pytest.mark.parametrize("message", ["你好", "天气怎么样", "把第二只换掉"])
def test_scope_templates_have_zero_rounds(agent_settings, message):
    events = []
    reply = TeamAdvisorAgent(agent_settings, llm=ScriptedLLM([])).chat(message, execution_observer=events.append)
    assert reply.rounds == 0 and events == []


@pytest.mark.parametrize("name,args,status", [
    ("get_spirit_profile", {"name": "不存在的精灵"}, "not_found"),
    ("validate_team", {"team": [{"spirit": "不存在", "skills": []}]}, "invalid"),
    ("validate_team", {"team": []}, "empty"),
    ("tool_search", {"tool_names": ["unknown"]}, "not_found"),
])
def test_business_outcomes_are_not_transport_success(name, args, status):
    registry = _build_advisor_registry()
    result = ToolDispatcher(registry).dispatch(tool_call(name, args), DispatchContext(visibility=ToolVisibility(registry)))
    presentation, details = present_tool(result)
    assert result.ok
    assert presentation["domain_status"] == status
    truncated = replace(result, truncated=True, content='{"broken"')
    assert present_tool(truncated)[0]["domain_status"] == "truncated"


def test_full_team_payload_survives_tool_text_limit():
    from test_advisor_tool_schemas import _valid_payload
    from roco_pvp_agent.advisor.advice import submit_team_advice
    payload = _valid_payload()
    for count in (0, 2):
        invalid = {**payload, "team": payload["team"][:count]}
        assert not submit_team_advice(invalid)["ok"]
    payload["synergy"] = "长说明" * 8000
    result = ToolDispatcher(_build_advisor_registry()).dispatch(tool_call("submit_team_advice", {"payload": payload}), DispatchContext())
    assert result.ok and result.truncated
    assert result.final_result.advice["synergy"] == payload["synergy"]
    assert '主队配置已通过校验' in result.final_result.message
    assert len(result.final_result.message) < 240
    presentation, details = present_tool(result)
    assert presentation["domain_status"] == "valid"
    assert "synergy" not in json.dumps(details)


def test_six_person_intermediate_and_final_validation_agree():
    from environment.dataset import load_spirits, DataSource
    from test_advisor_tool_schemas import _valid_payload
    from roco_pvp_agent.advisor.advice import submit_team_advice
    team, families = [], set()
    for spirit in load_spirits(DataSource.VALID).values():
        if spirit.family_key in families or spirit.is_boss or not spirit.skills_default:
            continue
        families.add(spirit.family_key)
        team.append({"spirit": spirit.name, "skills": [spirit.skills_default[0]]})
        if len(team) == 6:
            break
    dispatcher = ToolDispatcher(_build_advisor_registry())
    correct = dispatcher.dispatch(tool_call("validate_team", {"team": team, "team_size": 6}), DispatchContext())
    wrong = dispatcher.dispatch(tool_call("validate_team", {"team": team}), DispatchContext())
    assert json.loads(correct.content)["ok"]
    assert not json.loads(wrong.content)["ok"]
    payload = _valid_payload()
    payload.update(team=team, rules_used={"team_size": 6, "lives": 2, "source": "VALID"})
    assert submit_team_advice(payload)["ok"]
    payload["team"] = team[:3]
    assert not submit_team_advice(payload)["ok"]


def test_failed_terminal_retry_is_retained_before_success(agent_settings):
    from test_advisor_tool_schemas import _valid_payload
    bad = _valid_payload(); bad["team"][0]["spirit"] = "不存在"
    llm = ScriptedLLM([
        AIMessage(content="<round_summary>提交候选，等待校验。</round_summary>", tool_calls=[tool_call("submit_team_advice", {"payload": bad})]),
        AIMessage(content="<round_summary>按校验结果修复候选。</round_summary>", tool_calls=[tool_call("submit_team_advice", {"payload": _valid_payload()})]),
    ])
    events = []
    reply = TeamAdvisorAgent(agent_settings, llm=llm).chat("帮我组队", execution_observer=events.append)
    complete = [e for e in events if e.event == "tool.completed"]
    assert [present_tool(e.result)[0]["domain_status"] for e in complete] == ["invalid", "valid"]
    assert reply.final_result.kind == "team_advice"
    assert len({e.payload["tool_execution_id"] for e in complete}) == 2


def test_expired_budget_before_invoke_creates_no_round(agent_settings, monkeypatch):
    import roco_pvp_agent.agent as module
    clock = [0.0]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    llm, events = ScriptedLLM([]), []
    agent = TeamAdvisorAgent(agent_settings, llm=llm)
    def prepare(names):
        clock[0] = 556
        return llm
    monkeypatch.setattr(agent, "_get_llm", prepare)
    reply = agent.chat("帮我组队", execution_observer=events.append)
    assert reply.rounds == llm.invocations == 0
    assert events == [] and reply.final_result.status == "timed_out"
