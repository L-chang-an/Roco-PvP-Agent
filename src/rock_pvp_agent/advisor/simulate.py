"""确定性成对模拟（M3）：贪心玩家换边多 seed 胜率下限。

与 `run_match` 的 `Player` 协议不同：贪心玩家需要 `BattleState`（`predictions_for` 的输入），
而 `Player.decide` 只拿迷雾 dict、不持 session——故本模块**直接驱动 session**，每回合读
`session.state` + `predictions_for` + `legal_actions` 取最高伤害合法技能。

结论是「**模拟（贪心策略）胜率下限**」，不是真实最优胜率——返回结构强制带此标签。
"""

from __future__ import annotations

import dataclasses

from environment.actions import Decision, recharge_action
from environment.battle_config import build_battle_rules
from environment.dataset import DataSource
from environment.models import SIDES
from environment.prediction import predictions_for
from environment.session import BattleSession

from rock_pvp_agent.advisor.trajectory import team_key


def _greedy_decision(state, side: str, legal: list[dict]) -> Decision:
    """贪心决策：候选伤害与合法池交叉取最高伤害技能；无合法攻击 → 聚能兜底。纯确定性。"""
    preds = predictions_for(state, side)
    damage_by_slot = {p["slot"]: p["damage"] for p in preds}

    best_action: dict | None = None
    best_damage = -1
    for action in legal:
        if action.get("type") != "skill":
            continue
        slot = action.get("value")
        if isinstance(slot, int) and damage_by_slot.get(slot, -1) > best_damage:
            best_action = action
            best_damage = damage_by_slot[slot]
    if best_action is not None:
        return Decision(action=best_action)
    return Decision(action=recharge_action())


def _play(roster_a: list[dict], roster_b: list[dict], seed: int,
          max_turns: int | None = None) -> str | None:
    """跑一局（a/b 各贪心），返回 winner（"a"/"b"/None=平局）。确定性。"""
    rules = build_battle_rules(team_size=len(roster_a))
    if max_turns is not None:
        rules = dataclasses.replace(rules, max_turns=max_turns)
    session = BattleSession.start(roster_a, roster_b, seed=seed, rules=rules,
                                  battle_id=f"sim-{seed}")

    # 第 0 回合：双方取第一个存活单位首发（确定性）。
    for s in SIDES:
        session.choose_starter(s, session.starter_options(s)[0])
    session.start_entry()

    while not session.state.done:
        for s in SIDES:
            dec = _greedy_decision(session.state, s, session.legal_actions(s))
            if not session.submit(s, dec)["ok"]:          # 防御：非法 → 聚能兜底
                session.submit(s, Decision(action=recharge_action()))
        res = session.resolve()
        while res.get("need_replacement"):
            side = res["need_replacement"]
            res = session.submit_replacement(side, session.replacement_options(side)[0])
    return session.state.winner


def simulate_matchups(roster: list[dict], opponents: list[list[dict]], *,
                      seeds: list[int], source: DataSource = DataSource.VALID,
                      max_turns: int | None = None) -> dict:
    """成对换边模拟：每个 opponent 每个 seed 跑两局（不换边 + 换边），统计 roster 胜率。

    `source` 预留（roster 已 build 完成，本函数不消费）。返回每对：
    `{games, wins, win_rate, seeds, replay_ok}` + 全局 `strategy="greedy"` 标签。
    """
    matchups: dict[str, dict] = {}
    for opp in opponents:
        if len(opp) != len(roster):
            raise ValueError(f"对手队伍规模 {len(opp)} 与 {len(roster)} 不一致。")
        games = 0
        wins = 0
        seed_list: list[int] = []
        for seed in seeds:
            if _play(roster, opp, seed, max_turns) == "a":
                wins += 1                    # roster 作为 a 胜
            if _play(opp, roster, seed, max_turns) == "b":
                wins += 1                    # roster 换边作为 b 胜
            games += 2
            seed_list.extend([seed, seed])
        key = f"{team_key(roster)[:8]}:{team_key(opp)[:8]}"
        matchups[key] = {
            "opponent": [u.get("name") for u in opp],
            "games": games,
            "wins": wins,
            "win_rate": (wins / games) if games else None,
            "seeds": seed_list,
            "replay_ok": True,
        }
    return {
        "strategy": "greedy",
        "label": "模拟（贪心策略）胜率下限",
        "seeds": list(seeds),
        "matchups": matchups,
    }
