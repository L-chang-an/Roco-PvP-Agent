"""安全延迟加载：目录 → tool_search → 下一轮直接执行。"""

from __future__ import annotations

from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict

from fakes import ScriptedLLM, tool_call
from roco_pvp_agent import agent as agent_module
from roco_pvp_agent.advisor.agent import _build_advisor_registry
from roco_pvp_agent.agent import ChatAgent
from roco_pvp_agent.config import Settings
from roco_pvp_agent.tooling import (
    DispatchContext,
    ToolConcurrency,
    ToolDispatcher,
    ToolEntry,
    ToolErrorCode,
    ToolExposure,
    ToolRegistry,
    ToolVisibility,
    enable_deferred_loading,
)
from ui.context import ChatContext


class _TextArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str


@tool(args_schema=_TextArgs)
def rare_lookup(text: str) -> str:
    """查询低频数据。"""

    return text.upper()


@tool(args_schema=_TextArgs)
def complete(text: str) -> str:
    """提交最终答案。"""

    return text


def _registry(*, calls: list[str] | None = None) -> ToolRegistry:
    deferred = rare_lookup
    if calls is not None:
        @tool(args_schema=_TextArgs)
        def tracked_lookup(text: str) -> str:
            """查询低频数据。"""

            calls.append(text)
            return text.upper()

        deferred = tracked_lookup.model_copy(update={"name": "rare_lookup"})

    registry = ToolRegistry()
    registry.register(ToolEntry(tool=complete, terminal_on_success=True))
    registry.register(ToolEntry(
        tool=deferred,
        exposure=ToolExposure.DEFERRED,
        concurrency=ToolConcurrency.CONCURRENT_SAFE,
        directory_description="查询稀有的低频数据。",
    ))
    enable_deferred_loading(registry)
    return registry


def _dispatch_context(visibility: ToolVisibility) -> DispatchContext:
    return DispatchContext(
        visible_tool_names=set(visibility.visible_names()),
        visibility=visibility,
    )


def test_advisor_registry_assigns_real_concurrency_and_visibility_policies():
    registry = _build_advisor_registry()

    assert {entry.name for entry in registry.deferred_entries()} == {
        "query_trajectory_evidence",
        "analyze_team",
        "simulate_matchups",
        "retrieve_team_skill",
        "query_global_mem",
        "query_local_mem",
    }
    assert all(
        entry.concurrency is ToolConcurrency.CONCURRENT_SAFE
        for entry in registry.entries()
        if not entry.terminal_on_success and entry.name != "tool_search"
    )
    assert registry.get("tool_search").exposure is ToolExposure.IMMEDIATE
    assert registry.get("tool_search").concurrency is ToolConcurrency.SERIAL


def test_startup_directory_is_compact_and_initial_visibility_excludes_schema():
    registry = _registry()
    visibility = ToolVisibility(registry)

    assert registry.deferred_directory() == [
        {"name": "rare_lookup", "description": "查询稀有的低频数据。"},
    ]
    assert visibility.visible_names() == frozenset({"complete", "tool_search"})
    prompt = visibility.directory_prompt()
    assert "rare_lookup" in prompt
    assert "查询稀有" in prompt
    assert "properties" not in prompt and "required" not in prompt


def test_exact_search_loads_full_schema_once_and_reports_missing_names():
    registry = _registry()
    visibility = ToolVisibility(registry)

    first = visibility.load_by_name([
        "rare_lookup", "complete", "missing", "rare_lookup",
    ])
    second = visibility.load_by_name(["rare_lookup"])

    assert [match.name for match in first.matches] == ["rare_lookup"]
    assert first.matches[0].cache_hit is False
    assert first.matches[0].schema["input_schema"]["required"] == ["text"]
    assert first.missing == ("complete", "missing")
    assert second.matches[0].cache_hit is True
    assert visibility.loaded_names() == ("rare_lookup",)


def test_keyword_search_is_stable_and_miss_does_not_pollute_session():
    registry = ToolRegistry()
    for name in ("zeta_lookup", "alpha_lookup"):
        registry.register(ToolEntry(
            tool=rare_lookup.model_copy(update={"name": name}),
            exposure=ToolExposure.DEFERRED,
            directory_description="查询稀有数据。",
        ))
    visibility = ToolVisibility(registry)

    miss = visibility.search(["日历"], top_k=2)
    first = visibility.search(["稀有"], top_k=2)
    second = visibility.search(["稀有"], top_k=2)

    assert miss.matches == () and miss.missing == ("日历",)
    assert [match.name for match in first.matches] == ["alpha_lookup", "zeta_lookup"]
    assert all(match.cache_hit is False for match in first.matches)
    assert all(match.cache_hit is True for match in second.matches)


def test_deferred_tool_cannot_execute_before_search():
    calls: list[str] = []
    registry = _registry(calls=calls)
    visibility = ToolVisibility(registry)
    dispatcher = ToolDispatcher(registry)

    denied = dispatcher.dispatch(
        tool_call("rare_lookup", {"text": "secret"}, "d1"),
        _dispatch_context(visibility),
    )

    assert denied.error_code is ToolErrorCode.NOT_LOADED
    assert calls == []


