"""M1 validate：组队硬闸 + 错误码映射器 的确定性测试。

分三层：
1. `_map_error` 直接单测（钉住映射器，独立于 teambuilder 现场调用）。
2. 合法/空阵容/逐一注入非法项 → 对应 code + pick_index。
3. 钉住测试：teambuilder 全部 19 条错误分支 → 映射 code 绝不落到 UNKNOWN。

夹具一律运行时发现，且保证「基础精灵 / 两只队友 / 同族 pair / 首领」彼此家族不冲突。
"""

from __future__ import annotations

import pytest

from environment.dataset import DataSource, load_families, load_skills, load_spirits, load_types
from environment.teambuilder import BOSS_BLOODLINE, TeamPick, learnable_skills
from rock_pvp_agent.advisor.validate import UNKNOWN, TeamValidation, _map_error, validate_team

VALID = DataSource.VALID
_spirits = load_spirits(VALID)
_valid_skills = load_skills(VALID)
_full_skills = load_skills(DataSource.FULL)
_fam = load_families(VALID)


def _mk(spirit: str, skills: list[str] | None = None, *, bloodline: str = "",
        nature: str = "坦率", iv: dict[str, int] | None = None) -> TeamPick:
    if skills is None:
        # 精灵不存在时技能内容无关（teambuilder 先报 SPIRIT_NOT_FOUND 就 continue），
        # 给一个占位技能避免 learnable_skills KeyError。
        skills = [learnable_skills(spirit, "", VALID)[0]] if spirit in _spirits else ["任意技能"]
    return TeamPick(spirit=spirit, skills=skills, bloodline=bloodline, nature=nature, iv=iv or {})


def _codes(picks, items=None) -> list[str]:
    return [e["code"] for e in validate_team(picks, items if items is not None else []).errors]


# ── 稳定夹具（运行时发现，保证互异）──

# 基础精灵：非首领 + 有 VALID 血脉技能
_BLOOD = next(s for s in _spirits.values()
              if not s.is_boss and any(n in _valid_skills for n in s.skills_bloodline))
_BLOOD_SKILL = next(n for n in _BLOOD.skills_bloodline if n in _valid_skills)
_BLOOD_SKILL_TYPE = _valid_skills[_BLOOD_SKILL].type
_OTHER_TYPE = next(t for t in load_types(VALID) if t != _BLOOD_SKILL_TYPE)
_NOT_LEARNABLE = next(n for n in _valid_skills
                      if n not in set(learnable_skills(_BLOOD.name, "", VALID))
                      and n not in _BLOOD.skills_bloodline)
_FULL_ONLY = next(n for n in _full_skills if n not in _valid_skills)

# 首领 + 有 VALID 可学技能
_BOSS = next(s.name for s in _spirits.values()
             if s.is_boss and learnable_skills(s.name, "", VALID))


def _teammates(n: int = 2) -> list[str]:
    """找 n 只「队友」：与 _BLOOD 家族不同、彼此家族不同、非首领、有 VALID 技能。"""
    mates, used = [], {_BLOOD.family_key}
    for s in _spirits.values():
        if s.name == _BLOOD.name or s.is_boss or s.family_key in used:
            continue
        if not learnable_skills(s.name, "", VALID):
            continue
        used.add(s.family_key)
        mates.append(s.name)
        if len(mates) == n:
            return mates
    raise AssertionError("找不到足够队友")


_B, _C = _teammates(2)


def _same_family_pair() -> tuple[str, str, str]:
    """找同族两只非首领，家族 ∉ {_BLOOD,_B,_C} 的家族，成员不同于三者。"""
    excluded_fams = {_BLOOD.family_key, _spirits[_B].family_key, _spirits[_C].family_key}
    excluded_names = {_BLOOD.name, _B, _C}
    for key, names in _fam.items():
        if key in excluded_fams:
            continue
        nonboss = [n for n in names if n not in excluded_names and not _spirits[n].is_boss]
        if len(nonboss) >= 2:
            return nonboss[0], nonboss[1], key
    raise AssertionError("找不到同族两只非首领")


