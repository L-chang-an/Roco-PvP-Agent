"""纯负面 buff（DOT/异常状态）结算 + 目录（2026-08-30 施工）。

六状态（battle_docs §F.2）：中毒/灼烧/寄生（mode="dot"，TURN_END 结算）、
冻结/引电/萌化（mode="special"，各自触发时机）。本批结算：**中毒/灼烧/寄生/引电**；
冻结/萌化层施加照常（编译器/天气已通），结算下批。

已拍板（2026-08-30）：
- **属性免疫**：火系免疫灼烧 / 草系免疫寄生 / 毒系免疫中毒——在**层数施加**处拦截
  （AddModifier reducer 漏斗统一跳过，不落层不发事件）；**中毒印记走 marks 路径不受影响**；
- **伤害克制**：灼烧（火）/中毒（毒）吃属性克制抵抗关系；**寄生不吃**（固定百分比
  真实伤害吸血，回复量 = 固定扣血量）；引电（电）同吃克制；
- **TURN_END 顺序**：DOT（中毒→灼烧→寄生）→ 印记 → 天气（收集序见 triggers.py）；
- 灼烧减半**向下取整**、归零移除；引电达 2 层立即 25% 最大生命电伤并扣 2 层（余层保留，
  链式触发由 pipeline fixpoint 自然终止）。

纯声明 + 纯函数：`collect` 产出 Atom 交 reducer 执行（**不改状态**）；`is_immune` /
`status_kwargs` 供 reducer / compiler 读取。伤害一律按**最大生命**百分比，出口 int() 一次。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .atom import Faint, HealFlat, LoseHp, SetModLayers

if TYPE_CHECKING:
    from .atom import Atom
    from .models import BattleState, Unit

# 状态名 → (mode, kwargs)。单一事实源：编译器 / 天气 / 结算都从这里取参数。
STATUS_TABLE: dict[str, tuple[str, dict]] = {
    "中毒": ("dot", {"pct": 3}),
    "灼烧": ("dot", {"pct": 2, "halve": True}),
    "寄生": ("dot", {"pct": 2}),
    "冻结": ("special", {"pct": 5}),
    "引电": ("special", {"pct": 25, "at": 2}),
    "萌化": ("special", {}),
}

# 属性免疫表：状态名 → 免疫系别（2026-08-30 拍板：火免疫灼烧/草免疫寄生/毒免疫中毒/
# 冰免疫冻结）。萌化无属性免疫（靠技能/特性效果清除）。
IMMUNE_TYPES: dict[str, str] = {"灼烧": "火", "寄生": "草", "中毒": "毒", "冻结": "冰"}

# 状态 → 伤害系别（DOT 伤害吃克制；"" = 真实伤害不吃克制）。
DAMAGE_TYPES: dict[str, str] = {"中毒": "毒", "灼烧": "火", "寄生": "", "引电": "电"}

# TURN_END 内 DOT 结算固定序（确定性）。
_TURN_END_ORDER: tuple[str, ...] = ("中毒", "灼烧", "寄生")


def status_kwargs(name: str) -> dict:
    """状态 kwargs（编译器填 AddModifier.kwargs 用）。"""
    return dict(STATUS_TABLE[name][1])


def is_immune(unit: "Unit", stat: str) -> bool:
    """属性免疫：火系免疫灼烧 / 草系免疫寄生 / 毒系免疫中毒（层数施加拦截）。"""
    immune_type = IMMUNE_TYPES.get(stat)
    return bool(immune_type and immune_type in unit.types)


def morph_layers(unit: "Unit") -> int:
    """萌化层数（0 = 无萌化）。首领化门控（萌化中不可首领化）读它。"""
    m = _find(unit, "萌化", "special")
    return m.layers if m is not None else 0


def _find(unit: "Unit", stat: str, mode: str):
    for m in unit.stat_mods:
        if m.stat == stat and m.mode == mode:
            return m
    return None


def _unit_by_id(state: "BattleState", unit_id: str) -> "Unit" | None:
    for s in ("a", "b"):
        for u in state.side(s).units:
            if u.id == unit_id:
                return u
    return None


def _freeze_layers(unit: "Unit") -> int:
    """冻结层数（mode="special" 记录；0 = 无冻结）。"""
    for m in unit.stat_mods:
        if m.stat == "冻结" and m.mode == "special":
            return m.layers
    return 0


def _freeze_faint_atom(state: "BattleState", unit: "Unit") -> "Atom | None":
    """冻结力竭判定（2026-08-30 拍板）：血量低于冻结层数×5% → 力竭阵亡。

    非伤害（Faint 原子），触发于**血量变化**（受击/DOT/印记）与**冻结层数变化**
    （施加/增加）。阈值用整数交叉相乘比较，避免 floor 除法 off-by-one。
    """
    if unit.fainted:
        return None
    layers = _freeze_layers(unit)
    if layers <= 0:
        return None
    pct = int(STATUS_TABLE["冻结"][1].get("pct", 5))
    if unit.current_hp * 100 < unit.max_hp * layers * pct:
        from .models import side_of

        return Faint(side=side_of(state, unit), unit=unit, source="冻结")
    return None


def collect(state: "BattleState", event) -> list["Atom"]:
    """事件 → DOT 结算原子。纯收集器（不改状态），triggers.collect_reactions 调用。

    - TurnEnded：双方在场（阵亡跳过）的 mode="dot" 记录，按 中毒→灼烧→寄生 固定序；
    - StatModChanged：引电达 at(2) 层 → 立即 25% 电伤 + 扣 2 层（余层保留；
      链式触发由 pipeline fixpoint 自然终止）；
    - 冻结力竭（2026-08-30）：DamageApplied / HpChanged / StatModChanged(冻结) →
      血量低于冻结层数×5% → Faint 原子。
    """
    if state is None:
        return []
    etype = type(event).__name__
    if etype == "TurnEnded":
        atoms: list["Atom"] = []
        for side in ("a", "b"):
            unit = state.active(side)
            if unit.fainted:
                continue
            foe_side = "b" if side == "a" else "a"
            for name in _TURN_END_ORDER:
                record = _find(unit, name, "dot")
                if record is None:
                    continue
                pct = int(record.kwargs.get("pct", 0))
                layers = record.layers
                atoms.append(LoseHp(side=side, unit=unit, pct=pct * layers,
                                    source=name, skill_type=DAMAGE_TYPES.get(name, "")))
                if name == "灼烧":
                    atoms.append(SetModLayers(unit=unit, stat=name, mode="dot",
                                              layers=layers // 2, source=name))
                elif name == "寄生":
                    amount = int(unit.max_hp * pct * layers / 100)
                    atoms.append(HealFlat(side=foe_side, unit=state.active(foe_side),
                                          amount=amount, source="寄生"))
        return atoms
    if etype == "DamageApplied" or etype == "HpChanged":
        uid = event.target_id if etype == "DamageApplied" else event.unit_id
        unit = _unit_by_id(state, uid)
        atom = _freeze_faint_atom(state, unit) if unit is not None else None
        return [atom] if atom is not None else []
    if etype == "StatModChanged" and event.stat == "冻结":
        unit = _unit_by_id(state, event.unit_id)
        atom = _freeze_faint_atom(state, unit) if unit is not None else None
        return [atom] if atom is not None else []
    if etype == "StatModChanged" and event.stat == "引电":
        at = int(STATUS_TABLE["引电"][1].get("at", 2))
        if event.total_layers < at:
            return []
        unit = _unit_by_id(state, event.unit_id)
        if unit is None or unit.fainted:
            return []
        from .models import side_of

        pct = int(STATUS_TABLE["引电"][1].get("pct", 25))
        return [LoseHp(side=side_of(state, unit), unit=unit, pct=pct,
                       source="引电", skill_type="电"),
                SetModLayers(unit=unit, stat="引电", mode="special",
                             layers=event.total_layers - at, source="引电")]
    return []
