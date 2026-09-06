"""Creation is POST-only. Event subscriptions never execute or cancel a turn."""
import asyncio
import json
import sqlite3
import time

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .chat_store import ACTIVE, ChatError

router = APIRouter(prefix="/api/chat")


class TurnBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=2000)

    @field_validator("message", "request_id")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("不能为空白")
        return value.strip()


def services(request):
    coordinator = getattr(request.app.state, "turn_coordinator", None)
    if coordinator is None:
        raise ChatError("CHAT_NOT_STARTED", 503)
    return coordinator, coordinator.store


@router.post("/sessions", status_code=201)
def create_session(request: Request):
    return services(request)[1].new_session()


@router.get("/sessions/{sid}")
def session(sid: str, request: Request):
    return services(request)[1].session(sid)


@router.get("/sessions/{sid}/messages")
def messages(sid: str, request: Request, before: str | None = None, limit: int = Query(20, ge=1, le=100)):
    return services(request)[1].messages(sid, before, limit)


@router.post("/sessions/{sid}/turns")
def create_turn(sid: str, body: TurnBody, request: Request):
    coordinator, _ = services(request)
    snap, created = coordinator.create(sid, body.request_id, body.message)
    return JSONResponse({**snap, "events_url": f"/api/chat/turns/{snap['turn_id']}/events"}, status_code=202 if created else 200)


@router.get("/turns/{tid}")
def turn(tid: str, request: Request):
    return services(request)[0].get(tid)


@router.post("/turns/{tid}/cancel")
def cancel(tid: str, request: Request):
    return services(request)[0].cancel(tid)


@router.get("/turns/{tid}/tools/{xid}")
def details(tid: str, xid: str, request: Request):
    return services(request)[1].details(tid, xid)


@router.get("/turns/{tid}/events")
async def events(tid: str, request: Request, after_seq: int = Query(0, ge=0),
                 last_event_id: str | None = Header(None)):
    coordinator, store = services(request)
    try:
        cursor = int(last_event_id) if last_event_id is not None else after_seq
    except ValueError:
        raise ChatError("INVALID_EVENT_CURSOR", 400) from None
    coordinator.get(tid)
    store.events(tid, cursor)  # Validate before response headers are sent.

    async def generate():
        nonlocal cursor
        heartbeat = time.monotonic()
        while not await request.is_disconnected():
            try:
                snap = coordinator.get(tid)
                rows = store.events(tid, cursor)
            except (ChatError, sqlite3.Error):
                yield 'data: {"event":"stream.error","message":"保存或读取失败，执行状态需重新确认。"}\n\n'
                return
            for row in rows:
                cursor = row["seq"]
                yield f"id: {cursor}\ndata: {json.dumps(row, ensure_ascii=False)}\n\n"
            if not rows and snap["status"] not in ACTIVE and cursor >= snap["last_seq"]:
                return
            if time.monotonic() - heartbeat >= 5:
                yield ": heartbeat\n\n"
                heartbeat = time.monotonic()
            await asyncio.sleep(0.1)

    return StreamingResponse(generate(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
