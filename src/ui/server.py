"""FastAPI factory with lifespan-managed SQLite and durable chat coordination.

New clients create tasks by POST, then subscribe to read-only SSE or poll snapshots.
Legacy endpoints preserve their wire format through the same coordinator.
Team and battle routes share the static application without importing storage into the engine.
"""

import asyncio
import json
import queue
import threading
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from roco_pvp_agent.advisor.agent import TeamAdvisorAgent
from roco_pvp_agent.agent import EVENT_DONE, EVENT_REPLY
from roco_pvp_agent.config import Settings, get_settings
from .legacy_chat import DurableChatContext
from .routes_battle import router as battle_router
from .routes_team import router as team_router
from .routes_chat import router as chat_router
from .routes_chat_artifacts import router as artifacts_router
from .chat_store import SQLiteChatSessionStore, ChatError
from .turn_coordinator import TurnCoordinator

STATIC_DIR = Path(__file__).parent / "static"

# 队列结束哨兵：后台线程塞完所有事件后塞入，SSE 生成器据此断开。
_SENTINEL = object()
SERVER_ERROR_REPLY = "（服务暂时异常，已安全结束本次请求，请稍后重试。）"


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: Optional[str] = None

    @field_validator('message')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('消息不能为空')
        return value

    model_config = {"extra": "forbid"}  # 前端发了多余字段直接 400，早暴露问题


class ResetBody(BaseModel):
    session_id: str = Field(min_length=1)

    model_config = {"extra": "forbid"}


def _meta_event(session_id: str) -> dict:
    return {"event": "meta", "session_id": session_id}


def _run_chat(
    context: DurableChatContext,
    session_id: str,
    message: str,
    put: Callable[[object], None],
    cancel_event: threading.Event,
) -> None:
    """后台线程入口：跑一次对话，事件逐个入队；任何异常降级为错误回复（宁失败不抛）。"""
    try:
        context.chat(
            message, session_id, event_sink=put, cancel_event=cancel_event)
    except Exception:
        put({
            "event": EVENT_REPLY,
            "text": SERVER_ERROR_REPLY,
            "offline": False,
            "rounds": 0,
        })
        # 前端只在 done 时解除输入框 busy 状态；异常路径也必须完整终结事件流。
        put({"event": EVENT_DONE})
    finally:
        put(_SENTINEL)


def _serialize_history(messages) -> list[dict]:
    """历史消息序列化为前端可渲染的 dict（不暴露内部消息对象）。"""
    out = []
    for m in messages:
        content = m.content
        if isinstance(content, list):
            content = "".join(
                str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text"
            )
        out.append({"type": m.type, "content": str(content or "")})
    return out


class _CancellableStreamingResponse(StreamingResponse):
    """Cancel tools even when disconnect happens while the generator is yielding."""

    def __init__(self, *args, cancel_event: threading.Event, **kwargs):
        super().__init__(*args, **kwargs)
        self._cancel_event = cancel_event

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._cancel_event.set()


