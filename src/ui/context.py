"""ChatContext：按 session 隔离的聊天上下文（内存实现，接口稳定，后续可换 sqlite）。

- 共享一个无状态 ChatAgent（stateless by design，M1 的承诺在这里兑现）。
- 每会话独立历史；上限 MAX_SESSIONS 个会话，LRU 逐出最久未用的。
- 单把全局锁保护会话字典即可（每会话同一时刻只有一次对话在跑，前端已禁用重复提交）。
"""

import threading
from collections import OrderedDict
from uuid import uuid4

from roco_pvp_agent.agent import ChatAgent

MAX_SESSIONS = 64


class ChatContext:
    def __init__(self, agent: ChatAgent):
        self._agent = agent
        self._sessions: "OrderedDict[str, list]" = OrderedDict()
        self._lock = threading.Lock()

    # ---------- 内部 ----------

    def _touch(self, session_id: str) -> None:
        """标记最近使用并逐出最久未用的会话。必须在持锁时调用。"""
        self._sessions.move_to_end(session_id)
        while len(self._sessions) > MAX_SESSIONS:
            self._sessions.popitem(last=False)

    def _get_history(self, session_id: str) -> list:
        """取（必要时创建）会话历史。返回的是真实列表引用，调用方应只读。"""
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = []
            self._touch(session_id)
            return self._sessions[session_id]

    # ---------- 对外 ----------

    def new_session(self) -> str:
        """分配一个新会话 id。"""
        session_id = uuid4().hex
        with self._lock:
            self._sessions[session_id] = []
            self._touch(session_id)
        return session_id

    def chat(self, message: str, session_id: str, *, event_sink=None):
        """执行一次对话：取历史 → agent.chat → 回写新历史。返回 ChatReply。"""
        history = list(self._get_history(session_id))
        reply = self._agent.chat(message, history=history, event_sink=event_sink)
        with self._lock:
            self._sessions[session_id] = reply.history
            self._touch(session_id)
        return reply

    def reset(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def get_history(self, session_id: str) -> list:
        return list(self._get_history(session_id))
