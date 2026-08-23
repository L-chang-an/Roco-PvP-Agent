"""工具测试：calculator 求值 / final_answer / 实例级工具集。"""

import pytest

from rock_pvp_agent.tools import FINAL_ANSWER_TOOL, build_agent_tools


def _tool_map():
    return {t.name: t for t in build_agent_tools()}


def _calc(expression: str) -> str:
    return _tool_map()["calculator"].invoke({"expression": expression})


# ---------- calculator 正确求值 ----------

@pytest.mark.parametrize(
    "expr,expected",
    [
        ("3.5*4", "14.0"),
        ("(1+2)*3", "9"),
        ("2**10", "1024"),
        ("10%3", "1"),
        ("7//2", "3"),
        ("1/2", "0.5"),
        ("-5+3", "-2"),
        ("+5", "5"),
    ],
)
def test_calculator_valid_expressions(expr, expected):
    assert _calc(expr) == expected


# ---------- 非法输入 → 错误文本（宁失败不抛） ----------

@pytest.mark.parametrize("expr", ["1/0", "abc", "", "1+", "import os"])
def test_calculator_invalid_returns_error_text(expr):
    result = _calc(expr)
    assert isinstance(result, str)
    assert "失败" in result


def test_calculator_rejects_unsafe_code():
    """即使能 parse，非白名单节点也被拒绝。"""
    result = _calc("__import__('os').system('echo hi')")
    assert isinstance(result, str)
    assert "失败" in result


@pytest.mark.parametrize(
    "expr",
    [
        "1 << 2",   # 位运算（BinOp 白名单外）
        "~5",       # 一元位取反（UnaryOp 白名单外）
        "[1, 2]",   # 列表（非白名单节点）
        "1j",       # 复数常量（非 int/float）
        "'abc'",    # 字符串常量（非 int/float）
    ],
)
def test_calculator_whitelist_edge_cases(expr):
    """白名单边界：能 parse 但节点类型不被允许 → 失败文本。"""
    result = _calc(expr)
    assert isinstance(result, str)
    assert "失败" in result


# ---------- final_answer 终结工具 ----------

def test_final_answer_returns_text():
    assert _tool_map()[FINAL_ANSWER_TOOL].invoke({"text": "你好"}) == "你好"


# ---------- 实例级工具集 ----------

def test_build_agent_tools_names():
    assert [t.name for t in build_agent_tools()] == ["calculator", "final_answer"]


def test_tools_are_instance_level():
    """每次构建都是新实例（闭包），不共享全局状态。"""
    a, b = build_agent_tools(), build_agent_tools()
    assert a[0] is not b[0]