_PAIR0, _PAIR1, _PAIR_KEY = _same_family_pair()


def _legal3() -> list[TeamPick]:
    """三只合法不同家族（供道具类测试：道具是唯一错误来源）。"""
    picks, seen = [], set()
    for s in _spirits.values():
        if s.is_boss or s.family_key in seen:
            continue
        pool = learnable_skills(s.name, "", VALID)
        if not pool:
            continue
        seen.add(s.family_key)
        picks.append(_mk(s.name, [pool[0]]))
        if len(picks) == 3:
            return picks
    raise AssertionError("找不到 3 只合法不同家族")


# ── 1. 映射器直接单测（钉住 teambuilder 固定文案 → code）──

_MAP_CASES = [
    ("队伍规模必须为 3 只，实际 1 只。", "TEAM_SIZE", None),
    ("第1只（不存在）：精灵「不存在」不存在。", "SPIRIT_NOT_FOUND", 1),
    ("第1只（迪莫）：血脉「不是系别」不是合法系别（['光']）。", "BLOODLINE_INVALID", 1),
    ("第1只（圣光迪莫）：首领形态不可入队。", "BOSS_NOT_ALLOWED", 1),
    ("第1只（迪莫）：技能数必须为 1–4 个，实际 0 个。", "SKILL_COUNT", 1),
    ("第1只（迪莫）：技能「迫近攻击」效果未实装（P1∪P2 白名单外），当前不可携带。", "SKILL_NOT_IMPLEMENTED", 1),
    ("第1只（迪莫）：技能「不存在的技能」不存在。", "SKILL_NOT_FOUND", 1),
    ("第1只（迪莫）：血脉技能「火焰冲锋」需要先选择血脉系别。", "BLOODLINE_SKILL_REQUIRED", 1),
    ("第1只（迪莫）：首领血脉精灵无法选用系别血脉技能「火焰冲锋」。", "BLOODLINE_SKILL_MISMATCH", 1),
    ("第1只（迪莫）：血脉技能「火焰冲锋」系别为「火」，与所选血脉「水」不符。", "BLOODLINE_SKILL_MISMATCH", 1),
    ("第1只（迪莫）：技能「抓挠」不在「迪莫」（血脉「无」）的可学池内。", "SKILL_NOT_LEARNABLE", 1),
    ("第1只（迪莫）：性格「未知性格」未知。", "NATURE_UNKNOWN", 1),
    ("第1只（迪莫）：个体值键「foo」不是六维之一。", "IV_INVALID", 1),
    ("第1只（迪莫）：个体值 hp=11 越界（须为 0–10 的整数）。", "IV_INVALID", 1),
    ("第1只（迪莫）：最多 3 个维度可加个体值，实际 4 个维度（['hp', 'atk', 'def', 'speed']）。", "IV_TOO_MANY_DIMS", 1),
    ("同一家族只能入队一只：第1只「喵喵」、第2只「喵呜」同属一个家族（进化链最低阶编号 002）。", "FAMILY_CONFLICT", None),
    ("道具列表含重复项：['草魔法', '草魔法']。", "ITEM_DUPLICATE", None),
    ("道具只能携带一种，实际 2 种：['草魔法', '首领进化']。", "ITEM_DUPLICATE", None),
    ("道具「不存在的道具」不存在。", "ITEM_NOT_FOUND", None),
]


@pytest.mark.parametrize("message,code,pick_index", _MAP_CASES)
def test_map_error(message, code, pick_index):
    assert _map_error(message) == (code, pick_index)


def test_map_error_unknown_fallback():
    assert _map_error("完全陌生的错误文案") == (UNKNOWN, None)


# ── 2. 合法 / 空阵容 / 结构化 ──

def test_legal_team_ok():
    result = validate_team(_legal3(), [])
    assert result.ok is True
    assert result.errors == []


def test_empty_picks_ok():
    result = validate_team([], [])
    assert result.ok is True
    assert result.errors == []


