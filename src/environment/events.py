"""事件：类型登记表 + 唯一构造器。

每条事件必带 `type` 与 `side`（无归属的全局错误用 ""）。参考项目每条事件同时写
`team` 和 `side` 两个同义字段，是双事实源；本项目只有 `side`。

E0b 的事件全集在此定死，后续里程碑**只加字段不改形状**；P2 新增两种资源事件
（`energy_gain` 技能回能量 / `steal` 偷能量），damage 事件带 `hit/hits` 连击信息。
"""

from __future__ import annotations

EVENT_TYPES: frozenset[str] = frozenset({
    "damage", "heal", "item_use", "stat_change", "reduce_arm", "recharge",
    "switch", "replace", "faint", "life_loss", "skipped", "battle_end", "error",
    "energy_gain", "steal",
    # 印记/天气批（2026-08-30）
    "mark", "weather", "energy_loss",
    # 防御冷却（2026-08-30）
    "cooldown",
})


def ev(etype: str, side: str, **fields) -> dict:
    """事件唯一构造器：保证每条都带 type 与 side。"""
    return {"type": etype, "side": side, **fields}
