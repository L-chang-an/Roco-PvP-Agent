"""Old wire shapes backed by the same durable turns as the new UI."""
import time
from uuid import uuid4
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from roco_pvp_agent.agent import ChatReply
from .chat_store import ACTIVE, ChatError


class DurableChatContext:
    def __init__(self, app):
        self.app = app

    @property
    def coordinator(self):
        service = getattr(self.app.state, 'turn_coordinator', None)
        if service is None:
            raise ChatError('CHAT_NOT_STARTED', 503)
        return service

    def new_session(self):
        return self.coordinator.store.new_session()['id']

    def chat(self, message, session_id, *, event_sink=None, cancel_event=None):
        service = self.coordinator
        turn, _ = service.create(session_id, uuid4().hex, message, legacy_sink=event_sink)
        while True:
            turn = service.get(turn['turn_id'])
            if turn['status'] not in ACTIVE:
                break
            if cancel_event is not None and cancel_event.is_set():
                service.cancel(turn['turn_id'])
            time.sleep(.02)
        result = turn['result']
        legacy = service.store.legacy(turn['turn_id'])
        reply = ChatReply(reply=legacy.get('reply', result['message']),
            tool_calls=legacy.get('tool_calls', []), thinking=[], offline=turn['offline'],
            rounds=legacy.get('rounds', len(turn['rounds'])), usage=legacy.get('usage', result['usage']),
            history=self.get_history(session_id))
        if event_sink:
            event_sink({'event': 'reply', 'text': reply.reply, 'offline': reply.offline, 'rounds': reply.rounds, 'usage': reply.usage})
            event_sink({'event': 'done'})
        return reply

    def reset(self, session_id):
        self.coordinator.store.clear_session(session_id)

    def get_history(self, session_id):
        messages = []
        for turn in self.coordinator.store.rebuild_turns(session_id):
            projection = self.coordinator.store.legacy(turn['turn_id']).get('history') or [
                {'type': 'human', 'content': turn['message']},
                {'type': 'ai', 'content': turn['result']['message']}]
            for item in projection:
                if item['type'] == 'human':
                    messages.append(HumanMessage(content=item['content']))
                elif item['type'] == 'tool':
                    messages.append(ToolMessage(content=item['content'], tool_call_id='legacy-projection'))
                elif item['type'] == 'ai':
                    messages.append(AIMessage(content=item['content']))
        return messages
