"""数据加载与归一：把两个 JSON 读成引擎认识的结构。

唯一的坑在 `_to_int`：数值字段**绝不写 `x or default`**——`"0"` 与 `0` 都是合法值，
而 `0` 是 falsy。参考项目正是在这里栽的：防御技能 `strong: null` → `power=0.0` →
走 `float(... or 30)` → 52 个防御技能的 50%~100% 减伤全部退化成固定 30%。
本项目的防御/状态技能 `strong` 全是 `"0"`，第一天就会撞上这个坑，所以写死规则：
`default if raw is None or raw == "" else int(raw)`。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"
E0_SKILLS_FILE = DATA_DIR / "e0_skills.json"
E0_SPIRITS_FILE = DATA_DIR / "e0_spirits.json"

# 中文六维 → 英文 key。顺序即稳定输出顺序（hp, atk, sp_atk, def, sp_def, speed）。
STAT_KEY_MAP: dict[str, str] = {
    "生命": "hp",
    "物攻": "atk",
    "魔攻": "sp_atk",
    "物防": "def",
    "魔防": "sp_def",
    "速度": "speed",
}
# 英文六维的稳定顺序：校验 iv 键、打印六维都用它。
STAT_KEYS: tuple[str, ...] = ("hp", "atk", "sp_atk", "def", "sp_def", "speed")


def _to_int(raw: str | int | None, default: int = 0) -> int:
    """字符串数字 → int。**绝不写 `raw or default`**——`"0"` 与 `0` 都是合法值。

    只写 `default if raw is None or raw == "" else int(raw)`。
    """
    if raw is None or raw == "":
        return default
    return int(raw)


def _stats_zh_to_en(stats: dict[str, Any]) -> dict[str, int]:
    """中文六维 → 英文 key 的 int dict，按 STAT_KEY_MAP 顺序稳定输出。"""
    return {en: _to_int(stats.get(zh)) for zh, en in STAT_KEY_MAP.items()}


@dataclass(frozen=True)
class RawSkill:
    """归一后的技能静态定义。`power` / `energy_cost` 已从字符串转 int。"""

    name: str
    type: str            # 系别，E0 全为「普通」
    kind: str            # 物攻 / 魔攻 / 防御 / 状态
    power: int           # 来自 strong，防御/状态为 0（不是 30）
    energy_cost: int     # 来自 energy
    desc: str = ""       # 只用于展示，引擎永不解析

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RawSkill":
        return cls(
            name=d["name"],
            type=d.get("type", "普通"),
            kind=d.get("kind", ""),
            power=_to_int(d.get("strong"), default=0),
            energy_cost=_to_int(d.get("energy"), default=0),
            desc=d.get("desc", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "kind": self.kind,
            "power": self.power,
            "energy_cost": self.energy_cost,
            "desc": self.desc,
        }


@dataclass(frozen=True)
class RawSpirit:
    """归一后的精灵。stats 已为英文 key 的 int；skills 拆成默认池与血脉池。"""

    name: str
    types: tuple[str, ...]                 # type[]，系别（E0 只记录，E2 起血脉改写）
    trait_name: str
    trait_desc: str
    stats: dict[str, int]                  # 已归一（英文 key、int）
    skills_default: tuple[str, ...]        # skills.默认
    skills_bloodline: tuple[str, ...]      # skills.血脉（选了血脉才并入可学池）
    bloodlines: tuple[str, ...] = ()       # 合法血脉列表（E0 手写数据自带；真实数据缺省 → 空）

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RawSpirit":
        stats = d.get("stats") or {}
        trait = d.get("trait") or {}
        skills = d.get("skills") or {}
        return cls(
            name=d["name"],
            types=tuple(d.get("type") or []),
            trait_name=trait.get("name", "") if isinstance(trait, dict) else "",
            trait_desc=trait.get("desc", "") if isinstance(trait, dict) else "",
            stats=_stats_zh_to_en(stats),
            skills_default=tuple(skills.get("默认") or []),
            skills_bloodline=tuple(skills.get("血脉") or []),
            bloodlines=tuple(d.get("bloodlines") or []),
        )


@lru_cache(maxsize=1)
def load_skills() -> dict[str, RawSkill]:
    """E0 技能表，按 name 索引。lru_cache 让多次调用只读一次文件。

    E3 换真实数据源时改这里（新增 source 参数），调用方不变。
    """
    raw = json.loads(E0_SKILLS_FILE.read_text(encoding="utf-8"))
    return {item["name"]: RawSkill.from_dict(item) for item in raw}


@lru_cache(maxsize=1)
def load_spirits() -> dict[str, RawSpirit]:
    """E0 精灵表，按 name 索引，六维已归一为 int。"""
    raw = json.loads(E0_SPIRITS_FILE.read_text(encoding="utf-8"))
    return {item["name"]: RawSpirit.from_dict(item) for item in raw}
