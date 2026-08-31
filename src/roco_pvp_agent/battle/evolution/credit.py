"""R2 信度分配：把「这局输了」归到具体回合（§6.2 三信号交集）。

- **信号 1 校准偏差 ★（免费）**：`battle_act` 的 `prediction`（预期结果文本）vs 实际事件，
  离线比对 → 「算错了 vs 运气差」。零额外调用成本（决策 3 拍板）。
- **信号 2 价值落差（便宜）**：`V_heuristic` 扫全场，标 `V(s_t) − V(s_{t+1})` 下降最大的 top-k。
- **信号 3 反事实回放（贵，产生真标签）**：fork 候选回合前状态 → **代理尾策略**
  （`RandomPlayer`，固定 seed 集）驱动双方打完剩余回合 M=24 次 →
  `delta_winrate = E[outcome(替代动作)] − E[outcome(原动作)]`（原动作也走同一代理尾，自洽基线）。

关键回合卡片（§6.2 形状）是 R3 反思的唯一输入。**代理尾是局面级动作评估**：
不完美（代理 ≠ LLM）但确定、可复现、便宜（纯引擎、零 token）。
"""

from __future__ import annotations

import re

from environment.actions import Decision, legal_actions
from environment.match import run_match
from environment.models import SIDES
from environment.players import RandomPlayer
from environment.rules import BattleRules
from environment.session import BattleSession

from roco_pvp_agent.battle.evolution.analysis import _replay_one_turn
from roco_pvp_agent.battle.evolution.feedback import render_feedback

# 价值落差 / delta_winrate 的「显著」阈值（低于它不标记，防噪声灌给反思器）。
# DELTA_EPS 校准到 M=24 噪声地板之上：M 次伯努利胜率的 delta 标准差 ~0.07–0.14
# （配对比较略降），0.02 完全在噪声内会大量假阳性；0.10 大约是 1 个配对 SE 的量级，
# 低于它的反事实差异视为「无显著更好动作」。M 更大（--m）时噪声更低，可下调。
DROP_EPS = 0.02
DELTA_EPS = 0.10


# ---------------------------------------------------------------------------
# 信号 1：校准偏差（prediction vs 实际事件）
# ---------------------------------------------------------------------------


def _side_damage_done(events: list[dict], side: str) -> int:
    """该方在本回合造成的伤害总和（filtered 事件的 damage 事件）。"""
    return sum(e.get("damage", 0) for e in events
               if e.get("type") == "damage" and e.get("side") == side)


def calibration_miss(prediction: str, events: list[dict], side: str, *, tol: float = 0.3) -> bool:
    """预测文本里的数字 vs 实际造成伤害：偏差 > `tol` → True（校准偏差信号）。

    确定性启发式（R2 起步）：从 `prediction` 抽全部整数，与该方实际伤害总和比对
    （取最接近的一个）；无数字 → False（LLM 没给可校准的量化预期）；实际为 0 但
    预测了伤害（或反之）→ True。R3 反思时以更细的语义解析覆盖。
    """
    preds = [int(m) for m in re.findall(r"\d+", prediction or "")]
    if not preds:
        return False
    actual = _side_damage_done(events, side)
    if actual <= 0:
        return True
    closest = min(preds, key=lambda p: abs(p - actual))
    return abs(closest - actual) / actual > tol


# ---------------------------------------------------------------------------
# 信号 3：反事实回放（代理尾策略，确定性、纯引擎）
# ---------------------------------------------------------------------------


class _OverrideFirstPlayer:
    """首个 `decide` 返回 override_action（无则委托代理），之后全部委托代理策略。

    用于反事实仿真：候选回合该方被强制走替代动作，后续回合与对方全走代理尾。
    """

    def __init__(self, side: str, override: Decision | None, inner) -> None:
        self.side = side
        self.kind = f"override_{getattr(inner, 'kind', 'proxy')}"
        self._override = override
        self._inner = inner
        self._used = override is None      # 无 override → 全程委托

    def on_match_start(self, observation: dict) -> None:
        self._inner.on_match_start(observation)

    def decide(self, observation: dict, legal: list[dict], items: list[str]):
        if not self._used:
            self._used = True
            return self._override
        return self._inner.decide(observation, legal, items)

    def choose_replacement(self, observation: dict, bench: list[int]) -> int:
        return self._inner.choose_replacement(observation, bench)

    def on_turn_result(self, observation: dict, events: list[dict]) -> None:
        self._inner.on_turn_result(observation, events)


def _simulate(state, side: str, first_action: Decision, *, proxy_seed: int) -> str | None:
    """从给定状态 fork，候选回合该方用 `first_action`，之后双方用 RandomPlayer
    代理尾驱动到 done。返回胜方（a/b/None）。确定性：同 state + 同 seed → 同结果。"""
    players = {
        "a": _OverrideFirstPlayer("a", first_action if side == "a" else None,
                                  RandomPlayer("a", seed=proxy_seed)),
        "b": _OverrideFirstPlayer("b", first_action if side == "b" else None,
                                  RandomPlayer("b", seed=proxy_seed + 1)),
    }
    session = BattleSession(state.clone())
    return run_match(session, players).winner


