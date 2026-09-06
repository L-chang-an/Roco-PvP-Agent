"""One ordered event consumer per turn, separate from HTTP subscribers."""
from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field

from roco_pvp_agent.advisor.presentation import NAMES, args_summary, present_round, present_tool
from roco_pvp_agent.events import ExecutionEvent
from roco_pvp_agent.results import AssistantResult
from .chat_store import ChatError
from roco_pvp_agent.conversation import Checkpoint, ContextBudgetError, decode_checkpoint, compile_context, advance_checkpoint


@dataclass
class RunningTurn:
    cancel: threading.Event = field(default_factory=threading.Event)
    closed: threading.Event = field(default_factory=threading.Event)
    events: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=256))
    thread: threading.Thread | None = None
    legacy_sink: object = None
    legacy: dict | None = None

    def put(self, event):
        while not self.closed.is_set():
            try:
                self.events.put(event, timeout=0.1)
                return
            except queue.Full:
                continue


class TurnCoordinator:
    def __init__(self, agent, store, *, max_active=4, max_chars=32000, max_turns=20):
        self.agent, self.store, self.max_active = agent, store, max_active
        self.context_budget = dict(max_chars=max_chars, max_turns=max_turns)
        self._lock = threading.RLock()
        self._running = {}
        self._storage_errors = set()
        self._closing = False

    def create(self, sid, request_id, message, retry_of=None, legacy_sink=None):
        with self._lock:
            if self._closing or self._storage_errors:
                raise ChatError("CHAT_STORAGE_UNAVAILABLE", 503)
            snap, created = self.store.create_turn(sid, request_id, message, {
                "max_llm_rounds": self.agent._max_llm_rounds,
                "max_total_seconds": self.agent._max_total_seconds,
            }, self.max_active, retry_of)
            if created:
                tid = snap["turn_id"]
                runtime = RunningTurn()
                runtime.legacy_sink = legacy_sink
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
            sid = snap["session_id"]
            try:
                cp = decode_checkpoint(self.store.checkpoint(sid))
                if cp.through_turn_id != self.store.latest_completed_turn(sid):
                    raise ValueError('Checkpoint is behind the visible completed history')
            except (ValueError, TypeError, KeyError):
                cp = Checkpoint()
                for old in self.store.rebuild_turns(sid):
                    if old['status'] not in ('completed', 'degraded'):
                        continue
                    result = AssistantResult.model_validate(old['result']) if old.get('result') else None
                    if result is None:
                        continue
                    artifact = self.store.artifact(result.artifacts[0]['artifact_id']) if result.artifacts else None
                    cp = advance_checkpoint(cp, old['turn_id'], old['message'], result,
                        advice=artifact['advice'] if artifact else None, **self.context_budget)
                    if artifact:
                        cp.conversation_state.latest_team_artifact_id = artifact['artifact_id']
                        cp.conversation_state.artifact_version = artifact['artifact_version']
                self.store.recovery_warning(sid, '续聊上下文已从可见记录重建。')
            history, _ = compile_context(cp, **self.context_budget)
            visibility = self.agent.new_tool_visibility()
            restored = visibility.load_by_name(cp.loaded_tools)
            cp.unavailable_tools = list(restored.missing)
            if restored.missing:
                self.store.recovery_warning(sid, '部分历史工具已不可用，后续按当前能力重新发现。')
            def legacy_event(event):
                if runtime.legacy_sink and event.get('event') not in ('reply', 'done', 'thinking'):
                    runtime.legacy_sink(event)
            reply = self.agent.chat(snap["message"], history=history,
                tool_visibility=visibility, cancel_event=runtime.cancel,
                execution_observer=runtime.put, conversation_state=cp.conversation_state, event_sink=legacy_event)
            from roco_pvp_agent.agent import _content_text
            current = reply.history[len(history):]
            runtime.legacy = {'reply': reply.reply, 'tool_calls': reply.tool_calls, 'thinking': [],
                'rounds': reply.rounds, 'usage': reply.usage, 'offline': reply.offline,
                'history': [{'type': m.type, 'content': _content_text(m)} for m in current]}
            try:
                updated = advance_checkpoint(cp, snap['turn_id'], snap['message'], reply.final_result,
                    advice=reply.final_result.advice, loaded_tools=reply.loaded_tools,
                    digest=self.agent._registry.schema_digest(visibility.visible_names()), **self.context_budget).model_dump()
            except ContextBudgetError as exc:
                self.store.recovery_warning(sid, str(exc))
                updated = None
            runtime.put((reply.final_result, updated, reply.offline))
        except ContextBudgetError as exc:
            runtime.put((AssistantResult(message=str(exc), kind='error', status='failed', reason_code='context_budget_exceeded'), None, False))
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
                        runtime.legacy = None
                    self.store.finish(tid, result, cp, offline, legacy=runtime.legacy)
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
