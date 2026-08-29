"""E4 迷雾观测：按 viewer 屏蔽信息。引擎持有完整状态推进，给玩家的展示只给白名单。

viewer 的**己方全见**；敌方只可见白名单（负责人 2026-08-25，含 E4 修正）：
- 精灵名 / **系别**（负责人拍板放行——公开图鉴数据，精灵名已可见）；
- **血量百分比**（`hp_pct`，不是绝对血量）；
- **能量值**；
- 敌方**剩余命数**；
- 敌方每只精灵的**特性描述**（name + desc，图鉴公开数据）；
- **已揭示技能**（起始全未知；某精灵释放某技能后才揭示该技能详情，含 desc）；
- **增减益层数**（E4 修正：双方阵营都有状态栏——常规增减益层数 / 特性层数 / 能耗减益，
  `stat_mods` / `energy_cost_mods` 对敌方也输出；印记将来加）。

其余（六维、性格、血脉、IV、绝对血量、道具次数、未揭示技能）一律不进敌方视图。

`mode="partial"` 是玩家唯一能看到的口径；`mode="global"` = `state.to_dict()`（引擎回放/CLI
全量输出用）。本模块是**纯函数**：不写状态、不揭示、不发事件——揭示发生在 engine.resolve_skill。
"""

from __future__ import annotations

from .dataset import DataSource, load_spirits
from .models import BattleState


def _hp_pct(unit) -> int:
    """血量百分比：`current*100//max`，整数、确定性（0–100）。"""
    return unit.current_hp * 100 // unit.max_hp


def _skill_dict(skill) -> dict:
    """Skill → 前端完整技能卡片（详情含 desc——「技能详情描述」是揭示的内容）。"""
    return {
        "name": skill.name,
        "type": skill.type,
        "kind": skill.kind,
        "power": skill.power,
        "energy_cost": skill.energy_cost,
        "desc": skill.desc,
        "priority": skill.priority,
    }


def _mod_dict(m) -> dict:
    return {"stat": m.stat, "mode": m.mode, "layers": m.layers,
            "permanent": m.permanent, "source": m.source, "trait": m.trait}


def _ecm_dict(m) -> dict:
    return {"layers": m.layers, "permanent": m.permanent, "trait": m.trait, "source": m.source}


def _unit_full(u) -> dict:
    """己方单位：全量（六维/技能详情/增益层/性格/血脉/IV/绝对血量）。"""
    return {
        "name": u.name,
        "types": list(u.types),
        "stats": dict(u.stats),
        "skills": [_skill_dict(s) for s in u.skills],
        "nature": u.nature,
        "bloodline": u.bloodline,
        "iv": dict(u.iv),
        "max_hp": u.max_hp,
        "current_hp": u.current_hp,
        "energy": u.energy,
        "fainted": u.fainted,
        "stat_mods": [_mod_dict(m) for m in u.stat_mods],
        "energy_cost_mods": [_ecm_dict(m) for m in u.energy_cost_mods],
        "trait": {"name": u.trait.name, "used_once": u.trait.used_once} if u.trait else None,
    }


def _trait_info(u) -> dict:
    """敌方特性描述：图鉴公开数据（真实特性名 + 描述），按精灵名查表。

    Unit.trait 是**已装备**特性（未实现 → 白板 default），而「特性描述」是图鉴数据；
    敌人名字可见 → 特性可查，故直接给图鉴的真实特性名 + 描述。
    """
    for source in (DataSource.FULL, DataSource.VALID):
        sp = load_spirits(source).get(u.name)
        if sp is not None and sp.trait_name:
            return {"name": sp.trait_name, "desc": sp.trait_desc}
    return {"name": u.trait.name if u.trait else "", "desc": ""}


def _unit_masked(u, revealed: set[tuple[int, str]], index: int) -> dict:
    """敌方单位：白名单字段。技能 = 已揭示的（详情）；未揭示的不出现（UI 显示 ？？？）。

    E4 修正（负责人 2026-08-25）：**增减益可见**——双方阵营都显示状态栏（常规增减益层数 /
    特性层数 / 能耗减益；印记将来加），故 stat_mods / energy_cost_mods 对敌方也输出。
    其余（六维 / 性格 / 血脉 / IV / 绝对血量 / 道具次数）仍隐藏。
    """
    return {
        "name": u.name,
        "types": list(u.types),
        "hp_pct": _hp_pct(u),
        "energy": u.energy,
        "fainted": u.fainted,
        "trait": _trait_info(u),
        "skills": [_skill_dict(s) for s in u.skills if (index, s.name) in revealed],
        "stat_mods": [_mod_dict(m) for m in u.stat_mods],
        "energy_cost_mods": [_ecm_dict(m) for m in u.energy_cost_mods],
    }


def _side_view(side_state, *, masked: bool) -> dict:
    """一方视图：masked=False → 全量（己方）；masked=True → 白名单（敌方）。"""
    units = [u for u in side_state.units]
    if masked:
        revealed = side_state.revealed
        return {
            "lives": side_state.lives,
            "active": side_state.active,
            "units": [_unit_masked(u, revealed, i) for i, u in enumerate(units)],
        }
    return {
        "lives": side_state.lives,
        "active": side_state.active,
        "item_uses": dict(side_state.item_uses),
        "units": [_unit_full(u) for u in units],
    }


def observe(state: BattleState, viewer: str, mode: str = "partial") -> dict:
    """E4 观测：`viewer`（"a"/"b"）视角的屏蔽视图。

    - `mode="partial"`：`me`（己方全量）+ `opponent`（白名单屏蔽）；
    - `mode="global"`：`state.to_dict()`（引擎回放/全量输出）。
    viewer 非法 → ValueError（调用方传错立即暴露，不做静默回退）。
    """
    if mode != "partial":
        return state.to_dict()
    if viewer not in ("a", "b"):
        raise ValueError(f"viewer 必须是 a 或 b，实际 {viewer!r}。")
    foe = "b" if viewer == "a" else "a"
    r = state.rules
    return {
        "battle_id": state.battle_id,
        "side": viewer,
        "turn": state.turn,
        "winner": state.winner,
        "done": state.done,
        "rules": {
            "team_size": r.team_size,
            "lives": r.lives,
            "max_turns": r.max_turns,
            "energy_max": r.energy_max,
            "energy_start": r.energy_start,
            "recharge_amount": r.recharge_amount,
            "skill_slots": r.skill_slots,
            "iv_max": r.iv_max,
        },
        "me": _side_view(state.side(viewer), masked=False),
        "opponent": _side_view(state.side(foe), masked=True),
    }