def _eval_action(state, side: str, action: Decision, *, M: int, seed_base: int) -> float:
    """一个动作在代理尾下的胜率：M 次不同 seed 仿真。确定性。"""
    wins = 0
    for i in range(M):
        winner = _simulate(state, side, action, proxy_seed=seed_base + i * 2)
        wins += 1 if winner == side else 0
    return wins / M if M else 0.0


def counterfactual(state, side: str, chosen: Decision, alternatives: list[Decision], *,
                   M: int = 24, seed_base: int = 0) -> dict:
    """fork 候选回合前状态 → 代理尾评估原动作与每替代动作的胜率。

    返回 §6.2 卡片所需的反事实部分：chosen / chosen_winrate / alternatives
    （各带 winrate 与 delta）/ counterfactual_better（delta > `DELTA_EPS` 的最佳替代）/ delta_winrate。
    `delta` 从**舍入后**的胜率计算（`round(wr,4) − round(base,4)`），保证
    `alt.delta == alt.winrate − chosen_winrate` 严格成立（无双重舍入偏差）。
    """
    base_r = round(_eval_action(state, side, chosen, M=M, seed_base=seed_base), 4)
    alts: list[dict] = []
    for alt in alternatives:
        wr_r = round(_eval_action(state, side, alt, M=M, seed_base=seed_base), 4)
        alts.append({"action": dict(alt.action), "item": alt.item,
                     "winrate": wr_r, "delta": wr_r - base_r})   # 不二次舍入：恒等成立
    best = max(alts, key=lambda x: x["delta"], default=None)
    return {
        "chosen": dict(chosen.action),
        "chosen_winrate": base_r,
        "n_replays": M,
        "alternatives": alts,
        "counterfactual_better": best["action"] if best and best["delta"] > DELTA_EPS else None,
        "delta_winrate": best["delta"] if best else 0.0,
    }


class CounterfactualCache:
    """反事实回放结果缓存：按 (record_digest, turn_no, side, M) 共享（§十 前缀复用）。

    key 含 **record_digest**（轨迹指纹）——同一 battle_id 的多个记录（不同对局）
    不会串缓存；`mine_critical_turns` 重复跑（--repeat）命中缓存加速，但真正的
    代理确定性由「同输入重跑逐位一致」验证（--repeat 用新 cache 时）。
    """

    def __init__(self) -> None:
        self._store: dict[tuple, dict] = {}

    @staticmethod
    def key(record_digest: str, turn_no: int, side: str, M: int) -> tuple:
        return (record_digest, turn_no, side, M)

    def get(self, key: tuple) -> dict | None:
        return self._store.get(key)

    def put(self, key: tuple, value: dict) -> None:
        self._store[key] = value


# ---------------------------------------------------------------------------
# 候选回合选择（先便宜信号定位，再贵反事实确认）
# ---------------------------------------------------------------------------


def _state_before_turn(record: dict, turn_no: int):
    """重放回合 1..turn_no−1，返回 turn_no 回合前的 BattleState（供 fork）。

    与 `analyze_record` 同一套重放纪律（逐回合 state_hash 校验），不可信记录抛错。
    """
    rules = BattleRules(**record["rules"])
    session = BattleSession.start(record["team_a"], record["team_b"], seed=record["seed"],
                                  rules=rules, battle_id=record["battle_id"])
    for tr in record["turns"][:turn_no - 1]:
        _, _, ok = _replay_one_turn(session, tr)
        if not ok or session.state.state_hash() != tr["state_hash"]:
            raise ValueError(f"重放到第 {tr['turn']} 回合失配：反事实 fork 不可信。")
    return session.state


def _select_alternatives(state, side: str, chosen: dict, *, cap: int = 6) -> list[Decision]:
    """候选回合的合法替代动作（确定性排序，上限 `cap`）。

    优先换人/聚能（结构差异大、最可能是「该改的」），再攻击技；不含原动作。
    """
    rest = [a for a in legal_actions(state, side) if a != chosen]

    def _key(a: dict):
        t = a["type"]
        return (0 if t == "switch" else 1 if t == "recharge" else 2, str(a))

    rest.sort(key=_key)
    return [Decision(action=a) for a in rest[:cap]]


def _join_turn_logs(record: dict) -> dict[str, dict[int, dict]]:
    """record 的 analysis_a/b（LLMPlayer._turn_log）→ {side: {turn: entry}}。"""
    out: dict[str, dict[int, dict]] = {"a": {}, "b": {}}
    for side in SIDES:
        log = record.get(f"analysis_{side}") or []
        for e in log:
            if isinstance(e, dict) and "turn" in e:
                out[side][e["turn"]] = e
    return out


