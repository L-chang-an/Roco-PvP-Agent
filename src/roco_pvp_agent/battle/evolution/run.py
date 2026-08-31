"""R3 单步优化编排（最小版）：rollout → credit → reflect → edit，暂不接池。

`run_step` 把 R0–R3 的组件串成一个 step：
① **rollout**：`run_selfplay` 打一局（玩家注入缝：真实 LLM / fake / 风格）；
② **credit**：`analyze_record` + `mine_critical_turns` → 关键回合卡片；
③ **reflect**：`ReflectionService` 双分析师 → `EditCandidate` 列表；
④ **edit**：`bounded_edit` → 新 Playbook + `EditReport`，`edit_apply_report.jsonl` 落盘（审计）。

此时编辑**没有 D_sel 门禁**（R4 接池与晋级）——本 step 的产物是「编辑提案 + 处置报告」，
Gate 重点是编辑纪律本身对。确定性：注入确定性玩家与假反思 LLM 时可逐位复现。
"""

from __future__ import annotations

import math
from pathlib import Path

from roco_pvp_agent.battle.evolution.analysis import analyze_record
from roco_pvp_agent.battle.evolution.credit import mine_critical_turns
from roco_pvp_agent.battle.evolution.editor import append_report, bounded_edit
from roco_pvp_agent.battle.evolution.league import promotion_gate
from roco_pvp_agent.battle.evolution.playbook import Playbook
from roco_pvp_agent.battle.evolution.reflect import ReflectionService
from roco_pvp_agent.battle.selfplay import run_selfplay
from roco_pvp_agent.config import get_settings


def _build_rollout_players(a_kind: str, b_kind: str, settings) -> dict | None:
    """按 kind 构造 rollout 玩家；**任一风格时构造双方**（风格侧 StylePlayer、
    其余侧 build_player），否则 None 让 run_selfplay 默认构造。"""
    if not any("style_" in k for k in (a_kind, b_kind)):
        return None
    from roco_pvp_agent.battle.evolution.styles import StylePlayer
    from roco_pvp_agent.battle.selfplay import build_player
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
        "reports": [r.to_dict() for r in reports],        # 逐条编辑报告（审计/CLI 打印原因）
        "rejected_edits": [r.to_dict() for r in rejected_reports],  # 交给下一步的 rejected buffer
        "playbook_before": pb.version,
        "playbook_after": new_pb.version,
        "edits_applied": applied,
        "edits_rejected": len(rejected_reports),
        "report_path": report_path,
        "playbook": new_pb,
    }


# ---------------------------------------------------------------------------
# R4：多步进化（完整闭环：池 + 两级门 + 剥削者 + 回归门 + 健康度）
# ---------------------------------------------------------------------------

# 能量档 → 占比中点（健康度「能量利用率」的确定性近似；energy_max=10 → low≤2/mid≤5/high>5）。
_ENERGY_BAND_MID = {"low": 0.15, "mid": 0.40, "high": 0.80}

# 慢更新「同批 20 实例」（plan R5：上一/当前 Champion 同批 20 实例跑四类）——成本旋钮。
SLOW_UPDATE_INSTANCES = 20


def _make_memory_retriever(store):
    """MemoryStore → 检索器 `(side, situation_key) -> list[dict]`（部署期注入与离线采纳判定共用）。"""
    from environment.datafingerprint import data_digest
    from roco_pvp_agent.battle.evolution.memory import MemoryQuery, two_phase_search

    def retrieve(side, situation_key):
        return two_phase_search(store, MemoryQuery(situation_key=situation_key,
                                                   side=side, data_digest=data_digest()))
    return retrieve


def _strategy_player(playbook, side: str, seed: int, *, llm: bool, settings,
                     memory_retriever=None):
    """被优化方玩家：真实路径 = LLMPlayer 读 `[战术手册]` + 检索记忆注入 `[记忆]`；
    离线/无 key = 确定性 PlaybookPlayer。

    `llm=True` 但无 API key → **降级** PlaybookPlayer（与 build_player 的降级哲学一致：
    无 key 也能跑通闭环，Gate 测量的是确定性基线而非真实 LLM——CLI 已打印警告）。
    """
    from roco_pvp_agent.battle.player import LLMPlayer, PlaybookPlayer
    if llm and getattr(settings, "has_api_key", False):
        return LLMPlayer(side, settings=settings, seed=seed, strategy=playbook.text(),
                         memory=memory_retriever)
    return PlaybookPlayer(side, playbook, seed=seed)


def _opponent_player(spec, side: str, seed: int, *, settings):
    """对手规格 → 玩家：Playbook → PlaybookPlayer；`"style:X"` → StylePlayer；`"random"` → RandomPlayer
    （§十对手用便宜档；`_SCENARIO_OPPONENT` 的冻结基准对手走这里）。"""
    from roco_pvp_agent.battle.evolution.styles import StylePlayer
    from roco_pvp_agent.battle.player import PlaybookPlayer
    if isinstance(spec, Playbook):
        return PlaybookPlayer(side, spec, seed=seed)
    if isinstance(spec, str) and spec.startswith("style:"):
        return StylePlayer(side, spec[len("style:"):], seed=seed)
    if spec == "random":
        from roco_pvp_agent.battle.selfplay import build_player
        return build_player(side, "random", seed=seed, settings=settings)
    raise ValueError(f"未知对手规格：{spec!r}（Playbook / style:attack / random）")


