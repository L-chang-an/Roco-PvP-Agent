"""组队测试：validate_team 的每一条拒绝理由 + build_roster 的 roster spec 形状。

数据源 = FULL（默认）。合法 3 只队伍：迪莫(族001)/喵喵(族002)/火花(族005)，
技能均 battle_ready 且在各自可学池内。E0 血脉合法性列表语义已随 E0 删除。
"""

from __future__ import annotations

import pytest

from environment.teambuilder import TeamPick, build_roster, learnable_skills, validate_team

# 迪莫 FULL 可学 battle_ready 池示例：闪光/猛烈撞击/防御/魔法增效/力量增效/光球…
# 迪莫血脉技（需选血脉系别）：折线冲击(光)/火焰冲锋(火)/泡沫(水)…


def _pick(spirit: str = "迪莫", skills: list[str] | None = None, **kw) -> TeamPick:
    """skills 为 None 时用默认双技能；显式传空列表表示「不带技能」。"""
    if skills is None:
        skills = ["闪光", "力量增效"]
    return TeamPick(spirit=spirit, skills=skills, **kw)


def _valid_picks() -> list[TeamPick]:
    """一个合法的 3 只队伍（3v3，家族唯一，技能可学 + battle_ready）。"""
    return [
        TeamPick("迪莫", ["闪光", "力量增效"]),
        TeamPick("喵喵", ["抓挠", "休息回复"]),
        TeamPick("火花", ["火苗", "力量增效"]),
    ]


def test_valid_team_has_no_errors() -> None:
    assert validate_team(_valid_picks(), []) == []
    assert validate_team(_valid_picks(), ["草魔法"]) == []


def test_wrong_team_size() -> None:
    errs = validate_team(_valid_picks()[:2], [])
    assert len(errs) == 1
    assert "队伍规模必须为 3 只" in errs[0]


def test_unknown_spirit() -> None:
    picks = _valid_picks()
    picks[0] = _pick("不存在的精灵")
    errs = validate_team(picks, [])
    assert any("精灵「不存在的精灵」不存在" in e for e in errs)


def test_one_skill_valid() -> None:
    """最少 1 个技能：单个技能合法。"""
    picks = _valid_picks()
    picks[0] = _pick("迪莫", ["闪光"])
    assert validate_team(picks, []) == []


def test_four_skills_valid() -> None:
    """技能槽位 4——四个技能合法（都在可学池内）。"""
    picks = _valid_picks()
    picks[0] = TeamPick("迪莫", ["闪光", "魔法增效", "力量增效", "光球"])
    assert validate_team(picks, []) == []


@pytest.mark.parametrize("skills", [[], ["闪光", "魔法增效", "力量增效", "光球", "猛烈撞击"]])
def test_skill_count_out_of_range(skills: list[str]) -> None:
    """0 个或 5 个技能都越界（允许 1–4 个）。"""
    picks = _valid_picks()
    picks[0] = _pick("迪莫", skills)
    errs = validate_team(picks, [])
    assert any("技能数必须为 1–4 个" in e for e in errs)


def test_unknown_skill_name() -> None:
    picks = _valid_picks()
    picks[0] = _pick("迪莫", ["不存在的技能", "力量增效"])
    errs = validate_team(picks, [])
    assert any("技能「不存在的技能」不存在" in e for e in errs)


def test_bloodline_skill_requires_bloodline() -> None:
    """血脉技（折线冲击=光）未选血脉 → 需要先选择血脉系别。"""
    picks = _valid_picks()
    picks[0] = _pick("迪莫", ["折线冲击"])
    errs = validate_team(picks, [])
    assert any("血脉技能「折线冲击」需要先选择血脉系别" in e for e in errs)


def test_learnable_skills_widens_with_valid_bloodline() -> None:
    """FULL：选血脉把对应系别的血脉技并进可学池。"""
    assert "折线冲击" not in learnable_skills("迪莫")   # 无血脉：血脉技不可学
    assert "折线冲击" in learnable_skills("迪莫", "光")  # 光血脉：光系血脉技可学


def test_bloodline_valid() -> None:
    """FULL：血脉 = 任意合法系别；光系血脉技配光血脉 → 合法。"""
    picks = _valid_picks()
    picks[0] = TeamPick("迪莫", ["折线冲击"], bloodline="光")
    assert validate_team(picks, []) == []


def test_bloodline_skill_type_mismatch() -> None:
    """血脉技系别必须等于所选血脉：光系折线冲击配火血脉 → 拒。"""
    picks = _valid_picks()
    picks[0] = TeamPick("迪莫", ["折线冲击"], bloodline="火")
    errs = validate_team(picks, [])
    assert any("血脉技能「折线冲击」系别为「光」" in e and "与所选血脉「火」不符" in e for e in errs)


def test_unknown_nature() -> None:
    picks = _valid_picks()
    picks[0] = _pick("迪莫", nature="传说性格")
    errs = validate_team(picks, [])
    assert any("性格「传说性格」未知" in e for e in errs)


def test_iv_bad_key() -> None:
    picks = _valid_picks()
    picks[0] = _pick("迪莫", iv={"luck": 5})
    errs = validate_team(picks, [])
    assert any("个体值键「luck」不是六维之一" in e for e in errs)


