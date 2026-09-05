"""阶段四：Chat Mode 工具的严格 Pydantic 入参契约。"""

from __future__ import annotations

import copy

import pytest

from environment.datafingerprint import data_digest
from roco_pvp_agent.advisor.advice import submit_team_advice
from roco_pvp_agent.advisor.agent import _build_advisor_registry
from roco_pvp_agent.tooling import DispatchContext, ToolDispatcher, ToolErrorCode


def _unit(spirit: str, skill: str) -> dict:
    return {
        "spirit": spirit,
        "skills": [skill],
        "bloodline": "",
        "nature": "坦率",
        "iv": {},
        "role": "强攻",
        "rationale": "测试理由",
        "evidence_ids": ["catalog:test"],
    }


def _valid_payload() -> dict:
    return {
        "rules_used": {"team_size": 3, "lives": 2, "source": "VALID"},
        "assumptions": [],
        "data_digest": data_digest(),
        "team": [
            _unit("迪莫", "闪光"),
            _unit("喵喵", "抓挠"),
            _unit("火花", "火苗"),
        ],
        "synergy": "",
        "strengths": [],
        "weak_matchups": [],
        "evidence": {
            "catalog": {},
            "human": {},
            "selfplay": {},
            "simulation": {},
        },
        "uncertainty": "",
        "alternatives": [],
    }


def _invalid_calls() -> dict[str, dict]:
    payload = _valid_payload()
    payload["team"][0]["typo"] = "不应被忽略"
    pick_with_extra = {"spirit": "迪莫", "skills": ["闪光"], "typo": True}
    return {
        "get_catalog_version": {"unexpected": True},
        "search_spirits": {
            "filters": [[{"field": "sql", "op": "eq", "value": "迪莫"}]],
        },
        "get_spirit_profile": {"name": 123},
        "get_skill_profile": {},
        "get_build_options": {"name": "迪莫", "unexpected": "火"},
        "validate_team": {"team": [pick_with_extra]},
        "query_trajectory_evidence": {"kind": "mixed"},
        "analyze_team": {"team": {"spirit": "迪莫"}},
        "simulate_matchups": {
            "team": [], "opponents": [], "seeds": ["1"],
        },
        "retrieve_team_skill": {"query": "组队", "limit": 3},
        "query_global_mem": {"my_team": [], "foe_team": [], "team_size": 4},
        "query_local_mem": {"situation_key": 123},
        "submit_team_advice": {"payload": payload},
        "final_answer": {"text": 123},
        "tool_search": {"tool_names": []},
    }


def test_every_advisor_tool_has_closed_top_level_object_schema():
    registry = _build_advisor_registry()

    assert set(registry.names()) == set(_invalid_calls())
    for record in registry.model_schema_records():
        assert record["input_schema"]["type"] == "object", record["name"]
        assert record["input_schema"]["additionalProperties"] is False, record["name"]


@pytest.mark.parametrize("name", sorted(_invalid_calls()))
def test_every_advisor_tool_rejects_malformed_arguments_before_handler(name: str):
    dispatcher = ToolDispatcher(_build_advisor_registry())
    result = dispatcher.dispatch(
        {"id": f"bad-{name}", "name": name, "args": _invalid_calls()[name]},
        DispatchContext(),
    )

    assert result.ok is False
    assert result.error_code is ToolErrorCode.INVALID_ARGUMENTS
    assert result.details == {}


@pytest.mark.parametrize(
    "filter_input",
    [
        {"field": "is_boss", "op": "contains", "value": True},
        {"field": "is_boss", "op": "eq", "value": "true"},
        {"field": "type", "op": "eq", "value": "火"},
        {"field": "name", "op": "in", "value": "迪莫"},
        {"field": "name", "op": "eq", "value": "迪莫", "typo": 1},
    ],
)
def test_search_filter_cross_field_contract_is_validated(filter_input: dict):
    dispatcher = ToolDispatcher(_build_advisor_registry())
    result = dispatcher.dispatch(
        {
            "id": "bad-filter",
            "name": "search_spirits",
            "args": {"filters": [[filter_input]]},
        },
        DispatchContext(),
    )

    assert result.error_code is ToolErrorCode.INVALID_ARGUMENTS


def test_submit_payload_nested_models_are_closed():
    payload = _valid_payload()
    payload["rules_used"]["source"] = "FULL"
    payload["evidence"]["merged"] = {"win_rate": 1.0}

    dispatcher = ToolDispatcher(_build_advisor_registry())
    result = dispatcher.dispatch(
        {
            "id": "bad-advice",
            "name": "submit_team_advice",
            "args": {"payload": payload},
        },
        DispatchContext(),
    )

    assert result.error_code is ToolErrorCode.INVALID_ARGUMENTS


def test_direct_advice_api_uses_the_same_strict_nested_contract():
    payload = copy.deepcopy(_valid_payload())
    payload["team"][0]["typo"] = "不应被忽略"

    result = submit_team_advice(payload)

    assert result["ok"] is False
    assert result["errors"][0]["code"] == "SCHEMA_INVALID"


@pytest.mark.parametrize(
    "args",
    [
        {"my_team": [], "foe_team": [], "team_size": "3"},
        {"my_team": [], "foe_team": [], "team_size": 3, "lives": 0},
        {"my_team": [], "foe_team": [], "team_size": 3, "lives": 3},
    ],
)
def test_global_memory_rule_arguments_are_strict(args: dict):
    dispatcher = ToolDispatcher(_build_advisor_registry())
    result = dispatcher.dispatch(
        {"id": "bad-rules", "name": "query_global_mem", "args": args},
        DispatchContext(),
    )

    assert result.error_code is ToolErrorCode.INVALID_ARGUMENTS
