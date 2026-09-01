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
import time
from typing import Callable, Optional

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from .config import Settings
from .llm import build_chat_llm
from .tools import FINAL_ANSWER_TOOL, build_agent_tools

EMPTY_REPLY = "（模型未返回有效回复）"
OFFLINE_HINT = "未配置 LLM_API_KEY，当前处于离线模式，仅返回确定性回复。请配置 .env 后重启以获得完整对话能力。"
MAX_ROUNDS_FALLBACK = "（达到最大轮数仍未获得最终答案，请稍后再试或调整问题）"

# 最后一轮的强制终结引导（Harness 注入，避免模型无限调非终结工具耗尽轮次）。
# 只在最后一轮 invoke 前临时附加到消息列表，**不写入历史**（不污染后续对话）。
LAST_ROUND_HINT = (
    "（Harness 提示：这是最后一轮。你已收集足够信息，请立即调用终结工具"
    "（submit_team_advice 或 final_answer）给出终稿，不要再调用其他工具。）"
)

# 事件类型：thinking / tool / reply / done（meta 由 SSE 服务端补发）
EVENT_THINKING = "thinking"
EVENT_TOOL = "tool"
EVENT_REPLY = "reply"
EVENT_DONE = "done"
EVENT_PROGRESS = "progress"


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
        terminal_tools=None,
        emit_thinking: bool = True,
        max_total_seconds: Optional[float] = None,
    ):
        self._settings = settings
        # 工具集注入缝：顾问用 advisor 工具集，默认仍是基础 [final_answer]。
        self._tools = tools if tools is not None else build_agent_tools()
        self._llm = llm  # 测试注入缝
        self._system_prompt = system_prompt
        self._max_llm_rounds = max_llm_rounds
        # 终结工具名集合（顾问 = submit_team_advice + final_answer）。
        self._terminal_tools = frozenset(terminal_tools) if terminal_tools is not None \
            else frozenset({FINAL_ANSWER_TOOL})
        self._emit_thinking = emit_thinking      # 思维链外显开关（顾问关闭）
        # 整体时间预算（秒）：超时强制终结，防止用户无限等待（"1 分钟内给答案"的兜底）。
        self._max_total_seconds = max_total_seconds

    @property
    def has_llm(self) -> bool:
        return self._llm is not None or self._settings.has_api_key

    def _get_llm(self):
        if self._llm is not None:
            return self._llm
        return build_chat_llm(self._settings, self._tools)

    # ---------- 对外入口 ----------

    def chat(
        self,
        message: str,
        history: Optional[list[BaseMessage]] = None,
        *,
        event_sink: Optional[Callable[[dict], None]] = None,
    ) -> ChatReply:
        history = list(history or [])
        if not self.has_llm:
            return self._offline_chat(message, history, event_sink)
        return self._run_online(message, history, event_sink)

    # ---------- 离线降级 ----------

    def _offline_chat(
        self,
        message: str,
        history: list[BaseMessage],
        event_sink: Optional[Callable[[dict], None]],
    ) -> ChatReply:
        reply_text = f"[离线回复] 收到你的消息：{message}\n\n{OFFLINE_HINT}"
        history = history + [
            HumanMessage(content=message),
            AIMessage(content=reply_text),
        ]
        reply_obj = ChatReply(reply=reply_text, history=history, offline=True, rounds=0)
        self._emit_reply_events(event_sink, reply_obj)
        return reply_obj

    # ---------- 在线工具循环 ----------

    def _run_online(
        self,
        message: str,
        history: list[BaseMessage],
        event_sink: Optional[Callable[[dict], None]],
    ) -> ChatReply:
        working: list[BaseMessage] = [SystemMessage(content=self._system_prompt)]
        working.extend(history)
        working.append(HumanMessage(content=message))

        llm = self._get_llm()
        tools_map = {getattr(t, "name", ""): t for t in self._tools}

        tool_log: list[dict] = []
        thinking: list[str] = []
        usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        reply_text = EMPTY_REPLY
        rounds = 0
        start_ts = time.monotonic()

        for rounds in range(1, self._max_llm_rounds + 1):
            # 时间预算：超时立即终结，不再发起新一轮慢调用（"1 分钟内给答案"的兜底）。
            if self._max_total_seconds is not None \
                    and time.monotonic() - start_ts > self._max_total_seconds:
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

            # 最后一轮：临时附加强制终结引导（不写入 working/历史，避免污染后续对话）。
            invoke_messages = working
            if rounds == self._max_llm_rounds:
                invoke_messages = working + [SystemMessage(content=LAST_ROUND_HINT)]

            response = llm.invoke(invoke_messages)
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

            # 逐个执行工具调用（一次回复可带多个，顺序执行）。
            # 关键：每个 tool_use 都必须紧跟 tool_result（含终结工具），
            # 否则历史重放时 OpenAI/Anthropic 网关会报 400（tool_use 无对应 tool_result）。
            terminal = False
            for call in calls:
                name = call.get("name", "")
                args = call.get("args", {})
                call_id = call.get("id", "")

                if name in self._terminal_tools:
                    result_content, terminal = self._handle_terminal(name, args, call_id)
                    if terminal:
                        reply_text = result_content
                    working.append(ToolMessage(content=result_content, tool_call_id=call_id))
                    continue

                result = self._invoke_tool(tools_map, call)
                tool_log.append({"name": name, "args": args, "result": result})
                working.append(ToolMessage(content=result, tool_call_id=call_id))
                self._emit(event_sink, {"event": EVENT_TOOL, "name": name, "args": args, "result": result})

            if terminal:
                break
        else:
            # 轮次耗尽仍未获得终稿 → 交给 _handle_exhausted（默认 = MAX_ROUNDS_FALLBACK，
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
            usage=usage,
        )
        self._emit_reply_events(event_sink, reply_obj)
        return reply_obj

    def _handle_terminal(self, name: str, args: dict, call_id: str) -> tuple[str, bool]:
        """终结工具处理钩子。返回 (tool_result_content, terminal)。

        默认 = final_answer 语义：取 text 为终稿并终结。顾问覆写为解析结构化建议 +
        EvidenceGate 校验（失败时 terminal=False 让模型修复一次）。
        """
        return str(args.get("text", "")) or EMPTY_REPLY, True

    def _handle_no_tool_call(self, content: str) -> str:
        """无工具调用的兜底钩子。默认 = 以 content 为终稿（空则 EMPTY_REPLY）。

        顾问覆写为「必须走 submit_team_advice」的提示（拒绝自由文本终稿）。
        """
        return content or EMPTY_REPLY

    def _handle_exhausted(self, reason: str, tool_log: list[dict], thinking: list[str],
                          event_sink: Optional[Callable[[dict], None]]) -> str:
        """轮次耗尽（reason="rounds"）或超时（reason="timeout"）的兜底钩子。

        默认返回 MAX_ROUNDS_FALLBACK；顾问覆写为「列出已查了哪些工具 + 缺什么」的
        有信息降级答案，避免用户拿到一句空话。
        """
        return MAX_ROUNDS_FALLBACK

    @staticmethod
    def _accumulate_usage(usage: dict, response) -> None:
        """把一次 LLM 响应的用量累加进统计（网关不给 usage 时保持 0）。

        提示缓存命中字段（`cache_read_tokens` 等）**只在网关真的返回时才出现**——
        不塞零值，避免把「网关不报」与「零命中」混为一谈。
        """
        from .llm import accumulate_usage
        accumulate_usage(usage, response)

    def _invoke_tool(self, tools_map: dict, call: dict) -> str:
        """执行单个工具调用；任何异常都吞成错误字符串（宁失败不抛）。"""
        name = call.get("name", "")
        tool = tools_map.get(name)
        if tool is None:
            return f"未知工具：{name}"
        try:
            result = tool.invoke(call.get("args", {}))
            return str(result)
        except Exception as exc:
            return f"工具 {name} 执行失败：{exc}"

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
