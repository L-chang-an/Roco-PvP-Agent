"""E4 事件可见性：按 viewer 过滤事件流（展示级，纯函数、不写状态）。

引擎事件**全量**落盘/记录；给玩家的展示经本函数变换。敌方白名单之外的信息一律隐藏：
- 敌方**绝对血量**（damage 的 `target_hp_left` → 改成 `target_hp_pct`；heal 作用于敌方的
  `applied/overflow/hp` 去掉——否则泄漏敌方 max_hp）；
- 敌方**道具剩余次数**（item_use → 去 uses_left）。

敌方**增减益可见**（负责人 2026-08-25 修正：双方阵营都有状态栏）→ `stat_change` / `switch`
（含 cleared_layers，由可见的增益层派生）原样保留。敌方**系别放行**（公开图鉴）→ `eff`/`stab` 保留。
敌方能量 / 命数 / 技能名 / 特性 / 精灵名 均为白名单内 → 相关事件原样保留。

**fail-closed**：不在 `EVENT_TYPES` 登记表里的事件类型（引擎从未定义过）→ 丢弃并 `warnings.warn`，
绝不静默放行未知信息。
"""

from __future__ import annotations

import warnings

from .events import EVENT_TYPES


def _foe_names(state, viewer: str) -> frozenset[str]:
    """敌方单位名集合（名字是白名单内，用于判定事件里的单位归属）。"""
    return frozenset(u.name for u in state.foe(viewer).units)


def _foe_unit_by_name(state, viewer: str, name: str):
    """敌方单位（name 必然命中；用 max_hp 换 hp 百分比，max_hp 整局不变）。"""
    for u in state.foe(viewer).units:
        if u.name == name:
            return u
    raise KeyError(f"敌方单位「{name}」不存在（viewer={viewer}）。")


def _damage(viewer, e, state) -> dict:
    """伤害：目标为敌方 → 绝对血量改成百分比（不泄漏绝对血量）；eff/stab 保留（系别放行）。"""
    if e["target"] in _foe_names(state, viewer):
        unit = _foe_unit_by_name(state, viewer, e["target"])
        out = dict(e)
        out["target_hp_pct"] = int(e.get("target_hp_left", 0)) * 100 // unit.max_hp
        out.pop("target_hp_left", None)
        return out
    return e


def _strip(e: dict, *keys: str) -> dict:
    return {k: v for k, v in e.items() if k not in keys}


def _heal(viewer, e, state) -> dict:
    """回复：目标为敌方 → 去掉数值（泄漏敌方 max_hp / 绝对血量），保留来源（技能名，已揭示）。"""
    if e["unit"] in _foe_names(state, viewer):
        return _strip(e, "applied", "overflow", "hp")
    return e


def _item_use(viewer, e, state) -> dict:
    """道具使用：敌方 → 去掉剩余次数（道具次数不在白名单）。"""
    if e["side"] != viewer:
        return _strip(e, "uses_left")
    return e


# 需要变换的事件类型 → 处理函数；未列入的已知类型（recharge/energy_gain/steal/reduce_arm/
# stat_change/switch/replace/faint/life_loss/skipped/battle_end/error）原样放行——
# 能量/命数/名字/技能数据/增减益层数（E4 修正：可见）都在白名单内。
_HANDLERS = {
    "damage": _damage,
    "heal": _heal,
    "item_use": _item_use,
}


def filter_events_for(viewer: str, events: list[dict], state) -> list[dict]:
    """按 `viewer` 过滤/变换一条事件流。输入：viewer / 引擎全量事件 / 任意快照 state（见各 handler
    ——只用单位名与 max_hp，二者整局不变，故对局结束后用终态过滤历史事件也正确）。输出：展示用事件。"""
    out: list[dict] = []
    for e in events:
        etype = e.get("type")
        if etype not in EVENT_TYPES:
            warnings.warn(f"E4 visibility：未登记事件类型「{etype}」已丢弃（fail-closed）。")
            continue
        handler = _HANDLERS.get(etype)
        out.append(handler(viewer, e, state) if handler else e)
    return out
