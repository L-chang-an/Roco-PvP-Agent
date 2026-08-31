"""伤害与回复：全局唯一的三个口子，任何伤害/回复都不许绕过。

为什么走漏斗、哪怕现在只有一条公式和一个道具：将来的减伤链、「受致命伤害时保留
1 点生命」、吸血、on-damaged 钩子、前瞻预测（「若对手这招足以击败我」）全都挂在
这三个函数上。参考项目因为没有漏斗，后来只能「临时改攻击方属性 → 算 → 改回来」，
导致伤害路径不纯、前瞻无法复用。

**2026-08-30 伤害公式规范（负责人拍板，唯一事实源）**：`formula()` 是全部伤害的
唯一计算公式（`modifiers.compute` 与 `compute_damage` 都委托它）。每段伤害：

```
(atk/def) × coefficient × [基础威力×应对倍率 + 威力绝对值加成] × 比值项 × 威力百分比
× STAB × 克制 × 天气 × (1 − 减伤)
```

- atk/def = 六维基线 + flat 层（±10×层，下限 1）；pct 层**不进属性**、进比值项；
- 比值项 = (1 + 0.1×(我方atk增益pct + 敌方def减益pct)) / (1 + 0.1×(我方atk减益pct + 敌方def增益pct))，
  四分量各自夹 `stat_layer_cap`；
- 威力绝对值/百分比加成 = `attack_power` 的 flat（+10×层）/ pct（+10%×层）——本次激活；
- 天气项现恒 1.0（天气效果未实现，落位已定）；
- **逐段结算**：连击数不进公式，每段独立 `int()`（事件流 per-hit，总伤害 = Σ段）；
- `power_term ≤ 0 → 0`；`min_damage` 保底；顺序求值、出口 `int()` 一次。
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import buff_layers


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


@dataclass(frozen=True)
class DamageTerms:
    """一次伤害计算的输入项（公式规范 2026-08-30）：`build_damage_terms` 的产物。

    纯数据、不写状态；预估体系（prediction.py）与真实伤害共用同一份项计算。
    `weather`（2026-08-30 印记/天气批）：天气威力乘子（雨天水系 ×1.75，否则 1.0）。
    """

    atk: int             # 攻击侧六维（基线 + flat 层，下限 1）
    defense: int         # 防御侧六维（基线 + flat 层，下限 1）
    power_term: float    # 基础威力 × 应对倍率 + attack_power flat 层 ×10 + 蓄电印记
    ratio_num: float     # 1 + 0.1×(我方atk增益pct + 敌方def减益pct)
    ratio_den: float     # 1 + 0.1×(我方atk减益pct + 敌方def增益pct)
    power_pct: float     # 1 + 0.1×attack_power pct 层 + 印记威力修正
    weather: float = 1.0     # 天气威力乘子（雨天水系 1.75）


def _clamp(layers: float, cap: int) -> float:
    """层数夹到 [−cap, cap]（沿用既有 stat_layer_cap 纪律）。"""
    return min(max(layers, -cap), cap)


def _pct_parts(unit, stat: str) -> tuple[float, float]:
    """某 stat 的 pct 层正/负拆分（增益和, 减益绝对值），各夹 cap。

    读取 stat_mods + trait.gains 两处（特性增益并入，buff_layers 同源）。
    """
    pos = neg = 0.0
    for m in list(unit.stat_mods) + [g for g in (unit.trait.gains if unit.trait else [])]:
        if m.stat == stat and m.mode == "pct":
            if m.layers > 0:
                pos += m.layers
            else:
                neg -= m.layers
    return pos, neg


def build_damage_terms(state, attacker, defender, *, damage_kind: str,
                       power: int, counter_mult: float, side: str = "",
                       skill_type: str = "", acted_first: bool = False) -> DamageTerms:
    """物/魔选边 → 计算 DamageTerms（公式规范 2026-08-30 + 印记/天气批）。

    - atk/def = 六维基线 + flat 层（±10×层），下限 1；
    - pct 层四分量（我方atk增益/减益、敌方def增益/减益）各自夹 cap；
    - attack_power 的 flat（+10×层）进威力绝对值、pct（+10%×层）进威力百分比；
    - **印记**（side 非空时读攻击方阵营）：攻击/蓄势/风起 → power_pct；蓄电 → power_term；
    - **天气**：雨天水系 ×1.75（weather 项）。
    """
    from .marks import power_flat_bonus, power_pct_bonus
    from .weather import power_multiplier

    rules = state.rules
    physical = damage_kind == "物攻"
    atk_key = "atk" if physical else "sp_atk"
    def_key = "def" if physical else "sp_def"

    atk = attacker.stats[atk_key] + rules.stat_flat_per_layer \
        * _clamp(buff_layers(attacker, atk_key, "flat"), rules.stat_layer_cap)
    defense = defender.stats[def_key] + rules.stat_flat_per_layer \
        * _clamp(buff_layers(defender, def_key, "flat"), rules.stat_layer_cap)

    a_pos, a_neg = _pct_parts(attacker, atk_key)
    d_pos, d_neg = _pct_parts(defender, def_key)
    cap = rules.stat_layer_cap
    ratio_num = 1 + rules.stat_pct_per_layer * (min(a_pos, cap) + min(d_neg, cap))
    ratio_den = 1 + rules.stat_pct_per_layer * (min(a_neg, cap) + min(d_pos, cap))

    power_flat = _clamp(buff_layers(attacker, "attack_power", "flat"), cap)
    power_pct = _clamp(buff_layers(attacker, "attack_power", "pct"), cap)
    side_state = state.side(side) if side else None
    mark_pct = power_pct_bonus(side_state, acted_first=acted_first) if side_state else 0.0
    mark_flat = power_flat_bonus(side_state) if side_state else 0
    # 冻土特性（2026-08-30）：每携带 1 个冰系技能，地系技能威力 +10%
    if skill_type == "地" and attacker.trait is not None \
            and attacker.trait.name == "冻土":
        mark_pct += 0.10 * sum(1 for s in attacker.skills if s.type == "冰")
    # 冰钻特性（2026-08-30）：敌方携带技能总能耗每有 1 点，自己攻击威力 +10%
    if attacker.trait is not None and attacker.trait.name == "冰钻":
        mark_pct += 0.10 * sum(s.energy_cost for s in defender.skills)
    # 冰雪魂魄特性（2026-08-30）：天气为暴风雪时，敌方队伍每有 1 层冻结，冰系威力 +10%
    if skill_type == "冰" and attacker.trait is not None \
            and attacker.trait.name == "冰雪魂魄" and state.weather is not None \
            and state.weather.kind == "暴风雪":
        from .models import side_of
        from .statuses import freeze_layers

        foe_side = side_of(state, defender)
        mark_pct += 0.10 * sum(freeze_layers(u) for u in state.side(foe_side).units)
    return DamageTerms(
        atk=max(1, int(atk)),
        defense=max(1, int(defense)),
        power_term=power * counter_mult + rules.stat_flat_per_layer * power_flat + mark_flat,
        ratio_num=ratio_num,
        ratio_den=ratio_den,
        power_pct=1 + rules.stat_pct_per_layer * power_pct + mark_pct,
        weather=power_multiplier(state, skill_type),
    )


def formula(*, atk: int, defense: int, power_term: float,
            ratio_num: float, ratio_den: float, power_pct: float,
            stab: float, effectiveness: float, weather: float,
            reduction: float, coefficient: float, min_damage: int) -> int:
    """唯一伤害公式（2026-08-30 拍板顺序）：**顺序求值、出口 int() 一次**。

    `power_term <= 0 → 0`（不享受 min_damage 保底，与旧语义一致）；其余
    `max(min_damage, int(raw))`。逐段结算：连击数不进公式（每段独立调用）。
    """
    if power_term <= 0:
        return 0
    raw = (atk / defense) * coefficient * power_term * (ratio_num / ratio_den) \
        * power_pct * stab * effectiveness * weather * (1 - reduction)
    return max(min_damage, int(raw))


def compute_damage(state, attacker, defender, skill, *,
                   counter_mult: float = 1.0, reduction: float = 0.0,
                   effectiveness: float = 1.0, stab: float = 1.0) -> int:
    """唯一伤害公式的兼容入口（签名不变）：委托 `build_damage_terms` + `formula`。

    纯函数：零副作用、零 RNG、零事件。应对乘子 / 减伤 / 克制倍率 / STAB 都是
    **显式入参**，不读 TurnContext——可单测、可被预估复用（预估侧 counter_mult=1.0）。
    印记/天气修正从 state 派生（side 由 `models.side_of` 反查；acted_first=False，
    无回合上下文）。
    """
    from .models import side_of

    terms = build_damage_terms(state, attacker, defender, damage_kind=skill.kind,
                               power=skill.power, counter_mult=counter_mult,
                               side=side_of(state, attacker), skill_type=skill.type,
                               acted_first=False)
    return formula(atk=terms.atk, defense=terms.defense, power_term=terms.power_term,
                   ratio_num=terms.ratio_num, ratio_den=terms.ratio_den,
                   power_pct=terms.power_pct, stab=stab, effectiveness=effectiveness,
                   weather=terms.weather, reduction=reduction,
                   coefficient=state.rules.damage_coefficient,
                   min_damage=state.rules.min_damage)


def apply_hp_loss(state, target, amount: int, *, source: str) -> HpLoss:
    """唯一扣血入口：本函数是 `current_hp` / `fainted` 的唯一写者之一。
    amount 夹到 [0, current_hp]；归零置 fainted=True。不发事件（事件由 engine 发）。"""
    amount = max(0, int(amount))
    applied = min(amount, target.current_hp)
    target.current_hp -= applied
    fainted = target.fainted or target.current_hp == 0
    target.fainted = fainted
    return HpLoss(requested=amount, applied=applied, fainted=fainted)


def apply_faint(state, target, *, source: str) -> HpLoss:
    """冻结力竭（非伤害）：置 current_hp=0 + fainted=True。

    与 apply_hp_loss 同址（`current_hp` 唯一写点纪律）：力竭是「状态判定」而非
    「伤害」，不触发受击类效果，但同样集中于此——reducer 绝不直接改 current_hp。
    """
    applied = target.current_hp
    target.current_hp = 0
    target.fainted = True
    return HpLoss(requested=applied, applied=applied, fainted=True)


def apply_max_hp_change(state, target, new_max: int, *,
                        source: str) -> HpLoss | HealResult | None:
    """萌化/首领化资质变化的血量调整：设置 max_hp，current_hp 同比例缩放取整（下限 1）。

    与 apply_hp_loss/apply_heal 同址（`current_hp`/`max_hp` 唯一写点纪律）：缩放后的
    HP 增减经扣血/回血漏斗执行；**阵亡单位保持 0 不缩放**。
    """
    old_max = max(1, target.max_hp)
    target.max_hp = max(1, int(new_max))
    if target.fainted:
        return None
    want = max(1, int(target.max_hp * (target.current_hp / old_max)))
    if want < target.current_hp:
        return apply_hp_loss(state, target, target.current_hp - want, source=source)
    if want > target.current_hp:
        return apply_heal(state, target, want - target.current_hp, source=source)
    return None


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
