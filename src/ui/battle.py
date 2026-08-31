"""战斗编排：人类(a) vs LLM（假LLM/随机）(b) 的一个进行中对局。

引擎持有完整状态推进（`BattleSession`）；给人类的展示经 E4 `view`/`filter_events_for`
屏蔽（己方全见、敌方白名单）。回合流程：人类 `act` → 假LLM `decide` → 双方 `submit` →
`resolve` →（阵亡：a 暂停等人类 `replace`，b 自动代打）→ `end_turn` 收尾。
逐回合记录 `(decisions, replacements, llm_reply, events, state_hash)`，供 `record()` 落盘、
重放复现（马尔可夫不变式的可执行验证）。

阶段机：`decision` →（人类在场阵亡时）`replacement` → `decision` / `done`。
单用户工具：模块级注册表 `_BATTLES` 持有一个进行中的对局，`battle_id` 作句柄；
非线程安全由每实例一把锁兜住（人类是唯一主动方，并发极低）。
"""

from __future__ import annotations

import threading
from dataclasses import asdict, fields

from environment.actions import Decision, boss_evolution_options, recharge_action
from environment.datafingerprint import data_digest, rules_digest
from environment.rules import BattleRules
from environment.session import BattleSession
from environment.visibility import filter_events_for

from roco_pvp_agent.battle.player import FakeLLMPlayer


def _rules_dict(r: BattleRules) -> dict:
    """BattleRules → 纯 dict（落盘用，重放时 `BattleRules(**d)` 原样还原）。"""
    return {f.name: getattr(r, f.name) for f in fields(BattleRules)}


