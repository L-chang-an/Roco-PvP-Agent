"""P2 效果编译器测试：batch-P2.json 全部 54 技能逐一验收。

51 个实现（连击伤害 / 先手 / 吸血 / 能量 / 场下 / 每连击状态 / 连击buff / 偷能量 /
吸血buff / 虫鸣）；3 个歧义 desc（三连破 / 电离爆破 / 雾气环绕）返回 None（待人工介入）。
每个技能都有一条期望断言（参数化表），无死角覆盖。
"""

from __future__ import annotations

import json

import pytest

from environment.dataset import DataSource, load_skills
from environment.skillbook import (
    P2_EFFECTS, P2_SKILLS_FILE, SkillCategory, SkillStatEffect,
)
from environment.types import TYPE_NAMES

FULL = load_skills(DataSource.FULL)
BATCH = json.loads(P2_SKILLS_FILE.read_text("utf-8"))["skills"]


def _expected(sig: str):
    """签名 → 期望元组。sig 字段：cat=攻击/状态, hits=N, eligible, se(energy), eg, heal,
    ls, steal, bench, prio, ratio, stat=...（逗号分隔目标.stat.mode.层）, buff=...（同格式）。"""
    out = dict(cat=None, hits=1, eligible=False, se=0, eg=0, heal=0, ls=0, steal=0,
               bench=0, prio=0, ratio=0.0, stat=(), buff=())
    for part in sig.split(","):
        part = part.strip()
        if not part:
            continue
        key, _, val = part.partition("=")
        if key == "cat":
            out["cat"] = SkillCategory.ATTACK if val == "攻击" else SkillCategory.STATUS
        elif key in ("hits", "se", "eg", "heal", "ls", "steal", "bench", "prio"):
            out[key] = int(val)
        elif key == "ratio":
            out[key] = float(val)
        elif key == "eligible":
            out["eligible"] = (val == "1")
        elif key in ("stat", "buff"):
            out[key] = tuple(_se(token) for token in val.split("|") if token)
    return out


def _se(token: str) -> SkillStatEffect:
    tgt, stat, mode, layers = token.split(".")
    return SkillStatEffect(tgt, stat, mode, int(layers))


# 51 个实现技能的期望签名（按机制分组）
_P2_EXPECT: dict[str, str] = {
    # ── 连击伤害（18）──
    "乱打": "cat=攻击,hits=5,eligible=1", "光之矛": "cat=攻击,hits=3,eligible=1",
    "刺藤": "cat=攻击,hits=2,eligible=1", "午夜噪音": "cat=攻击,hits=5,eligible=1",
    "双响炮": "cat=攻击,hits=2,eligible=1", "啄击": "cat=攻击,hits=2,eligible=1",
    "打雪仗": "cat=攻击,hits=2,eligible=1", "旋转突击": "cat=攻击,hits=3,eligible=1",
    "水花四溅": "cat=攻击,hits=4,eligible=1", "流火": "cat=攻击,hits=3,eligible=1",
    "种皮爆裂": "cat=攻击,hits=2,eligible=1", "缠丝劲": "cat=攻击,hits=2,eligible=1",
    "能量炮": "cat=攻击,hits=2,eligible=1", "落石": "cat=攻击,hits=1,eligible=1",
    "虫刺": "cat=攻击,hits=3,eligible=1", "针状物": "cat=攻击,hits=3,eligible=1",
    "音波弹": "cat=攻击,hits=1,eligible=1", "黑手": "cat=攻击,hits=2,eligible=1",
    # ── 伤害 + 自己回复能量（11）──
    "偷师": "cat=攻击,se=1", "寸拳": "cat=攻击,se=1", "抓挠": "cat=攻击,se=1",
    "火苗": "cat=攻击,se=1", "甩水": "cat=攻击,se=1", "种子弹": "cat=攻击,se=1",
    "虫网": "cat=攻击,se=1", "风吹雪": "cat=攻击,se=1", "鬼火": "cat=攻击,se=1",
    "魔爪": "cat=攻击,se=1", "藤绞": "cat=攻击,se=5",
    # ── 伤害 + 回血 / 吸血 ──
    "丰收": "cat=攻击,heal=20", "汲取": "cat=攻击,ls=100", "蝙蝠": "cat=攻击,ls=100",
    # ── 先手 ──
    "俯冲": "cat=攻击,prio=1", "先发制人": "cat=攻击,prio=1", "后发制人": "cat=攻击,prio=-1",
    # ── 伤害 + 场下 / 敌连击减益 ──
    "养分回流": "cat=攻击,bench=1", "震击": "cat=攻击,buff=foe.combo.flat.-3",
    # ── 虫鸣（队伍携带数加成连击）──
    "虫鸣": "cat=攻击,hits=1,eligible=1",
    # ── 状态：能量 / 回血 / 场下 ──
    "徒长": "cat=状态,eg=10", "休息回复": "cat=状态,heal=30",
    "根吸收": "cat=状态,heal=15,eg=4", "富养化": "cat=状态,bench=3",
    # ── 状态：回能量 + 属性 ──
    "氧输送": "cat=状态,eg=4,stat=self.sp_atk.pct.7",
    "缓一缓": "cat=状态,eg=1,heal=10,stat=self.sp_atk.pct.1|self.sp_def.pct.1|self.speed.flat.1",
    # ── 状态：连击数buff / 吸血buff / 偷能量 ──
    "暴风眼": "cat=状态,buff=self.combo.pct.10", "热身运动": "cat=状态,buff=self.combo.flat.3",
    "耀眼": "cat=状态,buff=foe.combo.flat.-4", "贪婪": "cat=状态,buff=self.lifesteal.flat.1",
    "勾魂": "cat=状态,steal=3",
    # ── 状态：每连击（花炮 / 冰捆缚）──
    "花炮": "cat=状态,hits=2,eligible=1,stat=self.sp_atk.pct.6",
    "冰捆缚": "cat=状态,hits=2,eligible=1,stat=foe.energy_cost.flat.1",
    # ── 人工裁决（2026-08-25）：三连破/电离爆破 = 每连击应用 + 吃连击buff；雾气环绕 = 敌技能能耗一半 ──
    "三连破": "cat=状态,hits=3,eligible=1,stat=self.atk.pct.3",
    "电离爆破": "cat=状态,hits=2,eligible=1,stat=foe.sp_atk.pct.-2|foe.speed.flat.-2",
    "雾气环绕": "cat=状态,ratio=0.5",
}

