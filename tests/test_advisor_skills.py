"""M4 skills：顾问 Skill 注册表（hash 校验 / probationary / 触发检索 / 白名单裁剪）的测试。"""

from __future__ import annotations

import hashlib
import json

import pytest

from roco_pvp_agent.advisor.skills import (
    Skill,
    load_skills,
    register_skill,
    retrieve_team_skill,
)


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
    out = retrieve_team_skill("帮我组队")
    assert out, "内置 roco-team-advisor 应被触发"
    assert all(s["name"] for s in out)
    assert len(out) <= 3


def test_retrieve_team_skill_trims_allowed_tools(tmp_path, monkeypatch):
    """allowed_tools 里不在顾问工具白名单的一律裁剪。"""
    spec = {
        "name": "evil-skill", "version": 1, "status": "active", "trigger": ["组队"],
        "boundary": [], "verification": [],
        "allowed_tools": ["validate_team", "execute_code", "open_file"],
        "body": "body", "hash": hashlib.sha256("body".encode("utf-8")).hexdigest(),
    }
    bad_dir = tmp_path / "skills"
    bad_dir.mkdir()
    (bad_dir / "evil.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("roco_pvp_agent.advisor.skills.SKILLS_DIR", bad_dir)

    out = retrieve_team_skill("组队")
    assert len(out) == 1
    assert out[0]["allowed_tools"] == ["validate_team"]  # execute_code/open_file 被裁剪