def create_chat_app(settings: Settings, *, llm_factory: Optional[Callable] = None,
                    chat_store: SQLiteChatSessionStore | None = None) -> FastAPI:
    llm = llm_factory(settings) if llm_factory else None
    agent = TeamAdvisorAgent(settings, llm=llm)

    @asynccontextmanager
    async def lifespan(app):
        store = chat_store or SQLiteChatSessionStore(settings.chat_db_path)
        store.open()
        try:
            store.recover()
            coordinator = TurnCoordinator(agent, store, max_active=settings.chat_max_concurrent_turns,
                                          max_chars=settings.chat_context_max_chars, max_turns=settings.chat_context_max_turns)
            app.state.turn_coordinator = coordinator
            try:
                yield
            finally:
                await asyncio.to_thread(coordinator.close)
                app.state.turn_coordinator = None
        finally:
            store.close()

    app = FastAPI(title="Roco PVP Agent", lifespan=lifespan)
    app.state.agent = agent
    app.state.context = DurableChatContext(app)
    app.state.settings = settings

    @app.exception_handler(ChatError)
    async def chat_error_handler(request, exc):
        messages = {
            'SESSION_BUSY': '当前会话已有请求运行，请等待结束或先停止。',
            'SESSION_ARCHIVED': '会话已归档，请先恢复会话。',
            'SESSION_NOT_FOUND': '找不到该会话。', 'SESSION_DELETED': '该会话已删除。',
            'REVISION_CONFLICT': '会话已在其他页面更新，请刷新后重试。',
            'REQUEST_ID_CONFLICT': '该请求编号已用于不同内容，请重新提交。',
            'SERVER_BUSY': '当前运行请求较多，请稍后重试，草稿已保留。',
            'ARTIFACT_NOT_FOUND': '找不到该队伍建议，原会话可能已删除。',
            'ARTIFACT_VERSION_CONFLICT': '队伍版本不匹配，请重新打开建议。',
            'CHAT_STORAGE_UNAVAILABLE': '聊天存储暂不可用，请检查存储后重启服务。',
            'INVALID_RETRY': '无法重试这条请求，请选择当前会话中已结束的请求。',
            'TEAM_INVALID': '队伍未通过当前规则校验，请在组队页修复。',
            'ALTERNATIVE_INVALID': '该备选尚未通过校验，不能作为合法队伍保存。',
        }
        return JSONResponse({"error": exc.code, 'message': messages.get(exc.code, '请求未能完成，请刷新后重试。'), **exc.details}, status_code=exc.status)

    @app.exception_handler(sqlite3.Error)
    async def storage_error_handler(request, exc):
        return JSONResponse({"error": "CHAT_STORAGE_UNAVAILABLE", "message": "聊天记录存储暂不可用。"}, status_code=503)

    # ---------- 基础 ----------

    @app.get("/api/health")
    def health() -> dict:
        sandbox = app.state.agent.sandbox_health
        return {
            "status": "degraded" if sandbox.enabled and not sandbox.healthy else "ok",
            "sandbox": sandbox.to_public_dict(),
        }

    @app.get("/api/config")
    def config() -> dict:
        s = app.state.settings
        # 脱敏：绝不返回 api_key
        return {
            "model": s.model,
            "has_api_key": s.has_api_key,
            "debug": s.debug,
            "chat_budget": {"max_llm_rounds": agent._max_llm_rounds,
                            "max_total_seconds": agent._max_total_seconds},
        }

    # ---------- 同步兜底（SSE 不可用时的 POST 回退） ----------

    @app.post("/api/chat")
    def chat_sync(body: ChatBody) -> dict:
        ctx = app.state.context
        session_id = body.session_id or ctx.new_session()
        try:
            reply = ctx.chat(body.message, session_id)
        except ChatError:
            raise
        except Exception:
            # SSE 不可用时前端会走此同步兜底；异常也返回稳定 JSON，避免 500/白屏。
            return {
                "session_id": session_id,
                "reply": SERVER_ERROR_REPLY,
                "thinking": [],
                "tool_calls": [],
                "offline": False,
                "rounds": 0,
                "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            }
        return {
            "session_id": session_id,
            "reply": reply.reply,
            "thinking": reply.thinking,
            "tool_calls": reply.tool_calls,
            "offline": reply.offline,
            "rounds": reply.rounds,
            "usage": reply.usage,
        }

    # ---------- SSE 流式 ----------

    @app.get("/api/chat/stream")
    async def chat_stream(message: str, session_id: Optional[str] = None):
        if not message or not message.strip() or len(message) > 2000:
            raise HTTPException(status_code=400, detail="message 不能为空")
        ctx = app.state.context
        session_id = session_id or ctx.new_session()

        ctx.coordinator.store.session(session_id)

        events: "queue.Queue" = queue.Queue()
        cancel_event = threading.Event()
        events.put_nowait(_meta_event(session_id))
        threading.Thread(
            target=_run_chat,
            args=(ctx, session_id, message, events.put_nowait, cancel_event),
            daemon=True,
        ).start()

        async def gen():
            try:
                while True:
                    item = await asyncio.to_thread(events.get)
                    if item is _SENTINEL:
                        break
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            finally:
                # 客户端断开 SSE 时，协作式取消会一路传到沙箱进程组。
                cancel_event.set()

        return _CancellableStreamingResponse(
            gen(), media_type="text/event-stream", cancel_event=cancel_event)

    # ---------- 会话管理 ----------

    @app.post("/api/chat/reset")
    def reset(body: ResetBody) -> dict:
        app.state.context.reset(body.session_id)
        return {"ok": True}

    @app.get("/api/chat/history")
    def history(session_id: str) -> dict:
        return {"session_id": session_id, "history": _serialize_history(app.state.context.get_history(session_id))}

    # ---------- 静态资源 ----------

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/chat/{session_id}")
    def chat_page(session_id: str) -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/team")
    def team_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "team.html")

    @app.get("/battle")
    def battle_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "battle.html")

    @app.get("/spectate")
    def spectate_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "spectate.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # ---------- 组队模式（精灵搜索 + 校验 + 队伍持久化，见 routes_team.py） ----------
    app.include_router(team_router)
    app.include_router(chat_router)
    app.include_router(artifacts_router)

    # ---------- 对战模式（人类 vs LLM，见 routes_battle.py） ----------
    app.include_router(battle_router)

    return app


# 模块底部实例：`uvicorn ui.server:app` 或 `python -m ui` 直接可用。
app = create_chat_app(get_settings())
