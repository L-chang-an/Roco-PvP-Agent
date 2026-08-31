"""R1 记忆健康度：Q 分布 / Forgetting Rate / 检索命中率（`evolve health --memory`）。

R1 起步：Q 全 0、Forgetting Rate 与命中率 n/a（依赖 R2 的采纳归因与使用追踪）。
R4 健康度看板会并入记忆健康指标（§九：动作熵 / 编辑接受率 / 记忆 Q 分布 + Forgetting Rate）。
"""

from __future__ import annotations

from rock_pvp_agent.battle.evolution.memory import MemoryStore


def memory_health(dir_path) -> dict:
    """读一个 MemoryStore 目录 → 健康度报告（确定性、纯读）。"""
    store = MemoryStore(dir_path)
    entries = store.all()
    qs = [e.get("Q", 0.0) for e in entries]
    n = len(qs)
    return {
        "store_dir": str(store.dir),
        "count": n,
        "q_distribution": {
            "min": round(min(qs), 4) if n else 0.0,
            "max": round(max(qs), 4) if n else 0.0,
            "mean": round(sum(qs) / n, 4) if n else 0.0,
        },
        "n_used_total": sum(e.get("n_used", 0) for e in entries),
        "n_adopted_total": sum(e.get("n_adopted", 0) for e in entries),
        "source_type_counts": _source_counts(entries),
        "forgetting_rate": 0.0,     # R2 起：被检索未采纳比例（依赖采纳归因）
        "retrieval_hits": None,     # R2 起：检索命中率（依赖使用追踪）
    }


def _source_counts(entries: list[dict]) -> dict[str, int]:
    """按 provenance.source_type 计数（R1 全为 extract；R3 起混入 reflection）。"""
    counts: dict[str, int] = {}
    for e in entries:
        src = ((e.get("provenance") or {}).get("source_type")) or "unknown"
        counts[src] = counts.get(src, 0) + 1
    return counts
