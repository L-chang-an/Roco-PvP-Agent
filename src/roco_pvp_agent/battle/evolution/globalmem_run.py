"""G5 GlobalMem 完整编排：固定池取阵容 → 战斗（双重注入）→ 双视角分析 → 双库落地 → Q 更新
→ 周期性 A/B 度量。

一局的完整链路（对应设计的 ①–⑦）：
1. 从固定实例池取阵容（`bench.build_instances`；LLM 自选阵容 = G4，已延后）；
2. 战斗：开局注入 `[全局经验]`（G2）+ 每回合注入 `[记忆]`（记忆接线）；
3. 双视角分析：`GlobalAnalyst.analyze_both`（G3，a/b 各一次、互不可见）；
4. GlobalMem 落库：`apply_decision`（update→supersede / create→add / skip→no-op）；
5. 局部记忆提取：`reflect.store_experiences`（确定性，不调 LLM）；
6. 双库 Q 更新：**先落库、再更新本局实际加载的那条**——若分析把它 supersede 了，
   append-only 保证旧条目仍在，更新的正是本局真正用过的那条（不会把胜负错记到新建条目上）；
7. 每 `ab_every` 局做一次 A/B：同批实例，subject 分别「开/关 GlobalMem」各跑一遍。

**A/B 是 GlobalMem 唯一的有效性证据**，也比 Playbook 门禁弱：它证明「记忆整体有用」，
不证明「某一条有用」——单条价值只能靠同 matchup 桶内的 Q 排序间接反映（拍板时已接受此取舍）。

**离线路径的边界（诚实标注）**：无 key 时对战玩家是确定性随机策略，它**不读**注入文本，
所以离线 A/B 的 delta 恒 ≈ 0——离线只验证编排链路与度量管线，真实效果必须用 `--llm` 验证
（同 R4「离线基线不产生晋级」的哲学）。
"""

from __future__ import annotations

from pathlib import Path

from environment.datafingerprint import data_digest, rules_digest
from environment.dataset import DEFAULT_SOURCE, DataSource

from roco_pvp_agent.battle.evolution.analysis import analyze_record
from roco_pvp_agent.battle.evolution.bench import build_instances, paired_eval
from roco_pvp_agent.battle.evolution.globalmem import (
    GlobalMemStore,
    make_global_retriever,
    update_global_q,
)
from roco_pvp_agent.battle.evolution.globalmem_analyst import GlobalAnalyst, apply_decision
from roco_pvp_agent.battle.evolution.memory import MemoryStore
from roco_pvp_agent.battle.evolution.memory_inject import apply_adoption
from roco_pvp_agent.battle.evolution.reflect import store_experiences
from roco_pvp_agent.battle.evolution.run import _make_memory_retriever
from roco_pvp_agent.battle.player import FakeLLMPlayer, LLMPlayer
from roco_pvp_agent.battle.selfplay import build_player, run_selfplay
from roco_pvp_agent.config import get_settings
from roco_pvp_agent.llm import cache_hit_rate


class _OfflineGlobalMemPlayer:
    """离线确定性玩家 + **真实执行** GlobalMem 检索与记录。

    为什么需要它：`FakeLLMPlayer` 不接检索器，用它跑离线编排会让「检索 → 记录 →
    Q 更新」整段链路无法验证。本类把检索/记录做真，决策仍走确定性随机流——
    于是离线也能端到端验证编排，同时保持确定性。

    **注意**：它不读注入文本（决策与注入无关），所以离线 A/B 的 delta 恒 ≈ 0。
    """

    def __init__(self, side: str, *, seed: int, global_mem=None, memory=None) -> None:
        self.side = side
        self.kind = "offline_gm"
        self._inner = FakeLLMPlayer(side, seed=seed)
        self._global_mem = global_mem
        self._memory = memory
        self.loaded_global_mem_id: str | None = None
        self._turn_log: list[dict] = []       # 供局部记忆采纳判定（apply_adoption）

    def on_match_start(self, observation: dict) -> None:
        self.loaded_global_mem_id = None
        self._turn_log = []
        if self._global_mem is not None:
            hits = self._global_mem(self.side, observation)
            if hits:
                self.loaded_global_mem_id = hits[0].get("entry_id")
        return self._inner.on_match_start(observation)

    def decide(self, observation: dict, legal: list[dict], items: list[str]):
        if self._memory is not None:          # 走一遍检索（验证链路），但不改决策
            from environment.evaluate import situation_key
            self._memory(self.side, situation_key(observation))
        dec = self._inner.decide(observation, legal, items)
        from environment.evaluate import situation_key
        self._turn_log.append({"turn": observation.get("turn"),
                               "situation_key": situation_key(observation),
                               "action": dict(dec.action), "item": dec.item,
                               "prediction": ""})
        return dec

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _make_player(side: str, seed: int, *, llm: bool, settings,
                 global_mem=None, memory=None):
    """被观测方玩家：有 key 且 `llm=True` → 真实 LLMPlayer（读注入）；否则离线确定性。"""
    if llm and getattr(settings, "has_api_key", False):
        return LLMPlayer(side, settings=settings, seed=seed,
                         memory=memory, global_mem=global_mem)
    return _OfflineGlobalMemPlayer(side, seed=seed, global_mem=global_mem, memory=memory)