def _score_pair(a_pb, b_spec, instances, *, seeds, team_size, lives, llm, settings,
                memory_retriever=None) -> float:
    """a（被优化方）对 b（对手规格）在给定实例/seed 上的配对胜率（双向对消阵容强度）。"""
    from roco_pvp_agent.battle.evolution.bench import paired_eval

    def make_subject(side, s):
        return _strategy_player(a_pb, side, s, llm=llm, settings=settings,
                                memory_retriever=memory_retriever)

    def make_opponent(side, s):
        return _opponent_player(b_spec, side, s, settings=settings)

    rep = paired_eval(make_subject, make_opponent, instances,
                      seeds=seeds, team_size=team_size, lives=lives)
    return rep["winrate"]


# D_sel 实例场景 → 冻结基准对手（§八：人工 + 极端风格；`_score_strategy` 用）。
# 关键：分数向量按实例的对手类型打——Pareto 前沿/领先实例数才捕获「能打赢多少种对手」
# 的非传递性多样性，而不是只测「打 random 的阵容多样性」。
_SCENARIO_OPPONENT: dict[str, str] = {
    "mirror": "random",
    "energy_denial": "style:energy_denial",
    "fast_attack": "style:attack",
    "stall": "style:stall",
    "status_control": "style:status",
}


def _aggregate_report(playbook, instances, *, seeds, team_size, lives, llm, settings,
                      memory_retriever=None) -> dict:
    """playbook 在实例集上的配对评测汇总：winrate + Wilson 95%CI + n_games + per-instance。

    对手 = 实例**场景匹配**的冻结基准（`_SCENARIO_OPPONENT`，与分数向量同口径）。
    用于「上一 vs 当前 Champion 是否改进」（R5 慢更新）与 **D_test 汇报**（只汇报，
    不进任何优化输入）。确定性：固定实例/seed + 确定性玩家 → 逐位可复现。
    """
    from roco_pvp_agent.battle.evolution.bench import paired_eval, wilson_ci

    total_wins = 0.0
    total_games = 0
    per_instance: dict[str, float] = {}
    for inst in instances:
        opp = _SCENARIO_OPPONENT.get(inst.scenario, "random")

        def make_subject(side, s):
            return _strategy_player(playbook, side, s, llm=llm, settings=settings,
                                    memory_retriever=memory_retriever)

        def make_opponent(side, s):
            return _opponent_player(opp, side, s, settings=settings)

        rep = paired_eval(make_subject, make_opponent, [inst],
                          seeds=seeds, team_size=team_size, lives=lives)
        per_instance[inst.name] = rep["winrate"]
        total_wins += rep["winrate"] * rep["n_games"]
        total_games += rep["n_games"]
    ci = wilson_ci(round(total_wins), total_games) if total_games else (0.0, 1.0)
    return {"winrate": total_wins / total_games if total_games else 0.0,
            "ci95": ci, "ci95_low": ci[0], "ci95_high": ci[1],
            "n_games": total_games, "per_instance": per_instance}


def _score_strategy(playbook, instances, *, seeds, team_size, lives, llm, settings,
                    memory_retriever=None) -> dict[str, float]:
    """候选在 D_sel 实例集上的分数向量（per-instance 胜率；对手 = 实例场景的冻结基准对手）。

    §四/§八：D_sel 使用冻结的基准对手库（人工 + 极端风格）——优化器只看到分数向量，
    实例内容不进反思上下文。每个实例配**场景匹配**的冻结对手（mirror → random；
    其余 → 极端风格），分数向量捕获「对不同类型对手的胜率」。
    """
    return _aggregate_report(playbook, instances, seeds=seeds, team_size=team_size,
                             lives=lives, llm=llm, settings=settings,
                             memory_retriever=memory_retriever)["per_instance"]


