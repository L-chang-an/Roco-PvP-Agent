"""只读目录查询：白名单 DSL + 引擎归一化数据的确定性读取。

安全边界：Agent 只能通过 `search_spirits` 的白名单查询 DSL 读数据，DSL 只编译到已审计的
`dataset.*` / `teambuilder.learnable_skills`，**没有** `eval`/`exec`/`open`/`subprocess`/网络/
文件写。`source`（FULL/VALID）由工具签名锁定（默认 VALID），模型不可传任意字符串绕过语义。

数据口径：只回 `RawSpirit`/`RawSkill` 的归一化字段，不碰原始 JSON 里未归一化的数组。
"""

from __future__ import annotations

from dataclasses import dataclass

from environment.datafingerprint import data_digest, rules_digest
from environment.dataset import (
    DataSource,
    RawSpirit,
    load_families,
    load_skills,
    load_spirits,
    load_types,
)
from environment.rules import DEFAULT_RULES
from environment.statline import NATURE_BONUS, NEUTRAL_NATURE
from environment.teambuilder import learnable_skills

# 允许的 field / op 白名单（DSL 的原子，表外一律拒绝）。
_FIELDS = frozenset({"name", "type", "trait", "learnable_skill", "family", "is_boss", "number"})
_OPS = frozenset({"eq", "in", "contains"})

# 字段值形状：标量 / 集合 / 布尔（决定 op 的合法 value 类型）。
_FIELD_KIND = {
    "name": "scalar",
    "type": "collection",
    "trait": "scalar",
    "learnable_skill": "collection",
    "family": "scalar",
    "is_boss": "bool",
    "number": "scalar",
}

# 防御性注入字样：DSL 从不执行任何代码，value 出现这些 token 一律拒绝（宁保守不放过）。
_DANGEROUS_TOKENS = ("eval", "exec", "__import__", "open", "subprocess", "import")


class QueryNotAllowed(ValueError):
    """DSL 查询越界：field/op 不在白名单、value 类型不符、或含注入字样。"""


@dataclass(frozen=True)
class SpiritFilter:
    """`search_spirits` 的过滤原子。

    - `field` ∈ {name, type, trait, learnable_skill, family, is_boss, number}
    - `op`   ∈ {eq, in, contains}
    - `value`：str | bool | list[str]，类型随 (field, op) 而定（见 `_validate_filter`）。

    op 语义（确定性）：
    - 集合字段（type / learnable_skill）：`in`/`contains` = `value in field_value`（value 为 str）；
      `eq` = 整表相等（value 为 list）。
    - 标量字段（name / trait / number / family）：`eq` = 相等；`in` = `field_value in value`
      （value 为 list）；`contains` = 子串（`value in str(field_value)`）。
    - `is_boss` 只支持 `eq`（value 为 bool）。
    - `family` 的 value 可传**精灵名或 family_key**，反查后归一比较。
    """

    field: str
    op: str
    value: str | bool | list[str]


def _field_value(sp: RawSpirit, field: str, source: DataSource):
    """精灵在某 field 上的值（已审计函数表，别无其他读取路径）。"""
    if field == "name":
        return sp.name
    if field == "type":
        return list(sp.types)
    if field == "trait":
        return sp.trait_name
    if field == "is_boss":
        return sp.is_boss
    if field == "number":
        return sp.number
    if field == "learnable_skill":
        return learnable_skills(sp.name, "", source)
    if field == "family":
        return sp.family_key
    raise QueryNotAllowed(f"field 不在白名单：{field!r}")


def _check_dangerous(value) -> None:
    """str value 含注入字样 → 拒绝（防御性，DSL 本身从不执行任何代码）。"""
    if isinstance(value, str):
        low = value.lower()
        for token in _DANGEROUS_TOKENS:
            if token in low:
                raise QueryNotAllowed(f"value 含禁止字样 {token!r}")
    elif isinstance(value, (list, tuple)):
        for item in value:
            _check_dangerous(item)


