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
from typing import Iterator

from environment.battle_config import build_battle_rules
from environment.dataset import DataSource
from environment.match import drive_turn, run_match
from environment.models import SIDES
from environment.players import RandomPlayer
from environment.presets import p1_preset
from environment.replay import replay_record
from environment.session import BattleSession
from environment.teambuilder import build_roster
from environment.view import observe

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
        "starters": result.starters,   # 第 0 回合首发（重放据此重建入场）
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


# ---------------------------------------------------------------------------
# E7：观战流（人类以全局视角观看，两个 LLM 玩家仍迷雾）
# ---------------------------------------------------------------------------


def _global_view(session) -> dict:
    """观战者全局（上帝）视角：双方都全量——绝对血量 / 全部技能（含 desc）/ 性格 / 血脉 / IV / 增减益。

    复用公开 `view.observe(state, side, "partial")["me"]`（己方全量口径）取双方，观战者无所遮蔽。
    **与给 LLM 玩家的迷雾 view() 是两套口径**：玩家经 `drive_turn` 只拿各自 `session.view(s)` 白名单。
    """
    state = session.state
    return {
        "turn": state.turn,
        "winner": state.winner,
        "done": state.done,
        "rules": _rules_dict(state.rules),
        "a": observe(state, "a", "partial")["me"],
        "b": observe(state, "b", "partial")["me"],
        # 天气（全局，双方共享，观战者可见）
        "weather": ({"kind": state.weather.kind, "turns_left": state.weather.turns_left,
                     "source": state.weather.source} if state.weather else None),
    }


def run_spectate(*, seed: int, a_kind: str = "llm", b_kind: str = "llm",
                 team_size: int = 3, lives: int = 2, max_turns: int | None = None,
                 settings=None, players: dict | None = None,
                 roster_a: list | None = None, roster_b: list | None = None,
                 battle_id: str | None = None) -> Iterator[dict]:
    """观战流生成器（E7）：逐回合驱动一局双玩家对战，产出**全局视角**帧。

    - 两个 LLM 玩家与普通对局一样走 `drive_turn`（迷雾口径：观测 view()、事件 filter_events_for）——
      观战流不会让 LLM 多看到任何东西。
    - 帧协议：`meta`（配置+双方精灵名+实际 kind）→ `state`（turn 0 全局快照）→ `turn*`（
      全局快照 + 双方 decisions + **全量事件**）→ `done`（winner / 回合数）。
    - `players=` / `roster_a/b=` 为测试注入缝。
    """
    rules = build_battle_rules(team_size=team_size, lives=lives)
    if max_turns is not None:
        rules = dataclasses.replace(rules, max_turns=max_turns)
    if roster_a is None or roster_b is None:
        picks_a, picks_b = p1_preset(team_size)
        roster_a = build_roster(picks_a, DataSource.VALID, rules)
        roster_b = build_roster(picks_b, DataSource.VALID, rules)
    bid = battle_id or f"spectate-{seed}"
    session = BattleSession.start(roster_a, roster_b, seed=seed, rules=rules, battle_id=bid)
    if players is None:
        settings = settings or get_settings()
        players = {
            "a": build_player("a", a_kind, seed=seed + 1, settings=settings),
            "b": build_player("b", b_kind, seed=seed + 2, settings=settings),
        }
    for s in SIDES:
        players[s].on_match_start(session.view(s))

    # 第 0 回合（2026-08-30）：双方选首发 → 首发触发入场效果 → 进入第 1 回合。
    starters: dict[str, int] = {}
    for s in SIDES:
        options = session.starter_options(s)
        chooser = getattr(players[s], "choose_starter", None)
        idx = chooser(session.view(s), options) if chooser else options[0]
        if not session.choose_starter(s, idx)["ok"]:
            session.choose_starter(s, options[0])
        starters[s] = idx
    session.start_entry()

    yield {
        "event": "meta",
        "battle_id": bid,
        "seed": seed,
        "rules": _rules_dict(rules),
        "players": {"a": players["a"].kind, "b": players["b"].kind},
        "team_a": [u["name"] for u in roster_a],
        "team_b": [u["name"] for u in roster_b],
        "starters": starters,
    }
    yield {"event": "state", "turn": 0, "state": _global_view(session)}

    turn_no = 0
    while not session.state.done:
        out = drive_turn(session, players)
        turn_no = out.turn
        yield {
            "event": "turn",
            "turn": turn_no,
            "state": _global_view(session),
            "decisions": {"a": asdict(out.decisions["a"]), "b": asdict(out.decisions["b"])},
            "events": out.events,          # 全量事件（含绝对血量等——观战者上帝视角）
        }
    yield {"event": "done", "winner": session.state.winner, "turn": turn_no}