class DeterministicAnalyst:
    """**离线链路验证用**的确定性"分析师"（零 LLM）。

    产出的 `strategy_text` 只是从战报确定性拼出来的占位描述——**不是真经验**，
    只为让无 key 环境也能端到端验证「create → 下局命中加载 → Q 更新 → supersede」整条闭环
    （与 R4 用 `PlaybookPlayer` 离线验证门禁闭环同一哲学）。

    规则：本局加载过 → `update`（验证 supersede 路径）；否则 → `create`。
    """

    def __init__(self) -> None:
        self.diagnostics: list[dict] = []

    def analyze(self, record: dict, side: str, *, analysis=None, store=None) -> dict | None:
        analysis = analysis or analyze_record(record)
        if not analysis.replay_ok:
            self.diagnostics.append({"side": side, "status": "rejected",
                                     "reason": "replay_ok=False"})
            return None
        won = record.get("winner") == side
        loaded = record.get(f"global_mem_{side}")
        decision = "update" if (loaded and store is not None
                                and store.get(loaded) is not None) else "create"
        text = (f"[离线占位经验] {side} 方在 {record.get('battle_id', '')} "
                f"{'胜' if won else '未胜'}（{analysis.turn_count} 回合）：保持既有节奏。")
        self.diagnostics.append({"side": side, "status": "ok", "decision": decision})
        return {"outcome": "win" if won else "loss", "root_cause": "离线占位",
                "decision": decision, "strategy_text": text, "reason": "离线链路验证"}

    def analyze_both(self, record: dict, *, analysis=None, store=None) -> dict:
        analysis = analysis or analyze_record(record)
        return {s: self.analyze(record, s, analysis=analysis, store=store)
                for s in ("a", "b")}


def measure_ab(*, instances, globalmem_dir, settings, team_size: int = 3, lives: int = 2,
               seeds: int = 1, llm: bool = False, seed_offset: int = 0,
               source: DataSource = DEFAULT_SOURCE) -> dict:
    """周期性 A/B：同批实例，subject 分别「开 GlobalMem」与「关 GlobalMem」各跑一遍。

    只**读** store（不写）；关闭侧传 `global_mem=None`，两侧对手同为便宜档（random）。
    返回 `{on, off, delta}`，各含 winrate / Wilson CI / n_games。
    """
    store = GlobalMemStore(globalmem_dir,
                           max_strategy_tokens=settings.globalmem_max_tokens)
    retriever = make_global_retriever(store, data_digest=data_digest(), source=source,
                                      delta=settings.globalmem_delta,
                                      lam=settings.globalmem_lam,
                                      top_k=settings.globalmem_top_k)

    def mk_on(side, s):
        return _make_player(side, s, llm=llm, settings=settings, global_mem=retriever)

    def mk_off(side, s):
        return _make_player(side, s, llm=llm, settings=settings, global_mem=None)

    def mk_opp(side, s):
        return build_player(side, "random", seed=s, settings=settings)

    kw = dict(seeds=seeds, team_size=team_size, lives=lives, seed_offset=seed_offset)
    on = paired_eval(mk_on, mk_opp, instances, **kw)
    off = paired_eval(mk_off, mk_opp, instances, **kw)
    return {
        "on": {"winrate": round(on["winrate"], 4), "ci95": [round(x, 4) for x in on["ci95"]],
               "n_games": on["n_games"]},
        "off": {"winrate": round(off["winrate"], 4), "ci95": [round(x, 4) for x in off["ci95"]],
                "n_games": off["n_games"]},
        "delta": round(on["winrate"] - off["winrate"], 4),
    }


