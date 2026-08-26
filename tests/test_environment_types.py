"""E2 属性克制与系别测试（修正版克制表）：完整矩阵 / 多系克制封顶 / 抵抗乘算 / STAB / 系别恒为自身系别。

克制表一致性对照 `mydocs/type_chart.md` 原文（Markdown 表格）解析——转录必须与来源逐条一致。
多系别规则（负责人确认）：克制取乘积但**封顶 ×3**，抵抗**正常乘算**，一克制一抵抗互抵。
本表**不对称**（例：武 克制 普通，普通 对 武 中性），并有互克对（光↔幽、地↔冰、萌↔恶）
与自克/自抗（龙/幽 自克 ×2，毒 自抗 ×0.5）。
**血脉不改写系别**（负责人 2026-08-25 澄清）：types 恒为精灵自身系别，血脉只决定血脉技能系别。
"""

from __future__ import annotations

from pathlib import Path

from environment.dataset import DataSource, load_spirits
from environment.rules import DEFAULT_RULES
from environment.teambuilder import TeamPick, build_roster
from environment.types import (CHART, MAX_EFFECTIVENESS, STAB_MULT, TYPE_NAMES,
                               normalize_type, stab_multiplier, type_effectiveness)

CHART_MD = Path(__file__).resolve().parents[1] / "mydocs" / "type_chart.md"


def _parse_chart_md() -> dict[str, dict[str, float]]:
    """解析 type_chart.md 的 Markdown 表格 → {防御方: {攻击方: 倍率}}（与 types.CHART 同形状）。"""
    chart: dict[str, dict[str, float]] = {}
    header: list[str] | None = None
    for line in CHART_MD.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if header is None:
            if "攻击方" in cells[0]:
                header = cells[1:]
            continue
        defense = cells[0]
        if defense.startswith("--"):
            continue
        chart[defense] = {a: float(v) for a, v in zip(header, cells[1:])}
    return chart


# ── 克制表：与来源一致 ──
def test_chart_matches_source_md() -> None:
    """CHART 转录必须与 type_chart.md 原文逐条一致。"""
    assert CHART == _parse_chart_md()


def test_chart_matrix_shape_and_values() -> None:
    """18×18 完整，值 ∈ {1.0, 2.0, 0.5}。"""
    assert len(CHART) == 18
    for d, row in CHART.items():
        assert d in TYPE_NAMES and len(row) == 18
        assert set(row) == set(TYPE_NAMES)
        assert set(row.values()) <= {1.0, 2.0, 0.5}


def test_chart_is_asymmetric() -> None:
    """本表不对称：武 克制 普通，但 普通 对 武 中性（存全矩阵而非两个方向的由来）。"""
    assert CHART["普通"]["武"] == 2.0
    assert CHART["武"]["普通"] == 1.0


# ── 单系克制 / 抵抗 ──
def test_single_super_resist() -> None:
    assert type_effectiveness("火", ["草"]) == 2.0       # 火克草
    assert type_effectiveness("火", ["水"]) == 0.5       # 火被水克
    assert type_effectiveness("草", ["水"]) == 2.0
    assert type_effectiveness("水", ["火"]) == 2.0


def test_self_super_and_self_resist() -> None:
    assert type_effectiveness("龙", ["龙"]) == 2.0       # 龙自克
    assert type_effectiveness("幽", ["幽"]) == 2.0       # 幽自克
    assert type_effectiveness("毒", ["毒"]) == 0.5       # 毒自抗


def test_mutual_super_pairs() -> None:
    """互克对：光↔幽、地↔冰、萌↔恶 双向 ×2（不是经典对称表）。"""
    for a, d in [("光", "幽"), ("幽", "光"), ("地", "冰"), ("冰", "地"),
                 ("萌", "恶"), ("恶", "萌")]:
        assert type_effectiveness(a, [d]) == 2.0, f"{a} 打 {d}"


# ── 多系别结算（负责人规则）──
def test_double_super_caps_at_three() -> None:
    """多系克制取乘积但封顶 ×3：火 打 草+虫 = 2×2 = 4 → 3。"""
    assert type_effectiveness("火", ["草", "虫"]) == MAX_EFFECTIVENESS == 3.0
    assert type_effectiveness("水", ["火", "地"]) == 3.0
    assert type_effectiveness("水", ["火", "地", "机械"]) == 3.0    # 三系全克制也不超 3


def test_double_resist_multiplies_normally() -> None:
    """多系抵抗正常乘算：火 打 水+龙 = 0.5×0.5 = 0.25。"""
    assert type_effectiveness("火", ["水", "龙"]) == 0.25
    assert type_effectiveness("草", ["火", "毒", "虫"]) == 0.125     # 0.5³


def test_mixed_super_and_resist_neutral() -> None:
    """一克制一抵抗互抵：火 打 草+水 = 2×0.5 = 1.0。"""
    assert type_effectiveness("火", ["草", "水"]) == 1.0
    assert type_effectiveness("草", ["水", "火"]) == 1.0


def test_dual_type_repeated_is_multiplicative() -> None:
    """同名双系等同两系抵抗/克制：火 打 水+水 = 0.25；水 打 机械+机械 → 封顶 3。"""
    assert type_effectiveness("火", ["水", "水"]) == 0.25
    assert type_effectiveness("水", ["机械", "机械"]) == 3.0


