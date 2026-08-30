"""数值常量表：一局对战的全部可调数字**只**住在这里。

术语铁律②：同一个上限只写在 `BattleRules` 一处（参考项目的减伤上限一处读配置、
一处硬编码 `min(0.9, …)`，两个事实源）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BattleRules:
    """一局对战的全部数值常量。构造一次、挂在 state 上、**永不按调用覆盖**。"""

    # ── 组队（E0a 已用）──
    team_size: int = 3            # 每方精灵数（管理员接口可配 3–6，见 battle_config.py）
    skill_slots: int = 4          # 每只精灵可携带技能数（E3：最多 4、至少 1）
    iv_max: int = 10              # 个体值上限（每点折合 +3，满值 10 → 加 30）

    # ── 战斗（E0b 补齐）──
    lives: int = 2                      # 每方命数；与 team_size 解耦，不写 lives = len(units)
    energy_max: int = 10                # 能量上限
    energy_start: int = 10              # 开场能量
    recharge_amount: int = 5            # 聚能一回合回复量
    max_turns: int = 20                 # 回合上限；超过后按 E4 规则定胜负（命数→血量百分比和→随机，不再平局）
    switch_priority: int = 99           # 换人优先级（最高一级）
    item_priority: int = 99             # 道具优先级（与换人同级）
    item_before_main_action: bool = True  # 同方同优先级时道具先结算（治离场那只）
    stat_pct_per_layer: float = 0.10    # 1 层 pct = 10%
    stat_flat_per_layer: int = 10       # 1 层 flat = +10
    stat_layer_cap: int = 99            # 每个 (单位, 属性) 的 pct/flat 各自层数上限
    damage_coefficient: float = 0.9     # 伤害系数
    min_damage: int = 1                 # 单次攻击保底伤害


DEFAULT_RULES = BattleRules()

# 草魔法：回复当前在场精灵 50% 最大 HP（量 = max_hp // 2，夹取交给 apply_heal）。
ITEM_HEAL_PCT = 0.5

# 首领进化：首领血脉精灵（boss 的上一阶）一阶进化成首领形态（2026-08-30，见 engine）。
BOSS_EVOLUTION_ITEM = "首领进化"

# 道具 → 每方每局可用次数。
ITEMS: dict[str, int] = {"草魔法": 1, BOSS_EVOLUTION_ITEM: 1}

# 默认道具栏：new_battle items 为 None 时每方携带（**首领进化不默认携带**——需对局
# 配置显式指定，保持既有对局快照/哨兵零变化，2026-08-30）。
DEFAULT_ITEMS: tuple[str, ...] = ("草魔法",)
