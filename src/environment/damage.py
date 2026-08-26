"""伤害与回复：全局唯一的三个口子，任何伤害/回复都不许绕过。

为什么走漏斗、哪怕现在只有一条公式和一个道具：将来的减伤链、「受致命伤害时保留
1 点生命」、吸血、on-damaged 钩子、前瞻预测（「若对手这招足以击败我」）全都挂在
这三个函数上。参考项目因为没有漏斗，后来只能「临时改攻击方属性 → 算 → 改回来」，
导致伤害路径不纯、前瞻无法复用。
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import aggregate_stats


@dataclass(frozen=True)
class HpLoss:
    """一次扣血的结算结果（apply_hp_loss 的输出）。

    字段：requested=请求扣血量；applied=实际扣血量（夹到 [0, current_hp] 后）；
    fainted=扣后是否阵亡。frozen：结果不可变，可安全进事件流。
    """

    requested: int
    applied: int
    fainted: bool


@dataclass(frozen=True)
class HealResult:
    """一次回复的结算结果（apply_heal 的输出）。

    字段：requested=请求回复量；applied=实际回复量（夹到 [0, max_hp − current_hp]）；
    overflow=被夹掉的量（将来「敌方回复时反伤含溢出」类效果要读它）。
    """

    requested: int
    applied: int
    overflow: int


def compute_damage(state, attacker, defender, skill, *,
                   counter_mult: float = 1.0, reduction: float = 0.0,
                   effectiveness: float = 1.0, stab: float = 1.0) -> int:
    """唯一伤害公式（纯函数：零副作用、零 RNG、零事件）。

        raw = (atk / def) × power × rules.damage_coefficient × counter_mult × (1 − reduction)
              × effectiveness × stab
        dmg = 0 if power <= 0 else max(rules.min_damage, int(raw))

    物攻读 aggregate_stats 的 atk/def，魔攻读 sp_atk/sp_def；atk/def 下限 1。
    **全程 float，只在出口 int() 一次**——参考项目 int() 截断与 int(round()) 混用，
    而且减伤是第二次取整，于是「改一个乘子」会在两处产生不同的舍入。
    应对乘子 / 减伤 / 克制倍率 / STAB 都是**显式入参**，不读 TurnContext 或
    state.types——这样它是可单测的纯函数，将来接修饰链也不用改调用方形状。
    """
    if skill.power <= 0:
        return 0
    physical = skill.kind == "物攻"
    atk_key = "atk" if physical else "sp_atk"
    def_key = "def" if physical else "sp_def"
    atk = max(1, aggregate_stats(attacker, state.rules)[atk_key])
    defense = max(1, aggregate_stats(defender, state.rules)[def_key])
    raw = (atk / defense) * skill.power * state.rules.damage_coefficient * counter_mult \
        * (1 - reduction) * effectiveness * stab
    return max(state.rules.min_damage, int(raw))


def apply_hp_loss(state, target, amount: int, *, source: str) -> HpLoss:
    """唯一扣血入口：本函数是 `current_hp` / `fainted` 的唯一写者之一。
    amount 夹到 [0, current_hp]；归零置 fainted=True。不发事件（事件由 engine 发）。"""
    amount = max(0, int(amount))
    applied = min(amount, target.current_hp)
    target.current_hp -= applied
    fainted = target.fainted or target.current_hp == 0
    target.fainted = fainted
    return HpLoss(requested=amount, applied=applied, fainted=fainted)


def apply_heal(state, target, amount: int, *, source: str) -> HealResult:
    """唯一回复入口。amount 夹到 [0, max_hp − current_hp]；`overflow` 记录被夹掉的量
    （将来「若敌方本回合回复生命，改为失去 2 倍含溢出量」这类效果要读夹取前的值）。
    **已阵亡单位不可回复**（applied=0）——E0 没有复活语义。"""
    amount = max(0, int(amount))
    if target.fainted:
        return HealResult(requested=amount, applied=0, overflow=amount)
    applied = min(amount, target.max_hp - target.current_hp)
    target.current_hp += applied
    return HealResult(requested=amount, applied=applied, overflow=amount - applied)
