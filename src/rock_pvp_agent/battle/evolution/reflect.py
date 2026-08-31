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

import json

from langchain_core.messages import HumanMessage, SystemMessage

from environment.datafingerprint import data_digest as _fingerprint_data_digest
from environment.datafingerprint import rules_digest as _fingerprint_rules_digest
from environment.models import SIDES

from rock_pvp_agent.battle.evolution.analysis import analyze_record
from rock_pvp_agent.battle.evolution.editor import EDIT_OPS, EditCandidate
from rock_pvp_agent.battle.evolution.feedback import render_feedback
from rock_pvp_agent.battle.evolution.memory import MemoryStore, make_entry_id
from rock_pvp_agent.battle.evolution.playbook import MODULE_KEYS


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


# ---------------------------------------------------------------------------
# R3：双分析师（Failure / Success）+ JSON schema 校验 + 失败降级确定性摘要
# ---------------------------------------------------------------------------

FAILURE_REFLECTION_SYSTEM = """你是对战策略的失败分析师。你**只**收到「失败证据」：关键回合卡片
（含反事实确认的更好动作 / 校准偏差）与被拒绝的旧编辑。你的输出用来改进战术手册。

只输出一个 JSON 对象（不要任何其他文本、不要 markdown 围栏），字段：
{
  "root_cause": "一句话根因",
  "pattern_to_avoid": "要避免的模式",
  "correct_approach": "正确做法",
  "target_module": "M2 action_selector",   // 取 M1 team_reader / M2 action_selector / M3 energy_planner / M4 endgame 之一
  "edit_op": "append",                     // append / insert_after / replace / delete
  "proposed_edit": "要写进手册的新规则（一句话、具体可执行、指向正确做法）",
  "anchor": "",                            // insert_after/replace/delete 需锚点（原规则行的一部分）；append 留空
  "evidence": ["证据1", "证据2"]           // 从卡片里引用的具体证据
}"""

SUCCESS_REFLECTION_SYSTEM = """你是对战策略的成功分析师。你**只**收到「成功证据」：做得好的关键回合
（无反事实更好动作、价值未下降，或反事实确认原决策是最优的）。你的输出把成功流程固化成手册规则，
而非新增臆想规则。

只输出一个 JSON 对象（不要任何其他文本、不要 markdown 围栏），字段：
{
  "script": ["步骤1", "步骤2", "步骤3"],   // 3~5 个高层步骤，描述成功流程
  "target_module": "M2 action_selector",   // 固化到哪个模块
  "edit_op": "append",                     // 一般用 append 追加固化规则
  "proposed_edit": "把 script 浓缩成一句话规则（具体可执行）",
  "anchor": "",
  "evidence": ["证据1"]
}"""


def _balanced_json_blocks(text: str):
    """扫描文本产出**平衡的** `{...}` 候选块（跳过字符串内花括号）。

    对 LLM 输出做鲁棒提取：容忍散文、markdown 围栏、多个 JSON 对象、
    字符串里的花括号——不会像贪心正则那样把「第一个 { 到最后一个 }」一次抓死。
    """
    start = None
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(text or ""):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if start is None:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0:
                    yield text[start:i + 1]
                    start = None


def _parse_reflection_json(text: str) -> dict | None:
    """从 LLM 回复提取第一个合法 JSON 对象（多对象/散文花括号不丢有效块）。"""
    for block in _balanced_json_blocks(text):
        try:
            d = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict):
            return d
    return None


def _normalize_module(key: str) -> str | None:
    """容错模块键：`"M2"` / `"M2 action_selector"` / `"M2_action_selector"` /
    `" m2 "` / `"action_selector"` 都归到全键；非法 → None。"""
    k = str(key or "").strip().lower().replace("_", " ").replace("-", " ")
    for full in MODULE_KEYS:
        short, name = full.split(" ", 1)
        full_key = full.lower().replace("_", " ").replace("-", " ")
        name_key = name.lower().replace("_", " ").replace("-", " ")
        if k == full_key or k == short.lower() or k == name_key:
            return full
    return None


