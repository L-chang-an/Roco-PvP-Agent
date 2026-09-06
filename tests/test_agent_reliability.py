"""Harness 可靠性：最后一轮终结引导 / 时间预算超时兜底 / 顾问有信息降级。"""

from __future__ import annotations

import json
import time

from langchain_core.messages import AIMessage

from fakes import ScriptedLLM, tool_call
from roco_pvp_agent.advisor.agent import TeamAdvisorAgent
from roco_pvp_agent.agent import LAST_ROUND_HINT, ChatAgent
from roco_pvp_agent.config import Settings


class _RecordingLLM:
    """记录每次 invoke 收到的消息（断言最后一轮引导是否注入、是否写入历史）。"""

    def __init__(self, replies):
        self._replies = list(replies)
        self.seen_messages: list[list] = []

    def invoke(self, messages):
        self.seen_messages.append(list(messages))
        return self._replies.pop(0)


class _StuckToolLLM(_RecordingLLM):
    """每轮都调非终结工具 echo（模拟不守终结协议的模型），并在最后一轮响应终结。"""

    def __init__(self, n_rounds: int, terminal: AIMessage):
        replies = [AIMessage(content="", tool_calls=[tool_call("echo", {"text": "x"}, f"c{i}")])
                   for i in range(n_rounds - 1)]
        replies.append(terminal)
        super().__init__(replies)


def test_last_round_hint_injected_but_not_in_history(agent_settings):
    """最后一轮注入强制终结引导（模型收到）；引导不写入 history（不污染后续对话）。"""
    terminal = AIMessage(content="", tool_calls=[tool_call("final_answer", {"text": "好"}, "ct")])
    llm = _StuckToolLLM(3, terminal)
    agent = ChatAgent(agent_settings, llm=llm, max_llm_rounds=3)
    reply = agent.chat("hi")
    assert reply.reply == "好"                      # 引导让模型在最后一轮终结
    # 最后一轮 invoke 的消息里含 LAST_ROUND_HINT
    assert any(LAST_ROUND_HINT in getattr(m, "content", "") for m in llm.seen_messages[-1])
    # 引导不写入历史
    assert not any(LAST_ROUND_HINT in getattr(m, "content", "") for m in reply.history)


def test_timeout_triggers_exhausted_hook(agent_settings):
    """max_total_seconds 超时 → 走通用预算降级，不再发起慢调用。"""

    class _SlowLLM:
        def __init__(self):
            self.invoked = 0

        def invoke(self, messages):
            self.invoked += 1
            time.sleep(0.5)
            return AIMessage(content="", tool_calls=[tool_call("echo", {"text": "x"})])

    llm = _SlowLLM()
    agent = ChatAgent(agent_settings, llm=llm, max_llm_rounds=5, max_total_seconds=0.0)
    t0 = time.monotonic()
    reply = agent.chat("hi")
    elapsed = time.monotonic() - t0
    assert reply.reply == "（本轮未能在执行预算内完成终稿，请稍后重试或缩小问题范围。）"
    assert llm.invoked == 0                    # 0.0 → 首次检查即超时，一次都不发起
    assert elapsed < 0.3                       # 快速返回


def test_advisor_timeout_returns_informative_degraded(agent_settings):
    """顾问超时 → 有信息降级（含 reason/hint），而非一句空话。"""
    agent = TeamAdvisorAgent(Settings(), llm=ScriptedLLM([AIMessage(content="", tool_calls=[tool_call("echo", {}, "c1")])]))
    agent._max_total_seconds = 0.0              # 覆写有效总预算 → 首次即超时
    reply = agent.chat("帮我组个队")
    body = json.loads(reply.reply)
    assert body["ok"] is False and body["degraded"] is True
    assert body["reason"] == "timeout"
    assert "时间预算" in body["message"] or "55" in body["message"]
    assert "hint" in body


def test_progress_events_emitted_when_thinking_off(agent_settings):
    """emit_thinking=False（顾问）→ 每轮发射轻量 progress 事件，等待期有反馈。"""
    from roco_pvp_agent.agent import EVENT_PROGRESS
    events = []

    class _ProgressLLM:
        def __init__(self):
            self.n = 0

        def invoke(self, messages):
            self.n += 1
            if self.n == 1:
                return AIMessage(content="", tool_calls=[tool_call("echo", {"text": "x"}, "c1")])
            return AIMessage(content="", tool_calls=[tool_call("final_answer", {"text": "完成"}, "c2")])

    agent = ChatAgent(agent_settings, llm=_ProgressLLM(), max_llm_rounds=3,
                      emit_thinking=False)
    agent.chat("hi", event_sink=events.append)
    progresses = [e for e in events if e.get("event") == EVENT_PROGRESS]
    assert progresses, "应发射 progress 事件"
    assert any("第 1/3 轮" in p["text"] for p in progresses)
    assert all("正在分析请求" in p["text"] for p in progresses)


