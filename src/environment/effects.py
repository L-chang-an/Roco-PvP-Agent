"""声明式效果模型：Effect + EffectBinding。

这是统一效果架构（mark / trait / skill 三源共用）的「声明层」：
来源只声明「某时机做什么」，不写引擎分支。效果原语是封闭集合（见 primitives.py），
每个 Effect 由一个原语执行。

效果分解公式：Effect = (Target, Modifier, Params)。
绑定 = (Hook 时机, 条件, 效果列表)——技能/特性/印记都只是绑定集合。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Effect:
    """一条效果原语的参数。op 是原语名（封闭集合），其余是它的参数。"""

    op: str              # "stat_mod" / "energy_gain" / …（primitives.py 实现）
    target: str = "self"   # self / foe / …（S1 只解析 self）

    # stat_mod 参数
    stat: str = ""
    mode: str = ""       # pct / flat
    layers: int = 1
    permanent: bool = False    # 离场是否保留（非永久离场消失）
    trait: bool = False        # True = 特性增益（免疫常规驱散）

    # 通用数值参数
    value: int | float = 0


@dataclass(frozen=True)
class EffectBinding:
    """一条「时机 → 条件 → 效果」绑定。技能/特性/印记都携带一批这样的绑定。"""

    hook: str            # Hook 枚举值（hooks.py）
    effects: tuple[Effect, ...] = ()
    cond: str = ""       # 条件名（hooks.py 的 _CONDITIONS 注册表）；"" = 无条件
