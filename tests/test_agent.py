"""ChatAgent 行为测试（fake LLM 鸭子类型，零网络）。

覆盖：离线降级 / 显式终稿 / 工具循环 / 轮次兜底 / 错误吞掉 / 事件契约 / 历史可重放 / 边界情况。
"""

from langchain_core.messages import AIMessage

from rock_pvp_agent.agent import ChatAgent, EMPTY_REPLY, OFFLINE_HINT

from fakes import AlwaysToolLLM, ScriptedLLM, tool_call


# ---------- 离线路径 ----------

def test_offline_reply_without_api_key(agent_settings):
    agent = ChatAgent(agent_settings)
    assert not agent.has_llm
    reply = agent.chat("你好")
    assert reply.offline is True
    assert "你好" in reply.reply
    assert len(reply.history) == 2  # Human + AI


def test_offline_reply_includes_hint(agent_settings):
    reply = ChatAgent(agent_settings).chat("hi")
    assert OFFLINE_HINT in reply.reply


def test_offline_never_touches_llm(agent_settings):
    """离线路径不构造/调用 LLM。"""
    reply = ChatAgent(agent_settings).chat("hi")
    assert reply.rounds == 0


# ---------- 在线：显式终稿 ----------

def test_online_final_answer_via_terminal_tool(agent_settings):
    llm = ScriptedLLM([
        AIMessage(content="", tool_calls=[tool_call("final_answer", {"text": "结果是 14.0"})]),
    ])
    agent = ChatAgent(agent_settings, llm=llm)
    reply = agent.chat("计算 3.5*4")
    assert reply.reply == "结果是 14.0"
    assert reply.offline is False
    assert reply.rounds == 1


def test_online_fallback_content_without_tool_call(agent_settings):
    """模型未守协议直接吐文本（无 tool_calls）→ 当终稿（兜底）。"""
    llm = ScriptedLLM([AIMessage(content="直接回答你")])
    reply = ChatAgent(agent_settings, llm=llm).chat("hi")
    assert reply.reply == "直接回答你"


def test_online_empty_content_and_no_tool_call(agent_settings):
    llm = ScriptedLLM([AIMessage(content="")])
    reply = ChatAgent(agent_settings, llm=llm).chat("hi")
    assert reply.reply == EMPTY_REPLY


# ---------- 在线：工具循环 ----------

def test_online_tool_round_then_final(agent_settings):
    llm = ScriptedLLM([
        AIMessage(content="我先算一下", tool_calls=[tool_call("calculator", {"expression": "3.5*4"}, "call-1")]),
        AIMessage(content="", tool_calls=[tool_call("final_answer", {"text": "3.5*4 = 14.0"}, "call-2")]),
    ])
    agent = ChatAgent(agent_settings, llm=llm)
    reply = agent.chat("计算 3.5*4")
    assert reply.reply == "3.5*4 = 14.0"
    assert reply.rounds == 2
    assert len(reply.tool_calls) == 1
    assert reply.tool_calls[0]["name"] == "calculator"
    assert reply.thinking == ["我先算一下"]


def test_online_multiple_tools_in_one_round(agent_settings):
    """一次回复多个 tool_calls → 顺序逐个执行。"""
    llm = ScriptedLLM([
        AIMessage(content="", tool_calls=[
            tool_call("calculator", {"expression": "1+1"}, "call-a"),
            tool_call("calculator", {"expression": "2*3"}, "call-b"),
        ]),
        AIMessage(content="", tool_calls=[tool_call("final_answer", {"text": "2 和 6"}, "call-c")]),
    ])
    reply = ChatAgent(agent_settings, llm=llm).chat("算两个")
    assert len(reply.tool_calls) == 2
    assert [tc["result"] for tc in reply.tool_calls] == ["2", "6"]
    assert reply.rounds == 2


def test_online_fallback_when_no_final_answer(agent_settings):
    """模型一直调工具不终结 → 轮次耗尽兜底。"""
    llm = AlwaysToolLLM()
    agent = ChatAgent(agent_settings, llm=llm, max_llm_rounds=2)
    reply = agent.chat("算一下")
    assert "最大轮数" in reply.reply
    assert reply.rounds == 2
    assert len(reply.tool_calls) == 2


def test_tool_error_is_swallowed(agent_settings):
    """工具执行抛错 → 吞成错误字符串，不崩。"""
    llm = ScriptedLLM([
        AIMessage(content="", tool_calls=[tool_call("calculator", {"expression": "1/0"}, "call-1")]),
        AIMessage(content="", tool_calls=[tool_call("final_answer", {"text": "计算失败"}, "call-2")]),
    ])
    reply = ChatAgent(agent_settings, llm=llm).chat("1/0")
    assert reply.reply == "计算失败"
    assert "失败" in reply.tool_calls[0]["result"]


