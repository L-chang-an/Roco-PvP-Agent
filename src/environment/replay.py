"""轨迹重放（E6）：按 (rules, 双方 roster, seed, 逐回合提交序列) 重建全新 session，
逐回合比对 `state_hash`——马尔可夫不变式的可执行验证。

只依赖环境包内纯函数；不读 UI / 网络 / 玩家。重放消费的记录格式（store.py / 自博弈自检
共用同一来源）：
    {rules: {…可 BattleRules(**d)}, team_a: [roster spec], team_b: [roster spec],
     seed: int, battle_id: str,
     turns: [{turn, decision_a: {action, item}, decision_b: {action, item},
              replace_a: int|None, replace_b: int|None, state_hash: str}]}

team_a/team_b 存 **roster spec**（build_roster 的产物，`BattleSession.start` 直吃）——
回放不依赖数据源、不重算六维、不漂移。

为什么能重放：引擎是 `(state, 双方提交) → state'` 的纯转移，且玩家 RNG 流与引擎 RNG 流
早已分离，所以「提交序列 + seed」足以逐字节复现。
"""

from __future__ import annotations

from .actions import Decision
from .rules import BattleRules
from .session import BattleSession

_REQUIRED = ("rules", "team_a", "team_b", "seed", "battle_id", "turns")


def replay_record(record: dict) -> dict:
    """重放一条轨迹记录，返回逐回合 `expected vs actual` 比对。

    返回 `{ok, battle_id, all_match, turns: [{turn, expected, actual, match}]}`；
    **首个失配即停**（状态已偏离，后续回合无意义）。缺失必需键 → ValueError
    （引擎「宁失败不抛」改为显式校验）。
    """
    missing = [k for k in _REQUIRED if k not in record]
    if missing:
        raise ValueError(f"轨迹记录缺少必需键：{missing}（应有 {list(_REQUIRED)}）。")
    rules = BattleRules(**record["rules"])
    session = BattleSession.start(record["team_a"], record["team_b"], seed=record["seed"],
                                  rules=rules, battle_id=record["battle_id"])

    results: list[dict] = []
    for tr in record["turns"]:
        da = Decision(action=tr["decision_a"]["action"], item=tr["decision_a"].get("item", ""))
        db = Decision(action=tr["decision_b"]["action"], item=tr["decision_b"].get("item", ""))
        ok = bool(session.submit("a", da)["ok"])
        ok &= bool(session.submit("b", db)["ok"])
        res = session.resolve()
        ok &= bool(res["ok"])
        need = res.get("need_replacement")
        if ok:
            if need == "a":
                ok &= tr.get("replace_a") is not None \
                    and bool(session.submit_replacement("a", tr["replace_a"])["ok"])
            elif need == "b":
                ok &= tr.get("replace_b") is not None \
                    and bool(session.submit_replacement("b", tr["replace_b"])["ok"])
            elif need is not None:
                ok = False
        actual = session.state.state_hash()
        results.append({
            "turn": tr["turn"],
            "expected": tr["state_hash"],
            "actual": actual,
            "match": ok and actual == tr["state_hash"],
        })
        if not results[-1]["match"]:
            break   # 已偏离，后续回合无意义
    return {"ok": True, "battle_id": record["battle_id"],
            "all_match": all(r["match"] for r in results), "turns": results}
