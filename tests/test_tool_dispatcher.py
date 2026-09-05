"""阶段二：统一 ToolDispatcher 与结构化错误协议。"""

from __future__ import annotations

import json
import threading
import time

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict

from roco_pvp_agent.tooling import (
    DispatchContext,
    ToolCall,
    ToolConcurrency,
    ToolDispatcher,
    ToolEntry,
    ToolErrorCode,
    ToolOutcome,
    ToolRegistry,
)


class _StrictTextArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


@tool(args_schema=_StrictTextArgs)
def echo_text(text: str) -> str:
    """回显文本。"""

    return text


def _dispatcher(*entries: ToolEntry) -> ToolDispatcher:
    registry = ToolRegistry()
    for entry in entries:
        registry.register(entry)
    return ToolDispatcher(registry)


def _call(name: str, args: dict, call_id: str = "call-1") -> dict:
    return {"name": name, "args": args, "id": call_id}


def test_success_result_converts_to_tool_message_and_log():
    dispatcher = _dispatcher(ToolEntry(tool=echo_text, audit_tag="catalog"))
    result = dispatcher.dispatch(_call("echo_text", {"text": "你好"}), DispatchContext())

    assert result.ok is True
    assert result.content == "你好"
    assert result.error_code is None
    assert result.audit_tag == "catalog"
    message = result.to_tool_message()
    assert message.tool_call_id == "call-1"
    assert message.name == "echo_text"
    assert message.status == "success"
    log = result.to_log_record()
    assert log["args"] == {"text": "你好"}
    assert log["result"] == "你好"


def test_unknown_tool_is_a_structured_error():
    result = _dispatcher(ToolEntry(tool=echo_text)).dispatch(
        _call("missing", {}), DispatchContext())

    assert result.ok is False
    assert result.error_code is ToolErrorCode.UNKNOWN_TOOL
    assert json.loads(result.content)["error"]["code"] == "unknown_tool"
    assert result.to_tool_message().status == "error"


def test_malformed_call_is_rejected_before_lookup():
    result = _dispatcher(ToolEntry(tool=echo_text)).dispatch(
        {"name": "echo_text", "args": [], "id": "bad-1"}, DispatchContext())

    assert result.call.call_id == "bad-1"
    assert result.error_code is ToolErrorCode.INVALID_CALL
    assert json.loads(result.content)["error"]["code"] == "invalid_call"


def test_missing_wrong_and_extra_arguments_are_invalid_arguments():
    dispatcher = _dispatcher(ToolEntry(tool=echo_text))
    for args in ({}, {"text": ["不是字符串"]}, {"text": "ok", "typo": 1}):
        result = dispatcher.dispatch(_call("echo_text", args), DispatchContext())
        assert result.error_code is ToolErrorCode.INVALID_ARGUMENTS
        assert result.ok is False
        assert "input_value" not in result.content


def test_no_argument_tool_rejects_extra_fields_before_execution():
    calls = {"count": 0}

    class NoArgs(BaseModel):
        model_config = ConfigDict(extra="forbid")

    @tool(args_schema=NoArgs)
    def no_args() -> str:
        """不接收参数。"""

        calls["count"] += 1
        return "ok"

    result = _dispatcher(ToolEntry(tool=no_args)).dispatch(
        _call("no_args", {"unexpected": True}), DispatchContext())

    assert result.error_code is ToolErrorCode.INVALID_ARGUMENTS
    assert calls["count"] == 0


def test_handler_exception_is_hidden_from_model_observation():
    @tool
    def broken(text: str) -> str:
        """总是失败。"""

        raise RuntimeError("secret-path=/private/internal")

    result = _dispatcher(ToolEntry(tool=broken)).dispatch(
        _call("broken", {"text": "x"}), DispatchContext())

    assert result.error_code is ToolErrorCode.EXECUTION_ERROR
    assert "secret-path" not in result.content
    assert result.details == {"exception_type": "RuntimeError"}


def test_dict_result_is_serialized_stably():
    @tool
    def structured() -> dict:
        """返回结构化结果。"""

        return {"b": 2, "a": "中文"}

    result = _dispatcher(ToolEntry(tool=structured)).dispatch(
        _call("structured", {}), DispatchContext())
    assert result.content == '{"a": "中文", "b": 2}'


