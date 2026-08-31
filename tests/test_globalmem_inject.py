"""G2 GlobalMem 注入对战：observation 建键 / `[全局经验]` 渲染 / 注入 / 轨迹记录。"""

from __future__ import annotations

import dataclasses

from langchain_core.messages import AIMessage

from environment.dataset import DataSource
from environment.players import RandomPlayer
from environment.presets import p1_preset
from environment.rules import DEFAULT_RULES
from environment.session import BattleSession
from environment.teambuilder import build_roster
from roco_pvp_agent.battle.evolution.globalmem import (
    GlobalMemStore,
    make_global_entry_id,
    make_global_retriever,
    matchup_key,
    matchup_key_from_observation,
)
from roco_pvp_agent.battle.player import LLMPlayer, _render_global_mem
from roco_pvp_agent.battle.selfplay import run_selfplay
from roco_pvp_agent.config import Settings

_DIGEST = "d_test"


def _rosters():
    pa, pb = p1_preset(3)
    return build_roster(pa, DataSource.VALID), build_roster(pb, DataSource.VALID)


def _session():
    ra, rb = _rosters()
    return BattleSession.start(ra, rb, seed=7, rules=DEFAULT_RULES, battle_id="g2")


def _entry(key: str, text: str = "开局压能量，残局换人吸伤") -> dict:
    return {
        "entry_id": make_global_entry_id(key, text, _DIGEST),
        "matchup_key": key,
        "my_roster": ["迪莫", "喵喵", "火花"],
        "foe_roster": ["水蓝蓝", "板板壳", "鸭吉吉"],
        "strategy_text": text,
        "Q": 0.0, "n_used": 0, "n_wins": 0,
        "provenance": {"data_digest": _DIGEST, "rules_digest": "r_x"},
    }


class _FakeLLM:
    """固定回 recharge 的假 LLM（只为让 decide 走通）。"""

    def invoke(self, messages):
        return AIMessage(content="", tool_calls=[
            {"name": "battle_act_a", "args": {"action_type": "recharge"}, "id": "c1"}])


# ---------- 从 observation 建键 ----------

def test_key_from_observation_matches_key_from_roster():
    """同一场对局：从迷雾 observation 建的键 == 从 roster spec 建的键。"""
    ra, rb = _rosters()
    s = BattleSession.start(ra, rb, seed=7, rules=DEFAULT_RULES, battle_id="g2")
    from_obs = matchup_key_from_observation(s.view("a"), source=DataSource.VALID)
    from_roster = matchup_key(ra, rb, team_size=DEFAULT_RULES.team_size,
                              lives=DEFAULT_RULES.lives, source=DataSource.VALID)
    assert from_obs == from_roster


def test_key_from_observation_is_directional():
    """a 视角与 b 视角是不同的键（有向）。"""
    s = _session()
    ka = matchup_key_from_observation(s.view("a"), source=DataSource.VALID)
    kb = matchup_key_from_observation(s.view("b"), source=DataSource.VALID)
    assert ka != kb


def test_key_from_observation_has_no_foe_skill_info():
    """迷雾口径：对手侧不含技能类别/速度（开局未揭示）。"""
    s = _session()
    key = matchup_key_from_observation(s.view("a"), source=DataSource.VALID)
    foe_seg = key.split("|")[2]
    assert "k:" not in foe_seg and "spd:" not in foe_seg


# ---------- 渲染（安全标注）----------

def test_render_global_mem_has_all_three_guards():
    text = _render_global_mem(_entry("3v2|my:types:火3/spd:mid/k:a6|foe:types:水3"))
    assert "[全局经验]" in text
    assert "非当前局面事实" in text          # ① 标注
    assert "不得据此假定对手技能" in text     # ② 防技能当事实
    assert "不得覆盖" in text                # ③ 不可覆盖规则
    assert "开局压能量" in text


# ---------- 注入 ----------

def test_llm_player_injects_global_mem_into_system_prompt(tmp_path):
    s = _session()
    store = GlobalMemStore(tmp_path)
    key = matchup_key_from_observation(s.view("a"), source=DataSource.VALID)
    e = _entry(key)
    store.add(e)
    retriever = make_global_retriever(store, data_digest=_DIGEST, source=DataSource.VALID)
    p = LLMPlayer("a", settings=Settings(), seed=1, global_mem=retriever, llm=_FakeLLM())
    p.on_match_start(s.view("a"))
    assert "[全局经验]" in p._history[0].content       # 进的是 system prompt（整局不变）
    assert p.loaded_global_mem_id == e["entry_id"]


def test_empty_store_injects_nothing(tmp_path):
    s = _session()
    retriever = make_global_retriever(GlobalMemStore(tmp_path), data_digest=_DIGEST,
                                      source=DataSource.VALID)
    p = LLMPlayer("a", settings=Settings(), seed=1, global_mem=retriever, llm=_FakeLLM())
    p.on_match_start(s.view("a"))
    assert "[全局经验]" not in p._history[0].content
    assert p.loaded_global_mem_id is None


def test_global_mem_none_is_backward_compatible():
    """不传 global_mem → 行为与 G1 前完全一致（无注入、id 为 None）。"""
    s = _session()
    p = LLMPlayer("a", settings=Settings(), seed=1, llm=_FakeLLM())
    p.on_match_start(s.view("a"))
    assert "[全局经验]" not in p._history[0].content
    assert p.loaded_global_mem_id is None


def test_stale_digest_not_injected(tmp_path):
    """跨版本硬隔离：data_digest 不符的条目不注入。"""
    s = _session()
    store = GlobalMemStore(tmp_path)
    store.add(_entry(matchup_key_from_observation(s.view("a"), source=DataSource.VALID)))
    retriever = make_global_retriever(store, data_digest="d_other", source=DataSource.VALID)
    p = LLMPlayer("a", settings=Settings(), seed=1, global_mem=retriever, llm=_FakeLLM())
    p.on_match_start(s.view("a"))
    assert p.loaded_global_mem_id is None


# ---------- 轨迹记录 ----------

def test_trajectory_records_loaded_global_mem_ids(tmp_path):
    """record 写入双方各加载了哪条（G3/G5 的输入）。"""
    ra, rb = _rosters()
    s = BattleSession.start(ra, rb, seed=7, rules=DEFAULT_RULES, battle_id="probe")
    store = GlobalMemStore(tmp_path)
    key_a = matchup_key_from_observation(s.view("a"), source=DataSource.VALID)
    ea = _entry(key_a)
    store.add(ea)
    retriever = make_global_retriever(store, data_digest=_DIGEST, source=DataSource.VALID)

    class _Probe:
        """携带 loaded_global_mem_id 的确定性玩家（只验证记录链路）。"""

        kind = "probe"

        def __init__(self, side, entry_id):
            self._inner = RandomPlayer(side, seed=1)
            self.loaded_global_mem_id = entry_id

        def __getattr__(self, name):
            return getattr(self._inner, name)

    out = run_selfplay(seed=7, team_size=3, lives=2, roster_a=ra, roster_b=rb,
                       players={"a": _Probe("a", ea["entry_id"]), "b": _Probe("b", None)})
    rec = out["record"]
    assert rec["global_mem_a"] == ea["entry_id"]
    assert "global_mem_b" not in rec           # None → 不写键（向后兼容）
    assert out["replay_ok"] is True            # 新键不破坏重放
    assert retriever("a", s.view("a"))         # 检索器本身可用
