"""R1 反思管线（最小版）：轨迹 → 初始 MemoryEntry（**确定性摘要，不调 LLM**）。

`extract_experiences(record)` 用 R0 的 `analyze_record` 重放，逐回合逐方产出一条
`MemoryEntry`：situation_key / 行动 / 确定性摘要文本 / `Q=0`。R3 的 `ReflectionService`
（双分析师 + LLM）才生成凝练经验并覆盖这里的 `experience_text`
（`source_type` 从 `"extract"` 升级为 `"reflection"`）。

**迷雾口径**：experience_text 用 `render_feedback(..., show_v=False)` 的机制文本
（已按 `events_a/b` 过滤、对手行动按 `foe_revealed` 渲染、**不含 full-state 的 V 值**）——
记忆条目只沉淀白名单之上的文本，R3 注入玩家时不加信息面。

data_digest/rules_digest 复用 S0 的 `environment.datafingerprint` 共享指纹（读 record
stamp，旧记录回退当前指纹）——记忆隔离与轨迹 stamp 同口径（对齐审计 AUD-E-002 建议）。
"""

from __future__ import annotations

from environment.datafingerprint import data_digest as _fingerprint_data_digest
from environment.datafingerprint import rules_digest as _fingerprint_rules_digest
from environment.models import SIDES

from rock_pvp_agent.battle.evolution.analysis import analyze_record
from rock_pvp_agent.battle.evolution.feedback import render_feedback
from rock_pvp_agent.battle.evolution.memory import MemoryStore, make_entry_id


def _rules_version(record: dict) -> str:
    """规则版本指纹：优先读 record 的 `rules_digest` stamp，旧记录回退当前指纹。"""
    return record.get("rules_digest") or _fingerprint_rules_digest()


def _data_digest(record: dict) -> str:
    """数据源指纹：优先读 record 的 `data_digest` stamp，旧记录回退当前指纹。"""
    return record.get("data_digest") or _fingerprint_data_digest()


def _lineage_family(record: dict) -> str:
    """谱系族标签：R1 提取没有场景/原型信息，统一 `"extract"`（R2/R3 按证据细化）。"""
    return record.get("scenario") or "extract"


def _situation_text(situation_key: str) -> str:
    """situation_key（斜杠 10 段）→ 中文局面描述（确定性，供人读与检索展示）。"""
    p = situation_key.split("/")
    if len(p) != 10:
        return situation_key
    my_lives, foe_lives = p[0][2:], p[1][3:]       # 去掉 "my"/"foe" 前缀 → 纯数字
    return (f"命数 {my_lives} 对 {foe_lives} · 场上是 {p[2]} vs {p[3]} · "
            f"能量 {p[4]}:{p[5]} · 对手已揭示 {p[6]} 技能 · 阶段 {p[7]} · "
            f"存活后备 {p[8]}:{p[9]}")


def extract_experiences(record: dict, analysis=None) -> list[dict]:
    """一条轨迹重放 → 初始 MemoryEntry 列表（每回合每方一条，Q=0，幂等 entry_id）。

    确定性：同 record 两次产出逐字段相同（引擎纯转移 + 固定 seed）。
    `analysis` 可传入已算好的 TrajectoryAnalysis 避免二次重放（CLI 复用）。
    **replay_ok=False（重放失配/截断）→ 抛 ValueError**——绝不把不可信的轨迹
    沉淀成记忆（宁失败不抛，与 `update_q` 防脏写入同纪律）。
    """
    if analysis is None:
        analysis = analyze_record(record)
    if not analysis.replay_ok:
        raise ValueError(
            f"轨迹重放失配（replay_ok=False）：不可信，拒绝提取记忆。"
            f"检查轨迹完整性后重试。")
    rules_version = _rules_version(record)
    data_digest = _data_digest(record)
    lineage = _lineage_family(record)
    entries: list[dict] = []
    for ta in analysis.turns:
        for side in SIDES:
            action = ta.decisions[side]
            situation_key = ta.situation_keys[side]
            entries.append({
                "entry_id": make_entry_id(side, situation_key, action, scope=data_digest),
                "side": side,
                "lineage_family": lineage,
                "situation_key": situation_key,
                "situation_text": _situation_text(situation_key),
                "experience_text": render_feedback(ta, side=side, show_v=False),
                "action": action,
                "Q": 0.0,
                "n_used": 0,
                "n_adopted": 0,
                "provenance": {
                    "rules_version": rules_version,
                    "data_digest": data_digest,
                    "source_type": "extract",
                },
            })
    return entries


def store_experiences(record: dict, out_dir, analysis=None) -> list[str]:
    """提取并写入 MemoryStore（`evolve reflect --out` 用）。返回写入的 entry_id 列表。"""
    store = MemoryStore(out_dir)
    return [store.add(e) for e in extract_experiences(record, analysis=analysis)]
