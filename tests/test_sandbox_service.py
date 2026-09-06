"""权限到 ToolOutcome、证据与 Dispatcher 脱敏/取消的集成测试。"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from pathlib import Path

from langchain_core.messages import AIMessage

from fakes import ScriptedLLM, tool_call
from roco_pvp_agent.advisor.agent import TeamAdvisorAgent, _build_advisor_registry
from roco_pvp_agent.config import Settings
from roco_pvp_agent.sandbox.models import (
    SandboxLimits,
    SandboxExecutionResult,
    SandboxHealth,
)
from roco_pvp_agent.sandbox.service import SandboxQueryService, SandboxSetup
from roco_pvp_agent.sandbox.tool import SandboxPythonQueryTool
from roco_pvp_agent.tooling import (
    DispatchContext,
    ToolConcurrency,
    ToolDispatcher,
    ToolEntry,
    ToolErrorCode,
    ToolExposure,
    ToolRegistry,
)


class _FakeBackend:
    def __init__(self, result: SandboxExecutionResult | None = None) -> None:
        self.health = SandboxHealth(True, "macos", "macos", True, architecture="arm64")
        self.result = result or SandboxExecutionResult(ok=True, result={"count": 2}, duration_ms=1.5)
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        return self.result


class _CancellingBackend(_FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.cleaned = threading.Event()

    def execute(self, request):
        while not request.control.cancel_event.wait(0.01):
            pass
        self.cleaned.set()
        return SandboxExecutionResult(ok=False, error_code="sandbox_timeout")


class _ConcurrencyBackend(_FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def execute(self, request):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.05)
        with self.lock:
            self.active -= 1
        return self.result


def _data_root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    (root / "full_spirits.json").write_text('[{"name":"迪莫"}]', encoding="utf-8")
    return root


def _dispatcher(service: SandboxQueryService, *, timeout: float = 10.0) -> ToolDispatcher:
    registry = ToolRegistry()
    registry.register(ToolEntry(
        tool=SandboxPythonQueryTool(service),
        concurrency=ToolConcurrency.SERIAL,
        retry_limit=1,
        timeout_seconds=timeout,
        sensitive_arguments=frozenset({"code"}),
        cooperative_cancellation=True,
    ))
    return ToolDispatcher(registry)


def test_success_envelope_has_evidence_and_only_selected_snapshot(tmp_path: Path):
    backend = _FakeBackend()
    service = SandboxQueryService(backend, data_root=_data_root(tmp_path))
    outcome = service.execute(
        code="emit_result(1)", dataset_ids=["full_spirits"],
        deadline=None, cancel_event=threading.Event(),
    )
    payload = json.loads(outcome.content)
    assert outcome.ok is True
    assert payload["backend"] == "macos"
    assert payload["dataset_ids"] == ["full_spirits"]
    assert payload["evidence_id"] == (
        f"sandbox:{payload['data_digest']}:{payload['run_id']}")
    assert set(backend.requests[0].dataset_paths) == {"full_spirits"}
    assert all(not path.exists() for path in backend.requests[0].dataset_paths.values())


def test_advisor_registry_adds_only_healthy_service_as_deferred_serial_tool(tmp_path: Path):
    service = SandboxQueryService(_FakeBackend(), data_root=_data_root(tmp_path))
    registry = _build_advisor_registry(sandbox_service=service)
    entry = registry.get("sandbox_python_query")
    assert entry is not None
    assert entry.exposure is ToolExposure.DEFERRED
    assert entry.concurrency is ToolConcurrency.SERIAL
    assert entry.retry_limit == 1
    assert entry.timeout_seconds == 120.0
    assert entry.timeout_seconds == SandboxLimits().wall_seconds
    assert entry.max_output_chars == 20_000
    assert entry.sensitive_arguments == frozenset({"code"})
    assert "sandbox_python_query" not in {
        item.name for item in registry.immediate_entries()}


def test_dispatcher_redacts_code_from_log(tmp_path: Path):
    service = SandboxQueryService(_FakeBackend(), data_root=_data_root(tmp_path))
    code = "emit_result(123)"
    result = _dispatcher(service).dispatch(
        {"id": "c1", "name": "sandbox_python_query", "args": {
            "code": code, "dataset_ids": ["full_spirits"],
        }},
        DispatchContext(),
    )
    logged = result.to_log_record()
    assert result.ok is True
    assert code not in json.dumps(logged, ensure_ascii=False)
    assert logged["args"]["code"] == {
        "redacted": True,
        "chars": len(code),
        "sha256": hashlib.sha256(code.encode()).hexdigest(),
    }


def test_unknown_tool_with_code_argument_is_still_redacted(tmp_path: Path):
    service = SandboxQueryService(_FakeBackend(), data_root=_data_root(tmp_path))
    code = "emit_result('must-not-leak')"
    result = _dispatcher(service).dispatch(
        {"id": "c1", "name": "sandbox_python_typo", "args": {"code": code}},
        DispatchContext(),
    )
    assert result.error_code is ToolErrorCode.UNKNOWN_TOOL
    assert code not in json.dumps(result.to_log_record(), ensure_ascii=False)


def test_chat_reply_and_sse_tool_event_never_contain_code(tmp_path: Path):
    backend = _FakeBackend()
    service = SandboxQueryService(backend, data_root=_data_root(tmp_path))
    setup = SandboxSetup(service=service, health=backend.health)
    code = "rows=load_dataset('full_spirits')\nemit_result(len(rows))"
    llm = ScriptedLLM([
        AIMessage(content="", tool_calls=[tool_call(
            "tool_search", {"tool_names": ["sandbox_python_query"]}, "s1")]),
        AIMessage(content="", tool_calls=[tool_call(
            "sandbox_python_query",
            {"code": code, "dataset_ids": ["full_spirits"]}, "s2")]),
        AIMessage(content="", tool_calls=[tool_call(
            "final_answer", {"text": "统计完成"}, "s3")]),
    ])
    events = []
    reply = TeamAdvisorAgent(
        Settings(api_key="test"), llm=llm, sandbox_setup=setup,
    ).chat("统计精灵数据", event_sink=events.append)
    serialized = json.dumps(
        {"tool_calls": reply.tool_calls, "events": events}, ensure_ascii=False)
    assert code not in serialized
    sandbox_log = next(
        item for item in reply.tool_calls if item["name"] == "sandbox_python_query")
    assert sandbox_log["args"]["code"]["redacted"] is True


def test_policy_denial_does_not_invoke_backend_and_is_not_retryable(tmp_path: Path):
    backend = _FakeBackend()
    result = _dispatcher(SandboxQueryService(
        backend, data_root=_data_root(tmp_path))).dispatch(
        {"id": "c1", "name": "sandbox_python_query", "args": {
            "code": "import os\nemit_result(1)", "dataset_ids": ["full_spirits"],
        }},
        DispatchContext(),
    )
    assert result.error_code == "sandbox_policy_denied"
    assert result.retryable is False
    assert backend.requests == []


def test_audit_log_contains_only_code_digest_and_length(tmp_path: Path, caplog):
    backend = _FakeBackend()
    service = SandboxQueryService(backend, data_root=_data_root(tmp_path))
    code = "secret_marker = 7\nemit_result(secret_marker)"
    caplog.set_level(logging.INFO, logger="roco_pvp_agent.sandbox.audit")
    service.execute(
        code=code, dataset_ids=["full_spirits"],
        deadline=None, cancel_event=threading.Event(),
    )
    combined = "\n".join(record.getMessage() for record in caplog.records)
    assert code not in combined and "secret_marker" not in combined
    assert hashlib.sha256(code.encode()).hexdigest() in combined
    assert f'"code_chars": {len(code)}' in combined


def test_ordinary_execution_error_gets_one_repair_budget(tmp_path: Path):
    backend = _FakeBackend(SandboxExecutionResult(
        ok=False, error_code="sandbox_execution_error"))
    dispatcher = _dispatcher(SandboxQueryService(backend, data_root=_data_root(tmp_path)))
    context = DispatchContext()
    args = {"code": "emit_result(1)", "dataset_ids": ["full_spirits"]}
    first = dispatcher.dispatch(
        {"id": "c1", "name": "sandbox_python_query", "args": args}, context)
    second = dispatcher.dispatch(
        {"id": "c2", "name": "sandbox_python_query", "args": args}, context)
    assert first.retryable is True
    assert second.retryable is False and second.retry_exhausted is True


def test_dispatch_timeout_signals_backend_and_waits_for_cleanup(tmp_path: Path):
    backend = _CancellingBackend()
    service = SandboxQueryService(backend, data_root=_data_root(tmp_path))
    started = time.monotonic()
    result = _dispatcher(service, timeout=0.08).dispatch(
        {"id": "c1", "name": "sandbox_python_query", "args": {
            "code": "emit_result(1)", "dataset_ids": ["full_spirits"],
        }},
        DispatchContext(),
    )
    assert result.error_code is ToolErrorCode.TIMEOUT
    assert backend.cleaned.wait(0.2)
    assert time.monotonic() - started < 0.5


def test_external_cancel_signals_backend_within_cleanup_grace(tmp_path: Path):
    backend = _CancellingBackend()
    service = SandboxQueryService(backend, data_root=_data_root(tmp_path))
    context = DispatchContext()
    timer = threading.Timer(0.05, context.cancel_event.set)
    timer.start()
    started = time.monotonic()
    try:
        result = _dispatcher(service, timeout=5.0).dispatch(
            {"id": "c1", "name": "sandbox_python_query", "args": {
                "code": "emit_result(1)", "dataset_ids": ["full_spirits"],
            }},
            context,
        )
    finally:
        timer.cancel()
    assert result.error_code is ToolErrorCode.TIMEOUT
    assert backend.cleaned.wait(0.2)
    assert time.monotonic() - started < 0.5


def test_global_sandbox_concurrency_limit_is_enforced(tmp_path: Path):
    backend = _ConcurrencyBackend()
    service = SandboxQueryService(
        backend, data_root=_data_root(tmp_path), max_concurrency=2)
    threads = [threading.Thread(target=service.execute, kwargs={
        "code": "emit_result(1)",
        "dataset_ids": ["full_spirits"],
        "deadline": None,
        "cancel_event": threading.Event(),
    }) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
    assert all(not thread.is_alive() for thread in threads)
    assert backend.max_active == 2
