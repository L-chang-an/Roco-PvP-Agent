"""天气效果目录 + 收集器 + 读钩子数据（2026-08-30 施工）。

天气是**全局**（`BattleState.weather`，双方共享），`turns_left` 回合末递减、归零过期：
- 雨天：双方水系技能威力 +75%（`damage.build_damage_terms` 读 power_multiplier）；
- 沙暴：双方地系技能能耗减半（`primitives.skill_energy_cost` 读 cost_halved）；
- 暴风雪 / 雷鸣：TurnEnded → 双方在场精灵获得 冻结/引电 层（层数**结算**属下批 DOT
  系统，本批只施加）；
- 设置语义：覆盖设置、同种刷新（`set_weather`）；TURN_END 顺序 = 效果先结算再递减。

纯函数 + 声明；`collect` 由 triggers.collect_reactions 调用，产出 Atom 交 reducer。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .atom import AddModifier
from .models import WeatherState

if TYPE_CHECKING:
    from .atom import Atom
    from .models import BattleState

WEATHER_KINDS: tuple[str, ...] = ("雨天", "沙暴", "暴风雪", "雷鸣")

# 暴风雪/雷鸣 的 TURN_END 施加层（stat=纯负面中文名，mode=special，kwargs 携参数；
# 层数结算逻辑由未来 DOT 系统按 mode 分派）。
_TURN_END_LAYERS: dict[str, tuple[str, int, dict]] = {
    "暴风雪": ("冻结", 2, {"pct": 5}),
    "雷鸣": ("引电", 1, {"pct": 25, "at": 2}),
}


def power_multiplier(state: "BattleState", skill_type: str) -> float:
    """天气威力乘子（雨天：水系 ×1.75）。"""
    w = state.weather
    if w is not None and w.kind == "雨天" and skill_type == "水":
        return 1.75
    return 1.0


def cost_halved(state: "BattleState", skill_type: str) -> bool:
    """沙暴：地系技能能耗减半。"""
    w = state.weather
    return bool(w is not None and w.kind == "沙暴" and skill_type == "地")


def set_weather(state: "BattleState", kind: str, turns: int, source: str) -> None:
    """覆盖设置天气（同种刷新剩余回合）。"""
    state.weather = WeatherState(kind=kind, turns_left=turns, source=source)


def tick(state: "BattleState") -> None:
    """回合末递减；剩余 ≤0 → 天气过期（效果先结算再递减，调用方保证顺序）。"""
    if state.weather is None:
        return
    state.weather.turns_left -= 1
    if state.weather.turns_left <= 0:
        state.weather = None


def collect(state: "BattleState", event) -> list["Atom"]:
    """TurnEnded → 天气效果原子（暴风雪 +2 冻结 / 雷鸣 +1 引电，双方在场）。"""
    if state is None or state.weather is None or type(event).__name__ != "TurnEnded":
        return []
    kind = state.weather.kind
    if kind not in _TURN_END_LAYERS:
        return []
    stat, layers, kwargs = _TURN_END_LAYERS[kind]
    atoms: list["Atom"] = []
    for side in ("a", "b"):
        unit = state.active(side)
        if unit.fainted:
            continue
        atoms.append(AddModifier(side=side, unit=unit, stat=stat, mode="special",
                                 layers=layers, source=kind, kwargs=dict(kwargs)))
    return atoms
