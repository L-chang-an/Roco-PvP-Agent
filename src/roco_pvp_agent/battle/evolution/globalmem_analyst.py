"""G3 GlobalAnalyst：战后**双视角**分析成败 → GlobalMem 的 update/create/skip 决策。

与 R3 `ReflectionService`（局部反思 → Playbook 有界编辑）的分工：
- R3 分析**关键回合**（三信号卡片）→ 局部战术规则；
- **G3 分析整局**（该侧迷雾轨迹全程）→ 全局对局经验（GlobalMem），占据原 Playbook 生态位。

复用 R3 已建立的纪律（不重造）：独立系统提示、`_parse_reflection_json` 鲁棒解析、
异常/坏 JSON → 降级不阻塞、`diagnostics` 记录 ok/degraded。

**迷雾口径（安全关键）**：两侧分析**必须互不可见**——a 的分析师只看 a 的迷雾轨迹，
b 的只看 b 的。绝不能把两侧摘要拼在一次调用里（那等于让 a 看到 b 的视角，产出的
GlobalMem 会带上"我知道对手当时在想什么"的不实前提，下一场注入即泄漏）。
摘要用 `render_feedback(side=..., show_v=False)`：
- `show_v=False` → 不含 full-state 价值（那是离线优化器口径，绝不进对战玩家）；
- `events_a/b` 已按方过滤、对手技能按 `foe_revealed` 只显示已揭示的。
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from environment.dataset import DEFAULT_SOURCE, DataSource

from roco_pvp_agent.battle.evolution.analysis import analyze_record
from roco_pvp_agent.battle.evolution.feedback import render_feedback
from roco_pvp_agent.battle.evolution.globalmem import (
    GlobalMemStore,
    make_global_entry_id,
    matchup_key,
)
from roco_pvp_agent.battle.evolution.reflect import _parse_reflection_json

# 决策枚举。`skip` 必须允许——否则每局硬塞一条会让库膨胀 + 噪声。
DECISIONS = ("update", "create", "skip")

# 摘要里最多渲染多少回合（防超长；取首尾各半，中段省略）。
MAX_SUMMARY_TURNS = 24

GLOBAL_ANALYST_SYSTEM = """你是回合制对战的**全局复盘分析师**。你只收到**一方视角**的整局对战记录
（迷雾口径：你看不到对手未揭示的技能、也看不到全局价值估计）。你的任务是提炼**下一次遇到同类
阵容对局时可直接执行的全局策略经验**。

硬约束（违反则结果被丢弃）：
1. **绝不写入对手未揭示的技能名、性格、个体值**——记录里没给你的信息就是你不知道的；
2. 经验必须是**这套阵容对那套阵容**层面的全局打法（开局节奏、资源分配、残局处理），
   不要写单回合的微操（那由另一套局部记忆负责）；
3. 文本要短：**不超过 400 字**，具体可执行，不要复述战报。

