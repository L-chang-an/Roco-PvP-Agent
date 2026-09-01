"""C：顾问接记忆 —— query_global_mem / query_local_mem 两个只读工具。"""

from __future__ import annotations

import json

from environment.battle_config import build_battle_rules
from environment.datafingerprint import data_digest
from environment.dataset import DataSource
from environment.teambuilder import build_roster
from roco_pvp_agent.advisor.agent import _build_advisor_tools, _to_team_pick
from roco_pvp_agent.battle.evolution.globalmem import (
    GlobalMemStore,
    make_global_entry_id,
    matchup_key,
)
from roco_pvp_agent.battle.evolution.memory import MemoryStore, make_entry_id


def _roster(picks: list[dict]) -> list[dict]:
    rules = build_battle_rules(team_size=len(picks), lives=2)
    return build_roster([_to_team_pick(p) for p in picks], DataSource.VALID, rules)


def _global_store(tmp_path, *, key, text="开局压能量，残局换人吸伤") -> GlobalMemStore:
    store = GlobalMemStore(tmp_path / "gm")
    store.add({
        "entry_id": make_global_entry_id(key, text, data_digest()),
        "matchup_key": key,
        "my_roster": ["迪莫", "喵喵", "火花"],
        "foe_roster": ["水蓝蓝", "板板壳", "鸭吉吉"],
        "strategy_text": text,
        "Q": 0.3, "n_used": 5, "n_wins": 3,
        "provenance": {"data_digest": data_digest()},
    })
    return store


def _local_store(tmp_path) -> MemoryStore:
    store = MemoryStore(tmp_path / "mem")
    key = "my2/foe2/迪莫/水蓝蓝/high/high/0/early/2/2"
    store.add({
        "entry_id": make_entry_id("a", key, {"type": "skill", "value": 0}, scope=data_digest()),
        "side": "a", "lineage_family": "extract",
        "situation_key": key, "situation_text": "开局局面",
        "experience_text": "换人吸伤再回能，比原地回能少挨一次高倍率攻击",
        "action": {"type": "skill", "value": 0},
        "Q": 0.5, "n_used": 3, "n_adopted": 2,
        "provenance": {"rules_version": "r", "data_digest": data_digest(), "source_type": "extract"},
    })
    return store


def _team_picks() -> list[dict]:
    return [{"spirit": "迪莫", "skills": ["闪光", "猛烈撞击"], "bloodline": "", "nature": "坦率", "iv": {}},
            {"spirit": "喵喵", "skills": ["抓挠"], "bloodline": "", "nature": "坦率", "iv": {}},
            {"spirit": "火花", "skills": ["火苗"], "bloodline": "", "nature": "坦率", "iv": {}}]


def _invoke(tools_map, name, **args):
    return tools_map[name].invoke(args if args else {})


def test_query_global_mem_returns_history(tmp_path):
    """给定双方阵容 → 返回最相似 GlobalMem（含 Q/n_used/n_wins/标注历史经验）。"""
    my_picks = _team_picks()
    foe_picks = [{"spirit": "水蓝蓝", "skills": ["拍击"], "bloodline": "", "nature": "坦率", "iv": {}},
                 {"spirit": "板板壳", "skills": ["肥皂泡"], "bloodline": "", "nature": "坦率", "iv": {}},
                 {"spirit": "喵喵", "skills": ["抓挠"], "bloodline": "", "nature": "坦率", "iv": {}}]
    # 造 store：用 advisor 相同的键构造
    key = matchup_key(_roster(my_picks), _roster(foe_picks), team_size=3, lives=2)
    store = _global_store(tmp_path, key=key)

    tools = {t.name: t for t in _build_advisor_tools(globalmem_dir=str(tmp_path / "gm"))}
    out = json.loads(_invoke(tools, "query_global_mem", my_team=my_picks, foe_team=foe_picks))
    assert out["matchup_key"] == key
    assert out["hits"], "应命中沉淀的 GlobalMem"
    hit = out["hits"][0]
    assert hit["strategy_text"] == "开局压能量，残局换人吸伤"
    assert hit["Q"] == 0.3 and hit["n_used"] == 5 and hit["n_wins"] == 3
    assert hit["is_history_not_fact"] is True


def test_query_global_mem_disabled_without_dir():
    tools = {t.name: t for t in _build_advisor_tools()}
    out = _invoke(tools, "query_global_mem", my_team=_team_picks(),
                  foe_team=_team_picks())
    assert "未启用" in out


def test_query_local_mem_returns_history(tmp_path):
    store = _local_store(tmp_path)
    tools = {t.name: t for t in _build_advisor_tools(memory_dir=str(tmp_path / "mem"))}
    out = json.loads(_invoke(tools, "query_local_mem",
                             situation_key="my2/foe2/迪莫/水蓝蓝/high/high/0/early/2/2"))
    assert out["hits"], "应命中沉淀的局部记忆"
    hit = out["hits"][0]
    assert "换人吸伤" in hit["experience_text"]
    assert hit["Q"] == 0.5
    assert hit["is_history_not_fact"] is True
    assert store.count() == 1      # 只读：检索不改库


def test_query_local_mem_disabled_without_dir():
    tools = {t.name: t for t in _build_advisor_tools()}
    assert "未启用" in _invoke(tools, "query_local_mem",
                               situation_key="my2/foe2/迪莫/水蓝蓝/high/high/0/early/2/2")


def test_tools_are_read_only(tmp_path):
    """两个工具都是只读：检索前后库条目数不变。"""
    my_picks = _team_picks()
    key = matchup_key(_roster(my_picks), _roster(my_picks), team_size=3, lives=2)
    store = _global_store(tmp_path, key=key)
    tools = {t.name: t for t in _build_advisor_tools(globalmem_dir=str(tmp_path / "gm"),
                                                     memory_dir=str(tmp_path / "mem"))}
    before_gm = len(store.all())
    _invoke(tools, "query_global_mem", my_team=my_picks, foe_team=my_picks)
    assert len(store.all()) == before_gm
