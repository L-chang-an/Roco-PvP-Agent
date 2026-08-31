"""实例级工具集。

铁律：工具一律在此构造（闭包捕获依赖），绝不写进任何全局注册表。
"""

from langchain_core.tools import tool

# 终结工具名：AI 确定答案后必须调用它输出终稿。
FINAL_ANSWER_TOOL = "final_answer"


@tool
def final_answer(text: str) -> str:
    """输出最终答案给用户。当你确定可以回答时，必须调用本工具，把你的最终回复放在 text 参数中。"""
    return text


@tool
def echo(text: str) -> str:
    """回显输入文本（基础 Agent 的非终结演示工具，供工具循环测试用）。"""
    return text


def build_agent_tools():
    """返回本 agent 实例的基础工具列表（[echo, final_answer]）。

    组队顾问（advisor.agent）在此之上追加 catalog/轨迹/分析/模拟等专用工具，
    并额外支持 submit_team_advice 结构化终结。
    """
    return [echo, final_answer]