def _validate_filter(f: SpiritFilter) -> None:
    """校验单个过滤原子：field/op 白名单 + value 类型 + 注入字样。"""
    if f.field not in _FIELDS:
        raise QueryNotAllowed(f"field 不在白名单：{f.field!r}")
    if f.op not in _OPS:
        raise QueryNotAllowed(f"op 不在白名单：{f.op!r}")
    _check_dangerous(f.value)
    kind = _FIELD_KIND[f.field]
    if kind == "bool":
        if f.op != "eq":
            raise QueryNotAllowed("is_boss 只支持 op=eq")
        if not isinstance(f.value, bool):
            raise QueryNotAllowed(f"is_boss 的 value 必须为 bool，实际 {type(f.value).__name__}")
    elif kind == "collection":
        if f.op == "eq":
            if not isinstance(f.value, list):
                raise QueryNotAllowed(f"{f.field} 集合字段 eq 的 value 必须为 list")
        elif not isinstance(f.value, str):
            raise QueryNotAllowed(f"{f.field} 集合字段 {f.op} 的 value 必须为 str")
    else:  # scalar
        if f.op == "eq":
            if not isinstance(f.value, str):
                raise QueryNotAllowed(f"{f.field} 标量字段 eq 的 value 必须为 str")
        elif f.op == "in":
            if not isinstance(f.value, list):
                raise QueryNotAllowed(f"{f.field} 标量字段 in 的 value 必须为 list")
        elif not isinstance(f.value, str):
            raise QueryNotAllowed(f"{f.field} 标量字段 contains 的 value 必须为 str")


def _match(field_value, op: str, value) -> bool:
    """按 op 比较（假定类型已由 `_validate_filter` 保证）。"""
    if isinstance(field_value, list):
        if op == "eq":
            return field_value == value
        return value in field_value  # in / contains
    if op == "eq":
        return field_value == value
    if op == "in":
        return field_value in value
    return value in str(field_value)  # contains 子串


def _name_to_family_key(source: DataSource) -> dict[str, str]:
    """精灵名 → family_key 反查表（`family` 过滤 value 可传精灵名）。"""
    families = load_families(source)
    return {name: key for key, names in families.items() for name in names}


def _resolve_family_value(value, name_to_key: dict[str, str]):
    """family 过滤的 value 归一：精灵名 → family_key，其余（已是 key）原样。"""
    if isinstance(value, str):
        return name_to_key.get(value, value)
    if isinstance(value, list):
        return [name_to_key.get(v, v) for v in value]
    return value


def _spirit_matches(sp: RawSpirit, group: list[SpiritFilter], source: DataSource,
                    name_to_key: dict[str, str]) -> bool:
    """内层 AND：group 全部命中才算。空组不命中（避免「空 AND = 全真」灌出整表）。"""
    if not group:
        return False
    for f in group:
        fv = _field_value(sp, f.field, source)
        value = _resolve_family_value(f.value, name_to_key) if f.field == "family" else f.value
        if not _match(fv, f.op, value):
            return False
    return True


def _compact(sp: RawSpirit, source: DataSource) -> dict:
    """命中精灵的精简档案（不灌整表进上下文）。"""
    return {
        "name": sp.name,
        "types": list(sp.types),
        "trait_name": sp.trait_name,
        "is_boss": sp.is_boss,
        "family_key": sp.family_key,
        "number": sp.number,
        "learnable_skill_count": len(learnable_skills(sp.name, "", source)),
    }


def get_catalog_version() -> dict:
    """当前数据/规则版本的只读摘要（M2 的 VersionGate、M3 回答引用的唯一锚点）。"""
    return {
        "data_digest": data_digest(),
        "rules_digest": rules_digest(),
        "spirit_count": len(load_spirits(DataSource.FULL)),
        "skill_count": len(load_skills(DataSource.FULL)),
        "valid_skill_count": len(load_skills(DataSource.VALID)),
        "families_count": len(load_families(DataSource.VALID)),
    }