只输出一个 JSON 对象（不要任何其他文本、不要 markdown 围栏），字段：
{
  "outcome": "win",              // win / loss，取自战报终局
  "root_cause": "一句话成败根因",
  "decision": "update",          // update=改进你收到的已加载经验 / create=新建一条 / skip=本局无可沉淀
  "strategy_text": "下一场同类阵容的全局打法（≤400 字，具体可执行）",
  "reason": "为什么选这个 decision"
}"""


def _turn_lines(analysis, side: str) -> list[str]:
    """逐回合迷雾摘要（`show_v=False`：不含 full-state 价值）。超长则首尾各半、中段省略。"""
    turns = analysis.turns
    if len(turns) <= MAX_SUMMARY_TURNS:
        chosen = list(turns)
        elided = 0
    else:
        half = MAX_SUMMARY_TURNS // 2
        chosen = list(turns[:half]) + list(turns[-half:])
        elided = len(turns) - len(chosen)
    lines: list[str] = []
    for i, ta in enumerate(chosen):
        if elided and i == MAX_SUMMARY_TURNS // 2:
            lines.append(f"…（中间 {elided} 回合省略）")
        lines.append(render_feedback(ta, side=side, show_v=False))
    return lines


def render_battle_summary(analysis, side: str, *, loaded_strategy: str = "") -> str:
    """一侧视角的整局摘要（喂给分析师的唯一输入）。

    含：胜负 / 回合数 / 本局加载的已有经验（若有）/ 逐回合迷雾摘要。
    **不含**：全量原始事件、full-state 价值、对手未揭示技能——迷雾口径由
    `render_feedback(show_v=False)` 保证。
    """
    won = analysis.winner == side
    head = [f"[视角] {side} 方",
            f"[终局] {'胜' if won else ('负' if analysis.winner else '平/未决')}"
            f" · 共 {analysis.turn_count} 回合"]
    if loaded_strategy:
        head.append(f"[本局加载的已有经验] {loaded_strategy}")
    else:
        head.append("[本局加载的已有经验] 无（本局未命中任何全局经验）")
    return "\n".join(head + ["[逐回合]"] + _turn_lines(analysis, side))


class GlobalAnalyst:
    """全局复盘分析师：双视角各调一次 LLM，产出 GlobalMem 决策。

    `llm=` 是注入缝（测试用 fake，不碰网络）；异常/坏 JSON → None（降级不阻塞，
    与 `ReflectionService` 同纪律）。
    """

    def __init__(self, settings, *, llm=None) -> None:
        self._settings = settings
        self._llm = llm
        self.diagnostics: list[dict] = []

    def _call(self, user_text: str) -> dict | None:
        try:
            if self._llm is None:
                from roco_pvp_agent.llm import build_chat_llm
                self._llm = build_chat_llm(self._settings, [])
            resp = self._llm.invoke([SystemMessage(content=GLOBAL_ANALYST_SYSTEM),
                                     HumanMessage(content=user_text)])
        except Exception:
            return None
        return _parse_reflection_json(getattr(resp, "content", "") or "")

    def analyze(self, record: dict, side: str, *, analysis=None,
                store: GlobalMemStore | None = None) -> dict | None:
        """一侧视角 → 决策 dict（未通过校验/降级 → None）。

        `store` 给出时用于取「本局加载的经验文本」喂进摘要（让分析师知道要改进什么）。
        `record` 的 `replay_ok=False` → 拒绝分析（不可信轨迹不得沉淀经验，同 R1/R2 纪律）。
        """
        if side not in ("a", "b"):
            raise ValueError(f"side 必须是 a/b，实际 {side!r}")
        analysis = analysis or analyze_record(record)
        if not analysis.replay_ok:
            self.diagnostics.append({"side": side, "status": "rejected",
                                     "reason": "replay_ok=False"})
            return None
        loaded_text = ""
        loaded_id = record.get(f"global_mem_{side}")
        if store is not None and loaded_id:
            entry = store.get(loaded_id)
            if entry is not None:
                loaded_text = entry.get("strategy_text", "")
        j = self._call(render_battle_summary(analysis, side, loaded_strategy=loaded_text))
        ok = j is not None and self._valid(j)
        self.diagnostics.append({"side": side, "status": "ok" if ok else "degraded",
                                 "decision": (j or {}).get("decision")})
        return j if ok else None

    def analyze_both(self, record: dict, *, analysis=None,
                     store: GlobalMemStore | None = None) -> dict[str, dict | None]:
        """两侧各调一次（**输入互不含对方视角**）→ `{"a": 决策|None, "b": 决策|None}`。"""
        analysis = analysis or analyze_record(record)
        return {s: self.analyze(record, s, analysis=analysis, store=store)
                for s in ("a", "b")}

    @staticmethod
    def _valid(j: dict) -> bool:
        """schema 校验：decision 合法；非 skip 时 strategy_text 非空。"""
        if not isinstance(j, dict) or j.get("decision") not in DECISIONS:
            return False
        if j["decision"] != "skip" and not str(j.get("strategy_text") or "").strip():
            return False
        return True


def apply_decision(store: GlobalMemStore, decision: dict | None, *, record: dict, side: str,
                   data_digest: str, rules_digest: str = "",
                   source: DataSource = DEFAULT_SOURCE) -> dict:
    """决策 → 落库。`update`→`supersede`（append-only）/ `create`→`add` / `skip`→no-op。

    - **`update` 但本局没有该侧的 `global_mem_{side}` → 自动降级为 `create`**
      （只信 record 的加载记录，不信 LLM 自己给的 id——防它乱指）；
    - token 超限由 `GlobalMemStore` 拒绝（G1 已实现，拒绝不截断），审计同样落盘。

    返回 `{action, ok, entry_id, reason}`。
    """
    if decision is None:
        return {"action": "none", "ok": False, "entry_id": None, "reason": "分析降级，无决策"}
    if decision["decision"] == "skip":
        return {"action": "skip", "ok": True, "entry_id": None,
                "reason": decision.get("reason", "本局无可沉淀经验")}

    roster_key = f"team_{side}"
    foe_key = "team_b" if side == "a" else "team_a"
    rules = record.get("rules") or {}
    key = matchup_key(record[roster_key], record[foe_key],
                      team_size=rules.get("team_size", len(record[roster_key])),
                      lives=rules.get("lives", 0), source=source)
    text = str(decision["strategy_text"]).strip()
    entry = {
        "entry_id": make_global_entry_id(key, text, data_digest),
        "matchup_key": key,
        "my_roster": [u.get("name", "") for u in record[roster_key]],
        "foe_roster": [u.get("name", "") for u in record[foe_key]],
        "strategy_text": text,
        "Q": 0.0, "n_used": 0, "n_wins": 0,
        "provenance": {"data_digest": data_digest, "rules_digest": rules_digest,
                       "source_battle_ids": [record.get("battle_id", "")],
                       "side": side, "root_cause": decision.get("root_cause", "")},
    }
    battle_id = record.get("battle_id", "")
    loaded_id = record.get(f"global_mem_{side}")
    if decision["decision"] == "update" and loaded_id and store.get(loaded_id) is not None:
        out = store.supersede(loaded_id, entry, battle_id=battle_id)
        return {"action": "update", **out}
    # update 但没有可更新的目标 → 降级 create
    out = store.add(entry, battle_id=battle_id)
    action = "create" if decision["decision"] == "create" else "update→create"
    return {"action": action, **out}
