"""E0a 数据层测试：归一 / 效果表齐全 / 性格表 / 占位公式中性恒等 / CLI 冒烟。"""

from __future__ import annotations

import sys

from environment.dataset import STAT_KEYS, _to_int, load_skills, load_spirits
from environment.skillbook import E0_EFFECTS, KIND_TO_CATEGORY, SkillCategory
from environment.statline import NATURE_BONUS, calc_combat_stats, is_valid_nature

# 与 mydocs/E0_skills.json 逐条对齐的期望值：(kind, power, energy_cost)。
# power / energy_cost 直接来自 JSON 的 strong / energy 字段。
EXPECTED_SKILLS: dict[str, tuple[str, int, int]] = {
    "抓挠": ("物攻", 60, 2),
    "抓挠1": ("物攻", 80, 3),
    "抓挠2": ("物攻", 95, 4),
    "撞击": ("魔攻", 60, 2),
    "撞击1": ("魔攻", 80, 3),
    "撞击2": ("魔攻", 95, 3),
    "防御": ("防御", 0, 1),
    "防御1": ("防御", 0, 2),
    "防御2": ("防御", 0, 3),
    "加物攻": ("状态", 0, 1),
    "加魔攻": ("状态", 0, 1),
    "加魔防": ("状态", 0, 1),
    "加物防": ("状态", 0, 1),
    "加速度": ("状态", 0, 1),
}


# ── _to_int：0.0 is falsy 的坑 ──
def test_to_int_zero_is_not_falsy() -> None:
    """"0" / 0 都是合法值，绝不能走 `raw or default`。"""
    assert _to_int("0") == 0
    assert _to_int(0) == 0
    assert _to_int("") == 0
    assert _to_int(None) == 0
    assert _to_int("3") == 3
    assert _to_int(7) == 7
    assert _to_int(None, default=30) == 30


# ── 技能归一 ──
def test_skill_table_matches_documented_values() -> None:
    skills = load_skills()
    assert set(skills) == set(EXPECTED_SKILLS)
    for name, (kind, power, energy) in EXPECTED_SKILLS.items():
        s = skills[name]
        assert (s.kind, s.power, s.energy_cost) == (kind, power, energy)


def test_skills_normalized_to_int() -> None:
    skills = load_skills()
    assert len(skills) == 14
    for s in skills.values():
        assert isinstance(s.power, int)
        assert isinstance(s.energy_cost, int)


def test_defense_power_is_zero_not_thirty() -> None:
    """防御技能 strong 是 "0"——0.0 is falsy 的坑，绝不能被 or 兜成 30。"""
    assert load_skills()["防御"].power == 0
    assert load_skills()["防御1"].power == 0
    assert load_skills()["加速度"].power == 0


def test_skill_category_counts() -> None:
    cats = [KIND_TO_CATEGORY[s.kind] for s in load_skills().values()]
    assert sum(1 for c in cats if c == SkillCategory.ATTACK) == 6
    assert sum(1 for c in cats if c == SkillCategory.DEFENSE) == 3
    assert sum(1 for c in cats if c == SkillCategory.STATUS) == 5


# ── 效果表 ──
def test_effect_table_keys_match_skill_table_both_ways() -> None:
    """双向断言：加数据忘了加效果 / 加效果忘了加数据都会红。"""
    skills = load_skills()
    assert set(E0_EFFECTS) == set(skills)


def test_effect_table_semantics() -> None:
    """三条必须落进代码的事实，逐条钉死。"""
    # ① 抓挠（基础款）没有应对子句，撞击（基础款）有 → 不能按 kind 推
    assert E0_EFFECTS["抓挠"].counter_vs is None
    assert E0_EFFECTS["抓挠"].self_energy_gain == 1
    assert E0_EFFECTS["撞击"].counter_vs == SkillCategory.STATUS
    assert E0_EFFECTS["抓挠1"].counter_damage_mult == 1.5
    # ② 加速度是 flat，其余四个状态技能是 pct
    assert E0_EFFECTS["加速度"].mode == "flat" and E0_EFFECTS["加速度"].layers == 8
    assert E0_EFFECTS["加物攻"].mode == "pct" and E0_EFFECTS["加物攻"].layers == 9
    assert E0_EFFECTS["加魔攻"].layers == 9
    assert E0_EFFECTS["加魔防"].layers == 8
    assert E0_EFFECTS["加物防"].layers == 8
    # 应对加层数
    assert E0_EFFECTS["加物攻"].counter_extra_layers == 2
    assert E0_EFFECTS["加魔防"].counter_extra_layers == 1
    # 防御系减伤本身就是应对效果
    assert E0_EFFECTS["防御"].reduction_pct == 0.70
    assert E0_EFFECTS["防御1"].reduction_pct == 0.80
    assert E0_EFFECTS["防御2"].reduction_pct == 0.85
    assert E0_EFFECTS["防御"].counter_vs == SkillCategory.ATTACK


