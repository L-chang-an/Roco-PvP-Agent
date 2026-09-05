"""LangChain 工具适配器。"""

from __future__ import annotations

import threading

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import BaseModel, PrivateAttr

from roco_pvp_agent.tooling.discovery import execution_control_from_config

from .schema import SandboxPythonQueryArgs
from .service import SandboxQueryService


class SandboxPythonQueryTool(BaseTool):
    name: str = "sandbox_python_query"
    description: str = (
        "仅当结构化图鉴工具无法表达复杂统计或跨数据集关联时使用。提交只读 Python "
        "代码与最小 dataset_ids；代码必须且只能调用一次 emit_result(value)。"
    )
    args_schema: type[BaseModel] = SandboxPythonQueryArgs
    _service: SandboxQueryService = PrivateAttr()

    def __init__(self, service: SandboxQueryService) -> None:
        super().__init__()
        self._service = service

    def _run(
        self,
        code: str,
        dataset_ids: list[str],
        config: RunnableConfig,
    ):
        control = execution_control_from_config(config)
        deadline = getattr(control, "deadline", None)
        cancel_event = getattr(control, "cancel_event", None)
        if not isinstance(cancel_event, threading.Event):
            cancel_event = threading.Event()
        return self._service.execute(
            code=code,
            dataset_ids=dataset_ids,
            deadline=deadline,
            cancel_event=cancel_event,
        )


__all__ = ["SandboxPythonQueryTool"]
