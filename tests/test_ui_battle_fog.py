"""AUD-E-001 回归：BattleController 传给对手玩家的 on_turn_result 事件已按 b 视角过滤。"""

from __future__ import annotations

import dataclasses

from environment.actions import Decision, skill_action
from environment.players import ScriptedPlayer
from environment.rules import DEFAULT_RULES
from environment.session import BattleSession
from rosters import spec
from ui.battle import BattleController


class _CapturePlayer:
    """包一层真实玩家，记录 on_turn_result 收到的事件（迷雾隔离断言用）。"""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.result_events: list[list[dict]] = []

    def on_turn_result(self, observation: dict, events: list[dict]) -> None:
        self.result_events.append(events)
        return self.inner.on_turn_result(observation, events)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def test_controller_opponent_events_are_fogged():
    """对手(b)收到的回合结果事件无敌方绝对血量 target_hp_left（应转为 target_hp_pct）。

    钉死 AUD-E-001 的修复：BattleController 自实现回合循环，过去把未过滤事件喂给
    `on_turn_result`，接真实 LLM 对手即泄漏敌方绝对血量；现已按 b 视角过滤。
    """
    rules = dataclasses.replace(DEFAULT_RULES, team_size=1)
    a = [spec("甲", 300, 100, 100, 100, 100, 100, ["抓挠"])]
    b = [spec("乙", 300, 100, 100, 100, 100, 90, ["抓挠"])]
    session = BattleSession.start(a, b, seed=1, rules=rules, battle_id="t")
    cap = _CapturePlayer(ScriptedPlayer("b", script=[Decision(skill_action(0))]))
    ctrl = BattleController("t", session, seed=1, opponent="fake_llm",
                            team_a=a, team_b=b, rules=rules, saved_at="x", player=cap)
    ctrl.choose_starter(0)
    ctrl.act(skill_action(0))

    assert cap.result_events, "对手应收到回合结果事件"
    # 打到敌方(a 的「甲」)的伤害事件必须转百分比，绝不带敌方绝对血量；打到 b 自己单位的保留绝对血量是合法的。
    foe_damages = [e for e in cap.result_events[-1]
                   if e.get("type") == "damage" and e.get("target") == "甲"]
    assert foe_damages, "应存在 b 打到敌方「甲」的伤害事件"
    for e in foe_damages:
        assert "target_hp_pct" in e, f"敌方血量应转百分比，实际键 {sorted(e)}"
        assert "target_hp_left" not in e, f"泄漏敌方绝对血量：{e}"