def test_progress_not_emitted_when_thinking_on(agent_settings):
    """emit_thinking=True（默认）→ 不发射 progress（避免与原始思维链重复）。"""
    from roco_pvp_agent.agent import EVENT_PROGRESS
    events = []

    class _LLM:
        def invoke(self, messages):
            return AIMessage(content="", tool_calls=[tool_call("final_answer", {"text": "ok"}, "c1")])

    agent = ChatAgent(agent_settings, llm=_LLM(), max_llm_rounds=2)
    agent.chat("hi", event_sink=events.append)
    assert not any(e.get("event") == EVENT_PROGRESS for e in events)


def test_inflight_llm_call_is_cut_off_by_wall_clock_budget(agent_settings):
    """单次 LLM 卡住时也受墙钟预算约束，不能只在轮次之间检查。"""

    class _BlockingLLM:
        def invoke(self, messages):
            time.sleep(0.3)
            return AIMessage(content="迟到答案")

    agent = ChatAgent(agent_settings, llm=_BlockingLLM(), max_total_seconds=0.03)
    started = time.monotonic()
    reply = agent.chat("hi")
    assert time.monotonic() - started < 0.15
    assert "执行预算" in reply.reply


def test_remaining_budget_is_forwarded_as_provider_timeout(agent_settings):
    """支持 kwargs 的真实模型适配器会收到动态 timeout，形成客户端+Harness 双层截止。"""

    class _TimeoutAwareLLM:
        def __init__(self):
            self.timeout = None

        def invoke(self, messages, **kwargs):
            self.timeout = kwargs.get("timeout")
            return AIMessage(content="", tool_calls=[
                tool_call("final_answer", {"text": "ok"}, "c1")
            ])

    llm = _TimeoutAwareLLM()
    reply = ChatAgent(agent_settings, llm=llm, max_total_seconds=1.0).chat("hi")
    assert reply.reply == "ok"
    assert llm.timeout is not None and 0 < llm.timeout <= 1.0


def test_advisor_llm_exception_returns_degraded_reply(agent_settings):
    """API 限流/断网等异常不冒泡，顾问仍返回可呈现的终结答复。"""

    class _BrokenLLM:
        def invoke(self, messages):
            raise RuntimeError("secret-provider-detail")

    reply = TeamAdvisorAgent(agent_settings, llm=_BrokenLLM()).chat("帮我配队")
    body = json.loads(reply.reply)
    assert body["reason"] == "llm_error"
    assert "secret-provider-detail" not in reply.reply


def test_advisor_round_exhaustion_never_returns_old_empty_fallback(agent_settings):
    """复现线上症状：模型每轮只查工具，耗尽后也必须返回已核实结果。"""

    class _CatalogLoopLLM:
        def __init__(self):
            self.n = 0

        def invoke(self, messages):
            self.n += 1
            return AIMessage(content="", tool_calls=[
                tool_call("get_catalog_version", {}, f"c{self.n}")
            ])

    reply = TeamAdvisorAgent(agent_settings, llm=_CatalogLoopLLM()).chat("帮我组个队")
    body = json.loads(reply.reply)
    assert body["reason"] == "rounds"
    assert body["tools_queried"] == ["get_catalog_version"] * agent_settings.chat_max_llm_rounds
    assert body["partial_results"]
    assert "达到最大轮数仍未获得最终答案" not in reply.reply


def test_timeout_fulfils_remaining_tool_calls(agent_settings):
    """多工具响应中途超时后，未执行 tool_use 也有结果占位，历史仍可重放。"""
    from langchain_core.tools import tool as _tool
    from roco_pvp_agent.tools import final_answer

    @_tool
    def slow_tool(text: str) -> str:
        """模拟慢工具。"""
        time.sleep(0.3)
        return text

    llm = ScriptedLLM([AIMessage(content="", tool_calls=[
        tool_call("slow_tool", {"text": "x"}, "slow"),
        tool_call("final_answer", {"text": "不会执行"}, "final"),
    ])])
    agent = ChatAgent(agent_settings, llm=llm, tools=[slow_tool, final_answer],
                      max_total_seconds=0.03)
    reply = agent.chat("hi")
    tool_messages = [m for m in reply.history if m.type == "tool"]
    assert [m.tool_call_id for m in tool_messages] == ["slow", "final"]
