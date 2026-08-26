"""整局编排：把一局跑到 done，产出可复现指纹的 MatchResult。

`TurnRecord.turn` 取**提交时**的回合号，于是 `[t.turn for t in turns] ==
list(range(1, n+1))`、`len(turns) == turns[-1].turn`。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .actions import Decision, recharge_action
from .models import SIDES
from .players import Player
from .visibility import filter_events_for


@dataclass
class TurnRecord:
    """一回合的完整记录（run_match 的输出，供回放/审计）。

    字段：turn=**提交时**的回合号；decision_a/b=双方本回合 Decision；events=全部事件
    （含补位与回合末收尾）；state_hash=回合末的 state 指纹；replace_a/b=本回合双方的
    补位选择（无阵亡补位 → None）——**E6 轨迹重放的必需输入**。
    """

    turn: int                     # **提交时**的回合号
    decision_a: Decision
    decision_b: Decision
    events: list[dict]
    state_hash: str
    replace_a: int | None = None  # a 方本回合补位下标（无 → None）
    replace_b: int | None = None  # b 方本回合补位下标（无 → None）


@dataclass
class MatchResult:
    """一局的完整结果：元信息 + 逐回合记录。`digest()` 是确定性闸门的唯一指纹。"""

    battle_id: str
    seed: int
    winner: str | None
    done: bool
    turns: list[TurnRecord] = field(default_factory=list)

    @property
    def turn_count(self) -> int:
        """输出：已记录的回合数（== len(turns)）。"""
        return len(self.turns)

    def digest(self) -> str:
        """整局指纹 = sha256(逐回合 state_hash 拼接)。确定性闸门只比这一个字符串。"""
        h = hashlib.sha256()
        for t in self.turns:
            h.update(t.state_hash.encode("utf-8"))
        return h.hexdigest()


def run_match(session, players: dict[str, Player]) -> MatchResult:
    """把一局跑到 done：每回合 双方 decide → submit → resolve →（阵亡则补位）→ on_turn_result。

    交互式补位：`resolve()` 若返回 `need_replacement`（某方在场阵亡），就调用该方
    `choose_replacement` 选一个存活后备，`submit_replacement` 应用后走回合末收尾。
    防御性兜底：非法提交 / 非法补位（自定义策略写错）回落为聚能 / 第一个后备。

    **E6 语义（2026-08-26）**：
    - 观测口径：给玩家的观测一律 `session.view(side)`（E4 迷雾白名单，己方全见、敌方屏蔽）——
      绝不给全量 `observe()`，否则 E6.5 真实 LLM 一接入就泄密。random/scripted 玩家不读观测，行为无变化。
    - 事件口径：`on_turn_result` 给的是 `filter_events_for(side, …)` 过滤后的事件（敌方绝对血量
      → 百分比等）——原始事件含 `target_hp_left` 等隐藏量，直接给玩家就泄密（E6.5 的 LLM 会读它）。
    - 补位记录：每回合双方的补位选择写进 `TurnRecord.replace_a/b`（无 → None），是轨迹重放的必需输入。
    """
    result = MatchResult(battle_id=session.state.battle_id, seed=session.state.rng.seed,
                         winner=None, done=False)
    for s in SIDES:
        players[s].on_match_start(session.view(s))
    while not session.state.done:
        turn = session.state.turn
        decisions: dict[str, Decision] = {}
        for s in SIDES:
            dec = players[s].decide(session.view(s), session.legal_actions(s),
                                    session.legal_items(s))
            if not session.submit(s, dec)["ok"]:
                dec = Decision(recharge_action())
                session.submit(s, dec)
            decisions[s] = dec

        all_events: list[dict] = []
        replaces: dict[str, int | None] = {"a": None, "b": None}
        res = session.resolve()
        if not res["ok"]:          # 双方都已入缓冲，这里必然 ok
            raise RuntimeError(f"resolve 失败：{res.get('error')}")
        all_events += res["events"]
        while res.get("need_replacement"):
            side = res["need_replacement"]
            options = session.replacement_options(side)
            if not options:
                raise RuntimeError(f"{side} 方需要补位但没有存活后备。")
            bench = players[side].choose_replacement(session.view(side), options)
            r = session.submit_replacement(side, bench)
            if not r["ok"]:
                bench = options[0]
                r = session.submit_replacement(side, bench)
            replaces[side] = bench
            all_events += r["events"]
            res = r

        result.turns.append(TurnRecord(turn=turn, decision_a=decisions["a"],
                                       decision_b=decisions["b"], events=all_events,
                                       state_hash=session.state.state_hash(),
                                       replace_a=replaces["a"], replace_b=replaces["b"]))
        for s in SIDES:
            players[s].on_turn_result(session.view(s),
                                      filter_events_for(s, all_events, session.state))
    result.winner = session.state.winner
    result.done = session.state.done
    return result
