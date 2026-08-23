"""测试用 fake LLM（鸭子类型 invoke(messages) -> AIMessage，不 mock HTTP）。"""

from langchain_core.messages import AIMessage


def tool_call(name: str, args: dict, call_id: str = "call-1") -> dict:
    """构造一条 langchain tool_call。"""
    return {"name": name, "args": args, "id": call_id}


class EchoLLM:
    """把所有消息 content 拼接回显。"""

    def invoke(self, messages):
        texts = [m.content for m in messages if hasattr(m, "content")]
        return AIMessage(content="\n".join(texts))


class ScriptedLLM:
    """按预设顺序返回 AIMessage；耗尽后抛 StopIteration 暴露测试预期。"""

    def __init__(self, replies: list[AIMessage]):
        self._replies = list(replies)
        self.invocations = 0

    def invoke(self, messages):
        self.invocations += 1
        return self._replies.pop(0)


class AlwaysToolLLM:
    """永远调用同一工具，用于测试轮次终止与兜底。"""

    def __init__(self, tool_name: str = "calculator", args: dict | None = None):
        self._tool_name = tool_name
        self._args = args if args is not None else {"expression": "1+1"}

    def invoke(self, messages):
        return AIMessage(content="", tool_calls=[tool_call(self._tool_name, self._args)])
