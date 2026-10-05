"""Offline probes for the human/advisor memory audit (no LLM or API calls).

Run from the repository root:
  .venv/bin/python tmp/memory-review-2026-09-22/reproduce_human_findings.py

Writes only human-findings-evidence.json beside this script. Battle records used
for querying are synthetic fixtures in an automatically removed temporary dir.
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import asdict
from pathlib import Path

from environment.actions import recharge_action
from environment.battle_config import build_battle_rules
from environment.dataset import DataSource
from environment.presets import p1_preset
from environment.session import BattleSession
from environment.teambuilder import build_roster
from roco_pvp_agent.advisor.trajectory import aggregate, normalize, query_trajectory_evidence
from roco_pvp_agent.battle.player import FakeLLMPlayer
from roco_pvp_agent.battle.selfplay import run_selfplay
from ui.battle import BattleController


def main() -> None:
    rules = build_battle_rules(team_size=3)
    picks_a, picks_b = p1_preset(3)
    roster_a = build_roster(picks_a, DataSource.VALID, rules)
    roster_b = build_roster(picks_b, DataSource.VALID, rules)
    session = BattleSession.start(roster_a, roster_b, seed=7, rules=rules,
                                  battle_id="audit-unfinished")
    player = FakeLLMPlayer("b", seed=8)
    controller = BattleController("audit-unfinished", session, seed=7,
        opponent="fake_llm", team_a=[asdict(p) for p in picks_a],
        team_b=[asdict(p) for p in picks_b], rules=rules,
        saved_at="audit-fixture", player=player)
    record = controller.record()
    with tempfile.TemporaryDirectory(prefix="human-memory-audit-") as tempdir:
        Path(tempdir, "unfinished.json").write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8")
        query = query_trajectory_evidence("human", battles_dir=tempdir, min_games=1)
    unfinished = {
        "source": "actual BattleController.record() immediately after construction",
        "done": record["done"], "winner": record["winner"],
        "turn_count": len(record["turns"]),
        "replay_ok": normalize(record, "human").replay_ok,
        "query_result": query,
        "finding": "An unfinished zero-turn record counts as one game and zero wins.",
    }

    # A real deterministic mirror match supplies a terminal winner. Iterate a
    # small bounded seed range until side b wins, making the perspective bug visible.
    mirror_result = None
    for seed in range(1, 21):
        result = run_selfplay(seed=seed, team_size=3, lives=2, max_turns=30,
            a_kind="random", b_kind="random", roster_a=roster_a, roster_b=roster_a,
            battle_id=f"audit-mirror-{seed}", saved_at="audit-fixture")
        if result["winner"] == "b":
            mirror_result = result
            break
    assert mirror_result is not None, "No side-b mirror win found in bounded seed range"
    mirror_ev = normalize(mirror_result["record"], "selfplay")
    mirror = {
        "source": "completed deterministic random-vs-random mirror match",
        "seed": mirror_result["seed"], "done": mirror_result["done"],
        "winner": mirror_result["winner"], "replay_ok": mirror_result["replay_ok"],
        "aggregate": aggregate([mirror_ev]),
        "finding": "A side-b mirror win counts as a side-a team win because keys match.",
    }

    # Replay integrity currently checks turn hashes, but not terminal metadata.
    tampered = {**mirror_result["record"], "winner": "a"}
    terminal = {
        "source": "same completed replay with only winner metadata flipped",
        "original_winner": mirror_result["winner"],
        "declared_winner": tampered["winner"],
        "replay_ok": normalize(tampered, "selfplay").replay_ok,
        "finding": "Changing declared winner does not fail the evidence replay gate.",
    }

    # Serialization probe only: expose the same learning-metadata interface as an
    # LLMPlayer on a fake player, then inspect the real UI controller record.
    controller.choose_starter(0)
    controller.act(recharge_action())
    player._turn_log = [{"turn": 1, "prediction": "audit-probe"}]
    player.loaded_global_mem_id = "gm_audit_probe"
    persisted = controller.record()
    serialization = {
        "source": "fake player with LLM-compatible learning-metadata attributes",
        "record_keys": sorted(persisted),
        "player_has_turn_log": bool(player._turn_log),
        "player_has_loaded_global_mem_id": bool(player.loaded_global_mem_id),
        "record_has_analysis_b": "analysis_b" in persisted,
        "record_has_global_mem_b": "global_mem_b" in persisted,
        "finding": "Human battle persistence discards learning metadata available on player.",
    }

    evidence = {"audit_date": "2026-09-22", "network_calls": 0,
                "unfinished_evidence": unfinished, "mirror_aggregation": mirror,
                "terminal_metadata_gate": terminal,
                "human_learning_metadata_serialization": serialization}
    target = Path(__file__).with_name("human-findings-evidence.json")
    target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
