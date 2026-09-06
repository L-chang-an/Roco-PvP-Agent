"""One ordered event consumer per turn, separate from HTTP subscribers."""
from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from roco_pvp_agent.advisor.presentation import NAMES, args_summary, present_round, present_tool
from roco_pvp_agent.events import ExecutionEvent
from roco_pvp_agent.results import AssistantResult
from .chat_store import ChatError, dump


@dataclass
class RunningTurn:
    cancel: threading.Event = field(default_factory=threading.Event)
    closed: threading.Event = field(default_factory=threading.Event)
    events: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=256))
    thread: threading.Thread | None = None

    def put(self, event):
        while not self.closed.is_set():
            try:
                self.events.put(event, timeout=0.1)
                return
            except queue.Full:
                continue


class TurnCoordinator:
    def __init__(self, agent, store, *, max_active=4):
        self.agent, self.store, self.max_active = agent, store, max_active
        self._lock = threading.RLock()
        self._running = {}
        self._storage_errors = set()
        self._closing = False

    def create(self, sid, request_id, message):
        with self._lock:
            if self._closing or self._storage_errors:
                raise ChatError("CHAT_STORAGE_UNAVAILABLE", 503)
            snap, created = self.store.create_turn(sid, request_id, message, {
                "max_llm_rounds": self.agent._max_llm_rounds,
                "max_total_seconds": self.agent._max_total_seconds,
            }, self.max_active)
            if created:
                tid = snap["turn_id"]
                runtime = RunningTurn()
                self._running[tid] = runtime
                runtime.thread = threading.Thread(target=self._consume, args=(snap, runtime),
                    daemon=True, name=f"chat-{tid}")
                try:
                    runtime.thread.start()
                except Exception:
                    self._running.pop(tid, None)
                    self.store.finish(tid, AssistantResult(message="请求启动失败，请重试。",
                        kind="error", status="failed", reason_code="task_start_failed"))
            return snap, created

    def get(self, tid):
        if tid in self._storage_errors:
            raise ChatError("CHAT_STORAGE_UNAVAILABLE", 503,
                            message="本次请求保存失败，执行已停止。请检查存储后重启服务。")
        return self.store.turn(tid)

    def cancel(self, tid):
        with self._lock:
            snap = self.store.cancel(tid)
            if tid in self._running:
                self._running[tid].cancel.set()
            return snap

    def _invoke(self, snap, runtime):
        try:
            cp = self.store.checkpoint(snap["session_id"])
            if cp and cp.get("schema_version") != 1:
                raise ValueError("Unsupported checkpoint")
            state = cp.get("conversation_state", {})
            history = []
            if state.get("current_team"):
                history.append(SystemMessage(content="历史主队上下文，修改后必须重新校验：" + dump(state)))
            for item in cp.get("messages", []):
                message_type = {"human": HumanMessage, "ai": AIMessage}[item["role"]]
                history.append(message_type(content=item["content"]))
            visibility = self.agent.new_tool_visibility()
            visibility.load_by_name(cp.get("loaded_tools", []))
            reply = self.agent.chat(snap["message"], history=history,
                tool_visibility=visibility, cancel_event=runtime.cancel,
                execution_observer=runtime.put, conversation_state=state)
            updated = {"schema_version": 1, "through_turn_id": snap["turn_id"],
                "messages": [*cp.get("messages", []),
                    {"role": "human", "content": snap["message"]},
                    {"role": "ai", "content": reply.final_result.message}],
                "loaded_tools": list(reply.loaded_tools),
                "registry_schema_digest": self.agent._registry.schema_digest(visibility.visible_names()),
                "conversation_state": dict(state)}
            advice = reply.final_result.advice
            if advice:
                updated["conversation_state"] = {
                    "current_team": [{k: unit[k] for k in ("spirit", "skills", "bloodline", "nature", "iv")} for unit in advice["team"]],
                    "rules_used": advice["rules_used"], "data_digest": advice["data_digest"],
                    "assumptions": advice.get("assumptions", []), "source_turn_id": snap["turn_id"],
                }
            runtime.put((reply.final_result, updated, reply.offline))
        except Exception:
            runtime.put((AssistantResult(message="服务暂时异常，本次请求已结束。",
                kind="error", status="failed", reason_code="execution_error"), None, False))

    def _consume(self, snap, runtime):
        tid = snap["turn_id"]
        worker = threading.Thread(target=self._invoke, args=(snap, runtime), daemon=True)
        try:
            worker.start()
            last_progress = {}
            while not runtime.closed.is_set():
                try:
                    item = runtime.events.get(timeout=0.1)
                except queue.Empty:
                    continue
                if not isinstance(item, ExecutionEvent):
                    result, cp, offline = item
                    if self._closing:
                        result = AssistantResult(message="服务退出，本次请求已中断。", kind="partial",
                            status="interrupted", reason_code="service_interrupted", usage=result.usage)
                        cp = None
                    self.store.finish(tid, result, cp, offline)
                    break
                payload, details = dict(item.payload), None
                if item.event == "round.progress":
                    if last_progress.get(item.round_id) == payload:
                        continue
                    last_progress[item.round_id] = payload
                elif item.event == "tool.started":
                    args = payload.pop("args", {})
                    name = payload["name"]
                    try:
                        brief = args_summary(name, args)
                    except Exception:
                        brief = "参数摘要暂不可用"
                    payload.update(display_name=NAMES.get(name, name[:120]), args_summary=brief,
                        status="running", result_summary="正在执行…", details_available=False)
                elif item.event == "tool.completed":
                    presentation, details = present_tool(item.result)
                    payload = {**presentation, **payload}
                elif item.event == "round.completed":
                    round_ = self.store.turn(tid)["rounds"].get(item.round_id, {})
                    payload.update(present_round(list(round_.get("tools", {}).values()), payload["status"]))
                self.store.append(tid, item.event, payload, item.round_id, item.round_index, details)
                if item.event == "tool.completed":
                    # Publish completed facts immediately, including while parallel peers run.
                    round_ = self.store.turn(tid)["rounds"].get(item.round_id, {})
                    finished = [t for t in round_.get("tools", {}).values() if t.get("status") != "running"]
                    presentation = present_round(finished, "completed")
                    self.store.append(tid, "round.progress", {"phase": "tools", "text": "正在处理工具结果…",
                        "summary": presentation["summary"], "has_warnings": presentation["has_warnings"]},
                        item.round_id, item.round_index)
        except Exception:
            # No successful final event may escape after a failed write.
            runtime.cancel.set()
            self._storage_errors.add(tid)
        finally:
            runtime.closed.set()
            with self._lock:
                self._running.pop(tid, None)

    def close(self):
        with self._lock:
            self._closing = True
            pending = list(self._running.items())
            for _, runtime in pending:
                runtime.cancel.set()
        for tid, runtime in pending:
            runtime.thread.join(timeout=2)
            if runtime.thread.is_alive():
                runtime.closed.set()
                self.store.finish(tid, AssistantResult(message="服务退出，本次请求已中断。",
                    kind="partial", status="interrupted", reason_code="service_interrupted"))
