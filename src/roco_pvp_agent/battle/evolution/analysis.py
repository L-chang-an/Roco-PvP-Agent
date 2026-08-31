"""R0 轨迹重放分析：一条自博弈记录 → 逐回合 `TurnAnalysis`。

`analyze_record(record)` 自己驱动一场 replay（不开网络、不调 LLM）：
- **马尔可夫不变式现场验证**：逐回合重放比对 `state_hash`，与 `environment.replay`
  同一套纪律——任何一回合失配即停（`replay_ok=False`）。
- **逐回合局面快照**：迷雾 view()×2、situation_key×2、v_heuristic×2、双方提交、
  补位、全量事件（含按方过滤后的 `events_a/b`，即玩家视角口径）。
- **确定性**：同一记录两次分析逐字段相同（引擎纯转移 + 固定 seed）。

`events_a/events_b` 用 `filter_events_for` 按方过滤（与 `run_match.on_turn_result`
同一口径）——分析管线（R2 信度分配、R3 反思）消费的是玩家视角，结构上碰不到
敌方隐藏字段（绝对血量等），延续 E6.5 的迷雾收口。

本模块**不修改** `environment` 任何文件——replay 纪律在 agent 层再实现一份，
E 线的不变式（engine 零改动）不被动摇。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from environment.actions import Decision
from environment.evaluate import situation_key, v_heuristic
from environment.models import SIDES
from environment.rules import BattleRules
from environment.session import BattleSession
from environment.visibility import filter_events_for

_RECORD_REQUIRED = ("rules", "team_a", "team_b", "seed", "battle_id", "turns")


@dataclass
class TurnAnalysis:
    """一回合的完整分析快照（R2 信度分配 / R3 反思的唯一输入形状）。

    迷雾口径：`events_a/b` 已按方过滤（玩家视角），`situation_keys`/`decisions`
    只消费 view() 白名单。`v_before/after` 是 **full-state 口径**的离线价值估计
    （§3.3 刻意用完整状态扫全场，绝不注入对战玩家）；`events` 是全量原始事件，
    只作离线审计用，消费方一律读 `events_a/b`。
    `prediction` 是 battle_act 加 prediction（R2）后的逐回合预测文本，R0 恒为 None。
    """

    turn: int
    views: dict[str, dict]               # 回合前迷雾 view()：{"a": …, "b": …}
    decisions: dict[str, dict]           # 实际提交的主动作 dict：{"a": {type,value}, …}
    items: dict[str, str]                # 道具：{"a": 道具名|"", …}
    replaces: dict[str, int | None]      # 补位槽位：{"a": int|None, …}
    events: list[dict]                   # 全量事件（离线审计用，含绝对血量等）
    events_a: list[dict]                 # 过滤后（a 玩家视角）
    events_b: list[dict]                 # 过滤后（b 玩家视角）
    situation_keys: dict[str, str]       # 回合前 situation_key：{"a": …, "b": …}
    v_before: dict[str, float]           # 回合前 v_heuristic（full-state 口径）
    v_after: dict[str, float]            # 回合后 v_heuristic（full-state 口径）
    prediction: dict[str, str | None] = field(
        default_factory=lambda: {"a": None, "b": None})   # R2 起：battle_act 预测文本
    state_hash: str = ""                 # 回合末 state_hash（与记录比对）
    winner: str | None = None            # 终局胜方（局末统一填充）
    foe_revealed: dict[str, list[str | None]] = field(default_factory=dict)
    # 对手在场单位技能按原始槽位的已揭示名（side a 看 b、side b 看 a）；未揭示 → None。
    # 渲染对手行动时用它（白名单过滤子集丢原始下标，不能直接对过滤列表取值）。


@dataclass
class TrajectoryAnalysis:
    """一条轨迹的完整分析：元信息 + 逐回合。"""

    record: dict
    turns: list[TurnAnalysis] = field(default_factory=list)
    winner: str | None = None
    turn_count: int = 0
    replay_ok: bool = True

    def digest(self) -> str:
        """整局指纹 = 逐回合 state_hash 拼接的 sha256（与 match.MatchResult.digest 同口径）。"""
        import hashlib
        h = hashlib.sha256()
        for t in self.turns:
            h.update(t.state_hash.encode("utf-8"))
        return h.hexdigest()


def _validate_record(record: dict) -> None:
    """缺必需键 → ValueError（宁失败不抛，与 replay_record 同纪律）。

    顶层键 + 逐回合形状（decision_a/b 的 action dict、turn/state_hash、
    replace_a/b 为 int|None）都校验——畸形回合当场报错，而不是让
    `evolve reflect` 抛裸 KeyError/TypeError。
    """
    missing = [k for k in _RECORD_REQUIRED if k not in record]
    if missing:
        raise ValueError(f"轨迹记录缺少必需键：{missing}（应有 {list(_RECORD_REQUIRED)}）。")
    for i, tr in enumerate(record["turns"], start=1):
        if not isinstance(tr, dict):
            raise ValueError(f"轨迹记录第 {i} 回合不是 dict。")
        for key in ("turn", "decision_a", "decision_b", "state_hash"):
            if key not in tr:
                raise ValueError(f"轨迹记录第 {i} 回合缺少必需键「{key}」。")
        for side in SIDES:
            dec = tr[f"decision_{side}"]
            if not isinstance(dec, dict) or not isinstance(dec.get("action"), dict):
                raise ValueError(
                    f"轨迹记录第 {i} 回合 decision_{side} 形状非法（应为 {{action, item}}）。")
        for key in ("replace_a", "replace_b"):
            if tr.get(key) is not None and not isinstance(tr[key], int):
                raise ValueError(f"轨迹记录第 {i} 回合 {key} 应为 int|None，实际 {tr[key]!r}。")


def _replay_one_turn(session, tr: dict) -> tuple[list[dict], dict[str, int | None], bool]:
    """按记录驱动一个回合：submit 双方 → resolve →（阵亡则补位）。

    与 `drive_turn` 同构但不调玩家（决策/补位直接从记录读）。返回
    (全量事件, 补位 dict, ok)。补位非法 / 提交非法 → ok=False（失配信号）。
    """
    ok = bool(session.submit("a", Decision(action=tr["decision_a"]["action"],
                                           item=tr["decision_a"].get("item", "")))["ok"])
    ok &= bool(session.submit("b", Decision(action=tr["decision_b"]["action"],
                                            item=tr["decision_b"].get("item", "")))["ok"])
    events: list[dict] = []
    replaces: dict[str, int | None] = {"a": None, "b": None}
    if not ok:
        return events, replaces, False
    res = session.resolve()
    ok &= bool(res.get("ok"))
    if not ok:
        return events, replaces, False
    events += res["events"]
    while res.get("need_replacement"):
        side = res["need_replacement"]
        bench = tr.get(f"replace_{side}")
        if bench is None:
            return events, replaces, False   # 记录没有补位输入 → 失配
        r = session.submit_replacement(side, bench)
        ok &= bool(r.get("ok"))
        if not ok:
            return events, replaces, False
        replaces[side] = bench
        events += r["events"]
        res = r
    return events, replaces, True


def _revealed_names_for(state, viewer: str) -> list[str | None]:
    """viewer 视角下对手在场单位技能的已揭示名（按原始槽位对齐，未揭示 → None）。

    雾霾白名单 `_unit_masked` 只给已揭示技能的**过滤子集**（丢原始下标），渲染对手
    技能名若直接对过滤列表按下标取值会错位或越界。这里用完整状态里对手自己的
    `revealed` 集重建**按原始槽位对齐**的已揭示名——正是玩家视角能知道的名字集合。
    """
    foe_state = state.foe(viewer)
    active_idx = foe_state.active
    unit = foe_state.active_unit
    revealed = foe_state.revealed
    return [s.name if (active_idx, s.name) in revealed else None for s in unit.skills]


def analyze_record(record: dict, *, value_fn=None) -> TrajectoryAnalysis:
    """重放一条轨迹，逐回合产出 TurnAnalysis（马尔可夫不变式现场验证）。

    任意一回合 state_hash 失配即停（后续回合状态已偏离、分析无意义），
    `replay_ok=False`。**终局校验**：记录声称 done 但重放未到终局（截断/篡改），
    或 winner 与重放终局不符 → `replay_ok=False`。返回 TrajectoryAnalysis。

    `value_fn`（R5 档位 A）：可选的 V 提供者 `(state, side) -> float`；None → `v_heuristic`
    （R0 启发式）。V 是**离线反思/信度分配**口径（`show_v=False` 的玩家路径永不注入）。
    """
    _validate_record(record)
    rules = BattleRules(**record["rules"])
    session = BattleSession.start(record["team_a"], record["team_b"], seed=record["seed"],
                                  rules=rules, battle_id=record["battle_id"])
    vfn = (value_fn.value if value_fn is not None else v_heuristic)

    turns: list[TurnAnalysis] = []
    replay_ok = True
    for tr in record["turns"]:
        views = {s: session.view(s) for s in SIDES}
        sk = {s: situation_key(views[s]) for s in SIDES}
        vb = {s: vfn(session.state, s) for s in SIDES}
        foe_revealed = {s: _revealed_names_for(session.state, s) for s in SIDES}

        events, replaces, ok = _replay_one_turn(session, tr)

        state = session.state
        match = ok and state.state_hash() == tr["state_hash"]
        replay_ok &= match
        turns.append(TurnAnalysis(
            turn=tr["turn"],
            views=views,
            decisions={"a": dict(tr["decision_a"]["action"]), "b": dict(tr["decision_b"]["action"])},
            items={"a": tr["decision_a"].get("item", ""), "b": tr["decision_b"].get("item", "")},
            replaces=replaces,
            events=events,
            events_a=filter_events_for("a", events, state),
            events_b=filter_events_for("b", events, state),
            situation_keys=sk,
            v_before=vb,
            v_after={s: vfn(state, s) for s in SIDES},
            prediction={"a": tr.get("prediction_a"), "b": tr.get("prediction_b")},
            state_hash=state.state_hash(),
            winner=None,
            foe_revealed=foe_revealed,
        ))
        if not match:
            break   # 已偏离，后续回合无意义（与 replay_record 同策略）

    # 终局一致性：记录声称 done 但重放未到终局 / winner 不符 → 截断或篡改
    if record.get("done"):
        if not session.state.done:
            replay_ok = False
        elif record.get("winner") is not None and session.state.winner != record.get("winner"):
            replay_ok = False

    winner = record.get("winner") or session.state.winner
    for t in turns:
        t.winner = winner
    return TrajectoryAnalysis(record=record, turns=turns, winner=winner,
                              turn_count=len(turns), replay_ok=replay_ok)


def dump_analysis_json(analysis: TrajectoryAnalysis) -> dict:
    """TrajectoryAnalysis → 纯 JSON dict（落盘/汇报用，`json.dumps` 可直接序列化）。

    只含逐回合的分析派生量（局面键 / 价值 / 提交 / 事件），不重复整份 record。
    """
    return {
        "battle_id": analysis.record.get("battle_id", ""),
        "winner": analysis.winner,
        "turn_count": analysis.turn_count,
        "replay_ok": analysis.replay_ok,
        "digest": analysis.digest(),
        "turns": [
            {
                "turn": t.turn,
                "situation_keys": t.situation_keys,
                "v_before": t.v_before,
                "v_after": t.v_after,
                "decisions": t.decisions,
                "items": t.items,
                "replaces": t.replaces,
                "state_hash": t.state_hash,
            }
            for t in analysis.turns
        ],
    }


def analyze_record_file(path: str) -> TrajectoryAnalysis:
    """从 JSON 文件读记录 → analyze_record。`evolve reflect` CLI 用。"""
    with open(path, encoding="utf-8") as f:
        record = json.load(f)
    return analyze_record(record)
