"""Hook 时机与统一分发器：统一效果架构的「调度层」。

引擎在固定时机 emit(hook, ctx, sources)，分发器收集来源（特性/技能/印记）中
匹配该时机的绑定，条件通过后逐个执行效果原语。来源互不感知，只声明绑定。

S1 只实现机制与三个钩子（SKILL_RESOLVE / STAT_CALC / EXIT），枚举预留全集——
后续特性/印记按需接入，不改分发器形状。
"""

from __future__ import annotations

from enum import Enum

from .effects import Effect
from .primitives import apply_energy_cost_mod, apply_energy_gain, apply_stat_mod, heal_pct


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


def _apply_effect(state, ctx, effect: Effect, source: str) -> None:
    """把一条 Effect 分发到对应原语。target="self" 用 ctx.unit（特性绑定单位）。"""
    if effect.target != "self":
        raise ValueError(f"S1 只支持 target='self'，实际 {effect.target!r}（效果 {effect.op}）")
    unit = getattr(ctx, "unit", None)
    if unit is None:
        raise ValueError(f"ctx 缺少 unit（效果 {effect.op}）")
    if effect.op == "stat_mod":
        apply_stat_mod(unit, stat=effect.stat, mode=effect.mode, layers=effect.layers,
                       permanent=effect.permanent, trait=effect.trait, source=source)
    elif effect.op == "energy_gain":
        apply_energy_gain(unit, effect.value, energy_max=getattr(ctx, "energy_max", 10))
    elif effect.op == "energy_cost_mod":
        apply_energy_cost_mod(unit, layers=effect.layers, permanent=effect.permanent,
                              trait=effect.trait, source=source)
    elif effect.op == "heal_pct":
        heal_pct(state, unit, int(effect.value), source=source)
    else:
        raise ValueError(f"未知效果原语：{effect.op}")


def emit(state, hook: str, ctx, trait_defs) -> None:
    """在给定时机执行来源（特性）的全部命中绑定。

    S1 提供机制；S2 由引擎在关键点调用（resolve_skill 结算后 / 离场 / 属性计算）。
    trait_defs：本轮需要执行的特征静态定义列表（含 cond 匹配失败返回）。
    """
    for tdef in trait_defs:
        for binding in tdef.bindings:
            if binding.hook != hook or not _cond_matches(binding.cond, ctx):
                continue
            for effect in binding.effects:
                _apply_effect(state, ctx, effect, source=tdef.name)
