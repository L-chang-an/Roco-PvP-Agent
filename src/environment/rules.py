"""数值常量表：一局对战的全部可调数字**只**住在这里。

术语铁律②：同一个上限只写在 `BattleRules` 一处（参考项目的减伤上限一处读配置、
一处硬编码 `min(0.9, …)`，两个事实源）。

E0a 阶段只落组队相关的字段；E0b 再补战斗字段（energy_max / 优先级 / 伤害系数等）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BattleRules:
    """一局对战的全部数值常量。构造一次、挂在 state 上、**永不按调用覆盖**。"""

    # ── 组队（E0a 已用）──
    team_size: int = 3            # 每方精灵数
    skill_slots: int = 3          # 每只精灵可携带技能数
    iv_max: int = 10              # 个体值上限（每点折合 +3，满值 10 → 加 30）

    # ── 战斗（E0b 补齐）──
    # lives: int = 2
    # energy_max: int = 10
    # energy_start: int = 10
    # recharge_amount: int = 5
    # max_turns: int = 50
    # switch_priority: int = 99
    # item_priority: int = 99
    # item_before_main_action: bool = True
    # stat_pct_per_layer: float = 0.10
    # stat_flat_per_layer: int = 10
    # stat_layer_cap: int = 99
    # damage_coefficient: float = 0.9
    # min_damage: int = 1


DEFAULT_RULES = BattleRules()

# 道具 → 每方每局可用次数。E0 只有一种道具（草魔法，回复当前在场精灵 50% 最大 HP）。
E0_ITEMS: dict[str, int] = {"草魔法": 1}
