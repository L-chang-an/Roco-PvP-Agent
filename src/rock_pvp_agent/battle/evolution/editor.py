"""R3 有界编辑（SkillOpt 纪律）：`bounded_edit` + 编辑审计 + `[PROTECTED]` 保护。

- **有界（L_t）**：一次最多应用 L_t 条编辑。按 `support_count × avg_delta × coverage`
  排序截断（SkillOpt：预算大小不敏感，**有界本身才是收益**——不要也不允许大规模重写）。
- **编辑操作**：`append` / `insert_after` / `replace` / `delete`，均以**单规则一行**为单位
  （Playbook 文本按行组织，审计可逐条追溯）。
- **`[PROTECTED]` 保护区**：目标模块 `protected=True` 且非慢更新（`allow_protected=False`）→ 拒绝。
- **审计**：每条编辑产出一条 `EditReport`（applied/skipped/rejected + 原因），
  `edit_apply_report.jsonl` 追加落盘（可追溯到证据：support/evidence/版本）。
- 版本不可变：编辑产出**新版本** Playbook，旧版本不回改。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from rock_pvp_agent.battle.evolution.playbook import (
    MAX_BATTLE_TOKENS,
    MAX_MODULE_TOKENS,
    Playbook,
    PlaybookModule,
)

EDIT_OPS: tuple[str, ...] = ("append", "insert_after", "replace", "delete", "protect")

# 默认有界编辑数 L_t（SkillOpt 实测：1–4 次被接受的编辑就产生显著提升）。
DEFAULT_LT = 4


@dataclass(frozen=True)
class EditCandidate:
    """一条编辑提案（R3 反思产出 / 记忆固化的统一形状）。"""

    module_key: str
    op: str                        # append / insert_after / replace / delete
    text: str = ""                 # 新规则文本（append/insert_after/replace）
    anchor: str = ""               # 锚点（insert_after/replace/delete 定位行）
    support_count: int = 1         # 证据条数
    avg_delta: float = 0.0         # 平均 delta_winrate（反事实）
    coverage: int = 1              # 场景覆盖度（命中多少场/实例）
    source_type: str = "failure"   # failure / success / memory_promote
    evidence: tuple[str, ...] = () # 证据描述（审计）

    def __post_init__(self) -> None:
        if self.op not in EDIT_OPS:
            raise ValueError(f"未知编辑操作「{self.op}」（{'/'.join(EDIT_OPS)}）。")


@dataclass
class EditReport:
    """一条编辑的处置结论（审计用，`edit_apply_report.jsonl` 的一行）。"""

    candidate: EditCandidate
    status: str                    # applied / skipped / rejected
    reason: str
    playbook_version: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {"status": self.status, "reason": self.reason,
                "playbook_version": self.playbook_version, "timestamp": self.timestamp,
                **asdict(self.candidate)}


def _score(c: EditCandidate) -> float:
    """排序分数：`support_count × max(avg_delta, 0.001) × coverage`（§五 step⑧）。"""
    return c.support_count * max(c.avg_delta, 0.001) * c.coverage


def _lines(text: str) -> list[str]:
    """保留空行的按行切分（锚点编辑不重排格式）。"""
    return text.splitlines() if text else []


def _apply_edit(module: PlaybookModule, c: EditCandidate) -> tuple[PlaybookModule, str]:
    """应用一条编辑，返回 (新模块, 原因)。原因非空 = 该条被拒（调用方记 rejected）。

    行级操作：`insert_after/replace/delete` 用 `anchor` 做子串匹配定位行；
    **歧义拒绝**：anchor 命中多行 → 拒绝（避免静默改错行，审计可追溯）。
    `protect`（R5 epoch 慢更新）：把模块标记进 `[PROTECTED]` 保护区，不改变文本。
    """
    if c.op == "append":
        new = module.text + ("\n" if module.text else "") + f"- {c.text}"
        return PlaybookModule(module.key, new, module.protected), ""
    if c.op == "protect":
        return PlaybookModule(module.key, module.text, True), ""
    lines = _lines(module.text)
    if not c.anchor:
        return module, "行级编辑缺少 anchor（无法定位）"
    matches = [i for i, ln in enumerate(lines) if c.anchor in ln.strip()]
    if not matches:
        return module, f"锚点「{c.anchor}」未命中任何规则行"
    if len(matches) > 1:
        return module, f"锚点「{c.anchor}」命中 {len(matches)} 行，歧义（拒绝，避免改错行）"
    idx = matches[0]
    if c.op == "insert_after":
        lines.insert(idx + 1, f"- {c.text}")
    elif c.op == "replace":
        lines[idx] = f"- {c.text}"
    elif c.op == "delete":
        lines.pop(idx)
    return PlaybookModule(module.key, "\n".join(lines), module.protected), ""


def _over_limit(pb: Playbook) -> str | None:
    """token 超限校验：超 → 原因，否则 None。"""
    per = pb.token_report()
    for m in pb.modules:
        if m.tokens > MAX_MODULE_TOKENS:
            return f"模块「{m.key}」{m.tokens} token 超单模块上限 {MAX_MODULE_TOKENS}"
    if per["battle_total"] > MAX_BATTLE_TOKENS:
        return f"M1–M4 合计 {per['battle_total']} token 超上限 {MAX_BATTLE_TOKENS}"
    return None


def bounded_edit(pb: Playbook, candidates: list[EditCandidate], *,
                 lt: int = DEFAULT_LT, allow_protected: bool = False,
                 version: str | None = None) -> tuple[Playbook, list[EditReport]]:
    """按 L_t 有界应用编辑，产出新版本 Playbook + 逐条报告（审计）。

    - 排序：`support_count × max(avg_delta,0.001) × coverage` 降序；
    - **L_t 是「应用数」上限**：按序尝试，应用满 `lt` 条即停——被拒的（PROTECTED/
      超限/锚点问题/空文本/多行）不占预算槽，排名更低的合法候选仍有被尝试的机会；
    - 目标模块 `protected` 且非慢更新 → rejected；token 超限 → rejected 并回滚；
    - 空文本 / 含换行的文本（破坏「单规则一行」）→ rejected；
    - 版本递增一次（有应用才递增）；`version=` 覆盖版本号（R4 多步循环里父代不变时
      仍要全局唯一版本，避免候选版本撞车）；rejected 候选由调用方存入 rejected buffer。
    """
    if not isinstance(pb, Playbook):
        raise TypeError(f"pb 必须是 Playbook，实际 {type(pb).__name__}")
    new_version = version if version is not None else Playbook.next_version(pb.version)
    ordered = sorted(candidates, key=_score, reverse=True)
    modules = {m.key: m for m in pb.modules}
    reports: list[EditReport] = []
    applied = 0
    for c in ordered:
        if applied >= lt:
            break
        module = modules.get(c.module_key)
        if module is None:
            reports.append(EditReport(c, "rejected", f"模块「{c.module_key}」不存在", pb.version))
            continue
        if module.protected and not allow_protected:
            reports.append(EditReport(c, "rejected", "[PROTECTED] 保护区：仅 epoch 慢更新可写", pb.version))
            continue
        if c.op != "delete" and not c.text.strip():
            reports.append(EditReport(c, "rejected", "编辑文本为空", pb.version))
            continue
        if c.op != "delete" and "\n" in c.text:
            reports.append(EditReport(c, "rejected", "编辑文本含换行（须单规则一行）", pb.version))
            continue
        new_module, reason = _apply_edit(module, c)
        if reason:
            reports.append(EditReport(c, "rejected", reason, pb.version))
            continue
        modules[c.module_key] = new_module
        trial = Playbook(pb.version, list(modules.values()))
        lim = _over_limit(trial)
        if lim:
            modules[c.module_key] = module       # 回滚
            reports.append(EditReport(c, "rejected", lim, pb.version))
            continue
        reports.append(EditReport(c, "applied", "已应用", new_version))
        applied += 1
    if not applied:
        return pb, reports                        # 无编辑落地 → 版本不变
    new_pb = Playbook(new_version, list(modules.values()))
    return new_pb, reports


def append_report(entries: list[EditReport], out_path) -> Path:
    """把一组 EditReport 追加到 `edit_apply_report.jsonl`（原子写，审计）。"""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    text = existing + ("\n" if existing and not existing.endswith("\n") else "") + \
        "".join(json.dumps(r.to_dict(), ensure_ascii=False) + "\n" for r in entries)
    import os
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".edit-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def promote(pb_candidate, d_sel) -> bool:
    """晋级门：复合分严格「>」才接受、平手拒绝——**已由 R4 完整实现**。

    R4 起晋级逻辑在 `PlaybookPool.try_promote`（复合分严格超过 + 历史回归门）+
    `league.promotion_gate`（≥45%）。本占位保留签名（plan 关键签名）并指路，防误用。
    """
    raise NotImplementedError(
        "promote 已由 R4 实现：PlaybookPool.try_promote + league.promotion_gate（回归门 ≥45%）。")


# ---------------------------------------------------------------------------
# R5：epoch 慢更新（SkillOpt §5.2：慢更新候选同样过门禁，不是免检通道）
# ---------------------------------------------------------------------------

# 连续稳定多少 epoch 才固化进 [PROTECTED]（「被反复验证的规则」的门槛）。
PROTECT_AFTER_EPOCHS = 2


def _lines(text: str) -> list[str]:
    return text.splitlines() if text else []


def diff_rules(prev: Playbook, cur: Playbook) -> dict[str, dict]:
    """逐模块规则行 diff：`{key: {"stable": [行], "added": [行], "removed": [行]}}`。

    规则 = 按行组织的单条指令（锚点编辑的行单位）。稳定 = 两版都有；added = 本 epoch 新增；
    removed = 本 epoch 被移除。顺序确定（stable/added 按 cur 序、removed 按 prev 序）。
    **多集（multiset）语义**：重复行按出现次数差计算——本 epoch 追加一条与已有相同的行
    归入 added（而非 stable），避免「重复强调有效规则」被误判为稳定而永不被撤回（m6 修复）。
    """
    from collections import Counter
    out: dict[str, dict] = {}
    for key in Playbook.MODULE_KEYS:
        pm, cm = prev.module(key), cur.module(key)
        pl, cl = _lines(pm.text) if pm else [], _lines(cm.text) if cm else []
        pc, cc = Counter(pl), Counter(cl)
        # 多集差：cur 超出 prev 计数的行才算 added；其余按出现顺序归 stable。
        added: list[str] = []
        stable: list[str] = []
        for line in cl:
            excess = cc[line] - pc.get(line, 0)
            if excess > 0 and added.count(line) < excess:
                added.append(line)
            else:
                stable.append(line)
        removed: list[str] = []
        for line in pl:
            if pc[line] > cc.get(line, 0):
                removed.append(line)
        out[key] = {"stable": stable, "added": added, "removed": removed}
    return out


def slow_update(prev: Playbook, cur: Playbook, *, improved: bool,
                stable_for: dict[str, int] | None = None,
                persistent_failures: tuple[str, ...] = (),
                version: str | None = None) -> tuple[Playbook, list[EditCandidate], list[EditReport]]:
    """epoch 慢更新：比较上一/当前 Champion，产新手册（[PROTECTED] 固化/撤回/移除）+ 审计。

    四类规则（§五 / plan R5）：
    - **稳定成功**：模块**整体未变**（无 added/removed）且连续稳定 ≥ `PROTECT_AFTER_EPOCHS`
      epoch → 标 `protected=True`（固化进保护区，后续快速编辑不得覆写）；
    - **改进**（`improved=True`）：本 epoch 新增/变更的规则保留；
    - **回退**（`improved=False`）：变更规则**撤回**为上一版本行（防漂移）；
    - **持续失败**：跨 epoch 反复失败/被拒的规则 → 移除。
    **new_pb 直接构建（保证正确性）**；candidates/reports 是**逐条准确审计**（cur→final 的真实
    diff，M5/n1 修复：regress=delete 每条 added + insert_after 恢复每条 removed；持续失败=
    delete；固化=protect，幂等跳过已保护模块）。报告标 applied 只因 new_pb 确实反映了这些变更。
    **输出同样过门禁**（调用方 run_epochs 负责非降级门 + 回归门，不是免检通道）。
    """
    from collections import Counter
    diff = diff_rules(prev, cur)
    stable_for = stable_for or {}
    # 纵深防御（M1）：空/空白持续失败条目是噪声——`"" in ln` 恒真会清空全部模块。
    failures = tuple(pf for pf in persistent_failures if pf.strip())
    candidates: list[EditCandidate] = []
    modules: list[PlaybookModule] = []
    for key in Playbook.MODULE_KEYS:
        pm, cm = prev.module(key), cur.module(key)
        if cm is None:
            continue
        d = diff[key]
        cur_lines = _lines(cm.text)
        prev_lines = _lines(pm.text) if pm else []
        # 目标文本：回退 → prev 的行；否则 cur 的行；再移除持续失败行
        base_lines = prev_lines if (d["added"] and not improved) else cur_lines
        final_lines = [ln for ln in base_lines if not any(pf in ln for pf in failures)]
        # 固化保护（整体未变 + 连续稳定达门槛；幂等跳过已保护）
        protect = (not d["added"] and not d["removed"] and d["stable"]
                   and stable_for.get(key, 1) >= PROTECT_AFTER_EPOCHS and not cm.protected)
        if protect:
            candidates.append(EditCandidate(
                key, "protect", text="", source_type="epoch_stable",
                evidence=(f"整体稳定 {stable_for.get(key, 1)} epoch",)))
        # 审计候选（cur → final 的真实 diff）
        cc, fc = Counter(cur_lines), Counter(final_lines)
        for line in cur_lines:
            if cc[line] > fc.get(line, 0):
                candidates.append(EditCandidate(
                    key, "delete", anchor=line,
                    source_type="epoch_regress" if (d["added"] and not improved and line in d["added"])
                    else "epoch_persistent_failure",
                    evidence=("回退：撤回新增" if line in d["added"] else "持续失败移除",)))
        for line in final_lines:
            if fc[line] > cc.get(line, 0):
                idx = final_lines.index(line)
                anchor = final_lines[idx - 1] if idx > 0 else None
                candidates.append(EditCandidate(
                    key, "insert_after", text=line, anchor=anchor or "",
                    source_type="epoch_regress", evidence=("回退：恢复上一版本规则",)))
        modules.append(PlaybookModule(key, "\n".join(final_lines), protect or cm.protected))

    new_pb = Playbook(version or Playbook.next_version(cur.version), modules)
    reports = [EditReport(c, "applied", "epoch 慢更新", new_pb.version) for c in candidates]
    return new_pb, candidates, reports
