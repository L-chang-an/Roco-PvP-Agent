"""动作空间与合法性：动作工厂 + `Decision` + 唯一的技能门控谓词。

`legal_actions` 与 `validate_decision` **只能**通过 `skill_block_reason` 判断技能
可用性，保证「合法池 / 谓词 / 校验」三处对「技能能不能用」的看法永远一致。
"""

from __future__ import annotations

from dataclasses import dataclass

from .evolution import boss_targets_of
from .models import ActionType
from .primitives import skill_energy_cost
from .rules import BOSS_EVOLUTION_ITEM, ITEMS
from .statuses import morph_layers
from .teambuilder import BOSS_BLOODLINE

_ACTION_TYPES: frozenset[str] = frozenset(a.value for a in ActionType)


def skill_action(index: int) -> dict:
    """一律用工厂函数，**不设模块级常量 dict**——共享可变对象被下游 mutate 就全局串味。"""
    return {"type": ActionType.SKILL.value, "value": index}


def switch_action(index: int) -> dict:
    """换人动作。输入：目标后备槽位下标（0-based）。输出：`{"type": "switch", "value": index}`。"""
    return {"type": ActionType.SWITCH.value, "value": index}


def recharge_action() -> dict:
    """聚能动作（回复能量）。无输入。输出：`{"type": "recharge"}`（恒合法，防死锁）。"""
    return {"type": ActionType.RECHARGE.value}


@dataclass(frozen=True)
class Decision:
    """一方一回合的完整提交：一个主动作 + 可选一个道具（附赠动作）。
    道具**不占用**主动作，两者同回合生效、各自入队。

    `item_arg`：道具参数（2026-08-30）——首领进化多分支（迪莫/魔力猫）时的分支
    精灵名；单分支可空（自动取唯一分支）。"""

    action: dict
    item: str = ""     # "" = 本回合不用道具
    item_arg: str = "" # 首领化分支精灵名（多分支时必填）


def boss_evolution_options(state, side: str) -> list[str]:
    """首领化分支列表（玩家 `item_arg` 从这里选）；空 = 当前不可首领化。

    已拍板（2026-08-30）：首领化 = 一阶进化，只有 boss 的上一阶可触发；萌化层数 > 0
    → 不可首领化（资质已退化）；多分支只有迪莫（4）/魔力猫（2）。
    **血脉门控（2026-08-30）**：血脉必须是「首领」（非系别血脉）才能首领化——
    组队时选「首领」血脉标记，选「光」等系别血脉则不能首领化。
    """
    unit = state.side(side).active_unit
    if unit.fainted or morph_layers(unit) > 0:
        return []
    if unit.bloodline != BOSS_BLOODLINE:
        return []
    return list(boss_targets_of(unit.name))


def skill_block_reason(state, side: str, unit, index) -> str | None:
    """唯一的技能门控谓词：可用 → None，否则中文原因。
    `legal_actions` 与 `validate_decision` **只能**通过它判断技能可用性。
    三道门：① 槽位越界（含非 int）② 冷却中（防御冷却 2026-08-30）③ 能量不足。
    将来的禁足 / 蓄力锁 / 号位锁全部只加进本函数。
    数据协议 v2：读 `current_skills`（当前生效视图，能耗/冷却以 current_skills 为准）；
    印记/天气批（2026-08-30）：能耗含湿润/蓄势印记与沙暴修正。"""
    if isinstance(index, bool) or not isinstance(index, int):
        return f"技能槽位必须是整数，实际 {index!r}。"
    if index < 0 or index >= len(unit.current_skills):
        return f"技能槽位 {index} 越界（该单位有 {len(unit.current_skills)} 个技能）。"
    skill = unit.current_skills[index]
    if skill.cooldown > 0:
        return f"技能「{skill.name}」冷却中（剩余 {skill.cooldown} 回合）。"
    cost = skill_energy_cost(state, side, unit, skill.energy_cost, skill)
    if unit.energy < cost:
        return f"能量不足（技能「{skill.name}」需 {cost}，现有 {unit.energy}）。"
    return None


def legal_actions(state, side: str) -> list[dict]:
    """**只含合法项**——不像参考项目那样把被门控的技能也塞进池子再靠 reason 字段标记
    （那样每个消费者都得记得过滤，而它自己的随机兜底就漏过了一次）。
    `recharge` 恒在池中 → 池永不为空 → 不会死锁。"""
    side_state = state.side(side)
    unit = side_state.active_unit
    actions: list[dict] = []
    for idx in range(len(unit.current_skills)):
        if skill_block_reason(state, side, unit, idx) is None:
            actions.append(skill_action(idx))
    for idx, bench in enumerate(side_state.units):
        if idx != side_state.active and not bench.fainted:
            actions.append(switch_action(idx))
    actions.append(recharge_action())
    return actions


