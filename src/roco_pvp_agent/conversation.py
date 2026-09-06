"""Versioned, bounded conversation state. Provider metadata never enters this codec."""
from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field


class ConversationScopeContext(BaseModel):
    model_config = ConfigDict(extra='ignore')
    current_team: list[dict] = Field(default_factory=list)
    latest_team_artifact_id: str | None = None
    unresolved_questions: list[dict] = Field(default_factory=list)
    recent_topic: str = ''


class ConversationState(ConversationScopeContext):
    team_size: int | None = None
    lives: int | None = None
    items: list[str] = Field(default_factory=list)
    artifact_version: int | None = None
    rules_used: dict = Field(default_factory=dict)
    data_digest: str = ''
    rules_digest: str = ''
    explicit_constraints: list[dict] = Field(default_factory=list)
    assumptions: list[dict] = Field(default_factory=list)
    verified_facts: list[dict] = Field(default_factory=list)
    evidence_refs: list[dict] = Field(default_factory=list)


class ContextMessage(BaseModel):
    model_config = ConfigDict(extra='ignore')
    role: Literal['human', 'ai']
    content: str
    turn_id: str = ''


class Checkpoint(BaseModel):
    model_config = ConfigDict(extra='ignore')
    schema_version: Literal[1, 2] = 2
    through_turn_id: str | None = None
    messages: list[ContextMessage] = Field(default_factory=list)
    conversation_state: ConversationState = Field(default_factory=ConversationState)
    loaded_tools: list[str] = Field(default_factory=list)
    registry_schema_digest: str = ''
    unavailable_tools: list[str] = Field(default_factory=list)


class ContextBudgetError(ValueError):
    pass


def decode_checkpoint(raw):
    raw = deepcopy(raw)
    state = raw.get('conversation_state', {})
    state['assumptions'] = [a if isinstance(a, dict) else {'text': a, 'turn_id': state.get('source_turn_id', '')}
                            for a in state.get('assumptions', [])]
    rules = state.get('rules_used', {})
    for key in ('team_size', 'lives'):
        state.setdefault(key, rules.get(key))
    raw['conversation_state'] = state
    cp = Checkpoint.model_validate(raw)
    if len(cp.messages) % 2 or any(m.role != ('human' if i % 2 == 0 else 'ai') for i, m in enumerate(cp.messages)):
        raise ValueError('Incomplete conversation pairs')
    cp.schema_version = 2
    from .advisor.scope import classify, ScopeVerdict
    cp.messages = [m for i in range(0, len(cp.messages), 2)
                   if classify(cp.messages[i].content) not in (ScopeVerdict.REFUSE, ScopeVerdict.OUT_OF_SCOPE)
                   for m in cp.messages[i:i + 2]]
    return cp


def compile_context(cp, *, max_chars=32000, max_turns=20):
    state = cp.conversation_state.model_dump()
    text = '会话已记录的配置和事实（用户约束与未确认假设分开；修改后重新校验）：' + json.dumps(state, ensure_ascii=False)
    if len(text) > max_chars:
        raise ContextBudgetError('当前队伍和约束超过上下文预算，请提高 CHAT_CONTEXT_MAX_CHARS 或新建会话。')
    remaining = max_chars - len(text)
    selected = []
    for i in range(len(cp.messages) - 2, max(-1, len(cp.messages) - max_turns * 2 - 1), -2):
        pair = cp.messages[i:i + 2]
        cost = sum(len(m.content) for m in pair)
        if cost > remaining:
            break
        selected[0:0] = pair
        remaining -= cost
    history = [SystemMessage(content=text)]
    history.extend((HumanMessage if m.role == 'human' else AIMessage)(content=m.content) for m in selected)
    return history, selected


def advance_checkpoint(cp, tid, message, result, *, advice=None, loaded_tools=(), digest='', max_chars=32000, max_turns=20):
    from .advisor.scope import route
    updated = cp.model_copy(deep=True)
    state = updated.conversation_state
    target, _ = route(message, conversation_state=state)
    # Visible history includes every turn; refused/out-of-scope requests never enter business context.
    if target not in ('refuse', 'out_of_scope'):
        updated.messages.extend([ContextMessage(role='human', content=message, turn_id=tid),
                                 ContextMessage(role='ai', content=result.message, turn_id=tid)])
        if target == 'ambiguous':
            state.unresolved_questions = [{'turn_id': tid, 'message': message, 'question': result.message}]
        elif target == 'agent':
            state.recent_topic = 'team_advisor'
            state.unresolved_questions = []
            if re.search(r'不要|禁止|必须|只用|不能|保留|围绕|目标|希望|偏好', message):
                state.explicit_constraints.append({'turn_id': tid, 'text': message, 'kind': 'source_message'})
    if advice:
        state.current_team = [{k: u[k] for k in ('spirit', 'skills', 'bloodline', 'nature', 'iv')} for u in advice['team']]
        state.rules_used = advice['rules_used']
        state.team_size, state.lives = advice['rules_used']['team_size'], advice['rules_used']['lives']
        state.items = []
        state.data_digest = advice['data_digest']
        from environment.datafingerprint import rules_digest
        state.rules_digest = rules_digest()
        state.assumptions = [{'text': a, 'turn_id': tid} for a in advice.get('assumptions', [])]
    updated.through_turn_id = tid
    updated.loaded_tools = list(loaded_tools)
    updated.registry_schema_digest = digest
    _, selected = compile_context(updated, max_chars=max_chars, max_turns=max_turns)
    updated.messages = selected
    return updated
