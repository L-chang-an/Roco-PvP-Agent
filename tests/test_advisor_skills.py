"""M4 skills：顾问 Skill 注册表（hash 校验 / probationary / 触发检索 / 白名单裁剪）的测试。"""

from __future__ import annotations

import hashlib
import json

import pytest
from langchain_core.tools import tool

from roco_pvp_agent.advisor.skills import (
    Skill,
    load_skills,
    register_skill,
    retrieve_team_skill,
)
from roco_pvp_agent.tooling import ToolEntry, ToolRegistry


@tool("validate_team")
def _validate_team_stub() -> str:
    """测试用合法阵容校验工具。"""
    return "ok"


@tool("newly_registered_tool")
def _newly_registered_tool_stub() -> str:
    """测试 Registry 新增能力无需同步第二份白名单。"""
    return "ok"


def _skill_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ToolEntry(tool=_validate_team_stub))
    registry.register(ToolEntry(tool=_newly_registered_tool_stub))
    return registry


def test_load_skills_reads_json():
    skills = load_skills()
    assert skills, "内置 Skill 目录不应为空"
    for s in skills:
        assert isinstance(s, Skill)
        assert s.name and s.hash and s.status in ("active", "probationary")


def test_load_skills_detects_tampered_body(tmp_path, monkeypatch):
    """篡改 body（不改 hash）→ load_skills 抛 ValueError。"""
    skills = load_skills()
    assert skills
    tampered = {**skills[0].model_dump(), "body": "被篡改的 body"}
    bad_dir = tmp_path / "skills"
    bad_dir.mkdir()
    (bad_dir / "x.json").write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("roco_pvp_agent.advisor.skills.SKILLS_DIR", bad_dir)
    with pytest.raises(ValueError, match="篡改"):
        load_skills()


def test_register_skill_forces_probationary():
    spec = {"name": "test-skill", "version": 1, "status": "active", "trigger": ["x"],
            "boundary": [], "allowed_tools": [], "verification": [], "body": "body text"}
    s = register_skill(spec)
    assert s.status == "probationary"
    assert s.hash == hashlib.sha256("body text".encode("utf-8")).hexdigest()


def test_retrieve_team_skill_active_and_trigger():
    out = retrieve_team_skill("帮我组队", registry=_skill_registry())
    assert out, "内置 roco-team-advisor 应被触发"
    assert all(s["name"] for s in out)
    assert len(out) <= 3


def test_retrieve_team_skill_trims_allowed_tools_from_registry(tmp_path, monkeypatch):
    """Skill 权限从 Registry 派生：新增已注册工具保留，未知工具裁剪。"""
    spec = {
        "name": "evil-skill", "version": 1, "status": "active", "trigger": ["组队"],
        "boundary": [], "verification": [],
        "allowed_tools": ["validate_team", "newly_registered_tool", "execute_code"],
        "body": "body", "hash": hashlib.sha256("body".encode("utf-8")).hexdigest(),
    }
    bad_dir = tmp_path / "skills"
    bad_dir.mkdir()
    (bad_dir / "evil.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("roco_pvp_agent.advisor.skills.SKILLS_DIR", bad_dir)

    out = retrieve_team_skill("组队", registry=_skill_registry())
    assert len(out) == 1
    assert out[0]["allowed_tools"] == ["validate_team", "newly_registered_tool"]
