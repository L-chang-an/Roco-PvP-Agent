"""反应执行器（v3 骨架·调度层）：Atom 执行 + 事件 → 反应 fixpoint 循环。

这是全库**唯一**运行「执行 → 发事件 → 收集反应 → 再执行」循环的地方
（`mydocs/battle_docs.md` §7 主循环的骨架版）。引擎只调 `run()`，对任何具体
技能 / 效果 / 特性零感知——「加效果 = 加 Atom 类型 + 一个 reducer + 一条 Trigger
绑定，引擎零改动」的 v3 扩展铁律由此继续成立。

循环语义：
1. 执行初始 Atom 列表（`reducer.reduce_all`，唯一状态写入口）；
2. 事件队列 = 原子产生的领域事件（Frame.domain_events），**再**追加
   `after(frame)` 提供的事件（如 SkillResolved 在伤害事件之后反应）——
   与旧「技能结算完再触发特性」的顺序一致；
3. 逐事件出队 → `collector`（默认 triggers.collect_reactions，**只返回新 Atom、
   不直接改状态**）→ 新 Atom 再经 reducer 执行 → 新领域事件入队尾 → 直到队列空；
4. 保护闸：反应原子总数 > `budget` 或处理事件数 > `max_events` → 确定性
   `warnings.warn` + 停止（丢弃剩余队列）。FIFO + 纯 collector + 纯 reducer
   → 顺序确定。去重 / EngineFault 升级留到真实循环风险绑定出现时（v3 §6.1）。

依赖方向：`engine → pipeline → reducer/triggers`；不 import engine（无环）。
"""

from __future__ import annotations

import warnings
from collections import deque
from typing import TYPE_CHECKING, Callable, Iterable

from .reducer import Frame, reduce_all
from .triggers import collect_reactions

if TYPE_CHECKING:
    from .atom import Atom
    from .domain import DomainEvent
    from .models import BattleState, Unit

DEFAULT_REACTION_BUDGET = 32   # 一次 run 的反应原子总数上限（v3 §6.1 反应预算）
DEFAULT_MAX_EVENTS = 32        # 被处理事件数上限（防事件链无限增长的兜底闸）


def run(state: "BattleState", atoms: list["Atom"], frame: Frame, *,
        unit: "Unit" | None = None, trait_defs=None, energy_max: int = 10,
        after: Callable[[Frame], Iterable["DomainEvent"]] | None = None,
        collector: Callable = collect_reactions,
        budget: int = DEFAULT_REACTION_BUDGET,
        max_events: int = DEFAULT_MAX_EVENTS) -> tuple[list[dict], list["DomainEvent"]]:
    """执行 atoms 并沿领域事件触发反应直至静默（fixpoint）。

    参数：
    - `after`：原子执行完后、反应循环开始前追加的领域事件构造器（读 Frame 派生量，
      如 dealt_counter → SkillResolved）。追加在原子领域事件**之后**。
    - `collector`：事件 → 新 Atom 的纯收集器（默认 triggers.collect_reactions；
      单测注入假 collector）。
    - `budget` / `max_events`：保护闸（超限 → warnings.warn + 确定性停止）。

    返回 (展示事件, 全部领域事件)。展示事件交给引擎；领域事件供测试与未来的
    TURN_END 阵亡检查。行为承诺：对既有绑定，展示事件与状态修改与旧路径逐位一致。
    """
    events: list[dict] = []
    events += reduce_all(state, list(atoms), frame)
    domain: list["DomainEvent"] = list(frame.domain_events)
    pending = deque(domain)
    pending.extend(after(frame) if after else ())

    processed = 0   # 已处理事件数
    spent = 0       # 反应原子总数
    while pending:
        if processed >= max_events or spent >= budget:
            warnings.warn(
                f"v3 反应预算超限：processed={processed} spent={spent}，"
                f"丢弃剩余 {len(pending)} 个事件（确定性停止）。")
            break
        evt = pending.popleft()
        processed += 1
        new_atoms = collector(state, evt, unit, trait_defs, energy_max)
        if not new_atoms:
            continue
        if spent + len(new_atoms) > budget:
            warnings.warn(
                f"v3 反应预算超限：反应原子将达 {spent + len(new_atoms)} > {budget}，"
                f"丢弃本事件反应（确定性停止）。")
            break
        spent += len(new_atoms)
        events += reduce_all(state, new_atoms, frame)
        # 只把新产生的领域事件入队（同一 Frame 累积，需记游标防重复处理）
        pending.extend(frame.domain_events[len(domain):])
        domain = list(frame.domain_events)
    return events, domain
