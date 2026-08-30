"""R5 Meta Playbook：只给优化器（反思 LLM）的元手册——**不进对战玩家提示**。

SkillOpt §5.1：Meta Playbook 向优化器暴露「哪些编辑模式反复被拒 / 哪些诊断反复奏效 /
哪些失败跨 epoch 持续」，让反思 LLM 避免重蹈覆辙。它是对**反思提示**的增强，不是对战策略——
玩家系统提示里的 `[战术手册]` 永不包含这里的任何内容（信息隔离：优化器信息不泄漏给执行器）。

- `observe_step(step_report)`：吸收每 step 的编辑报告（接受率按 op/模块）与反思诊断；
- `mark_persistent_failure(text, epochs)`：跨 epoch 持续失败/被拒的规则（慢更新喂进来）；
- `render()`：确定性文本，追加进反思双分析师的系统提示。

一切确定性、可审计；零 LLM。
"""

from __future__ import annotations

import json
from collections import Counter


class MetaPlaybook:
    """优化器元手册：编辑接受率 / 诊断奏效度 / 跨 epoch 持续失败。"""

    def __init__(self) -> None:
        self._applied: Counter = Counter()          # (op, module) → 应用次数
        self._rejected: Counter = Counter()         # (op, module) → 被拒次数
        self._diagnostics: Counter = Counter()      # analyst → 次数（ok/degraded）
        self._persistent_failures: dict[str, int] = {}   # 规则文本 → 连续失败 epoch 数

    def observe_step(self, step_report: dict) -> None:
        """吸收一步：编辑报告（接受率）+ 反思诊断（奏效度）。确定性，纯计数。"""
        for r in step_report.get("reports", []):
            key = (r.get("op", "?"), r.get("module_key", "?"))
            if r.get("status") == "applied":
                self._applied[key] += 1
            elif r.get("status") in ("rejected", "applied_then_gated"):
                self._rejected[key] += 1
        for d in step_report.get("reflection_diagnostics", []):
            self._diagnostics[(d.get("analyst", "?"), d.get("status", "?"))] += 1

    def mark_persistent_failure(self, text: str, *, epochs: int = 1) -> None:
        """标记一条规则被拒（每 epoch 调一次）；计数跨 epoch 累积。

        `persistent_failures()` 只返回**连续 ≥2 epoch** 被拒的规则（「跨 epoch 持续失败」
        的门槛）——单次被拒是噪声，连续被拒才值得慢更新移除。
        **空/空白文本 no-op**：被拒的 delete 候选 `text=""` 若入账，slow_update 的
        `pf in ln` 对 `pf=""` 恒真会清空全部模块（M1 修复）。
        """
        t = text.strip()
        if not t:
            return
        self._persistent_failures[t] = self._persistent_failures.get(t, 0) + epochs

    def persistent_failures(self) -> tuple[str, ...]:
        """跨 epoch 持续失败（≥2 epoch 被拒）的规则文本（喂给 slow_update 做移除候选）。"""
        return tuple(t for t, n in self._persistent_failures.items() if n >= 2)

    def _acceptance_rows(self) -> list[str]:
        """编辑接受率（按 op×模块），按被拒次数降序——被拒最多的模式排最前。"""
        rows: list[str] = []
        all_keys = set(self._applied) | set(self._rejected)
        for key in sorted(all_keys, key=lambda k: (-self._rejected[k], k)):
            ap, rj = self._applied[key], self._rejected[key]
            tot = ap + rj
            rate = ap / tot if tot else 0.0
            rows.append(f"- {key[0]}@{key[1]}: 接受 {ap}/{tot}（{rate:.2f}）")
        return rows

    def render(self) -> str:
        """确定性元手册文本（追加进反思提示；空历史 → 空串，不干扰初始反射）。"""
        if not (self._applied or self._rejected or self._diagnostics or self._persistent_failures):
            return ""
        parts = ["[Meta Playbook：上一轮优化记录]"]
        if self._applied or self._rejected:
            parts.append("编辑接受率：")
            parts.extend(self._acceptance_rows())
        if self._diagnostics:
            parts.append("反思诊断：" +
                         "，".join(f"{a}={n}" for (a, s), n in
                                   sorted(self._diagnostics.items())))
        if self._persistent_failures:
            parts.append("跨 epoch 持续失败（避免重复提议）：")
            parts.append(json.dumps(list(self._persistent_failures), ensure_ascii=False))
        return "\n".join(parts)
