"""基础 Agent 的实例级工具集与治理注册表。

注册表按 Agent 实例创建，不使用进程级可变全局注册表。
"""

from collections.abc import Iterable

from langchain_core.tools import BaseTool
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict

from .tooling import ToolConcurrency, ToolEntry, ToolRegistry


class _StrictTextArgs(BaseModel):
    """基础工具的严格文本参数。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    text: str


@tool(args_schema=_StrictTextArgs)
def final_answer(text: str) -> str:
    """输出最终答案给用户。当你确定可以回答时，必须调用本工具，把你的最终回复放在 text 参数中。"""
    return text


@tool(args_schema=_StrictTextArgs)
def echo(text: str) -> str:
    """回显输入文本（基础 Agent 的非终结演示工具，供工具循环测试用）。"""
    return text


def build_agent_registry(tools: Iterable[BaseTool] | None = None) -> ToolRegistry:
    """构建基础注册表；``tools`` 参数保留自定义/测试工具注入能力。

    兼容注入的 ``final_answer`` 按注册策略标记为终结工具。具体名称判断只发生在
    注册装配层，不进入 Agent Loop 或 Dispatcher。
    """

    registry = ToolRegistry()
    candidates = list(tools) if tools is not None else [echo, final_answer]
    for candidate in candidates:
        terminal = candidate.name == final_answer.name
        concurrency = ToolConcurrency.CONCURRENT_SAFE \
            if candidate is echo else ToolConcurrency.SERIAL
        registry.register(ToolEntry(
            tool=candidate,
            concurrency=concurrency,
            terminal_on_success=terminal,
            audit_tag="terminal" if terminal else "basic",
        ))
    return registry
