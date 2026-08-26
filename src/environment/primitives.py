"""效果原语：效果的最小执行单元。每个原语只做一件事、可独立单测。

这是统一效果架构的「执行层」：声明层（effects.py）的每个 Effect 最终落到某个原语。
S1 只实现 stat_mod / energy_gain / dispel_gains 三个（迪莫特性需要的最小集）；
S2 补 heal_pct（喵喵·氧循环）/ energy_cost_mod（水蓝蓝·浸润）+ 能耗读取。
后续特性/印记按需新增原语，不改现有原语形状。
"""

from __future__ import annotations

from .damage import HealResult, apply_heal
from .models import EnergyCostMod, StatModifier


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
    """写入 Unit.energy_cost_mods 一层「全技能能耗−1」。

    与 apply_stat_mod 同款合并规则（同 permanent/trait/source 追加层），返回值 = 合并后总层数。
    """
    for m in unit.energy_cost_mods:
        if m.permanent == permanent and m.trait == trait and m.source == source:
            m.layers += layers
            return m.layers
    unit.energy_cost_mods.append(EnergyCostMod(layers=layers, permanent=permanent,
                                               trait=trait, source=source))
    return layers


def skill_energy_cost(unit, base_cost: int) -> int:
    """全技能能耗的实际值：`base − Σ(energy_cost_mods 层)`，夹到 0。

    唯一读取能耗减益的地方（actions 的门控 / engine 的支付都走它），保证「付得起」
    与「扣多少」永远一致。
    """
    return max(0, base_cost - sum(m.layers for m in unit.energy_cost_mods))


def combo_bonus(unit) -> tuple[int, int]:
    """连击数buff：(flat 层, pct 层)。1 flat 层 = +1 连击；1 pct 层 = +10%。

    存放形式是 Unit.stat_mods 里 stat="combo" 的记录（普通增益，随离场清除、可被常规驱散）。
    """
    flat = sum(m.layers for m in unit.stat_mods if m.stat == "combo" and m.mode == "flat")
    pct = sum(m.layers for m in unit.stat_mods if m.stat == "combo" and m.mode == "pct")
    return flat, pct


def lifesteal_bonus(unit) -> int:
    """吸血buff层数总和（1 层 = +100% 吸血；如贪婪「自己获得100%吸血」= 1 层）。"""
    return sum(m.layers for m in unit.stat_mods if m.stat == "lifesteal")


def heal_pct(state, unit, pct: int, *, source: str) -> HealResult:
    """按 max_hp 的 pct% 回复（经 apply_heal 唯一入口）。pct 整数，向下取整。"""
    return apply_heal(state, unit, int(unit.max_hp * pct / 100), source=source)


def dispel_gains(unit, scope: str = "regular") -> int:
    """驱散属性增益与能耗减益，返回清除的总层数。

    - scope="regular"（默认）：只清**常规增益**（trait=False），特性增益保留。
    - scope="all"：连特性增益一起清（假设的「驱散特性增益」技能）。
    """
    if scope == "all":
        removed = (sum(m.layers for m in unit.stat_mods)
                   + sum(m.layers for m in unit.energy_cost_mods))
        unit.stat_mods.clear()
        unit.energy_cost_mods.clear()
        return removed
    removed = sum(m.layers for m in unit.stat_mods if not m.trait)
    unit.stat_mods = [m for m in unit.stat_mods if m.trait]
    removed += sum(m.layers for m in unit.energy_cost_mods if not m.trait)
    unit.energy_cost_mods = [m for m in unit.energy_cost_mods if m.trait]
    return removed
