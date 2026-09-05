"""工具治理使用的稳定数据模型。

这里不重新实现 LangChain Tool。``ToolEntry`` 包裹 ``BaseTool``，只承载模型
schema 之外的 Harness 策略；``ToolCall`` 则把模型产生的不可信字典收窄为
后续 Dispatcher 可以消费的形状。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Mapping

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool


class ToolExposure(str, Enum):
    """工具 schema 对模型的可见性策略。"""

    IMMEDIATE = "immediate"
    DEFERRED = "deferred"


class ToolConcurrency(str, Enum):
    """同一模型回复中的工具执行策略。"""

    SERIAL = "serial"
    CONCURRENT_SAFE = "concurrent_safe"


class ToolErrorCode(str, Enum):
    """跨工具稳定的 Harness 错误分类。"""

    UNKNOWN_TOOL = "unknown_tool"
    INVALID_CALL = "invalid_call"
    INVALID_ARGUMENTS = "invalid_arguments"
    EXECUTION_ERROR = "execution_error"
    TIMEOUT = "timeout"
    OUTPUT_TOO_LARGE = "output_too_large"
    NOT_LOADED = "not_loaded"
    SKIPPED_AFTER_TERMINAL = "skipped_after_terminal"
    SANDBOX_POLICY_DENIED = "sandbox_policy_denied"
    SANDBOX_BACKEND_UNAVAILABLE = "sandbox_backend_unavailable"
    SANDBOX_TIMEOUT = "sandbox_timeout"
    SANDBOX_RESOURCE_LIMIT = "sandbox_resource_limit"
    SANDBOX_VIOLATION = "sandbox_violation"
    SANDBOX_EXECUTION_ERROR = "sandbox_execution_error"
    SANDBOX_OUTPUT_INVALID = "sandbox_output_invalid"
    SANDBOX_OUTPUT_TOO_LARGE = "sandbox_output_too_large"


class InvalidToolCall(ValueError):
    """模型工具调用无法归一化时抛出；只能由 Dispatcher 转成协议错误。"""


@dataclass(frozen=True)
class ToolCall:
    """与 provider SDK 解耦后的工具调用。"""

    call_id: str
    name: str
    arguments: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.call_id, str) or not self.call_id.strip():
            raise InvalidToolCall("工具调用 id 必须是非空字符串")
        if not isinstance(self.name, str) or not self.name.strip():
            raise InvalidToolCall("工具名称必须是非空字符串")
        if not isinstance(self.arguments, Mapping):
            raise InvalidToolCall("工具参数 args 必须是对象")
        object.__setattr__(self, "arguments", MappingProxyType(dict(self.arguments)))

    @classmethod
    def from_langchain(cls, raw_call: object) -> "ToolCall":
        """从 LangChain 标准 tool-call 字典构造并复制不可信参数。"""

        if not isinstance(raw_call, Mapping):
            raise InvalidToolCall("工具调用必须是对象")

        name = raw_call.get("name")
        if not isinstance(name, str) or not name.strip():
            raise InvalidToolCall("工具名称必须是非空字符串")

        call_id = raw_call.get("id")
        if not isinstance(call_id, str) or not call_id.strip():
            raise InvalidToolCall("工具调用 id 必须是非空字符串")

        arguments = raw_call.get("args")
        if not isinstance(arguments, Mapping):
            raise InvalidToolCall("工具参数 args 必须是对象")

        return cls(call_id=call_id, name=name, arguments=arguments)


@dataclass(frozen=True)
class ToolOutcome:
    """工具可选的富返回值；普通工具仍可直接返回 str/dict。"""

    content: str
    ok: bool = True
    error_code: str | None = None
    retryable: bool = False
    terminal_override: bool | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolEntry:
    """LangChain Tool 与 Harness 治理策略的单一注册项。"""

    tool: BaseTool
    exposure: ToolExposure = ToolExposure.IMMEDIATE
    concurrency: ToolConcurrency = ToolConcurrency.SERIAL
    terminal_on_success: bool = False
    retry_limit: int = 0
    timeout_seconds: float | None = None
    max_output_chars: int = 20_000
    audit_tag: str = ""
    directory_description: str = ""
    # 这些参数只在 handler 内可见；普通日志/UI/SSE 仅保存长度与 SHA-256。
    sensitive_arguments: frozenset[str] = frozenset()
    cooperative_cancellation: bool = False
    on_retry_exhausted: Callable[[ToolOutcome], ToolOutcome] | None = None

    @property
    def name(self) -> str:
        return self.tool.name


@dataclass(frozen=True)
class ToolDispatchResult:
    """Dispatcher 的统一结果，可稳定转换为模型观察和 UI 日志。"""

    call: ToolCall
    content: str
    ok: bool
    error_code: ToolErrorCode | str | None
    terminal: bool
    retryable: bool
    duration_ms: float
    truncated: bool = False
    retry_exhausted: bool = False
    audit_tag: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)
    logged_arguments: Mapping[str, Any] | None = None

    def to_tool_message(self) -> ToolMessage:
        """生成与原始 call id 一一对应的 LangChain ToolMessage。"""

        return ToolMessage(
            content=self.content,
            tool_call_id=self.call.call_id,
            name=self.call.name,
            status="success" if self.ok else "error",
        )

    def to_log_record(self) -> dict[str, Any]:
        """生成兼容现有 UI 字段、同时带治理状态的日志记录。"""

        code = self.error_code.value if isinstance(self.error_code, ToolErrorCode) \
            else self.error_code
        return {
            "name": self.call.name,
            "call_id": self.call.call_id,
            "args": dict(
                self.logged_arguments
                if self.logged_arguments is not None
                else self.call.arguments
            ),
            "result": self.content,
            "ok": self.ok,
            "error_code": code,
            "terminal": self.terminal,
            "retryable": self.retryable,
            "retry_exhausted": self.retry_exhausted,
            "duration_ms": self.duration_ms,
            "truncated": self.truncated,
            "audit_tag": self.audit_tag,
        }
