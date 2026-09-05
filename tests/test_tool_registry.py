"""阶段一：工具治理模型与实例级 ToolRegistry。"""

from __future__ import annotations

import pytest
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict

from roco_pvp_agent.tooling import (
    InvalidToolCall,
    ToolCall,
    ToolConcurrency,
    ToolEntry,
    ToolExposure,
    ToolRegistrationError,
    ToolRegistry,
)


class _StrictTextArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


@tool(args_schema=_StrictTextArgs)
def _echo_text(text: str) -> str:
    """回显文本。"""

    return text


@tool
def _count_text(text: str, limit: int = 1) -> int:
    """计算文本长度。"""

    return min(len(text), limit)


def test_registry_uses_same_base_tool_for_schema_and_execution():
    registry = ToolRegistry()
    registry.register(ToolEntry(
        tool=_echo_text,
        concurrency=ToolConcurrency.CONCURRENT_SAFE,
        audit_tag="test",
    ))

    assert registry.names() == ("_echo_text",)
    assert registry.get("_echo_text").tool is _echo_text
    assert registry.model_tools() == [_echo_text]
    schema = registry.model_schema_records()[0]
    assert schema["name"] == "_echo_text"
    assert schema["input_schema"]["additionalProperties"] is False


def test_duplicate_name_is_rejected_at_registration():
    registry = ToolRegistry()
    registry.register(ToolEntry(tool=_echo_text))

    with pytest.raises(ToolRegistrationError, match="重复工具名称"):
        registry.register(ToolEntry(tool=_echo_text))


@pytest.mark.parametrize(
    "entry,error",
    [
        (ToolEntry(tool=_echo_text, retry_limit=-1), "retry_limit"),
        (ToolEntry(tool=_echo_text, timeout_seconds=0), "timeout_seconds"),
        (ToolEntry(tool=_echo_text, max_output_chars=0), "max_output_chars"),
        (
            ToolEntry(
                tool=_echo_text,
                retry_limit=0,
                on_retry_exhausted=lambda outcome: outcome,
            ),
            "retry_limit 必须大于 0",
        ),
        (
            ToolEntry(
                tool=_echo_text,
                exposure=ToolExposure.DEFERRED,
                directory_description="",
            ),
            "目录简述",
        ),
        (
            ToolEntry(
                tool=_echo_text,
                exposure=ToolExposure.DEFERRED,
                directory_description="回显",
                terminal_on_success=True,
            ),
            "必须立即可见",
        ),
        (
            ToolEntry(
                tool=_echo_text,
                concurrency=ToolConcurrency.CONCURRENT_SAFE,
                terminal_on_success=True,
            ),
            "不能同时承担终结或重试状态",
        ),
        (
            ToolEntry(
                tool=_echo_text,
                concurrency=ToolConcurrency.CONCURRENT_SAFE,
                retry_limit=1,
            ),
            "不能同时承担终结或重试状态",
        ),
    ],
)
def test_invalid_policy_is_rejected(entry, error):
    with pytest.raises(ToolRegistrationError, match=error):
        ToolRegistry().register(entry)


def test_blank_tool_name_is_rejected():
    blank = _echo_text.model_copy(update={"name": " "})
    with pytest.raises(ToolRegistrationError, match="名称不能为空"):
        ToolRegistry().register(ToolEntry(tool=blank))


def test_sensitive_argument_must_exist_in_tool_schema():
    with pytest.raises(ToolRegistrationError, match="敏感参数"):
        ToolRegistry().register(ToolEntry(
            tool=_echo_text,
            sensitive_arguments=frozenset({"code"}),
        ))


def test_visible_tool_filter_is_strict_and_keeps_registration_order():
    registry = ToolRegistry()
    registry.register(ToolEntry(tool=_echo_text))
    registry.register(ToolEntry(tool=_count_text))

    assert registry.model_tools({"_count_text"}) == [_count_text]
    with pytest.raises(ToolRegistrationError, match="未注册名称"):
        registry.model_tools({"missing"})


def test_deferred_directory_is_stable_and_does_not_include_schema():
    registry = ToolRegistry()
    registry.register(ToolEntry(
        tool=_count_text,
        exposure=ToolExposure.DEFERRED,
        directory_description="计数",
    ))
    registry.register(ToolEntry(
        tool=_echo_text,
        exposure=ToolExposure.DEFERRED,
        directory_description="回显",
    ))

    assert registry.deferred_directory() == [
        {"name": "_count_text", "description": "计数"},
        {"name": "_echo_text", "description": "回显"},
    ]
    assert all("input_schema" not in row for row in registry.deferred_directory())


def test_model_schema_records_are_defensive_copies():
    registry = ToolRegistry()
    registry.register(ToolEntry(tool=_echo_text))

    first = registry.model_schema_records()
    first[0]["input_schema"]["properties"].clear()
    second = registry.model_schema_records()

    assert "text" in second[0]["input_schema"]["properties"]


def test_schema_digest_is_order_independent_but_contract_sensitive():
    first = ToolRegistry()
    first.register(ToolEntry(tool=_echo_text))
    first.register(ToolEntry(tool=_count_text))

    reordered = ToolRegistry()
    reordered.register(ToolEntry(tool=_count_text))
    reordered.register(ToolEntry(tool=_echo_text))

    changed = ToolRegistry()
    changed_description = _echo_text.model_copy(update={"description": "新的工具说明"})
    changed.register(ToolEntry(tool=changed_description))
    changed.register(ToolEntry(tool=_count_text))

    assert first.schema_digest() == reordered.schema_digest()
    assert first.schema_digest() != changed.schema_digest()


def test_tool_call_normalizes_and_copies_langchain_mapping():
    raw = {"id": "call-1", "name": "_echo_text", "args": {"text": "hi"}}
    call = ToolCall.from_langchain(raw)
    raw["args"]["text"] = "changed"

    assert call.call_id == "call-1"
    assert call.name == "_echo_text"
    assert call.arguments == {"text": "hi"}
    with pytest.raises(TypeError):
        call.arguments["text"] = "blocked"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {},
        {"id": "call-1", "name": "", "args": {}},
        {"id": "", "name": "_echo_text", "args": {}},
        {"id": "call-1", "name": "_echo_text", "args": []},
    ],
)
def test_invalid_tool_call_is_rejected(raw):
    with pytest.raises(InvalidToolCall):
        ToolCall.from_langchain(raw)