@pytest.mark.parametrize("iv", [{"atk": 99}, {"atk": -1}, {"atk": "31"}, {"atk": True}])
def test_iv_out_of_range_or_wrong_type(iv: dict) -> None:
    picks = _valid_picks()
    picks[0] = _pick("迪莫", iv=iv)
    errs = validate_team(picks, [])
    assert any("个体值" in e and "越界" in e for e in errs)


def test_iv_max_three_dimensions() -> None:
    """恰好 3 个维度有投入 → 合法。"""
    picks = _valid_picks()
    picks[0] = _pick("迪莫", iv={"atk": 3, "def": 3, "sp_def": 3})
    assert validate_team(picks, []) == []


def test_iv_four_dimensions_rejected() -> None:
    picks = _valid_picks()
    picks[0] = _pick("迪莫", iv={"atk": 3, "def": 3, "sp_def": 3, "speed": 3})
    errs = validate_team(picks, [])
    assert any("最多 3 个维度" in e for e in errs)


def test_iv_zero_value_does_not_count() -> None:
    """值为 0 的维度不算「有投入」，4 个键但只有 3 个非零 → 合法。"""
    picks = _valid_picks()
    picks[0] = _pick("迪莫", iv={"atk": 3, "def": 3, "sp_def": 3, "speed": 0})
    assert validate_team(picks, []) == []


def test_same_family_rejected() -> None:
    """FULL 家族唯一：喵喵/喵呜同属 family 002 → 拒。"""
    picks = _valid_picks()
    picks[2] = TeamPick("喵呜", ["抓挠"])   # 与第 2 只喵喵同族
    errs = validate_team(picks, [])
    assert any("同一家族只能入队一只" in e for e in errs)


def test_unknown_item() -> None:
    errs = validate_team(_valid_picks(), ["不存在道具"])
    assert any("道具「不存在道具」不存在" in e for e in errs)


def test_duplicate_item() -> None:
    errs = validate_team(_valid_picks(), ["草魔法", "草魔法"])
    assert any("道具列表含重复项" in e for e in errs)


def test_reports_all_errors_at_once() -> None:
    picks = _valid_picks()
    picks[0] = _pick("迪莫", [])             # 技能数不足（0 个）
    picks[1] = _pick("不存在的精灵")         # 精灵不存在
    errs = validate_team(picks, ["不存在道具"])  # 道具不存在
    assert len(errs) >= 3


# ── build_roster ──
def test_build_roster_shape() -> None:
    roster = build_roster(_valid_picks())
    assert len(roster) == 3
    entry = roster[0]
    assert set(entry) == {"name", "types", "base_stats", "stats", "skills",
                          "nature", "bloodline", "iv", "trait"}
    assert entry["name"] == "迪莫"
    assert entry["types"] == ["光"]
    assert entry["skills"] == ["闪光", "力量增效"]
    assert entry["base_stats"] == {"hp": 120, "atk": 80, "sp_atk": 80, "def": 105, "sp_def": 105, "speed": 92}
    # 中性口径（iv 全 0 / 坦率）下的真实公式值（⚠️ 实现 _STAT_GROWTH_BASE=10，待负责人拍板）
    assert entry["stats"] == {"hp": 374, "atk": 148, "sp_atk": 148,
                              "def": 175, "sp_def": 175, "speed": 161}
    assert entry["nature"] == "坦率"
    assert entry["bloodline"] == ""
    assert entry["iv"] == {}
    assert entry["trait"] == "最好的伙伴"      # FULL 迪莫图鉴特性


def test_build_roster_applies_iv_and_nature() -> None:
    """个体值与性格独立作用于公式（先取整 raw 再乘性格；⚠️ 实现 _STAT_GROWTH_BASE=10）：
    atk = int(int(1.1×(80+30)+10)×1.2)+50 = int(131×1.2)+50 = 207。"""
    picks = _valid_picks()
    picks[0] = _pick("迪莫", iv={"atk": 10}, nature="加攻击减速度")
    entry = build_roster(picks)[0]
    assert entry["stats"]["atk"] == 207
    assert entry["stats"]["speed"] == 149  # int(int(1.1×92+10)×0.9)+50 = int(111.2×0.9)+50 = 149


def test_build_roster_bloodline_does_not_change_types() -> None:
    """血脉系别不改写精灵系别（负责人 2026-08-25 澄清）：types 恒为自身系别，
    血脉只决定可携带的血脉技能系别（规则 2）。"""
    picks = _valid_picks()
    picks[0] = TeamPick("迪莫", ["折线冲击"], bloodline="光")
    entry = build_roster(picks)[0]
    assert entry["bloodline"] == "光"
    assert entry["types"] == ["光"]   # 迪莫自身系别，不被血脉改写


def test_build_roster_raises_on_invalid() -> None:
    with pytest.raises(ValueError):
        build_roster([TeamPick("迪莫", [])])


def test_build_roster_returns_distinct_dicts() -> None:
    """推导式逐个构造，绝不 `[spec] * n`——6 个引用指向同一个 dict 就会全队串味。"""
    roster = build_roster(_valid_picks())
    roster[0]["stats"]["hp"] = 1
    assert roster[1]["stats"]["hp"] != 1
    assert roster[2]["stats"]["hp"] != 1
