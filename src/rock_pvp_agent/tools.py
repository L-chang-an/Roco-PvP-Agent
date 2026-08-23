"""实例级工具集。

铁律：工具一律在此构造（闭包捕获依赖），绝不写进任何全局注册表。
"""

import ast
import operator

from langchain_core.tools import tool

# 终结工具名：AI 确定答案后必须调用它输出终稿。
FINAL_ANSWER_TOOL = "final_answer"


def _safe_eval(expression: str) -> str:
    """安全求值四则运算表达式（ast 白名单，不执行任意代码）。失败返回错误文本。"""
    try:
        tree = ast.parse(expression, mode="eval")
        return str(_eval_node(tree.body))
    except Exception as exc:  # 宁失败不抛
        return f"计算失败：{exc}"


def _eval_node(node):
    """仅允许数字常量 + 四则/取模/幂/一元运算。"""
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.BinOp):
        op = {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
            ast.FloorDiv: operator.floordiv,
            ast.Mod: operator.mod,
            ast.Pow: operator.pow,
        }.get(type(node.op))
        if op is None:
            raise ValueError(f"不支持的运算符: {type(node.op).__name__}")
        return op(_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.USub):
            return -_eval_node(node.operand)
        if isinstance(node.op, ast.UAdd):
            return +_eval_node(node.operand)
        raise ValueError(f"不支持的运算符: {type(node.op).__name__}")
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    raise ValueError(f"不支持的表达式节点: {type(node).__name__}")


def build_agent_tools():
    """返回本 agent 实例的工具列表（闭包构造，实例级）。"""

    @tool
    def calculator(expression: str) -> str:
        """计算数学表达式并返回字符串结果。expression 支持四则运算、括号、取模、幂。"""
        return _safe_eval(expression)

    @tool
    def final_answer(text: str) -> str:
        """输出最终答案给用户。当你确定可以回答时，必须调用本工具，把你的最终回复放在 text 参数中。"""
        return text

    return [calculator, final_answer]
