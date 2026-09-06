"""Durable turns, atomic completion, replay and restart without re-execution."""
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from roco_pvp_agent.config import Settings
from roco_pvp_agent.results import AssistantResult
from ui.chat_store import SQLiteChatSessionStore
from ui.server import create_chat_app
from fakes import ScriptedLLM, tool_call


def app_for(path, llm, **kwargs):
    return create_chat_app(Settings(chat_db_path=str(path), **kwargs), llm_factory=lambda settings: llm)


def wait_done(client, tid):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        response = client.get(f"/api/chat/turns/{tid}")
        assert response.status_code == 200, response.text
        snap = response.json()
        if snap["status"] not in ("pending", "running", "cancelling"):
            return snap
        time.sleep(0.01)
    pytest.fail("Turn did not finish")


def start(client, sid=None, request_id="request", message="帮我组队"):
    sid = sid or client.post("/api/chat/sessions").json()["id"]
    response = client.post(f"/api/chat/sessions/{sid}/turns", json={"request_id": request_id, "message": message})
    assert response.status_code == 202, response.text
    return sid, response.json()["turn_id"]


def event_list(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_persist_replay_and_restart_normalized_checkpoint(tmp_path):
    path = tmp_path / "chat.db"
    llm = ScriptedLLM([
        AIMessage(content="<round_summary>公开思考摘要</round_summary>", additional_kwargs={"reasoning_content": "PRIVATE_SENTINEL"},
            tool_calls=[tool_call("get_catalog_version", {})]),
        AIMessage(content="<round_summary>整理终稿</round_summary>", tool_calls=[tool_call("final_answer", {"text": "完成回答"})]),
    ])
    app = app_for(path, llm)
    assert not path.exists()  # No import/factory-time database creation.
    with TestClient(app) as client:
        sid, tid = start(client)
        snap = wait_done(client, tid)
        assert snap["status"] == "completed" and len(snap["rounds"]) == 2
        events = event_list(client.get(f"/api/chat/turns/{tid}/events"))
        assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
        assert [e["event"] for e in events][-2:] == ["reply", "done"]
        assert events[-1]["payload"]["last_seq"] == snap["last_seq"]
        assert "PRIVATE_SENTINEL" not in json.dumps(events)
        assert len([e for e in events if e["event"] == "round.summary"]) == 2
        for round_ in snap["rounds"].values():
            for xid, tool in round_["tools"].items():
                assert "result" not in tool
                assert client.get(f"/api/chat/turns/{tid}/tools/{xid}").status_code == 200
        tail = event_list(client.get(f"/api/chat/turns/{tid}/events?after_seq=0", headers={"Last-Event-ID": str(snap["last_seq"] - 1)}))
        assert [e["event"] for e in tail] == ["done"]
        assert client.get(f"/api/chat/turns/{tid}/events?after_seq=9999").status_code == 400
        cp = app.state.turn_coordinator.store.checkpoint(sid)
        assert cp["messages"] == [{"role": "human", "content": "帮我组队", "turn_id": tid}, {"role": "ai", "content": "完成回答", "turn_id": tid}]
        assert "公开思考摘要" not in json.dumps(cp, ensure_ascii=False)
        assert not client.get(f"/api/chat/sessions/{sid}").json()["active_turn_id"]
    seen = []
    class Next:
        def invoke(self, messages, **kwargs):
            seen.extend(messages)
            return AIMessage(content="第二次回答")
    with TestClient(app_for(path, Next())) as client:
        restored = client.get(f"/api/chat/sessions/{sid}/messages").json()["turns"][0]
        assert restored == snap
        assert not seen
        _, tid2 = start(client, sid, "second", "继续帮我配招")
        wait_done(client, tid2)
        assert any(m.content == "完成回答" for m in seen)
        assert not any(m.type == "tool" for m in seen)
    assert b"PRIVATE_SENTINEL" not in path.read_bytes()


def test_idempotency_concurrency_cancellation_and_late_result(tmp_path):
    entered, release = threading.Event(), threading.Event()
    class Blocking:
        n = 0
        def invoke(self, messages, **kwargs):
            self.n += 1; entered.set(); release.wait(3)
            return AIMessage(content="LATE_RESULT")
    llm = Blocking()
    with TestClient(app_for(tmp_path / "c.db", llm, chat_max_concurrent_turns=1)) as client:
        sid, tid = start(client)
        assert entered.wait(1)
        path = f"/api/chat/sessions/{sid}/turns"
        with ThreadPoolExecutor(max_workers=4) as pool:
            duplicates = list(pool.map(lambda _: client.post(path, json={"request_id": "request", "message": "帮我组队"}), range(4)))
        assert all(r.status_code == 200 and r.json()["turn_id"] == tid for r in duplicates)
        assert llm.n == 1
        assert client.post(path, json={"request_id": "request", "message": "帮我配招"}).json()["error"] == "REQUEST_ID_CONFLICT"
        assert client.post(path, json={"request_id": "other", "message": "帮我组队"}).json()["error"] == "SESSION_BUSY"
        sid2 = client.post("/api/chat/sessions").json()["id"]
        assert client.post(f"/api/chat/sessions/{sid2}/turns", json={"request_id": "other", "message": "帮我组队"}).status_code == 429
        assert client.post(f"/api/chat/turns/{tid}/cancel").status_code == 200
        snap = wait_done(client, tid)
        assert snap["status"] == "cancelled"
        assert list(snap["rounds"].values())[0]["status"] == "cancelled"
        release.set(); time.sleep(0.05)
        assert client.get(f"/api/chat/turns/{tid}").json() == snap
        assert "LATE_RESULT" not in json.dumps(snap)
        assert client.post(f"/api/chat/turns/{tid}/cancel").json()["status"] == "cancelled"


def test_execution_summary_is_persisted_before_parallel_slow_tool_finishes(tmp_path, monkeypatch):
    from roco_pvp_agent.tooling import ToolDispatcher

    entered, release = threading.Event(), threading.Event()
    original = ToolDispatcher._invoke
    def controlled(entry, call, timeout, context):
        if call.name == "get_spirit_profile":
            entered.set(); assert release.wait(3)
        return original(entry, call, timeout, context)
    monkeypatch.setattr(ToolDispatcher, "_invoke", staticmethod(controlled))
    llm = ScriptedLLM([
        AIMessage(content="<round_summary>查询版本和档案。</round_summary>", tool_calls=[
            tool_call("get_spirit_profile", {"name": "迪莫"}), tool_call("get_catalog_version", {})]),
        AIMessage(content="回答"),
    ])
    with TestClient(app_for(tmp_path / "live.db", llm)) as client:
        _, tid = start(client)
        try:
            assert entered.wait(1)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                snap = client.get(f"/api/chat/turns/{tid}").json()
                round_ = next(iter(snap["rounds"].values()))
                if round_["summary"]:
                    break
                time.sleep(.01)
            assert snap["status"] == round_["status"] == "running"
            assert round_["thought_summary"] == "查询版本和档案。"
            assert "数据" in round_["summary"][0]
            tools = {t["name"]: t for t in round_["tools"].values()}
            assert tools["get_catalog_version"]["status"] == "completed"
            assert tools["get_spirit_profile"]["status"] == "running"
        finally:
            release.set()
        wait_done(client, tid)


def test_storage_failure_rolls_back_final_events_and_recovers(tmp_path, monkeypatch):
    path = tmp_path / "fail.db"
    app = app_for(path, ScriptedLLM([AIMessage(content="answer")]))
    with TestClient(app) as client:
        store = app.state.turn_coordinator.store
        original = store._append
        def fail_on_done(db, tid, event, *args, **kwargs):
            if event == "done":
                raise sqlite3.OperationalError("injected disk failure")
            return original(db, tid, event, *args, **kwargs)
        monkeypatch.setattr(store, "_append", fail_on_done)
        sid, tid = start(client)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and client.get(f"/api/chat/turns/{tid}").status_code == 200:
            time.sleep(.01)
        assert client.get(f"/api/chat/turns/{tid}").status_code == 503
        assert not any(e["event"] in ("reply", "done") for e in store.events(tid))
        assert store.turn(tid)["result"] is None
        assert store.checkpoint(sid) == {}
    with TestClient(app_for(path, ScriptedLLM([]))) as client:
        snap = client.get(f"/api/chat/turns/{tid}").json()
        assert snap["status"] == "interrupted"
        assert len([e for e in event_list(client.get(f"/api/chat/turns/{tid}/events")) if e["event"] == "done"]) == 1


def test_recovery_closes_running_tools_and_lock_prevents_second_process(tmp_path):
    path = tmp_path / "recovery.db"
    store = SQLiteChatSessionStore(path); store.open()
    try:
        with pytest.raises(RuntimeError, match="另一服务"):
            SQLiteChatSessionStore(path).open()
        sid = store.new_session()["id"]
        snap, _ = store.create_turn(sid, "r", "message", {})
        tid = snap["turn_id"]
        store.append(tid, "round.started", {}, "r1", 1)
        store.append(tid, "tool.started", {"tool_execution_id": "x1", "call_index": 1, "status": "running"}, "r1", 1)
    finally:
        store.close()
    store = SQLiteChatSessionStore(path); store.open()
    try:
        store.recover()
        snap = store.turn(tid)
        assert snap["status"] == "interrupted"
        assert snap["rounds"]["r1"]["status"] == "interrupted"
        assert snap["rounds"]["r1"]["tools"]["x1"]["status"] == "interrupted"
        old_seq = snap["last_seq"]
        assert store.append(tid, "round.progress", {"text": "late"}, "r1", 1) is None
        assert store.turn(tid)["last_seq"] == old_seq
    finally:
        store.close()


def test_completed_commit_wins_late_cancel_and_pagination(tmp_path):
    store = SQLiteChatSessionStore(tmp_path / "paging.db"); store.open()
    try:
        sid = store.new_session()["id"]
        ids = []
        for i in range(5):
            snap, _ = store.create_turn(sid, str(i), f"user {i}", {})
            ids.append(snap["turn_id"])
            store.finish(ids[-1], AssistantResult(message=f"answer {i}"))
            time.sleep(.002)
        assert store.cancel(ids[-1])["status"] == "completed"
        page = store.messages(sid, limit=2)
        assert [t["turn_id"] for t in page["turns"]] == ids[-2:]
        page = store.messages(sid, before=page["next_before"], limit=2)
        assert [t["turn_id"] for t in page["turns"]] == ids[1:3]
    finally:
        store.close()


def test_team_artifact_and_context_followup_restore(tmp_path):
    from test_advisor_tool_schemas import _valid_payload
    payload = _valid_payload()
    payload["alternatives"] = [[{**payload["team"][0], "spirit": "不存在"}]]
    path = tmp_path / "team.db"
    with TestClient(app_for(path, ScriptedLLM([AIMessage(content="", tool_calls=[tool_call("submit_team_advice", {"payload": payload})])]))) as client:
        sid, tid = start(client)
        snap = wait_done(client, tid)
        assert snap["result"]["kind"] == "team_advice"
        assert len(snap["result"]["artifacts"]) == 1
        tool = next(iter(next(iter(snap["rounds"].values()))["tools"].values()))
        assert "备选" in tool["result_summary"]
    llm = ScriptedLLM([AIMessage(content="已理解第二只换人请求")])
    with TestClient(app_for(path, llm)) as client:
        _, tid2 = start(client, sid, "followup", "把第二只换掉")
        assert wait_done(client, tid2)["result"]["message"] == "已理解第二只换人请求"
        assert llm.invocations == 1


def test_validation_and_read_only_missing_sessions(tmp_path):
    with TestClient(app_for(tmp_path / "validation.db", ScriptedLLM([]))) as client:
        assert client.get("/api/chat/sessions/missing").status_code == 404
        sid = client.post("/api/chat/sessions").json()["id"]
        for message in ("", "   ", "a" * 2001):
            assert client.post(f"/api/chat/sessions/{sid}/turns", json={"request_id": "r", "message": message}).status_code == 422
        _, tid = start(client, sid, message="你好")
        assert wait_done(client, tid)["rounds"] == {}
        assert client.get(f"/api/chat/turns/{tid}/tools/missing").status_code == 404
