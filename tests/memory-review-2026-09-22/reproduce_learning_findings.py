"""离线审查复现：不连接模型、不修改项目源码，只写旁边的证据 JSON。

运行：.venv/bin/python tmp/memory-review-2026-09-22/reproduce_learning_findings.py
首发/ValueFn 使用真实引擎；编排接线检查用明确标注的 mock 替代昂贵评分。
mock 得分仅用于走通分支，不构成对战实力提升证据。
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from environment.players import RandomPlayer
from environment.rules import BattleRules
from environment.session import BattleSession
from roco_pvp_agent.battle.evolution import run as evolution_run
from roco_pvp_agent.battle.evolution.analysis import analyze_record
from roco_pvp_agent.battle.evolution.bench import build_instances
from roco_pvp_agent.battle.evolution.credit import mine_critical_turns
from roco_pvp_agent.battle.evolution.editor import EditCandidate
from roco_pvp_agent.battle.evolution.playbook import Playbook
from roco_pvp_agent.battle.evolution.pool import PlaybookPool
from roco_pvp_agent.battle.evolution.valuefn import ValueFn
from roco_pvp_agent.battle.selfplay import run_selfplay


class LastStarter(RandomPlayer):
    def choose_starter(self, observation, options):
        return options[-1]


def check_starter_replay():
    rollout = run_selfplay(seed=7, players={
        "a": LastStarter("a", seed=8), "b": LastStarter("b", seed=9),
    })
    record = rollout["record"]
    analysis = analyze_record(record)
    try:
        mine_critical_turns(analysis, record, M=1)
        error = None
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    assert rollout["replay_ok"] and not analysis.replay_ok and error
    return {
        "method": "真实引擎 + 确定性随机玩家，仅首发固定为最后一只",
        "starters": record["starters"], "engine_replay_ok": rollout["replay_ok"],
        "analysis_replay_ok": analysis.replay_ok,
        "record_turn_count": rollout["turn_count"],
        "analyzed_turn_count": analysis.turn_count, "critical_turn_error": error,
        "evidence": ["src/roco_pvp_agent/battle/evolution/analysis.py:169",
                     "src/roco_pvp_agent/battle/evolution/credit.py:178",
                     "src/environment/replay.py:42"],
    }


def check_memory_evaluation_wiring(default_rollout):
    calls = []

    def fake_score_strategy(playbook, instances, **kwargs):
        calls.append({"call": "score_strategy", "playbook": playbook.version,
                      "memory_retriever_provided": kwargs.get("memory_retriever") is not None})
        score = 0.5 if playbook.version == "pb_v0" else 0.6
        return {instance.name: score for instance in instances}

    def fake_score_pair(playbook, opponent, instances, **kwargs):
        calls.append({"call": "score_pair", "playbook": playbook.version,
                      "memory_retriever_provided": kwargs.get("memory_retriever") is not None})
        return 0.6

    def fake_rollout(*args, **kwargs):
        calls.append({"call": "training_rollout",
                      "memory_retriever_provided": kwargs.get("memory_retriever") is not None})
        return default_rollout

    class OfflineReflection:
        diagnostics = []

        def __init__(self, *args, **kwargs):
            pass

        def reflect(self, *args, **kwargs):
            return [EditCandidate("M2 action_selector", "append", text="接线审查用候选规则。")]

    with tempfile.TemporaryDirectory() as task_tmp:
        with (patch.object(evolution_run, "_score_strategy", fake_score_strategy),
              patch.object(evolution_run, "_score_pair", fake_score_pair),
              patch.object(evolution_run, "_play_rollout", fake_rollout),
              patch.object(evolution_run, "mine_critical_turns", return_value=[]),
              patch.object(evolution_run, "ReflectionService", OfflineReflection)):
            report = evolution_run.run_steps(
                n=1, instances=build_instances("d_sel")[:1],
                seeds_per_instance=1, minibatch_seeds=1, M=1,
                memory_dir=str(Path(task_tmp) / "memory"),
                out_dir=str(Path(task_tmp) / "output"),
                settings=SimpleNamespace(has_api_key=False), llm=False,
            )
    evaluation_calls = [c for c in calls if c["call"] != "training_rollout"]
    rollout_calls = [c for c in calls if c["call"] == "training_rollout"]
    assert evaluation_calls and all(not c["memory_retriever_provided"] for c in evaluation_calls)
    assert rollout_calls and all(c["memory_retriever_provided"] for c in rollout_calls)
    return {
        "method": "真实 run_steps/gate/pool 编排，mock 评分/反思/rollout；只验证参数接线",
        "calls": calls, "candidate_entered": report["steps"][0]["entered"],
        "interpretation": "训练 rollout 获得记忆检索器，全部评分调用未获得；不是实力实验。",
        "evidence": ["src/roco_pvp_agent/battle/evolution/run.py:452",
                     "src/roco_pvp_agent/battle/evolution/run.py:463",
                     "src/roco_pvp_agent/battle/evolution/run.py:489",
                     "src/roco_pvp_agent/battle/evolution/run.py:236"],
    }


def check_historical_opponent_type():
    class OfflineLLMStub:
        def __init__(self, side, **kwargs):
            self.side = side

    playbook = Playbook.initial()
    settings = SimpleNamespace(has_api_key=True)
    with patch("roco_pvp_agent.battle.player.LLMPlayer", OfflineLLMStub):
        subject = evolution_run._strategy_player(playbook, "a", 7, llm=True, settings=settings)
        opponent = evolution_run._opponent_player(playbook, "b", 8, settings=settings)
    assert isinstance(subject, OfflineLLMStub) and type(opponent._random).__name__ == "RandomPlayer"
    return {
        "method": "仅构造对象，LLMPlayer 替换为无调用 stub；同一本手册用于双方",
        "subject_type": type(subject).__name__, "historical_opponent_type": type(opponent).__name__,
        "historical_opponent_policy_delegate": type(opponent._random).__name__,
        "interpretation": "llm=True 的历史对手仍是手册 hash 决定随机流，不执行历史手册语义。",
        "evidence": ["src/roco_pvp_agent/battle/evolution/run.py:131",
                     "src/roco_pvp_agent/battle/evolution/run.py:142",
                     "src/roco_pvp_agent/battle/player.py:106"],
    }


def check_valuefn_interface(default_rollout):
    record = default_rollout["record"]
    state = BattleSession.start(record["team_a"], record["team_b"], seed=7,
                                rules=BattleRules(**record["rules"])).state
    try:
        ValueFn(weights=[0.0] * 9, auc=0.9).value(state, "a")
        error = None
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    assert error and "AttributeError" in error
    return {"error": error,
            "interpretation": "analyze_record 给价值函数的参数正是 BattleState。",
            "evidence": ["src/roco_pvp_agent/battle/evolution/analysis.py:178",
                         "src/roco_pvp_agent/battle/evolution/valuefn.py:113"]}


def check_slow_update_instance_count():
    instances = build_instances("d_sel")

    def fake_aggregate(playbook, instances, **kwargs):
        return {"winrate": 0.5, "ci95": (0.4, 0.6), "ci95_low": 0.4,
                "ci95_high": 0.6, "n_games": len(instances),
                "per_instance": {i.name: 0.5 for i in instances}}

    def fake_score(playbook, instances, **kwargs):
        return {i.name: 0.5 for i in instances}

    with tempfile.TemporaryDirectory() as task_tmp:
        with (patch.object(evolution_run, "_score_strategy", fake_score),
              patch.object(evolution_run, "_aggregate_report", fake_aggregate),
              patch.object(evolution_run, "run_steps", return_value={"steps": [], "records": []})):
            try:
                evolution_run.run_epochs(n=2, E=0, out_dir=task_tmp,
                                         instances=instances,
                                         dtest_instances=build_instances("d_test")[:1],
                                         settings=SimpleNamespace(has_api_key=False))
                error = None
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
    assert error and "缺实例" in error
    return {"method": "真实 epoch 慢更新编排，mock 对战评分与 fast step；模拟两轮稳定手册",
            "pool_instance_count": len(instances),
            "slow_evaluation_instance_count": evolution_run.SLOW_UPDATE_INSTANCES,
            "error": error,
            "evidence": ["src/roco_pvp_agent/battle/evolution/run.py:673",
                         "src/roco_pvp_agent/battle/evolution/run.py:708",
                         "src/roco_pvp_agent/battle/evolution/pool.py:371"]}


def check_resume_accepts_changed_seed_manifest():
    original = build_instances("d_sel")[:1]
    changed = [replace(original[0], seeds=(987654321,))]
    pool = PlaybookPool([original[0].name])
    pool.add(Playbook.initial(), {original[0].name: 0.5})
    with tempfile.TemporaryDirectory() as task_tmp:
        result = evolution_run.run_steps(n=0, pool=pool, instances=changed,
                                         out_dir=task_tmp,
                                         settings=SimpleNamespace(has_api_key=False))
    assert result["pool"]["members"][0]["scores"][original[0].name] == 0.5
    return {
        "method": "真实续跑检查，n=0 避免对战；已有池仍保留原评测分数",
        "original_seeds": original[0].seeds, "changed_seeds": changed[0].seeds,
        "resume_accepted": True, "stored_score_preserved": 0.5,
        "interpretation": "续跑只校验实例名，无法发现同名实例 seed/阵容或模型、规则变化。",
        "evidence": ["src/roco_pvp_agent/battle/evolution/run.py:428",
                     "src/roco_pvp_agent/battle/evolution/pool.py:457"],
    }


def main():
    default_rollout = run_selfplay(seed=7)
    results = {
        "scope": "零付费 API；未修改项目源码；mock 分支仅为接线与边界验证，不可解释为胜率证据。",
        "nonzero_starter_breaks_learning_replay": check_starter_replay(),
        "memory_absent_from_evaluation_calls": check_memory_evaluation_wiring(default_rollout),
        "historical_opponent_is_random_proxy": check_historical_opponent_type(),
        "valuefn_battlestate_interface_error": check_valuefn_interface(default_rollout),
        "slow_update_subset_cannot_fill_pool": check_slow_update_instance_count(),
        "resume_accepts_changed_seed_manifest": check_resume_accepts_changed_seed_manifest(),
    }
    output = Path(__file__).with_name("learning-findings-evidence.json")
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    print(json.dumps({key: "reproduced" for key in results if key != "scope"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