def search_spirits(filters: list[list[SpiritFilter]], *,
                   source: DataSource = DataSource.VALID, limit: int = 20) -> list[dict]:
    """白名单 DSL 检索精灵。析取范式：外层 OR、内层 AND。

    空 `filters` 或空内层组 → 不命中（避免「空 AND = 全真」灌出整表）。命中按 `name` 排序、
    `limit` 截断。任何 field/op 越界、value 类型不符、注入字样 → `QueryNotAllowed`。
    """
    if not isinstance(limit, int) or limit < 1:
        raise QueryNotAllowed("limit 必须为正整数")
    if not isinstance(filters, (list, tuple)):
        raise QueryNotAllowed("filters 必须是 list[list[SpiritFilter]]")

    groups: list[list[SpiritFilter]] = []
    for group in filters:
        if not isinstance(group, (list, tuple)):
            raise QueryNotAllowed("filters 每项必须是 list[SpiritFilter]")
        compiled: list[SpiritFilter] = []
        for f in group:
            if not isinstance(f, SpiritFilter):
                raise QueryNotAllowed("filters 原子必须是 SpiritFilter")
            _validate_filter(f)
            compiled.append(f)
        groups.append(compiled)

    need_family = any(f.field == "family" for g in groups for f in g)
    name_to_key = _name_to_family_key(source) if need_family else {}

    hits = []
    for sp in load_spirits(source).values():
        if any(_spirit_matches(sp, g, source, name_to_key) for g in groups):
            hits.append(_compact(sp, source))
    hits.sort(key=lambda d: d["name"])
    return hits[:limit]


def get_spirit_profile(name: str, *, source: DataSource = DataSource.VALID) -> dict | None:
    """单只精灵完整档案：RawSpirit 全字段 + 可学池（无血脉）+ 合法血脉(18 系)。未找到 → None。"""
    sp = load_spirits(source).get(name)
    if sp is None:
        return None
    return {
        "name": sp.name,
        "types": list(sp.types),
        "trait_name": sp.trait_name,
        "trait_desc": sp.trait_desc,
        "stats": dict(sp.stats),
        "skills_default": list(sp.skills_default),
        "skills_bloodline": list(sp.skills_bloodline),
        "bloodlines": list(sp.bloodlines),
        "skills_stone": list(sp.skills_stone),
        "skills_legend": list(sp.skills_legend),
        "is_boss": sp.is_boss,
        "family_key": sp.family_key,
        "family_lowest": sp.family_lowest,
        "number": sp.number,
        "region": sp.region,
        "learnable_skills": learnable_skills(name, "", source),
        "legal_bloodlines": sorted(load_types(source)),
    }


def get_skill_profile(name: str, *, source: DataSource = DataSource.VALID) -> dict | None:
    """单条技能档案。`implemented` = 是否在 VALID 白名单（已实装效果）。未找到 → None。"""
    sk = load_skills(source).get(name)
    if sk is None:
        return None
    return {
        "name": sk.name,
        "type": sk.type,
        "kind": sk.kind,
        "power": sk.power,
        "energy_cost": sk.energy_cost,
        "desc": sk.desc,
        "implemented": name in load_skills(DataSource.VALID),
    }


def get_build_options(name: str, bloodline: str = "", *,
                      source: DataSource = DataSource.VALID) -> dict | None:
    """合法构筑项：可学池（含所选血脉）+ 全部合法性格 + IV 范围/维度上限。未找到 → None。"""
    if name not in load_spirits(source):
        return None
    return {
        "learnable_skills": learnable_skills(name, bloodline, source),
        "valid_natures": [NEUTRAL_NATURE] + list(NATURE_BONUS),
        "iv_max": DEFAULT_RULES.iv_max,
        # iv_dims 与 teambuilder.validate_team 里「最多 3 个维度」硬编码一致（无 rules 字段）。
        "iv_dims": 3,
    }
