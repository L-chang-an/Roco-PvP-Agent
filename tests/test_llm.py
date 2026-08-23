"""LLM 工厂测试：注入缝 / normalize_base_url / 模块级缓存（零网络）。"""

import pytest

from rock_pvp_agent.llm import build_chat_llm, normalize_base_url
from rock_pvp_agent.tools import build_agent_tools


class FakeLLM:
    """标记类：验证注入缝原样返回。"""

    def __init__(self, name: str = "fake"):
        self.name = name


# ---------- 注入缝 ----------

def test_injected_llm_returned_unchanged(agent_settings):
    fake = FakeLLM()
    assert build_chat_llm(agent_settings, [], llm=fake) is fake


def test_injected_llm_bypasses_cache(agent_settings):
    """注入的 fake 不走模块级缓存。"""
    a = build_chat_llm(agent_settings, [], llm=FakeLLM("a"))
    b = build_chat_llm(agent_settings, [], llm=FakeLLM("b"))
    assert a is not b


# ---------- normalize_base_url ----------

@pytest.mark.parametrize(
    "url,expected",
    [
        ("", ""),
        ("   ", ""),
        ("https://api.openai.com/v1", "https://api.openai.com/v1"),
        ("https://api.example.com/v1/", "https://api.example.com/v1"),
        ("https://gateway.example.com/chat/completions", "https://gateway.example.com/chat/completions"),
        ("https://api.example.com", "https://api.example.com/v1"),
        ("https://api.example.com/", "https://api.example.com/v1"),
    ],
)
def test_normalize_base_url(url, expected):
    assert normalize_base_url(url) == expected


# ---------- 模块级缓存 ----------

class _FakeChatOpenAI:
    """构造时不校验凭据的假客户端，隔离网络依赖测缓存逻辑。"""

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def bind_tools(self, tools):
        return self


def _monkeypatch_chat(monkeypatch):
    from rock_pvp_agent import llm as llm_module

    monkeypatch.setattr(llm_module, "ChatOpenAI", _FakeChatOpenAI)


def test_cache_reuses_instance_for_same_config(agent_settings, monkeypatch):
    _monkeypatch_chat(monkeypatch)
    tools = build_agent_tools()
    a = build_chat_llm(agent_settings, tools)
    b = build_chat_llm(agent_settings, tools)
    assert a is b


def test_cache_distinct_for_different_tools(agent_settings, monkeypatch):
    _monkeypatch_chat(monkeypatch)
    a = build_chat_llm(agent_settings, build_agent_tools())
    b = build_chat_llm(agent_settings, [])
    assert a is not b


def test_cache_distinct_for_different_api_key(monkeypatch):
    from rock_pvp_agent.config import Settings

    _monkeypatch_chat(monkeypatch)
    s1 = Settings(api_key="k1", base_url="http://test.invalid", model="m")
    s2 = Settings(api_key="k2", base_url="http://test.invalid", model="m")
    assert build_chat_llm(s1, []) is not build_chat_llm(s2, [])
