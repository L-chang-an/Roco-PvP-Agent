"""进化链数据与索引测试（2026-08-30）：数据一致性 + 退化唯一 + 首领化集合。

已拍板口径：萌化 = 沿链往低退种族值（x 层 = x 阶，最低阶拦截）；首领化 = 一阶进化、
**只有 boss 的上一阶**可触发，多分支只有迪莫（4）/魔力猫（2），其余与地区形态一一对应。
"""

from __future__ import annotations

import json

from environment.dataset import (
    EVOLUTION_CHAINS_FILE,
    FULL_SPIRITS_FILE,
    derive_evolution_chains,
)
from environment.evolution import (
    base_stats_of,
    boss_targets_of,
    is_lowest,
    prev_name_of,
    prev_of,
)


def _records() -> list[dict]:
    return json.loads(FULL_SPIRITS_FILE.read_text(encoding="utf-8"))


# ── 数据一致性 ──
def test_chains_file_matches_derive() -> None:
    """evolution_chains.json 是 derive_evolution_chains 的固化产物：逐字节一致。"""
    doc = json.loads(EVOLUTION_CHAINS_FILE.read_text(encoding="utf-8"))
    assert doc == derive_evolution_chains(_records())


def test_chain_names_all_exist_in_spirit_table() -> None:
    from environment.dataset import load_spirits

    names = set(load_spirits())
    for c in json.loads(EVOLUTION_CHAINS_FILE.read_text(encoding="utf-8"))["chains"]:
        for n in c["path"]:
            assert n in names, f"链上名字不在精灵表：{n}"


# ── 退化唯一性与方向 ──
def test_prev_of_directions() -> None:
    assert prev_of("圣光迪莫") == "迪莫"
    assert prev_of("喵呜") == "喵喵"
    assert prev_of("叶冕魔力猫") == "魔力猫"
    assert prev_of("迪莫") is None          # 链首无退化目标
    assert prev_of("喵喵") is None


def test_prev_name_of_multi_steps_and_clamp() -> None:
    assert prev_name_of("叶冕魔力猫", 1) == "魔力猫"
    assert prev_name_of("叶冕魔力猫", 2) == "喵呜"
    assert prev_name_of("叶冕魔力猫", 3) == "喵喵"
    assert prev_name_of("叶冕魔力猫", 99) == "喵喵"   # 越界夹到最低阶


def test_is_lowest() -> None:
    assert is_lowest("迪莫") and is_lowest("喵喵") and is_lowest("矿晶虫")
    assert not is_lowest("魔力猫") and not is_lowest("圣光迪莫")


# ── 首领化集合（一阶进化、仅 boss 上一阶）──
def test_boss_targets_multi_branch_only_dimo_and_magic_cat() -> None:
    assert boss_targets_of("迪莫") == ("圣光迪莫", "圣水迪莫", "圣火迪莫", "圣草迪莫")
    assert boss_targets_of("魔力猫") == ("叶冕魔力猫", "武斗酷猫")


def test_boss_targets_single_branch_region_forms() -> None:
    # 地区形态一一对应（单分支）
    assert boss_targets_of("晶石蜗（星彩榴石的样子）") == ("钻石蜗（星彩榴石的样子）",)
    assert boss_targets_of("棋祈督（白子）") == ("棋契陛下（白棋棋祈督分支）",)
    assert boss_targets_of("岚鸟（夏天的样子）") == ("霜翼领主（夏天的样子）",)


def test_boss_targets_empty_for_non_prev_stage() -> None:
    # 喵喵/喵呜/矿晶虫/棋棋不是 boss 上一阶 → 不可首领化（2026-08-30 拍板）
    assert boss_targets_of("喵喵") == ()
    assert boss_targets_of("喵呜") == ()
    assert boss_targets_of("矿晶虫") == ()
    assert boss_targets_of("棋棋（白子）") == ()
    assert boss_targets_of("叶冕魔力猫") == ()   # 首领形态自身不可再首领化


def test_base_stats_of_race_values() -> None:
    assert base_stats_of("迪莫")["hp"] == 120
    assert base_stats_of("圣光迪莫")["hp"] == 122   # 首领形态种族值更高
