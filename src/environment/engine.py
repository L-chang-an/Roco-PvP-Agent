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

from dataclasses import dataclass, replace

from .actions import Decision
from .compiler import compile_skill
from .damage import apply_heal
from .domain import SkillResolved, TurnEnded, TurnStarted, UnitEntered, UnitExited
from .events import ev
from .marks import speed_penalty
from .models import (ActionType, BattleState, SIDES, Skill, TraitState, Unit,
                     aggregate_stats, skill_from_instance)
from .pipeline import run
from .prediction import predictions_for
from .reducer import Frame
from .rules import BOSS_EVOLUTION_ITEM
from .skillbook import SkillCategory
from .traits import trait_defs_for


@dataclass(frozen=True)
class TurnContext:
    """本回合的全部派生量。**是局部对象，不是 BattleState 的字段。**

    这是核心不变式的关键：回合内临时量一旦住进状态，就必须记得清理（参考项目的
    `_defense_skill_a` 正是如此，`end_of_turn_cleanup` 手工清 8 个字段）。做成局部
    对象后，回合末时段的**效果**（DOT / 天气 / 冷却）从 `end_of_turn` 的 TurnEnded
    管道接入，同样不落状态。
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


def _combat_skill(unit: Unit, idx: int) -> Skill | None:
    """从 `unit.current_skills[idx]`（当前回合技能详情，SkillInstance）构建引擎 Skill。

    数据协议 v2：Unit.skills / current_skills 只存五要素（无 effect）；引擎需要效果时
    按技能名查 P1∪P2 效果表重建——五要素取**当前回合视图**（愿力替换 / 冷却后的能耗、
    威力、类别以 current_skills 为准）。构造逻辑上提到 models.skill_from_instance
    （与 prediction 预估共用）。
    """
    return skill_from_instance(unit.current_skills[idx])


def _declared_skill(state, side: str, dec: Decision):
    """读出该方声明的技能（非 skill 动作 / 槽位非法 → None）。防御性：直接调用
    execute_turn 时不会因非法槽位崩溃。"""
    if dec.action.get("type") != ActionType.SKILL.value:
        return None
    idx = dec.action.get("value")
    if isinstance(idx, bool) or not isinstance(idx, int):
        return None
    unit = state.active(side)
    if idx < 0 or idx >= len(unit.current_skills):
        return None
    return _combat_skill(unit, idx)


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
        # 减速印记：该方在场速度 −10×层（印记/天气批 2026-08-30 读钩子）
        speed = aggregate_stats(unit, state.rules)["speed"] - speed_penalty(state.side(side))
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
    """道具分派：首领进化 → `_resolve_boss_item`；其余（草魔法）→ 回血 50%。

    次数已尽在 validate_decision 就被拦住，这里只做防负数兜底。"""
    side = entry.side
    item = ctx.decision(side).item
    if item == BOSS_EVOLUTION_ITEM:
        return _resolve_boss_item(state, ctx, entry)
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


def _resolve_boss_item(state, ctx: TurnContext, entry: QueuedEntry) -> list[dict]:
    """首领进化道具（2026-08-30 拍板）：**一阶进化** name → 选定 boss 形态。

    - 只有 boss 的上一阶可触发（无首领血脉 / 萌化中 → skipped）；
    - 原地替换 name/types/base_stats/stats/trait（**unit_id 不变**；技能保留、
      stat_mods 保留、能量保留；HP 同比例缩放走 damage.apply_max_hp_change）；
    - 发 boss_evolution 展示事件 + UnitEntered(from_boss=True) → 首领化后的
      入场类特性/印记触发。
    """
    from .damage import apply_max_hp_change
    from .dataset import load_spirits
    from .evolution import boss_targets_of
    from .statline import calc_combat_stats
    from .statuses import morph_layers
    from .traits import resolve_trait_name

    side = entry.side
    unit = entry.actor
    dec = ctx.decision(side)
    item = dec.item
    uses = state.side(side).item_uses
    if uses.get(item, 0) <= 0:
        return [ev("skipped", side, kind="item", unit=unit.name, reason="道具次数已尽")]
    targets = boss_targets_of(unit.name)
    if not targets or morph_layers(unit) > 0:
        return [ev("skipped", side, kind="item", unit=unit.name,
                   reason="当前精灵不可首领化")]
    boss = dec.item_arg if dec.item_arg else targets[0]
    if boss not in targets:
        return [ev("skipped", side, kind="item", unit=unit.name, reason="首领化分支非法")]
    uses[item] -= 1

    sp = load_spirits().get(boss)
    base = dict(sp.stats)
    new_stats = calc_combat_stats(base, unit.iv, unit.nature)
    old_name = unit.name
    unit.name = boss
    unit.types = list(sp.types)
    unit.base_stats = base
    unit.trait = TraitState(name=resolve_trait_name(sp.trait_name), desc=sp.trait_desc)
    apply_max_hp_change(state, unit, new_stats["hp"], source="首领进化")
    unit.stats = new_stats

    events = [
        ev("item_use", side, item=item, unit=boss, uses_left=uses[item]),
        ev("boss_evolution", side, from_=old_name, to=boss, unit=boss),
    ]
    enter_ev = UnitEntered(unit_id=unit.id, from_faint=False, from_boss=True)
    re_events, _ = run(state, [], Frame(), unit=None, after=lambda f, e=enter_ev: [e])
    return events + re_events


def resolve_skill(state, ctx: TurnContext, entry: QueuedEntry,
                  acted_first: bool = False) -> list[dict]:
    """支付能量 → 按 effect.category 分三支（**v3 骨架：走 compiler + pipeline**）。

    攻击/防御/状态三支的全部效果逻辑从旧的内联 if/else 迁移到
    `compiler.compile_skill`（技能 → Atom 列表）+ `pipeline.run`（Atom → 事件 →
    领域事件 → 特性反应 fixpoint）。SkillResolved 由 after 回调在读
    `frame.dealt_counter` 后构造——特性触发与旧 emit 路径逐位一致（哨兵把关）。

    骨架原则：**行为逐位等价**——事件流与 state_hash 与旧引擎完全一致
    （`tests/test_v3_sentinel.py` 哨兵把关）。
    """
    side = entry.side
    idx = ctx.decision(side).action.get("value")
    unit = entry.actor
    if isinstance(idx, bool) or not isinstance(idx, int) or not 0 <= idx < len(unit.current_skills):
        return [ev("skipped", side, kind="main", unit=unit.name, reason="技能槽位非法")]
    skill = _combat_skill(unit, idx)
    if skill is None:
        return [ev("skipped", side, kind="main", unit=unit.name, reason="技能效果未实装")]
    # 技能 → Atom 列表 → pipeline 执行（扣能量/揭示/伤害段/资源效果都在 reducer 里）；
    # 特性（SKILL_RESOLVE）作为 after 事件进入同一反应循环。
    frame = Frame()
    events, _domain = run(
        state, compile_skill(state, ctx, unit, skill, side, acted_first=acted_first), frame,
        unit=unit, trait_defs=trait_defs_for(unit),
        energy_max=state.rules.energy_max,
        after=lambda f, c=ctx.counters(side): [SkillResolved(unit_id=unit.id, skill=skill.name,
                                                             dealt_counter=f.dealt_counter,
                                                             skill_type=skill.type,
                                                             countered=c)])
    return events


def resolve_switch(state, ctx: TurnContext, entry: QueuedEntry) -> list[dict]:
    """改 active 下标 → **清除离场单位的全部非永久增益层**（stat_mods + trait.gains）→
    switch 事件带 cleared_layers → ENTER/EXIT 领域事件（先 EXIT 后 ENTER：
    暗涌印记先于降灵/棘刺作用于同一入场者，见 marks.collect）。"""
    side = entry.side
    idx = ctx.decision(side).action.get("value")
    side_state = state.side(side)
    if isinstance(idx, bool) or not isinstance(idx, int) or not 0 <= idx < len(side_state.units):
        return [ev("skipped", side, kind="main", unit=entry.actor.name, reason="换人槽位非法")]
    old = side_state.active_unit
    cleared = sum(m.layers for m in old.stat_mods if not m.permanent)
    old.stat_mods = [m for m in old.stat_mods if m.permanent]
    if old.trait:
        cleared += sum(m.layers for m in old.trait.gains if not m.permanent)
        old.trait.gains = [m for m in old.trait.gains if m.permanent]
    side_state.active = idx
    new = side_state.active_unit
    events = [ev("switch", side, out=old.name, **{"in": new.name}, cleared_layers=cleared)]
    exit_ev = UnitExited(unit_id=old.id, incoming_id=new.id)
    enter_ev = UnitEntered(unit_id=new.id, from_faint=False)
    re_events, _ = run(state, [], Frame(), unit=None,
                       after=lambda f, es=(exit_ev, enter_ev): list(es))
    return events + re_events


def resolve_recharge(state, ctx: TurnContext, entry: QueuedEntry) -> list[dict]:
    """energy = min(energy_max, energy + recharge_amount) → recharge 事件带实际 gained。"""
    side = entry.side
    unit = entry.actor
    gained = min(state.rules.recharge_amount, state.rules.energy_max - unit.energy)
    unit.energy += gained
    return [ev("recharge", side, unit=unit.name, gained=gained, energy=unit.energy)]


def resolve_entry(state, ctx: TurnContext, entry: QueuedEntry,
                  acted_first: bool = False) -> list[dict]:
    """分派一条队列条目到对应的结算函数。

    输入：state / ctx（本回合派生量）/ entry（待结算动作）/ acted_first（本回合
    执行顺序先于对手——风起印记读钩子）。
    输出：该条目产生的事件列表；未知动作类型 → skipped 事件。
    按 entry.kind 与决策动作类型分派：item → resolve_item；skill/switch/recharge → 各自结算。
    """
    if entry.kind == "item":
        return resolve_item(state, ctx, entry)
    atype = ctx.decision(entry.side).action.get("type")
    if atype == ActionType.SKILL.value:
        return resolve_skill(state, ctx, entry, acted_first=acted_first)
    if atype == ActionType.SWITCH.value:
        return resolve_switch(state, ctx, entry)
    if atype == ActionType.RECHARGE.value:
        return resolve_recharge(state, ctx, entry)
    return [ev("skipped", entry.side, kind=entry.kind, unit=entry.actor.name, reason="未知动作类型")]


# ── 第 0 回合：首发选择（2026-08-30）──
def choose_starter(state, side: str, bench_idx: int) -> list[dict]:
    """第 0 回合：选择首发精灵——设置该方 active（入场效果由 `start_battle_entry`
    统一触发）。合法性由调用方（`session.validate_starter`）保证，这里只改状态。"""
    state.side(side).active = bench_idx
    return []


def start_battle_entry(state) -> list[dict]:
    """第 0 回合收尾：双方首发触发入场效果（UnitEntered——入场类印记/特性的统一落点，
    如抓到你了/结晶水/守护者），之后进入第 1 回合的正常操作。"""
    entered = [UnitEntered(unit_id=state.active(s).id, from_faint=False) for s in SIDES]
    events, _ = run(state, [], Frame(), unit=None, after=lambda f, es=entered: es)
    return events


# ── 收尾函数：阵亡（交互式补位）/ 判负 / 回合末 ──
def _clear_on_faint(unit: Unit) -> None:
    """阵亡清理（2026-08-30 拍板）：清除非永久 buff 与**冻结层**；永久层（萌化等）
    保留——复活特性存在时，复活后仍带永久 buff（除冻结）。"""
    unit.stat_mods = [m for m in unit.stat_mods if m.permanent and m.stat != "冻结"]
    if unit.trait:
        unit.trait.gains = [g for g in unit.trait.gains if g.permanent]


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
            _clear_on_faint(unit)
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


def _tick_cooldowns(state) -> None:
    """防御冷却递减（2026-08-30 修订）：冷却衰减只看精灵是否**完整经历「冷却回合」
    的两个节点**——「冷却回合开始」节点（释放防御后的下一回合开始时在场）+「冷却
    回合结束」节点（经历回合结束 / 换下离场 / 阵亡等边界），**无需完整在场持续一回合**。

    实现上在 `resolve_turn` 入口对**当前在场精灵**逐项 −1：入口即「冷却回合开始」
    节点——被禁回合开始时在场者在此递减，之后即使本回合换下/阵亡也已解禁；
    释放防御的当回合即离场者，下一回合开始时不在场 → 不递减（未经历「开始」节点，
    即使之后阵亡补位上场也只经历了「结束」节点，**仍不递减**）。
    """
    for s in SIDES:
        unit = state.active(s)
        unit.current_skills = [replace(sk, cooldown=sk.cooldown - 1)
                               if sk.cooldown > 0 else sk for sk in unit.current_skills]


def apply_replacement(state, side: str, bench_idx: int) -> list[dict]:
    """应用玩家选择的补位：active = bench_idx，发 replace 事件。阵亡单位已死，无需清层。

    入场领域事件 UnitEntered(from_faint=True)——暗涌印记「持有者阵亡离场后、补位入场
    者承接收减益」的落点（见 marks.collect）。**补位不递减冷却**：补位入场者只经历了
    「冷却回合结束」节点，未经历「开始」节点 → 不满足「两个节点」衰减条件（见
    `_tick_cooldowns`），冷却保持原值。"""
    side_state = state.side(side)
    old = side_state.active_unit
    side_state.active = bench_idx
    events = [ev("replace", side, out=old.name, **{"in": side_state.active_unit.name})]
    incoming = side_state.active_unit
    enter_ev = UnitEntered(unit_id=incoming.id, from_faint=True)
    re_events, _ = run(state, [], Frame(), unit=None, after=lambda f, e=enter_ev: [e])
    return events + re_events


def end_of_turn(state) -> list[dict]:
    """回合末时段执行点（v3 骨架 2026-08-30）：构造 TurnEnded → 反应管道。

    属于当前回合的结算时段（在 `turn += 1` **之前**）；TurnEnded 反应收集序 =
    **DOT（中毒→灼烧→寄生）→ 印记（光合/中毒）→ 天气（暴风雪/雷鸣）**（2026-08-30
    拍板：保持印记先于天气，DOT 插到最前；见 triggers.collect_reactions），效果先
    结算、再天气递减过期（`weather.tick`）。DOT 造成回合末阵亡的补位由 resolve_turn
    开场兜底处理。
    """
    frame = Frame()
    events, _domain = run(state, [], frame, unit=None,
                          after=lambda f: [TurnEnded(turn=state.turn)])
    from .weather import tick

    tick(state)
    return events


def end_turn(state) -> list[dict]:
    """回合末统一收尾：**整个代码库里唯一推进回合号的地方**。

    终局、超时定胜负、常规回合都经过这里：回合末时段（end_of_turn，DOT/天气/印记/
    冷却的将来落点）→ 回合号推进 → 终局 → battle_end(winner)；
    超过回合上限 → `timeout_winner` 定出胜方（**不再平局**，E4 规则：命数 → 血量百分比和 →
    随机硬币）；否则只推进回合号。`battle_end` **只在这里发射**。
    """
    events = end_of_turn(state)
    state.turn += 1
    if state.done:
        return events + [ev("battle_end", state.winner or "both", winner=state.winner,
                            turn=state.turn)]
    if state.turn > state.rules.max_turns:
        state.done = True
        winner, reason = timeout_winner(state)
        state.winner = winner
        return events + [ev("battle_end", winner, winner=winner, turn=state.turn,
                            message=f"超过回合上限 {state.rules.max_turns}，{reason}。")]
    return events


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

    # 0) TURN_START：回合开始执行点（v3 骨架 2026-08-30）——确定性预估随事件携带
    #（预估与决策无关，双方各一份；「回合开始看预估伤害」的特性将来从此事件接入）。
    start_events, _ = run(state, [], Frame(), unit=None,
                          after=lambda f: [TurnStarted(
                              turn=state.turn,
                              predictions={"a": predictions_for(state, "a"),
                                           "b": predictions_for(state, "b")})])
    events += start_events

    # 0.45) 防御冷却递减（2026-08-30 修订）：入口 =「冷却回合开始」节点——仅在场精灵
    # 递减，先于本回合换人动作执行；被禁回合开始时在场者在此解禁（之后换下也仍解禁），
    # 释放当回合即离场者下一回合开始不在场 → 不递减（未经历「开始」节点）。**必须在
    # 开场兜底之前**（兜底提前返回时本回合已消耗，冷却不可因此多冻一回合）。
    _tick_cooldowns(state)

    # 0.5) 开场兜底（印记/天气批 2026-08-30）：TURN_END 效果（中毒印记等）可能在
    # 回合末造成阵亡——复用现有补位暂停流（本回合决策已提交，视为「阵亡 → 回合
    # 在此结束、等补位」语义顺延一个相位）。
    faint_events, need_side = settle_faints(state)
    events += faint_events
    if need_side is not None:
        winner = check_winner(state)
        if winner is not None:
            state.winner, state.done = winner, True
            return events, None
        return events, need_side

    # 0.6) 首回合入场已移到第 0 回合（2026-08-30）：入场效果在 start_battle_entry 触发
    #（首发选择之后、第 1 回合之前），此处不再重复触发。

    # 1) DECLARE：双方声明已知 → 定应对关系 + 武装减伤（必须在任何结算之前）
    ctx, arm_events = build_turn_context(state, dec_a, dec_b)
    events += arm_events

    # 2) + 3) ORDER + ACT（acted 跟踪 = 风起印记「先手」口径：对手尚未执行任何条目）
    acted: set[str] = set()
    for entry in build_queue(state, ctx):
        if entry.actor.fainted:
            events.append(ev("skipped", entry.side, kind=entry.kind,
                             unit=entry.actor.name, reason="已被击倒"))
            continue   # 防御性兜底：正常流程阵亡即暂停，到不了这里
        foe = "b" if entry.side == "a" else "a"
        events += resolve_entry(state, ctx, entry, acted_first=foe not in acted)
        acted.add(entry.side)
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
