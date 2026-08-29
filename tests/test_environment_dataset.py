"""数据层测试：_to_int / 归一 / 性格表 / 属性公式 / FULL 数据自检 / CLI 冒烟。

E0 教学数据已删除（2026-08-29），数据源默认 FULL（真实 553 技能 / 593 精灵）。
"""

from __future__ import annotations

import sys

from environment.dataset import DataSource, STAT_KEYS, _to_int, load_skills, load_spirits
from environment.skillbook import KIND_TO_CATEGORY, SkillCategory
from environment.statline import NATURE_BONUS, calc_combat_stats, is_valid_nature

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


# ── 默认源 = FULL ──
def test_default_source_is_full() -> None:
    """E0 删除后默认数据源 = FULL（553 技能 / 593 精灵）。"""
    from environment.dataset import DEFAULT_SOURCE

    assert DEFAULT_SOURCE is DataSource.FULL
    assert len(load_skills()) == 553
    assert len(load_spirits()) == 593


# ── 技能/精灵归一（FULL）──
def test_skills_normalized_to_int() -> None:
    skills = load_skills(DataSource.FULL)
    assert len(skills) == 553
    for s in skills.values():
        assert isinstance(s.power, int)
        assert isinstance(s.energy_cost, int)


def test_defense_power_is_zero_not_thirty() -> None:
    """防御技能 strong 是 "0"——0.0 is falsy 的坑，绝不能被 or 兜成 30。"""
    assert load_skills(DataSource.FULL)["防御"].power == 0


def test_skill_category_counts() -> None:
    cats = [KIND_TO_CATEGORY[s.kind] for s in load_skills(DataSource.FULL).values()]
    assert sum(1 for c in cats if c == SkillCategory.ATTACK) == 345
    assert sum(1 for c in cats if c == SkillCategory.DEFENSE) == 52
    assert sum(1 for c in cats if c == SkillCategory.STATUS) == 156


def test_spirits_normalized() -> None:
    spirits = load_spirits(DataSource.FULL)
    assert len(spirits) == 593
    for sp in spirits.values():
        assert set(sp.stats) == set(STAT_KEYS)
        assert all(isinstance(v, int) for v in sp.stats.values())
        assert sp.types
        assert sp.trait_name


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


# ── 属性公式（真实公式；_BASE 为迪莫种族值，FULL 与 E0 逐字节一致）──
_BASE = {"hp": 120, "atk": 80, "sp_atk": 80, "def": 105, "sp_def": 105, "speed": 92}

# 手算口径：中性（iv 全 0、中性性格）下的公式值（⚠️ 2026-08-29：实现 _STAT_GROWTH_BASE=10，
# docstring 写 50，待负责人拍板；以下按实现 10 计算）。
#   hp: 1.7×120+70=274, +100 → 374
#   atk/sp_atk: 1.1×80+10=98, +50 → 148
#   def/sp_def: 1.1×105+10=125.5, +50 → 175
#   speed: 1.1×92+10=111.2, +50 → 161
_NEUTRAL_STATS = {"hp": 374, "atk": 148, "sp_atk": 148, "def": 175, "sp_def": 175, "speed": 161}


def test_calc_neutral_formula() -> None:
    """中性口径：iv 全 0 + 中性性格 → 真实公式值（不再是种族值）。"""
    assert calc_combat_stats(_BASE) == _NEUTRAL_STATS
    assert calc_combat_stats(_BASE, iv={}, nature="坦率") == _NEUTRAL_STATS


def test_calc_iv_adds_points() -> None:
    """个体值每点折合 +3：atk = 1.1×(80+10×3)+10 +50 = 131+50 = 181。"""
    out = calc_combat_stats(_BASE, iv={"atk": 10})
    assert out["atk"] == 181
    assert out["hp"] == 374  # 其余项不受影响


def test_calc_nature_changes_values() -> None:
    out = calc_combat_stats(_BASE, nature="加攻击减速度")  # 升 atk +20%、降 speed −10%
    # 口径（实现：先对 raw 取整，再乘性格修正、加平值，最后再取整；_STAT_GROWTH_BASE=10）：
    #   atk: int(int(1.1×80+10)×1.2)+50 = int(117.6)+50 = 167
    #   speed: int(int(1.1×92+10)×0.9)+50 = int(100.08)+50 = 149
    assert out["atk"] == 167
    assert out["speed"] == 149
    assert out["hp"] == 374     # 中性项不变


def test_calc_unknown_nature_falls_back_to_neutral() -> None:
    assert calc_combat_stats(_BASE, nature="不存在的性格") == _NEUTRAL_STATS


# ── CLI 冒烟（零网络、零文件写入；FULL 口径）──
def test_data_report_cli(monkeypatch, capsys) -> None:
    import environment.__main__ as main_mod

    monkeypatch.setattr(sys, "argv", ["environment", "--data-report"])
    assert main_mod.main() == 0
    out = capsys.readouterr().out
    assert "FULL 数据自检" in out
    assert "技能 553 条（攻击 345 / 防御 52 / 状态 156）" in out
    assert "技能引用缺漏 0 ✅" in out


def test_team_report_cli(monkeypatch, capsys) -> None:
    import environment.__main__ as main_mod

    monkeypatch.setattr(sys, "argv", ["environment", "--team-report"])
    assert main_mod.main() == 0
    out = capsys.readouterr().out
    assert "队伍 FULL" in out
    assert "校验：队伍合法 ✅" in out


def test_probe_errors_cli(monkeypatch, capsys) -> None:
    """Gate 核对工具：非法阵容被逐条拒绝、不崩、返回 0。"""
    import environment.__main__ as main_mod

    monkeypatch.setattr(sys, "argv", ["environment", "--probe-errors"])
    assert main_mod.main() == 0
    out = capsys.readouterr().out
    assert "共" in out and "条错误" in out