def _gate_candidate(pool, cand_pb, instances, mini, *, seeds, minibatch_seeds,
                    team_size, lives, llm, settings, exploiter) -> dict:
    """全量门（入池）+ 前沿扫描 + 回归门 + 晋级——**fast 候选与 epoch 慢更新候选共用**。

    SkillOpt：慢更新候选同样过门禁，不是免检通道。outcome 字段：
    entered / reason / composite / promoted / promotion_reason / regression /
    candidate_exploitability / gated_after_entry。
    """
    scores = _score_strategy(cand_pb, instances, seeds=seeds, team_size=team_size,
                             lives=lives, llm=llm, settings=settings)
    add_out = pool.add(cand_pb, scores,
                       health=1.0 if not cand_pb.validate() else 0.6)   # 合法/降级健康度
    out: dict = {"entered": add_out["entered"], "reason": add_out["reason"],
                 "composite": round(add_out["composite"], 3),
                 "promoted": False, "promotion_reason": None,
                 "regression": None, "candidate_exploitability": None,
                 "gated_after_entry": None}
    if not add_out["entered"]:
        return out
    entry = pool.member(cand_pb.version)
    if entry is not None and entry.status == "archived":
        out["gated_after_entry"] = "P_max 剪枝"          # 入池即被剪 → 不跑晋级（自己打自己无意义）
        return out
    # 前沿扫描：候选对每个前沿成员补成对胜率（复合分 0.70 项原料）——**双向都记**：
    # 只记候选视角会让 Champion 的复合分被中性 0.5 稀释（晋级失真，R4 review M2）。
    for o in pool.pareto_front():
        if o.playbook.version == cand_pb.version:
            continue
        wr = _score_pair(cand_pb, o.playbook, mini, seeds=minibatch_seeds,
                         team_size=team_size, lives=lives, llm=llm, settings=settings)
        pool.record_game(cand_pb.version, o.playbook.version, wr, n=1)
        pool.record_game(o.playbook.version, cand_pb.version, 1.0 - wr, n=1)
    # 挑战者可利用性也测量（与 Champion 同口径，防 0.15 项系统性偏优）
    cand_exploit = exploiter.measure(
        cand_pb, lambda ex, c: _score_pair(ex, c, mini, seeds=minibatch_seeds,
                                           team_size=team_size, lives=lives, llm=llm,
                                           settings=settings))
    pool.set_exploitability(cand_pb.version, cand_exploit)
    out["candidate_exploitability"] = round(cand_exploit, 3)
    # 历史回归门（§九）：对历史池（archive + 历次 Champion）胜率 ≥45%
    hist = [h.playbook for h in pool.history()]
    ok, rows = promotion_gate(
        cand_pb, hist,
        lambda c, h: _score_pair(c, h, mini, seeds=minibatch_seeds,
                                 team_size=team_size, lives=lives, llm=llm, settings=settings))
    promo = pool.try_promote(cand_pb.version, regression_ok=ok)
    out["promoted"] = promo["promoted"]
    out["promotion_reason"] = promo["reason"]
    out["regression"] = {"ok": ok,
                         "rows": [{k: (round(v, 3) if isinstance(v, float) else v)
                                   for k, v in r.items()} for r in rows]}
    return out


def _tr_minibatch(team_size: int, *, n_seeds: int, seed_base: int = 100) -> list:
    """D_tr 训练 minibatch（§八：反思只用 D_tr；D_sel/D_test 只给分数，实例内容不进反思上下文）。

    用 `tr_spirit_pool()`（后 1/4 训练家族，**与 d_sel/d_test 结构不相交**）构造一个训练实例
    （若干固定 seed）。**关键隔离**：训练家族绝不与评估实例 roster 重合——否则 rollout/反思
    LLM 每 step 反复看到 D_sel 基准阵容的精灵组合，优化器会把针对固定评测阵容的反制写进手册
    （评测污染，§九 头号失败模式）。D_sel/D_test 只经 `_aggregate_report` 的分数进入选择/汇报。
    """
    from environment.battle_config import build_battle_rules
    from environment.dataset import DataSource
    from environment.presets import p1_team
    from environment.teambuilder import build_roster
    from roco_pvp_agent.battle.evolution.bench import Instance, tr_spirit_pool
    rules = build_battle_rules(team_size=team_size, lives=2)
    pool_names = tr_spirit_pool()
    if len(pool_names) < 2 * team_size:
        raise ValueError(f"D_tr 训练家族池不足（{len(pool_names)} 只，需 >{2 * team_size}）。")
    picks_a, picks_b = pool_names[:team_size], pool_names[team_size:2 * team_size]
    roster_a = build_roster(p1_team(picks_a), DataSource.VALID, rules)
    roster_b = build_roster(p1_team(picks_b), DataSource.VALID, rules)
    return [Instance(name="tr-rollout-000",
                     scenario="mirror" if roster_a == roster_b else "stall",
                     roster_a=roster_a, roster_b=roster_b,
                     seeds=tuple(seed_base + i for i in range(n_seeds)))]


def _play_rollout(slot_a, slot_b, inst, *, seed, team_size, lives, llm, settings,
                  subject_slot: str = "a", out_dir=None, battle_id=None,
                  memory_retriever=None) -> dict:
    """一局 canonical rollout（父代 vs 采样对手，固定阵容/种子）→ run_selfplay 结果。

    `slot_a/slot_b`：a/b 槽的玩家规格（Playbook 或 `"style:X"`）；`subject_slot` = 被优化方
    （父代，真实 LLM 只给这侧）坐哪个槽（`--a-version sampled` 时父代坐 b）。
    `out_dir` 给出则**轨迹落盘**（§五④：rollout → 轨迹落盘，D_tr 内容，非 D_sel）。
    确定性：注入玩家（PlaybookPlayer 手册指纹 / StylePlayer）+ 固定 seed → 逐位可复现。
    """
    from roco_pvp_agent.battle.selfplay import run_selfplay
    players: dict = {}
    for side, spec in (("a", slot_a), ("b", slot_b)):
        s = seed + (1 if side == "a" else 2)
        if side == subject_slot:
            players[side] = _strategy_player(spec, side, s, llm=llm, settings=settings,
                                             memory_retriever=memory_retriever)
        else:
            players[side] = _opponent_player(spec, side, s, settings=settings)
    return run_selfplay(seed=seed, team_size=team_size, lives=lives,
                        roster_a=inst.roster_a, roster_b=inst.roster_b,
                        players=players, out_dir=out_dir, battle_id=battle_id or f"steps-{seed}",
                        saved_at="steps")


