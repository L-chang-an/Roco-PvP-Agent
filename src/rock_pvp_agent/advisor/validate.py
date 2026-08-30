"""组队合法性硬闸：把 `teambuilder.validate_team` 的中文错误串映射为结构化错误码。

M1 用**确定性关键字映射器**（teambuilder 返回中文自由文本）；若后续里程碑证明该映射脆弱，
再单独立项把 teambuilder 升级为直接返回 code（属于 `environment` 改动，允许——environment
只是不能 import rock_pvp_agent，改动本身可做）。映射器对未命中分支返回 `UNKNOWN`（不静默、不抛），
钉住测试保证全部已知分支绝不落到 `UNKNOWN`。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from environment.dataset import DataSource
from environment.rules import DEFAULT_RULES, BattleRules
from environment.teambuilder import TeamPick, validate_team as _teambuilder_validate

UNKNOWN = "UNKNOWN"

# 团队级错误：不绑定某一只精灵，`pick_index` 为 None。
_TEAM_LEVEL_CODES = frozenset({"TEAM_SIZE", "FAMILY_CONFLICT", "ITEM_DUPLICATE", "ITEM_NOT_FOUND"})

# 错误码映射规则：每条 = (判据子串元组, code)。子串**全部**命中才映射；顺序靠前优先。
# 判据子串取自 teambuilder.validate_team 的固定文案（测试钉住，改文案即红）。
_ERROR_RULES: list[tuple[tuple[str, ...], str]] = [
    (("队伍规模必须为",), "TEAM_SIZE"),
    (("精灵「", "不存在"), "SPIRIT_NOT_FOUND"),
    (("不是合法系别",), "BLOODLINE_INVALID"),
    (("首领形态不可入队",), "BOSS_NOT_ALLOWED"),
    (("技能数必须为",), "SKILL_COUNT"),
    (("效果未实装",), "SKILL_NOT_IMPLEMENTED"),
    (("道具「", "不存在"), "ITEM_NOT_FOUND"),
    (("技能「", "不存在"), "SKILL_NOT_FOUND"),
    (("需要先选择血脉系别",), "BLOODLINE_SKILL_REQUIRED"),
    (("首领血脉精灵无法选用系别血脉技能",), "BLOODLINE_SKILL_MISMATCH"),
    (("与所选血脉", "不符"), "BLOODLINE_SKILL_MISMATCH"),
    (("可学池内",), "SKILL_NOT_LEARNABLE"),
    (("性格「", "未知"), "NATURE_UNKNOWN"),
    (("个体值键",), "IV_INVALID"),
    (("最多 3 个维度可加个体值",), "IV_TOO_MANY_DIMS"),
    (("个体值", "越界"), "IV_INVALID"),
    (("同一家族只能入队一只",), "FAMILY_CONFLICT"),
    (("道具列表含重复项",), "ITEM_DUPLICATE"),
    (("道具只能携带一种",), "ITEM_DUPLICATE"),
]

# 逐只错误以「第N只（…」开头（全角括号）；团队级错误（家族冲突等）用「第N只「…」」不命中。
_PICK_INDEX_RE = re.compile(r"^第(\d+)只（")


@dataclass(frozen=True)
class TeamValidation:
    """组队校验结果。`errors` 每项 = {code, message, pick_index|None}。"""

    ok: bool
    errors: list[dict]


def _map_error(message: str) -> tuple[str, int | None]:
    """一条中文错误串 → (code, pick_index)。未命中规则 → `UNKNOWN`。"""
    code = UNKNOWN
    for substrings, candidate in _ERROR_RULES:
        if all(s in message for s in substrings):
            code = candidate
            break
    return code, _pick_index(message, code)


def _pick_index(message: str, code: str) -> int | None:
    """团队级错误 → None；逐只错误 → 解析「第N只（…」前缀。"""
    if code in _TEAM_LEVEL_CODES:
        return None
    m = _PICK_INDEX_RE.match(message)
    return int(m.group(1)) if m else None


def validate_team(picks: list[TeamPick], items: list[str], *,
                  rules: BattleRules = DEFAULT_RULES,
                  source: DataSource = DataSource.VALID) -> TeamValidation:
    """组队硬闸：直接调 teambuilder.validate_team，映射为结构化错误码。

    空 `picks` → `ok=True`（M1 详设明确：无阵容 = 无校验，队伍规模错误留给有部分阵容时）。
    默认 `source=VALID`（LegalityGate 口径：实战推荐只用已实装技能）。
    """
    if not picks:
        return TeamValidation(ok=True, errors=[])
    raw = _teambuilder_validate(picks, items, rules=rules, source=source)
    errors = []
    for message in raw:
        code, pick_index = _map_error(message)
        errors.append({"code": code, "message": message, "pick_index": pick_index})
    return TeamValidation(ok=not errors, errors=errors)
