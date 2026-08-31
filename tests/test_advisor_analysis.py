"""M3 analysis：analyze_team 攻防覆盖/速度分层/角色缺口 的确定性测试。"""

from __future__ import annotations

from environment.dataset import DataSource, load_skills
from environment.types import TYPE_NAMES, type_effectiveness
from roco_pvp_agent.advisor.analysis import analyze_team

from rosters import mirror_pair


def test_coverage_matches_type_effectiveness():
    a, _ = mirror_pair()
    result = analyze_team(a)
    skills = load_skills(DataSource.VALID)
    skill_types = {skills[s].type for u in a for s in u["skills"] if s in skills}

    for st, covered in result["offensive_coverage"].items():
        assert st in skill_types
        assert covered == [dt for dt in TYPE_NAMES if type_effectiveness(st, [dt]) >= 2.0]

    team_types = {t for u in a for t in u["types"]}
    for tt, weak_to in result["defensive_gaps"].items():
        assert tt in team_types
        assert weak_to == [at for at in TYPE_NAMES if type_effectiveness(at, [tt]) >= 2.0]


def test_speed_tiers_descending_and_complete():
    a, _ = mirror_pair()
    result = analyze_team(a)
    tiers = result["speed_tiers"]
    assert [name for name, _ in tiers] == sorted([u["name"] for u in a]) or len(tiers) == len(a)
    speeds = [spd for _, spd in tiers]
    assert speeds == sorted(speeds, reverse=True)


def test_energy_hint_covers_all_units():
    a, _ = mirror_pair()
    result = analyze_team(a)
    assert set(result["energy_hint"].keys()) == {u["name"] for u in a}
    for name, avg in result["energy_hint"].items():
        assert avg >= 0


def test_role_gaps_heuristic_flag():
    a, _ = mirror_pair()
    result = analyze_team(a)
    assert result["heuristic"] == {"role_gaps": True}
    # 角色缺口是启发式标签（可能为空或非空，但结构稳定）
    assert isinstance(result["role_gaps"], list)
