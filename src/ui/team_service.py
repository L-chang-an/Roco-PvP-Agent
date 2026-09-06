"""Shared team validation, immutable advice snapshots and v2 documents."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from environment.battle_config import validate_team_size
from environment.datafingerprint import data_digest, rules_digest
from environment.dataset import DataSource, load_skills, load_spirits
from environment.rules import BattleRules
from environment.teambuilder import TeamPick, build_roster, validate_team


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def content_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def advice_document(advice):
    return {'team_size': advice['rules_used']['team_size'], 'items': [], 'team': [
        {k: unit[k] for k in ('spirit', 'skills', 'bloodline', 'nature', 'iv')} for unit in advice['team']]}


def validate_document(document):
    error = validate_team_size(document['team_size'])
    errors = [error] if error else []
    if len(document['team']) != document['team_size']:
        errors.append(f"队伍规模必须为 {document['team_size']} 只，实际 {len(document['team'])} 只。")
    picks = [TeamPick(**unit) for unit in document['team']]
    rules = BattleRules(team_size=document['team_size'])
    if not errors:
        errors = validate_team(picks, document['items'], rules, DataSource.VALID)
    return {'ok': not errors, 'errors': errors, 'data_digest': data_digest(),
            'rules_digest': rules_digest(), 'content_digest': content_digest(document), 'checked_at': timestamp(),
            'roster': build_roster(picks, DataSource.VALID, rules) if not errors else []}


def enrich_pick(pick):
    data = dict(pick)
    skills, spirits = load_skills(DataSource.FULL), load_spirits(DataSource.FULL)
    data['skills'] = [{'name': name, 'type': skills[name].type, 'desc': skills[name].desc}
                      if name in skills else {'name': name, 'type': '', 'desc': '当前数据中不存在'} for name in pick['skills']]
    sp = spirits.get(pick['spirit'])
    data['trait'] = {'name': sp.trait_name, 'desc': sp.trait_desc} if sp else {'name': '', 'desc': ''}
    data['types'] = list(sp.types) if sp else []
    return data


def serialize_v2(document, saved_at):
    team = []
    for pick in document['team']:
        enriched = enrich_pick(pick)
        enriched.pop('types', None)
        team.append(enriched)
    return {'version': 2, 'saved_at': saved_at, 'team_size': document['team_size'],
            'items': list(document['items']), 'team': team}


def atomic_write(target, payload):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix='.team-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def artifact_payload(advice, aid, sid, tid, *, legacy=False, document=None):
    document = document or advice_document(advice)
    validation = validate_document(document)
    display = [enrich_pick(unit) for unit in document['team']]
    return {'artifact_id': aid, 'artifact_version': 1, 'session_id': sid, 'turn_id': tid,
            'advice_schema_version': 1, 'created_at': timestamp(), 'advice': advice,
            'team_document': document, 'display_snapshot': display,
            'validation_at_creation': validation if not legacy else None,
            'legacy_snapshot': legacy, 'validation': {'main_team': 'valid' if validation['ok'] else 'invalid', 'alternatives': 'unverified'},
            'evidence_status': {'catalog': [], 'human': [], 'selfplay': [], 'simulation': [],
                                'unverified_refs': sorted({ref for unit in advice['team'] for ref in unit.get('evidence_ids', [])})}}
