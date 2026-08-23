"""14 个技能的**显式效果表**（`SkillEffect`）。引擎永不读 `desc`。

这是「不做 DSL 编译器」的代价与边界：14 条手写，553 条时才需要编译器。
效果参数**只**从本表读——引擎里出现正则就是设计事故（参考项目的
`re.search(r"(\\d+)%")` 写进了 engine.py，减伤比例从 power 反推）。

三条从 `mydocs/E0_skills.json` 读出来、必须落进代码的事实：
- **抓挠（基础款）没有应对子句，撞击（基础款）有。** 所以效果表必须逐技能手写，
  不能按 `kind` 推。
- **加速度是扁平 +80，其余四个状态技能是百分比。** 所以 `mode` 从第一天就区分
  `pct` / `flat`。
- **撞击2 能耗 3，而抓挠2 能耗 4。** 数据如此，不要"顺手修正"——那是 E0_skills.json
  里的事，不在本表（本表不含 power / energy_cost）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .dataset import load_skills


class SkillCategory(str, Enum):
    """技能三类。**应对判定只看类别**，不看 kind。"""

    ATTACK = "攻击"
    DEFENSE = "防御"
    STATUS = "状态"


KIND_TO_CATEGORY: dict[str, SkillCategory] = {
    "物攻": SkillCategory.ATTACK,
    "魔攻": SkillCategory.ATTACK,
    "防御": SkillCategory.DEFENSE,
    "状态": SkillCategory.STATUS,
}


@dataclass(frozen=True)
class SkillEffect:
    """一个技能的全部结构化效果。引擎永不读 `desc`——desc 只用于展示与将来的提示词。

    注意防御系的减伤**本身就是应对效果**：`应对攻击时，减伤70%` 意味着对手没出攻击
    就什么也不发生。所以 `reduction_pct` 只在 `counter_vs` 命中时生效。
    """

    category: SkillCategory
    self_energy_gain: int = 0                # 抓挠系：自己回复 1 能量
    reduction_pct: float = 0.0               # 防御系：应对命中时的减伤比例
    stat: str = ""                           # 状态系：作用于哪个属性
    mode: str = ""                           # "pct"（每层 10%）| "flat"（每层 +10）
    layers: int = 0                          # 基础层数
    counter_vs: SkillCategory | None = None  # 应对哪一类
    counter_damage_mult: float = 1.0         # 攻击系应对成功时的伤害乘子
    counter_extra_layers: int = 0            # 状态系应对成功时的额外层数


E0_EFFECTS: dict[str, SkillEffect] = {
    # ── 攻击（6）：物攻走 atk/def，魔攻走 sp_atk/sp_def ──
    "抓挠": SkillEffect(category=SkillCategory.ATTACK, self_energy_gain=1),
    "抓挠1": SkillEffect(
        category=SkillCategory.ATTACK, self_energy_gain=1,
        counter_vs=SkillCategory.STATUS, counter_damage_mult=1.5,
    ),
    "抓挠2": SkillEffect(
        category=SkillCategory.ATTACK, self_energy_gain=1,
        counter_vs=SkillCategory.STATUS, counter_damage_mult=1.5,
    ),
    "撞击": SkillEffect(
        category=SkillCategory.ATTACK,
        counter_vs=SkillCategory.STATUS, counter_damage_mult=1.5,
    ),
    "撞击1": SkillEffect(
        category=SkillCategory.ATTACK,
        counter_vs=SkillCategory.STATUS, counter_damage_mult=1.5,
    ),
    "撞击2": SkillEffect(
        category=SkillCategory.ATTACK,
        counter_vs=SkillCategory.STATUS, counter_damage_mult=1.5,
    ),
    # ── 防御（3）：减伤只在应对攻击时武装（E0b 的 build_turn_context 负责武装）──
    "防御": SkillEffect(
        category=SkillCategory.DEFENSE,
        counter_vs=SkillCategory.ATTACK, reduction_pct=0.70,
    ),
    "防御1": SkillEffect(
        category=SkillCategory.DEFENSE,
        counter_vs=SkillCategory.ATTACK, reduction_pct=0.80,
    ),
    "防御2": SkillEffect(
        category=SkillCategory.DEFENSE,
        counter_vs=SkillCategory.ATTACK, reduction_pct=0.85,
    ),
    # ── 状态（5）：属性增益层，1 层 = 10%(pct) 或 +10(flat) ──
    "加物攻": SkillEffect(
        category=SkillCategory.STATUS, stat="atk", mode="pct", layers=9,
        counter_vs=SkillCategory.DEFENSE, counter_extra_layers=2,
    ),
    "加魔攻": SkillEffect(
        category=SkillCategory.STATUS, stat="sp_atk", mode="pct", layers=9,
        counter_vs=SkillCategory.DEFENSE, counter_extra_layers=2,
    ),
    "加魔防": SkillEffect(
        category=SkillCategory.STATUS, stat="sp_def", mode="pct", layers=8,
        counter_vs=SkillCategory.DEFENSE, counter_extra_layers=1,
    ),
    "加物防": SkillEffect(
        category=SkillCategory.STATUS, stat="def", mode="pct", layers=8,
        counter_vs=SkillCategory.DEFENSE, counter_extra_layers=1,
    ),
    "加速度": SkillEffect(
        category=SkillCategory.STATUS, stat="speed", mode="flat", layers=8,
        counter_vs=SkillCategory.DEFENSE, counter_extra_layers=2,
    ),
}


def category_of(skill) -> SkillCategory:
    """按 kind 推类别。效果表的键集合 == 技能表的键集合由测试钉死（双向包含）。"""
    return KIND_TO_CATEGORY[skill.kind]