def test_search_then_execute_uses_original_registry_handler_and_schema():
    calls: list[str] = []
    registry = _registry(calls=calls)
    visibility = ToolVisibility(registry)
    dispatcher = ToolDispatcher(registry)

    loaded = dispatcher.dispatch(
        tool_call("tool_search", {"tool_names": ["rare_lookup"]}, "s1"),
        _dispatch_context(visibility),
    )
    executed = dispatcher.dispatch(
        tool_call("rare_lookup", {"text": "hello"}, "d1"),
        _dispatch_context(visibility),
    )

    assert loaded.ok is True
    assert executed.ok is True and executed.content == "HELLO"
    assert calls == ["hello"]


def test_search_and_guessed_execution_in_same_model_batch_is_rejected():
    calls: list[str] = []
    registry = _registry(calls=calls)
    visibility = ToolVisibility(registry)
    dispatcher = ToolDispatcher(registry)
    context = _dispatch_context(visibility)  # 本轮开始时的可见性快照

    results = dispatcher.dispatch_many([
        tool_call("tool_search", {"tool_names": ["rare_lookup"]}, "s1"),
        tool_call("rare_lookup", {"text": "guessed"}, "d1"),
    ], context)

    assert results[0].ok is True
    assert results[1].error_code is ToolErrorCode.NOT_LOADED
    assert visibility.loaded_names() == ("rare_lookup",)
    assert calls == []


def test_loaded_state_is_isolated_between_sessions():
    registry = _registry()
    first = ToolVisibility(registry)
    second = ToolVisibility(registry)

    first.load_by_name(["rare_lookup"])

    assert "rare_lookup" in first.visible_names()
    assert "rare_lookup" not in second.visible_names()


class _RecordingScriptedLLM(ScriptedLLM):
    def __init__(self, replies):
        super().__init__(replies)
        self.messages: list[list] = []

    def invoke(self, messages):
        self.messages.append(list(messages))
        return super().invoke(messages)


def test_agent_rebinds_real_llm_from_session_visible_schema(monkeypatch):
    registry = _registry()
    llm = _RecordingScriptedLLM([
        AIMessage(content="", tool_calls=[
            tool_call("tool_search", {"tool_names": ["rare_lookup"]}, "s1")]),
        AIMessage(content="", tool_calls=[
            tool_call("rare_lookup", {"text": "hello"}, "d1")]),
        AIMessage(content="", tool_calls=[
            tool_call("complete", {"text": "done"}, "f1")]),
    ])
    bindings: list[tuple[tuple[str, ...], str]] = []

    def fake_build(_settings, tools, *, schema_digest=None):
        bindings.append((tuple(tool.name for tool in tools), schema_digest))
        return llm

    monkeypatch.setattr(agent_module, "build_chat_llm", fake_build)
    agent = ChatAgent(
        Settings(api_key="sk-test", base_url="http://test.invalid"),
        registry=registry,
        max_llm_rounds=3,
    )
    reply = agent.chat("查询低频数据")

    assert reply.reply == "done"
    assert "rare_lookup" not in bindings[0][0]
    assert "rare_lookup" in bindings[1][0]
    assert bindings[1][1] == bindings[2][1]
    assert bindings[0][1] != bindings[1][1]
    assert reply.loaded_tools == ("rare_lookup",)
    assert "延迟工具目录" in llm.messages[0][0].content
    assert "延迟工具目录" not in llm.messages[1][0].content


def test_chat_context_persists_and_reset_clears_loaded_tools():
    registry = _registry()
    llm = ScriptedLLM([
        # 第一次请求：加载后终结。
        AIMessage(content="", tool_calls=[
            tool_call("tool_search", {"tool_names": ["rare_lookup"]}, "s1")]),
        AIMessage(content="", tool_calls=[
            tool_call("complete", {"text": "loaded"}, "f1")]),
        # 第二次请求：同一会话可直接调用。
        AIMessage(content="", tool_calls=[
            tool_call("rare_lookup", {"text": "again"}, "d2")]),
        AIMessage(content="", tool_calls=[
            tool_call("complete", {"text": "reused"}, "f2")]),
        # reset 后：直接调用再次被拒绝。
        AIMessage(content="", tool_calls=[
            tool_call("rare_lookup", {"text": "after-reset"}, "d3")]),
        AIMessage(content="", tool_calls=[
            tool_call("complete", {"text": "blocked then finished"}, "f3")]),
    ])
    context = ChatContext(ChatAgent(
        Settings(api_key="sk-test", base_url="http://test.invalid"),
        llm=llm,
        registry=registry,
        max_llm_rounds=2,
    ))
    session_id = context.new_session()

    first = context.chat("load", session_id)
    second = context.chat("reuse", session_id)
    context.reset(session_id)
    third = context.chat("after reset", session_id)

    assert first.loaded_tools == ("rare_lookup",)
    assert second.tool_calls[0]["ok"] is True
    assert third.tool_calls[0]["error_code"] == "not_loaded"
