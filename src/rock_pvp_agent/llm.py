"""LLM 工厂：构建 OpenAI 兼容的 chat 模型。

提供注入缝（llm 参数）与线程安全的模块级缓存，便于测试与复用。
"""

import threading

from langchain_openai import ChatOpenAI

from .config import Settings


def normalize_base_url(url: str) -> str:
    """网关常缺 /v1，自动补全；空则返回空串（走 OpenAI 官方默认）。

    规则：结尾已是 /chat/completions → 原样；URL 中已含 /v1 → 原样；否则追加 /v1。
    """
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    if url.endswith("/chat/completions"):
        return url
    if "/v1" in url:
        return url
    return f"{url}/v1"


_CACHE: dict[tuple, object] = {}
_LOCK = threading.Lock()


def build_chat_llm(settings: Settings, tools, *, llm=None):
    """构建 chat LLM 并绑定工具。

    - llm 不为 None 时原样返回（测试注入缝，fake LLM 走这里，不碰网络）。
    - 否则按 (model, base_url, api_key, timeout, 工具名集合) 缓存复用实例。
    """
    if llm is not None:
        return llm

    tool_names = tuple(sorted(getattr(t, "name", "") for t in tools))
    key = (settings.model, settings.base_url, settings.api_key, settings.timeout, tool_names)
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]

        kwargs: dict = {
            "model": settings.model,
            "api_key": settings.api_key,
            "temperature": 0.7,
            "timeout": settings.timeout,
        }
        base_url = normalize_base_url(settings.base_url)
        if base_url:
            kwargs["base_url"] = base_url

        instance = ChatOpenAI(**kwargs).bind_tools(list(tools))
        _CACHE[key] = instance
        return instance