# ── 精灵归一 ──
def test_spirits_normalized() -> None:
    spirits = load_spirits()
    assert len(spirits) == 6
    for sp in spirits.values():
        assert set(sp.stats) == set(STAT_KEYS)
        assert all(isinstance(v, int) for v in sp.stats.values())
        assert sp.types
        assert sp.trait_name
        assert len(sp.skills_default) >= 4


def test_learnable_pool_range() -> None:
    """数据自检口径：未选血脉最小池 4，选了血脉最大池 9。"""
    spirits = load_spirits()
    no_bloodline = [len(sp.skills_default) for sp in spirits.values()]
    with_bloodline = [
        len(set(sp.skills_default) | set(sp.skills_bloodline)) for sp in spirits.values()
    ]
    assert min(no_bloodline) == 4
    assert max(with_bloodline) == 9


# ── 性格表 ──
def test_nature_bonus_shape() -> None:
    # 30 种非中性：提升六维之一 × 降低另外五维之一（坦率走 fallback，不进表）
    assert len(NATURE_BONUS) == 6 * 5
    assert "坦率" not in NATURE_BONUS
    # 每个 (升, 降) 组合恰好出现一次 → 完备性
    combos = set(NATURE_BONUS.values())
    assert combos == {(b, r) for b in STAT_KEYS for r in STAT_KEYS if b != r}
    # 命名格式：加『某维』减『另一维』
    assert NATURE_BONUS["加攻击减速度"] == ("atk", "speed")
    assert NATURE_BONUS["加速度减生命"] == ("speed", "hp")


def test_is_valid_nature() -> None:
    assert is_valid_nature("坦率")  # 中性默认合法
    assert is_valid_nature("加攻击减速度")
    assert not is_valid_nature("传说性格")


# ── 属性公式（真实公式）──
_BASE = {"hp": 120, "atk": 80, "sp_atk": 80, "def": 105, "sp_def": 105, "speed": 92}

# 手算口径：中性（iv 全 0、中性性格）下的公式值。
#   hp: 1.7×120+70=274, +100 → 374
#   atk/sp_atk: 1.1×80+50=138, +50 → 188
#   def/sp_def: 1.1×105+50=165.5, +50 → 215（int(215.5)）
#   speed: 1.1×92+50=151.2, +50 → 201（int(201.2)）
_NEUTRAL_STATS = {"hp": 374, "atk": 188, "sp_atk": 188, "def": 215, "sp_def": 215, "speed": 201}


def test_calc_neutral_formula() -> None:
    """中性口径：iv 全 0 + 中性性格 → 真实公式值（不再是种族值）。"""
    assert calc_combat_stats(_BASE) == _NEUTRAL_STATS
    assert calc_combat_stats(_BASE, iv={}, nature="坦率") == _NEUTRAL_STATS


def test_calc_iv_adds_points() -> None:
    """个体值每点折合 +3：atk = 1.1×(80+10×3)+50+50 = 221.0 → 221。"""
    out = calc_combat_stats(_BASE, iv={"atk": 10})
    assert out["atk"] == 221
    assert out["hp"] == 374  # 其余项不受影响


def test_calc_nature_changes_values() -> None:
    out = calc_combat_stats(_BASE, nature="加攻击减速度")  # 升 atk +20%、降 speed −10%
    # 口径（负责人 2026-08-25 改公式：先对 raw 取整，再乘性格修正、加平值，最后再取整）：
    #   atk: int(int(1.1×80+50)×1.2)+50 = int(138×1.2)+50 = 165+50... 实际 int(165.6+50)=215
    #   speed: int(int(1.1×92+50)×0.9)+50 = int(151×0.9)+50 = int(135.9+50) = 185
    assert out["atk"] == 215
    assert out["speed"] == 185
    assert out["hp"] == 374     # 中性项不变


def test_calc_unknown_nature_falls_back_to_neutral() -> None:
    assert calc_combat_stats(_BASE, nature="不存在的性格") == _NEUTRAL_STATS


# ── CLI 冒烟（零网络、零文件写入）──
def test_data_report_cli(monkeypatch, capsys) -> None:
    import environment.__main__ as main_mod

    monkeypatch.setattr(sys, "argv", ["environment", "--data-report"])
    assert main_mod.main() == 0
    out = capsys.readouterr().out
    assert "技能 14 条（攻击 6 / 防御 3 / 状态 5）" in out
    assert "防御.power = 0（不是 30）✅" in out
    assert "可学池最小 4 最大 9" in out


def test_team_report_cli(monkeypatch, capsys) -> None:
    import environment.__main__ as main_mod

    monkeypatch.setattr(sys, "argv", ["environment", "--team-report"])
    assert main_mod.main() == 0
    out = capsys.readouterr().out
    assert "队伍 a" in out and "队伍 b" in out
    assert "校验：两队合法 ✅" in out


def test_probe_errors_cli(monkeypatch, capsys) -> None:
    """Gate 核对工具：非法阵容被逐条拒绝、不崩、返回 0。"""
    import environment.__main__ as main_mod

    monkeypatch.setattr(sys, "argv", ["environment", "--probe-errors"])
    assert main_mod.main() == 0
    out = capsys.readouterr().out
    assert "共 11 条错误" in out
