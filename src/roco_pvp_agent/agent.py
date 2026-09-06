"""ChatAgent：无状态聊天核心。

- 在线路径：显式终稿协议的工具循环（final_answer 终结），思考文本与工具事件经 event_sink 发射。
- 离线路径（无 key）：确定性模板回复，保证"宁失败不抛"。
- 历史由调用方持有并传回（stateless by design），一个实例可服务任意多会话。

可靠性设计（Harness 侧，避免"模型不守协议 → 用户拿到空话"）：
- `max_total_seconds`：整体时间预算，超时强制终结（防止用户在 LLM 调用慢时无限等待）。
- 最后一轮注入"必须立即终结"引导，避免模型无限调非终结工具耗尽轮次。
- 轮次耗尽/超时 → `_handle_exhausted`（子类可覆写成有信息的降级答案，而非空话）。
"""

from dataclasses import dataclass, field
import inspect
import queue
import threading
import time
from typing import Callable, Collection, Optional
from uuid import uuid4
from .events import ExecutionEvent, ExecutionObserver, split_round_summary
from .results import AssistantResult

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from .config import Settings
from .llm import build_chat_llm
from .tooling import (
    DispatchContext,
    ToolDispatcher,
    ToolErrorCode,
    ToolRegistry,
    ToolVisibility,
)
from .tools import build_agent_registry

EMPTY_REPLY = "（模型未返回有效回复）"
OFFLINE_HINT = "未配置 LLM_API_KEY，当前处于离线模式，仅返回确定性回复。请配置 .env 后重启以获得完整对话能力。"
EXECUTION_BUDGET_FALLBACK = "（本轮未能在执行预算内完成终稿，请稍后重试或缩小问题范围。）"

# 最后一轮的强制终结引导（Harness 注入，避免模型无限调非终结工具耗尽轮次）。
# 只在最后一轮 invoke 前临时附加到消息列表，**不写入历史**（不污染后续对话）。
LAST_ROUND_HINT = (
    "（Harness 提示：这是最后一轮。你已收集足够信息，请立即调用当前可用的"
    "终结工具给出终稿，不要再调用查询或分析工具。）"
)

# 事件类型：progress / thinking / tool / reply / done（meta 由 SSE 服务端补发）
EVENT_THINKING = "thinking"
EVENT_TOOL = "tool"
EVENT_REPLY = "reply"
EVENT_DONE = "done"
EVENT_PROGRESS = "progress"


class _BudgetExpired(TimeoutError):
    """一次 LLM/工具调用超过本轮对话剩余墙钟预算。"""


class _Cancelled(Exception):
    """Explicit cancellation, distinct from time budget exhaustion."""


def _content_text(response) -> str:
    """从响应中提取纯文本 content。

    兼容字符串与 Anthropic 风格的块列表（text / tool_use / thinking 块）：
    - text 块取 text 字段；
    - thinking/reasoning 块不进入公开正文；
    - tool_use 块不算思考文本。
    """
    content = getattr(response, "content", "")
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                btype = block.get("type")
                if btype == "text":
                    parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(part for part in parts if part)
    return str(content or "")


def _reasoning_text(response) -> str:
    """提取网关返回的 thinking 链文本（OpenAI 兼容的 reasoning_content 字段）。"""
    kwargs = getattr(response, "additional_kwargs", None) or {}
    rc = kwargs.get("reasoning_content")
    if isinstance(rc, str) and rc.strip():
        return rc
    return ""