def legal_items(state, side: str) -> list[str]:
    """剩余次数 > 0 的道具名。"""
    return [name for name, left in state.side(side).item_uses.items() if left > 0]


def replacement_options(state, side: str) -> list[int]:
    """阵亡后可选择的补位后备（存活、非当前在场）的槽位下标。"""
    side_state = state.side(side)
    return [i for i, u in enumerate(side_state.units)
            if i != side_state.active and not u.fainted]


def validate_replacement(state, side: str, bench_idx) -> str | None:
    """补位合法性：合法 → None，否则中文原因。调用方据此拒绝且**不消耗回合**。"""
    if state.done:
        return "对局已结束。"
    side_state = state.side(side)
    if isinstance(bench_idx, bool) or not isinstance(bench_idx, int):
        return f"补位槽位必须是整数，实际 {bench_idx!r}。"
    if not 0 <= bench_idx < len(side_state.units):
        return f"补位槽位 {bench_idx} 越界。"
    if bench_idx == side_state.active:
        return "不能补位到自己（已是当前在场）。"
    if side_state.units[bench_idx].fainted:
        return "目标精灵已倒下，不能补位。"
    return None


def validate_starter(state, side: str, bench_idx) -> str | None:
    """首发合法性（第 0 回合，2026-08-30）：合法 → None，否则中文原因。

    与补位类似，但首发无「当前在场」概念——任意存活单位都可选作首发。"""
    if state.done:
        return "对局已结束。"
    side_state = state.side(side)
    if isinstance(bench_idx, bool) or not isinstance(bench_idx, int):
        return f"首发槽位必须是整数，实际 {bench_idx!r}。"
    if not 0 <= bench_idx < len(side_state.units):
        return f"首发槽位 {bench_idx} 越界。"
    if side_state.units[bench_idx].fainted:
        return "目标精灵已倒下，不能首发。"
    return None


def validate_decision(state, side: str, dec: Decision) -> str | None:
    """合法 → None；非法 → 原因字符串（调用方据此拒绝且**不消耗回合**）。

    校验项：对局已结束 / 未知 action type / value 类型与范围 / 换人目标是自己或已倒下 /
    技能走 skill_block_reason / 道具名未知或次数已尽。
    """
    if state.done:
        return "对局已结束。"
    action = dec.action
    atype = action.get("type")
    if atype not in _ACTION_TYPES:
        return f"未知 action type「{atype}」。"
    side_state = state.side(side)
    if atype == ActionType.SKILL.value:
        reason = skill_block_reason(state, side, side_state.active_unit, action.get("value"))
        if reason is not None:
            return reason
    elif atype == ActionType.SWITCH.value:
        idx = action.get("value")
        if isinstance(idx, bool) or not isinstance(idx, int):
            return f"换人槽位必须是整数，实际 {idx!r}。"
        if idx < 0 or idx >= len(side_state.units):
            return f"换人槽位 {idx} 越界。"
        if idx == side_state.active:
            return "不能换人到自己（已是当前在场）。"
        if side_state.units[idx].fainted:
            return "目标精灵已倒下，不能换上场。"
    # RECHARGE 恒合法

    if dec.item:
        if dec.item not in ITEMS:
            return f"道具「{dec.item}」不存在。"
        if side_state.item_uses.get(dec.item, 0) <= 0:
            return f"道具「{dec.item}」次数已尽。"
        if dec.item == BOSS_EVOLUTION_ITEM:
            reason = _boss_item_reason(state, side, dec)
            if reason is not None:
                return reason
    return None


def _boss_item_reason(state, side: str, dec: Decision) -> str | None:
    """首领进化道具门控（2026-08-30 拍板）：合法 → None，否则中文原因。

    ① 场上精灵须是 boss 的上一阶（首领血脉，一阶进化）；
    ② 萌化层数 == 0（资质已退化则不再是 boss 上一阶）；
    ③ 多分支（迪莫/魔力猫）时 item_arg 必须给出合法分支；单分支可空。
    """
    unit = state.side(side).active_unit
    targets = boss_targets_of(unit.name)
    if not targets:
        return f"「{unit.name}」无首领血脉，无法使用首领进化。"
    if unit.bloodline != BOSS_BLOODLINE:
        return f"「{unit.name}」血脉不是「首领」，无法首领化。"
    if morph_layers(unit) > 0:
        return f"「{unit.name}」处于萌化状态，无法首领化。"
    if len(targets) > 1 and dec.item_arg not in targets:
        return f"首领化分支必须从 {list(targets)} 中选择。"
    return None
