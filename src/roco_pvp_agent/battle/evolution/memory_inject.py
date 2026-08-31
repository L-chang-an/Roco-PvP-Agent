"""记忆采纳判定 + Q 更新（R 线闭环）：把「注入的记忆是否被采纳」反馈进 Q 值。

部署期 LLMPlayer 检索 top-3 记忆注入 `[记忆]` 块；本模块在离线期比对
「该回合实际 action」与「检索到的记忆 action」，命中即采纳，更新 Q（reward = 终局胜负），
让 Q 值反向影响下次检索排序——形成「存 → 检索 → 注入 → 采纳 → 更新」完整闭环。
"""

from __future__ import annotations

from roco_pvp_agent.battle.evolution.memory import MemoryStore, update_q


def apply_adoption(store: MemoryStore, record: dict, retriever, *,
                   winner: str | None) -> dict:
    """比对 record 逐回合 action（analysis_a/b 的 _turn_log）与检索记忆 action，命中则采纳 + 更新 Q。

    - `record`：run_selfplay 落盘的 record（R2 起含 `analysis_a/b` = LLMPlayer 的 `_turn_log`）；
    - `retriever`：检索器 `(side, situation_key) -> list[dict]`，与部署期同一口径；
    - `winner`：终局胜方（"a"/"b"/None），reward = 该记忆所属方是否获胜（1.0/0.0）；
    - 确定性玩家无 analysis_a/b → 直接返回空统计（不报错）。

    返回 `{checked, adopted, updated}`——checked = 检索到并参与比对的记忆条数，
    adopted = 被采纳条数，updated = 触发 Q 更新的条数（与 adopted 一致）。
    """
    stats = {"checked": 0, "adopted": 0, "updated": 0}
    for side in ("a", "b"):
        turn_log = record.get(f"analysis_{side}")
        if not turn_log:
            continue
        for entry in turn_log:
            key = entry.get("situation_key")
            if not key:
                continue
            memories = retriever(side, key)
            for m in memories:
                stats["checked"] += 1
                if m.get("action") == entry.get("action"):
                    stats["adopted"] += 1
                    reward = 1.0 if winner == side else 0.0
                    update_q(store, m["entry_id"], reward)
                    stats["updated"] += 1
    return stats