def test_team_validation_structure():
    result = validate_team([_mk("不存在")], [])
    assert isinstance(result, TeamValidation)
    assert result.ok is False
    assert set(result.errors[0].keys()) == {"code", "message", "pick_index"}


# ── 3. 逐分支注入 → 对应 code 命中（含 pick_index）──

def test_team_size():
    assert _codes([_mk(_BLOOD.name)]) == ["TEAM_SIZE"]


def test_spirit_not_found():
    assert "SPIRIT_NOT_FOUND" in _codes([_mk("不存在的精灵"), _mk(_B), _mk(_C)])


def test_bloodline_invalid():
    assert "BLOODLINE_INVALID" in _codes([_mk(_BLOOD.name, bloodline="不是系别"), _mk(_B), _mk(_C)])


def test_boss_not_allowed():
    assert "BOSS_NOT_ALLOWED" in _codes([_mk(_BOSS), _mk(_B), _mk(_C)])


def test_skill_count():
    assert "SKILL_COUNT" in _codes([_mk(_BLOOD.name, []), _mk(_B), _mk(_C)])


def test_skill_not_implemented():
    assert "SKILL_NOT_IMPLEMENTED" in _codes([_mk(_BLOOD.name, [_FULL_ONLY]), _mk(_B), _mk(_C)])


def test_skill_not_found():
    assert "SKILL_NOT_FOUND" in _codes([_mk(_BLOOD.name, ["不存在的技能"]), _mk(_B), _mk(_C)])


def test_bloodline_skill_required():
    assert "BLOODLINE_SKILL_REQUIRED" in _codes([_mk(_BLOOD.name, [_BLOOD_SKILL], bloodline=""),
                                                 _mk(_B), _mk(_C)])


def test_bloodline_skill_boss_mismatch():
    assert "BLOODLINE_SKILL_MISMATCH" in _codes(
        [_mk(_BLOOD.name, [_BLOOD_SKILL], bloodline=BOSS_BLOODLINE), _mk(_B), _mk(_C)])


def test_bloodline_skill_type_mismatch():
    assert "BLOODLINE_SKILL_MISMATCH" in _codes(
        [_mk(_BLOOD.name, [_BLOOD_SKILL], bloodline=_OTHER_TYPE), _mk(_B), _mk(_C)])


def test_skill_not_learnable():
    assert "SKILL_NOT_LEARNABLE" in _codes([_mk(_BLOOD.name, [_NOT_LEARNABLE]), _mk(_B), _mk(_C)])


def test_nature_unknown():
    assert "NATURE_UNKNOWN" in _codes([_mk(_BLOOD.name, nature="未知性格"), _mk(_B), _mk(_C)])


def test_iv_invalid_key():
    assert "IV_INVALID" in _codes([_mk(_BLOOD.name, iv={"foo": 1}), _mk(_B), _mk(_C)])


def test_iv_invalid_value():
    assert "IV_INVALID" in _codes([_mk(_BLOOD.name, iv={"hp": 11}), _mk(_B), _mk(_C)])


def test_iv_too_many_dims():
    assert "IV_TOO_MANY_DIMS" in _codes(
        [_mk(_BLOOD.name, iv={"hp": 1, "atk": 1, "def": 1, "speed": 1}), _mk(_B), _mk(_C)])


def test_family_conflict():
    result = validate_team([_mk(_PAIR0), _mk(_PAIR1), _mk(_C)], [])
    codes = [e["code"] for e in result.errors]
    assert "FAMILY_CONFLICT" in codes
    for e in result.errors:
        if e["code"] == "FAMILY_CONFLICT":
            assert e["pick_index"] is None


def test_item_duplicate():
    assert "ITEM_DUPLICATE" in _codes(_legal3(), ["草魔法", "草魔法"])


def test_item_too_many_kinds():
    assert "ITEM_DUPLICATE" in _codes(_legal3(), ["草魔法", "首领进化"])


