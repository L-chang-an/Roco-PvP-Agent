"""统一工具分发边界。

模型输出在这里依次经过归一化、注册查找、可见性检查、LangChain/Pydantic
参数验证、handler 执行和结果编码。Agent Loop 不需要了解具体工具的异常类型。
"""

from __future__ import annotations

import json
import hashlib
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from pydantic import BaseModel, ValidationError

from .models import (
    InvalidToolCall,
    ToolCall,
    ToolConcurrency,
    ToolDispatchResult,
    ToolEntry,
    ToolErrorCode,
    ToolOutcome,
)
from .registry import ToolRegistry

if TYPE_CHECKING:
    from .visibility import ToolVisibility


class _DispatchTimeout(TimeoutError):
    """内部墙钟预算耗尽信号。"""


@dataclass(frozen=True)
class ToolExecutionControl:
    """传给可取消 handler 的内部控制面，不属于模型工具 schema。"""

    deadline: float | None
    cancel_event: threading.Event


@dataclass
class DispatchContext:
    """一次 Agent 调用独享的可变分发状态，禁止存放在共享 Agent 实例上。"""

    retry_counts: dict[str, int] = field(default_factory=dict)
    visible_tool_names: set[str] | None = None
    # time.monotonic() 口径的绝对截止时间；None 表示只服从工具自身 timeout。
    deadline: float | None = None
    progress_callback: Callable[[str], None] | None = None
    visibility: "ToolVisibility | None" = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    _progress_lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )

    def report_progress(self, message: str) -> None:
        """串行化并发 worker 的进度回调，避免调用方承担线程安全责任。"""

        if self.progress_callback is None:
            return
        with self._progress_lock:
            self.progress_callback(message)


