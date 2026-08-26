"""数据加载与归一：把 JSON 读成引擎认识的结构。

三数据源：`E0`（手写教学数据，6 精灵 / 14 技能）、`FULL`（真实数据，594 精灵 / 553 技能）、
`VALID`（E3：FULL 精灵 + **已实装效果的** 179 技能，见 valid_skills.json）。`load_skills` /
`load_spirits` 带 `source` 参数，默认 `DEFAULT_SOURCE`（E0）——翻转默认的唯一前提是真实技能名
进了效果表（`E0_EFFECTS`），在那之前真实 roster 只做配队校验、不进引擎。

唯一的坑在 `_to_int`：数值字段**绝不写 `x or default`**——`"0"` 与 `0` 都是合法值，
而 `0` 是 falsy。参考项目正是在这里栽的：防御技能 `strong: null` → `power=0.0` →
走 `float(... or 30)` → 52 个防御技能的 50%~100% 减伤全部退化成固定 30%。
真实数据的 `strong: null`（状态/防御技能）同样走这条路，写死规则：
`default if raw is None or raw == "" else int(raw)`。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"
E0_SKILLS_FILE = DATA_DIR / "e0_skills.json"
E0_SPIRITS_FILE = DATA_DIR / "e0_spirits.json"
FULL_SKILLS_FILE = DATA_DIR / "full_skills.json"
FULL_SPIRITS_FILE = DATA_DIR / "full_spirits.json"
VALID_SKILLS_FILE = DATA_DIR / "valid_skills.json"   # E3：已实装效果的技能子集
FAMILIES_FILE = DATA_DIR / "families.json"   # 家族详情（scripts/build_families.py 生成）

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


class DataSource(str, Enum):
    """数据源。str 子类，兼容 3.10（StrEnum 是 3.11+）。

    E0 教学 / FULL 全量真实 / VALID（E3）：FULL 精灵 + 已实装效果的技能子集。
    """

    E0 = "E0"
    FULL = "FULL"
    VALID = "VALID"


# 唯一翻转点：效果表覆盖真实技能名之前必须留在 E0。
DEFAULT_SOURCE = DataSource.E0


def _is_full_like(source: DataSource) -> bool:
    """FULL / VALID 共享「真实数据」语义（精灵表、家族、脏值跳过都走 FULL 路径）。"""
    return source in (DataSource.FULL, DataSource.VALID)


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
    """归一后的技能静态定义。`power` / `energy_cost` 已从字符串转 int。

    FULL（553 条）与 E0 字段完全同构：`strong: null`（状态/防御）→ `power=0`，
    `energy` 全是字符串数字。**无需新增字段**。
    """

    name: str
    type: str            # 系别，FULL 下是真实 18 系
    kind: str            # 物攻 / 魔攻 / 防御 / 状态
    power: int           # 来自 strong，防御/状态为 0（不是 30）
    energy_cost: int     # 来自 energy
    desc: str = ""       # 只用于展示，引擎永不解析

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RawSkill":
        """输入：JSON 原始技能 dict（name/type/kind/desc/strong/energy）。
        输出：归一化的 RawSkill（power/energy_cost 已转 int，`strong: null` → 0）。"""
        return cls(
            name=d["name"],
            type=d.get("type", "普通"),
            kind=d.get("kind", ""),
            power=_to_int(d.get("strong"), default=0),
            energy_cost=_to_int(d.get("energy"), default=0),
            desc=d.get("desc", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        """输出：RawSkill 的 dict 视图（供报告/调试；字段已是归一化形状）。"""
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
    """归一后的精灵。stats 已为英文 key 的 int；skills 拆成各池。

    E0 数据只有 `默认` / `血脉` 两池；FULL 数据有 `默认` / `血脉` / `技能石` /
    `传说`，并携带家族 / 首领 / 图鉴号 / 形态信息。新增字段全带默认值，E0
    反序列化不受影响。
    """

    name: str
    types: tuple[str, ...]                 # type[]，精灵**自身**系别（克制/STAB 的依据；血脉不改写）
    trait_name: str
    trait_desc: str
    stats: dict[str, int]                  # 已归一（英文 key、int）
    skills_default: tuple[str, ...]        # skills.默认
    skills_bloodline: tuple[str, ...]      # skills.血脉
    bloodlines: tuple[str, ...] = ()       # 合法血脉列表（E0 手写数据自带；FULL 无此概念）

    # ── FULL 新增（E0 全走默认值）──
    skills_stone: tuple[str, ...] = ()     # skills.技能石
    skills_legend: tuple[str, ...] = ()    # skills.传说（仅 14 条有，1 个技能名）
    is_boss: bool = False                  # isBoss：首领形态不可入队
    family_key: str | None = None          # 家族 = evolution 链首精灵的 number（无链 → None）
    family_lowest: bool = False            # name ∈ 各链 chain[0] 的并集（家族最低阶）
    number: str = ""                       # 图鉴号，仅展示；**不做身份**（同家族可跨号）
    region: str = ""                       # 形态描述符（"蜕皮时的样子"等）

    @classmethod
    def from_dict(cls, d: dict[str, Any], *, full: bool = False,
                  family_key: str | None = None, family_lowest: bool = False) -> "RawSpirit":
        """输入：JSON 原始精灵 dict（name/type/trait/stats/skills/bloodlines…）。
        full=False（E0 形状）只取公共字段；full=True（FULL/VALID）再补技能石/传说/
        首领/家族/形态字段。输出：归一化的 RawSpirit（stats 已转英文 key 的 int）。"""
        stats = d.get("stats") or {}
        trait = d.get("trait") or {}
        skills = d.get("skills") or {}
        common = dict(
            name=d["name"],
            types=tuple(d.get("type") or []),
            trait_name=trait.get("name", "") if isinstance(trait, dict) else "",
            trait_desc=trait.get("desc", "") if isinstance(trait, dict) else "",
            stats=_stats_zh_to_en(stats),
            skills_default=tuple(skills.get("默认") or []),
            skills_bloodline=tuple(skills.get("血脉") or []),
            bloodlines=tuple(d.get("bloodlines") or []),
        )
        if not full:
            return cls(**common)
        return cls(
            **common,
            skills_stone=tuple(skills.get("技能石") or []),
            skills_legend=tuple(skills.get("传说") or []),
            is_boss=bool(d.get("isBoss")),
            family_key=family_key,
            family_lowest=family_lowest,
            number=d.get("number", ""),
            region=d.get("region", ""),
        )


def _should_skip(d: dict[str, Any]) -> str | None:
    """FULL 脏记录跳过原因，None = 保留。已确认真实数据只有「学院呱呱」命中。"""
    skills = d.get("skills")
    if skills is None or not skills:
        return "技能表为空"
    stats = d.get("stats") or {}
    if not stats or all(v is None or v == "" for v in stats.values()):
        return "六维缺失"
    return None


def _family_key(d: dict[str, Any], number_by_name: dict[str, str]) -> str | None:
    """家族 = evolution 各链首精灵的 number（负责人指定的「最低阶编号一致」规则）。

    无 evolution（如脏记录）→ None，自成一族不冲突。多条链通常共享同一链首
    （分支进化），取其 number；若出现多链首（数据里不应发生）则返回 None 保守跳过。
    """
    chains = d.get("evolution") or []
    if not chains:
        return None
    bases = {chain[0] for chain in chains if chain}
    if len(bases) != 1:
        return None
    return number_by_name.get(bases.pop())


def _is_family_lowest(d: dict[str, Any]) -> bool:
    """name 是否是该家族的最低阶（各链 chain[0] 的并集）。"""
    chains = d.get("evolution") or []
    return d["name"] in {chain[0] for chain in chains if chain}


def derive_families(records: list[dict]) -> dict[str, dict]:
    """从原始精灵记录派生家族（负责人指定的「链首编号一致 → 同族」规则）。

    **families.json 的生成逻辑**，也是它的一致性测试的比对基准——运行时不再推导，
    只读 `families.json`。返回：
        {family_key: {"lowest": [链首名...], "members": [成员名...]}}
    无进化链的记录（学院呱呱）不属任何家族，不进结果。
    """
    number_by_name = {r["name"]: r.get("number", "") for r in records}
    groups: dict[str, dict] = {}
    for r in records:
        key = _family_key(r, number_by_name)
        if key is None:
            continue
        info = groups.setdefault(key, {"lowest": [], "members": []})
        if _is_family_lowest(r):
            info["lowest"].append(r["name"])
        info["members"].append(r["name"])
    return {
        key: {"lowest": sorted(set(v["lowest"])), "members": sorted(set(v["members"]))}
        for key, v in sorted(groups.items())
    }


@lru_cache(maxsize=4)
def load_skills(source: DataSource = DEFAULT_SOURCE) -> dict[str, RawSkill]:
    """技能表，按 name 索引。`source` 作缓存键；默认 E0，FULL/VALID 显式指定。

    VALID = E3 的可对战技能子集（已实装效果的 P1∪P2）。lru_cache(maxsize=4)：
    同进程混用多个 source 时不被互相顶掉、反复读盘。
    """
    if source is DataSource.E0:
        file = E0_SKILLS_FILE
    elif source is DataSource.VALID:
        file = VALID_SKILLS_FILE
    else:
        file = FULL_SKILLS_FILE
    raw = json.loads(file.read_text(encoding="utf-8"))
    return {item["name"]: RawSkill.from_dict(item) for item in raw}


@lru_cache(maxsize=4)
def load_spirits(source: DataSource = DEFAULT_SOURCE) -> dict[str, RawSpirit]:
    """精灵表，按 name 索引，六维已归一为 int。

    FULL / VALID：跳过脏记录（学院呱呱）并登记到 `load_skipped_spirits`；家族 key / 首领 /
    形态字段在加载时派生。**家族从 `families.json` 直接查**（scripts/build_families.py
    已把推导固化成数据），不再每次从 evolution 现场推导。
    """
    if source is DataSource.E0:
        raw = json.loads(E0_SPIRITS_FILE.read_text(encoding="utf-8"))
        return {item["name"]: RawSpirit.from_dict(item) for item in raw}
    raw = json.loads(FULL_SPIRITS_FILE.read_text(encoding="utf-8"))
    doc = _load_families_doc(source)
    name_to_fam = {
        name: (key, tuple(info["lowest"]))
        for key, info in doc.items()
        for name in info["members"]
    }
    out: dict[str, RawSpirit] = {}
    for item in raw:
        reason = _should_skip(item)
        if reason is not None:
            continue
        key, lowest = name_to_fam.get(item["name"], (None, ()))
        sp = RawSpirit.from_dict(
            item, full=True,
            family_key=key,
            family_lowest=item["name"] in lowest,
        )
        out[sp.name] = sp
    return out


@lru_cache(maxsize=4)
def load_skipped_spirits(source: DataSource = DEFAULT_SOURCE) -> tuple[tuple[str, str], ...]:
    """FULL/VALID：被跳过的脏记录 `(名字, 原因)`。E0：空。`--data-report` 用。"""
    if source is DataSource.E0:
        return ()
    raw = json.loads(FULL_SPIRITS_FILE.read_text(encoding="utf-8"))
    return tuple(
        (item["name"], _should_skip(item))
        for item in raw
        if _should_skip(item) is not None
    )


@lru_cache(maxsize=4)
def _load_families_doc(source: DataSource = DEFAULT_SOURCE) -> dict[str, dict]:
    """families.json：{family_key: {"lowest": [链首名...], "members": [成员名...]}}。"""
    if source is DataSource.E0:
        return {}
    return json.loads(FAMILIES_FILE.read_text(encoding="utf-8"))


@lru_cache(maxsize=4)
def load_families(source: DataSource = DEFAULT_SOURCE) -> dict[str, tuple[str, ...]]:
    """家族 key（链首编号）→ 家族成员名。FULL/VALID：从 families.json 直接查；E0：空。"""
    if source is DataSource.E0:
        return {}
    return {k: tuple(v["members"]) for k, v in _load_families_doc(source).items()}


@lru_cache(maxsize=4)
def load_types(source: DataSource = DEFAULT_SOURCE) -> frozenset[str]:
    """技能表里出现的全部系别（FULL = 18 系），血脉合法性校验用。"""
    return frozenset(s.type for s in load_skills(source).values())
