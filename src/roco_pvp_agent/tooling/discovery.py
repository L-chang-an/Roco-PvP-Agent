"""模型可调用的延迟工具发现入口。"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import ToolEntry
from .registry import ToolRegistry
from .visibility import ToolVisibility

TOOL_SEARCH_NAME = "tool_search"
_VISIBILITY_CONFIG_KEY = "tool_visibility"
_EXECUTION_CONTROL_CONFIG_KEY = "tool_execution_control"


class ToolSearchArgs(BaseModel):
    """按精确名称或用途二选一发现延迟工具。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    tool_names: list[str] | None = Field(default=None, min_length=1)
    queries: list[str] | None = Field(default=None, min_length=1)
    top_k: int = Field(default=3, ge=1, le=5)

    @model_validator(mode="after")
    def validate_search_mode(self) -> "ToolSearchArgs":
        if bool(self.tool_names) == bool(self.queries):
            raise ValueError("tool_names 与 queries 必须且只能提供一个")
        values = self.tool_names if self.tool_names is not None else self.queries or []
        if any(not value.strip() for value in values):
            raise ValueError("搜索名称或关键词不能为空")
        return self


class ToolSearchTool(BaseTool):
    """从 RunnableConfig 取得会话状态的 LangChain Tool。"""

    name: str = TOOL_SEARCH_NAME
    description: str = (
        "发现并加载延迟工具 schema；按 tool_names 精确加载，或按 queries 用途关键词检索。"
    )
    args_schema: type[BaseModel] = ToolSearchArgs

    def _run(
        self,
        config: RunnableConfig,
        tool_names: list[str] | None = None,
        queries: list[str] | None = None,
        top_k: int = 3,
    ) -> dict:
        configurable = config.get("configurable") or {}
        visibility = configurable.get(_VISIBILITY_CONFIG_KEY)
        if not isinstance(visibility, ToolVisibility):
            raise RuntimeError("tool_search 缺少会话级 ToolVisibility")
        result = (
            visibility.load_by_name(tool_names)
            if tool_names is not None
            else visibility.search(queries or [], top_k=top_k)
        )
        return result.to_payload()


tool_search = ToolSearchTool()


def enable_deferred_loading(registry: ToolRegistry) -> None:
    """当注册表含延迟工具时，注册唯一的即时发现入口。"""

    if not registry.deferred_entries():
        return
    registry.register(ToolEntry(
        tool=tool_search,
        audit_tag="discovery",
        max_output_chars=40_000,
    ))


def tool_invoke_config(
    visibility: ToolVisibility | None,
    execution_control: object | None = None,
) -> RunnableConfig:
    """构造 Dispatcher 传给 LangChain Tool 的会话配置。"""

    return {"configurable": {
        _VISIBILITY_CONFIG_KEY: visibility,
        _EXECUTION_CONTROL_CONFIG_KEY: execution_control,
    }}


def execution_control_from_config(config: RunnableConfig) -> object | None:
    """供需要协作取消的工具读取 Dispatcher 内部控制对象。"""

    return (config.get("configurable") or {}).get(_EXECUTION_CONTROL_CONFIG_KEY)


__all__ = [
    "TOOL_SEARCH_NAME",
    "ToolSearchArgs",
    "enable_deferred_loading",
    "execution_control_from_config",
    "tool_invoke_config",
    "tool_search",
]
