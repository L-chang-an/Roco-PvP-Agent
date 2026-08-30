"""R0 启发式/极端风格对手（plan R0 目标「启发式/极端风格对手」）。

实现 `environment.players.Player` Protocol 的确定性启发式玩家，作为评测基线
与后续对手池（§九 风格对手池 / R4 专职剥削者）的基础。全部零 LLM、确定性
（独立 RNG 流，同 seed 同结果，不碰引擎流）。

风格对应 §二 应对三角 + §八 场景标签：
- `attack`         纯攻击：有攻击技就放（威力降序），否则聚能/换人；
- `status`         状态压制：优先状态技（属性增减益），其次攻击；
- `stall`          耐久消耗：优先聚能/防御（拖长对局，吃 max_turns 判负规则），少进攻；
- `energy_denial`  能量压制：平时聚能蓄能，能付得起最高威力攻击技就爆发。
"""

from __future__ import annotations

from environment.actions import Decision
from environment.rng import BattleRng

STYLES: tuple[str, ...] = ("attack", "status", "stall", "energy_denial")

_ATTACK_KINDS = ("物攻", "魔攻")


class StylePlayer:
    """极端风格启发式玩家。kind = `style_{style}`。确定性、零 LLM。"""

    def __init__(self, side: str, style: str, *, seed: int) -> None:
        if style not in STYLES:
            raise ValueError(f"未知风格「{style}」（{'/'.join(STYLES)}）。")
        self.side = side
        self.style = style
        self.kind = f"style_{style}"
        self._rng = BattleRng(seed)

    # ── Player Protocol ──
    def on_match_start(self, observation: dict) -> None:
        """开局回调：风格玩家无动作。"""

    def decide(self, observation: dict, legal: list[dict], items: list[str]):
        """按风格从合法池里挑一个主动作（确定性：同 seed 同结果）。"""
        skills = [a for a in legal if a.get("type") == "skill"]
        switches = [a for a in legal if a.get("type") == "switch"]
        recharges = [a for a in legal if a.get("type") == "recharge"]
        me = observation["me"]
        active_unit = me["units"][me["active"]]
        energy = active_unit["energy"]

        def _kind(a: dict) -> str:
            idx = a.get("value")
            if isinstance(idx, int) and 0 <= idx < len(active_unit["skills"]):
                return active_unit["skills"][idx].get("kind", "")
            return ""

        def _power(a: dict) -> int:
            idx = a.get("value")
            if isinstance(idx, int) and 0 <= idx < len(active_unit["skills"]):
                return active_unit["skills"][idx].get("power", 0)
            return 0

        def _cost(a: dict) -> int:
            idx = a.get("value")
            if isinstance(idx, int) and 0 <= idx < len(active_unit["skills"]):
                return active_unit["skills"][idx].get("energy_cost", 0)
            return 0

        if self.style == "attack":
            atk = [a for a in skills if _kind(a) in _ATTACK_KINDS]
            atk.sort(key=_power, reverse=True)
            pool = atk or skills or recharges or legal
        elif self.style == "status":
            st = [a for a in skills if _kind(a) == "状态"]
            pool = st or skills or recharges or legal
        elif self.style == "stall":
            defense = [a for a in skills if _kind(a) == "防御"]
            pool = (recharges + defense) or skills or legal   # 聚能/防御优先，攻击兜底
        elif self.style == "energy_denial":
            payable = [a for a in skills
                       if _kind(a) in _ATTACK_KINDS and _cost(a) <= energy]
            payable.sort(key=_power, reverse=True)
            pool = payable or (recharges + switches) or legal  # 蓄能期聚能/换人
        else:
            pool = legal
        return Decision(action=self._rng.choice(pool))   # Player Protocol 要求 Decision

    def choose_replacement(self, observation: dict, bench: list[int]) -> int:
        """确定性补位：选血量比例最高的存活后备（耐久直觉，保持风格可复现）。"""
        me = observation["me"]
        units = me["units"]

        def _hp_ratio(i: int) -> float:
            u = units[i]
            return u.get("current_hp", 0) / max(1, u.get("max_hp", 1))

        return max(bench, key=_hp_ratio) if bench else bench[0]

    def on_turn_result(self, observation: dict, events: list[dict]) -> None:
        """回合结束回调：风格玩家无动作。"""


def build_style_player(side: str, style: str, *, seed: int) -> StylePlayer:
    """按风格构造（CLI 路由用，`kind.startswith("style_")` 时走这里）。"""
    return StylePlayer(side, style, seed=seed)