def test_unknown_tool_is_reported_not_crash(agent_settings):
    llm = ScriptedLLM([
        AIMessage(content="", tool_calls=[tool_call("nonexistent_tool", {})]),
        AIMessage(content="", tool_calls=[tool_call("final_answer", {"text": "继续"}, "call-2")]),
    ])
    reply = ChatAgent(agent_settings, llm=llm).chat("hi")
    assert "未知工具" in reply.tool_calls[0]["result"]
    assert reply.reply == "继续"


# ---------- 思考文本 ----------

def test_thinking_extracted_from_anthropic_blocks(agent_settings):
    """content 为 Anthropic 风格块列表 → 只提取 text 块为思考。"""
    llm = ScriptedLLM([
        AIMessage(
            content=[
                {"type": "text", "text": "先算一下"},
                {"type": "tool_use", "id": "c1", "name": "calculator", "input": {"expression": "1+1"}},
            ],
            tool_calls=[tool_call("calculator", {"expression": "1+1"}, "c1")],
        ),
        AIMessage(content="", tool_calls=[tool_call("final_answer", {"text": "2"}, "c2")]),
    ])
    reply = ChatAgent(agent_settings, llm=llm).chat("1+1?")
    assert reply.thinking == ["先算一下"]


# ---------- 事件契约 ----------

def test_event_sequence_contract(agent_settings):
    events: list[dict] = []
    llm = ScriptedLLM([
        AIMessage(content="思考中", tool_calls=[tool_call("calculator", {"expression": "1+1"}, "call-1")]),
        AIMessage(content="", tool_calls=[tool_call("final_answer", {"text": "2"}, "call-2")]),
    ])
    agent = ChatAgent(agent_settings, llm=llm)
    agent.chat("1+1?", event_sink=events.append)
    assert [e["event"] for e in events] == ["thinking", "tool", "reply", "done"]
    assert events[0]["text"] == "思考中"
    assert events[1]["name"] == "calculator"
    assert events[2]["text"] == "2"


def test_offline_event_sequence(agent_settings):
    events: list[dict] = []
    ChatAgent(agent_settings).chat("hi", event_sink=events.append)
    assert [e["event"] for e in events] == ["reply", "done"]


def test_no_event_sink_is_safe(agent_settings):
    llm = ScriptedLLM([AIMessage(content="", tool_calls=[tool_call("final_answer", {"text": "ok"})])])
    reply = ChatAgent(agent_settings, llm=llm).chat("hi", event_sink=None)
    assert reply.reply == "ok"


# ---------- 历史：无状态 + 可重放 ----------

def test_history_threading(agent_settings):
    """无状态：history 由调用方传回，上下文连续。"""
    llm = ScriptedLLM([
        AIMessage(content="", tool_calls=[tool_call("final_answer", {"text": "第一轮"}, "c1")]),
        AIMessage(content="", tool_calls=[tool_call("final_answer", {"text": "第二轮"}, "c2")]),
    ])
    agent = ChatAgent(agent_settings, llm=llm)
    r1 = agent.chat("你好")
    assert r1.reply == "第一轮"
    assert len(r1.history) == 3  # Human + AIMessage(tool_use) + ToolMessage
    r2 = agent.chat("继续", history=r1.history)
    assert r2.reply == "第二轮"
    assert len(r2.history) == 6  # 历史累积（每轮 3 条）


def test_final_answer_appends_tool_result(agent_settings):
    """终结工具也必须回填 tool_result：历史不能以悬挂 tool_use 结尾（防网关 400）。"""
    llm = ScriptedLLM([
        AIMessage(content="", tool_calls=[tool_call("final_answer", {"text": "你好"}, "c1")]),
    ])
    reply = ChatAgent(agent_settings, llm=llm).chat("你好")
    assert reply.history[-1].type == "tool"
    assert reply.history[-1].tool_call_id == "c1"


def test_mixed_tools_and_terminal_are_all_fulfilled(agent_settings):
    """同一条回复里 [calculator, final_answer] 都要有 tool_result，不能中途 break 遗留。"""
    llm = ScriptedLLM([
        AIMessage(content="", tool_calls=[
            tool_call("calculator", {"expression": "1+1"}, "call-a"),
            tool_call("final_answer", {"text": "结果是 2"}, "call-b"),
        ]),
    ])
    reply = ChatAgent(agent_settings, llm=llm).chat("1+1=?")
    assert reply.reply == "结果是 2"
    assert [m.type for m in reply.history[-2:]] == ["tool", "tool"]


def test_fallback_history_replay_safe(agent_settings):
    """轮次耗尽后历史也合法（最后一条带 tool_calls 的 AI 消息必须被回填）。"""
    llm = AlwaysToolLLM()
    agent = ChatAgent(agent_settings, llm=llm, max_llm_rounds=1)
    reply = agent.chat("hi")
    # 最后一条不能是带 tool_calls 的 AIMessage
    assert reply.history[-1].type == "tool"
