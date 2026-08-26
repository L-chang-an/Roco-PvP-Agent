"""FULL 真实数据测试：规模 / 跳过脏记录 / 家族 key / 首领 / 系别 / 技能引用 1:1。

家族判定的量化边界（负责人指定的「不严谨规则」）：按 evolution 链首精灵的编号
判族，真实数据聚成 178 族；其中 31 组不同进化链共享同一编号会被合并为同族
（鸭吉吉 12 个变体全归 011、板板壳与板板壳蜕皮形态同归 012）——这条钉死在这里，
免得日后有人以为它是 bug。
"""

from __future__ import annotations

from environment.dataset import (DEFAULT_SOURCE, DataSource, load_families, load_skills,
                                 load_skipped_spirits, load_spirits, load_types)

F = DataSource.FULL


def _spirits():
    return load_spirits(F)


def _skills():
    return load_skills(F)


# ── 规模与归一 ──
def test_full_skills_scale() -> None:
    skills = _skills()
    assert len(skills) == 553
    assert len(set(skills)) == 553                      # 名唯一
    for s in skills.values():
        assert isinstance(s.power, int) and isinstance(s.energy_cost, int)
        assert s.kind in ("物攻", "魔攻", "防御", "状态")


def test_full_skills_null_strong_means_power_zero() -> None:
    """strong:null（状态/防御）→ power=0，绝不 or 兜成 30。"""
    skills = _skills()
    status_def = [s for s in skills.values() if s.kind in ("状态", "防御")]
    assert status_def and all(s.power == 0 for s in status_def)
    attack = [s for s in skills.values() if s.kind in ("物攻", "魔攻")]
    assert attack and all(s.power > 0 for s in attack)


def test_full_spirits_scale_and_skip_dirty() -> None:
    spirits = _spirits()
    assert len(spirits) == 593                          # 594 − 1 跳过
    skipped = load_skipped_spirits(F)
    assert ("学院呱呱", "技能表为空") in skipped
    assert "学院呱呱" not in spirits
    for sp in spirits.values():
        assert set(sp.stats) == {"hp", "atk", "sp_atk", "def", "sp_def", "speed"}
        assert all(isinstance(v, int) for v in sp.stats.values())
        assert sp.types


def test_full_skill_refs_are_complete() -> None:
    """精灵引用的技能名与技能表 1:1——0 缺漏、0 悬空。"""
    skills = _skills()
    refs = set()
    for sp in _spirits().values():
        for pool in (sp.skills_default, sp.skills_bloodline, sp.skills_stone, sp.skills_legend):
            refs.update(pool)
    assert refs - set(skills) == set()
    assert set(skills) - refs == set()


def test_full_types_and_boss_count() -> None:
    assert len(load_types(F)) == 18
    bosses = [s for s in _spirits().values() if s.is_boss]
    assert len(bosses) == 61


# ── 家族（规则 1 的数据基础）──
def test_family_key_grouping() -> None:
    spirits = _spirits()
    assert spirits["迪莫"].family_key == spirits["圣光迪莫"].family_key == "001"
    assert spirits["喵喵"].family_key == spirits["喵呜"].family_key == "002"
    assert spirits["迪莫"].family_key != spirits["喵喵"].family_key
    assert spirits["迪莫"].family_lowest and not spirits["圣光迪莫"].family_lowest


def test_family_count_is_178() -> None:
    """按链首编号判族 → 178 族（负责人指定的不严谨规则的量化结果）。"""
    assert len(load_families(F)) == 178


def test_family_imprecise_boundary_merges_variants() -> None:
    """不严谨规则的已知边界：板板壳与蜕皮形态同编号 012，鸭吉吉 12 变体全归 011。
    这不是 bug——是负责人指定的「最低阶编号一致 → 同族」判法的直接后果。"""
    spirits = _spirits()
    assert spirits["板板壳"].family_key == spirits["板板壳（蜕皮时的样子）"].family_key == "012"
    yaji = [s.name for s in spirits.values() if "鸭吉吉" in s.name]
    assert len(yaji) == 12 and len({spirits[n].family_key for n in yaji}) == 1


def test_families_json_is_derived_artifact() -> None:
    """families.json 是 derive_families 的固化产物：两者必须逐字节一致，
    否则「家族详情文档」与推导逻辑漂移，配队校验会静默改变。"""
    import json

    from environment.dataset import FAMILIES_FILE, FULL_SPIRITS_FILE, derive_families
    records = json.loads(FULL_SPIRITS_FILE.read_text(encoding="utf-8"))
    doc = json.loads(FAMILIES_FILE.read_text(encoding="utf-8"))
    assert doc == derive_families(records)


def test_all_bosses_are_family_final_forms() -> None:
    """首领恒为家族最高阶（非最低阶），0 例外。"""
    for sp in _spirits().values():
        if sp.is_boss:
            assert not sp.family_lowest, sp.name


# ── 双源隔离：E0 默认路径不受 FULL 影响 ──
def test_default_source_stays_e0() -> None:
    assert DEFAULT_SOURCE is DataSource.E0
    assert len(load_skills()) == 14
    assert len(load_spirits()) == 6
    assert load_families() == {}
    assert load_skipped_spirits() == ()