def _calibration_info(turn_logs: dict, ta, side: str) -> tuple[bool, str, int]:
    """该回合该方的校准信息：`(miss, prediction, actual_damage)`。

    从 record 的 analysis_a/b（LLMPlayer._turn_log）取 prediction，与实际该方伤害比对。
    """
    entry = turn_logs[side].get(ta.turn)
    if not entry or not entry.get("prediction"):
        return False, "", 0
    prediction = entry["prediction"]
    events = ta.events_a if side == "a" else ta.events_b
    actual = _side_damage_done(events, side)
    return calibration_miss(prediction, events, side), prediction, actual


def _module_attribution(ta, side: str) -> str:
    """初判（R3 细化）：任一方剩 1 命 → M4 endgame；聚能 → M3；换人/默认 → M2。"""
    sk = ta.situation_keys[side].split("/")
    if len(sk) == 10 and (sk[0] == "my1" or sk[1] == "foe1"):
        return "M4 endgame"
    action = ta.decisions[side]
    if action.get("type") == "recharge":
        return "M3 energy_planner"
    return "M2 action_selector"


def mine_critical_turns(analysis, record: dict, *, top_k: int = 5,
                        M: int = 24, cache: CounterfactualCache | None = None) -> list[dict]:
    """三信号交集 → 关键回合卡片（§6.2 形状，R3 反思的唯一输入）。

    **replay_ok=False（重放失配/截断）→ 抛 ValueError**（宁失败不抛——不可信轨迹
    产出的卡片会误导 R3 反思）。候选 = **价值落差 top-k ∪ 校准偏差命中**（两个便宜
    信号各自独立定位，§6.2 校准是独立信号 1）。仅对候选跑反事实确认。
    `cache` 传入则按 (轨迹指纹, turn_no, side, M) 复用。确定性：同 record 两次
    产出逐字段相同。
    """
    if not analysis.replay_ok:
        raise ValueError(
            f"轨迹重放失配（replay_ok=False）：不可信，拒绝产出关键回合卡片。"
            f"检查轨迹完整性后重试。")
    cache = cache or CounterfactualCache()
    turn_logs = _join_turn_logs(record)
    trajectory_id = record.get("battle_id", "")
    digest = analysis.digest()[:16]          # 轨迹指纹 → 缓存 key 隔离不同记录
    by_turn = {ta.turn: ta for ta in analysis.turns}   # turn → TurnAnalysis（不假设连续）
    cards: list[dict] = []
    for side in SIDES:
        scored = []
        for ta in analysis.turns:
            drop = ta.v_before[side] - ta.v_after[side]
            miss, _, _ = _calibration_info(turn_logs, ta, side)
            scored.append((ta.turn, drop, miss))
        scored.sort(key=lambda x: -x[1])
        # 候选 = 价值落差 top_k ∪ 校准偏差命中（两个信号各自独立定位候选）
        top_drop = {t for t, _, _ in scored[:top_k]}
        cal_misses = {t for t, _, m in scored if m}
        for turn_no in sorted(top_drop | cal_misses):
            ta = by_turn[turn_no]
            drop = next(d for t, d, _ in scored if t == turn_no)
            miss, prediction, actual = _calibration_info(turn_logs, ta, side)
            if drop <= DROP_EPS and not miss:
                continue                     # 无任何信号 → 不喂噪声给反思器
            state = _state_before_turn(record, turn_no)
            chosen = Decision(action=dict(ta.decisions[side]), item=ta.items[side])
            alts = _select_alternatives(state, side, chosen.action)
            key = CounterfactualCache.key(digest, turn_no, side, M)
            cf = cache.get(key) or counterfactual(state, side, chosen, alts, M=M,
                                                  seed_base=turn_no * 1000 + len(trajectory_id))
            if cache.get(key) is None:
                cache.put(key, cf)
            signals: list[str] = []
            if miss:
                signals.append("calibration_miss")
            if drop > DROP_EPS:
                signals.append("value_drop")
            if cf["counterfactual_better"] is not None:
                signals.append("counterfactual_confirmed")
            calibration = {"prediction": prediction, "actual": actual, "miss": miss} \
                if prediction else None
            cards.append({
                "trajectory_id": trajectory_id,
                "turn_no": turn_no,
                "side": side,
                "situation_key": ta.situation_keys[side],
                "chosen": dict(ta.decisions[side]),
                "counterfactual_better": cf["counterfactual_better"],
                "delta_winrate": cf["delta_winrate"],
                "n_replays": M,
                "signals": signals,
                "feedback_text": render_feedback(ta, side=side, prediction=prediction,
                                                 counterfactual=cf, calibration=calibration),
                "module_attribution": _module_attribution(ta, side),
            })
    # 关键错误优先：delta 绝对值降序，再按回合号
    cards.sort(key=lambda c: (-abs(c["delta_winrate"]), c["turn_no"]))
    return cards
