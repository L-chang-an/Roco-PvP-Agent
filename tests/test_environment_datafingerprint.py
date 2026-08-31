"""数据指纹（S0.①）：确定性 + 敏感性 + 精确性三条不变式。"""

from __future__ import annotations

import dataclasses

import environment.datafingerprint as fp
from environment.dataset import DataSource


def test_digests_deterministic():
    assert fp.data_digest() == fp.data_digest()
    assert fp.rules_digest() == fp.rules_digest()


def test_digest_format():
    assert fp.data_digest().startswith("d_")
    assert fp.rules_digest().startswith("rules_")


def test_data_digest_changes_when_skill_power_changes(monkeypatch):
    """敏感性：技能 power 变化 → data_digest 变。"""
    orig = fp.data_digest()
    skills = fp.load_skills(DataSource.VALID)
    name = next(iter(skills))
    modified = dict(skills)
    modified[name] = dataclasses.replace(skills[name], power=skills[name].power + 1)
    monkeypatch.setattr(fp, "load_skills", lambda src: modified)
    assert fp.data_digest() != orig


def test_data_digest_changes_when_spirit_stats_change(monkeypatch):
    """敏感性：精灵六维变化 → data_digest 变。"""
    orig = fp.data_digest()
    spirits = fp.load_spirits(DataSource.VALID)
    name = next(iter(spirits))
    modified = dict(spirits)
    s = spirits[name]
    modified[name] = dataclasses.replace(s, stats={**s.stats, "hp": s.stats["hp"] + 1})
    monkeypatch.setattr(fp, "load_spirits", lambda src: modified)
    assert fp.data_digest() != orig


def test_data_digest_ignores_display_fields(monkeypatch):
    """精确性：desc（纯展示字段）变化 → data_digest 不变。"""
    orig = fp.data_digest()
    skills = fp.load_skills(DataSource.VALID)
    name = next(iter(skills))
    modified = dict(skills)
    modified[name] = dataclasses.replace(skills[name], desc="changed-for-test")
    monkeypatch.setattr(fp, "load_skills", lambda src: modified)
    assert fp.data_digest() == orig
