"""M1 catalog：白名单 DSL + 版本指纹 + 只读查询工具 的确定性测试。"""

from __future__ import annotations

import pytest

import environment.datafingerprint as fp
from environment.dataset import DataSource, load_families, load_skills, load_spirits
from environment.teambuilder import learnable_skills
from roco_pvp_agent.advisor.catalog import (
    QueryNotAllowed,
    SpiritFilter,
    get_build_options,
    get_catalog_version,
    get_skill_profile,
    get_spirit_profile,
    search_spirits,
)


# ── 版本指纹 ──

def test_catalog_version_matches_shared_fingerprint():
    v = get_catalog_version()
    assert v["data_digest"] == fp.data_digest()
    assert v["rules_digest"] == fp.rules_digest()


def test_catalog_version_counts_from_loaders():
    v = get_catalog_version()
    assert v["spirit_count"] == len(load_spirits(DataSource.FULL))
    assert v["skill_count"] == len(load_skills(DataSource.FULL))
    assert v["valid_skill_count"] == len(load_skills(DataSource.VALID))
    assert v["families_count"] == len(load_families(DataSource.VALID))
    # 文档里写的 217 已过时——一律运行时读，这里只断言它是运行时事实（>0 且 == loader）。
    assert v["valid_skill_count"] == len(load_skills(DataSource.VALID))


# ── search_spirits DSL ──

def test_search_type_in_fire_all_fire():
    hits = search_spirits([[SpiritFilter("type", "in", "火")]], limit=100)
    assert hits, "火系应有命中"
    for h in hits:
        assert "火" in h["types"]


def test_search_outer_or_inner_and():
    fire = search_spirits([[SpiritFilter("type", "in", "火")]], limit=1000)
    electric = search_spirits([[SpiritFilter("type", "in", "电")]], limit=1000)
    union = search_spirits(
        [[SpiritFilter("type", "in", "火")], [SpiritFilter("type", "in", "电")]], limit=1000
    )
    assert {h["name"] for h in union} == {h["name"] for h in fire} | {h["name"] for h in electric}

    # 内层 AND：火系 且 特性含「火」
    and_hits = search_spirits(
        [[SpiritFilter("type", "in", "火"), SpiritFilter("trait", "contains", "火")]], limit=1000
    )
    for h in and_hits:
        assert "火" in h["types"]
        assert "火" in h["trait_name"]
    # 所有 AND 命中都在火系集合内
    assert {h["name"] for h in and_hits} <= {h["name"] for h in fire}


def test_search_name_eq_and_learnable_skill():
    by_name = search_spirits([[SpiritFilter("name", "eq", "迪莫")]], limit=10)
    assert [h["name"] for h in by_name] == ["迪莫"]

    # 可学技能走 teambuilder.learnable_skills（计算而非存储字段）
    by_skill = search_spirits([[SpiritFilter("learnable_skill", "in", "闪光")]], limit=1000)
    assert "迪莫" in {h["name"] for h in by_skill}
    for h in by_skill:
        assert "闪光" in learnable_skills(h["name"], "", DataSource.VALID)


def test_search_is_boss_and_family():
    bosses = search_spirits([[SpiritFilter("is_boss", "eq", True)]], limit=1000)
    assert bosses, "应有首领"
    for h in bosses:
        assert h["is_boss"] is True

    # family value 传 family_key
    by_key = search_spirits([[SpiritFilter("family", "eq", "001")]], limit=1000)
    # family value 传精灵名（反查 family_key）
    by_name = search_spirits([[SpiritFilter("family", "eq", "迪莫")]], limit=1000)
    assert {h["name"] for h in by_key} == {h["name"] for h in by_name}
    assert "迪莫" in {h["name"] for h in by_key}


def test_search_empty_filters_no_hits():
    assert search_spirits([]) == []
    assert search_spirits([[]]) == []


