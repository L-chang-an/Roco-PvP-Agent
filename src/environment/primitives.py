"""效果原语：效果的最小执行单元。每个原语只做一件事、可独立单测。

这是统一效果架构的「执行层」：声明层（effects.py）的每个 Effect 最终落到某个原语。
S1 只实现 stat_mod / energy_gain / dispel_gains 三个（迪莫特性需要的最小集）；
S2 补 heal_pct（喵喵·氧循环）/ energy_cost_mod（水蓝蓝·浸润）+ 能耗读取。
后续特性/印记按需新增原语，不改现有原语形状。

**2026-08-29 数据协议 v2**：能耗减益并入 `Unit.stat_mods`（`stat="energy_cost"`，
`mode="flat"`，层数 = 能耗修正值：−1 = 能耗−1、+1 = 能耗+1）；特性增益写入
`TraitState.gains`；`buff_layers` 统一读 stat_mods + trait.gains 两处。
"""

from __future__ import annotations

from .damage import HealResult, apply_heal
from .models import StatModifier, buff_layers


def apply_stat_mod(unit, *, stat: str, mode: str, layers: int,
                   permanent: bool = False, trait: bool = False,
                   source: str = "") -> int:
    """写入 Unit.stat_mods 一条属性增减益层。

    同 (stat, mode, permanent, trait, source) 记录**追加层**（可叠层），否则**新增**一条。
    `source` 是来源标签（技能名/特性名）——同源定位供「不可叠加刷新」与驱散区分用。
    返回该记录合并后的总层数。
    """
    for m in unit.stat_mods:
        if (m.stat == stat and m.mode == mode and m.permanent == permanent
                and m.trait == trait and m.source == source):
            m.layers += layers
            return m.layers
    unit.stat_mods.append(StatModifier(stat=stat, mode=mode, layers=layers,
                                       permanent=permanent, trait=trait, source=source))
    return layers


def apply_energy_gain(unit, amount: int, *, energy_max: int = 10) -> int:
    """回复能量，夹到 energy_max。返回实际 gained。"""
    gained = min(max(0, int(amount)), energy_max - unit.energy)
    unit.energy += gained
    return gained


def apply_energy_cost_mod(unit, *, layers: int, permanent: bool = False,
                          trait: bool = False, source: str = "") -> int:
    """写入 Unit.stat_mods 一条「全技能能耗」层（`stat="energy_cost"`, `mode="flat"`）。

    `layers` = **能耗修正值**：−1 = 能耗−1（水蓝蓝·浸润），+1 = 能耗+1（冰捆缚）。
    与 apply_stat_mod 同款合并规则（同 permanent/trait/source 追加层），返回值 = 合并后总层数。
    """
    for m in unit.stat_mods:
        if (m.stat == "energy_cost" and m.mode == "flat"
                and m.permanent == permanent and m.trait == trait and m.source == source):
            m.layers += layers
            return m.layers
    unit.stat_mods.append(StatModifier(stat="energy_cost", mode="flat", layers=layers,
                                       permanent=permanent, trait=trait, source=source))
    return layers


def skill_energy_cost(unit, base_cost: int) -> int:
    """全技能能耗的实际值：`base + Σ(energy_cost 层)`，夹到 0。

    唯一读取能耗修正的地方（actions 的门控 / engine 的支付都走它），保证「付得起」
    与「扣多少」永远一致。读取 stat_mods + trait.gains 两处。
    """
    return max(0, base_cost + buff_layers(unit, "energy_cost", "flat"))


def combo_bonus(unit) -> tuple[int, int]:
    """连击数buff：(flat 层, pct 层)。1 flat 层 = +1 连击；1 pct 层 = +10%。

    存放形式是 Unit.stat_mods / trait.gains 里 stat="combo" 的记录。
    """
    return buff_layers(unit, "combo", "flat"), buff_layers(unit, "combo", "pct")


def lifesteal_bonus(unit) -> int:
    """吸血buff层数总和（1 层 = +100% 吸血；如贪婪「自己获得100%吸血」= 1 层）。"""
    return buff_layers(unit, "lifesteal")


def heal_pct(state, unit, pct: int, *, source: str) -> HealResult:
    """按 max_hp 的 pct% 回复（经 apply_heal 唯一入口）。pct 整数，向下取整。"""
    return apply_heal(state, unit, int(unit.max_hp * pct / 100), source=source)


def dispel_gains(unit, scope: str = "regular") -> int:
    """驱散属性增益与能耗减益，返回清除的总层数。

    - scope="regular"（默认）：只清**常规增益**（stat_mods 里 trait=False），特性增益
      （TraitState.gains）保留——特性增益免疫常规驱散；
    - scope="all"：连特性增益一起清（假设的「驱散特性增益」技能）。
    """
    if scope == "all":
        removed = (sum(m.layers for m in unit.stat_mods)
                   + sum(m.layers for m in (unit.trait.gains if unit.trait else [])))
        unit.stat_mods.clear()
        if unit.trait:
            unit.trait.gains.clear()
        return removed
    removed = sum(m.layers for m in unit.stat_mods if not m.trait)
    unit.stat_mods = [m for m in unit.stat_mods if m.trait]
    # 特性增益（trait.gains）保留——免疫常规驱散
    return removed