# 全部 54 个 P2 技能都已实现（2026-08-25 人工裁决后无歧义剩余）。
_P2_FLAGGED: tuple[str, ...] = ()


@pytest.mark.parametrize("name", sorted(_P2_EXPECT), ids=lambda n: n)
def test_p2_skill_effect(name: str) -> None:
    """每个实现技能的编译结果与期望签名逐项一致。"""
    e = P2_EFFECTS[name]
    exp = _expected(_P2_EXPECT[name])
    assert e.category is exp["cat"], name
    assert e.hits == exp["hits"], name
    assert e.combo_eligible == exp["eligible"], name
    assert e.priority == exp["prio"], name
    assert (e.self_energy_gain, e.energy_gain) == (exp["se"], exp["eg"]), name
    assert (e.heal_pct_self, e.lifesteal_pct) == (exp["heal"], exp["ls"]), name
    assert (e.steal_energy, e.bench_energy_gain) == (exp["steal"], exp["bench"]), name
    assert e.energy_foe_cost_ratio == exp["ratio"], name
    assert e.stat_effects == exp["stat"], name
    assert e.buff_effects == exp["buff"], name
    assert name in FULL and FULL[name].type in TYPE_NAMES


def test_all_p2_skills_implemented() -> None:
    """54 个 P2 技能全部实现，无歧义剩余。"""
    assert set(P2_EFFECTS) == {b["name"] for b in BATCH}
    assert _P2_FLAGGED == ()


def test_p2_batch_count_and_names() -> None:
    """批次 54 条全部进效果表。"""
    assert len(BATCH) == 54
    assert set(P2_EFFECTS) == {b["name"] for b in BATCH}


def test_batch_matches_full_authoritative() -> None:
    """批次的 type/kind/power/energy/desc 与 FULL 权威表一致。"""
    for item in BATCH:
        raw = FULL[item["name"]]
        assert item["type"] == raw.type and item["kind"] == raw.kind, item["name"]
        assert item["power"] == raw.power and item["energy_cost"] == raw.energy_cost, item["name"]
        assert item["desc"] == raw.desc, item["name"]


def test_chongming_dynamic_combo_skill() -> None:
    """虫鸣：队伍中每携带 1 个虫鸣，本次连击数 +1（combo_per_team_skill 字段）。"""
    e = P2_EFFECTS["虫鸣"]
    assert e.combo_per_team_skill == "虫鸣"
    assert e.hits == 1 and e.combo_eligible

