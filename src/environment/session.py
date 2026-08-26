"""对局门面：同时提交缓冲 + 合法性闸门 + 只读观测。

交互式补位（判断 8）：`resolve()` 结算到第一个阵亡会**暂停**（返回
`need_replacement`），阵亡方玩家通过 `submit_replacement` 决定换哪只，之后才走
回合末收尾。`submit` 只入缓冲、不推进状态；非法输入一律显式拒绝，**不做
try/except Exception 的全吞降级**。非线程安全：并发由未来的 web 层持锁。
"""

from __future__ import annotations

from .actions import (Decision, legal_actions, legal_items, replacement_options,
                      validate_decision, validate_replacement)
from .engine import apply_replacement, end_turn, resolve_turn
from .models import SIDES, BattleState, new_battle
from .rules import DEFAULT_RULES


class BattleSession:
    """对局状态机：SUBMIT → RESOLVE → (阵亡?) REPLACE_QUERY → END_TURN。

    回合推进由 `resolve()` + 可选的 `submit_replacement()` 驱动：resolve 结算到
    第一个阵亡就停下等补位，补位完成才走回合末收尾。
    """

    def __init__(self, state: BattleState):
        self._state = state
        self._pending: dict[str, Decision] = {}
        self._need_replace: str | None = None   # 正在等待哪一方选择补位（None = 无）

    @classmethod
    def start(cls, roster_a, roster_b, *, seed: int, items_a=None, items_b=None,
              rules=DEFAULT_RULES, battle_id: str = "") -> "BattleSession":
        """开局工厂。输入：双方 roster spec 列表 / seed / 道具 / 规则 / battle_id；
        输出：新 BattleSession（内部已 new_battle 建好 BattleState）。"""
        state = new_battle(roster_a, roster_b, seed=seed, items_a=items_a,
                           items_b=items_b, rules=rules, battle_id=battle_id)
        return cls(state)

    @property
    def state(self) -> BattleState:
        """输出：底层 BattleState（只读使用；测试与回放直接读它）。"""
        return self._state

    def pending_sides(self) -> list[str]:
        """稳定序，取自 SIDES。"""
        return [s for s in SIDES if s in self._pending]

    def legal_actions(self, side: str) -> list[dict]:
        """输入：side；输出：该方当前合法动作池（门控后，恒含聚能）。"""
        return legal_actions(self._state, side)

    def legal_items(self, side: str) -> list[str]:
        """输入：side；输出：该方剩余次数 > 0 的道具名列表。"""
        return legal_items(self._state, side)

    def replacement_options(self, side: str) -> list[int]:
        """阵亡方可选的补位后备槽位（存活、非当前在场）。"""
        return replacement_options(self._state, side)

    def submit(self, side: str, dec: Decision) -> dict:
        """校验并缓冲一方提交（同时出招语义：只入缓冲、不推进状态）。
        非法 → {"ok": False, "error": …}，缓冲不变、turn 不变、state_hash 不变。"""
        if self._need_replace is not None:
            return {"ok": False, "error": f"正在等待 {self._need_replace} 方选择补位。"}
        if side not in SIDES:
            return {"ok": False, "error": f"未知阵营「{side}」。"}
        reason = validate_decision(self._state, side, dec)
        if reason is not None:
            return {"ok": False, "error": reason}
        self._pending[side] = dec
        return {"ok": True, "turn": self._state.turn, "state_hash": self._state.state_hash()}

    def resolve(self) -> dict:
        """双方齐备 → 结算到第一个阵亡或回合正常结束。

        返回字段：ok / events / need_replacement / done / winner / state_hash / observation。
        - `need_replacement` 为某方时：**回合在此暂停**，等该方 `submit_replacement`；
        - 否则回合已走完回合末收尾（end_turn），events 已含 battle_end（若终局/平局）。
        """
        if self._need_replace is not None:
            return {"ok": False, "error": f"正在等待 {self._need_replace} 方选择补位。"}
        missing = [s for s in SIDES if s not in self._pending]
        if missing:
            return {"ok": False, "error": f"双方提交未齐备，缺：{missing}。"}
        dec_a, dec_b = self._pending.pop("a"), self._pending.pop("b")
        events, need_side = resolve_turn(self._state, dec_a, dec_b)
        if need_side is not None:
            self._need_replace = need_side
        else:
            events += end_turn(self._state)
        return {
            "ok": True,
            "need_replacement": need_side,
            "events": events,
            "done": self._state.done,
            "winner": self._state.winner,
            "state_hash": self._state.state_hash(),
            "observation": self.observe(),
        }

    def submit_replacement(self, side: str, bench_idx: int) -> dict:
        """阵亡方玩家提交补位选择：应用补位 → 回合末收尾（回合号推进 / battle_end）。

        非法 → {"ok": False, "error": …}，state 不变、回合未推进。
        """
        if self._need_replace is None:
            return {"ok": False, "error": "当前没有等待补位。"}
        if side != self._need_replace:
            return {"ok": False, "error": f"当前等待 {self._need_replace} 方补位，不是 {side}。"}
        reason = validate_replacement(self._state, side, bench_idx)
        if reason is not None:
            return {"ok": False, "error": reason}
        events = apply_replacement(self._state, side, bench_idx)
        self._need_replace = None
        events += end_turn(self._state)
        return {
            "ok": True,
            "need_replacement": None,
            "events": events,
            "done": self._state.done,
            "winner": self._state.winner,
            "state_hash": self._state.state_hash(),
            "observation": self.observe(),
        }

    def observe(self, side: str = "") -> dict:
        """E0 对称全量观测（迷雾在 E4）。传入 side 时带上己方标识。"""
        d = self._state.to_dict()
        if side:
            d["side"] = side
        return d

    def view(self, side: str) -> dict:
        """E4 迷雾观测：玩家视角（己方全见、敌方按白名单屏蔽，见 view.py）。

        引擎持有完整状态推进；给玩家的展示经这里。非法 side → ValueError（调用方传错即暴露）。
        """
        from .view import observe
        return observe(self._state, side, mode="partial")
