"""R3 单步优化（最小版）：rollout → credit → reflect → edit，产 edit_apply_report。

R4 才补全 `run_steps`/`run_epochs`（池 + 两级门 + 剥削者 + 回归门 + 健康度）。
"""

from __future__ import annotations

from pathlib import Path

from rock_pvp_agent.battle.evolution.analysis import analyze_record
from rock_pvp_agent.battle.evolution.credit import mine_critical_turns
from rock_pvp_agent.battle.evolution.editor import append_report, bounded_edit
from rock_pvp_agent.battle.evolution.playbook import Playbook
from rock_pvp_agent.battle.evolution.reflect import ReflectionService
from rock_pvp_agent.battle.selfplay import run_selfplay
from rock_pvp_agent.config import get_settings


def _build_rollout_players(a_kind: str, b_kind: str, settings) -> dict | None:
    """按 kind 构造 rollout 玩家；非风格时返回 None 让 run_selfplay 默认构造。

    （R4 起 style_* 风格对手走 styles.StylePlayer，R3 默认 fake_llm/random 不触发。）
    """
    if not any("style_" in k for k in (a_kind, b_kind)):
        return None
    from rock_pvp_agent.battle.evolution.styles import StylePlayer
    from rock_pvp_agent.battle.selfplay import build_player
    players: dict = {}
    for side, kind in (("a", a_kind), ("b", b_kind)):
        if kind.startswith("style_"):
            players[side] = StylePlayer(side, kind[len("style_"):], seed=7)
        else:
            players[side] = build_player(side, kind, seed=7, settings=settings)
    return players


def run_step(*, seed: int, out_dir: str = "artifacts", a_kind: str = "fake_llm",
             b_kind: str = "random", players: dict | None = None,
             playbook: Playbook | None = None, reflect_llm=None,
             settings=None, M: int = 24, team_size: int = 3, lives: int = 2,
             rejected: list[dict] | None = None) -> dict:
    """单步优化（R3 最小版）：rollout → credit → reflect → edit，产 edit_apply_report。

    `rejected`：上一步被拒的编辑（rejected buffer，R4 `evolve steps` 跨步接线），
    喂给本步失败分析师当负面证据；本步的 rejected 候选通过返回的 `rejected_edits` 交给下一步。
    `players=` / `reflect_llm=` 为测试注入缝（确定性）。
    """
    settings = settings or get_settings()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ① rollout（style_* 注入；其余走 run_selfplay 默认构造）
    roll_players = players or _build_rollout_players(a_kind, b_kind, settings)
    rollout = run_selfplay(seed=seed, team_size=team_size, lives=lives,
                           a_kind=a_kind, b_kind=b_kind, players=roll_players,
                           out_dir=str(out_dir), saved_at="step")

    # ② credit
    analysis = analyze_record(rollout["record"])
    cards = mine_critical_turns(analysis, rollout["record"], M=M)

    # ③ reflect（失败分析师可见上一步被拒编辑）
    service = ReflectionService(settings, llm=reflect_llm)
    candidates = service.reflect(cards, rejected=rejected or [])

    # ④ edit
    pb = playbook or Playbook.initial()
    new_pb, reports = bounded_edit(pb, candidates)
    report_path = str(out_dir / "edit_apply_report.jsonl")
    if reports:
        append_report(reports, report_path)

    applied = sum(1 for r in reports if r.status == "applied")
    rejected_reports = [r for r in reports if r.status == "rejected"]
    return {
        "seed": seed,
        "battle_id": rollout["battle_id"],
        "turn_count": rollout["turn_count"],
        "cards": len(cards),
        "reflection_diagnostics": service.diagnostics,
        "candidates": [c.__dict__ for c in candidates],
        "reports": [r.to_dict() for r in reports],
        "rejected_edits": [r.to_dict() for r in rejected_reports],
        "playbook_before": pb.version,
        "playbook_after": new_pb.version,
        "edits_applied": applied,
        "edits_rejected": len(rejected_reports),
        "report_path": report_path,
        "playbook": new_pb,
    }