def test_rich_outcome_controls_terminal_and_details():
    @tool
    def finish(text: str) -> ToolOutcome:
        """提交终稿。"""

        return ToolOutcome(content=text, details={"kind": "answer"})

    result = _dispatcher(ToolEntry(
        tool=finish,
        terminal_on_success=True,
        audit_tag="terminal",
    )).dispatch(_call("finish", {"text": "完成"}), DispatchContext())

    assert result.terminal is True
    assert result.details == {"kind": "answer"}


def test_retry_budget_is_scoped_to_dispatch_context():
    @tool
    def submit() -> ToolOutcome:
        """返回可修复失败。"""

        return ToolOutcome(
            content='{"ok": false}',
            ok=False,
            error_code="advice_invalid",
            retryable=True,
        )

    dispatcher = _dispatcher(ToolEntry(
        tool=submit,
        terminal_on_success=True,
        retry_limit=1,
    ))
    context = DispatchContext()

    first = dispatcher.dispatch(_call("submit", {}, "c1"), context)
    second = dispatcher.dispatch(_call("submit", {}, "c2"), context)
    fresh = dispatcher.dispatch(_call("submit", {}, "c3"), DispatchContext())

    assert first.retryable is True and first.terminal is False
    assert json.loads(first.content)["error"]["code"] == "advice_invalid"
    assert second.retryable is False and second.retry_exhausted is True
    assert second.terminal is True
    assert fresh.retryable is True and fresh.terminal is False


def test_retry_exhausted_callback_can_produce_terminal_degradation():
    @tool
    def submit() -> ToolOutcome:
        """返回可修复失败。"""

        return ToolOutcome(
            content="校验失败",
            ok=False,
            error_code="advice_invalid",
            retryable=True,
            details={"errors": [{"code": "BAD_TEAM"}]},
        )

    def degrade(outcome: ToolOutcome) -> ToolOutcome:
        return ToolOutcome(
            content='{"ok": false, "degraded": true}',
            terminal_override=True,
            details=outcome.details,
        )

    dispatcher = _dispatcher(ToolEntry(
        tool=submit,
        terminal_on_success=True,
        retry_limit=1,
        on_retry_exhausted=degrade,
    ))
    context = DispatchContext()

    first = dispatcher.dispatch(_call("submit", {}, "c1"), context)
    second = dispatcher.dispatch(_call("submit", {}, "c2"), context)

    assert first.retryable is True and first.terminal is False
    assert second.ok is True and second.terminal is True
    assert second.retry_exhausted is True
    assert json.loads(second.content)["degraded"] is True


def test_invalid_retry_exhausted_callback_result_is_contained():
    @tool
    def submit() -> ToolOutcome:
        """返回可修复失败。"""

        return ToolOutcome(content="失败", ok=False, retryable=True)

    dispatcher = _dispatcher(ToolEntry(
        tool=submit,
        terminal_on_success=True,
        retry_limit=1,
        on_retry_exhausted=lambda outcome: "wrong-type",  # type: ignore[arg-type,return-value]
    ))
    context = DispatchContext()
    dispatcher.dispatch(_call("submit", {}, "c1"), context)
    result = dispatcher.dispatch(_call("submit", {}, "c2"), context)

    assert result.terminal is True
    assert result.error_code == ToolErrorCode.EXECUTION_ERROR.value
    assert "wrong-type" not in result.content


def test_hidden_tool_is_rejected_without_execution():
    calls = {"count": 0}

    @tool
    def hidden() -> str:
        """不可见工具。"""

        calls["count"] += 1
        return "执行了"

    result = _dispatcher(ToolEntry(tool=hidden)).dispatch(
        _call("hidden", {}), DispatchContext(visible_tool_names={"echo_text"}))

    assert result.error_code is ToolErrorCode.NOT_LOADED
    assert calls["count"] == 0


def test_output_is_truncated_to_registered_limit():
    @tool
    def verbose() -> str:
        """返回很长文本。"""

        return "x" * 100

    result = _dispatcher(ToolEntry(tool=verbose, max_output_chars=40)).dispatch(
        _call("verbose", {}), DispatchContext())

    assert len(result.content) == 40
    assert result.truncated is True
    assert "工具输出已截断" in result.content


