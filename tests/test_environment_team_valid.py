"""E3 VALID 组队测试：FULL 精灵 + 白名单技能池、1–4 技能槽位、管理员 rules 透传。"""

from __future__ import annotations

import pytest

from environment.battle_config import build_battle_rules
from environment.dataset import DataSource, load_skills, load_spirits
from environment.skillbook import battle_ready
from environment.teambuilder import TeamPick, build_roster, learnable_skills, validate_team

VALID = DataSource.VALID
FULL = DataSource.FULL


def _learnable(spirit: str) -> list[str]:
    return learnable_skills(spirit, "", VALID)


def _pick(spirit: str, skills: list[str]) -> TeamPick:
    return TeamPick(spirit, skills)


# ── learnable_skills：FULL 池 ∩ 白名单 ──
def test_learnable_valid_is_whitelist_intersection() -> None:
    """VALID 可学池 = FULL 可学池 ∩ battle_ready。"""
    for spirit in ["迪莫", "喵喵", "火花", "水蓝蓝", "魔力猫", "焰火"]:
        full_pool = set(learnable_skills(spirit, "", FULL))
        valid_pool = _learnable(spirit)
        assert set(valid_pool) == full_pool & set(load_skills(VALID))
        assert all(battle_ready(s) for s in valid_pool)


def test_every_non_boss_spirit_has_valid_skill() -> None:
    """每只非首领精灵至少能带 1 个白名单技能（最少 1 个的规则可满足）。"""
    spirits = load_spirits(VALID)
    for name, sp in spirits.items():
        if sp.is_boss:
            continue
        assert any(battle_ready(s) for s in sp.skills_default), name


# ── validate_team：VALID 三条规则 + 白名单文案 ──
def test_valid_team_legal() -> None:
    picks = [_pick("迪莫", ["闪光", "猛烈撞击"]), _pick("喵喵", ["抓挠"]), _pick("火花", ["火苗"])]
    assert validate_team(picks, items=[], source=VALID) == []


def test_unimplemented_skill_clear_message() -> None:
    """非白名单技能（借用，FULL 有但效果未实装）→ 清晰文案。"""
    picks = [_pick("迪莫", ["借用"]), _pick("喵喵", ["抓挠"]), _pick("火花", ["火苗"])]
    errs = validate_team(picks, items=[], source=VALID)
    assert any("效果未实装" in e and "借用" in e for e in errs)


def test_family_boss_bloodline_rules_apply_on_valid() -> None:
    assert any("同一家族只能入队一只" in e for e in validate_team(
        [_pick("喵喵", ["抓挠"]), _pick("魔力猫", ["棘突"]), _pick("火花", ["火苗"])],
        items=[], source=VALID))
    assert any("首领形态不可入队" in e for e in validate_team(
        [_pick("圣光迪莫", ["闪光"]), _pick("喵喵", ["抓挠"]), _pick("火花", ["火苗"])],
        items=[], source=VALID))
    assert any("需要先选择血脉系别" in e for e in validate_team(
        [_pick("迪莫", ["折线冲击"]), _pick("喵喵", ["抓挠"]), _pick("火花", ["火苗"])],
        items=[], source=VALID))
    assert any("与所选血脉" in e for e in validate_team(
        [TeamPick("迪莫", ["折线冲击"], bloodline="火"), _pick("喵喵", ["抓挠"]),
         _pick("火花", ["火苗"])],
        items=[], source=VALID))


# ── skill_slots：1–4 ──
def test_four_skills_valid_five_rejected() -> None:
    picks4 = [_pick("迪莫", ["闪光", "猛烈撞击", "魔法增效", "光球"]),
              _pick("喵喵", ["抓挠"]), _pick("火花", ["火苗"])]
    assert validate_team(picks4, items=[], source=VALID) == []
    picks5 = [_pick("迪莫", ["闪光", "猛烈撞击", "魔法增效", "光球", "力量增效"]),
              _pick("喵喵", ["抓挠"]), _pick("火花", ["火苗"])]
    assert any("1–4 个" in e for e in validate_team(picks5, items=[], source=VALID))


def test_min_one_skill_enforced() -> None:
    picks = [_pick("迪莫", []), _pick("喵喵", ["抓挠"]), _pick("火花", ["火苗"])]
    assert any("1–4 个" in e for e in validate_team(picks, items=[], source=VALID))


# ── build_roster：管理员 rules 透传（4v4）──
def test_build_roster_with_admin_rules_4v4() -> None:
    rules = build_battle_rules(team_size=4, lives=3)
    picks = [_pick("迪莫", ["闪光"]), _pick("喵喵", ["抓挠"]),
             _pick("火花", ["火苗"]), _pick("水蓝蓝", ["拍击"])]
    roster = build_roster(picks, source=VALID, rules=rules)
    assert len(roster) == 4
    assert roster[0]["trait"] == "最好的伙伴"      # roster 仍带真实特性名（build_unit 装白板）


def test_build_roster_wrong_size_rejected() -> None:
    rules = build_battle_rules(team_size=4, lives=3)
    picks = [_pick("迪莫", ["闪光"]), _pick("喵喵", ["抓挠"]), _pick("火花", ["火苗"])]
    with pytest.raises(ValueError, match="队伍规模必须为 4 只"):
        build_roster(picks, source=VALID, rules=rules)
