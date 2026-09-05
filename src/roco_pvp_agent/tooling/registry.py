"""实例级 ToolRegistry：工具定义和治理策略的唯一集合。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Collection

from langchain_core.tools import BaseTool

from .models import ToolConcurrency, ToolEntry, ToolExposure


class ToolRegistrationError(ValueError):
    """工具配置在启动期不满足注册不变量。"""


class ToolRegistry:
    """注册 LangChain Tools，并从同一批条目派生模型目录和运行时查找。"""

    def __init__(self) -> None:
        self._entries: dict[str, ToolEntry] = {}

    def register(self, entry: ToolEntry) -> None:
        """注册一个工具；配置错误立即失败，不推迟到模型调用阶段。"""

        self._validate_entry(entry)
        if entry.name in self._entries:
            raise ToolRegistrationError(f"重复工具名称：{entry.name}")
        self._entries[entry.name] = entry

    def get(self, name: str) -> ToolEntry | None:
        return self._entries.get(name)

    def names(self) -> tuple[str, ...]:
        """按注册顺序返回稳定名称快照。"""

        return tuple(self._entries)

    def entries(self) -> tuple[ToolEntry, ...]:
        """按注册顺序返回稳定条目快照。"""

        return tuple(self._entries.values())

    def immediate_entries(self) -> tuple[ToolEntry, ...]:
        return tuple(
            entry for entry in self._entries.values()
            if entry.exposure is ToolExposure.IMMEDIATE
        )

    def deferred_entries(self) -> tuple[ToolEntry, ...]:
        return tuple(
            entry for entry in self._entries.values()
            if entry.exposure is ToolExposure.DEFERRED
        )

    def model_tools(self, visible_names: Collection[str] | None = None) -> list[BaseTool]:
        """返回应绑定给模型的 BaseTool；未知可见名称按配置错误拒绝。"""

        visible = self._normalize_visible_names(visible_names)
        return [
            entry.tool for name, entry in self._entries.items()
            if visible is None or name in visible
        ]

    def model_schema_records(
        self,
        visible_names: Collection[str] | None = None,
    ) -> list[dict]:
        """返回 provider 无关且可安全修改的模型工具 schema 快照。"""

        visible = self._normalize_visible_names(visible_names)
        records = []
        for name, entry in self._entries.items():
            if visible is not None and name not in visible:
                continue
            records.append({
                "name": name,
                "description": entry.tool.description or "",
                "input_schema": self._input_schema(entry.tool),
                "exposure": entry.exposure.value,
            })
        return deepcopy(records)

    def deferred_directory(self) -> list[dict[str, str]]:
        """返回可复现的紧凑延迟工具目录；不泄露完整 input schema。"""

        return [
            {"name": entry.name, "description": entry.directory_description}
            for entry in sorted(self.deferred_entries(), key=lambda item: item.name)
        ]

    def schema_digest(self, visible_names: Collection[str] | None = None) -> str:
        """计算与注册顺序无关的模型契约指纹，供 LLM 绑定缓存使用。"""

        records = sorted(
            self.model_schema_records(visible_names),
            key=lambda record: record["name"],
        )
        canonical = json.dumps(
            records,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _normalize_visible_names(
        self,
        visible_names: Collection[str] | None,
    ) -> frozenset[str] | None:
        if visible_names is None:
            return None
        visible = frozenset(visible_names)
        unknown = sorted(visible.difference(self._entries))
        if unknown:
            raise ToolRegistrationError(f"可见工具集合包含未注册名称：{', '.join(unknown)}")
        return visible

    @classmethod
    def _validate_entry(cls, entry: ToolEntry) -> None:
        if not isinstance(entry, ToolEntry):
            raise ToolRegistrationError("只能注册 ToolEntry")
        if not isinstance(entry.tool, BaseTool):
            raise ToolRegistrationError("ToolEntry.tool 必须是 LangChain BaseTool")
        if not isinstance(entry.name, str) or not entry.name.strip():
            raise ToolRegistrationError("工具名称不能为空")
        if not isinstance(entry.exposure, ToolExposure):
            raise ToolRegistrationError(f"工具 {entry.name} 的 exposure 必须是 ToolExposure")
        if not isinstance(entry.concurrency, ToolConcurrency):
            raise ToolRegistrationError(
                f"工具 {entry.name} 的 concurrency 必须是 ToolConcurrency")
        if entry.retry_limit < 0:
            raise ToolRegistrationError(f"工具 {entry.name} 的 retry_limit 不能小于 0")
        if entry.timeout_seconds is not None and entry.timeout_seconds <= 0:
            raise ToolRegistrationError(f"工具 {entry.name} 的 timeout_seconds 必须大于 0")
        if entry.max_output_chars <= 0:
            raise ToolRegistrationError(f"工具 {entry.name} 的 max_output_chars 必须大于 0")
        if entry.on_retry_exhausted is not None and not callable(entry.on_retry_exhausted):
            raise ToolRegistrationError(f"工具 {entry.name} 的 on_retry_exhausted 必须可调用")
        if entry.on_retry_exhausted is not None and entry.retry_limit <= 0:
            raise ToolRegistrationError(
                f"工具 {entry.name} 配置 on_retry_exhausted 时 retry_limit 必须大于 0")
        if entry.concurrency is ToolConcurrency.CONCURRENT_SAFE \
                and (entry.terminal_on_success or entry.retry_limit > 0
                     or entry.on_retry_exhausted is not None):
            raise ToolRegistrationError(
                f"并发安全工具 {entry.name} 不能同时承担终结或重试状态")
        if entry.terminal_on_success and entry.exposure is ToolExposure.DEFERRED:
            raise ToolRegistrationError(f"终结工具 {entry.name} 必须立即可见")
        if entry.exposure is ToolExposure.DEFERRED and not entry.directory_description.strip():
            raise ToolRegistrationError(f"延迟工具 {entry.name} 必须提供目录简述")

        schema = cls._input_schema(entry.tool)
        if schema.get("type") != "object":
            raise ToolRegistrationError(f"工具 {entry.name} 的 input schema 必须是 object")
        properties = schema.get("properties") or {}
        unknown_sensitive = sorted(set(entry.sensitive_arguments).difference(properties))
        if unknown_sensitive:
            raise ToolRegistrationError(
                f"工具 {entry.name} 的敏感参数未出现在 schema：{', '.join(unknown_sensitive)}")

    @staticmethod
    def _input_schema(tool: BaseTool) -> dict:
        """提取 LangChain Tool 的 Pydantic/JSON Schema，并返回独立副本。"""

        schema_type = tool.get_input_schema()
        if isinstance(schema_type, dict):
            schema = schema_type
        elif hasattr(schema_type, "model_json_schema"):
            schema = schema_type.model_json_schema()
        elif hasattr(schema_type, "schema"):
            # 兼容仍暴露 Pydantic v1 schema() 的自定义 BaseTool。
            schema = schema_type.schema()
        else:
            raise ToolRegistrationError(f"工具 {tool.name} 无法生成 input schema")
        if not isinstance(schema, dict):
            raise ToolRegistrationError(f"工具 {tool.name} 的 input schema 不是对象")
        return deepcopy(schema)
