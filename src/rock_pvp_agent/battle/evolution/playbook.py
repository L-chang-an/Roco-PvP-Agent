"""R3 Playbook（战术手册）：5 模块 + `[PROTECTED]` 保护区 + 版本 + token 上限。

§3.1：Playbook 是注入 LLM 玩家的可训练状态（SkillOpt skill-as-trainable-state），
模块化、可审计、可回滚。每模块一份自然语言指令块，单规则条目按行组织——
编辑（append/insert_after/replace/delete）以「行」为单位操作，审计可追溯到每条规则。

- **`[PROTECTED]` 保护区**：只有 epoch 级慢更新（R5）能写，快速步进编辑（R3/R4）不得覆写。
- **token 上限**：单模块 800、M1–M4 合计 ≤2,500（SkillOpt 实测最终技能 379–1,995 token；
  设计目标不是写长手册）。估算用确定性近似（CJK 约 1 token/字，取 `len/2`）。
- 版本不可变：每次编辑产出**新版本**（`pb_v{n+1}`），旧版本可回滚。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 模块键（§3.1）：M5 build_proposer 只在 R6 构筑阶段激活，不参与 M1–M4 的合计上限。
MODULE_KEYS: tuple[str, ...] = (
    "M1 team_reader",
    "M2 action_selector",
    "M3 energy_planner",
    "M4 endgame",
    "M5 build_proposer",
)
_BATTLE_MODULE_KEYS: tuple[str, ...] = MODULE_KEYS[:4]   # M1–M4（对战期激活）

# token 上限（§3.1，确定性近似：CJK 1 字 ≈ 1 token，取 len/2 保守估）。
MAX_MODULE_TOKENS = 800
MAX_BATTLE_TOKENS = 2500


def estimate_tokens(text: str) -> int:
    """确定性 token 近似：`max(1, len(text) // 2)`（CJK 为主的规则文本）。"""
    return max(1, len(text or "") // 2)


_INITIAL_TEXT: dict[str, str] = {
    "M1 team_reader": "从公开事件推断对手配置倾向与节奏。",
    "M2 action_selector": "按局面选择技能/换人/聚能。",
    "M3 energy_planner": "规划能量收支，避免透支。",
    "M4 endgame": "残局处理：一击必杀线、锁胜。",
    "M5 build_proposer": "构筑提议（R6 激活）。",
}


@dataclass
class PlaybookModule:
    """一个模块：`text` 是按行组织的规则块（单规则条目一行）。"""

    key: str
    text: str = ""
    protected: bool = False        # [PROTECTED] 区：仅 epoch 慢更新可写

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


@dataclass
class Playbook:
    """战术手册：不可变版本 + 有序模块列表。编辑产出新版本。"""

    version: str
    modules: list[PlaybookModule] = field(default_factory=list)

    MODULE_KEYS = MODULE_KEYS

    @classmethod
    def initial(cls) -> "Playbook":
        """初始手册：pb_v000，5 模块各一句基线（全部非保护区，可快速编辑）。"""
        return cls(version="pb_v000",
                   modules=[PlaybookModule(k, _INITIAL_TEXT[k]) for k in MODULE_KEYS])

    def module(self, key: str) -> PlaybookModule | None:
        return next((m for m in self.modules if m.key == key), None)

    def text(self) -> str:
        """整册文本（注入 LLMPlayer 系统提示的形态：`[模块键]` + 规则行）。"""
        return "\n\n".join(f"[{m.key}]\n{m.text}" for m in self.modules if m.text)

    def token_report(self) -> dict:
        """每模块 token + M1–M4 合计（审计/健康度用）。"""
        per = {m.key: m.tokens for m in self.modules}
        return {"per_module": per,
                "battle_total": sum(m.tokens for m in self.modules
                                    if m.key in _BATTLE_MODULE_KEYS)}

    def validate(self) -> list[str]:
        """合法性问题列表（空 = 合法）。token 超上限 / 缺模块都报。"""
        errors: list[str] = []
        keys = {m.key for m in self.modules}
        missing = [k for k in MODULE_KEYS if k not in keys]
        if missing:
            errors.append(f"缺模块：{missing}")
        for m in self.modules:
            if m.tokens > MAX_MODULE_TOKENS:
                errors.append(f"模块「{m.key}」{m.tokens} token 超单模块上限 {MAX_MODULE_TOKENS}")
        battle_total = sum(m.tokens for m in self.modules if m.key in _BATTLE_MODULE_KEYS)
        if battle_total > MAX_BATTLE_TOKENS:
            errors.append(f"M1–M4 合计 {battle_total} token 超上限 {MAX_BATTLE_TOKENS}")
        return errors

    @staticmethod
    def next_version(version: str) -> str:
        """版本递增：`pb_v{n}` → `pb_v{n+1}`。"""
        num = int(version.removeprefix("pb_v"))
        return f"pb_v{num + 1}"

    def copy(self) -> "Playbook":
        """浅拷贝（模块内容拷贝，字段不变；R4 剥削者 fork / 池存档用）。"""
        return Playbook(self.version, [PlaybookModule(m.key, m.text, m.protected)
                                       for m in self.modules])

    def to_dict(self) -> dict:
        """纯 dict（池持久化 / 审计）。"""
        return {"version": self.version,
                "modules": [{"key": m.key, "text": m.text, "protected": m.protected}
                            for m in self.modules]}

    @classmethod
    def from_dict(cls, d: dict) -> "Playbook":
        """dict → Playbook（`to_dict` 的逆；缺字段容错为初始模块）。"""
        modules = [PlaybookModule(m["key"], m.get("text", ""),
                                  protected=m.get("protected", False))
                   for m in d.get("modules", [])]
        if not modules:
            return cls.initial()
        return cls(d.get("version", "pb_v000"), modules)
