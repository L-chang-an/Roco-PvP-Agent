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


def _content_text(response) -> str:
    """从响应中提取纯文本 content。

    兼容字符串与 Anthropic 风格的块列表（text / tool_use / thinking 块）：
    - text 块取 text 字段；
    - thinking 块取 thinking 字段（思维链）；
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
                elif btype in ("thinking", "reasoning_content", "redacted_thinking"):
                    parts.append(str(block.get("thinking") or block.get("text") or ""))
            else:
                parts.append(str(block))
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
    usage: dict[str, int] = field(
        default_factory=lambda: {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    )


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
        # 整体时间预算（秒）：超时强制终结，防止用户无限等待（"1 分钟内给答案"的兜底）。
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
    ) -> ChatReply:
        history = list(history or [])
        visibility = tool_visibility or self.new_tool_visibility()
        if visibility.registry is not self._registry:
            raise ValueError("tool_visibility 不属于当前 Agent 的 ToolRegistry")
        if not self.has_llm:
            return self._offline_chat(message, history, event_sink, visibility)
        return self._run_online(
            message, history, event_sink, visibility,
            cancel_event=cancel_event,
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
        self,
        message: str,
        history: list[BaseMessage],
        event_sink: Optional[Callable[[dict], None]],
        visibility: ToolVisibility,
        *,
        cancel_event: threading.Event | None = None,
    ) -> ChatReply:
        working: list[BaseMessage] = [SystemMessage(content=self._system_prompt)]
        working.extend(history)
        working.append(HumanMessage(content=message))

        tool_log: list[dict] = []
        thinking: list[str] = []
        usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        reply_text = EMPTY_REPLY
        rounds = 0
        start_ts = time.monotonic()
        dispatch_context = DispatchContext(
            deadline=(start_ts + self._max_total_seconds)
            if self._max_total_seconds is not None else None,
            progress_callback=lambda text: self._emit(
                event_sink, {"event": EVENT_PROGRESS, "text": text}),
            visibility=visibility,
            cancel_event=cancel_event or threading.Event(),
        )

        for rounds in range(1, self._max_llm_rounds + 1):
            # 时间预算：超时立即终结，不再发起新一轮慢调用（"1 分钟内给答案"的兜底）。
            if self._remaining_seconds(start_ts) == 0:
                reply_text = self._handle_exhausted(
                    "timeout", tool_log, thinking, event_sink)
                break

            # 进度事件：顾问关闭思维链（emit_thinking=False）时，仍发一条轻量"第几轮/正在调工具"
            # 让 CLI/WebUI 在等待期也有反馈，不让用户干等（非原始思维链，无泄露）。
            if not self._emit_thinking:
                self._emit(event_sink, {
                    "event": EVENT_PROGRESS,
                    "text": f"第 {rounds}/{self._max_llm_rounds} 轮：正在调用工具分析…",
                })

            # 每轮从会话加载状态派生同一份“模型可见 + Dispatcher 可执行”工具集合。
            # tool_search 在本轮加载的 schema 只从下一轮起生效，模型必须先看到契约。
            visible_names = visibility.visible_names()
            dispatch_context.visible_tool_names = set(visible_names)
            directory_prompt = visibility.directory_prompt()
            system_content = self._system_prompt
            if directory_prompt:
                system_content = f"{system_content}\n\n{directory_prompt}" \
                    if system_content else directory_prompt
            working[0] = SystemMessage(content=system_content)
            llm = self._get_llm(visible_names)

            # 最后一轮：临时附加强制终结引导（不写入 working/历史，避免污染后续对话）。
            invoke_messages = working
            if rounds == self._max_llm_rounds:
                invoke_messages = working + [SystemMessage(content=LAST_ROUND_HINT)]

            try:
                request_timeout = self._remaining_seconds(start_ts)
                response = self._run_with_budget(
                    lambda: self._invoke_llm(llm, invoke_messages, request_timeout),
                    start_ts=start_ts,
                    event_sink=event_sink,
                    waiting_text=f"第 {rounds}/{self._max_llm_rounds} 轮：模型仍在整理证据…",
                )
            except _BudgetExpired:
                reply_text = self._handle_exhausted(
                    "timeout", tool_log, thinking, event_sink)
                break
            except Exception:
                # API 超时、断网、限流、响应解析失败都不能直接冒泡到 CLI/WebUI。
                # 详细异常可能带服务端信息，不进入用户回复；统一走子类的安全降级。
                reply_text = self._handle_exhausted(
                    "llm_error", tool_log, thinking, event_sink)
                break
            working.append(response)
            self._accumulate_usage(usage, response)

            content = _content_text(response)
            calls = getattr(response, "tool_calls", None) or []
            reasoning = _reasoning_text(response)

            # 思维链文本：reasoning_content 无条件捕获为思考；content 仅在有工具调用时算思考。
            # emit_thinking=False（顾问）时既不收集也不发射——不存原始思维链。
            if reasoning and self._emit_thinking:
                thinking.append(reasoning)
                self._emit(event_sink, {"event": EVENT_THINKING, "text": reasoning})

            # 兜底：模型未守协议，返回无工具调用 → 交给 _handle_no_tool_call（默认以 content 为终稿）
            if not calls:
                reply_text = self._handle_no_tool_call(content)
                break

            # 思考文本：伴随工具调用的中间输出，记为思考
            if content and self._emit_thinking:
                thinking.append(content)
                self._emit(event_sink, {"event": EVENT_THINKING, "text": content})

            # 所有工具统一穿过 Dispatcher；Agent Loop 只消费结果，不识别具体工具名。
            # dispatch_many 保证成功终结后的剩余调用也获得 skipped ToolMessage。
            results = self._dispatcher.dispatch_many(calls, dispatch_context)
            terminal_result = None
            for result in results:
                working.append(result.to_tool_message())
                entry = self._registry.get(result.call.name)
                terminal_capable = bool(entry and entry.terminal_on_success)

                # 保持 ChatReply/UI 既有语义：终结工具不作为取证工具展示；其调用仍完整
                # 存在于消息历史和 Dispatcher 结果中，协议可重放。
                if not terminal_capable:
                    log_record = result.to_log_record()
                    tool_log.append(log_record)
                    self._emit(event_sink, {
                        "event": EVENT_TOOL,
                        "name": log_record["name"],
                        "args": log_record["args"],
                        "result": log_record["result"],
                        "ok": log_record["ok"],
                        "error_code": log_record["error_code"],
                    })
                if result.terminal and terminal_result is None:
                    terminal_result = result

            if terminal_result is not None:
                reply_text = terminal_result.content
                break

            budget_exhausted = (
                self._remaining_seconds(start_ts) == 0
                and any(result.error_code is ToolErrorCode.TIMEOUT for result in results)
            )
            if budget_exhausted:
                reply_text = self._handle_exhausted(
                    "timeout", tool_log, thinking, event_sink)
                break
        else:
            # 轮次耗尽仍未获得终稿 → 交给 _handle_exhausted（默认通用预算降级，
            # 顾问覆写为有信息的降级答案）。
            reply_text = self._handle_exhausted(
                "rounds", tool_log, thinking, event_sink)

        # 若最终走了"未终结"路径（timeout/rounds），最后一条 AI 消息可能带未回填的
        # tool_result——补全保证历史可重放（OpenAI/Anthropic 网关 400 防护）。
        last = working[-1]
        for call in getattr(last, "tool_calls", None) or []:
            working.append(
                ToolMessage(content="未执行（已中止）", tool_call_id=call.get("id", ""))
            )

        reply_obj = ChatReply(
            reply=reply_text,
            tool_calls=tool_log,
            thinking=thinking,
            history=working[1:],
            rounds=rounds,
            loaded_tools=visibility.loaded_names(),
            usage=usage,
        )
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
    ):
        """在剩余墙钟预算内执行阻塞调用，并每 5 秒发一次安全进度摘要。

        Python 无法可靠中断正在进行的同步 HTTP/工具调用，因此使用 daemon worker 隔离：
        到期后主对话立即返回降级答案，迟到结果会被丢弃。真实 LLM 客户端自身的
        request timeout 仍作为第二层资源回收保护。
        """
        remaining = self._remaining_seconds(start_ts)
        if remaining is None:
            return fn()
        if remaining <= 0:
            raise _BudgetExpired

        result_queue: "queue.Queue[tuple[bool, object]]" = queue.Queue(maxsize=1)

        def worker() -> None:
            try:
                result_queue.put((True, fn()))
            except BaseException as exc:  # 在线程边界传回，主线程统一处理
                result_queue.put((False, exc))

        threading.Thread(target=worker, daemon=True).start()
        deadline = time.monotonic() + remaining
        heartbeat = 5.0
        while True:
            wait_for = min(heartbeat, max(0.0, deadline - time.monotonic()))
            if wait_for <= 0:
                raise _BudgetExpired
            try:
                ok, value = result_queue.get(timeout=wait_for)
            except queue.Empty:
                if time.monotonic() >= deadline:
                    raise _BudgetExpired
                self._emit(event_sink, {"event": EVENT_PROGRESS, "text": waiting_text})
                continue
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
