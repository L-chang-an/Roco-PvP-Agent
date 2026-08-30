"""E3 valid_skills.json 数据层测试：已实装效果的技能（P1∪P2∪MW）、格式与 full_skills 一致。"""

from __future__ import annotations

import json
from pathlib import Path

from environment.dataset import DataSource, load_skills, load_spirits
from environment.skillbook import (MW_EFFECTS, P1_EFFECTS, P2_EFFECTS,
                                 ST_EFFECTS, battle_ready)

DATA = Path(__file__).resolve().parents[1] / "src" / "environment" / "data"
VALID = DataSource.VALID


def _raw_file(path: Path) -> list[dict]:
    return json.loads(path.read_text("utf-8"))


def test_valid_skills_count_and_set() -> None:
    """valid 池 = P1∪P2∪MW∪ST 白名单 = battle_ready 集合。"""
    valid = load_skills(VALID)
    assert len(valid) == len(P1_EFFECTS) + len(P2_EFFECTS) + len(MW_EFFECTS) + len(ST_EFFECTS) == 224
    assert set(valid) == set(P1_EFFECTS) | set(P2_EFFECTS) | set(MW_EFFECTS) | set(ST_EFFECTS)
    full = load_skills(DataSource.FULL)
    assert {n for n in full if battle_ready(n)} == set(valid)


def test_valid_skills_format_matches_full() -> None:
    """格式与 full_skills.json 一致：同键、strong/energy 字符串、power=0 → strong=null。"""
    full_raw = _raw_file(DATA / "full_skills.json")
    valid_raw = _raw_file(DATA / "valid_skills.json")
    full_by_name = {s["name"]: s for s in full_raw}
    assert {s["name"] for s in valid_raw} <= set(full_by_name)
    for entry in valid_raw:
        assert set(entry) == {"name", "type", "kind", "desc", "strong", "energy"}, entry["name"]
        src = full_by_name[entry["name"]]
        assert entry == src, entry["name"]          # 与 FULL 原条逐字段一致
        assert isinstance(entry["strong"], str) or entry["strong"] is None
        assert isinstance(entry["energy"], str)
        if entry["kind"] in ("状态", "防御"):
            assert entry["strong"] is None


def test_valid_spirits_are_full() -> None:
    """VALID 精灵表 = FULL（全部 593 只）。"""
    assert load_spirits(VALID) == load_spirits(DataSource.FULL)
    assert len(load_spirits(VALID)) == 593


def test_valid_types_18() -> None:
    """VALID 技能覆盖 18 系（血脉合法性校验用）。"""
    from environment.dataset import load_types
    assert len(load_types(VALID)) == 18


def test_valid_skills_normalized() -> None:
    """归一：power/energy_cost 为 int；防御/状态 power==0（不是 30 的坑）。"""
    for s in load_skills(VALID).values():
        assert isinstance(s.power, int) and isinstance(s.energy_cost, int)
    assert load_skills(VALID)["防御"].power == 0
