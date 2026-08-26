"""E0a 组队测试：validate_team 的每一条拒绝理由 + build_roster 的 roster spec 形状。

所有 pick 一律推导式逐个构造（`[spec] * n` 会让多个引用指向同一个对象）。
"""

from __future__ import annotations

import pytest

from environment.teambuilder import TeamPick, build_roster, learnable_skills, validate_team

# 迪莫默认池 = 抓挠1/撞击/防御/加物攻（4 条），血脉池 = 抓挠2/加物防/加速度/防御1/撞击2。


def _pick(spirit: str = "迪莫", skills: list[str] | None = None, **kw) -> TeamPick:
    """skills 为 None 时用默认双技能；显式传空列表表示「不带技能」。"""
    if skills is None:
        skills = ["抓挠1", "加物攻"]
    return TeamPick(spirit=spirit, skills=skills, **kw)


def _valid_picks() -> list[TeamPick]:
    """一个合法的 3 只队伍（3v3，每只 1–3 技能）。"""
    return [
        TeamPick("迪莫", ["抓挠1", "加物攻"]),
        TeamPick("小火猴", ["抓挠", "撞击1"]),
        TeamPick("水蓝蓝", ["撞击", "加魔攻"]),
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
    picks[0] = _pick("迪莫", ["抓挠1"])
    assert validate_team(picks, []) == []


def test_four_skills_valid() -> None:
    """E3：技能槽位升到 4——四个技能合法（都在可学池内）。"""
    picks = _valid_picks()
    picks[0] = TeamPick("迪莫", ["抓挠1", "撞击", "防御", "加物攻"])
    assert validate_team(picks, []) == []


@pytest.mark.parametrize("skills", [[], ["抓挠1", "撞击", "防御", "加物攻", "加速度"]])
def test_skill_count_out_of_range(skills: list[str]) -> None:
    """0 个或 5 个技能都越界（允许 1–4 个，E3）。"""
    picks = _valid_picks()
    picks[0] = _pick("迪莫", skills)
    errs = validate_team(picks, [])
    assert any("技能数必须为 1–4 个" in e for e in errs)


def test_unknown_skill_name() -> None:
    picks = _valid_picks()
    picks[0] = _pick("迪莫", ["不存在的技能", "加物攻"])
    errs = validate_team(picks, [])
    assert any("技能「不存在的技能」不存在" in e for e in errs)


def test_skill_not_in_default_pool() -> None:
    """撞击2 在血脉池里，未选血脉不可学。"""
    picks = _valid_picks()
    picks[0] = _pick("迪莫", ["撞击2", "加物攻"])
    errs = validate_team(picks, [])
    assert any("技能「撞击2」不在" in e and "可学池" in e for e in errs)


def test_learnable_skills_widens_with_valid_bloodline() -> None:
    assert "撞击2" in learnable_skills("迪莫", "火")
    assert "撞击2" not in learnable_skills("迪莫")  # 无血脉不可学


def test_learnable_skills_ignores_invalid_bloodline() -> None:
    """非法血脉不给任何好处：不拓宽可学池（防偷渡血脉技能）。"""
    assert learnable_skills("迪莫", "雷") == learnable_skills("迪莫")


def test_bloodline_valid() -> None:
    picks = _valid_picks()
    picks[0] = TeamPick("迪莫", ["抓挠2", "加速度"], bloodline="火")
    assert validate_team(picks, []) == []


def test_bloodline_not_in_spirit_list() -> None:
    picks = _valid_picks()
    picks[0] = TeamPick("迪莫", ["抓挠1", "加物攻"], bloodline="雷")
    errs = validate_team(picks, [])
    assert any("血脉「雷」不在「迪莫」的合法血脉列表" in e for e in errs)


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


def test_duplicate_spirit_allowed() -> None:
    """判断 3：同名精灵允许重复入队，validate_team 不拦。"""
    picks = _valid_picks()
    picks[2] = TeamPick("迪莫", ["撞击", "防御"])  # 与第一只同名，合法
    assert validate_team(picks, []) == []


# ── build_roster ──
def test_build_roster_shape() -> None:
    roster = build_roster(_valid_picks())
    assert len(roster) == 3
    entry = roster[0]
    assert set(entry) == {"name", "types", "stats", "skills", "nature", "bloodline", "iv", "trait"}
    assert entry["name"] == "迪莫"
    assert entry["types"] == ["光"]
    assert entry["skills"] == ["抓挠1", "加物攻"]
    # 中性口径（iv 全 0 / 坦率）下的真实公式值
    assert entry["stats"] == {"hp": 374, "atk": 188, "sp_atk": 188,
                              "def": 215, "sp_def": 215, "speed": 201}
    assert entry["nature"] == "坦率"
    assert entry["bloodline"] == ""
    assert entry["iv"] == {}
    assert entry["trait"] == "最好的伙伴"      # E0 迪莫数据自带特性（未注册时在战斗里是惰性的）


def test_build_roster_applies_iv_and_nature() -> None:
    """个体值与性格独立作用于公式（先取整 raw 再乘性格，负责人 2026-08-25 口径）：
    atk = int(int(1.1×(80+30)+50)×1.2)+50 = int(171×1.2)+50 = 255。"""
    picks = _valid_picks()
    picks[0] = _pick("迪莫", iv={"atk": 10}, nature="加攻击减速度")
    entry = build_roster(picks)[0]
    assert entry["stats"]["atk"] == 255
    assert entry["stats"]["speed"] == 185  # int(int(1.1×92+50)×0.9)+50 = int(151×0.9)+50 = 185


def test_build_roster_bloodline_does_not_change_types() -> None:
    """血脉系别不改写精灵系别（负责人 2026-08-25 澄清）：types 恒为自身系别，
    血脉只决定可携带的血脉技能系别（规则 2）。"""
    picks = _valid_picks()
    picks[0] = TeamPick("迪莫", ["抓挠2", "加速度"], bloodline="火")
    entry = build_roster(picks)[0]
    assert entry["bloodline"] == "火"
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