def test_dispatch_many_fulfils_calls_after_terminal_without_execution():
    executed: list[str] = []

    @tool
    def finish() -> str:
        """结束。"""

        executed.append("finish")
        return "完成"

    @tool
    def later() -> str:
        """不应执行。"""

        executed.append("later")
        return "晚了"

    dispatcher = _dispatcher(
        ToolEntry(tool=finish, terminal_on_success=True),
        ToolEntry(tool=later),
    )
    results = dispatcher.dispatch_many([
        _call("finish", {}, "finish-id"),
        _call("later", {}, "later-id"),
    ], DispatchContext())

    assert executed == ["finish"]
    assert [result.call.call_id for result in results] == ["finish-id", "later-id"]
    assert results[1].error_code is ToolErrorCode.SKIPPED_AFTER_TERMINAL
    assert results[1].to_tool_message().tool_call_id == "later-id"


def test_all_concurrent_safe_batch_runs_in_parallel_and_preserves_result_order():
    state_lock = threading.Lock()
    both_started = threading.Event()
    running = 0
    max_running = 0

    @tool
    def read_value(value: str) -> str:
        """模拟可安全并发的只读查询。"""

        nonlocal running, max_running
        with state_lock:
            running += 1
            max_running = max(max_running, running)
            if running == 2:
                both_started.set()
        try:
            if not both_started.wait(0.5):
                raise RuntimeError("第二个只读调用没有并发启动")
            if value == "first":
                time.sleep(0.03)
            return value
        finally:
            with state_lock:
                running -= 1

    dispatcher = _dispatcher(ToolEntry(
        tool=read_value,
        concurrency=ToolConcurrency.CONCURRENT_SAFE,
    ))
    results = dispatcher.dispatch_many([
        _call("read_value", {"value": "first"}, "c1"),
        _call("read_value", {"value": "second"}, "c2"),
    ], DispatchContext())

    assert max_running == 2
    assert [result.call.call_id for result in results] == ["c1", "c2"]
    assert [result.content for result in results] == ["first", "second"]


def test_one_serial_tool_forces_the_entire_batch_to_run_in_order():
    events: list[str] = []

    @tool
    def write_value(value: str) -> str:
        """模拟写操作。"""

        events.append(f"write:{value}")
        return value

    @tool
    def read_value(value: str) -> str:
        """模拟只读操作。"""

        events.append(f"read:{value}")
        return value

    dispatcher = _dispatcher(
        ToolEntry(tool=write_value),
        ToolEntry(tool=read_value, concurrency=ToolConcurrency.CONCURRENT_SAFE),
    )
    results = dispatcher.dispatch_many([
        _call("write_value", {"value": "a"}, "c1"),
        _call("read_value", {"value": "b"}, "c2"),
    ], DispatchContext())

    assert events == ["write:a", "read:b"]
    assert [result.call.call_id for result in results] == ["c1", "c2"]


def test_tool_timeout_returns_without_exposing_late_result():
    release = threading.Event()

    @tool
    def slow() -> str:
        """等待外部释放。"""

        release.wait(1)
        return "迟到结果"

    dispatcher = _dispatcher(ToolEntry(tool=slow, timeout_seconds=0.02))
    started = time.monotonic()
    result = dispatcher.dispatch(_call("slow", {}), DispatchContext())
    elapsed = time.monotonic() - started
    release.set()

    assert result.error_code is ToolErrorCode.TIMEOUT
    assert elapsed < 0.5
    assert "迟到结果" not in result.content


def test_expired_global_deadline_does_not_start_handler():
    calls = {"count": 0}

    @tool
    def untouched() -> str:
        """不应启动。"""

        calls["count"] += 1
        return "unexpected"

    result = _dispatcher(ToolEntry(tool=untouched)).dispatch(
        _call("untouched", {}),
        DispatchContext(deadline=time.monotonic() - 1),
    )

    assert result.error_code is ToolErrorCode.TIMEOUT
    assert calls["count"] == 0


def test_normalized_tool_call_can_be_dispatched_directly():
    call = ToolCall(call_id="direct", name="echo_text", arguments={"text": "ok"})
    result = _dispatcher(ToolEntry(tool=echo_text)).dispatch(call, DispatchContext())
    assert result.content == "ok"
