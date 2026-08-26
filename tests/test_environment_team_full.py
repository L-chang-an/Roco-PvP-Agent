"""FULL 真实数据组队测试：三条新规则（家族唯一 / 血脉系别 / 首领禁止）。

每个 pick 一律用真实精灵 + 真实技能名。家族判定按负责人指定的「evolution 链首
编号一致 → 同族」规则。
"""

from __future__ import annotations

import pytest

from environment.dataset import DataSource, load_spirits
from environment.statline import calc_combat_stats
from environment.teambuilder import TeamPick, build_roster, learnable_skills, validate_team

F = DataSource.FULL


def _t(spirit: str, skills: list[str], **kw) -> TeamPick:
    return TeamPick(spirit=spirit, skills=skills, **kw)


def _valid_trio() -> list[TeamPick]:
    """三只不同家族（迪莫 001 / 喵喵 002 / 火花），各自带默认技能。"""
    return [_t("迪莫", ["闪光"]), _t("喵喵", ["抓挠"]), _t("火花", ["火苗"])]


# ── learnable_skills（FULL）──
def test_learnable_default_stone_legend() -> None:
    pool = learnable_skills("迪莫", source=F)
    assert "闪光" in pool              # 默认
    assert "气泡" in pool              # 技能石
    assert "疾风连袭" not in pool      # 传说仅圣羽翼王有，迪莫没有


def test_learnable_bloodline_filtered_by_type() -> None:
    assert "折线冲击" not in learnable_skills("迪莫", source=F)       # 无血脉 → 禁血脉技
    assert "折线冲击" in learnable_skills("迪莫", "光", source=F)      # 光血脉 → 光系血脉技
    assert "折线冲击" not in learnable_skills("迪莫", "火", source=F)  # 火血脉 → 光系血脉技不可学
    assert "火焰冲锋" in learnable_skills("迪莫", "火", source=F)      # 火血脉 → 火系血脉技


def test_e0_learnable_unchanged() -> None:
    """E0 路径逐字节不变：选合法血脉才拓宽。"""
    assert "撞击2" in learnable_skills("迪莫", "火")
    assert "撞击2" not in learnable_skills("迪莫")


# ── 规则 1：同一家族只能入队一只 ──
def test_rule1_distinct_families_valid() -> None:
    assert validate_team(_valid_trio(), [], source=F) == []


def test_rule1_same_family_rejected() -> None:
    picks = [_t("喵喵", ["抓挠"]), _t("喵呜", ["抓挠"]), _t("火花", ["火苗"])]
    errs = validate_team(picks, [], source=F)
    assert any("同一家族只能入队一只" in e and "喵喵" in e and "喵呜" in e for e in errs)
    assert any("002" in e for e in errs)


def test_rule1_duplicate_spirit_rejected_in_full() -> None:
    """FULL 下同名重复 = 同家族 → 规则 1 拦（与 E0 的「同名允许重复」相反）。"""
    picks = [_t("迪莫", ["闪光"]), _t("迪莫", ["猛烈撞击"]), _t("火花", ["火苗"])]
    errs = validate_team(picks, [], source=F)
    assert any("同一家族只能入队一只" in e for e in errs)


# ── 规则 2：血脉技能系别必须匹配所选血脉 ──
def test_rule2_mismatch_rejected() -> None:
    """喵喵 bloodline=火 + 荆棘爪（type=草）→ 不符。"""
    picks = [_t("喵喵", ["荆棘爪"], bloodline="火"), _t("迪莫", ["闪光"]), _t("火花", ["火苗"])]
    errs = validate_team(picks, [], source=F)
    assert any("血脉技能「荆棘爪」系别为「草」" in e and "所选血脉「火」不符" in e for e in errs)


def test_rule2_matching_bloodline_valid() -> None:
    picks = [_t("喵喵", ["荆棘爪"], bloodline="草"), _t("迪莫", ["闪光"]), _t("火花", ["火苗"])]
    assert validate_team(picks, [], source=F) == []


def test_rule2_no_bloodline_forbids_bloodline_skill() -> None:
    picks = [_t("迪莫", ["折线冲击"]), _t("喵喵", ["抓挠"]), _t("火花", ["火苗"])]
    errs = validate_team(picks, [], source=F)
    assert any("血脉技能「折线冲击」需要先选择血脉系别" in e for e in errs)


