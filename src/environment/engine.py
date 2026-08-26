"""回合循环：一个函数拆成三段，一条清理路径，一个局部上下文。

核心不变式（回合制的本质）：`resolve_turn` 的输出**只**由 `(state.to_dict(),
decision_a, decision_b)` 决定；阵亡后的补位由阵亡方玩家选择（交互式补位，判断 8）。
回合内派生量（谁应对了谁、减伤多少、出手顺序）一律住在本文件的局部 `TurnContext`，
**绝不落进 BattleState**——跨回合泄漏在结构上不可能发生。

四条不变式（写代码时反复回头对照）：
1. 回合号推进与 `battle_end` 发射只发生在 `end_turn` 一处（整个代码库唯一 `turn += 1`）。
2. **阵亡 → 回合立即结束**：`resolve_turn` 在第一个阵亡处暂停，剩余队列条目不再结算；
   该方有存活后备 → 等玩家补位；无存活 / 命归零 → 直接终局。
3. 终局回合也走统一收尾（end_turn），回合号照常推进。
4. `state` 里不允许出现任何回合内字段。
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from .actions import Decision
from .damage import apply_heal, apply_hp_loss, compute_damage
from .events import ev
from .hooks import Hook, emit
from .models import ActionType, BattleState, SIDES, Skill, StatModifier, Unit, aggregate_stats
from .primitives import (
    apply_energy_cost_mod, apply_energy_gain, combo_bonus, heal_pct, lifesteal_bonus,
    skill_energy_cost,
)
from .skillbook import SkillCategory, SkillStatEffect
from .traits import trait_defs_for
from .types import stab_multiplier, type_effectiveness


@dataclass(frozen=True)
class TurnContext:
    """本回合的全部派生量。**是局部对象，不是 BattleState 的字段。**

    这是核心不变式的关键：回合内临时量一旦住进状态，就必须记得清理（参考项目的
    `_defense_skill_a` 正是如此，`end_of_turn_cleanup` 手工清 8 个字段）。做成局部
    对象，`end_of_turn()` 函数体是 pass 而且理应如此。
    """

    decision_a: Decision
    decision_b: Decision
    skill_a: Skill | None = None   # 本方声明的技能（换人/聚能 → None）
    skill_b: Skill | None = None
    category_a: SkillCategory | None = None
    category_b: SkillCategory | None = None
    reduction_a: float = 0.0       # 已武装的减伤比例（未武装 = 0.0）
    reduction_b: float = 0.0

    def decision(self, side: str) -> Decision:
        """输入：side（"a"/"b"）。输出：该方本回合的 Decision（换人/聚能时同样返回）。"""
        return self.decision_a if side == "a" else self.decision_b

    def skill(self, side: str):
        """本方声明的技能（换人/聚能 → None）。"""
        return self.skill_a if side == "a" else self.skill_b

    def category(self, side: str) -> SkillCategory | None:
        """本方声明技能的类别（攻击/防御/状态；换人/聚能 → None）。应对判定读它。"""
        return self.category_a if side == "a" else self.category_b

    def reduction(self, side: str) -> float:
        """本方已武装的减伤比例（防御技应对攻击命中才非 0；否则 0.0）。"""
        return self.reduction_a if side == "a" else self.reduction_b

    def counters(self, side: str) -> bool:
        """本方技能的 counter_vs 是否命中对手本回合声明的类别。"""
        skill = self.skill(side)
        if skill is None or skill.effect.counter_vs is None:
            return False
        foe_cat = self.category("b" if side == "a" else "a")
        return skill.effect.counter_vs == foe_cat


@dataclass(frozen=True)
class QueuedEntry:
    """回合队列里的一条待结算动作。

    字段：side=归属方；kind="item"|"main"（道具/主动作）；actor=**入队时**的在场单位
    （执行前若已阵亡则整条跳过）；priority=优先级（技能先手修正/换人·道具 99）；
    speed=入队时的速度（聚合 stat_mods 后），排序用。
    """

    side: str
    kind: str            # "item" | "main"
    actor: Unit          # **入队时**的在场单位；执行前若已阵亡则整条跳过
    priority: int
    speed: int           # 入队时 aggregate_stats(actor)["speed"]


def _declared_skill(state, side: str, dec: Decision):
    """读出该方声明的技能（非 skill 动作 / 槽位非法 → None）。防御性：直接调用
    execute_turn 时不会因非法槽位崩溃。"""
    if dec.action.get("type") != ActionType.SKILL.value:
        return None
    idx = dec.action.get("value")
    if isinstance(idx, bool) or not isinstance(idx, int):
        return None
    unit = state.active(side)
    if idx < 0 or idx >= len(unit.skills):
        return None
    return unit.skills[idx]


def build_turn_context(state, dec_a: Decision, dec_b: Decision) -> tuple[TurnContext, list[dict]]:
    """① 读出双方声明的技能类别；② 为防御方武装减伤（**仅当对手声明了攻击**，
    且能量足以支付——付不起在 E0 压根就是非法动作，所以这里天然成立）。
    返回 ctx 与 `reduce_arm` 事件列表。"""
    skill_a = _declared_skill(state, "a", dec_a)
    skill_b = _declared_skill(state, "b", dec_b)
    cat_a = skill_a.effect.category if skill_a else None
    cat_b = skill_b.effect.category if skill_b else None

    events: list[dict] = []
    red_a = red_b = 0.0
    if skill_a is not None and skill_a.effect.category == SkillCategory.DEFENSE:
        armed = cat_b == SkillCategory.ATTACK
        if armed:
            red_a = skill_a.effect.reduction_pct
        events.append(ev("reduce_arm", "a", skill=skill_a.name,
                         pct=skill_a.effect.reduction_pct, armed=armed))
    if skill_b is not None and skill_b.effect.category == SkillCategory.DEFENSE:
        armed = cat_a == SkillCategory.ATTACK
        if armed:
            red_b = skill_b.effect.reduction_pct
        events.append(ev("reduce_arm", "b", skill=skill_b.name,
                         pct=skill_b.effect.reduction_pct, armed=armed))

    ctx = TurnContext(decision_a=dec_a, decision_b=dec_b,
                      skill_a=skill_a, skill_b=skill_b,
                      category_a=cat_a, category_b=cat_b,
                      reduction_a=red_a, reduction_b=red_b)
    return ctx, events


def entry_priority(state, ctx: TurnContext, side: str, kind: str) -> int:
    """道具 = rules.item_priority(99)；换人 = rules.switch_priority(99)；
    技能 = skill.priority（E0 全为 0）；聚能 = 0。"""
    if kind == "item":
        return state.rules.item_priority
    dec = ctx.decision(side)
    atype = dec.action.get("type")
    if atype == ActionType.SWITCH.value:
        return state.rules.switch_priority
    if atype == ActionType.SKILL.value:
        skill = ctx.skill(side)
        return skill.priority if skill else 0
    return 0   # recharge


def _order_group(group: list[QueuedEntry], state) -> list[QueuedEntry]:
    """平手组内排序：同方道具先于主动作；跨方仍完全相同 → rng.choice 抛硬币。

    组内每个 (方, 下一条) 的决策都是真实的跨方平手，硬币每枚都该被抽——这就是
    `rng.calls` 的计数来源。速度互异的阵容永远不会进这里，于是 calls == 0。
    """
    a = sorted((e for e in group if e.side == "a"), key=lambda e: 0 if e.kind == "item" else 1)
    b = sorted((e for e in group if e.side == "b"), key=lambda e: 0 if e.kind == "item" else 1)
    out: list[QueuedEntry] = []
    ia = ib = 0
    while ia < len(a) or ib < len(b):
        if ia >= len(a):
            out.append(b[ib]); ib += 1
            continue
        if ib >= len(b):
            out.append(a[ia]); ia += 1
            continue
        first = state.rng.choice([a[ia], b[ib]])
        if first is a[ia]:
            out.append(a[ia]); ia += 1
        else:
            out.append(b[ib]); ib += 1
    return out


def build_queue(state, ctx: TurnContext) -> list[QueuedEntry]:
    """最多 4 条：双方各「道具」+「主动作」。

    排序键，依次比较：
        ① priority 降序
        ② 在场精灵速度降序（读 aggregate_stats，本回合之前叠的速度层算数）
        ③ 同方同优先级：道具先于主动作（rules.item_before_main_action）
        ④ 跨方仍完全相同：state.rng.choice 抛硬币（50%）

    第 ④ 步是 E0b **唯一**的 RNG 抽取点。注意 rng.calls 因此**不等于**回合数——
    只有跨方平手才抽，用速度互异的阵容跑完整局应当 calls == 0。
    """
    entries: list[QueuedEntry] = []
    for side in SIDES:
        dec = ctx.decision(side)
        unit = state.active(side)
        speed = aggregate_stats(unit, state.rules)["speed"]
        if dec.item:
            entries.append(QueuedEntry(side, "item", unit, state.rules.item_priority, speed))
        entries.append(QueuedEntry(side, "main", unit, entry_priority(state, ctx, side, "main"), speed))
    entries.sort(key=lambda e: (-e.priority, -e.speed))

    # 把 (priority, speed) 完全相同的条目分成一组，组内再定序（含硬币）
    result: list[QueuedEntry] = []
    i, n = 0, len(entries)
    while i < n:
        j = i
        while (j + 1 < n and entries[j + 1].priority == entries[i].priority
               and entries[j + 1].speed == entries[i].speed):
            j += 1
        result.extend(_order_group(entries[i:j + 1], state))
        i = j + 1
    return result


# ── 四个结算函数 ──
def resolve_item(state, ctx: TurnContext, entry: QueuedEntry) -> list[dict]:
    """扣 1 次道具次数 → 对**当前在场**单位调 apply_heal(max_hp // 2)
    → 发 item_use + heal。次数已尽在 validate_decision 就被拦住，这里只做防负数兜底。"""
    side = entry.side
    item = ctx.decision(side).item
    uses = state.side(side).item_uses
    if uses.get(item, 0) <= 0:
        return [ev("skipped", side, kind="item", unit=entry.actor.name, reason="道具次数已尽")]
    uses[item] -= 1
    target = state.active(side)
    amount = target.max_hp // 2
    hr = apply_heal(state, target, amount, source=item)
    return [
        ev("item_use", side, item=item, unit=target.name, uses_left=uses[item]),
        ev("heal", side, unit=target.name, applied=hr.applied, overflow=hr.overflow,
           hp=target.current_hp, source=item),
    ]


def _add_stat_layers(unit: Unit, stat: str, mode: str, layers: int,
                     source: str, permanent: bool = False) -> int:
    """层数合并进同 (stat, mode, permanent) 记录；返回合并后的总层数。"""
    for m in unit.stat_mods:
        if m.stat == stat and m.mode == mode and m.permanent == permanent:
            m.layers += layers
            return m.layers
    unit.stat_mods.append(StatModifier(stat=stat, mode=mode, layers=layers,
                                       permanent=permanent, source=source))
    return layers


def _apply_target_effect(tgt: Unit, se: SkillStatEffect, source: str) -> int:
    """把一条 SkillStatEffect 作用到单位上：能耗 → energy_cost_mods；属性/连击/吸血 → stat_mods。

    能耗减益（冰捆缚「全技能能耗+1」）：EnergyCostMod 层为 `-se.layers`（能耗读函数减层）。
    返回该记录合并后的总层数。
    """
    if se.stat == "energy_cost":
        return apply_energy_cost_mod(tgt, layers=-se.layers, permanent=False, trait=False, source=source)
    return _add_stat_layers(tgt, se.stat, se.mode, se.layers, source)


def effective_hits(state, unit: Unit, skill: Skill, side: str) -> int:
    """技能的实际连击数：基础 hits + 连击数buff（flat/pct），夹到 ≥1。

    - 虫鸣类（combo_per_team_skill）：基础 = 1 + 队内携带该技能的单位数。
    - **只有「带有连击描述」的技能（combo_eligible）受连击数buff加成**（负责人规则：
      常规连击数buff 1 层 = +1 连击；pct 1 层 = +10%）。
    """
    effect = skill.effect
    base = effect.hits
    if effect.combo_per_team_skill:
        base += sum(1 for u in state.side(side).units
                    if any(s.name == effect.combo_per_team_skill for s in u.skills))
    if not effect.combo_eligible:
        return max(1, base)
    flat, pct = combo_bonus(unit)
    return max(1, int((base + flat) * (1 + 0.10 * pct)))


def resolve_skill(state, ctx: TurnContext, entry: QueuedEntry) -> list[dict]:
    """支付能量 → 按 effect.category 分三支：
      攻击：按连击数逐发 compute_damage → apply_hp_loss → damage 事件（带 hit/hits），
            每发后若目标阵亡即停；全部命中后结算一次性效果（回能量 / 回血 / 吸血 /
            场下回能量 / 敌连击减益）。
      防御：什么也不做 —— 减伤已在 build_turn_context 武装完毕。
      状态：每连击应用 stat_effects（花炮/冰捆缚），再一次性应用 buff_effects（连击/
            吸血 buff）与资源效果（回能量 / 回血 / 偷能量 / 场下回能量）。
      三支之后：SKILL_RESOLVE 特性分发（一次技能恰好一次）。
    """
    side = entry.side
    idx = ctx.decision(side).action.get("value")
    unit = entry.actor
    if isinstance(idx, bool) or not isinstance(idx, int) or not 0 <= idx < len(unit.skills):
        return [ev("skipped", side, kind="main", unit=unit.name, reason="技能槽位非法")]
    skill = unit.skills[idx]
    effect = skill.effect
    # 能量支付走能耗减益（水蓝蓝·浸润）——actions 门控与这里读同一个函数，付得起才算合法
    unit.energy -= skill_energy_cost(unit, skill.energy_cost)
    # E4 迷雾：技能**确实释放** → 该方该下标精灵的该技能名揭示（对手视角从此可见详情）。
    # 同回合每方最多执行一次主动作（阵亡即回合结束），state.side(side).active 即释放者下标。
    state.side(side).revealed.add((state.side(side).active, skill.name))

    foe = "b" if side == "a" else "a"
    events: list[dict] = []
    dealt_counter = False
    if effect.category == SkillCategory.ATTACK:
        mult = effect.counter_damage_mult if ctx.counters(side) else 1.0
        reduced = ctx.reduction(foe)
        target = state.active(foe)
        # E2：克制倍率 + STAB——纯函数显式入参，不读 ctx/state.types 之外的东西
        eff = type_effectiveness(skill.type, target.types)
        stab = stab_multiplier(skill.type, unit.types)
        hits = effective_hits(state, unit, skill, side)
        dealt_counter = eff > 1.0   # 克制判定：系数大于 1 才算（迪莫·最好的伙伴）
        counter_cat = ctx.category(foe).value if ctx.counters(side) else ""
        total_dmg = 0
        for i in range(1, hits + 1):
            if target.fainted:
                break   # 连击途中目标阵亡 → 剩余连击不再结算
            dmg = compute_damage(state, unit, target, skill, counter_mult=mult, reduction=reduced,
                                 effectiveness=eff, stab=stab)
            loss = apply_hp_loss(state, target, dmg, source=skill.name)
            total_dmg += loss.applied
            events.append(ev(
                "damage", side, attacker=unit.name, skill=skill.name, target=target.name,
                damage=loss.applied, target_hp_left=target.current_hp,
                counter=counter_cat, mult=mult, reduced=reduced, eff=eff, stab=stab,
                hit=i, hits=hits,
            ))
        # ── 一次性效果 ──
        if effect.self_energy_gain:
            gained = apply_energy_gain(unit, effect.self_energy_gain,
                                       energy_max=state.rules.energy_max)
            events.append(ev("energy_gain", side, unit=unit.name, gained=gained, energy=unit.energy,
                             source=skill.name, target="self"))
        if effect.heal_pct_self:
            hr = heal_pct(state, unit, effect.heal_pct_self, source=skill.name)
            events.append(ev("heal", side, unit=unit.name, applied=hr.applied, overflow=hr.overflow,
                             hp=unit.current_hp, source=skill.name))
        lifesteal = effect.lifesteal_pct + 100 * lifesteal_bonus(unit)
        if lifesteal > 0 and total_dmg > 0:
            hr = apply_heal(state, unit, int(total_dmg * lifesteal / 100), source=skill.name)
            events.append(ev("heal", side, unit=unit.name, applied=hr.applied, overflow=hr.overflow,
                             hp=unit.current_hp, source=skill.name))
        if effect.bench_energy_gain:
            for bench in state.side(side).units:
                if bench is unit or bench.fainted:
                    continue
                gained = apply_energy_gain(bench, effect.bench_energy_gain,
                                           energy_max=state.rules.energy_max)
                events.append(ev("energy_gain", side, unit=bench.name, gained=gained,
                                 energy=bench.energy, source=skill.name, target="bench"))
        for se in effect.buff_effects:
            tgt = unit if se.target == "self" else state.active(foe)
            total = _apply_target_effect(tgt, se, skill.name)
            events.append(ev("stat_change", side, unit=tgt.name, skill=skill.name, stat=se.stat,
                             mode=se.mode, layers=se.layers, total_layers=total,
                             counter=counter_cat, target=se.target))
    elif effect.category == SkillCategory.DEFENSE:
        pass
    elif effect.category == SkillCategory.STATUS:
        counter_cat = ctx.category(foe).value if ctx.counters(side) else ""
        if effect.stat_effects:
            # P1/P2 状态系：每连击应用（花炮 / 冰捆缚 / 缓一缓…）
            hits = effective_hits(state, unit, skill, side)
            for _ in range(hits):
                for se in effect.stat_effects:
                    tgt = unit if se.target == "self" else state.active(foe)
                    total = _apply_target_effect(tgt, se, skill.name)
                    events.append(ev("stat_change", side, unit=tgt.name, skill=skill.name,
                                     stat=se.stat, mode=se.mode, layers=se.layers,
                                     total_layers=total, counter=counter_cat, target=se.target))
        elif effect.stat or effect.layers:
            # E0 教学：单条自身状态
            layers = effect.layers + (effect.counter_extra_layers if ctx.counters(side) else 0)
            total = _add_stat_layers(unit, effect.stat, effect.mode, layers, skill.name)
            events.append(ev("stat_change", side, unit=unit.name, skill=skill.name,
                             stat=effect.stat, mode=effect.mode, layers=layers,
                             total_layers=total, counter=counter_cat))
        # ── 一次性 buff_effects（连击/吸血）──
        for se in effect.buff_effects:
            tgt = unit if se.target == "self" else state.active(foe)
            total = _apply_target_effect(tgt, se, skill.name)
            events.append(ev("stat_change", side, unit=tgt.name, skill=skill.name, stat=se.stat,
                             mode=se.mode, layers=se.layers, total_layers=total,
                             counter=counter_cat, target=se.target))
        # ── 一次性资源效果 ──
        if effect.energy_gain:
            gained = apply_energy_gain(unit, effect.energy_gain, energy_max=state.rules.energy_max)
            events.append(ev("energy_gain", side, unit=unit.name, gained=gained, energy=unit.energy,
                             source=skill.name, target="self"))
        if effect.heal_pct_self:
            hr = heal_pct(state, unit, effect.heal_pct_self, source=skill.name)
            events.append(ev("heal", side, unit=unit.name, applied=hr.applied, overflow=hr.overflow,
                             hp=unit.current_hp, source=skill.name))
        if effect.steal_energy:
            foe_unit = state.active(foe)
            gained = min(effect.steal_energy, foe_unit.energy)
            foe_unit.energy -= gained
            unit.energy = min(state.rules.energy_max, unit.energy + gained)
            events.append(ev("steal", side, unit=unit.name, gained=gained, energy=unit.energy,
                             foe=foe_unit.name, foe_energy=foe_unit.energy, source=skill.name))
        if effect.energy_foe_cost_ratio > 0:
            # 雾气环绕：回复 = 敌方当前在场精灵全部技能能耗 × 比例（一半）
            foe_unit = state.active(foe)
            foe_cost = sum(s.energy_cost for s in foe_unit.skills)
            gained = apply_energy_gain(unit, int(foe_cost * effect.energy_foe_cost_ratio),
                                       energy_max=state.rules.energy_max)
            events.append(ev("energy_gain", side, unit=unit.name, gained=gained, energy=unit.energy,
                             source=skill.name, target="self", from_foe_cost=foe_cost))
        if effect.bench_energy_gain:
            for bench in state.side(side).units:
                if bench is unit or bench.fainted:
                    continue
                gained = apply_energy_gain(bench, effect.bench_energy_gain,
                                           energy_max=state.rules.energy_max)
                events.append(ev("energy_gain", side, unit=bench.name, gained=gained,
                                 energy=bench.energy, source=skill.name, target="bench"))
    # 特性：技能结算后统一分发（SKILL_RESOLVE）。特性只作用于本回合主动作使用者自身——
    # ctx.unit 指向 entry.actor，条件（用了哪系 / 是否克制）由 emit 的谓词解析。
    emit(state, Hook.SKILL_RESOLVE,
         SimpleNamespace(unit=unit, skill=skill, dealt_counter=dealt_counter,
                         energy_max=state.rules.energy_max),
         trait_defs_for(unit))
    return events


def resolve_switch(state, ctx: TurnContext, entry: QueuedEntry) -> list[dict]:
    """改 active 下标 → **清除离场单位的全部非永久增益层**（属性 + 能耗减益）→ switch 事件带 cleared_layers。"""
    side = entry.side
    idx = ctx.decision(side).action.get("value")
    side_state = state.side(side)
    if isinstance(idx, bool) or not isinstance(idx, int) or not 0 <= idx < len(side_state.units):
        return [ev("skipped", side, kind="main", unit=entry.actor.name, reason="换人槽位非法")]
    old = side_state.active_unit
    cleared = sum(m.layers for m in old.stat_mods if not m.permanent)
    cleared += sum(m.layers for m in old.energy_cost_mods if not m.permanent)
    old.stat_mods = [m for m in old.stat_mods if m.permanent]
    old.energy_cost_mods = [m for m in old.energy_cost_mods if m.permanent]
    side_state.active = idx
    new = side_state.active_unit
    return [ev("switch", side, out=old.name, **{"in": new.name}, cleared_layers=cleared)]


def resolve_recharge(state, ctx: TurnContext, entry: QueuedEntry) -> list[dict]:
    """energy = min(energy_max, energy + recharge_amount) → recharge 事件带实际 gained。"""
    side = entry.side
    unit = entry.actor
    gained = min(state.rules.recharge_amount, state.rules.energy_max - unit.energy)
    unit.energy += gained
    return [ev("recharge", side, unit=unit.name, gained=gained, energy=unit.energy)]


def resolve_entry(state, ctx: TurnContext, entry: QueuedEntry) -> list[dict]:
    """分派一条队列条目到对应的结算函数。

    输入：state / ctx（本回合派生量）/ entry（待结算动作）。
    输出：该条目产生的事件列表；未知动作类型 → skipped 事件。
    按 entry.kind 与决策动作类型分派：item → resolve_item；skill/switch/recharge → 各自结算。
    """
    if entry.kind == "item":
        return resolve_item(state, ctx, entry)
    atype = ctx.decision(entry.side).action.get("type")
    if atype == ActionType.SKILL.value:
        return resolve_skill(state, ctx, entry)
    if atype == ActionType.SWITCH.value:
        return resolve_switch(state, ctx, entry)
    if atype == ActionType.RECHARGE.value:
        return resolve_recharge(state, ctx, entry)
    return [ev("skipped", entry.side, kind=entry.kind, unit=entry.actor.name, reason="未知动作类型")]


# ── 收尾函数：阵亡（交互式补位）/ 判负 / 回合末 ──
def settle_faints(state) -> tuple[list[dict], str | None]:
    """处理当前在场的阵亡：faint → life_loss（**不自动补位**）。

    交互式补位（E0b 判断 8）：阵亡后由阵亡方玩家决定换哪只。本函数只发出
    faint / life_loss，返回需要补位的方；补位由 `apply_replacement` 应用。
    一次只处理第一个阵亡——回合在第一个阵亡处结束（判断 9：阵亡 → 回合结束）。
    是否终局由调用方用 `check_winner` 判定。
    """
    events: list[dict] = []
    for s in SIDES:
        side_state = state.side(s)
        unit = side_state.active_unit
        if unit.fainted:
            events.append(ev("faint", s, unit=unit.name))
            side_state.lives -= 1
            events.append(ev("life_loss", s, unit=unit.name, lives_left=side_state.lives))
            return events, s
    return events, None


def check_winner(state) -> str | None:
    """某方 lives <= 0 或已无存活单位 → 对方获胜；否则 None。
    第二个条件是死锁兜底：E0 默认 3 只 / 2 命走的是第一个条件；
    测第二个条件得用 `replace(DEFAULT_RULES, lives=5)`。"""
    for s in SIDES:
        if state.side(s).lives <= 0 or not state.side(s).has_living():
            return "b" if s == "a" else "a"
    return None


def _hp_pct_sum(side_state) -> int:
    """E4 无平局：一方剩余精灵血量百分比之和（逐只 `current*100//max`，整数、确定性）。"""
    return sum(u.current_hp * 100 // u.max_hp for u in side_state.units)


def timeout_winner(state) -> tuple[str, str]:
    """超过回合上限后的定胜负（负责人 2026-08-25，取消平局）：

    ① 双方剩余命数，多者胜；
    ② 命数相同 → 双方剩余精灵血量百分比之和，多者胜；
    ③ 仍相同 → **随机一方胜**（走引擎 RNG 流 → 同 seed 可复现，calls +1）。
    返回 (胜方, 判定依据文案)。
    """
    a, b = state.side_a, state.side_b
    if a.lives != b.lives:
        w = "a" if a.lives > b.lives else "b"
        return w, f"命数 {a.lives} 对 {b.lives}，{w} 方领先"
    sa, sb = _hp_pct_sum(a), _hp_pct_sum(b)
    if sa != sb:
        w = "a" if sa > sb else "b"
        return w, f"血量百分比和 {sa}% 对 {sb}%"
    w = state.rng.choice(SIDES)
    return w, "双方命数与血量百分比和均相同，随机判定胜方"


def apply_replacement(state, side: str, bench_idx: int) -> list[dict]:
    """应用玩家选择的补位：active = bench_idx，发 replace 事件。阵亡单位已死，无需清层。"""
    side_state = state.side(side)
    old = side_state.active_unit
    side_state.active = bench_idx
    return [ev("replace", side, out=old.name, **{"in": side_state.active_unit.name})]


def end_of_turn(state) -> None:
    """回合末清理的扩展点。**E0b 函数体是 pass，而且理应如此**——回合内临时量都在
    TurnContext 这个局部对象里。将来的状态叠层 tick / 冷却递减挂进这里。"""


def end_turn(state) -> list[dict]:
    """回合末统一收尾：**整个代码库里唯一推进回合号的地方**。

    终局、超时定胜负、常规回合都经过这里：终局 → battle_end(winner)；
    超过回合上限 → `timeout_winner` 定出胜方（**不再平局**，E4 规则：命数 → 血量百分比和 →
    随机硬币）；否则只推进回合号。`battle_end` **只在这里发射**。
    """
    end_of_turn(state)
    state.turn += 1
    if state.done:
        return [ev("battle_end", state.winner or "both", winner=state.winner, turn=state.turn)]
    if state.turn > state.rules.max_turns:
        state.done = True
        winner, reason = timeout_winner(state)
        state.winner = winner
        return [ev("battle_end", winner, winner=winner, turn=state.turn,
                   message=f"超过回合上限 {state.rules.max_turns}，{reason}。")]
    return []


def resolve_turn(state, dec_a: Decision, dec_b: Decision) -> tuple[list[dict], str | None]:
    """结算一回合的队列，直到第一个阵亡或回合正常结束。

    返回 (事件, 需要补位的方 或 None)：
    - None 且 state.done：终局（无存活 / 命归零，无补位可问）；
    - None 且 not done：回合正常结束（无阵亡）；
    - 某方：该方在场阵亡且有存活后备，**回合在此结束**，等待该方玩家选择补位。

    本函数不做回合末收尾（那是 end_turn）、不自动补位（那是 apply_replacement）。
    """
    if state.done:
        return [ev("error", "", message="对局已结束。")], None

    events: list[dict] = []

    # 1) DECLARE：双方声明已知 → 定应对关系 + 武装减伤（必须在任何结算之前）
    ctx, arm_events = build_turn_context(state, dec_a, dec_b)
    events += arm_events

    # 2) + 3) ORDER + ACT
    for entry in build_queue(state, ctx):
        if entry.actor.fainted:
            events.append(ev("skipped", entry.side, kind=entry.kind,
                             unit=entry.actor.name, reason="已被击倒"))
            continue   # 防御性兜底：正常流程阵亡即暂停，到不了这里
        events += resolve_entry(state, ctx, entry)
        faint_events, need_side = settle_faints(state)
        events += faint_events
        if need_side is not None:
            winner = check_winner(state)
            if winner is not None:
                state.winner, state.done = winner, True
                return events, None      # 无存活 / 命归零 → 终局，无补位可问
            return events, need_side     # 有存活后备 → 暂停等玩家补位（回合在此结束）

    return events, None


def execute_turn(state, dec_a: Decision, dec_b: Decision) -> list[dict]:
    """推进一回合。**默认补位策略**：阵亡时自动取第一个存活后备。

    execute_turn 是确定性整回合转移（供 step / MCTS / 回放用）。玩家**交互式**补位
    （阵亡 → 回合结束 → 由阵亡方玩家选哪只）由 session 驱动
    `resolve_turn` → `apply_replacement` → `end_turn`。
    """
    if state.done:
        return [ev("error", "", message="对局已结束。")]
    events, need_side = resolve_turn(state, dec_a, dec_b)
    if need_side is not None:
        bench = state.side(need_side).first_living_bench()
        events += apply_replacement(state, need_side, bench)
    events += end_turn(state)
    return events


def step(state, dec_a: Decision, dec_b: Decision) -> tuple[BattleState, list[dict]]:
    """**纯转移语义**：不改入参 state，返回 (新状态, 事件流)。实现 = clone 后调 execute_turn。
    前瞻 / MCTS / 回放校验用它；正常对局用 session 的交互式流程。
    `step` 就是 f(state, dec_a, dec_b) → state'——核心不变式的可执行答案。"""
    new_state = state.clone()
    events = execute_turn(new_state, dec_a, dec_b)
    return new_state, events