class ToolDispatcher:
    """使用 ToolRegistry 完成工具调用并返回统一结果。"""

    def __init__(self, registry: ToolRegistry, *, max_concurrent_workers: int = 4):
        if max_concurrent_workers < 1:
            raise ValueError("max_concurrent_workers 必须大于等于 1")
        self._registry = registry
        self._max_concurrent_workers = max_concurrent_workers

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def dispatch(
        self,
        raw_call: ToolCall | object,
        context: DispatchContext,
    ) -> ToolDispatchResult:
        """执行一个工具调用；所有预期失败均转换成 ToolDispatchResult。"""

        started = time.perf_counter()
        try:
            call = raw_call if isinstance(raw_call, ToolCall) \
                else ToolCall.from_langchain(raw_call)
        except InvalidToolCall as exc:
            call = self._fallback_call(raw_call)
            return self._error_result(
                call,
                ToolErrorCode.INVALID_CALL,
                str(exc),
                started=started,
            )

        entry = self._registry.get(call.name)
        if entry is None:
            return self._error_result(
                call,
                ToolErrorCode.UNKNOWN_TOOL,
                f"未知工具：{call.name}",
                started=started,
            )

        if context.visible_tool_names is not None \
                and call.name not in context.visible_tool_names:
            return self._error_result(
                call,
                ToolErrorCode.NOT_LOADED,
                f"工具 {call.name} 尚未在当前会话加载",
                started=started,
                entry=entry,
            )

        timeout = self._effective_timeout(entry, context)
        if timeout is not None and timeout <= 0:
            return self._error_result(
                call,
                ToolErrorCode.TIMEOUT,
                f"工具 {call.name} 执行超时",
                started=started,
                entry=entry,
            )

        try:
            # 不把 LangChain 的 invoke 解析当成唯一安全边界：零参数 StructuredTool
            # 会静默丢弃多余字段，即使 JSON Schema 已声明 additionalProperties=false。
            # Dispatcher 先按注册工具的同一个 Pydantic schema 显式校验，保证模型契约
            # 和运行时契约一致；handler 只会在校验成功后执行。
            self._validate_arguments(entry, call)
            raw_result = self._invoke(entry, call, timeout, context)
        except ValidationError as exc:
            return self._error_result(
                call,
                ToolErrorCode.INVALID_ARGUMENTS,
                self._validation_message(exc),
                started=started,
                entry=entry,
            )
        except _DispatchTimeout:
            return self._error_result(
                call,
                ToolErrorCode.TIMEOUT,
                f"工具 {call.name} 执行超时",
                started=started,
                entry=entry,
            )
        except Exception as exc:
            # 原始异常消息可能含路径、凭据或服务端细节，不写入模型观察与普通日志。
            return self._error_result(
                call,
                ToolErrorCode.EXECUTION_ERROR,
                f"工具 {call.name} 执行失败",
                started=started,
                entry=entry,
                details={"exception_type": type(exc).__name__},
            )

        return self._success_or_outcome_result(
            call,
            entry,
            raw_result,
            context,
            started=started,
        )

    @staticmethod
    def _validate_arguments(entry: ToolEntry, call: ToolCall) -> None:
        """用工具注册时暴露的 Pydantic 模型执行强制运行时校验。"""

        schema_type = entry.tool.get_input_schema()
        if isinstance(schema_type, type) and issubclass(schema_type, BaseModel):
            schema_type.model_validate(dict(call.arguments))

    def dispatch_many(
        self,
        raw_calls: Sequence[ToolCall | object],
        context: DispatchContext,
    ) -> list[ToolDispatchResult]:
        """仅当整个批次均标记为 concurrent_safe 时并发，否则保守串行。

        并发结果仍按模型调用顺序返回。未知、畸形、串行或终结工具只要出现一个，
        整批就走串行路径，从而保留写后读/终结跳过等顺序语义。
        """

        if self._can_dispatch_concurrently(raw_calls):
            workers = min(self._max_concurrent_workers, len(raw_calls))
            with ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix="tool-batch",
            ) as pool:
                futures = [pool.submit(self.dispatch, raw_call, context)
                           for raw_call in raw_calls]
                return [future.result() for future in futures]

        results: list[ToolDispatchResult] = []
        terminal_seen = False
        for raw_call in raw_calls:
            if terminal_seen:
                try:
                    call = raw_call if isinstance(raw_call, ToolCall) \
                        else ToolCall.from_langchain(raw_call)
                except InvalidToolCall:
                    call = self._fallback_call(raw_call)
                results.append(self._error_result(
                    call,
                    ToolErrorCode.SKIPPED_AFTER_TERMINAL,
                    "前序终结工具已完成，本调用未执行",
                    started=time.perf_counter(),
                    entry=self._registry.get(call.name),
                ))
                continue

            result = self.dispatch(raw_call, context)
            results.append(result)
            terminal_seen = result.terminal
        return results

    def _can_dispatch_concurrently(
        self,
        raw_calls: Sequence[ToolCall | object],
    ) -> bool:
        if len(raw_calls) < 2:
            return False
        for raw_call in raw_calls:
            try:
                call = raw_call if isinstance(raw_call, ToolCall) \
                    else ToolCall.from_langchain(raw_call)
            except InvalidToolCall:
                return False
            entry = self._registry.get(call.name)
            if entry is None \
                    or entry.concurrency is not ToolConcurrency.CONCURRENT_SAFE \
                    or entry.terminal_on_success:
                return False
        return True

    def _success_or_outcome_result(
        self,
        call: ToolCall,
        entry: ToolEntry,
        raw_result: object,
        context: DispatchContext,
        *,
        started: float,
    ) -> ToolDispatchResult:
        if isinstance(raw_result, ToolOutcome):
            outcome = raw_result
        else:
            outcome = ToolOutcome(content=self._stringify(raw_result))

        terminal = entry.terminal_on_success and outcome.ok
        if outcome.terminal_override is not None:
            terminal = outcome.terminal_override

        retryable = False
        retry_exhausted = False
        if not outcome.ok and outcome.retryable:
            if entry.retry_limit <= 0:
                # 无重试预算时无需写共享计数；这也保证 concurrent_safe 工具的
                # 动态 ToolOutcome 不会触碰会话级可变重试状态。
                retry_exhausted = True
            else:
                failures = context.retry_counts.get(call.name, 0) + 1
                context.retry_counts[call.name] = failures
                retryable = failures <= entry.retry_limit
                retry_exhausted = not retryable
                if retryable:
                    terminal = False
                else:
                    if entry.on_retry_exhausted is not None:
                        try:
                            exhausted_outcome = entry.on_retry_exhausted(outcome)
                            if not isinstance(exhausted_outcome, ToolOutcome):
                                raise TypeError("on_retry_exhausted 必须返回 ToolOutcome")
                            outcome = exhausted_outcome
                        except Exception as exc:
                            outcome = ToolOutcome(
                                content="工具降级处理失败",
                                ok=False,
                                error_code=ToolErrorCode.EXECUTION_ERROR.value,
                                terminal_override=entry.terminal_on_success,
                                details={"exception_type": type(exc).__name__},
                            )
                    if outcome.terminal_override is not None:
                        terminal = outcome.terminal_override
                    else:
                        # 对终结工具，修复预算耗尽后必须收敛，避免继续无限请求模型。
                        terminal = entry.terminal_on_success
        elif outcome.ok:
            context.retry_counts.pop(call.name, None)

        raw_content = outcome.content if isinstance(outcome.content, str) \
            else self._stringify(outcome.content)
        error_code: ToolErrorCode | str | None = outcome.error_code
        if not outcome.ok and error_code is None:
            error_code = ToolErrorCode.EXECUTION_ERROR
        if outcome.ok:
            content = raw_content
        else:
            content = self._error_content(
                error_code,
                raw_content,
                details=outcome.details,
            )
        content, truncated = self._truncate(content, entry.max_output_chars)
        return ToolDispatchResult(
            call=call,
            content=content,
            ok=outcome.ok,
            error_code=error_code,
            terminal=terminal,
            retryable=retryable,
            retry_exhausted=retry_exhausted,
            duration_ms=self._elapsed_ms(started),
            truncated=truncated,
            audit_tag=entry.audit_tag,
            details=MappingProxyType(dict(outcome.details)),
            logged_arguments=self._logged_arguments(entry, call),
        )

    def _error_result(
        self,
        call: ToolCall,
        code: ToolErrorCode,
        message: str,
        *,
        started: float,
        entry: ToolEntry | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> ToolDispatchResult:
        # Harness 自己生成的错误协议不使用工具输出上限截断，保证 JSON 始终完整。
        # message 均来自上方受控分支；原始 handler 异常不会进入这里。
        content = self._error_content(code, message)
        return ToolDispatchResult(
            call=call,
            content=content,
            ok=False,
            error_code=code,
            terminal=False,
            retryable=False,
            duration_ms=self._elapsed_ms(started),
            truncated=False,
            audit_tag=entry.audit_tag if entry is not None else "",
            details=MappingProxyType(dict(details or {})),
            logged_arguments=(
                self._logged_arguments(entry, call)
                if entry is not None
                else self._redact_arguments(call, frozenset({"code"}))
            ),
        )

    @staticmethod
    def _invoke(
        entry: ToolEntry,
        call: ToolCall,
        timeout: float | None,
        context: DispatchContext,
    ) -> object:
        from .discovery import tool_invoke_config

        arguments = dict(call.arguments)
        deadline = time.monotonic() + timeout if timeout is not None else context.deadline
        if context.deadline is not None:
            deadline = min(deadline, context.deadline) if deadline is not None \
                else context.deadline
        control = ToolExecutionControl(
            deadline=deadline,
            cancel_event=threading.Event(),
        )
        invoke_config = tool_invoke_config(context.visibility, control)
        if timeout is None:
            return entry.tool.invoke(arguments, config=invoke_config)

        # Python 线程无法强制杀死正在运行的 handler；daemon worker 保证 Dispatcher
        # 按时返回，且迟到/永久阻塞的 handler 不会阻止进程退出。
        result_queue: "queue.Queue[tuple[bool, object]]" = queue.Queue(maxsize=1)

        def worker() -> None:
            try:
                result_queue.put((True, entry.tool.invoke(arguments, config=invoke_config)))
            except BaseException as exc:
                result_queue.put((False, exc))

        threading.Thread(
            target=worker,
            daemon=True,
            name=f"tool-dispatch-{entry.name}",
        ).start()
        deadline = control.deadline or (time.monotonic() + timeout)
        heartbeat = 5.0
        poll_interval = 0.05 if entry.cooperative_cancellation else heartbeat
        next_progress = time.monotonic() + heartbeat
        while True:
            if context.cancel_event.is_set():
                control.cancel_event.set()
                if entry.cooperative_cancellation:
                    try:
                        result_queue.get(timeout=1.0)
                    except queue.Empty:
                        pass
                raise _DispatchTimeout
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                control.cancel_event.set()
                # 可取消工具（沙箱）有最多 1 秒清理其完整进程组。普通旧工具仍
                # 保持 daemon 线程兼容语义，不会阻塞 Dispatcher 更久。
                if entry.cooperative_cancellation:
                    try:
                        result_queue.get(timeout=1.0)
                    except queue.Empty:
                        pass
                raise _DispatchTimeout
            try:
                ok, value = result_queue.get(timeout=min(poll_interval, remaining))
            except queue.Empty:
                if time.monotonic() >= deadline:
                    control.cancel_event.set()
                    if entry.cooperative_cancellation:
                        try:
                            result_queue.get(timeout=1.0)
                        except queue.Empty:
                            pass
                    raise _DispatchTimeout
                if time.monotonic() >= next_progress:
                    context.report_progress(f"工具 {entry.name} 仍在执行…")
                    next_progress = time.monotonic() + heartbeat
                continue
            if ok:
                return value
            raise value

    @staticmethod
    def _effective_timeout(entry: ToolEntry, context: DispatchContext) -> float | None:
        limits: list[float] = []
        if entry.timeout_seconds is not None:
            limits.append(entry.timeout_seconds)
        if context.deadline is not None:
            limits.append(context.deadline - time.monotonic())
        return min(limits) if limits else None

    @staticmethod
    def _validation_message(exc: ValidationError) -> str:
        parts: list[str] = []
        for error in exc.errors(include_url=False, include_input=False):
            location = ".".join(str(part) for part in error.get("loc", ())) or "args"
            parts.append(f"{location}: {error.get('msg', '参数无效')}")
        return "；".join(parts) or "工具参数无效"

    @staticmethod
    def _stringify(value: object) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, BaseModel):
            value = value.model_dump(mode="json")
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _error_content(
        code: ToolErrorCode | str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> str:
        code_value = code.value if isinstance(code, ToolErrorCode) else code
        error: dict[str, Any] = {"code": code_value, "message": message}
        if details:
            error["details"] = dict(details)
        return json.dumps(
            {"ok": False, "error": error},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )

    @staticmethod
    def _truncate(content: str, limit: int) -> tuple[str, bool]:
        if len(content) <= limit:
            return content, False
        marker = "\n…[工具输出已截断]"
        if limit <= len(marker):
            return marker[:limit], True
        return content[:limit - len(marker)] + marker, True

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 3)

    @staticmethod
    def _logged_arguments(entry: ToolEntry, call: ToolCall) -> Mapping[str, Any]:
        """生成普通日志/UI 可见参数；敏感值永不进入 ToolDispatchResult 日志。"""

        return ToolDispatcher._redact_arguments(call, entry.sensitive_arguments)

    @staticmethod
    def _redact_arguments(
        call: ToolCall,
        sensitive_arguments: frozenset[str],
    ) -> Mapping[str, Any]:
        logged = dict(call.arguments)
        for name in sensitive_arguments:
            if name not in logged:
                continue
            value = logged[name]
            raw = value if isinstance(value, str) else json.dumps(
                value, ensure_ascii=False, sort_keys=True, default=str)
            logged[name] = {
                "redacted": True,
                "chars": len(raw),
                "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            }
        return MappingProxyType(logged)

    @staticmethod
    def _fallback_call(raw_call: object) -> ToolCall:
        """为不可重放的畸形调用提供仅用于错误记录的稳定占位字段。"""

        call_id = "invalid-tool-call"
        name = "<invalid>"
        if isinstance(raw_call, Mapping):
            raw_id = raw_call.get("id")
            raw_name = raw_call.get("name")
            if isinstance(raw_id, str) and raw_id.strip():
                call_id = raw_id
            if isinstance(raw_name, str) and raw_name.strip():
                name = raw_name
        return ToolCall(call_id=call_id, name=name, arguments={})
