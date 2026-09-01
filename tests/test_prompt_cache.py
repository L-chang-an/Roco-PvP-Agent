"""提示缓存观测：跨网关字段名兼容的用量提取 / 累加 / 命中率。"""

from __future__ import annotations

import dataclasses

from langchain_core.messages import AIMessage

from roco_pvp_agent.llm import accumulate_usage, cache_hit_rate, extract_usage


def _resp(usage_metadata=None, response_metadata=None) -> AIMessage:
    m = AIMessage(content="x")
    if usage_metadata is not None:
        m.usage_metadata = usage_metadata
    if response_metadata is not None:
        m.response_metadata = response_metadata
    return m


# ---------- 提取：三种网关命名 ----------

def test_extract_base_usage_only():
    """网关不报缓存 → **只有三个基础键**（不塞零值，避免与「零命中」混淆）。"""
    u = extract_usage(_resp({"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}))
    assert u == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    assert "cache_read_tokens" not in u


def test_extract_langchain_normalized_cache():
    u = extract_usage(_resp({"input_tokens": 100, "output_tokens": 5, "total_tokens": 105,
                             "input_token_details": {"cache_read": 80, "cache_creation": 20}}))
    assert u["cache_read_tokens"] == 80
    assert u["cache_write_tokens"] == 20


def test_extract_deepseek_raw_cache():
    """DeepSeek 用 prompt_cache_hit/miss_tokens，langchain 未必映射 → 走 response_metadata。"""
    u = extract_usage(_resp({"input_tokens": 100, "output_tokens": 5, "total_tokens": 105},
                            {"token_usage": {"prompt_cache_hit_tokens": 90,
                                             "prompt_cache_miss_tokens": 10}}))
    assert u["cache_read_tokens"] == 90
    assert u["cache_miss_tokens"] == 10


def test_extract_openai_raw_cache():
    u = extract_usage(_resp({"input_tokens": 100, "output_tokens": 5, "total_tokens": 105},
                            {"token_usage": {"prompt_tokens_details": {"cached_tokens": 64}}}))
    assert u["cache_read_tokens"] == 64


def test_extract_ignores_non_int_and_missing():
    u = extract_usage(_resp({"input_tokens": "十", "output_tokens": 5},
                            {"token_usage": {"prompt_cache_hit_tokens": None}}))
    assert u == {"output_tokens": 5}


def test_extract_no_metadata_at_all():
    assert extract_usage(AIMessage(content="x")) == {}


# ---------- 累加 ----------

def test_accumulate_across_rounds():
    target: dict = {}
    accumulate_usage(target, _resp({"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
                                   {"token_usage": {"prompt_cache_hit_tokens": 8,
                                                    "prompt_cache_miss_tokens": 2}}))
    accumulate_usage(target, _resp({"input_tokens": 20, "output_tokens": 3, "total_tokens": 23},
                                   {"token_usage": {"prompt_cache_hit_tokens": 18,
                                                    "prompt_cache_miss_tokens": 2}}))
    assert target["input_tokens"] == 30 and target["output_tokens"] == 5
    assert target["cache_read_tokens"] == 26 and target["cache_miss_tokens"] == 4


def test_accumulate_does_not_create_absent_keys():
    target: dict = {}
    accumulate_usage(target, _resp({"input_tokens": 10}))
    assert target == {"input_tokens": 10}


# ---------- 命中率 ----------

def test_hit_rate_from_hit_and_miss():
    assert cache_hit_rate({"cache_read_tokens": 90, "cache_miss_tokens": 10}) == 0.9


def test_hit_rate_falls_back_to_input_tokens():
    assert cache_hit_rate({"cache_read_tokens": 50, "input_tokens": 200}) == 0.25


def test_hit_rate_none_when_gateway_silent():
    """网关未报 → None，**不假装 0**（0 会被误读成"缓存没生效"）。"""
    assert cache_hit_rate({"input_tokens": 100, "output_tokens": 5}) is None
    assert cache_hit_rate({}) is None


def test_hit_rate_none_on_zero_denominator():
    assert cache_hit_rate({"cache_read_tokens": 0, "cache_miss_tokens": 0}) is None


# ---------- 对战侧接线 ----------

def test_llm_player_accumulates_usage():
    """LLMPlayer 每次 invoke 累计用量（对战侧是 token 大头）。"""
    from environment.rules import DEFAULT_RULES
    from environment.session import BattleSession
    from roco_pvp_agent.battle.player import LLMPlayer
    from roco_pvp_agent.config import Settings
    from rosters import spec

    class _LLM:
        """返回合法 battle_act（否则 decide 会重试 4 次，用量翻 4 倍）。"""

        def invoke(self, messages):
            m = _resp({"input_tokens": 100, "output_tokens": 5, "total_tokens": 105},
                      {"token_usage": {"prompt_cache_hit_tokens": 90,
                                       "prompt_cache_miss_tokens": 10}})
            m.tool_calls = [{"name": "battle_act_a",
                             "args": {"action_type": "recharge"}, "id": "c1"}]
            return m

    rules = dataclasses.replace(DEFAULT_RULES, team_size=1)
    a = [spec("甲", 300, 100, 100, 100, 100, 100, ["抓挠"])]
    b = [spec("乙", 300, 100, 100, 100, 100, 90, ["抓挠"])]
    s = BattleSession.start(a, b, seed=1, rules=rules, battle_id="t")
    p = LLMPlayer("a", settings=Settings(), seed=1, llm=_LLM())
    p.on_match_start(s.view("a"))
    assert p.usage == {}                                  # 还没调用
    p.decide(s.view("a"), s.legal_actions("a"), s.legal_items("a"))
    assert p.usage["input_tokens"] == 100
    assert cache_hit_rate(p.usage) == 0.9


def test_retries_multiply_usage():
    """非法/无 tool_call 时 decide 会重试（≤max_retries+1 次）→ 用量成倍——观测能看见它。"""
    from environment.rules import DEFAULT_RULES
    from environment.session import BattleSession
    from roco_pvp_agent.battle.player import LLMPlayer
    from roco_pvp_agent.config import Settings
    from rosters import spec

    class _NoToolLLM:
        def invoke(self, messages):
            return _resp({"input_tokens": 100, "output_tokens": 5, "total_tokens": 105})

    rules = dataclasses.replace(DEFAULT_RULES, team_size=1)
    a = [spec("甲", 300, 100, 100, 100, 100, 100, ["抓挠"])]
    b = [spec("乙", 300, 100, 100, 100, 100, 90, ["抓挠"])]
    s = BattleSession.start(a, b, seed=1, rules=rules, battle_id="t")
    p = LLMPlayer("a", settings=Settings(), seed=1, llm=_NoToolLLM(), max_retries=3)
    p.on_match_start(s.view("a"))
    p.decide(s.view("a"), s.legal_actions("a"), s.legal_items("a"))
    assert p.usage["input_tokens"] == 400          # 4 次尝试 × 100


def test_chat_agent_usage_shape_unchanged_without_cache_fields(agent_settings):
    """回归保护：网关不报缓存时，ChatReply.usage 仍是精确三键（既有契约不破）。"""
    from roco_pvp_agent.agent import ChatAgent

    class _LLM:
        def invoke(self, messages):
            return _resp({"input_tokens": 3, "output_tokens": 4, "total_tokens": 7})

    reply = ChatAgent(agent_settings, llm=_LLM()).chat("你好")
    assert reply.usage == {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}
