"""battle environment 包（零第三方依赖，只吃 stdlib）。

里程碑线：E0a 数据与组队层 → E0b 回合内核 → E1–E7。
对外 re-export：`new_battle` / `BattleSession` / `run_match` / `RandomPlayer` / `step`。
"""

from __future__ import annotations

from .engine import step
from .match import MatchResult, run_match
from .models import new_battle
from .players import RandomPlayer, ScriptedPlayer
from .replay import replay_record
from .rules import DEFAULT_RULES
from .session import BattleSession

__all__ = [
    "new_battle",
    "BattleSession",
    "run_match",
    "MatchResult",
    "RandomPlayer",
    "ScriptedPlayer",
    "replay_record",
    "step",
    "DEFAULT_RULES",
]
