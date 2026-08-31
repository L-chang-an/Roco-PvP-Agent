"""记忆接线（R 线闭环）：注入渲染 / LLMPlayer 注入 / 采纳判定 + Q 更新。"""

from __future__ import annotations

import dataclasses

from langchain_core.messages import AIMessage

from environment.players import RandomPlayer
from environment.rules import DEFAULT_RULES
from environment.session import BattleSession
from roco_pvp_agent.battle.evolution.memory import MemoryStore, make_entry_id
from roco_pvp_agent.battle.evolution.memory_inject import apply_adoption
from roco_pvp_agent.battle.player import LLMPlayer, _render_memories
from roco_pvp_agent.config import Settings
from rosters import spec


def test_render_memories_marks_non_current():
    memories = [{"situation_text": "命数 2 对 2", "experience_text": "换人吸伤再回能"}]
    text = _render_memories(memories)
    assert "[记忆]" in text and "非当前局面" in text and "换人吸伤再回能" in text


def test_llm_player_injects_memory():
    class _FakeLLM:
        def __init__(self):
            self.messages = []

        def invoke(self, messages):
            self.messages.append(messages)
            return AIMessage(content="", tool_calls=[
                {"name": "battle_act_a", "args": {"action_type": "recharge"}, "id": "c1"}])

    rules = dataclasses.replace(DEFAULT_RULES, team_size=1)
    a = [spec("甲", 300, 100, 100, 100, 100, 100, ["抓挠"])]
    b = [spec("乙", 300, 100, 100, 100, 100, 90, ["抓挠"])]
    session = BattleSession.start(a, b, seed=1, rules=rules, battle_id="t")
    fake_llm = _FakeLLM()
    retriever = lambda side, key: [{"situation_text": "历史局面", "experience_text": "换人吸伤",
                                    "action": {"type": "recharge"}, "entry_id": "mem_x"}]
    player = LLMPlayer("a", settings=Settings(), seed=1, memory=retriever, llm=fake_llm)
    player.on_match_start(session.view("a"))
    player.decide(session.view("a"), session.legal_actions("a"), session.legal_items("a"))
    assert any("[记忆]" in getattr(m, "content", "") for m in player._history)


def test_apply_adoption_updates_q(tmp_path):
    store = MemoryStore(tmp_path)
    key = "my2/foe2/迪莫/水蓝蓝/high/high/0/early/2/2"
    action = {"type": "skill", "value": 0}
    eid = store.add({
        "entry_id": make_entry_id("a", key, action),
        "side": "a", "lineage_family": "extract",
        "situation_key": key, "situation_text": "t", "experience_text": "e",
        "action": action, "Q": 0.0, "n_used": 0, "n_adopted": 0,
        "provenance": {"rules_version": "r", "data_digest": "d", "source_type": "extract"},
    })
    retriever = lambda side, k: [store.get(eid)] if k == key else []
    record = {"analysis_a": [{"turn": 1, "situation_key": key, "action": action}]}
    stats = apply_adoption(store, record, retriever, winner="a")
    assert stats["adopted"] == 1 and stats["updated"] == 1
    assert abs(store.get(eid)["Q"] - 0.3) < 1e-9          # 0 + 0.3*(1.0 - 0)