class BattleController:
    """一个进行中的对局。构造时已开局；`act`/`replace` 推进，`snapshot` 只读。"""

    PHASE_STARTER = "starter"      # 第 0 回合：选首发（2026-08-30）
    PHASE_DECISION = "decision"
    PHASE_REPLACEMENT = "replacement"
    PHASE_DONE = "done"

    def __init__(self, battle_id: str, session: BattleSession, *, seed: int,
                 opponent: str, team_a: list[dict], team_b: list[dict],
                 rules: BattleRules, saved_at: str, player=None,
                 items_a: list[str] | None = None, items_b: list[str] | None = None) -> None:
        if opponent not in ("fake_llm", "random"):
            raise ValueError(f"未知对手类型「{opponent}」（fake_llm | random）。")
        self.battle_id = battle_id
        self._session = session
        self._seed = seed
        self._opponent = opponent
        self._team_a = team_a
        self._team_b = team_b
        self._rules = rules
        self._saved_at = saved_at
        self._items_a = items_a
        self._items_b = items_b
        self._lock = threading.Lock()
        self._phase = self.PHASE_STARTER    # 第 0 回合：先选首发（2026-08-30）
        self._turns: list[dict] = []
        self._cur: dict | None = None        # 人类补位暂停中的本回合累积
        self._starters: dict[str, int] = {}  # 第 0 回合首发（side → 槽位）
        # 对手玩家：独立 RNG 流（seed+1），绝不共用引擎的流；`player` 为测试注入缝
        if player is not None:
            self._player = player
        elif opponent == "fake_llm":
            self._player = FakeLLMPlayer("b", seed=seed + 1)
        else:
            from environment.players import RandomPlayer
            self._player = RandomPlayer("b", seed=seed + 1)
        self._player.on_match_start(session.view("b"))

    # ── 只读属性 ──
    @property
    def phase(self) -> str:
        return self._phase

    @property
    def done(self) -> bool:
        return self._session.state.done

    @property
    def winner(self) -> str | None:
        return self._session.state.winner

    @property
    def seed(self) -> int:
        return self._seed

    # ── 对外动作 ──
    def snapshot(self) -> dict:
        """只读快照（无新事件）。人类刷新页面用。"""
        with self._lock:
            return self._snapshot_locked([], "", None)

    def choose_starter(self, bench_idx: int) -> dict:
        """第 0 回合（2026-08-30）：人类选首发 → 假LLM 选首发 → 双方首发触发入场效果
        → 进入出招阶段。非法 → `{"ok": False, "error": …}`，零状态变更。"""
        with self._lock:
            if self._phase != self.PHASE_STARTER:
                return {"ok": False, "error": "当前不在首发选择阶段。"}
            r = self._session.choose_starter("a", bench_idx)
            if not r["ok"]:
                return {"ok": False, "error": r["error"]}
            # 假LLM 选首发（随机，独立 RNG 流，不碰引擎流）
            options = self._session.starter_options("b")
            idx = self._player.choose_starter(self._session.view("b"), options)
            if not self._session.choose_starter("b", idx)["ok"]:
                self._session.choose_starter("b", options[0])
            entry = self._session.start_entry()
            self._starters = dict(self._session.starters)
            self._phase = self.PHASE_DECISION
            return self._snapshot_locked(
                filter_events_for("a", entry.get("events", []), self._session.state), "", 0)

    def act(self, action: dict, item: str = "", item_arg: str = "") -> dict:
        """人类提交本回合行动 → 假LLM 决定 → 双方同时结算 → 返回人类视角快照。
        非法行动 → `{"ok": False, "error": …}`，零状态变更。
        `item_arg`：首领进化多分支时的分支精灵名（单分支可空）。"""
        with self._lock:
            if self._phase == self.PHASE_DONE:
                return {"ok": False, "error": "对局已结束。"}
            if self._phase == self.PHASE_REPLACEMENT:
                return {"ok": False, "error": "正在等待补位。"}
            if self._phase == self.PHASE_STARTER:
                return {"ok": False, "error": "请先选择首发。"}
            dec_a = Decision(action=action, item=item or "", item_arg=item_arg or "")
            r = self._session.submit("a", dec_a)
            if not r["ok"]:
                return {"ok": False, "error": r["error"]}
            # 假LLM：以迷雾口径观测（随机策略不读，但缝是对的——真实 LLM 也只该看到白名单）
            dec_b = self._player.decide(
                self._session.view("b"),
                self._session.legal_actions("b"),
                self._session.legal_items("b"),
            )
            r = self._session.submit("b", dec_b)
            if not r["ok"]:                       # 防御：玩家写错 → 聚能兜底（不该发生）
                dec_b = Decision(recharge_action())
                self._session.submit("b", dec_b)
            reply = getattr(self._player, "last_reply", "")
            return self._advance(dec_a, dec_b, reply)

    def replace(self, bench_idx: int) -> dict:
        """人类补位选择（己方在场阵亡后）：应用补位 → 回合末收尾 → 返回快照。

        `events` 只含**本步新增**的事件（补位 + 回合末收尾）——出招步已展示过该回合前半段，
        前端据此追加日志而不重复。完整事件累积在 `cur["events"]` 里供记录/重放。
        """
        with self._lock:
            if self._cur is None:
                return {"ok": False, "error": "当前没有等待补位。"}
            r = self._session.submit_replacement("a", bench_idx)
            if not r["ok"]:
                return {"ok": False, "error": r["error"]}
            cur = self._cur
            cur["replaces"]["a"] = bench_idx
            cur["events"] += r["events"]                       # 完整轨迹（记录/重放用）
            self._phase = self.PHASE_DONE if self._session.state.done else self.PHASE_DECISION
            self._finalize_turn(cur)
            self._cur = None
            return self._snapshot_locked(
                filter_events_for("a", r["events"], self._session.state),
                cur["llm_reply"], cur["turn"])

    def record(self) -> dict:
        """完整对局记录（落盘/重放用）：配置 + seed + 双方队伍 + 道具 + 逐回合轨迹。

        `items_a/items_b` 进记录：重放按同一道具栏重建，否则非默认道具的对局逐回合
        state_hash 失配（初始 item_uses 不一致）。"""
        return {
            "version": 1,
            "battle_id": self.battle_id,
            "saved_at": self._saved_at,
            "seed": self._seed,
            "opponent": self._opponent,
            "rules": _rules_dict(self._rules),
            "data_digest": data_digest(),
            "rules_digest": rules_digest(),
            "team_a": self._team_a,
            "team_b": self._team_b,
            "items_a": self._items_a,
            "items_b": self._items_b,
            "starters": self._starters,
            "winner": self.winner,
            "done": self.done,
            "turns": self._turns,
        }

    # ── 内部 ──
    def _advance(self, dec_a: Decision, dec_b: Decision, reply: str) -> dict:
        """双方已入缓冲 → 结算到回合结束（含自动代打 b 的补位），返回人类视角快照。
        a 需要补位 → 暂停（phase=replacement，等人类 replace）。"""
        s = self._session
        turn_no = s.state.turn                    # 提交时的回合号（TurnRecord.turn 语义）
        all_events: list[dict] = []
        res = s.resolve()
        if not res["ok"]:
            return {"ok": False, "error": res["error"]}
        all_events += res["events"]
        replaces: dict[str, int | None] = {"a": None, "b": None}
        while res.get("need_replacement"):
            side = res["need_replacement"]
            if side == "a":
                self._phase = self.PHASE_REPLACEMENT
                self._cur = {
                    "turn": turn_no, "dec_a": dec_a, "dec_b": dec_b, "llm_reply": reply,
                    "events": all_events, "replaces": replaces,
                }
                return self._snapshot_locked(filter_events_for("a", all_events, s.state), reply, turn_no)
            options = s.replacement_options("b")
            idx = self._player.choose_replacement(s.view("b"), options)
            r = s.submit_replacement("b", idx)
            if not r["ok"]:
                idx = options[0]
                r = s.submit_replacement("b", idx)
            replaces["b"] = idx
            all_events += r["events"]
            res = r
        self._phase = self.PHASE_DONE if s.state.done else self.PHASE_DECISION
        self._finalize_turn({
            "turn": turn_no, "dec_a": dec_a, "dec_b": dec_b, "llm_reply": reply,
            "events": all_events, "replaces": replaces,
        })
        return self._snapshot_locked(filter_events_for("a", all_events, s.state), reply, turn_no)

    def _finalize_turn(self, cur: dict) -> None:
        """回合完成 → 追加 TurnRecord 并通知对手玩家。"""
        self._turns.append({
            "turn": cur["turn"],
            "decision_a": asdict(cur["dec_a"]),
            "decision_b": asdict(cur["dec_b"]),
            "replace_a": cur["replaces"]["a"],
            "replace_b": cur["replaces"]["b"],
            "llm_reply": cur["llm_reply"],
            "events": cur["events"],
            "state_hash": self._session.state.state_hash(),
        })
        self._player.on_turn_result(
            self._session.view("b"), filter_events_for("b", cur["events"], self._session.state))

    def _snapshot_locked(self, events: list[dict], llm_reply: str, events_turn: int | None) -> dict:
        """人类视角快照：观测（迷雾）+ 合法池 + 本回合事件（已过滤）。

        `events_turn`：这批事件所属的回合号（补位续步与出招步同回合）；无新事件（纯刷新）→ None。
        """
        s = self._session
        decision_phase = self._phase == self.PHASE_DECISION
        return {
            "ok": True,
            "battle_id": self.battle_id,
            "seed": self._seed,
            "turn": s.state.turn,
            "phase": self._phase,
            "done": s.state.done,
            "winner": s.state.winner,
            "events": events,
            "events_turn": events_turn,
            "llm_reply": llm_reply,
            "need_replacement": "a" if self._phase == self.PHASE_REPLACEMENT else None,
            "observation": s.view("a"),
            "legal": s.legal_actions("a") if decision_phase else [],
            "legal_items": s.legal_items("a") if decision_phase else [],
            "boss_options": boss_evolution_options(s.state, "a") if decision_phase else [],
            "starter_options": s.starter_options("a") if self._phase == self.PHASE_STARTER else [],
            "state_hash": s.state.state_hash(),
        }


# ── 进程内对局注册表（单用户本地工具：无 TTL，done 后保留可查/可回放） ──
_BATTLES: dict[str, BattleController] = {}


def register(ctrl: BattleController) -> None:
    """登记一个进行中的对局（battle_id 作句柄）。"""
    _BATTLES[ctrl.battle_id] = ctrl


def get_controller(battle_id: str) -> BattleController | None:
    """按 battle_id 取对局；不存在 → None（路由 404）。"""
    return _BATTLES.get(battle_id)


# 供路由层引用 `observe` 之外的工具（避免重复导入）。
__all__ = ["BattleController", "register", "get_controller"]