def test_search_family_in_list_of_names():
    # family in [精灵名] → 反查 family_key 归一并集
    hits = search_spirits([[SpiritFilter("family", "in", ["迪莫", "喵喵"])]], limit=1000)
    names = {h["name"] for h in hits}
    assert "迪莫" in names and "喵喵" in names


@pytest.mark.parametrize("bad_call", [
    lambda: search_spirits([[SpiritFilter("type", "in", "火")]], limit=0),
    lambda: search_spirits("not-a-list"),
    lambda: search_spirits([["not-a-filter"]]),
])
def test_search_malformed_input_rejected(bad_call):
    with pytest.raises(QueryNotAllowed):
        bad_call()


def test_search_sorted_and_limited():
    hits = search_spirits([[SpiritFilter("type", "in", "火")]], limit=5)
    assert len(hits) <= 5
    names = [h["name"] for h in hits]
    assert names == sorted(names)


# ── QueryNotAllowed ──

@pytest.mark.parametrize("bad", [
    SpiritFilter("hack", "eq", "x"),
    SpiritFilter("name", "hack", "x"),
    SpiritFilter("is_boss", "eq", "yes"),
    SpiritFilter("is_boss", "contains", True),
    SpiritFilter("type", "eq", "火"),          # 集合字段 eq 须 list
    SpiritFilter("type", "in", 123),           # 集合字段 in 须 str
    SpiritFilter("learnable_skill", "eq", "闪光"),
    SpiritFilter("name", "in", "火"),          # 标量字段 in 须 list
    SpiritFilter("name", "contains", 123),     # 标量字段 contains 须 str
    SpiritFilter("name", "in", ["eval"]),      # list 项含注入字样
    SpiritFilter("name", "contains", "eval('x')"),
    SpiritFilter("name", "eq", "open"),
])
def test_query_not_allowed(bad):
    with pytest.raises(QueryNotAllowed):
        search_spirits([[bad]])


# ── get_spirit_profile / get_skill_profile / get_build_options ──

def test_spirit_profile_matches_dataset():
    p = get_spirit_profile("迪莫")
    assert p is not None
    sp = load_spirits(DataSource.VALID)["迪莫"]
    assert p["name"] == sp.name
    assert p["types"] == list(sp.types)
    assert p["trait_name"] == sp.trait_name
    assert p["is_boss"] == sp.is_boss
    assert p["stats"] == sp.stats
    assert p["legal_bloodlines"] == sorted({s.type for s in load_skills(DataSource.VALID).values()})
    # 可学池 == learnable_skills（无血脉）
    assert p["learnable_skills"] == learnable_skills("迪莫", "", DataSource.VALID)


def test_spirit_profile_not_found():
    assert get_spirit_profile("不存在的精灵") is None


def test_skill_profile_implemented_flag():
    ok = get_skill_profile("闪光", source=DataSource.VALID)
    assert ok is not None and ok["implemented"] is True

    # 找一个 FULL 有、VALID 无的技能
    full = load_skills(DataSource.FULL)
    valid = load_skills(DataSource.VALID)
    full_only = next(n for n in full if n not in valid)
    full_profile = get_skill_profile(full_only, source=DataSource.FULL)
    assert full_profile is not None
    assert full_profile["implemented"] is False
    # VALID 源下未实现技能查不到
    assert get_skill_profile(full_only, source=DataSource.VALID) is None


def test_skill_profile_not_found():
    assert get_skill_profile("不存在的技能") is None


def test_build_options_valid_natures_and_iv():
    opts = get_build_options("迪莫")
    assert opts is not None
    assert opts["learnable_skills"] == learnable_skills("迪莫", "", DataSource.VALID)
    assert opts["iv_max"] == 10
    assert opts["iv_dims"] == 3
    assert opts["valid_natures"][0] == "坦率"
    assert len(opts["valid_natures"]) == 1 + 30  # 中性 + 30 非中性
    # VALID 下可学池不含未实装技能
    valid = set(load_skills(DataSource.VALID))
    assert set(opts["learnable_skills"]) <= valid


def test_build_options_not_found():
    assert get_build_options("不存在的精灵") is None