def _json_to_candidate(j: dict, source_type: str, support: int) -> EditCandidate | None:
    """一条反射 JSON → EditCandidate；非法（缺字段/坏 op/坏模块/空文本/多行）→ None（宁缺勿乱）。"""
    module_key = _normalize_module(str(j.get("target_module", "")))
    if module_key is None:
        return None
    op = str(j.get("edit_op", "append"))
    if op not in EDIT_OPS:
        return None
    text = str(j.get("proposed_edit", "")).strip()
    if not text or (op != "delete" and "\n" in text):
        return None                                   # 空文本 / 多行 → 拒（单规则一行）
    try:
        support_count = int(j.get("support_count", support) or 1)
        avg_delta = float(j.get("avg_delta", 0.0) or 0.0)
        coverage = int(j.get("coverage", 1) or 1)
    except (TypeError, ValueError):
        support_count, avg_delta, coverage = support, 0.0, 1
    ev = j.get("evidence")
    evidence = tuple(str(x) for x in ev) if isinstance(ev, list) else ()
    return EditCandidate(module_key=module_key, op=op, text=text,
                         anchor=str(j.get("anchor", "") or ""),
                         support_count=support_count, avg_delta=avg_delta,
                         coverage=coverage, source_type=source_type, evidence=evidence)


def _json_to_candidates(j: dict, source_type: str, support: int) -> list[EditCandidate]:
    """反射 JSON → 候选列表。支持三种形状：单条扁平 / `{"edits": [...]}` 列表 /
    `{"edits": {...}}` 单条 dict 包装（都处理，非法条目丢弃）。"""
    raw = j.get("edits")
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = [raw]                                   # 单编辑被包装成 dict
    else:
        items = [j]
    return [c for item in items if isinstance(item, dict)
            for c in [_json_to_candidate(item, source_type, support)] if c]


def _render_cards(cards: list[dict], rejected: list[dict]) -> str:
    """卡片 + 被拒缓冲 → LLM 的紧凑证据文本（只喂一类证据给对应分析师）。"""
    parts = [f"证据共 {len(cards)} 张关键回合卡片："]
    for c in cards:
        parts.append(f"[T{c.get('turn_no', '?')}/{c.get('side', '?')}] "
                     f"signals={c.get('signals', [])} "
                     f"delta={c.get('delta_winrate', 0.0):.3f} "
                     f"反事实更好={c.get('counterfactual_better')}")
        parts.append(c.get("feedback_text", ""))
    if rejected:
        parts.append("被拒绝的旧编辑：" + json.dumps(rejected, ensure_ascii=False))
    return "\n".join(parts)


class ReflectionService:
    """双分析师（GEPA/SkillOpt）：失败分析师与成功分析师各喂一类证据，独立提示。"""

    def __init__(self, settings, *, llm=None, meta: str = "") -> None:
        self._settings = settings
        self._llm = llm              # 注入缝（测试用 fake；None → 真实反思 LLM）
        self._meta = meta            # R5：Meta Playbook 文本（只给优化器，不进对战玩家）
        self.diagnostics: list[dict] = []

    def _call(self, system: str, user_text: str) -> dict | None:
        """调反思 LLM → JSON dict；异常/坏 JSON → None（降级）。"""
        try:
            if self._llm is None:
                from rock_pvp_agent.llm import build_chat_llm
                self._llm = build_chat_llm(self._settings, [])
            if self._meta:
                system = system + "\n\n" + self._meta
            resp = self._llm.invoke([SystemMessage(content=system),
                                     HumanMessage(content=user_text)])
        except Exception:
            return None
        return _parse_reflection_json(getattr(resp, "content", "") or "")

    def _diagnose(self, analyst: str, cards: list[dict], ok: bool) -> None:
        self.diagnostics.append({"analyst": analyst, "status": "ok" if ok else "degraded",
                                 "cards": len(cards)})

    def reflect_failure(self, cards: list[dict], rejected: list[dict]) -> list[EditCandidate]:
        if not cards:
            return []
        j = self._call(FAILURE_REFLECTION_SYSTEM, _render_cards(cards, rejected))
        self._diagnose("failure", cards, j is not None)
        return _json_to_candidates(j, "failure", len(cards)) if j is not None else []

    def reflect_success(self, cards: list[dict]) -> list[EditCandidate]:
        if not cards:
            return []
        j = self._call(SUCCESS_REFLECTION_SYSTEM, _render_cards(cards, []))
        self._diagnose("success", cards, j is not None)
        return _json_to_candidates(j, "success", len(cards)) if j is not None else []

    def reflect(self, cards: list[dict], rejected: list[dict] | None = None) -> list[EditCandidate]:
        """双分析师分桶，**不静默丢卡**：失败 = 反事实确认 / 校准偏差；成功 = 其余。"""
        rejected = rejected or []
        failure = [c for c in cards if any(s in c.get("signals", [])
                                           for s in ("counterfactual_confirmed", "calibration_miss"))]
        success = [c for c in cards if c not in failure]
        dropped = [c for c in cards if c not in failure and c not in success]
        if dropped:
            self.diagnostics.append({"analyst": "dropped", "status": "note",
                                     "cards": len(dropped)})
        return self.reflect_failure(failure, rejected) + self.reflect_success(success)