def _action_stats(analysis) -> dict:
    """动作熵 / 换人率（健康度）：从重放分析的 decisions 统计。"""
    from math import log2
    types: list[str] = []
    for ta in analysis.turns:
        for side in ("a", "b"):
            types.append(ta.decisions[side].get("type", "?"))
    n = len(types)
    if not n:
        return {"action_entropy": 0.0, "switch_rate": 0.0}
    counts: dict[str, int] = {}
    for t in types:
        counts[t] = counts.get(t, 0) + 1
    entropy = -sum((c / n) * log2(c / n) for c in counts.values())
    return {"action_entropy": round(entropy, 3),
            "switch_rate": round(counts.get("switch", 0) / n, 3)}


def _energy_utilization(analysis) -> float:
    """能量利用率 = 1 − 平均能量档占比中点（situation_key 的 my_energy_band，确定性）。

    从重放分析的 situation_keys 提取（10 段键，p[4]=我方能量档），无需额外回放。
    """
    ratios: list[float] = []
    for ta in analysis.turns:
        for side in ("a", "b"):
            key = ta.situation_keys.get(side) or ""
            parts = key.split("/")
            if len(parts) == 10:
                ratios.append(_ENERGY_BAND_MID.get(parts[4], 0.5))
    if not ratios:
        return 0.0
    return round(1.0 - sum(ratios) / len(ratios), 3)


def _payoff_antisym(pool) -> float:
    """收益矩阵反对称分量（池健康度，§九）：‖M−Mᵀ‖_F / ‖M‖_F。高 → 循环/非传递性丰富。"""
    M = pool.payoff.matrix()
    n = len(M)
    if n < 2:
        return 0.0
    num = sum((M[i][j] - M[j][i]) ** 2 for i in range(n) for j in range(n))
    den = sum(M[i][j] ** 2 for i in range(n) for j in range(n))
    return round(math.sqrt(num) / math.sqrt(den), 3) if den else 0.0


