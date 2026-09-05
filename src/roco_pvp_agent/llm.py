"""LLM 工厂：构建 OpenAI 兼容的 chat 模型。

提供注入缝（llm 参数）与线程安全的模块级缓存，便于测试与复用。
"""

import threading
from typing import Any

from langchain_core.outputs import ChatResult
from langchain_openai import ChatOpenAI

from .config import Settings


class ReasoningChatOpenAI(ChatOpenAI):
    """透传网关的 reasoning_content（思维链）到 additional_kwargs。

    langchain 通用 ChatOpenAI 会丢弃 `reasoning_content`（见其文档字符串），
    导致模型中间推理文本不可见。子类在消息转换后把该字段捞回，
    agent 侧再从 additional_kwargs 提取为 thinking 事件。
    """

    def _create_chat_result(
        self, response: Any, generation_info: dict[str, Any] | None = None
    ) -> ChatResult:
        result = super()._create_chat_result(response, generation_info)
        if isinstance(response, dict):
            choices = response.get("choices") or []
        else:
            choices = getattr(response, "choices", None) or []
        for idx, gen in enumerate(result.generations):
            if idx >= len(choices):
                break
            choice = choices[idx]
            msg = (
                choice.get("message", {})
                if isinstance(choice, dict)
                else getattr(choice, "message", {})
            )
            rc = (
                msg.get("reasoning_content")
                if isinstance(msg, dict)
                else getattr(msg, "reasoning_content", None)
            )
            if rc:
                gen.message.additional_kwargs["reasoning_content"] = rc
        return result


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


# ---------------------------------------------------------------------------
# 用量与提示缓存观测（跨网关字段名兼容）
# ---------------------------------------------------------------------------
#
# 提示缓存原理：Transformer 推理分 prefill（把输入过一遍、算出每层 KV）与 decode（逐 token
# 生成）。KV 对给定 token 前缀是确定的，所以服务端可存住前缀的 KV，下次同前缀直接复用、
# 跳过 prefill。硬约束：**必须从第 0 个 token 起精确前缀匹配**（中间改一字，后面全失效）；
# 只省输入不省输出。
#
# 本项目走 OpenAI 兼容网关（`langchain_openai`）→ **自动前缀缓存**，代码无需打缓存断点
# （`cache_control` 是 Anthropic 的显式机制）。真正缺的是**观测**：各网关字段名不同，且
# langchain 未必把非 OpenAI 命名映射进 `usage_metadata`，故这里逐路径兜底提取。
_CACHE_READ_PATHS = (
    ("usage_metadata", "input_token_details", "cache_read"),                    # langchain 归一
    ("response_metadata", "token_usage", "prompt_cache_hit_tokens"),            # DeepSeek
    ("response_metadata", "token_usage", "prompt_tokens_details", "cached_tokens"),  # OpenAI
)
_CACHE_WRITE_PATHS = (
    ("usage_metadata", "input_token_details", "cache_creation"),                # 写入缓存的量
)
_CACHE_MISS_PATHS = (
    ("response_metadata", "token_usage", "prompt_cache_miss_tokens"),           # DeepSeek
)

_BASE_USAGE_KEYS = ("input_tokens", "output_tokens", "total_tokens")


def _dig(response, path: tuple) -> int | None:
    """按路径取整数字段；任一层缺失/类型不符 → None（宁缺毋滥，不猜）。"""
    cur = getattr(response, path[0], None)
    for key in path[1:]:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur if isinstance(cur, int) else None


def extract_usage(response) -> dict:
    """LLM 响应 → token 用量 + 提示缓存命中。

    **缓存键只在网关真的返回时才出现**（不塞零值）——否则把「网关不报」与「零命中」混为
    一谈，也会破坏现有 `ChatReply.usage` 的精确形状契约。
    可能含：`input_tokens`/`output_tokens`/`total_tokens`/`cache_read_tokens`/
    `cache_write_tokens`/`cache_miss_tokens`。
    """
    meta = getattr(response, "usage_metadata", None) or {}
    out = {k: meta[k] for k in _BASE_USAGE_KEYS if isinstance(meta.get(k), int)}
    for name, paths in (("cache_read_tokens", _CACHE_READ_PATHS),
                        ("cache_write_tokens", _CACHE_WRITE_PATHS),
                        ("cache_miss_tokens", _CACHE_MISS_PATHS)):
        for path in paths:
            val = _dig(response, path)
            if val is not None:
                out[name] = val
                break
    return out


def accumulate_usage(target: dict, response) -> dict:
    """把一次响应的用量累加进 `target`（原地改并返回）。缺失的键不创建。"""
    for key, val in extract_usage(response).items():
        target[key] = target.get(key, 0) + val
    return target


def cache_hit_rate(usage: dict) -> float | None:
    """提示缓存命中率 ∈ [0,1]；网关未报缓存字段 → None（**不假装 0**）。

    优先 `hit/(hit+miss)`（DeepSeek 直接给两者）；只有 hit 时退回 `hit/input_tokens`。
    """
    hit = usage.get("cache_read_tokens")
    if hit is None:
        return None
    miss = usage.get("cache_miss_tokens")
    denom = (hit + miss) if miss is not None else usage.get("input_tokens")
    if not denom:
        return None
    return round(hit / denom, 4)


def build_chat_llm(settings: Settings, tools, *, llm=None, schema_digest: str | None = None):
    """构建 chat LLM 并绑定工具。

    - llm 不为 None 时原样返回（测试注入缝，fake LLM 走这里，不碰网络）。
    - 否则按 (model, base_url, api_key, timeout, 工具契约指纹) 缓存复用实例。
      未传 ``schema_digest`` 的旧调用方仍回退到工具名集合。
    """
    if llm is not None:
        return llm

    tool_names = tuple(sorted(getattr(t, "name", "") for t in tools))
    tool_contract = schema_digest or tool_names
    key = (settings.model, settings.base_url, settings.api_key, settings.timeout, tool_contract)
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

        instance = ReasoningChatOpenAI(**kwargs).bind_tools(list(tools))
        _CACHE[key] = instance
        return instance
