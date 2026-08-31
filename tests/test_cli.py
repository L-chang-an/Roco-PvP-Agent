"""CLI 测试：--version / -q 单发 / --serve 占位 / REPL / --debug（全程零网络，环境无依赖）。"""

import builtins

import pytest

import rock_pvp_agent
import rock_pvp_agent.__main__ as climod
from rock_pvp_agent.agent import ChatReply


class _FakeAgent:
    """离线确定性 agent：echo 回复并累积历史。"""

    def __init__(self, settings):
        self.settings = settings

    def chat(self, message, history=None):
        history = list(history or [])
        reply = f"[离线回复] 收到你的消息：{message}"
        return ChatReply(reply=reply, history=history + [message], offline=True)


class _RichAgent:
    """在线风格 agent：带思考与工具调用（测 --debug 打印）。"""

    def chat(self, message, history=None):
        return ChatReply(
            reply="答案是 14.0",
            tool_calls=[{"name": "echo", "args": {"expression": "3.5*4"}, "result": "14.0"}],
            thinking=["我先算一下"],
            history=[],
        )


def _run_main(monkeypatch, args):
    monkeypatch.setattr("sys.argv", ["rock_pvp_agent", *args])
    return climod.main()


def _patch_offline(monkeypatch, agent_settings):
    monkeypatch.setattr(climod, "get_settings", lambda: agent_settings)


# ---------- 单发与标志 ----------

def test_version_flag(monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, ["--version"])
    assert exc.value.code == 0
    assert rock_pvp_agent.__version__ in capsys.readouterr().out


def test_serve_calls_run_ui(monkeypatch, capsys):
    """--serve 委托给 ui.__main__.run_ui（M3 起不再是占位；ui 包 2026-08-25 提级为顶层）。"""
    called = []
    monkeypatch.setattr(
        "ui.__main__.run_ui",
        lambda: called.append(True) or 0,
    )
    code = _run_main(monkeypatch, ["--serve"])
    assert code == 0
    assert called == [True]


def test_single_query_offline(monkeypatch, agent_settings, capsys):
    _patch_offline(monkeypatch, agent_settings)
    code = _run_main(monkeypatch, ["-q", "帮我组队"])
    assert code == 0
    out = capsys.readouterr().out
    assert "离线" in out
    assert "帮我组队" in out


def test_debug_prints_thinking_and_tools(monkeypatch, agent_settings, capsys):
    """--debug：思考与工具调用过程打印出来。"""
    _patch_offline(monkeypatch, agent_settings)
    monkeypatch.setattr(climod, "TeamAdvisorAgent", lambda settings: _RichAgent())
    code = _run_main(monkeypatch, ["-q", "计算", "--debug"])
    assert code == 0
    out = capsys.readouterr().out
    assert "我先算一下" in out
    assert "echo" in out
    assert "答案是 14.0" in out


# ---------- 交互 REPL ----------

def test_repl_loop_history_and_exit(monkeypatch, agent_settings, capsys):
    """REPL：空行跳过 / 上下文历史累积 / 退出词 q 结束。"""
    _patch_offline(monkeypatch, agent_settings)
    monkeypatch.setattr(climod, "TeamAdvisorAgent", lambda settings: _FakeAgent(settings))
    _inputs = iter(["   ", "你好", "q"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(_inputs))
    code = _run_main(monkeypatch, [])
    assert code == 0
    out = capsys.readouterr().out
    assert "收到你的消息：你好" in out


def test_repl_handles_eof(monkeypatch, agent_settings, capsys):
    """EOF（Ctrl-D）→ 优雅退出，不抛异常。"""
    _patch_offline(monkeypatch, agent_settings)
    monkeypatch.setattr(climod, "TeamAdvisorAgent", lambda settings: _FakeAgent(settings))

    def _raise_eof(prompt=""):
        raise EOFError

    monkeypatch.setattr(builtins, "input", _raise_eof)
    assert _run_main(monkeypatch, []) == 0
