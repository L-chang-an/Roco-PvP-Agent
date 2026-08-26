"""Player 协议与两个零 LLM 的实现：脚本 / 随机。

`decide(observation, legal, items)` 的形状：合法池由**编排器显式传入**，玩家不持有
session 句柄——策略因此可以脱离引擎单测。
"""

from __future__ import annotations

from typing import Protocol

from .actions import Decision, recharge_action
from .rng import BattleRng


class Player(Protocol):
    """对局内核的玩家端口。E0 的两个实现零 LLM、零网络。

    四个方法的输入输出契约：
    - `on_match_start(observation)`：开局回调，无返回。
    - `decide(observation, legal, items) -> Decision`：每回合选主动作（+可选道具）。
      `legal`（合法动作池）与 `items`（可用道具）由编排器显式传入——玩家不持有
      session 句柄，策略因此可以脱离引擎单测。
    - `choose_replacement(observation, bench) -> int`：己方在场阵亡时从 `bench`
      （存活后备槽位列表）选一个补位下标。
    - `on_turn_result(observation, events)`：回合结束回调，无返回。

    阵亡补位也是玩家的决定：`choose_replacement` 在己方在场精灵阵亡时被调用，
    从存活后备槽位里选一个。
    """

    side: str
    kind: str

    def on_match_start(self, observation: dict) -> None:
        """开局观测回调。输入：己方开局观测；输出：无。"""
        ...

    def decide(self, observation: dict, legal: list[dict], items: list[str]) -> Decision:
        """每回合决策。输入：观测 / 合法动作池 / 可用道具；输出：一个合法 Decision。"""
        ...

    def choose_replacement(self, observation: dict, bench: list[int]) -> int:
        """阵亡补位决策。输入：观测 / 存活后备槽位列表；输出：选中的槽位下标。"""
        ...

    def on_turn_result(self, observation: dict, events: list[dict]) -> None:
        """回合结束回调。输入：回合末观测 / 本回合事件流；输出：无。"""
        ...


class ScriptedPlayer:
    """固定 Decision 序列；耗尽后返回 Decision(recharge_action())。"""

    def __init__(self, side: str, script: list[Decision] | None = None) -> None:
        self.side = side
        self.kind = "scripted"
        self._script = list(script or [])

    def on_match_start(self, observation: dict) -> None:
        """开局回调：脚本无动作。输入：观测；输出：无。"""

    def decide(self, observation: dict, legal: list[dict], items: list[str]) -> Decision:
        """输出脚本里下一条 Decision；脚本耗尽 → 恒返聚能（合法兜底）。"""
        if self._script:
            return self._script.pop(0)
        return Decision(recharge_action())

    def choose_replacement(self, observation: dict, bench: list[int]) -> int:
        """脚本无补位脚本：固定取第一个存活后备。"""
        return bench[0]

    def on_turn_result(self, observation: dict, events: list[dict]) -> None:
        """回合结束回调：脚本无动作。输入：观测 + 事件流；输出：无。"""


def _bernoulli(rng: BattleRng, p: float) -> bool:
    """用 choice 实现 p 概率的伯努利（BattleRng 只暴露 choice）。"""
    k = min(10, max(0, round(p * 10)))
    return bool(rng.choice([True] * k + [False] * (10 - k)))


class RandomPlayer:
    """从 legal 里随机取一个主动作，并以 `item_prob` 的概率带上一个可用道具。

    **自带独立 BattleRng(seed)，绝不共用引擎的 RNG 流**——混流的后果是换个策略就
    改变引擎的平手硬币，于是「用同一 seed 重放一条提交日志」再也复现不出同一条
    轨迹，E6 的轨迹回放直接失效。
    """

    def __init__(self, side: str, *, seed: int, item_prob: float = 0.2) -> None:
        self.side = side
        self.kind = "random"
        self._rng = BattleRng(seed)
        self._item_prob = item_prob

    def on_match_start(self, observation: dict) -> None:
        """开局回调：随机玩家无动作。输入：观测；输出：无。"""

    def decide(self, observation: dict, legal: list[dict], items: list[str]) -> Decision:
        """随机选一个合法主动作；以 item_prob 概率带上一个可用道具。
        输入：观测（未用）/ legal 合法动作池 / items 可用道具；输出：随机 Decision。"""
        action = self._rng.choice(legal)
        item = ""
        if items and _bernoulli(self._rng, self._item_prob):
            item = self._rng.choice(items)
        return Decision(action=action, item=item)

    def choose_replacement(self, observation: dict, bench: list[int]) -> int:
        """随机选一个存活后备——走自己的独立 RNG 流，不碰引擎的流。"""
        return self._rng.choice(bench)

    def on_turn_result(self, observation: dict, events: list[dict]) -> None:
        """回合结束回调：随机玩家无动作。输入：观测 + 事件流；输出：无。"""
