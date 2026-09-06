"""Transactional public chat history. Raw provider messages never enter this store."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import hashlib
import base64
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
            if version not in (0, 1, 2):
                raise RuntimeError("不支持的聊天数据库版本；未修改原数据")
            if version == 1:
                backup = sqlite3.connect(str(self.path) + '.v1-backup-' + uuid4().hex + '.sqlite3')
                try:
                    self._db.backup(backup)
                finally:
                    backup.close()
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
                if version < 2:
                    for column in (
                        "title TEXT NOT NULL DEFAULT '新会话'", "title_source TEXT NOT NULL DEFAULT 'auto'",
                        "archived_at TEXT", "deleted_at TEXT", "revision INTEGER NOT NULL DEFAULT 0",
                        "recovery_warning TEXT",
                    ):
                        db.execute('ALTER TABLE sessions ADD COLUMN ' + column)
                    db.execute('ALTER TABLE turns ADD COLUMN request_hash TEXT')
                    db.execute('ALTER TABLE turns ADD COLUMN retry_of TEXT')
                    for row in db.execute('SELECT id,message FROM turns').fetchall():
                        db.execute('UPDATE turns SET request_hash=? WHERE id=?', (request_hash(row['message']), row['id']))
                    for row in db.execute('SELECT id FROM sessions').fetchall():
                        first = db.execute('SELECT message FROM turns WHERE session_id=? ORDER BY created_at,id LIMIT 1', (row['id'],)).fetchone()
                        if first:
                            db.execute('UPDATE sessions SET title=? WHERE id=?', (first[0].strip()[:24], row['id']))
                db.execute("CREATE TABLE IF NOT EXISTS artifact_saves(artifact_id TEXT NOT NULL REFERENCES artifacts(id), request_id TEXT NOT NULL, intent TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(artifact_id,request_id))")
                db.execute("CREATE TABLE IF NOT EXISTS artifact_validations(artifact_id TEXT PRIMARY KEY REFERENCES artifacts(id), data TEXT NOT NULL)")
                db.execute("CREATE INDEX IF NOT EXISTS sessions_order ON sessions(updated_at DESC,id DESC)")
                db.execute("PRAGMA user_version=2")
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
            if row['deleted_at']:
                raise ChatError('SESSION_DELETED', 410)
            return {k: row[k] for k in ("id", "created_at", "updated_at", "active_turn_id", "title", "title_source", "archived_at", "revision", "recovery_warning")}

    def list_sessions(self, cursor=None, limit=20, archived=False, q=''):
        with self._lock:
            args = []
            where = 'deleted_at IS NULL AND archived_at IS ' + ('NOT NULL' if archived else 'NULL')
            if q:
                where += " AND instr(lower(title),lower(?))>0"
                args.append(q)
            if cursor:
                try:
                    stamp, sid = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                    if not isinstance(stamp, str) or not isinstance(sid, str):
                        raise ValueError()
                except Exception:
                    raise ChatError('INVALID_SESSION_CURSOR', 400) from None
                where += ' AND (updated_at,id)<(?,?)'
                args += [stamp, sid]
            rows = self._db.execute('SELECT * FROM sessions WHERE ' + where + ' ORDER BY updated_at DESC,id DESC LIMIT ?', (*args, limit + 1)).fetchall()
            selected = rows[:limit]
            items = []
            for row in selected:
                item = self.session(row['id'])
                last = self._db.execute('SELECT snapshot,status FROM turns WHERE session_id=? ORDER BY created_at DESC,id DESC LIMIT 1', (row['id'],)).fetchone()
                snap = json.loads(last['snapshot']) if last else {}
                preview = ((snap.get('result') or {}).get('message') or snap.get('message', ''))[:100]
                if (snap.get('result') or {}).get('kind') == 'team_advice' and preview.lstrip().startswith('{'):
                    preview = '队伍建议已生成'
                item.update(preview=preview, last_status=last['status'] if last else None)
                items.append(item)
            next_cursor = base64.urlsafe_b64encode(dump([selected[-1]['updated_at'], selected[-1]['id']]).encode()).decode() if len(rows) > limit else None
            return {'sessions': items, 'next_cursor': next_cursor}

    def update_session(self, sid, revision, *, title=None, archived=None):
        with self.transaction() as db:
            current = self.session(sid)
            if current['revision'] != revision:
                raise ChatError('REVISION_CONFLICT', current=current)
            if archived is not None and current['active_turn_id']:
                raise ChatError('SESSION_BUSY', active_turn_id=current['active_turn_id'])
            if title is not None:
                db.execute("UPDATE sessions SET title=?,title_source='manual' WHERE id=?", (title, sid))
            if archived is not None:
                db.execute('UPDATE sessions SET archived_at=? WHERE id=?', (now() if archived else None, sid))
            db.execute('UPDATE sessions SET revision=revision+1,updated_at=? WHERE id=?', (now(), sid))
            return self.session(sid)

    def clear_session(self, sid, *, delete=False, revision=None):
        with self.transaction() as db:
            current = self.session(sid)
            if revision is not None and current['revision'] != revision:
                raise ChatError('REVISION_CONFLICT', current=current)
            if current['active_turn_id']:
                raise ChatError('SESSION_BUSY', active_turn_id=current['active_turn_id'])
            for table in ('artifact_saves', 'artifact_validations'):
                db.execute(f'DELETE FROM {table} WHERE artifact_id IN (SELECT id FROM artifacts WHERE turn_id IN (SELECT id FROM turns WHERE session_id=?))', (sid,))
            for table in ('events', 'tool_details', 'artifacts'):
                db.execute(f'DELETE FROM {table} WHERE turn_id IN (SELECT id FROM turns WHERE session_id=?)', (sid,))
            db.execute('DELETE FROM turns WHERE session_id=?', (sid,))
            db.execute("UPDATE sessions SET checkpoint='{}',recovery_warning=NULL,deleted_at=?,updated_at=?,revision=revision+1 WHERE id=?", (now() if delete else None, now(), sid))

    def recovery_warning(self, sid, warning):
        with self.transaction() as db:
            db.execute('UPDATE sessions SET recovery_warning=? WHERE id=?', (warning, sid))

    def checkpoint(self, sid):
        with self._lock:
            self.session(sid)
            return json.loads(self._db.execute("SELECT checkpoint FROM sessions WHERE id=?", (sid,)).fetchone()[0])

    def latest_completed_turn(self, sid):
        with self._lock:
            row = self._db.execute("SELECT id FROM turns WHERE session_id=? AND status IN ('completed','degraded') ORDER BY created_at DESC,id DESC LIMIT 1", (sid,)).fetchone()
            return row[0] if row else None

    def _row(self, db, tid):
        row = db.execute("SELECT * FROM turns WHERE id=?", (tid,)).fetchone()
        if row is None:
            raise ChatError("TURN_NOT_FOUND", 404)
        self.session(row['session_id'])
        return row

    @staticmethod
    def _snapshot(row):
        snap = json.loads(row["snapshot"])
        snap.pop('legacy', None)
        if (snap.get('result') or {}).get('kind') == 'team_advice' and snap['result']['message'].lstrip().startswith('{'):
            snap['result']['message'] = '队伍建议已生成，完整配置见下方看板。'
        snap.update(status=row["status"], last_seq=row["last_seq"], finished_at=row["finished_at"])
        return snap

    def turn(self, tid):
        with self._lock:
            return self._snapshot(self._row(self._db, tid))

    def legacy(self, tid):
        with self._lock:
            return json.loads(self._row(self._db, tid)['snapshot']).get('legacy', {})

    def create_turn(self, sid, request_id, message, budget, max_active=4, retry_of=None):
        with self.transaction() as db:
            session = self.session(sid)
            old = db.execute("SELECT * FROM turns WHERE session_id=? AND request_id=?", (sid, request_id)).fetchone()
            if old is not None:
                if old["request_hash"] != request_hash(message, retry_of):
                    raise ChatError("REQUEST_ID_CONFLICT")
                return self._snapshot(old), False
            if session['archived_at']:
                raise ChatError('SESSION_ARCHIVED')
            if retry_of:
                retry = self._row(db, retry_of)
                if retry['session_id'] != sid or retry['status'] in ACTIVE:
                    raise ChatError('INVALID_RETRY', 422)
            if session["active_turn_id"]:
                raise ChatError("SESSION_BUSY", active_turn_id=session["active_turn_id"])
            count = db.execute("SELECT count(*) FROM turns WHERE status IN ('pending','running','cancelling')").fetchone()[0]
            if count >= max_active:
                raise ChatError("SERVER_BUSY", 429)
            tid, ts = "t_" + uuid4().hex, now()
            snap = {"session_id": sid, "turn_id": tid, "request_id": request_id,
                    "message": message, "created_at": ts, "rounds": {}, "result": None,
                    "budget": budget, "offline": False, "retry_of": retry_of}
            db.execute("INSERT INTO turns(id,session_id,request_id,message,status,created_at,snapshot) VALUES(?,?,?,?,?,?,?)",
                       (tid, sid, request_id, message, "running", ts, dump(snap)))
            db.execute('UPDATE turns SET request_hash=?,retry_of=? WHERE id=?', (request_hash(message, retry_of), retry_of, tid))
            db.execute("UPDATE sessions SET active_turn_id=?,updated_at=?,revision=revision+1 WHERE id=?", (tid, ts, sid))
            if db.execute('SELECT count(*) FROM turns WHERE session_id=?', (sid,)).fetchone()[0] == 1:
                db.execute("UPDATE sessions SET title=? WHERE id=? AND title_source='auto'", (message.strip()[:24], sid))
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

    def finish(self, tid, result: AssistantResult, checkpoint=None, offline=False, legacy=None):
        with self.transaction() as db:
            row = self._row(db, tid)
            if row["status"] not in ACTIVE:
                return self._snapshot(row)
            if row["status"] == "cancelling" and result.status != "interrupted":
                result = AssistantResult(message="（本次请求已停止。）", kind="partial",
                    status="cancelled", reason_code="cancelled", usage=result.usage)
                checkpoint = None
                legacy = None
            if legacy is not None:
                initial = json.loads(row['snapshot'])
                initial['legacy'] = legacy
                db.execute('UPDATE turns SET snapshot=? WHERE id=?', (dump(initial), tid))
                row = self._row(db, tid)
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
                from .team_service import artifact_payload
                aid = "a_" + uuid4().hex
                artifact = artifact_payload(result.advice, aid, row['session_id'], tid)
                observations = self._observations(json.loads(self._row(db, tid)['snapshot']), tid)
                for observation in observations:
                    artifact['evidence_status'][observation['category']].append(observation)
                db.execute("INSERT INTO artifacts VALUES(?,?,?,?)", (aid, tid, 1, dump(artifact)))
                result = result.model_copy(update={"artifacts": [{"artifact_id": aid, "artifact_version": 1, "type": "team_advice"}]})
                if checkpoint is not None:
                    checkpoint['conversation_state'].update(latest_team_artifact_id=aid, artifact_version=1)
            self._append(db, tid, "reply", {"result": result.model_dump(), "offline": offline})
            last = self._row(db, tid)["last_seq"] + 1
            self._append(db, tid, "done", {"status": result.status, "last_seq": last, "usage": result.usage})
            ts = now()
            db.execute("UPDATE turns SET status=?,finished_at=? WHERE id=?", (result.status, ts, tid))
            db.execute("UPDATE sessions SET active_turn_id=NULL,updated_at=?,revision=revision+1 WHERE id=? AND active_turn_id=?", (ts, row["session_id"], tid))
            if checkpoint is not None and result.status in ("completed", "degraded"):
                state = checkpoint.setdefault('conversation_state', {})
                observations = self._observations(json.loads(self._row(db, tid)['snapshot']), tid)
                state['verified_facts'] = [*state.get('verified_facts', []), *observations][-20:]
                state['evidence_refs'] = [{'turn_id': f['turn_id'], 'execution_id': f['execution_id']} for f in state['verified_facts']]
                db.execute("UPDATE sessions SET checkpoint=? WHERE id=?", (dump(checkpoint), row["session_id"]))
            return self._snapshot(self._row(db, tid))

    @staticmethod
    def _observations(snapshot, tid):
        categories = {'get_catalog_version': 'catalog', 'get_spirit_profile': 'catalog',
                      'get_skill_profile': 'catalog', 'search_spirits': 'catalog',
                      'simulate_matchups': 'simulation', 'query_trajectory_evidence': 'human'}
        facts = []
        for round_ in snapshot['rounds'].values():
            for xid, tool in round_['tools'].items():
                category = categories.get(tool.get('name'))
                # Trajectory source needs the actual request; do not guess human vs selfplay.
                if tool.get('name') == 'query_trajectory_evidence':
                    continue
                if category and tool.get('status') == 'completed' and tool.get('domain_status') not in ('error', 'invalid', 'not_found', 'disabled', 'truncated', 'empty'):
                    facts.append({'turn_id': tid, 'execution_id': xid, 'category': category,
                                  'tool': tool['name'], 'summary': tool['result_summary'][:240]})
        return facts[-20:]

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
                    "session": self.session(sid),
                    "next_before": selected[-1]["id"] if len(rows) > limit else None}

    def artifact(self, aid):
        with self._lock:
            row = self._db.execute('SELECT * FROM artifacts WHERE id=?', (aid,)).fetchone()
            if row is None:
                raise ChatError('ARTIFACT_NOT_FOUND', 404)
            turn = self._row(self._db, row['turn_id'])
            data = json.loads(row['data'])
            # v1 artifacts did not include display snapshots. Upgrade on explicit read.
            if 'team_document' not in data:
                from .team_service import artifact_payload
                data = artifact_payload(data['advice'], aid, turn['session_id'], row['turn_id'], legacy=True)
                self._db.execute('UPDATE artifacts SET data=? WHERE id=?', (dump(data), aid))
            data['saved_copies'] = [{k: v for k, v in json.loads(r[0]).items() if k not in ('payload', 'document', 'previous_digest')}
                                    for r in self._db.execute('SELECT data FROM artifact_saves WHERE artifact_id=?', (aid,))]
            return data

    def add_artifact(self, artifact):
        with self.transaction() as db:
            self._row(db, artifact['turn_id'])
            db.execute('INSERT INTO artifacts VALUES(?,?,?,?)', (artifact['artifact_id'], artifact['turn_id'], artifact['artifact_version'], dump(artifact)))

    def rebuild_turns(self, sid):
        with self._lock:
            self.session(sid)
            rows = self._db.execute('SELECT * FROM turns WHERE session_id=? ORDER BY created_at,id', (sid,)).fetchall()
            return [self._snapshot(row) for row in rows if row['status'] not in ACTIVE]

    def recover(self):
        with self._lock:
            ids = [r[0] for r in self._db.execute("SELECT id FROM turns WHERE status IN ('pending','running','cancelling')")]
        for tid in ids:
            self.finish(tid, AssistantResult(message="服务已重启，上次请求中断。已完成的工作仍可查看。",
                kind="partial", status="interrupted", reason_code="service_interrupted"))
        from .team_service import content_digest
        with self._lock:
            saves = self._db.execute('SELECT artifact_id,request_id,data FROM artifact_saves').fetchall()
        for row in saves:
            record = json.loads(row['data'])
            if record['status'] != 'pending':
                continue
            try:
                actual = content_digest(json.loads(Path(record['path']).read_text(encoding='utf-8')))
            except (OSError, ValueError):
                continue
            if actual == record['content_digest']:
                record['status'] = 'saved'
                with self.transaction() as db:
                    db.execute('UPDATE artifact_saves SET data=? WHERE artifact_id=? AND request_id=?',
                               (dump(record), row['artifact_id'], row['request_id']))


def request_hash(message, retry_of=None):
    return hashlib.sha256(dump([message.strip(), retry_of]).encode()).hexdigest()