@dataclass
class ChatReply:
    reply: str
    tool_calls: list[dict[str, object]] = field(default_factory=list)
    thinking: list[str] = field(default_factory=list)
    history: list[BaseMessage] = field(default_factory=list)
    offline: bool = False
    rounds: int = 0
    loaded_tools: tuple[str, ...] = ()
    final_result: AssistantResult | None = None
    usage: dict[str, int] = field(
        default_factory=lambda: {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    )


    def __post_init__(self):
        if self.final_result is None:
            self.final_result = AssistantResult(message=self.reply, usage=dict(self.usage))


class ChatAgent:
    def __init__(
        self,
        settings: Settings,
        *,
        llm=None,
        system_prompt: str = "",
        max_llm_rounds: int = 3,
        tools=None,
        registry: ToolRegistry | None = None,
        emit_thinking: bool = True,
        max_total_seconds: Optional[float] = None,
    ):
        self._settings = settings
        if registry is not None and tools is not None:
            raise ValueError("registry 与 tools 不能同时传入")
        # ``tools`` 保留旧调用方/测试注入能力，进入循环前统一归一为实例级注册表。
        self._registry = registry if registry is not None else build_agent_registry(tools)
        self._dispatcher = ToolDispatcher(self._registry)
        self._llm = llm  # 测试注入缝
        self._system_prompt = system_prompt
        self._max_llm_rounds = max_llm_rounds
        self._emit_thinking = emit_thinking      # 思维链外显开关（顾问关闭）
        # 所有模型与工具调用共用一次对话的截止时间。
        self._max_total_seconds = max_total_seconds

    @property
    def has_llm(self) -> bool:
        return self._llm is not None or self._settings.has_api_key

    def new_tool_visibility(self) -> ToolVisibility:
        """为一个新聊天会话创建独立的延迟工具加载状态。"""

        return ToolVisibility(self._registry)

    def _get_llm(self, visible_tool_names: Collection[str] | None = None):
        if self._llm is not None:
            return self._llm
        if visible_tool_names is None:
            visible_tool_names = {entry.name for entry in self._registry.immediate_entries()}
        return build_chat_llm(
            self._settings,
            self._registry.model_tools(visible_tool_names),
            schema_digest=self._registry.schema_digest(visible_tool_names),
        )

    # ---------- 对外入口 ----------

    def chat(
        self,
        message: str,
        history: Optional[list[BaseMessage]] = None,
        *,
        event_sink: Optional[Callable[[dict], None]] = None,
        tool_visibility: ToolVisibility | None = None,
        cancel_event: threading.Event | None = None,
        execution_observer: ExecutionObserver | None = None,
    ) -> ChatReply:
        history = list(history or [])
        visibility = tool_visibility or self.new_tool_visibility()
        if visibility.registry is not self._registry:
            raise ValueError("tool_visibility 不属于当前 Agent 的 ToolRegistry")
        if not self.has_llm:
            return self._offline_chat(message, history, event_sink, visibility)
        return self._run_online(
            message, history, event_sink, visibility,
            cancel_event=cancel_event, execution_observer=execution_observer,
        )

    # ---------- 离线降级 ----------

    def _offline_chat(
        self,
        message: str,
        history: list[BaseMessage],
        event_sink: Optional[Callable[[dict], None]],
        visibility: ToolVisibility,
    ) -> ChatReply:
        reply_text = f"[离线回复] 收到你的消息：{message}\n\n{OFFLINE_HINT}"
        history = history + [
            HumanMessage(content=message),
            AIMessage(content=reply_text),
        ]
        reply_obj = ChatReply(
            reply=reply_text,
            history=history,
            offline=True,
            rounds=0,
            loaded_tools=visibility.loaded_names(),
        )
        self._emit_reply_events(event_sink, reply_obj)
        return reply_obj

    # ---------- 在线工具循环 ----------

    def _run_online(
        self, message: str, history: list[BaseMessage], event_sink,
        visibility: ToolVisibility, *, cancel_event=None, execution_observer=None,
    ) -> ChatReply:
        working = [SystemMessage(content=self._system_prompt), *history, HumanMessage(content=message)]
        tool_log, thinking = [], []
        usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        reply_text, final_result, reason = EMPTY_REPLY, None, None
        rounds = 0
        start_ts = time.monotonic()
        cancel_event = cancel_event or threading.Event()

        def observe(name, payload):
            if execution_observer is not None:
                execution_observer(ExecutionEvent(
                    name, dispatch_context.round_id, dispatch_context.round_index, payload))

        def progress(text):
            self._emit(event_sink, {"event": EVENT_PROGRESS, "text": text})
            observe("round.progress", {"phase": "tools", "text": text})

        dispatch_context = DispatchContext(
            deadline=start_ts + self._max_total_seconds if self._max_total_seconds is not None else None,
            progress_callback=progress, visibility=visibility,
            cancel_event=cancel_event, observer=execution_observer,
        )
        for index in range(1, self._max_llm_rounds + 1):
            if cancel_event.is_set():
                reason = "cancelled"
                break
            if self._remaining_seconds(start_ts) == 0:
                reason = "timeout"
                break
            try:
                visible_names = visibility.visible_names()
                dispatch_context.visible_tool_names = set(visible_names)
                directory = visibility.directory_prompt()
                working[0] = SystemMessage(content=self._system_prompt + ("\n\n" + directory if directory else ""))
                llm = self._get_llm(visible_names)
            except Exception:
                reason = "llm_error"
                break
            # Preparing visible schemas/client may consume the remaining budget.
            if cancel_event.is_set():
                reason = "cancelled"
                break
            if self._remaining_seconds(start_ts) == 0:
                reason = "timeout"
                break
            invoke_messages = working
            if index == self._max_llm_rounds:
                invoke_messages = working + [SystemMessage(content=LAST_ROUND_HINT)]
            rounds += 1
            dispatch_context.round_id = "r_" + uuid4().hex
            dispatch_context.round_index = rounds
            round_start, round_status, results = time.monotonic(), "completed", []
            observe("round.started", {"phase": "model", "text": "正在分析请求…"})
            if not self._emit_thinking:
                self._emit(event_sink, {"event": EVENT_PROGRESS,
                    "text": f"第 {rounds}/{self._max_llm_rounds} 轮：正在分析请求…"})
            try:
                request_timeout = self._remaining_seconds(start_ts)
                response = self._run_with_budget(
                    lambda: self._invoke_llm(llm, invoke_messages, request_timeout),
                    start_ts=start_ts, event_sink=event_sink,
                    waiting_text=f"第 {rounds}/{self._max_llm_rounds} 轮：模型仍在整理证据…",
                    cancel_event=cancel_event,
                )
                working.append(response)
                self._accumulate_usage(usage, response)
                summary, content = split_round_summary(_content_text(response))
                observe("round.summary", {"source": "model", "text": summary,
                    "status": "available" if summary else "missing"})
                calls = getattr(response, "tool_calls", None) or []
                if self._emit_thinking:
                    reasoning = _reasoning_text(response)
                    if reasoning:
                        thinking.append(reasoning)
                        self._emit(event_sink, {"event": EVENT_THINKING, "text": reasoning})
                if not calls:
                    reply_text = self._handle_no_tool_call(content)
                    if not content:
                        final_result = AssistantResult(message=reply_text, kind="partial",
                            status="degraded", reason_code="empty_reply")
                    break
                if content and self._emit_thinking:
                    legacy_content = content
                    if isinstance(response.content, list):
                        blocks = [str(b.get("thinking") or b.get("text") or "")
                                  for b in response.content if isinstance(b, dict)
                                  and b.get("type") in ("thinking", "reasoning_content", "redacted_thinking")]
                        legacy_content = "\n".join([*filter(None, blocks), content])
                    thinking.append(legacy_content)
                    self._emit(event_sink, {"event": EVENT_THINKING, "text": legacy_content})
                if cancel_event.is_set():
                    raise _Cancelled
                observe("round.progress", {"phase": "tools", "text": "正在执行工具…"})
                results = self._dispatcher.dispatch_many(calls, dispatch_context)
                terminal = None
                for result in results:
                    working.append(result.to_tool_message())
                    entry = self._registry.get(result.call.name)
                    if not (entry and entry.terminal_on_success):
                        log = result.to_log_record()
                        tool_log.append(log)
                        self._emit(event_sink, {"event": EVENT_TOOL, **{
                            key: log[key] for key in ("name", "args", "result", "ok", "error_code")}})
                    if result.terminal and terminal is None:
                        terminal = result
                if cancel_event.is_set():
                    raise _Cancelled
                if self._remaining_seconds(start_ts) == 0:
                    raise _BudgetExpired
                if terminal is not None:
                    final_result = terminal.final_result
                    reply_text = final_result.message if final_result else terminal.content
                    if final_result is None and (not terminal.ok or terminal.retry_exhausted):
                        final_result = AssistantResult(message=reply_text, kind="partial",
                            status="degraded", reason_code="terminal_failed")
                    break
            except _Cancelled:
                reason, round_status = "cancelled", "cancelled"
                break
            except _BudgetExpired:
                reason, round_status = "timeout", "timed_out"
                break
            except Exception:
                reason, round_status = "llm_error", "failed"
                break
            finally:
                observe("round.completed", {
                    "status": round_status, "tool_count": len(results),
                    "duration_ms": round((time.monotonic() - round_start) * 1000, 2),
                    "has_warnings": any(not result.ok for result in results),
                })
        else:
            reason = "rounds"
        if reason:
            reply_text = "（本次请求已停止。）" if reason == "cancelled" else self._handle_exhausted(
                reason, tool_log, thinking, event_sink)
            status = {"timeout": "timed_out", "cancelled": "cancelled",
                      "llm_error": "failed", "rounds": "degraded"}[reason]
            final_result = AssistantResult(message=reply_text, status=status,
                kind="partial" if tool_log or reason == "rounds" else "error", reason_code=reason)
        # Every outstanding tool call must have a matching observation on early exit.
        for call in getattr(working[-1], "tool_calls", None) or []:
            working.append(ToolMessage(content="未执行（已中止）", tool_call_id=call.get("id", "")))
        if final_result is None:
            final_result = AssistantResult(message=reply_text)
        final_result = final_result.model_copy(update={"usage": dict(usage)})
        reply_obj = ChatReply(reply=reply_text, tool_calls=tool_log, thinking=thinking,
            history=working[1:], rounds=rounds, loaded_tools=visibility.loaded_names(),
            usage=usage, final_result=final_result)
        self._emit_reply_events(event_sink, reply_obj)
        return reply_obj

    def _handle_no_tool_call(self, content: str) -> str:
        """无工具调用的兜底钩子。默认 = 以 content 为终稿（空则 EMPTY_REPLY）。

        顾问沿用此兜底：模型直接输出正文时也能及时终结，不强迫再绕一轮工具。
        """
        return content or EMPTY_REPLY

    def _handle_exhausted(self, reason: str, tool_log: list[dict], thinking: list[str],
                          event_sink: Optional[Callable[[dict], None]]) -> str:
        """轮次耗尽、超时或 LLM 异常的兜底钩子。

        默认返回通用预算降级；顾问覆写为「列出已查了哪些工具 + 缺什么」的
        有信息降级答案，避免用户拿到一句空话。
        """
        return EXECUTION_BUDGET_FALLBACK

    def _remaining_seconds(self, start_ts: float) -> Optional[float]:
        """返回总预算剩余秒数；未配置预算时返回 None。"""
        if self._max_total_seconds is None:
            return None
        return max(0.0, self._max_total_seconds - (time.monotonic() - start_ts))

    @staticmethod
    def _invoke_llm(llm, messages, timeout: Optional[float]):
        """调用模型；支持 kwargs 的真实客户端同时接收动态 request timeout。

        测试 fake 通常只有 ``invoke(messages)``，因此先检查签名，避免用 TypeError
        猜测后重试（重试可能造成一次真实请求被重复计费）。
        """
        if timeout is not None:
            try:
                params = inspect.signature(llm.invoke).parameters.values()
                accepts_timeout = any(
                    p.name == "timeout" or p.kind is inspect.Parameter.VAR_KEYWORD
                    for p in params
                )
            except (TypeError, ValueError):
                accepts_timeout = False
            if accepts_timeout:
                return llm.invoke(messages, timeout=max(0.001, timeout))
        return llm.invoke(messages)

    def _run_with_budget(
        self,
        fn: Callable[[], object],
        *,
        start_ts: float,
        event_sink: Optional[Callable[[dict], None]],
        waiting_text: str,
        cancel_event: threading.Event | None = None,
    ):
        """在剩余墙钟预算内执行阻塞调用，并每 5 秒发一次安全进度摘要。

        Python 无法可靠中断正在进行的同步 HTTP/工具调用，因此使用 daemon worker 隔离：
        到期后主对话立即返回降级答案，迟到结果会被丢弃。真实 LLM 客户端自身的
        request timeout 仍作为第二层资源回收保护。
        """
        remaining = self._remaining_seconds(start_ts)
        if remaining is None and cancel_event is None:
            return fn()
        if remaining is not None and remaining <= 0:
            raise _BudgetExpired

        result_queue: "queue.Queue[tuple[bool, object]]" = queue.Queue(maxsize=1)

        def worker() -> None:
            try:
                result_queue.put((True, fn()))
            except BaseException as exc:  # 在线程边界传回，主线程统一处理
                result_queue.put((False, exc))

        deadline = time.monotonic() + remaining if remaining is not None else None
        threading.Thread(target=worker, daemon=True).start()
        heartbeat = 5.0
        next_progress = time.monotonic() + heartbeat
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise _Cancelled
            wait_for = min(0.05, max(0.0, deadline - time.monotonic())) if deadline is not None else 0.05
            if wait_for <= 0:
                raise _BudgetExpired
            try:
                ok, value = result_queue.get(timeout=wait_for)
            except queue.Empty:
                if deadline is not None and time.monotonic() >= deadline:
                    raise _BudgetExpired
                if time.monotonic() >= next_progress:
                    self._emit(event_sink, {"event": EVENT_PROGRESS, "text": waiting_text})
                    next_progress = time.monotonic() + heartbeat
                continue
            if cancel_event is not None and cancel_event.is_set():
                raise _Cancelled
            if deadline is not None and time.monotonic() >= deadline:
                raise _BudgetExpired
            if ok:
                return value
            raise value

    @staticmethod
    def _accumulate_usage(usage: dict, response) -> None:
        """把一次 LLM 响应的用量累加进统计（网关不给 usage 时保持 0）。

        提示缓存命中字段（`cache_read_tokens` 等）**只在网关真的返回时才出现**——
        不塞零值，避免把「网关不报」与「零命中」混为一谈。
        """
        from .llm import accumulate_usage
        accumulate_usage(usage, response)

    # ---------- 事件发射 ----------

    def _emit(self, event_sink: Optional[Callable[[dict], None]], event: dict) -> None:
        if event_sink is not None:
            event_sink(event)

    def _emit_reply_events(self, event_sink: Optional[Callable[[dict], None]], reply_obj: ChatReply) -> None:
        if event_sink is None:
            return
        event_sink(
            {
                "event": EVENT_REPLY,
                "text": reply_obj.reply,
                "offline": reply_obj.offline,
                "rounds": reply_obj.rounds,
                "usage": reply_obj.usage,
            }
        )
        event_sink({"event": EVENT_DONE})