# ── 未知系别 ──
def test_unknown_type_is_neutral() -> None:
    assert type_effectiveness("神秘系", ["光"]) == 1.0
    assert type_effectiveness("光", ["神秘系"]) == 1.0
    assert normalize_type("神秘系") == "普通"


# ── STAB ──
def test_stab_matches_attacker_type() -> None:
    assert stab_multiplier("火", ["火"]) == STAB_MULT == 1.25
    assert stab_multiplier("火", ["草", "火"]) == 1.25
    assert stab_multiplier("火", ["草"]) == 1.0
    assert stab_multiplier("神秘系", ["光"]) == 1.0


# ── 血脉不改写系别（负责人 2026-08-25 澄清）──
def test_bloodline_does_not_change_types() -> None:
    """血脉系别不改写精灵自身系别：types 恒为精灵系别（克制/STAB 的依据）。"""
    natural = build_roster(
        [TeamPick("迪莫", ["闪光"]), TeamPick("喵喵", ["抓挠"]), TeamPick("火花", ["火苗"])],
        source=DataSource.FULL,
    )
    assert natural[0]["types"] == ["光"]
    fire = build_roster(
        [TeamPick("迪莫", ["闪光"], bloodline="火"), TeamPick("喵喵", ["抓挠"]),
         TeamPick("火花", ["火苗"])],
        source=DataSource.FULL,
    )
    assert fire[0]["types"] == ["光"]   # 血脉 火 不改写 迪莫 的光系


def test_bloodline_skill_type_must_match_bloodline() -> None:
    """血脉系别决定可携带的血脉技能：光血脉 + 光系血脉技 → 合法；火血脉 + 光系血脉技 → 拒绝。"""
    from environment.teambuilder import validate_team
    ok = validate_team([TeamPick("迪莫", ["折线冲击"], bloodline="光"),
                        TeamPick("喵喵", ["抓挠"]), TeamPick("火花", ["火苗"])],
                       items=[], source=DataSource.FULL)
    assert ok == []                                          # 折线冲击=光系血脉技，配光血脉合法
    bad = validate_team([TeamPick("迪莫", ["折线冲击"], bloodline="火"),
                         TeamPick("喵喵", ["抓挠"]), TeamPick("火花", ["火苗"])],
                        items=[], source=DataSource.FULL)
    assert any("与所选血脉" in e for e in bad)               # 光系血脉技配火血脉 → 拒绝


def test_e2_eff_and_stab_in_engine() -> None:
    """引擎按 unit.types/skill.type 算克制/STAB：火系精灵用火系技能打草系 → eff 2.0 + stab 1.25。

    真实技能尚无效果表（P1 才做），这里手造 Skill 直接构造状态，白盒验证引擎计算。
    """
    from dataclasses import replace

    from environment.actions import Decision, skill_action
    from environment.engine import execute_turn
    from environment.models import BattleState, BattleRng  # noqa: F401
    from environment.models import SideState, Skill, Unit
    from environment.rules import DEFAULT_RULES
    from environment.skillbook import SkillCategory, SkillEffect

    fire = Skill(name="测试火袭", kind="物攻", type="火", power=100, energy_cost=2,
                 effect=SkillEffect(category=SkillCategory.ATTACK))

    def mk(name: str, types: list[str]) -> Unit:
        return Unit(name=name, types=types,
                    stats={"hp": 300, "atk": 100, "sp_atk": 100,
                           "def": 100, "sp_def": 100, "speed": 100},
                    skills=[fire], max_hp=300, current_hp=300, energy=10)

    rules = replace(DEFAULT_RULES, team_size=1)
    s = BattleState(side_a=SideState(units=[mk("炎龙", ["火"])], lives=2),
                    side_b=SideState(units=[mk("草龟", ["草"])], lives=2),
                    rng=BattleRng(7), rules=rules)
    events = execute_turn(s, Decision(skill_action(0)), Decision(skill_action(0)))
    d = [e for e in events if e["type"] == "damage" and e["side"] == "a"][0]
    assert d["eff"] == 2.0 and d["stab"] == 1.25


def test_engine_dual_type_caps_eff() -> None:
    """引擎多系封顶：草+虫 双系防守被 火 打 → damage 事件 eff == 3.0。"""
    from dataclasses import replace

    from environment.actions import Decision, skill_action
    from environment.engine import execute_turn
    from environment.models import BattleState, BattleRng
    from environment.models import SideState, Skill, Unit
    from environment.rules import DEFAULT_RULES
    from environment.skillbook import SkillCategory, SkillEffect

    fire = Skill(name="测试火袭", kind="物攻", type="火", power=100, energy_cost=2,
                 effect=SkillEffect(category=SkillCategory.ATTACK))

    def mk(name: str, types: list[str]) -> Unit:
        return Unit(name=name, types=types,
                    stats={"hp": 300, "atk": 100, "sp_atk": 100,
                           "def": 100, "sp_def": 100, "speed": 100},
                    skills=[fire], max_hp=300, current_hp=300, energy=10)

    rules = replace(DEFAULT_RULES, team_size=1)
    s = BattleState(side_a=SideState(units=[mk("炎龙", ["火"])], lives=2),
                    side_b=SideState(units=[mk("双系靶", ["草", "虫"])], lives=2),
                    rng=BattleRng(7), rules=rules)
    events = execute_turn(s, Decision(skill_action(0)), Decision(skill_action(0)))
    d = [e for e in events if e["type"] == "damage" and e["side"] == "a"][0]
    assert d["eff"] == 3.0