def test_rule2_invalid_bloodline_type_rejected() -> None:
    picks = [_t("迪莫", ["闪光"], bloodline="火火"), _t("喵喵", ["抓挠"]), _t("火花", ["火苗"])]
    errs = validate_team(picks, [], source=F)
    assert any("血脉「火火」不是合法系别" in e for e in errs)


# ── 规则 3：首领形态不可入队 ──
def test_rule3_boss_rejected() -> None:
    picks = [_t("圣光迪莫", ["闪光"]), _t("喵喵", ["抓挠"]), _t("火花", ["火苗"])]
    errs = validate_team(picks, [], source=F)
    assert any("首领形态不可入队" in e for e in errs)


def test_rule3_boss_also_same_family_as_base() -> None:
    """首领 + 同族一次报两条（圣光迪莫 vs 迪莫）。"""
    picks = [_t("迪莫", ["闪光"]), _t("圣光迪莫", ["闪光"]), _t("火花", ["火苗"])]
    errs = validate_team(picks, [], source=F)
    assert any("首领形态不可入队" in e for e in errs)
    assert any("同一家族只能入队一只" in e for e in errs)


# ── 一次报全 + E0 隔离 ──
def test_reports_all_rules_at_once() -> None:
    """同时踩 家族/首领/血脉/技能数/规模/道具 → 一次报全。"""
    bad = [
        _t("迪莫", ["折线冲击"]),                 # 血脉技能未选血脉
        _t("圣光迪莫", ["闪光"]),                 # 首领 + 与迪莫同族
        _t("喵喵", ["抓挠"]),
        _t("喵呜", ["抓挠"]),                     # 与喵喵同族
        _t("火花", ["折线冲击"], bloodline="火"),  # 不在可学池
    ]
    errs = validate_team(bad, ["不存在道具", "草魔法", "草魔法"], source=F)
    joined = "；".join(errs)
    assert "队伍规模必须为 3 只" in joined
    assert "首领形态不可入队" in joined
    assert "同一家族只能入队一只" in joined       # 迪莫组 + 喵喵组
    assert "需要先选择血脉系别" in joined
    assert "道具「不存在道具」不存在" in joined
    assert "道具列表含重复项" in joined
    assert len(errs) >= 8


def test_e0_rules_not_applied() -> None:
    """E0 默认路径：首领/家族概念不存在，同名重复仍允许（判断 3）。"""
    picks = [_t("迪莫", ["抓挠1", "加物攻"]), _t("迪莫", ["撞击", "防御"]), _t("小火猴", ["抓挠"])]
    assert validate_team(picks, []) == []


# ── build_roster（FULL）──
def test_build_roster_full_shape() -> None:
    roster = build_roster(_valid_trio(), source=F)
    assert len(roster) == 3
    entry = roster[0]
    assert set(entry) == {"name", "types", "stats", "skills", "nature", "bloodline", "iv", "trait"}
    assert entry["name"] == "迪莫"
    assert entry["types"] == ["光"]          # 真实系别
    # 迪莫中性六维 = 真实种族值公式：1.7×120+70+100=374；1.1×80+50+50=188 …
    assert entry["stats"] == {"hp": 374, "atk": 188, "sp_atk": 188,
                              "def": 215, "sp_def": 215, "speed": 201}
    assert entry["skills"] == ["闪光"] and entry["bloodline"] == ""
    assert entry["trait"] == "最好的伙伴"     # 特性名进入 roster（build_unit 据此绑定）


def test_build_roster_full_stats_formula() -> None:
    """FULL roster 六维 = calc_combat_stats(真实种族值, iv, nature)。"""
    sp = load_spirits(F)["喵喵"]
    expected = calc_combat_stats(sp.stats, {"atk": 10}, "加攻击减速度")
    entry = build_roster([_t("喵喵", ["抓挠"], iv={"atk": 10}, nature="加攻击减速度"),
                          _t("迪莫", ["闪光"]), _t("火花", ["火苗"])], source=F)[0]
    assert entry["stats"] == expected


def test_build_roster_full_raises_on_invalid() -> None:
    with pytest.raises(ValueError):
        build_roster([_t("圣光迪莫", ["闪光"])], source=F)
