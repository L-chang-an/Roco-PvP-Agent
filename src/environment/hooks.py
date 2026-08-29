"""Hook 时机与统一分发器：统一效果架构的「调度层」。

引擎在固定时机 emit(hook, ctx, sources)，分发器收集来源（特性/技能/印记）中
匹配该时机的绑定，条件通过后逐个执行效果原语。来源互不感知，只声明绑定。

**v3 骨架 Phase 3.1**：`emit` 是 `pipeline.run` 的兼容 shim——构造 SkillResolved →
collect_reactions（事件 → 新 Atom）→ reduce_all（Atom → 状态），特性效果不再由
Hook 直接改状态。对外行为与旧直写路径逐位等价（`tests/test_v3_sentinel.py` 把关）。
引擎主路径已直调 pipeline.run（engine.resolve_skill），emit 保留给测试与未来
其他 hook 的直调场景。

S1 只实现机制与三个钩子（SKILL_RESOLVE / STAT_CALC / EXIT），枚举预留全集——
后续特性/印记按需接入，不改分发器形状。
"""

from __future__ import annotations

from enum import Enum

from .effects import Effect
from .models import StatModifier


class Hook(str, Enum):
    """触发时机。S1 已实现机制的是 SKILL_RESOLVE / STAT_CALC / EXIT，其余预留给后续。"""

    # 事件钩子（副作用）
    TURN_START = "turn_start"
    TURN_END = "turn_end"
    ENTER = "enter"            # 精灵入场（补位/换人/返场/开战）
    EXIT = "exit"              # 精灵离场（换人/脱离）——清非永久增益
    SKILL_RESOLVE = "skill_resolve"   # 技能结算后（一次技能恰好一次，聚合全部命中）
    DEAL_DAMAGE = "deal_damage"
    TAKE_DAMAGE = "take_damage"
    KO = "ko"                  # 击杀
    COUNTER = "counter"        # 应对成功
    GAIN_BUFF = "gain_buff"    # 获得增益后

    # 读钩子（值变换，被动修正）
    STAT_CALC = "stat_calc"    # 属性计算
    SKILL_COST = "skill_cost"  # 技能能耗
    ATTACK_POWER = "attack_power"   # 攻击威力/伤害系数


# 条件注册表：绑定里的 cond 字符串 → 谓词。
# S1 只需「克制伤害」（迪莫）；S2 加三种「使用了某系技能」。
def _skill_type_is(ctx, t: str) -> bool:
    """ctx.skill 的技能系别 == t（SKILL_RESOLVE 上下文自带 skill）。"""
    return bool(getattr(getattr(ctx, "skill", None), "type", None) == t)


_CONDITIONS: dict[str, object] = {
    "": lambda ctx: True,
    "dealt_counter": lambda ctx: bool(getattr(ctx, "dealt_counter", False)),
    "used_fire": lambda ctx: _skill_type_is(ctx, "火"),
    "used_grass": lambda ctx: _skill_type_is(ctx, "草"),
    "used_water": lambda ctx: _skill_type_is(ctx, "水"),
}


def _cond_matches(cond: str, ctx) -> bool:
    fn = _CONDITIONS.get(cond)
    return bool(fn(ctx)) if callable(fn) else False


def emit(state, hook: str, ctx, trait_defs) -> None:
    """兼容 shim（v3 骨架 Phase 3.1）：委托 pipeline.run 的反应循环。

    - 构造 `SkillResolved` 事件（skill 名 / dealt_counter 从 ctx 取）；
    - `pipeline.run`：collect_reactions 按绑定条件返回新 Atom（TraitGain / 资源即时），
      reduce_all 执行（写 trait.gains，trait=True，免疫常规驱散）。

    对外行为与旧「直接改 trait.gains」逐位等价（哨兵把关）。`state` 可为 None（测试直调）。
    引擎主路径（engine.resolve_skill）已直调 pipeline.run，本 shim 保留给测试与直调场景。
    """
    if hook != Hook.SKILL_RESOLVE.value:
        return
    unit = getattr(ctx, "unit", None)
    if unit is None:
        return
    from .domain import SkillResolved
    from .pipeline import run
    from .reducer import Frame

    event = SkillResolved(unit_id=getattr(unit, "id", ""),
                          skill=getattr(getattr(ctx, "skill", None), "name", ""),
                          dealt_counter=bool(getattr(ctx, "dealt_counter", False)),
                          skill_type=getattr(getattr(ctx, "skill", None), "type", ""))
    run(state, [], Frame(), unit=unit, trait_defs=trait_defs,
        energy_max=getattr(ctx, "energy_max", 10), after=lambda f: [event])
    return None

