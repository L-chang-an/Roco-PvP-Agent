"""DomainEvent（v3 骨架·事件层）：引擎已发生的事实（Trigger 的输入），与展示事件分开。

v3 核心区分（`mydocs/battle_docs.md` §2.4）：
- **DomainEvent**：内部事实，触发 Trigger 的输入（`DamageApplied`、`SkillResolved`…）。
- **展示事件**：对外（玩家/前端）的 `EVENT_TYPES` dict（damage/heal/stat_change…），
  由 Reducer 从状态修改直接产出（形状与旧引擎逐位一致，保证 digest/事件签名不变）。

骨架阶段 Reducer 同时产出两者；Trigger 只消费 DomainEvent、返回新 Atom（不直接改状态）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DamageApplied:
    """一次伤害已施加（一个连击段）。`total` 是累计总伤害（供吸血等读取）。"""

    source_id: str
    target_id: str
    amount: int
    effectiveness: float
    skill: str
    total: int
    skill_type: str = ""   # 技能系别（星陨「非幻系」判定读它）


@dataclass(frozen=True)
class EnergyChanged:
    """能量变化（回复/偷取/聚能等）。"""

    unit_id: str
    before: int
    after: int
    source: str


@dataclass(frozen=True)
class HpChanged:
    """血量变化（回复/吸血等）。"""

    unit_id: str
    before: int
    after: int
    source: str


@dataclass(frozen=True)
class StatModChanged:
    """一条增减益层已应用（属性 / 连击 / 吸血 / 能耗）。"""

    unit_id: str
    stat: str
    mode: str
    layers: int
    total_layers: int
    source: str


@dataclass(frozen=True)
class SkillResolved:
    """一次技能结算完成（攻击/防御/状态三支 + 资源效果之后）。Trigger 据此触发特性。

    `countered`（2026-08-30）：本技能应对命中（防御应对攻击等，守望者特性读它）。
    """

    unit_id: str
    skill: str
    dealt_counter: bool
    skill_type: str = ""   # 技能系别（Trigger 条件 used_fire/grass/water 读它；兼容测试直调无 name 场景）
    countered: bool = False


@dataclass(frozen=True)
class UnitEntered:
    """精灵入场（换人 / 补位 / 开局 / 首领化）。`from_faint` = 补位入场（前一在场者
    阵亡）；`from_boss` = 首领化原地进化入场（2026-08-30，触发入场类特性/印记）。"""

    unit_id: str
    from_faint: bool = False
    from_boss: bool = False


@dataclass(frozen=True)
class UnitExited:
    """精灵离场（换人 / 脱离）。`incoming_id` = 换人时立即入场的精灵（暗涌印记读它；
    阵亡离场无 incoming → ""，由后续补位的 UnitEntered(from_faint=True) 承接）。"""

    unit_id: str
    incoming_id: str = ""


@dataclass(frozen=True)
class TurnEnded:
    """回合结束（end_of_turn 开始）。DOT/天气/印记的回合末结算以此为触发点。"""

    turn: int


@dataclass(frozen=True)
class TurnStarted:
    """回合开始（resolve_turn 入口，2026-08-30）。

    `predictions`：双方在场精灵的确定性预估（prediction.predictions_for，逐技能
    预估威力/预估伤害）——供「回合开始就通过预估伤害触发」的特性绑定读取。
    预估是派生量，随事件携带（回合内局部），不入 BattleState。
    """

    turn: int
    predictions: dict[str, list[dict]]


@dataclass(frozen=True)
class MarkChanged:
    """印记层数变化（施加/消耗/移除）。`layers` = 变更后总层数（0 = 已移除）。"""

    side: str
    name: str
    layers: int
    source: str


@dataclass(frozen=True)
class WeatherChanged:
    """天气设置/变化（全局）。"""

    kind: str
    turns_left: int
    source: str