def run_battles(*, n: int, seed: int = 7, out_dir: str = "artifacts/gm",
                settings=None, team_size: int = 3, lives: int = 2,
                instances=None, llm: bool = False, analyst_llm=None,
                globalmem_dir: str | None = None, memory_dir: str | None = None,
                ab_every: int = 0, ab_instances=None, ab_seeds: int = 1,
                fake_analyst: bool = False,
                source: DataSource = DEFAULT_SOURCE) -> dict:
    """GlobalMem 完整闭环 n 局。返回 `{n, battles, globalmem, memory, ab}`。

    - `instances`：固定阵容池（None → `build_instances("d_sel")`），逐局轮转取用；
    - `globalmem_dir` / `memory_dir`：**给出才启用**该库（None → 该环节全 no-op）；
    - `analyst_llm`：分析师注入缝（测试用 fake）。无 key 且未注入 → 跳过分析并显式标注；
    - `ab_every > 0`：每 N 局做一次 A/B（`ab_instances` 缺省复用训练实例的前几个）。

    确定性：离线路径（无 key）同 seed 两次逐位相同。
    """
    settings = settings or get_settings()
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    instances = instances or build_instances("d_sel", team_size=team_size)
    if not instances:
        raise ValueError("实例池为空，无法编排对局。")
    digest, rdigest = data_digest(), rules_digest()

    gstore = (GlobalMemStore(globalmem_dir,
                             max_strategy_tokens=settings.globalmem_max_tokens)
              if globalmem_dir else None)
    mstore = MemoryStore(memory_dir) if memory_dir else None
    gretriever = (make_global_retriever(gstore, data_digest=digest, source=source,
                                        delta=settings.globalmem_delta,
                                        lam=settings.globalmem_lam,
                                        top_k=settings.globalmem_top_k)
                  if gstore else None)
    mretriever = _make_memory_retriever(mstore) if mstore else None
    # 分析师：显式注入 fake > `fake_analyst`（离线确定性）> 有 key 且开了 --llm；否则跳过。
    analyst = None
    if gstore is not None:
        if analyst_llm is not None:
            analyst = GlobalAnalyst(settings, llm=analyst_llm)
        elif fake_analyst:
            analyst = DeterministicAnalyst()
        elif llm and getattr(settings, "has_api_key", False):
            analyst = GlobalAnalyst(settings)

    battles: list[dict] = []
    ab_runs: list[dict] = []
    for i in range(1, n + 1):
        inst = instances[(i - 1) % len(instances)]
        bseed = seed + i
        players = {s: _make_player(s, bseed + (1 if s == "a" else 2), llm=llm,
                                   settings=settings, global_mem=gretriever,
                                   memory=mretriever) for s in ("a", "b")}
        out = run_selfplay(seed=bseed, team_size=team_size, lives=lives,
                           roster_a=inst.roster_a, roster_b=inst.roster_b,
                           players=players, out_dir=str(out_path),
                           battle_id=f"gm-{bseed}-{i}", settings=settings)
        record = out["record"]
        analysis = analyze_record(record)
        # 提示缓存观测：只有真实 LLMPlayer 有 usage（离线玩家无）。
        battle_usage: dict[str, int] = {}
        for p in players.values():
            for k, v in (getattr(p, "usage", None) or {}).items():
                battle_usage[k] = battle_usage.get(k, 0) + v
        row: dict = {"battle": i, "seed": bseed, "instance": inst.name,
                     "winner": record.get("winner"), "turns": out["turn_count"],
                     "replay_ok": out["replay_ok"],
                     "loaded": {s: record.get(f"global_mem_{s}") for s in ("a", "b")},
                     "globalmem": {}, "memory": {}, "analyst": "skipped",
                     "usage": battle_usage,
                     "cache_hit_rate": cache_hit_rate(battle_usage)}

        # ③④ 双视角分析 + GlobalMem 落库
        if analyst is not None:
            row["analyst"] = "ran"
            decisions = analyst.analyze_both(record, analysis=analysis, store=gstore)
            for side, d in decisions.items():
                res = apply_decision(gstore, d, record=record, side=side,
                                     data_digest=digest, rules_digest=rdigest, source=source)
                row["globalmem"][side] = {"action": res["action"], "ok": res["ok"],
                                          "entry_id": res.get("entry_id"),
                                          "reason": res.get("reason")}

        # ⑤ 局部记忆提取（确定性，不调 LLM）
        if mstore is not None:
            row["memory"]["extracted"] = len(store_experiences(record, memory_dir,
                                                               analysis=analysis))

        # ⑥ Q 更新——**先落库、再更新本局实际加载的那条**（append-only 保证旧条目仍在）
        if gstore is not None:
            for side in ("a", "b"):
                loaded = record.get(f"global_mem_{side}")
                if loaded and gstore.get(loaded) is not None:
                    q = update_global_q(gstore, loaded, record.get("winner") == side,
                                        alpha=settings.globalmem_alpha)
                    row["globalmem"].setdefault(side, {})["q_after"] = q
        if mstore is not None and mretriever is not None:
            row["memory"]["adoption"] = apply_adoption(mstore, record, mretriever,
                                                       winner=record.get("winner"))

        # ⑦ 周期性 A/B（只读 store）
        if ab_every and gstore is not None and i % ab_every == 0:
            ab = measure_ab(instances=ab_instances or instances[:min(3, len(instances))],
                            globalmem_dir=globalmem_dir, settings=settings,
                            team_size=team_size, lives=lives, seeds=ab_seeds,
                            llm=llm, seed_offset=bseed, source=source)
            ab["after_battle"] = i
            ab_runs.append(ab)
            row["ab"] = ab
        battles.append(row)

    total_usage: dict[str, int] = {}
    for r in battles:
        for k, v in r["usage"].items():
            total_usage[k] = total_usage.get(k, 0) + v
    return {
        "n": n, "seed": seed, "out_dir": str(out_path), "battles": battles, "ab": ab_runs,
        "usage": total_usage, "cache_hit_rate": cache_hit_rate(total_usage),
        "globalmem": {"dir": globalmem_dir, "active": gstore.count() if gstore else 0,
                      "total": len(gstore.all()) if gstore else 0},
        "memory": {"dir": memory_dir, "entries": mstore.count() if mstore else 0},
    }
