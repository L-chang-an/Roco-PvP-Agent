"""跨家族真数据预设队（FULL/VALID 用）：CLI 与 Web 战斗页共用的单一来源。

固定预设 = 从跨家族候选池（每家族取一只非首领、有 battle_ready 默认技能的精灵）按家族 key
排序取前 N 只，每只带前 4 个已实装默认技能——确定性、家族唯一、全部能开战。测试期 LLM 用它
作「固定队伍配置」；CLI `--preset p1` 与战斗页对手预设共用这里。
"""

from __future__ import annotations

from .dataset import DataSource, load_spirits
from .skillbook import battle_ready
from .teambuilder import TeamPick

_PICK_SKILLS = 4   # 每只预设精灵带前 4 个 battle_ready 默认技能（rules.skill_slots = 4）


def valid_spirit_candidates() -> list[str]:
    """FULL/VALID 跨家族候选池：每家族一只非首领、有 battle_ready 默认技能的精灵。

    返回确定性排序（按家族 key），保证预设阵容可复现；家族唯一规则自然满足。
    """
    spirits = load_spirits(DataSource.VALID)
    by_family: dict[str, str] = {}
    for name, s in spirits.items():
        if s.is_boss or not any(battle_ready(x) for x in s.skills_default):
            continue
        by_family.setdefault(s.family_key or name, name)
    return [by_family[k] for k in sorted(by_family)]


def p1_team(names: list[str]) -> list[TeamPick]:
    """真数据队：每只精灵取前 `_PICK_SKILLS` 个 battle_ready 默认技能（跨家族，规则 1 通过）。"""
    spirits = load_spirits(DataSource.VALID)
    return [
        TeamPick(n, [s for s in spirits[n].skills_default if battle_ready(s)][:_PICK_SKILLS])
        for n in names
    ]


def p1_preset(team_size: int) -> tuple[list[TeamPick], list[TeamPick]]:
    """FULL/VALID 真数据默认阵容：两队各 `team_size` 只不同家族（管理员规模自适应）。"""
    cands = valid_spirit_candidates()
    if len(cands) < team_size * 2:
        raise ValueError(f"跨家族候选不足：需要 {team_size * 2} 只，实际 {len(cands)} 只。")
    return p1_team(cands[:team_size]), p1_team(cands[team_size:team_size * 2])


def fixed_team(team_size: int) -> list[TeamPick]:
    """战斗页对手（LLM）的固定预设队：候选池前 `team_size` 只。"""
    cands = valid_spirit_candidates()
    if len(cands) < team_size:
        raise ValueError(f"跨家族候选不足：需要 {team_size} 只，实际 {len(cands)} 只。")
    return p1_team(cands[:team_size])
