"""自博弈编排（E6）：两个隔离玩家自我对战，轨迹落盘 + 重放自检。

E6 测试期双方玩家 = FakeLLM（固定回复 + 随机动作，各自独立 RNG 流）/ 随机；真实 LLM 玩家
在 **E6.5** 实现同一个 `environment.players.Player` Protocol 后由 `players=` 注入即可——
本编排不关心玩家是不是真 LLM。

流程：管理员规则 → 预设阵容（p1_preset → build_roster，roster spec）→ BattleSession →
`run_match`（逐回合 decide→submit→resolve→补位，玩家只见 **迷雾 view()**）→ 组 lean 记录
→ `TrajectoryStore.save`（若给 out_dir）→ **`replay_record(record)` 自检**（逐回合 state_hash
重放一致，马尔可夫不变式的现场验证）。
"""

from __future__ import annotations

import dataclasses
from dataclasses import asdict, fields
from datetime import datetime, timezone

from environment.battle_config import build_battle_rules
from environment.dataset import DataSource
from environment.match import run_match
from environment.players import RandomPlayer
from environment.presets import p1_preset
from environment.replay import replay_record
from environment.session import BattleSession
from environment.teambuilder import build_roster

from rock_pvp_agent.battle.player import FakeLLMPlayer, LLMPlayer
from rock_pvp_agent.battle.store import TrajectoryStore
from rock_pvp_agent.config import get_settings

_PLAYER_KINDS = ("fake_llm", "random", "llm")


def _rules_dict(r) -> dict:
    """BattleRules → 纯 dict（落盘用，`BattleRules(**d)` 原样还原）。"""
    return {f.name: getattr(r, f.name) for f in fields(r)}


def _now() -> str:
    """UTC 时间戳（元数据，不进 state_hash，不影响重放）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_player(side: str, kind: str, *, seed: int, settings=None):
    """按 kind 构造一方玩家（各自独立 RNG 流 seed）。

    `kind == "llm"`（真实 LLM）：无 API key → **降级假LLM**（无 key 也能打完，测试/离线可跑）；
    有 key → `LLMPlayer`（真正调 LLM）。`settings` 缺省读全局配置。
    """
    settings = settings or get_settings()
    if kind == "llm":
        if settings.has_api_key:
            return LLMPlayer(side, settings=settings, seed=seed)
        return FakeLLMPlayer(side, seed=seed)       # 无 key 自动降级假LLM
    if kind == "fake_llm":
        return FakeLLMPlayer(side, seed=seed)
    if kind == "random":
        return RandomPlayer(side, seed=seed)
    raise ValueError(f"未知玩家类型「{kind}」（{'/'.join(_PLAYER_KINDS)}）。")


def run_selfplay(*, seed: int, team_size: int = 3, lives: int = 2, max_turns: int | None = None,
                 a_kind: str = "fake_llm", b_kind: str = "fake_llm",
                 roster_a: list | None = None, roster_b: list | None = None,
                 players: dict | None = None,
                 out_dir=None, battle_id: str | None = None,
                 saved_at: str | None = None,
                 settings=None) -> dict:
    """一局双玩家自博弈，返回终局信息 + 轨迹记录 + 重放自检结论。

    参数：`roster_a/b` 与 `players` 为测试注入缝（缺省用 p1 预设阵容 / 按 kind 构造）。
    `battle_id` 缺省 `selfplay-{seed}`。`out_dir` 给出则落盘（TrajectoryStore）。
    """
    rules = build_battle_rules(team_size=team_size, lives=lives)
    if max_turns is not None:
        rules = dataclasses.replace(rules, max_turns=max_turns)
    if roster_a is None or roster_b is None:
        picks_a, picks_b = p1_preset(team_size)
        roster_a = build_roster(picks_a, DataSource.VALID, rules)
        roster_b = build_roster(picks_b, DataSource.VALID, rules)
    bid = battle_id or f"selfplay-{seed}"
    session = BattleSession.start(roster_a, roster_b, seed=seed, rules=rules, battle_id=bid)
    if players is None:
        settings = settings or get_settings()
        players = {
            "a": build_player("a", a_kind, seed=seed + 1, settings=settings),
            "b": build_player("b", b_kind, seed=seed + 2, settings=settings),
        }
    result = run_match(session, players)

    record = {
        "version": 1,
        "battle_id": bid,
        "saved_at": saved_at or _now(),
        "seed": seed,
        "players": {"a": players["a"].kind, "b": players["b"].kind},
        "rules": _rules_dict(rules),
        "team_a": roster_a,
        "team_b": roster_b,
        "winner": result.winner,
        "done": result.done,
        "turns": [
            {
                "turn": t.turn,
                "decision_a": asdict(t.decision_a),
                "decision_b": asdict(t.decision_b),
                "replace_a": t.replace_a,
                "replace_b": t.replace_b,
                "state_hash": t.state_hash,
            }
            for t in result.turns
        ],
    }
    path = None
    if out_dir is not None:
        path = TrajectoryStore(out_dir).save(record)
    check = replay_record(record)               # 重放自检：马尔可夫不变式的现场验证
    return {
        "battle_id": bid,
        "seed": seed,
        "winner": result.winner,
        "done": result.done,
        "turn_count": result.turn_count,
        "rng_calls": session.state.rng.calls,
        "record_path": str(path) if path is not None else None,
        "replay_ok": check["all_match"],
        "record": record,
    }
