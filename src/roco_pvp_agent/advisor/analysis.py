"""队伍分析（M3）：属性攻防覆盖 / 速度分层 / 角色缺口（纯 environment 计算，零 LLM、零随机）。

输入 roster = `build_roster` 输出（含 types/stats/skills/…）。角色缺口是**启发式标签**，
输出里显式标 `heuristic`，M3 不据此下「必带某角色」的硬结论。
"""

from __future__ import annotations

from environment.dataset import DataSource, load_skills
from environment.types import TYPE_NAMES, type_effectiveness


def analyze_team(roster: list[dict], *, source: DataSource = DataSource.VALID) -> dict:
    """分析一支队伍（roster）的攻防覆盖 / 速度分层 / 角色缺口。

    返回：
    - offensive_coverage：技能系别 → 被其 ≥2x 克制的防守系别（队伍能打的克制面）。
    - defensive_gaps：本队系别 → ≥2x 克制它的攻击系别（队伍怕什么）。
    - speed_tiers：(精灵名, speed) 降序。
    - role_gaps：缺失角色提示（无「防御」类技能 → 缺耐久；无「状态」类 → 缺控制），启发式。
    - energy_hint：精灵名 → 技能平均能耗。
    """
    skills = load_skills(source)

    skill_types: set[str] = set()
    team_types: set[str] = set()
    speed_tiers: list[tuple[str, int]] = []
    energy_hint: dict[str, float] = {}
    kinds: set[str] = set()

    for u in roster:
        name = u.get("name", "")
        team_types.update(u.get("types", []))
        speed_tiers.append((name, u.get("stats", {}).get("speed", 0)))

        costs = []
        for sname in u.get("skills", []):
            sk = skills.get(sname)
            if sk is None:
                continue
            skill_types.add(sk.type)
            kinds.add(sk.kind)
            costs.append(sk.energy_cost)
        energy_hint[name] = (sum(costs) / len(costs)) if costs else 0.0

    offensive_coverage: dict[str, list[str]] = {}
    for st in sorted(skill_types):
        covered = [dt for dt in TYPE_NAMES if type_effectiveness(st, [dt]) >= 2.0]
        if covered:
            offensive_coverage[st] = covered

    defensive_gaps: dict[str, list[str]] = {}
    for tt in sorted(team_types):
        weak_to = [at for at in TYPE_NAMES if type_effectiveness(at, [tt]) >= 2.0]
        if weak_to:
            defensive_gaps[tt] = weak_to

    role_gaps: list[str] = []
    if "防御" not in kinds:
        role_gaps.append("缺耐久/恢复（无防御类技能）")
    if "状态" not in kinds:
        role_gaps.append("缺控制（无状态类技能）")

    speed_tiers.sort(key=lambda x: -x[1])

    return {
        "offensive_coverage": offensive_coverage,
        "defensive_gaps": defensive_gaps,
        "speed_tiers": speed_tiers,
        "role_gaps": role_gaps,
        "energy_hint": energy_hint,
        "heuristic": {"role_gaps": True},
    }
