"""M1 冒烟：离线路径 + 在线工具循环（fake LLM）+ 事件序列契约。"""

from langchain_core.messages import AIMessage

from rock_pvp_agent.agent import ChatAgent
from rock_pvp_agent.config import Settings

from fakes import AlwaysToolLLM, ScriptedLLM, tool_call


def test_offline_reply_without_api_key(agent_settings):
    agent = ChatAgent(agent_settings)
    assert not agent.has_llm
    reply = agent.chat("你好")
    assert reply.offline is True
    assert "你好" in reply.reply
    assert len(reply.history) == 2  # Human + AI


def test_online_final_answer_via_terminal_tool(agent_settings):
    llm = ScriptedLLM([
        AIMessage(content="", tool_calls=[tool_call("final_answer", {"text": "结果是 14.0"})]),
    ])
    agent = ChatAgent(agent_settings, llm=llm)
    reply = agent.chat("计算 3.5*4")
    assert reply.reply == "结果是 14.0"
    assert reply.offline is False
    assert reply.rounds == 1


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
    agent = ChatAgent(agent_settings, llm=llm)
    reply = agent.chat("1/0")
    assert reply.reply == "计算失败"
    assert "计算失败" in reply.tool_calls[0]["result"]


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
    agent = ChatAgent(agent_settings, llm=llm)
    reply = agent.chat("你好")
    # 最后一条必须是 ToolMessage，不能是带 tool_calls 的 AIMessage
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
    agent = ChatAgent(agent_settings, llm=llm)
    reply = agent.chat("1+1=?")
    assert reply.reply == "结果是 2"
    # 最后两条都是 ToolMessage，覆盖两个 tool_use
    assert [m.type for m in reply.history[-2:]] == ["tool", "tool"]
