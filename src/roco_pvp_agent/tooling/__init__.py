"""Agent 工具注册与治理基础设施。

LangChain ``BaseTool`` 继续作为工具名称、描述、参数 schema 与 callable 的载体；
本包只补充 Harness 所需的注册、严格分发、安全并发、会话级延迟加载与结果模型。
"""

from .models import (
    InvalidToolCall,
    ToolCall,
    ToolConcurrency,
    ToolDispatchResult,
    ToolEntry,
    ToolErrorCode,
    ToolExposure,
    ToolOutcome,
)
from .dispatcher import DispatchContext, ToolDispatcher, ToolExecutionControl
from .discovery import TOOL_SEARCH_NAME, enable_deferred_loading, tool_search
from .registry import ToolRegistrationError, ToolRegistry
from .schema import StrictToolArgs
from .visibility import ToolMatch, ToolSearchResult, ToolVisibility

__all__ = [
    "InvalidToolCall",
    "DispatchContext",
    "ToolCall",
    "ToolConcurrency",
    "ToolDispatchResult",
    "ToolDispatcher",
    "ToolExecutionControl",
    "ToolEntry",
    "ToolErrorCode",
    "ToolExposure",
    "ToolOutcome",
    "ToolRegistrationError",
    "ToolRegistry",
    "StrictToolArgs",
    "ToolMatch",
    "ToolSearchResult",
    "ToolVisibility",
    "TOOL_SEARCH_NAME",
    "enable_deferred_loading",
    "tool_search",
]