def run_steps(*, n: int, seed: int = 7, out_dir: str = "artifacts",
              settings=None, M: int = 24, team_size: int = 3, lives: int = 2,
              instances=None, seeds_per_instance: int | None = None,
              llm: bool = False, reflect_llm=None, init_playbook=None,
              rejected=None, reject_buffer_size: int = 8,
              a_version: str = "champion", b_version: str = "sampled",
              minibatch_seeds: int = 2, pool=None, meta: str = "",
              value_fn=None, memory_dir: str | None = None) -> dict:
    """R4 多步进化闭环：池 + 两级门 + 剥削者 + 回归门（§五 ①–⑩ + §九）。

    每 step：**①父代** = 当前 Champion → **③对手采样**（Pareto 领先实例数加权 / PFSP 兜底）
    → **④rollout**（minibatch，champion vs 采样对手）→ **⑤信度分配** → **⑥双分析师** →
    **⑧有界编辑** → **⑨廉价门**（同一 minibatch，未改进 → 写入 rejected buffer，本 step 结束）
    → **⑩全量门**（D_sel 分数向量 → `pool.add` 入池门 → 前沿扫描 + 历史回归门 → `try_promote`）
    → 专职剥削者测量可利用性 → 健康度 → 池落盘 `pool.json`（可续跑/审计）。

    R5：`pool=` 传入既有池则**续跑**（epoch 调度跨 epoch 复用，不再重建初始手册）；
    `meta=` 是 Meta Playbook 文本（只进反思提示，不进对战玩家）。
    确定性：注入确定性玩家（PlaybookPlayer/StylePlayer）+ 假反思 LLM 时逐位可复现。
    `llm=True` 时被优化方用真实 LLM 读手册；对手恒为便宜档（PlaybookPlayer/Style）。
    """
    from roco_pvp_agent.battle.evolution.bench import build_instances
    from roco_pvp_agent.battle.evolution.league import (
        EXPLOITER_RESET_EVERY,
        Exploiter,
        sample_opponent,
    )
    from roco_pvp_agent.battle.evolution.pool import PlaybookPool

    settings = settings or get_settings()
    from roco_pvp_agent.battle.evolution.memory import MemoryStore
    store = MemoryStore(memory_dir) if memory_dir else None
    retriever = _make_memory_retriever(store) if store else None
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if instances is None:
        instances = build_instances("d_sel", team_size=team_size)
    for inst in instances:
        if len(inst.roster_a) != team_size or len(inst.roster_b) != team_size:
            raise ValueError(f"实例「{inst.name}」规模与 team_size={team_size} 不符。")

    if pool is None:
        # 新建池：初始手册在 D_sel 上打分入池（首个成员即 Champion）。
        pool = PlaybookPool([i.name for i in instances])
        init_pb = init_playbook or Playbook.initial()
        init_scores = _score_strategy(init_pb, instances, seeds=seeds_per_instance,
                                      team_size=team_size, lives=lives, llm=llm, settings=settings)
        pool.add(init_pb, init_scores)
    else:
        if pool.instances != [i.name for i in instances]:
            raise ValueError(
                f"续跑池实例空间与本次评测不一致（须同一 D_sel 实例集）。")
    pool.save(str(out_dir / "pool.json"))

    # D_tr 训练 minibatch（rollout/廉价门/前沿扫描/回归门/剥削者共用——**不含 D_sel 实例内容**）
    mini = _tr_minibatch(team_size, n_seeds=minibatch_seeds)
    if not mini:
        raise ValueError("D_tr 训练 minibatch 为空——无法 rollout（team_size 过小？）。")
    reject_buffer: list[dict] = list(rejected or [])
    exploiter = Exploiter.fork(pool.champion().playbook, step=0, seed=seed)
    pool_path = out_dir / "pool.json"
    steps: list[dict] = []
    records: list[dict] = []                       # R5：D_tr 轨迹（V 档位重训数据 + 审计）
    counts = {"entered": 0, "promoted": 0, "cheap_rejected": 0, "full_rejected": 0,
              "edits_applied": 0, "edits_rejected": 0}

    for i in range(1, n + 1):
        step_seed = seed + i
        champ_pb = pool.champion().playbook
        opp = sample_opponent(pool, seed=step_seed)
        opp_label = f"pb:{opp.version}" if isinstance(opp, Playbook) else str(opp)

        # ④ rollout（D_tr minibatch：父代 vs 采样对手；引擎 seed 随 step → 相同对手不逐位重复）
        champ_wr = _score_pair(champ_pb, opp, mini, seeds=minibatch_seeds,
                               team_size=team_size, lives=lives, llm=llm, settings=settings)
        # --a-version/--b-version：父代坐哪个槽（默认 a=champion / b=sampled）；评分恒为父代视角
        if a_version == "sampled" and b_version != "sampled":
            subject_slot, slot_a, slot_b = "b", opp, champ_pb
        else:
            subject_slot, slot_a, slot_b = "a", champ_pb, opp
        rollout = _play_rollout(slot_a, slot_b, mini[0], seed=step_seed,
                                team_size=team_size, lives=lives, llm=llm, settings=settings,
                                subject_slot=subject_slot, out_dir=str(out_dir),
                                battle_id=f"steps-{step_seed}-{i}",
                                memory_retriever=retriever)
        record = rollout["record"]

        # 记忆采纳判定 + Q 更新（闭环：注入的记忆是否被采纳 → 反馈 Q 值，下次检索更准）
        memory_adoption = None
        if store is not None:
            from roco_pvp_agent.battle.evolution.memory_inject import apply_adoption
            memory_adoption = apply_adoption(store, record, retriever, winner=rollout["winner"])

        # ⑤ 信度分配 → 卡片；⑥ 双分析师（失败分析师可见 rejected buffer + Meta Playbook）；⑧ 有界编辑
        analysis = analyze_record(record, value_fn=value_fn)
        cards = mine_critical_turns(analysis, record, M=M)
        service = ReflectionService(settings, llm=reflect_llm, meta=meta)
        candidates = service.reflect(cards, rejected=reject_buffer)
        cand_pb, reports = bounded_edit(champ_pb, candidates,
                                        version=pool.next_version())  # 全局唯一候选版本
        applied = sum(1 for r in reports if r.status == "applied")
        counts["edits_applied"] += applied
        counts["edits_rejected"] += sum(1 for r in reports if r.status == "rejected")
        if reports:                                    # 编辑审计落盘（§3.1，可追溯）
            append_report(reports, str(out_dir / "edit_apply_report.jsonl"))

        # ⑨ 廉价门：候选在同一 minibatch 重跑；未改进（无编辑 或 明显回退）→ 写 rejected buffer。
        # B=4 局小批胜率粒度粗（0.25），严格「平手即拒」会把强候选也挡在全量门外——
        # 权威选择由 ⑩ 全量门（D_sel 分数向量 + 入池门）承担，廉价门只挡明显回退（省钱）。
        # （此「平手走全量门」偏离 §五⑨ 字面「未改进 → 写 R」，Gate 时确认是否收紧。）
        cand_wr = _score_pair(cand_pb, opp, mini, seeds=minibatch_seeds,
                              team_size=team_size, lives=lives, llm=llm, settings=settings)
        row: dict = {"step": i, "champion": champ_pb.version, "opponent": opp_label,
                     "turn_count": rollout["turn_count"], "cards": len(cards),
                     "candidates": len(candidates), "champ_winrate": champ_wr,
                     "candidate_winrate": cand_wr,
                     "memory_adoption": memory_adoption,
                     "entered": False, "promoted": False, "front_size": 0,
                     # 行内嵌报告剥 timestamp：审计落盘文件保留时间戳，行内快照保持确定性可复现
                     "reports": [{k: v for k, v in r.to_dict().items() if k != "timestamp"}
                                 for r in reports],
                     "reflection_diagnostics": service.diagnostics}
        no_edits = cand_pb.version == champ_pb.version          # 无编辑应用 → 候选 = 父代
        if no_edits or cand_wr < champ_wr:
            row["gate"] = "cheap"
            row["gate_reason"] = ("无编辑应用（候选 = 父代）" if no_edits
                                  else f"候选 {cand_wr:.3f} < 父代 {champ_wr:.3f}（明显回退）")
            counts["cheap_rejected"] += 1
            reject_buffer = _bump_reject(reject_buffer, reports, reject_buffer_size,
                                         include_applied=True)   # 造成回退的 applied 编辑也是负反馈
        else:
            # ⑩ 全量门（fast 候选与 epoch 慢更新候选共用 `_gate_candidate`）
            row["gate"] = "full"
            out = _gate_candidate(pool, cand_pb, instances, mini,
                                  seeds=seeds_per_instance, minibatch_seeds=minibatch_seeds,
                                  team_size=team_size, lives=lives, llm=llm, settings=settings,
                                  exploiter=exploiter)
            row.update({k: v for k, v in out.items() if v is not None})
            if out["entered"]:
                counts["entered"] += 1
                if out["promoted"]:
                    counts["promoted"] += 1
            else:
                counts["full_rejected"] += 1
                reject_buffer = _bump_reject(reject_buffer, reports, reject_buffer_size,
                                             include_applied=True)

        # 专职剥削者：测量当前 Champion 的可利用性（进复合分 0.15）；每 N step 重置
        # （R5 接 epoch 后按规划改为每 2 epoch 重置；R4 用 step 近似）
        if i % EXPLOITER_RESET_EVERY == 0:
            exploiter.reset(pool.champion().playbook, step=i)
        exploit = exploiter.measure(
            pool.champion().playbook,
            lambda ex, ch: _score_pair(ex, ch, mini, seeds=minibatch_seeds,
                                       team_size=team_size, lives=lives, llm=llm,
                                       settings=settings))
        pool.set_exploitability(pool.champion().playbook.version, exploit)
        pool.record_game(exploiter.playbook.version, pool.champion().playbook.version,
                         exploit, n=1)          # §九②：剥削者对局注入收益矩阵（α-rank/PFSP 可见）
        row["exploitability"] = round(exploit, 3)

        row["front_size"] = len(pool.pareto_front())
        row["archive_size"] = len(pool.archive)
        row["health"] = {
            **_action_stats(analysis),
            "energy_utilization": _energy_utilization(analysis),
            "front_width": len(pool.pareto_front()),
            "payoff_antisym": _payoff_antisym(pool),
            "edit_acceptance": round(counts["edits_applied"] / max(1, counts["edits_applied"]
                                                                  + counts["edits_rejected"]), 3),
        }
        pool.save(str(pool_path))
        steps.append(row)
        records.append(record)

    champ_e = pool.champion()
    return {
        "n": n,
        "seed": seed,
        "steps": steps,
        "records": records,                     # R5：D_tr 轨迹（V 档位重训数据）
        "pool": pool.to_dict(),
        "pool_path": str(pool_path),
        "final": {
            "champion": champ_e.playbook.version if champ_e else None,
            "best": pool.best().playbook.version if pool.best() else None,
            "best_score": round(pool._best_score, 3),
            "front_size": len(pool.pareto_front()),
            "archive_size": len(pool.archive),
            **counts,
        },
        "pool_object": pool,
    }


