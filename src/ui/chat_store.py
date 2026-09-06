"""Transactional public chat history. Raw provider messages never enter this store."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from roco_pvp_agent.events import PublicEvent
from roco_pvp_agent.results import AssistantResult
from roco_pvp_agent.advisor.presentation import present_round

ACTIVE = frozenset({"pending", "running", "cancelling"})


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class ChatError(Exception):
    def __init__(self, code, status=409, **details):
        super().__init__(code)
        self.code, self.status, self.details = code, status, details


class SQLiteChatSessionStore:
    """One guarded connection; transactions never span model/tool waits."""

    def __init__(self, path):
        self.path = Path(path).resolve()
        self._lock = threading.RLock()
        self._db = None
        self._process_lock = None

    def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = open(str(self.path) + ".lock", "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                if lock.seek(0, 2) == 0:
                    lock.write(b"0")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            raise RuntimeError("聊天数据库已由另一服务进程使用；请使用单 worker") from None
        self._process_lock = lock
        try:
            self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("PRAGMA busy_timeout=5000")
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("不支持的聊天数据库版本；未修改原数据")
            with self.transaction() as db:
                for sql in (
                    "CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, active_turn_id TEXT, checkpoint TEXT NOT NULL DEFAULT '{}')",
                    "CREATE TABLE IF NOT EXISTS turns(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), request_id TEXT NOT NULL, message TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, finished_at TEXT, last_seq INTEGER NOT NULL DEFAULT 0, snapshot TEXT NOT NULL, UNIQUE(session_id,request_id))",
                    "CREATE TABLE IF NOT EXISTS events(turn_id TEXT NOT NULL REFERENCES turns(id), seq INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY(turn_id,seq))",
                    "CREATE TABLE IF NOT EXISTS tool_details(turn_id TEXT NOT NULL REFERENCES turns(id), execution_id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(turn_id,execution_id))",
                    "CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY, turn_id TEXT NOT NULL REFERENCES turns(id), version INTEGER NOT NULL, data TEXT NOT NULL)",
                    "CREATE INDEX IF NOT EXISTS turns_session_order ON turns(session_id, created_at, id)",
                ):
                    db.execute(sql)
                db.execute("PRAGMA user_version=1")
        except Exception:
            self.close()
            raise

    def close(self):
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None
            if self._process_lock is not None:
                lock, self._process_lock = self._process_lock, None
                if os.name == "nt":
                    import msvcrt
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                lock.close()

    @contextmanager
    def transaction(self):
        with self._lock:
            if self._db is None:
                raise RuntimeError("Chat store is not open")
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def new_session(self):
        sid, ts = "s_" + uuid4().hex, now()
        with self.transaction() as db:
            db.execute("INSERT INTO sessions(id,created_at,updated_at) VALUES(?,?,?)", (sid, ts, ts))
        return self.session(sid)

    def session(self, sid):
        with self._lock:
            row = self._db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
            if row is None:
                raise ChatError("SESSION_NOT_FOUND", 404)
            return {k: row[k] for k in ("id", "created_at", "updated_at", "active_turn_id")}

    def checkpoint(self, sid):
        with self._lock:
            self.session(sid)
            return json.loads(self._db.execute("SELECT checkpoint FROM sessions WHERE id=?", (sid,)).fetchone()[0])

    def _row(self, db, tid):
        row = db.execute("SELECT * FROM turns WHERE id=?", (tid,)).fetchone()
        if row is None:
            raise ChatError("TURN_NOT_FOUND", 404)
        return row

    @staticmethod
    def _snapshot(row):
        snap = json.loads(row["snapshot"])
        snap.update(status=row["status"], last_seq=row["last_seq"], finished_at=row["finished_at"])
        return snap

    def turn(self, tid):
        with self._lock:
            return self._snapshot(self._row(self._db, tid))

    def create_turn(self, sid, request_id, message, budget, max_active=4):
        with self.transaction() as db:
            session = self.session(sid)
            old = db.execute("SELECT * FROM turns WHERE session_id=? AND request_id=?", (sid, request_id)).fetchone()
            if old is not None:
                if old["message"] != message:
                    raise ChatError("REQUEST_ID_CONFLICT")
                return self._snapshot(old), False
            if session["active_turn_id"]:
                raise ChatError("SESSION_BUSY", active_turn_id=session["active_turn_id"])
            count = db.execute("SELECT count(*) FROM turns WHERE status IN ('pending','running','cancelling')").fetchone()[0]
            if count >= max_active:
                raise ChatError("SERVER_BUSY", 429)
            tid, ts = "t_" + uuid4().hex, now()
            snap = {"session_id": sid, "turn_id": tid, "request_id": request_id,
                    "message": message, "created_at": ts, "rounds": {}, "result": None,
                    "budget": budget, "offline": False}
            db.execute("INSERT INTO turns(id,session_id,request_id,message,status,created_at,snapshot) VALUES(?,?,?,?,?,?,?)",
                       (tid, sid, request_id, message, "running", ts, dump(snap)))
            db.execute("UPDATE sessions SET active_turn_id=?,updated_at=? WHERE id=?", (tid, ts, sid))
            self._append(db, tid, "turn.started", {"budget": budget, "started_at": ts})
            return self._snapshot(self._row(db, tid)), True

    def _append(self, db, tid, event, payload, rid=None, index=None, details=None):
        row = self._row(db, tid)
        if row["status"] not in ACTIVE:
            return None
        seq = row["last_seq"] + 1
        data = PublicEvent(event=event, session_id=row["session_id"], turn_id=tid,
            seq=seq, created_at=now(), round_id=rid, round_index=index, payload=payload).model_dump()
        snap = json.loads(row["snapshot"])
        if rid:
            rounds = snap["rounds"]
            round_ = rounds.setdefault(rid, {"round_id": rid, "round_index": index,
                "status": "running", "title": "正在分析请求…", "tools": {},
                "thought_summary": None, "summary_status": "pending", "summary": []})
            if event == "round.summary":
                round_.update(thought_summary=payload.get("text"), summary_status=payload["status"])
            elif event == "round.progress":
                round_.update(phase=payload.get("phase"), progress=payload.get("text"))
                for key in ("summary", "has_warnings"):
                    if key in payload:
                        round_[key] = payload[key]
            elif event == "round.completed":
                round_.update(payload)
                if round_["summary_status"] == "pending":
                    round_["summary_status"] = "missing"
            elif event.startswith("tool."):
                xid = payload["tool_execution_id"]
                round_["tools"].setdefault(xid, {}).update(payload)
                if details is not None:
                    db.execute("INSERT OR REPLACE INTO tool_details VALUES(?,?,?)", (tid, xid, dump(details)))
        if event == "reply":
            snap["result"] = payload["result"]
            snap["offline"] = payload.get("offline", False)
        db.execute("INSERT INTO events VALUES(?,?,?)", (tid, seq, dump(data)))
        db.execute("UPDATE turns SET last_seq=?,snapshot=? WHERE id=?", (seq, dump(snap), tid))
        return data

    def append(self, tid, event, payload, rid=None, index=None, details=None):
        with self.transaction() as db:
            return self._append(db, tid, event, payload, rid, index, details)

    def cancel(self, tid):
        with self.transaction() as db:
            row = self._row(db, tid)
            if row["status"] in ("pending", "running"):
                self._append(db, tid, "turn.cancelling", {"status": "cancelling"})
                db.execute("UPDATE turns SET status='cancelling' WHERE id=?", (tid,))
            return self._snapshot(self._row(db, tid))

    def finish(self, tid, result: AssistantResult, checkpoint=None, offline=False):
        with self.transaction() as db:
            row = self._row(db, tid)
            if row["status"] not in ACTIVE:
                return self._snapshot(row)
            if row["status"] == "cancelling" and result.status != "interrupted":
                result = AssistantResult(message="（本次请求已停止。）", kind="partial",
                    status="cancelled", reason_code="cancelled", usage=result.usage)
                checkpoint = None
            snap = json.loads(row["snapshot"])
            for rid, round_ in snap["rounds"].items():
                if round_["status"] == "running":
                    ending = result.status if result.status in ("cancelled", "timed_out", "interrupted") else "failed"
                    for tool in round_["tools"].values():
                        if tool.get("status") == "running":
                            tool = {**tool, "status": ending, "domain_status": "error",
                                    "result_summary": "执行已中断", "duration_ms": None}
                            self._append(db, tid, "tool.completed", tool, rid, round_["round_index"])
                    payload = {"status": ending, **present_round(list(round_["tools"].values()), ending)}
                    self._append(db, tid, "round.completed", payload, rid, round_["round_index"])
            if result.advice is not None and result.kind == "team_advice":
                aid = "a_" + uuid4().hex
                db.execute("INSERT INTO artifacts VALUES(?,?,?,?)", (aid, tid, 1, dump({
                    "advice": result.advice, "validation": {"main_team": "valid", "alternatives": "unverified"}})))
                result = result.model_copy(update={"artifacts": [{"artifact_id": aid, "artifact_version": 1, "type": "team_advice"}]})
            self._append(db, tid, "reply", {"result": result.model_dump(), "offline": offline})
            last = self._row(db, tid)["last_seq"] + 1
            self._append(db, tid, "done", {"status": result.status, "last_seq": last, "usage": result.usage})
            ts = now()
            db.execute("UPDATE turns SET status=?,finished_at=? WHERE id=?", (result.status, ts, tid))
            db.execute("UPDATE sessions SET active_turn_id=NULL,updated_at=? WHERE id=? AND active_turn_id=?", (ts, row["session_id"], tid))
            if checkpoint is not None and result.status in ("completed", "degraded"):
                db.execute("UPDATE sessions SET checkpoint=? WHERE id=?", (dump(checkpoint), row["session_id"]))
            return self._snapshot(self._row(db, tid))

    def events(self, tid, after=0, limit=200):
        with self._lock:
            row = self._row(self._db, tid)
            if after < 0 or after > row["last_seq"]:
                raise ChatError("INVALID_EVENT_CURSOR", 400)
            return [json.loads(r[0]) for r in self._db.execute(
                "SELECT data FROM events WHERE turn_id=? AND seq>? ORDER BY seq LIMIT ?", (tid, after, limit))]

    def details(self, tid, xid):
        with self._lock:
            self._row(self._db, tid)
            row = self._db.execute("SELECT data FROM tool_details WHERE turn_id=? AND execution_id=?", (tid, xid)).fetchone()
            if row is None:
                raise ChatError("TOOL_DETAILS_NOT_FOUND", 404)
            return json.loads(row[0])

    def messages(self, sid, before=None, limit=20):
        with self._lock:
            self.session(sid)
            params = [sid]
            cursor = ""
            if before:
                old = self._row(self._db, before)
                if old["session_id"] != sid:
                    raise ChatError("INVALID_MESSAGE_CURSOR", 400)
                cursor = " AND (created_at,id)<(?,?)"
                params += [old["created_at"], before]
            rows = self._db.execute("SELECT * FROM turns WHERE session_id=?" + cursor + " ORDER BY created_at DESC,id DESC LIMIT ?", (*params, limit + 1)).fetchall()
            selected = rows[:limit]
            return {"turns": [self._snapshot(r) for r in reversed(selected)],
                    "next_before": selected[-1]["id"] if len(rows) > limit else None}

    def recover(self):
        with self._lock:
            ids = [r[0] for r in self._db.execute("SELECT id FROM turns WHERE status IN ('pending','running','cancelling')")]
        for tid in ids:
            self.finish(tid, AssistantResult(message="服务已重启，上次请求中断。已完成的工作仍可查看。",
                kind="partial", status="interrupted", reason_code="service_interrupted"))
