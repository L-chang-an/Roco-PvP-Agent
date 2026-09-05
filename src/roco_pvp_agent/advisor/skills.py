"""顾问 Skill 注册表（M4）：带版本/哈希/工具白名单的程序化技能，只读可审计。

Skill 是「数据」不是「指令」：`body`/`trigger` 只作流程建议，不能覆盖数据库事实、护栏
或工具白名单。`allowed_tools` 与顾问工具集求交（代码级裁剪），新 Skill 默认 probationary
（晋级门在 M5）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel

from roco_pvp_agent.tooling import ToolRegistry

SKILLS_DIR = Path(__file__).resolve().parent / "skills"


class Skill(BaseModel):
    name: str
    version: int
    hash: str               # body 的 sha256（内容校验，防篡改）
    status: str             # "active" | "probationary"
    trigger: list[str]      # 触发关键词
    boundary: list[str]     # 硬边界
    allowed_tools: list[str]  # Skill 最小权限；加载时与当前 Agent Registry 求交
    verification: list[str]   # 校验项
    body: str               # 程序性步骤（自然语言，仅流程建议）


def _hash_body(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def load_skills() -> list[Skill]:
    """读 advisor/skills/*.json；hash 与 body 不符 → ValueError（篡改检测）。"""
    skills: list[Skill] = []
    for f in sorted(SKILLS_DIR.glob("*.json")):
        raw = json.loads(f.read_text(encoding="utf-8"))
        skill = Skill(**raw)
        if skill.hash != _hash_body(skill.body):
            raise ValueError(f"Skill「{skill.name}」hash 与 body 不匹配（可能被篡改）。")
        skills.append(skill)
    return skills


def register_skill(spec: dict) -> Skill:
    """注册新 Skill：强制 status=probationary，按 body 计算 hash（仅内存，不落盘）。"""
    body = spec.get("body", "")
    return Skill(**{**spec, "status": "probationary", "hash": _hash_body(body)})


def retrieve_team_skill(query: str, *, registry: ToolRegistry) -> list[dict]:
    """检索 active Skill；allowed_tools 只保留当前 Agent 已注册的工具。"""
    out: list[dict] = []
    for skill in load_skills():
        if skill.status != "active":
            continue
        if not any(t in query for t in skill.trigger):
            continue
        trimmed = [t for t in skill.allowed_tools if registry.get(t) is not None]
        out.append({**skill.model_dump(), "allowed_tools": trimmed})
        if len(out) >= 3:
            break
    return out