def _bump_reject(buffer: list[dict], reports, cap: int, *, include_applied: bool = False) -> list[dict]:
    """把本 step 的负反馈编辑并入 rejected buffer（失败分析师下一 step 的证据），截断到 cap。

    `include_applied=True`：候选被门禁拒掉时，**造成回退的 applied 编辑也进 buffer**
    （标记 `applied_then_gated`）——§3.1「被门禁拒掉的编辑连同分数下降存档」，
    否则恰好把肇事的编辑排除在负反馈外（m3 修复）。
    """
    items: list[dict] = []
    for r in reports:
        if r.status == "rejected":
            d = r.to_dict()
        elif include_applied and r.status == "applied":
            d = r.to_dict()
            d["status"] = "applied_then_gated"        # 曾应用但候选过不了门禁（负反馈）
        else:
            continue
        d.pop("timestamp", None)                       # m9：buffer 进反思提示须剥时间戳（确定性）
        items.append(d)
    merged = list(buffer) + items
    return merged[-cap:] if cap > 0 else merged


def run_epochs(*, n: int = 8, seed: int = 7, out_dir: str = "artifacts",
               settings=None, E: int = 8, no_slow_update: bool = False,
               instances=None, dtest_instances=None, seeds_per_instance=None,
               llm: bool = False, reflect_llm=None, init_playbook=None,
               reject_buffer_size: int = 8, a_version: str = "champion",
               b_version: str = "sampled", minibatch_seeds: int = 2,
               M: int = 24, team_size: int = 3, lives: int = 2,
               pool=None, on_epoch=None, memory_dir: str | None = None) -> dict:
    """R5 epoch 调度：每 E 步快速进化 + 慢更新（[PROTECTED] 固化/撤回/移除，同样过门禁）+
    D_test 汇报（只汇报，不回流任何优化决策）。

    每 epoch：
      ① **E 步快速进化**（run_steps，池跨 epoch 续跑——champion 沿 epoch 演进）；
      ② **慢更新**（除非 `no_slow_update`）：比较上一/当前 Champion 在同批 D_sel 实例上的
         聚合胜率 → `slow_update`（稳定成功固化 / 改进保留 / 回退撤回 / 持续失败移除）
         → 新手册（含 `[PROTECTED]` 固化）**同样过门禁**——非降级门：D_sel 不劣于现
         Champion 才应用（`pool.apply_slow_update`）；会回退照样被拒（不是免检通道）。
         Meta Playbook 更新（编辑接受率/持续失败，喂给下一 epoch 的反思）；
      ③ **D_test 汇报**：Champion 在隐藏集（人工反套路 + 未公开阵容）上的 winrate + 95%CI
         ——数字只进汇报，不进任何优化输入（§八 逻辑隔离）。
    `no_slow_update=True`：跳过 ②——A/B 消融（SkillOpt：关慢更新 → D_test 退化）。
    """
    from roco_pvp_agent.battle.evolution.bench import build_instances
    from roco_pvp_agent.battle.evolution.editor import diff_rules, slow_update
    from roco_pvp_agent.battle.evolution.meta import MetaPlaybook
    from roco_pvp_agent.battle.evolution.playbook import MODULE_KEYS
    from roco_pvp_agent.battle.evolution.pool import PlaybookPool
    from roco_pvp_agent.battle.evolution.valuefn import train_value_fn

    settings = settings or get_settings()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if instances is None:
        instances = build_instances("d_sel", team_size=team_size)
    if dtest_instances is None:
        dtest_instances = build_instances("d_test", team_size=team_size)
    for inst in instances:
        if len(inst.roster_a) != team_size or len(inst.roster_b) != team_size:
            raise ValueError(f"实例「{inst.name}」规模与 team_size={team_size} 不符。")

    # 池 + 初始手册（跨 epoch 续跑——champion 沿 epoch 演进，不重建）
    if pool is None:
        pool = PlaybookPool([i.name for i in instances])
        init_pb = init_playbook or Playbook.initial()
        init_scores = _score_strategy(init_pb, instances, seeds=seeds_per_instance,
                                      team_size=team_size, lives=lives, llm=llm, settings=settings)
        pool.add(init_pb, init_scores)
    else:
        if pool.instances != [i.name for i in instances]:
            raise ValueError("续跑池实例空间与本次评测不一致（须同一 D_sel 实例集）。")
    meta = MetaPlaybook()
    stable_for = {key: 0 for key in MODULE_KEYS}
    prev_champion = pool.champion().playbook
    prev_dtest = _aggregate_report(prev_champion, dtest_instances, seeds=seeds_per_instance,
                                   team_size=team_size, lives=lives, llm=llm, settings=settings)

    epochs: list[dict] = []
    value_fn = None                                  # R5 档位 A（上一 epoch 重训的 V；None → 启发式）
    for e in range(1, n + 1):
        # ① E 步快速进化（池续跑；meta 只给反思优化器；V 用上一 epoch 重训档位）
        step_rep = run_steps(n=E, seed=seed + (e - 1) * E, out_dir=out_dir,
                             settings=settings, M=M, team_size=team_size, lives=lives,
                             instances=instances, seeds_per_instance=seeds_per_instance,
                             llm=llm, reflect_llm=reflect_llm, pool=pool,
                             meta=meta.render(), a_version=a_version, b_version=b_version,
                             minibatch_seeds=minibatch_seeds, value_fn=value_fn,
                             memory_dir=memory_dir)
        for s in step_rep["steps"]:                  # Meta：编辑接受率/诊断奏效度（M4/n2）
            meta.observe_step(s)
        cur_champion = pool.champion().playbook

        # ② 慢更新（除非 A/B 消融关掉）——**同样过门禁**（非降级门 + 回归门，会回退照样被拒）
        slow: dict = {"candidates": 0, "entered": False, "promoted": False,
                      "reason": "已禁用（--no-slow-update）", "promotion_reason": None}
        improved: bool | None = None
        if not no_slow_update:
            # 「同批 20 实例」（plan R5）——慢更新比较用固定 20 实例批，与全量门分开（成本旋钮）
            slow_inst = instances[:SLOW_UPDATE_INSTANCES]
            diff = diff_rules(prev_champion, cur_champion)
            for key in MODULE_KEYS:
                stable_for[key] = (stable_for[key] + 1) if diff[key]["stable"] else 0
            prev_scr = _aggregate_report(prev_champion, slow_inst, seeds=seeds_per_instance,
                                         team_size=team_size, lives=lives, llm=llm,
                                         settings=settings)
            cur_scr = _aggregate_report(cur_champion, slow_inst, seeds=seeds_per_instance,
                                        team_size=team_size, lives=lives, llm=llm,
                                        settings=settings)
            # 非严格 improved：聚合胜率持平也算改进（m2——少样本下严格 > 会把复合分晋级的
            # 真实改进误判为回退而静默撤回）
            improved = cur_scr["winrate"] >= prev_scr["winrate"] - 1e-6
            new_pb, cands, reports = slow_update(prev_champion, cur_champion,
                                                 improved=improved, stable_for=stable_for,
                                                 persistent_failures=meta.persistent_failures(),
                                                 version=pool.next_version())
            if cands:
                new_scr = _aggregate_report(new_pb, slow_inst, seeds=seeds_per_instance,
                                            team_size=team_size, lives=lives, llm=llm,
                                            settings=settings)
                # 非降级门：新手册 D_sel 不劣于现 Champion（保护是保守管理动作）
                non_degrade = new_scr["winrate"] + 1e-6 >= cur_scr["winrate"]
                # 回归门：对历史池（archive + 历次 Champion）胜率 ≥45%（m4/M6——慢更新
                # 不是免检通道，不能绕过 R4 的「循环变弱」防线）
                mini = _tr_minibatch(team_size, n_seeds=minibatch_seeds)
                hist = [h.playbook for h in pool.history()]
                reg_ok, reg_rows = True, []
                if hist:
                    reg_ok, reg_rows = promotion_gate(
                        new_pb, hist,
                        lambda c, h: _score_pair(c, h, mini, seeds=minibatch_seeds,
                                                 team_size=team_size, lives=lives, llm=llm,
                                                 settings=settings))
                if non_degrade and reg_ok:
                    applied = pool.apply_slow_update(new_pb, new_scr["per_instance"])
                    append_report(reports, str(out_dir / "edit_apply_report.jsonl"))
                    slow = {"candidates": len(cands), "entered": applied["applied"],
                            "promoted": False,
                            "reason": ("非降级门+回归门通过，慢更新已应用" if applied["applied"]
                                       else applied["reason"]),
                            "promotion_reason": None}
                else:
                    why = "非降级门未过" if not non_degrade else "回归门未过"
                    slow = {"candidates": len(cands), "entered": False, "promoted": False,
                            "reason": f"{why}（新 {new_scr['winrate']:.3f} vs "
                                      f"现 {cur_scr['winrate']:.3f}）",
                            "promotion_reason": None}
            else:
                slow["reason"] = "无慢更新候选（无稳定固化/回退/持续失败）"
            # Meta Playbook：本 epoch 被拒编辑 → 跨 epoch 持续失败计数（供下轮慢更新）
            epoch_rejected = {r["text"] for s in step_rep["steps"]
                              for r in s["reports"] if r.get("status") == "rejected"}
            for t in epoch_rejected:
                if t.strip():                          # 空文本 no-op（M1 双保险）
                    meta.mark_persistent_failure(t)

        # ③ 重训 V 档位（§ R5：每 epoch 重训；中局 AUC ≥0.75 才启用档位 A，否则启发式）
        trained = train_value_fn(step_rep["records"], seed=seed + e)
        value_fn = trained
        v_tier = "a1" if trained is not None else "a0"

        # ④ D_test 汇报（只汇报，不进任何优化输入）
        champ = pool.champion().playbook
        dtest = _aggregate_report(champ, dtest_instances, seeds=seeds_per_instance,
                                  team_size=team_size, lives=lives, llm=llm, settings=settings)
        # R8 健康看板：把逐 step 的健康指标（动作熵/换人率/能量利用率/编辑接受率）聚合到
        # epoch 层，供 HealthGate 暂停评估（§九 看板指标）。**epoch_health 不含 D_test**——
        # dtest 是独立的汇报字段（下方 dtest 键），健康闸/优化决策一律不读它（防测试集早停泄漏）。
        step_healths = [s.get("health", {}) for s in step_rep["steps"]]
        def _mean(key):
            vals = [h.get(key) for h in step_healths if h.get(key) is not None]
            return round(sum(vals) / len(vals), 3) if vals else None
        epoch_health = {
            "action_entropy": _mean("action_entropy"),
            "switch_rate": _mean("switch_rate"),
            "energy_utilization": _mean("energy_utilization"),
            "edit_acceptance": _mean("edit_acceptance"),
            "payoff_antisym": _payoff_antisym(pool),
            "front_width": len(pool.pareto_front()),
        }
        epochs.append({
            "epoch": e,
            "champion": champ.version,
            "improved": improved,
            "slow_update": slow,
            "v_tier": v_tier,
            "epoch_health": epoch_health,
            "dtest": {"winrate": round(dtest["winrate"], 4),
                      "ci95": [round(x, 4) for x in dtest["ci95"]],
                      "ci95_low": round(dtest["ci95_low"], 4),
                      "ci95_high": round(dtest["ci95_high"], 4),
                      "n_games": dtest["n_games"]},
            "dtest_delta": round(dtest["winrate"] - prev_dtest["winrate"], 4),
            "front_size": len(pool.pareto_front()),
            "archive_size": len(pool.archive),
        })
        if on_epoch is not None and not on_epoch(epochs[-1], pool):
            break                            # R8：健康度异常/注册表叫停 → 提前结束本批 epoch
        prev_dtest = dtest
        prev_champion = champ
        pool.save(str(out_dir / "pool.json"))

    return {"n": n, "E": E, "no_slow_update": no_slow_update,
            "epochs": epochs, "pool": pool.to_dict(),
            "pool_path": str(out_dir / "pool.json"),
            "pool_object": pool, "meta": meta}
