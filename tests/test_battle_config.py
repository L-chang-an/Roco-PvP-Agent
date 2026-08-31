"""管理员对局配置（battle_config.py）测试：team_size ∈ {3,6}、lives 1..team_size−1、技能槽位。

这是为 Web UI 准备的接口层——非法输入给中文原因（validate_*）或 ValueError（build_*）。
注意：BattleRules 本身无约束（测试用 team_size=1/2 直构），约束只在这层接口。
"""

from __future__ import annotations

import pytest

from environment.battle_config import (
    MAX_TEAM_SIZE, MIN_TEAM_SIZE, build_battle_rules, validate_lives, validate_team_size,
)
from environment.rules import DEFAULT_RULES


# ── validate_team_size ──
def test_team_size_bounds() -> None:
    assert validate_team_size(MIN_TEAM_SIZE) is None
    assert validate_team_size(MAX_TEAM_SIZE) is None
    assert validate_team_size(4) is not None          # 4/5 不再支持
    assert validate_team_size(5) is not None
    assert "3 或 6" in validate_team_size(MIN_TEAM_SIZE - 1)
    assert "3 或 6" in validate_team_size(MAX_TEAM_SIZE + 1)


def test_team_size_non_int() -> None:
    assert "整数" in validate_team_size(3.5)
    assert "整数" in validate_team_size("3")
    assert "整数" in validate_team_size(True)


# ── validate_lives ──
def test_lives_bounds() -> None:
    assert validate_lives(6, 1) is None
    assert validate_lives(6, 5) is None
    assert "1–5" in validate_lives(6, 0)          # 低于 1
    assert "1–5" in validate_lives(6, 6)          # 等于 team_size（必须小于）
    assert validate_lives(3, 2) is None
    assert "1–2" in validate_lives(3, 3)


def test_lives_non_int() -> None:
    assert "整数" in validate_lives(6, 2.5)
    assert "整数" in validate_lives(6, True)


def test_lives_requires_valid_team_size_first() -> None:
    assert "3 或 6" in validate_lives(7, 1)          # 先报 team_size，再谈 lives


# ── build_battle_rules（管理员接口）──
def test_build_rules_valid() -> None:
    rules = build_battle_rules(team_size=3, lives=2)
    assert rules.team_size == 3 and rules.lives == 2 and rules.skill_slots == 4
    rules6 = build_battle_rules(team_size=6, lives=5, max_turns=30)
    assert rules6.team_size == 6 and rules6.lives == 5 and rules6.max_turns == 30


def test_build_rules_invalid_raises() -> None:
    with pytest.raises(ValueError, match="3 或 6"):
        build_battle_rules(team_size=7)
    with pytest.raises(ValueError, match="1–5"):
        build_battle_rules(team_size=6, lives=6)
    with pytest.raises(ValueError, match="3 或 6"):
        build_battle_rules(team_size=4, lives=3)
    with pytest.raises(ValueError, match="整数"):
        build_battle_rules(team_size="3")
    with pytest.raises(ValueError, match="技能数"):
        build_battle_rules(team_size=3, lives=2, skill_slots=0)


def test_default_rules_match_admin_defaults() -> None:
    """引擎 DEFAULT_RULES 与管理员默认（3V3/2命/4技能）一致。"""
    admin = build_battle_rules()
    assert (DEFAULT_RULES.team_size, DEFAULT_RULES.lives, DEFAULT_RULES.skill_slots) == \
        (admin.team_size, admin.lives, admin.skill_slots)


def test_default_rules_helper() -> None:
    from environment.battle_config import default_rules
    assert default_rules() == build_battle_rules()