def test_item_not_found():
    assert "ITEM_NOT_FOUND" in _codes(_legal3(), ["不存在的道具"])


# ── 4. 钉住测试：teambuilder 全部 19 分支 → 绝不落到 UNKNOWN ──

def test_all_branches_map_to_known_codes():
    scenarios = [
        [_mk(_BLOOD.name)],                                                 # TEAM_SIZE
        [_mk("不存在的精灵"), _mk(_B), _mk(_C)],                             # SPIRIT_NOT_FOUND
        [_mk(_BLOOD.name, bloodline="不是系别"), _mk(_B), _mk(_C)],          # BLOODLINE_INVALID
        [_mk(_BOSS), _mk(_B), _mk(_C)],                                     # BOSS_NOT_ALLOWED
        [_mk(_BLOOD.name, []), _mk(_B), _mk(_C)],                           # SKILL_COUNT
        [_mk(_BLOOD.name, [_FULL_ONLY]), _mk(_B), _mk(_C)],                 # SKILL_NOT_IMPLEMENTED
        [_mk(_BLOOD.name, ["不存在的技能"]), _mk(_B), _mk(_C)],              # SKILL_NOT_FOUND
        [_mk(_BLOOD.name, [_BLOOD_SKILL]), _mk(_B), _mk(_C)],               # BLOODLINE_SKILL_REQUIRED
        [_mk(_BLOOD.name, [_BLOOD_SKILL], bloodline=BOSS_BLOODLINE), _mk(_B), _mk(_C)],
        [_mk(_BLOOD.name, [_BLOOD_SKILL], bloodline=_OTHER_TYPE), _mk(_B), _mk(_C)],
        [_mk(_BLOOD.name, [_NOT_LEARNABLE]), _mk(_B), _mk(_C)],             # SKILL_NOT_LEARNABLE
        [_mk(_BLOOD.name, nature="未知性格"), _mk(_B), _mk(_C)],             # NATURE_UNKNOWN
        [_mk(_BLOOD.name, iv={"foo": 1}), _mk(_B), _mk(_C)],                # IV_INVALID(key)
        [_mk(_BLOOD.name, iv={"hp": 11}), _mk(_B), _mk(_C)],                # IV_INVALID(value)
        [_mk(_BLOOD.name, iv={"hp": 1, "atk": 1, "def": 1, "speed": 1}), _mk(_B), _mk(_C)],
        [_mk(_PAIR0), _mk(_PAIR1), _mk(_C)],                                 # FAMILY_CONFLICT
    ]
    item_scenarios = [
        (_legal3(), ["草魔法", "草魔法"]),      # ITEM_DUPLICATE(重复项)
        (_legal3(), ["草魔法", "首领进化"]),    # ITEM_DUPLICATE(只能一种)
        (_legal3(), ["不存在的道具"]),          # ITEM_NOT_FOUND
    ]
    seen_codes: set[str] = set()
    for picks in scenarios:
        for e in validate_team(picks, []).errors:
            assert e["code"] != UNKNOWN, f"未命中映射：{e['message']!r}"
            seen_codes.add(e["code"])
    for picks, items in item_scenarios:
        for e in validate_team(picks, items).errors:
            assert e["code"] != UNKNOWN, f"未命中映射：{e['message']!r}"
            seen_codes.add(e["code"])

    assert seen_codes == {
        "TEAM_SIZE", "SPIRIT_NOT_FOUND", "BLOODLINE_INVALID", "BOSS_NOT_ALLOWED",
        "SKILL_COUNT", "SKILL_NOT_IMPLEMENTED", "SKILL_NOT_FOUND",
        "BLOODLINE_SKILL_REQUIRED", "BLOODLINE_SKILL_MISMATCH", "SKILL_NOT_LEARNABLE",
        "NATURE_UNKNOWN", "IV_INVALID", "IV_TOO_MANY_DIMS", "FAMILY_CONFLICT",
        "ITEM_DUPLICATE", "ITEM_NOT_FOUND",
    }
