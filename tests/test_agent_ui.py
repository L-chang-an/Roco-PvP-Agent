"""M3：REST + SSE 契约测试（TestClient + 注入 fake LLM，零网络）。"""

import json

import pytest

pytest.importorskip("fastapi")  # 未装 ui extra 时优雅跳过，不硬失败

from fastapi.testclient import TestClient  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402

from rock_pvp_agent.config import Settings  # noqa: E402
from ui.server import create_chat_app  # noqa: E402

from fakes import ScriptedLLM, tool_call  # noqa: E402


def _settings():
    return Settings(api_key="sk-test", base_url="http://test.invalid", model="test-model", timeout=5.0)


def _client(llm_factory):
    app = create_chat_app(_settings(), llm_factory=llm_factory)
    return TestClient(app)


def _final_llm(text="你好"):
    return lambda settings: ScriptedLLM(
        [AIMessage(content="", tool_calls=[tool_call("final_answer", {"text": text})])]
    )


# ---------- 基础路由 ----------

def test_health():
    assert _client(_final_llm()).get("/api/health").json() == {"status": "ok"}


def test_config_does_not_leak_api_key():
    body = _client(_final_llm()).get("/api/config").json()
    assert body["model"] == "test-model"
    assert body["has_api_key"] is True
    assert "api_key" not in body


def test_index_serves_html():
    res = _client(_final_llm()).get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "Rock PVP Agent" in res.text


def test_static_files_served():
    res = _client(_final_llm()).get("/static/style.css")
    assert res.status_code == 200
    assert "text/css" in res.headers["content-type"]


# ---------- 同步 POST 兜底 ----------

def test_sync_chat_returns_reply_and_session():
    res = _client(_final_llm("你好世界")).post("/api/chat", json={"message": "hi"})
    assert res.status_code == 200
    body = res.json()
    assert body["reply"] == "你好世界"
    assert body["session_id"]


def test_sync_chat_rejects_extra_field():
    """extra='forbid'：前端发了未定义字段直接 422，早暴露契约漂移。"""
    res = _client(_final_llm()).post("/api/chat", json={"message": "hi", "unexpected": 1})
    assert res.status_code == 422


def test_sync_chat_rejects_empty_message():
    res = _client(_final_llm()).post("/api/chat", json={"message": ""})
    assert res.status_code == 422


# ---------- SSE 流式 ----------

def _stream_events(client, url):
    with client.stream("GET", url) as res:
        assert res.status_code == 200
        events = []
        for line in res.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
    return events


def test_stream_event_sequence_final():
    """在线单轮：meta → reply → done。"""
    events = _stream_events(_client(_final_llm("你好")), "/api/chat/stream?message=hi")
    assert [e["event"] for e in events] == ["meta", "reply", "done"]
    assert events[0]["session_id"]
    assert events[1]["text"] == "你好"


def test_stream_event_sequence_with_tool():
    """工具循环：meta → thinking → tool → reply → done。"""
    llm = lambda settings: ScriptedLLM([
        AIMessage(content="先算一下", tool_calls=[tool_call("calculator", {"expression": "3.5*4"}, "c1")]),
        AIMessage(content="", tool_calls=[tool_call("final_answer", {"text": "14.0"}, "c2")]),
    ])
    events = _stream_events(_client(llm), "/api/chat/stream?message=计算")
    assert [e["event"] for e in events] == ["meta", "thinking", "tool", "reply", "done"]
    assert events[2]["name"] == "calculator"
    assert events[3]["text"] == "14.0"


def test_stream_requires_message():
    res = _client(_final_llm()).get("/api/chat/stream")
    assert res.status_code == 422


# ---------- token 统计透传 ----------

def _usage_llm():
    return lambda settings: ScriptedLLM([
        AIMessage(
            content="",
            tool_calls=[tool_call("final_answer", {"text": "ok"}, "c1")],
            usage_metadata={"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
        ),
    ])


def test_sync_chat_includes_usage():
    body = _client(_usage_llm()).post("/api/chat", json={"message": "hi"}).json()
    assert body["usage"] == {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}


def test_stream_reply_event_includes_usage():
    events = _stream_events(_client(_usage_llm()), "/api/chat/stream?message=hi")
    reply_event = next(e for e in events if e["event"] == "reply")
    assert reply_event["usage"] == {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}


# ---------- 会话管理 ----------

def test_reset_clears_history():
    client = _client(_final_llm("第一轮"))
    sid = client.post("/api/chat", json={"message": "hi"}).json()["session_id"]
    assert len(client.get(f"/api/chat/history?session_id={sid}").json()["history"]) >= 2
    client.post("/api/chat/reset", json={"session_id": sid})
    assert client.get(f"/api/chat/history?session_id={sid}").json()["history"] == []


def test_history_threading_across_requests():
    """无状态 ChatAgent 经 ChatContext 织出连续上下文。"""
    llm = lambda settings: ScriptedLLM([
        AIMessage(content="", tool_calls=[tool_call("final_answer", {"text": "第一轮"}, "c1")]),
        AIMessage(content="", tool_calls=[tool_call("final_answer", {"text": "第二轮"}, "c2")]),
    ])
    client = _client(llm)
    sid = client.post("/api/chat", json={"message": "你好"}).json()["session_id"]
    client.post("/api/chat", json={"message": "继续", "session_id": sid})
    history = client.get(f"/api/chat/history?session_id={sid}").json()["history"]
    assert len(history) == 6
    assert [h["type"] for h in history] == ["human", "ai", "tool", "human", "ai", "tool"]
