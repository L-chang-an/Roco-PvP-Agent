"""FastAPI 应用工厂 + REST/SSE 路由。

设计要点：
- create_chat_app(settings, *, llm_factory=None) 应用工厂：llm_factory 是测试注入缝。
- SSE 只支持 GET → message/session_id 走 query params（EventSource 无法带 body）。
- 后台 daemon 线程跑 agent，事件经 queue.Queue 送回，asyncio.to_thread 阻塞读不堵事件循环。
- 事件顺序契约：meta → thinking* → tool* → reply → done（meta 由本服务补发）。
"""

import asyncio
import json
import queue
import threading
from pathlib import Path
from typing import Callable, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..agent import EVENT_DONE, EVENT_REPLY, ChatAgent
from ..config import Settings, get_settings
from .context import ChatContext

STATIC_DIR = Path(__file__).parent / "static"

# 队列结束哨兵：后台线程塞完所有事件后塞入，SSE 生成器据此断开。
_SENTINEL = object()


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: Optional[str] = None

    model_config = {"extra": "forbid"}  # 前端发了多余字段直接 400，早暴露问题


class ResetBody(BaseModel):
    session_id: str = Field(min_length=1)

    model_config = {"extra": "forbid"}


def _meta_event(session_id: str) -> dict:
    return {"event": "meta", "session_id": session_id}


def _run_chat(
    context: ChatContext,
    session_id: str,
    message: str,
    put: Callable[[object], None],
) -> None:
    """后台线程入口：跑一次对话，事件逐个入队；任何异常降级为错误回复（宁失败不抛）。"""
    try:
        context.chat(message, session_id, event_sink=put)
    except Exception as exc:
        put({"event": EVENT_REPLY, "text": f"（服务端错误：{exc}）", "offline": False, "rounds": 0})
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


def create_chat_app(settings: Settings, *, llm_factory: Optional[Callable] = None) -> FastAPI:
    llm = llm_factory(settings) if llm_factory else None
    agent = ChatAgent(settings, llm=llm)
    context = ChatContext(agent)

    app = FastAPI(title="Rock PVP Agent")
    app.state.agent = agent
    app.state.context = context
    app.state.settings = settings

    # ---------- 基础 ----------

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/config")
    def config() -> dict:
        s = app.state.settings
        # 脱敏：绝不返回 api_key
        return {
            "model": s.model,
            "has_api_key": s.has_api_key,
            "debug": s.debug,
        }

    # ---------- 同步兜底（SSE 不可用时的 POST 回退） ----------

    @app.post("/api/chat")
    def chat_sync(body: ChatBody) -> dict:
        ctx = app.state.context
        session_id = body.session_id or ctx.new_session()
        reply = ctx.chat(body.message, session_id)
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
        if not message or not message.strip():
            raise HTTPException(status_code=400, detail="message 不能为空")
        ctx = app.state.context
        session_id = session_id or ctx.new_session()

        events: "queue.Queue" = queue.Queue()
        events.put_nowait(_meta_event(session_id))
        threading.Thread(
            target=_run_chat,
            args=(ctx, session_id, message, events.put_nowait),
            daemon=True,
        ).start()

        async def gen():
            while True:
                item = await asyncio.to_thread(events.get)
                if item is _SENTINEL:
                    break
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

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

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app


# 模块底部实例：`uvicorn rock_pvp_agent.ui.server:app` 或 `python -m rock_pvp_agent.ui` 直接可用。
app = create_chat_app(get_settings())
