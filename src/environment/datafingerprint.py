"""数据指纹：当前引擎/数据版本的确定性摘要（M/R 线共享的 VersionGate 锚点）。

语义口径（负责人 2026-08-30 拍板）：hash **加载后的归一化数据**（RawSpirit/RawSkill 全字段，
排除纯展示字段 `desc`/`trait_desc`）+ rules + families + evolution_chains。覆盖 E 线新增的
families / evolution_chains / 精灵技能池 / is_boss / family 等字段——比 main 分支旧版
`_data_digest`（仅技能/精灵**名字列表**）更强。

三条不变式（测试钉住）：
1. 确定性：同数据 → 同 digest（同进程/跨进程）；
2. 敏感性：任何「引擎行为相关」字段变化 → digest 变；
3. 精确性：纯展示字段（desc/trait_desc）变化 → digest 不变。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, fields

from .dataset import (
    DataSource,
    load_evolution_chains,
    load_families,
    load_skills,
    load_spirits,
)
from .rules import DEFAULT_RULES

# 纯展示字段：引擎永不解析（RawSkill.desc / RawSpirit.trait_desc），排除以保「精确性」。
_SKIP_FIELDS = frozenset({"desc", "trait_desc"})


def _rules_payload() -> dict:
    """BattleRules 全部字段按声明序 → dict（值即引擎读到的数值）。"""
    return {f.name: getattr(DEFAULT_RULES, f.name) for f in fields(DEFAULT_RULES)}


def _entity_payload(entity) -> dict:
    """dataclass → 稳定 dict：排除展示字段；元组交给 json 序列化（→ 列表）。"""
    out = asdict(entity)
    for key in _SKIP_FIELDS:
        out.pop(key, None)
    return out


def _sorted_entities(entities) -> list[dict]:
    """按 name 排序的实体 payload 列表（确定性，不依赖加载/JSON 顺序）。"""
    return [_entity_payload(e) for e in sorted(entities, key=lambda e: e.name)]


def rules_digest() -> str:
    """规则版本指纹（BattleRules 的稳定序列化 hash）。"""
    raw = json.dumps(_rules_payload(), sort_keys=True, ensure_ascii=False)
    return "rules_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def data_digest() -> str:
    """数据源指纹：rules + VALID 精灵/技能归一化字段 + families + evolution_chains。

    VALID 口径（对局/记忆只关心已实装技能）；families/evolution_chains 是 E 线新增、
    引擎读得到的数据，纳入以保证家族/进化链变化同样触发指纹变化。
    """
    payload = {
        "rules": _rules_payload(),
        "skills": _sorted_entities(load_skills(DataSource.VALID).values()),
        "spirits": _sorted_entities(load_spirits(DataSource.VALID).values()),
        "families": {k: sorted(v) for k, v in load_families(DataSource.VALID).items()},
        "evolution_chains": list(load_evolution_chains(DataSource.VALID)),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return "d_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
