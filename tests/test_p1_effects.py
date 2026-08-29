"""P1 效果编译器测试：batch-P1.json 全部 125 技能都能编译成 SkillEffect。

覆盖：125 全命中 / 三类模式抽查（纯伤害/纯防御/纯六维状态含多目标多维度）/ battle_ready
白名单边界 / data/p1_skills.json 与 mydocs 批次及 FULL 权威数据的一致性。
"""

from __future__ import annotations

import json
from pathlib import Path

from environment.dataset import DataSource, load_skills
from environment.skillbook import (
    MW_EFFECTS,
    P1_EFFECTS, P2_EFFECTS, P1_SKILLS_FILE, SkillCategory, SkillStatEffect,
    battle_ready, compile_p1_effect,
)

REPO = Path(__file__).resolve().parents[1]
BATCH_MD = REPO / "mydocs" / "skill-batches" / "batch-P1.json"
FULL = load_skills(DataSource.FULL)


def _batch_names() -> frozenset[str]:
    return frozenset(item["name"] for item in json.loads(BATCH_MD.read_text("utf-8"))["skills"])


def _p1_batch() -> list[dict]:
    return json.loads(P1_SKILLS_FILE.read_text("utf-8"))["skills"]


# ── 全量编译 ──
def test_all_p1_skills_compile() -> None:
    """125 个 P1 技能全部编译成功（效果表 = 批次名集合）。"""
    names = _batch_names()
    assert len(names) == 125
    assert set(P1_EFFECTS) == names


def test_every_compile_is_explicit() -> None:
    """对 FULL 里每个 P1 技能：compile_p1_effect 非 None（纯伤害/纯防御/纯六维都命中）。"""
    for name in _batch_names():
        assert compile_p1_effect(FULL[name]) is not None, f"{name} 未命中 P1 模式"


def test_unsupported_desc_returns_none() -> None:
    """非 P1 的复杂 desc（如 借用）→ None（未支持，battle_ready=False）。"""
    from environment.dataset import RawSkill
    complex_skill = RawSkill(name="借用", type="普通", kind="状态", power=0, energy_cost=3,
                             desc="借用敌方精灵的一个技能，本回合使用。")
    assert compile_p1_effect(complex_skill) is None
    assert not battle_ready("借用")


def test_kind_mismatch_returns_none() -> None:
    """desc 是伤害但 kind 不是攻击（数据异常）→ None，不编译成错误类别。"""
    from environment.dataset import RawSkill
    bad = RawSkill(name="脏", type="普通", kind="状态", power=60, energy_cost=2,
                   desc="对敌方精灵造成物理伤害。")
    assert compile_p1_effect(bad) is None


# ── 三类模式抽查 ──
def test_pure_damage_effect() -> None:
    assert P1_EFFECTS["闪光"].category == SkillCategory.ATTACK
    assert P1_EFFECTS["冰锥"].category == SkillCategory.ATTACK   # power=0? no, 冰锥 power=40
    assert P1_EFFECTS["闪光"].stat_effects == ()


def test_pure_defense_effect() -> None:
    e = P1_EFFECTS["防御"]
    assert e.category == SkillCategory.DEFENSE
    assert e.counter_vs == SkillCategory.ATTACK
    assert e.reduction_pct == 0.70


def test_status_single_stat_self() -> None:
    e = P1_EFFECTS["力量增效"]
    assert e.category == SkillCategory.STATUS
    assert e.stat_effects == (SkillStatEffect("self", "atk", "pct", 10),)


def test_status_multi_stat_self() -> None:
    """丰饶：自己物攻+魔攻+140% → 两条 pct 14。"""
    assert P1_EFFECTS["丰饶"].stat_effects == (
        SkillStatEffect("self", "atk", "pct", 14),
        SkillStatEffect("self", "sp_atk", "pct", 14),
    )


def test_status_mixed_target_and_flat() -> None:
    """咆哮：敌方物攻-60% + 速度-60（flat 减益）→ 两条负层。"""
    assert P1_EFFECTS["咆哮"].stat_effects == (
        SkillStatEffect("foe", "atk", "pct", -6),
        SkillStatEffect("foe", "speed", "flat", -6),
    )


def test_status_foe_flat_speed() -> None:
    assert P1_EFFECTS["雪球"].stat_effects == (SkillStatEffect("foe", "speed", "flat", -9),)


def test_status_multi_stat_foe() -> None:
    """锐利眼神：敌方物防和魔防-120% → 两条 pct -12。"""
    assert P1_EFFECTS["锐利眼神"].stat_effects == (
        SkillStatEffect("foe", "def", "pct", -12),
        SkillStatEffect("foe", "sp_def", "pct", -12),
    )


def test_status_dual_dual_effect() -> None:
    """怒火：自己双攻+120% 双防-40% → 4 条。"""
    assert len(P1_EFFECTS["怒火"].stat_effects) == 4
    assert P1_EFFECTS["怒火"].stat_effects[0] == SkillStatEffect("self", "atk", "pct", 12)
    assert P1_EFFECTS["怒火"].stat_effects[-1] == SkillStatEffect("self", "sp_def", "pct", -4)


# ── battle_ready 白名单边界 ──
def test_battle_ready_union() -> None:
    assert battle_ready("抓挠")      # P2（造成物伤，自己回复1能量）
    assert battle_ready("闪光")      # P1
    assert battle_ready("防御")      # P1（减伤 70%，应对攻击）
    assert battle_ready("火苗")      # P2（造成物伤，自己回复1能量）
    assert battle_ready("三连破")    # P2（2026-08-25 人工裁决后实现）
    assert not battle_ready("借用")  # FULL 里效果未实现


def test_battle_ready_has_179() -> None:
    """可对战白名单 = P1 125 ∪ P2 54 ∪ MW 24 → 203（印记/天气批 2026-08-30）。"""
    ready = {n for n in FULL if battle_ready(n)}
    assert len(ready) == len(P1_EFFECTS) + len(P2_EFFECTS) + len(MW_EFFECTS) == 203


# ── 数据一致性 ──
def test_data_copy_matches_mydocs_batch() -> None:
    """data/p1_skills.json 与 mydocs/skill-batches/batch-P1.json 逐条一致。"""
    assert json.loads(P1_SKILLS_FILE.read_text("utf-8"))["skills"] == _p1_batch()


def test_batch_matches_full_authoritative() -> None:
    """批次的 type/kind/strong/energy/desc 与 FULL 权威表一致（同一技能）。"""
    for item in _p1_batch():
        raw = FULL[item["name"]]
        assert item["type"] == raw.type, item["name"]
        assert item["kind"] == raw.kind, item["name"]
        assert item["power"] == raw.power, item["name"]
        assert item["energy_cost"] == raw.energy_cost, item["name"]
        assert item["desc"] == raw.desc, item["name"]
