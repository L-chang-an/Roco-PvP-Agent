"""Artifact actions bind to immutable server documents, never client validity claims."""
import json
import hashlib
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from roco_pvp_agent.advisor.advice import submit_team_advice
from .chat_store import ChatError, dump
from .routes_chat import services
from . import routes_team
from .team_service import (artifact_payload, atomic_write, content_digest, serialize_v2,
                           timestamp, validate_document)

router = APIRouter(prefix='/api/chat/artifacts')


def file_digest(target):
    if not target.exists():
        return None
    raw = target.read_bytes()
    try:
        return content_digest(json.loads(raw))
    except (ValueError, UnicodeDecodeError):
        return 'bytes_' + hashlib.sha256(raw).hexdigest()


class SaveArtifactBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    artifact_version: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=128)
    path: str = Field(default='', max_length=4096)
    overwrite: bool = False
    items: list[str] | None = Field(default=None, max_length=10)

    @field_validator('request_id')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('请求编号不能为空')
        return value


def validate_current(store, artifact):
    validation = validate_document(artifact['team_document'])
    with store.transaction() as db:
        store.artifact(artifact['artifact_id'])
        db.execute('INSERT OR REPLACE INTO artifact_validations VALUES(?,?)', (artifact['artifact_id'], dump(validation)))
    return validation


@router.get('/{aid}')
def get_artifact(aid: str, request: Request):
    store = services(request)[1]
    with store._lock:
        artifact = store.artifact(aid)
        current = validate_current(store, artifact)
        original = artifact['validation_at_creation'] or {}
        artifact.update(current_validation=current, saveable=current['ok'],
                        version_changed=any(original.get(k) != current[k] for k in ('data_digest', 'rules_digest')))
        return artifact


@router.post('/{aid}/validate')
def validate_artifact(aid: str, request: Request):
    store = services(request)[1]
    return validate_current(store, store.artifact(aid))


@router.post('/{aid}/alternatives/{index}/validate')
def validate_alternative(aid: str, index: int, request: Request):
    store = services(request)[1]
    with store._lock:
        original = store.artifact(aid)
        advice = original['advice']
        if not 0 <= index < len(advice['alternatives']):
            raise ChatError('ALTERNATIVE_NOT_FOUND', 404)
        payload = {**advice, 'team': advice['alternatives'][index], 'alternatives': []}
        checked = submit_team_advice(payload)
        if not checked['ok']:
            raise ChatError('ALTERNATIVE_INVALID', 422, errors=checked['errors'])
        derived = artifact_payload(checked['advice'].model_dump(), 'a_' + uuid4().hex, original['session_id'], original['turn_id'])
        derived['derived_from'] = aid
        store.add_artifact(derived)
        return derived


@router.get('/{aid}/download')
def download_artifact(aid: str, request: Request):
    store = services(request)[1]
    artifact = store.artifact(aid)
    validation = validate_current(store, artifact)
    if not validation['ok']:
        raise ChatError('TEAM_INVALID', 422, errors=validation['errors'])
    return JSONResponse(serialize_v2(artifact['team_document'], artifact['created_at']),
                        headers={'Content-Disposition': f'attachment; filename="team-{aid}.json"'})


def _save_artifact(aid: str, body: SaveArtifactBody, request: Request):
    store = services(request)[1]
    # Serialize saves/deletes within this single-process application. No model wait here.
    with store._lock:
        artifact = store.artifact(aid)
        if artifact['artifact_version'] != body.artifact_version:
            raise ChatError('ARTIFACT_VERSION_CONFLICT')
        intent = content_digest(body.model_dump(exclude={'request_id', 'overwrite'}))
        old = store._db.execute('SELECT * FROM artifact_saves WHERE artifact_id=? AND request_id=?', (aid, body.request_id)).fetchone()
        if old:
            if old['intent'] != intent:
                raise ChatError('REQUEST_ID_CONFLICT')
            record = json.loads(old['data'])
        else:
            document = {**artifact['team_document'], 'items': body.items if body.items is not None else artifact['team_document']['items']}
            validation = validate_document(document)
            if not validation['ok']:
                raise ChatError('TEAM_INVALID', 422, errors=validation['errors'])
            target = routes_team._resolve_path(body.path, must_exist=False)
            if target.exists() and not body.overwrite:
                raise ChatError('OVERWRITE_REQUIRED', path=str(target), message='目标文件已存在，请确认覆盖。')
            saved_at = timestamp()
            payload = serialize_v2(document, saved_at)
            record = {'artifact_id': aid, 'request_id': body.request_id, 'path': str(target),
                      'saved_at': saved_at, 'content_digest': content_digest(payload), 'status': 'pending',
                      'payload': payload, 'document': document, 'validation': validation,
                      'request_body': body.model_dump(),
                      'previous_digest': file_digest(target)}
            if body.items is not None and body.items != artifact['team_document']['items']:
                derived = artifact_payload(artifact['advice'], 'a_' + uuid4().hex, artifact['session_id'], artifact['turn_id'], document=document)
                derived['derived_from'] = aid
                store.add_artifact(derived)
                record['derived_artifact_id'] = derived['artifact_id']
            with store.transaction() as db:
                db.execute('INSERT INTO artifact_saves VALUES(?,?,?,?)', (aid, body.request_id, intent, dump(record)))
        target = Path(record['path'])
        try:
            actual = file_digest(target)
            if record['status'] == 'saved':
                return {**record, 'ok': True, 'file_matches': actual == record['content_digest']}
            if actual != record['content_digest']:
                if actual != record['previous_digest']:
                    raise ChatError('SAVE_TARGET_CHANGED', message='目标文件已改变，请选择新文件名。')
                # A pending write may resume under newer rules; validate before any new file mutation.
                checked = validate_document(record['document'])
                if not checked['ok']:
                    raise ChatError('TEAM_INVALID', 422, errors=checked['errors'])
                record['validation'] = checked
                atomic_write(target, record['payload'])
            record['status'] = 'saved'
            with store.transaction() as db:
                db.execute('UPDATE artifact_saves SET data=? WHERE artifact_id=? AND request_id=?', (dump(record), aid, body.request_id))
            return {**record, 'ok': True, 'file_matches': True}
        except (OSError, ValueError):
            raise ChatError('TEAM_SAVE_FAILED', 503, message='队伍文件写入或读取失败，请检查路径后重试。') from None


@router.post('/{aid}/save')
def save_artifact(aid: str, body: SaveArtifactBody, request: Request):
    try:
        return _save_artifact(aid, body, request)
    except OSError:
        raise ChatError('TEAM_SAVE_FAILED', 503, message='队伍文件无法访问，请检查路径和写入权限。') from None
