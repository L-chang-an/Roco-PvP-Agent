"""假 LLM 对战玩家（E5 测试阶段）：**固定回复 + 随机动作**。

真实 LLM 玩家以后实现同一个 `environment.players.Player` Protocol（decide 走 tool-call 循环）即可；
本类是可注入的占位实现：动作**随机**（委托 `RandomPlayer`，自带独立 RNG 流，绝不共用引擎的流），
每回合产出一条**固定前缀**的「思考回复」供 UI/日志展示。它不看观测（随机策略），所以迷雾不涉密。

设计要点：`kind="fake_llm"` 让日志/UI 能认出这是假模型；`last_reply` 暴露给编排器
（BattleController）写进回合记录与事件流。
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from environment.actions import Decision, recharge_action
from environment.players import RandomPlayer

from rock_pvp_agent.battle.prompts import (
    BATTLE_PLAYER_SYSTEM_PROMPT,
    render_events,
    render_observation,
    render_replacement,
)
from rock_pvp_agent.config import Settings
from rock_pvp_agent.llm import build_chat_llm

# 固定前缀：真实 LLM 的回复是自由的，假 LLM 每次都以这串开头，构成「固定回复」。
FIXED_REPLY_PREFIX = "（假LLM）快速思考后决定："


def _summarize_decision(dec) -> str:
    """Decision → 一句中文动作摘要（固定回复的内容；只读 action，不解析观测）。"""
    atype = dec.action.get("type", "")
    if atype == "skill":
        s = f"使用技能（槽位 {dec.action.get('value')}）"
    elif atype == "switch":
        s = f"换人（目标槽位 {dec.action.get('value')}）"
    elif atype == "recharge":
        s = "聚能回复能量"
    else:
        s = f"行动 {atype or '未知'}"
    if dec.item:
        s += f" + 道具「{dec.item}」"
    return s


class FakeLLMPlayer:
    """假 LLM 对战玩家：随机合法动作 + 固定回复。kind="fake_llm"。"""

    kind = "fake_llm"

    def __init__(self, side: str, *, seed: int) -> None:
        self.side = side
        self._random = RandomPlayer(side, seed=seed)   # 独立 RNG 流（策略侧，不碰引擎流）
        self._last_reply = ""

    def on_match_start(self, observation: dict) -> None:
        """开局回调：假模型无动作。输入：观测；输出：无。"""

    def decide(self, observation: dict, legal: list[dict], items: list[str]) -> Decision:
        """随机选一个合法主动作（委托 RandomPlayer），并生成固定前缀回复。
        输入：观测（未用）/ legal 合法动作池 / items 可用道具；输出：随机 Decision。"""
        dec = self._random.decide(observation, legal, items)
        self._last_reply = FIXED_REPLY_PREFIX + _summarize_decision(dec)
        return dec

    def choose_replacement(self, observation: dict, bench: list[int]) -> int:
        """随机选一个存活后备——走自己的独立 RNG 流。"""
        return self._random.choose_replacement(observation, bench)

    def choose_starter(self, observation: dict, options: list[int]) -> int:
        """随机选一个首发——走自己的独立 RNG 流。"""
        return self._random.choose_starter(observation, options)

    def on_turn_result(self, observation: dict, events: list[dict]) -> None:
        """回合结束回调：假模型无动作。输入：观测 + 事件流；输出：无。"""

    @property
    def last_reply(self) -> str:
        """本回合（最近一次 decide）的固定回复，供编排器记录/展示。"""
        return self._last_reply


def policy_seed(playbook, seed: int) -> int:
    """Playbook → 确定性策略种子：手册全文 SHA-256（**不用内置 hash**——PYTHONHASHSEED
    随机化会让同文本两次进程产出不同种子，破坏确定性）。不同手册 = 不同策略。"""
    import hashlib
    digest = hashlib.sha256(playbook.text().encode("utf-8")).digest()
    return (int(seed) & 0xFFFFFFFF) ^ (int.from_bytes(digest[:8], "big") & 0xFFFFFFFF)


class PlaybookPlayer:
    """确定性「读手册」对战玩家（R4 离线路径）。

    真实路径：`LLMPlayer(strategy=playbook.text())` 让 LLM 读 `[战术手册]`；
    离线路径（无 key / 测试 / CLI 冒烟）用本类把手册文本 hash 成策略种子——
    同 seed 同手册两次逐位相同（确定性）、不同手册策略不同（Pareto 池/门禁在确定性
    玩家上也能产生可复现的分数向量差异，让 R4 闭环离线可验）。

    实现：委托 `RandomPlayer`（自带独立 RNG 流，不碰引擎流），策略种子 = 手册指纹。
    """

    kind = "playbook"

    def __init__(self, side: str, playbook, *, seed: int) -> None:
        self.side = side
        self.playbook = playbook
        self._seed = seed
        self.kind = f"playbook:{playbook.version}"
        self._random = RandomPlayer(side, seed=policy_seed(playbook, seed))

    def on_match_start(self, observation: dict) -> None:
        """开局回调：策略已固定。输入：观测；输出：无。"""

    def decide(self, observation: dict, legal: list[dict], items: list[str]) -> Decision:
        """按手册指纹策略选一个合法主动作（委托 RandomPlayer 的确定性流）。"""
        return self._random.decide(observation, legal, items)

    def choose_replacement(self, observation: dict, bench: list[int]) -> int:
        """按手册指纹策略选补位（同上）。"""
        return self._random.choose_replacement(observation, bench)

    def on_turn_result(self, observation: dict, events: list[dict]) -> None:
        """回合结束回调：无动作。输入：观测 + 事件流；输出：无。"""

    @property
    def last_reply(self) -> str:
        return f"（playbook {self.playbook.version}）按手册策略行动"


# ---------------------------------------------------------------------------
# E6.5：真实 LLM 对战玩家
# ---------------------------------------------------------------------------


def _render_memories(memories: list[dict]) -> str:
    """检索到的 top-k 记忆 → `[记忆]` 块（注入 LLM 决策上下文，强制标注非当前局面）。"""
    parts = ["[记忆] 历史经验，非当前局面（仅供参考）："]
    for m in memories:
        sit = m.get("situation_text", "") or m.get("situation_key", "")
        exp = m.get("experience_text", "")
        parts.append(f"- {sit}：{exp}")
    return "\n".join(parts)


def build_side_tools(side: str):
    """一侧玩家的工具集：唯一工具 `battle_act_{side}`（调用被**拦截**，不真正执行）。

    工具名带 side 后缀 → `build_chat_llm` 的缓存键（settings + 工具名集合）自然分键，
    两侧各持一个独立 LLM 实例，不共用缓存。真正提交由编排器经 `Player.decide` 返回的
    Decision 完成——工具函数体只是 schema 载体，永远不被调用。
    """
    name = f"battle_act_{side}"

    @tool
    def battle_act(action_type: str, target: int | None = None, item: str = "",
                   prediction: str = "") -> str:
        """提交你本回合的行动。action_type ∈ {skill, switch, recharge}；skill/switch 的 target 是槽位下标（从 0 开始）；recharge 不需要 target；item 是道具名（不用则留空）；prediction 是可选的预期结果一句话（离线校准用，不用则留空）。"""
        return "行动已接收。"   # 拦截：真提交由编排器完成，这里不被调用

    battle_act.name = name
    return [battle_act]


class LLMPlayer:
    """真实 LLM 对战玩家（E6.5）：`decide` 走 tool-call 循环，`battle_act` 拦截为 Decision。

    - **私有 history**：系统提示 + 每回合渲染后的迷雾观测 + 自己的工具调用/结果，绝不注入另一侧。
    - **battle_act 拦截**：模型调它 → 解析成 Decision → 补一条合成 ToolMessage（历史可重放）。
    - **非法重试 ≤max_retries**：非法提交把原因回灌 ToolMessage，LLM 看到后修正；仍失败 → 随机兜底。
    - **异常直接兜底**：`llm.invoke` 抛异常不重试，随机决策（宁失败不抛）。
    - 观测/事件均为 E4 迷雾口径（`run_match` 喂 `view()` + 过滤后事件）。
    """

    kind = "llm"

    def __init__(self, side: str, *, settings: Settings, seed: int,
                 strategy: str = "", memory=None, llm=None, max_retries: int = 3) -> None:
        self.side = side
        self._settings = settings
        self._seed = seed
        self._strategy = strategy          # R4：Playbook 文本（注入系统提示 `[战术手册]`）
        self._memory = memory              # 记忆检索器 callable (side, situation_key) -> list[dict]（None=不注入）
        self._max_retries = max_retries
        self._act_name = f"battle_act_{side}"
        self._random = RandomPlayer(side, seed=seed)      # 兜底（独立 RNG 流，不碰引擎流）
        self._tools = build_side_tools(side)
        self._llm = build_chat_llm(settings, self._tools, llm=llm)   # llm= 为测试注入缝
        self._history: list = []
        self._turn_log: list[dict] = []   # R2：逐回合 (turn/situation_key/action/prediction)

    # ── Player Protocol ──
    def on_match_start(self, observation: dict) -> None:
        """开局：重置私有 history 与 _turn_log（R2 校准信号——防止同一实例跨局累积）。

        R4：`strategy`（战术手册文本）以 `[战术手册]` 块追加进系统提示——SkillOpt
        的 skill-as-trainable-state：手册是训练目标，LLM 是执行器。无 strategy 时
        行为与 R3 完全一致（向后兼容）。
        """
        prompt = BATTLE_PLAYER_SYSTEM_PROMPT
        if self._strategy:
            prompt += "\n\n[战术手册]\n" + self._strategy
        self._history = [SystemMessage(content=prompt)]
        self._turn_log = []

    def decide(self, observation: dict, legal: list[dict], items: list[str]) -> Decision:
        """渲染观测 → tool-call 循环 → 解析 battle_act → 合法则返回 Decision。

        成功与兜底都记录 `_turn_log`（R2 校准信号原料）——兜底 prediction=""。
        """
        self._history.append(HumanMessage(content=render_observation(observation, legal, items)))
        if self._memory is not None:
            from environment.evaluate import situation_key
            memories = self._memory(self.side, situation_key(observation))
            if memories:
                self._history.append(HumanMessage(content=_render_memories(memories)))
        for _attempt in range(self._max_retries + 1):
            try:
                resp = self._llm.invoke(self._history)
            except Exception:
                dec = self._random.decide(observation, legal, items)   # 异常直接兜底
                self._record_turn(observation, dec, "")
                return dec
            self._history.append(resp)
            calls = getattr(resp, "tool_calls", None) or []
            acted = next((c for c in calls if c.get("name") == self._act_name), None)
            if acted is None:
                # 模型没调 battle_act：补全所有 tool_call 的 ToolMessage（历史可重放）+ 提示重试
                for c in calls:
                    self._history.append(
                        ToolMessage(content="（非 battle_act 调用，已忽略）", tool_call_id=c.get("id", "")))
                self._history.append(
                    HumanMessage(content="你本回合没有调用 battle_act 提交行动，请立即调用一次。"))
                continue
            dec, reason = self._parse_decision(acted, legal, items)
            for c in calls:
                if c is acted:
                    content = reason if reason else "行动已提交。"
                else:
                    content = "（多余的调用已忽略）"
                self._history.append(ToolMessage(content=content, tool_call_id=c.get("id", "")))
            if dec is not None:
                prediction = str((acted.get("args") or {}).get("prediction", "") or "").strip()
                self._record_turn(observation, dec, prediction)
                return dec
            # 非法：原因已在 ToolMessage 里，下一轮 LLM 看到后修正重试
        dec = self._random.decide(observation, legal, items)            # 重试耗尽兜底
        self._record_turn(observation, dec, "")
        return dec

    def _record_turn(self, observation: dict, dec: Decision, prediction: str) -> None:
        """R2：记录本回合 (turn / situation_key / 实际提交 action / prediction)。"""
        from environment.evaluate import situation_key
        self._turn_log.append({
            "turn": observation.get("turn"),
            "situation_key": situation_key(observation),
            "action": dict(dec.action),
            "item": dec.item,
            "prediction": prediction,
        })

    def choose_replacement(self, observation: dict, bench: list[int]) -> int:
        """渲染补位提示 → LLM 调 battle_act(action_type='replace', target=…) → 返回选中槽位。"""
        self._history.append(HumanMessage(content=render_replacement(observation, bench)))
        for _attempt in range(self._max_retries + 1):
            try:
                resp = self._llm.invoke(self._history)
            except Exception:
                return self._random.choose_replacement(observation, bench)
            self._history.append(resp)
            calls = getattr(resp, "tool_calls", None) or []
            acted = next((c for c in calls if c.get("name") == self._act_name), None)
            if acted is None:
                for c in calls:
                    self._history.append(
                        ToolMessage(content="（非 battle_act 调用，已忽略）", tool_call_id=c.get("id", "")))
                self._history.append(
                    HumanMessage(content="请调用 battle_act(action_type='replace', target=<槽位下标>) 选择补位。"))
                continue
            args = acted.get("args", {}) or {}
            try:
                idx = int(args.get("target"))
            except (TypeError, ValueError):
                idx = -1
            ok = idx in bench
            for c in calls:
                if c is acted:
                    content = "补位已提交。" if ok else "补位目标非法，请重选。"
                else:
                    content = "（多余的调用已忽略）"
                self._history.append(ToolMessage(content=content, tool_call_id=c.get("id", "")))
            if ok:
                return idx
        return self._random.choose_replacement(observation, bench)       # 重试耗尽兜底

    def choose_starter(self, observation: dict, options: list[int]) -> int:
        """首发选择：简化实现，随机选一个首发（走独立 RNG 流兜底）。"""
        return self._random.choose_starter(observation, options)

    def on_turn_result(self, observation: dict, events: list[dict]) -> None:
        """回合结束：把**过滤后**的事件摘要追加进私有 history（LLM 学到结果）。"""
        self._history.append(HumanMessage(content=render_events(events)))

    # ── 内部 ──
    def _parse_decision(self, act: dict, legal: list[dict], items: list[str]) -> tuple[Decision | None, str | None]:
        """battle_act 工具调用 → (Decision, None) 或 (None, 中文原因)。"""
        args = act.get("args", {}) or {}
        atype = str(args.get("action_type", "")).strip().lower()
        item = str(args.get("item", "") or "").strip()
        if atype == "recharge":
            action = recharge_action()
        elif atype in ("skill", "switch"):
            raw = args.get("target")
            try:
                target = int(raw)
            except (TypeError, ValueError):
                return None, f"{atype} 需要整数槽位下标，收到 {raw!r}"
            action = {"type": atype, "value": target}
        else:
            return None, f"未知 action_type「{args.get('action_type')}」（skill|switch|recharge|replace）"
        if action not in legal:
            return None, f"行动 {action} 不在合法动作池（槽位越界 / 能量不足 / 目标非法）"
        if item and item not in items:
            return None, f"道具「{item}」不可用（次数已尽或不存在）"
        return Decision(action=action, item=item), None
