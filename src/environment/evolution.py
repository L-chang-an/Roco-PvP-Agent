"""进化链索引（2026-08-30）：萌化退化 / 首领化的查表口。

数据源 = `data/evolution_chains.json`（scripts/build_evolution_chains.py 从
full_spirits.json 的 evolution 字段去重生成）。纯查表、零状态、不 import engine。

已拍板口径（2026-08-30 负责人）：
- **萌化** = 沿链往低一阶退种族值资质（x 层 = 退 x 阶；实际资质已最低阶则拦截；
  解除 x 层沿链回升；特性不变、名字不变）；
- **首领化** = 一阶进化，**只有 boss 的上一阶**可触发（57 个精灵）；多分支只有
  迪莫（4）/魔力猫（2），其余单分支与地区形态一一对应；门控另需「萌化层数 == 0」
  （资质已退化则不可首领化，由 engine 检查）；
- 退化目标唯一性 = 数据不变量（链共享低阶前缀、末端才分叉）——索引构建时断言。
"""

from __future__ import annotations

from functools import lru_cache

from .dataset import DEFAULT_SOURCE, DataSource, load_evolution_chains, load_spirits


@lru_cache(maxsize=4)
def _index(source: DataSource) -> tuple[dict, frozenset, dict]:
    """三张索引（按 source 缓存）：
    - prev: name → 退化目标（唯一；冲突 = 数据异常，抛错）；
    - lowest: 链最低阶集合（萌化施加拦截依据）；
    - boss_targets: boss 上一阶 → 首领化分支元组（不在 = 不可首领化）。
    """
    prev: dict[str, str] = {}
    lowest: set[str] = set()
    boss_targets: dict[str, list[str]] = {}
    for c in load_evolution_chains(source):
        path = c["path"]
        for i, name in enumerate(path):
            if i > 0:
                if name in prev and prev[name] != path[i - 1]:
                    raise ValueError(f"进化链数据异常：{name} 的退化目标不唯一")
                prev[name] = path[i - 1]
            else:
                lowest.add(name)
        if c["boss"]:
            boss_targets.setdefault(path[-2], []).append(c["boss"])
    return prev, frozenset(lowest), {k: tuple(v) for k, v in boss_targets.items()}


def prev_of(name: str, source: DataSource = DEFAULT_SOURCE) -> str | None:
    """退化目标（低一阶形态名）；链最低阶 → None。"""
    return _index(source)[0].get(name)


def prev_name_of(name: str, steps: int, source: DataSource = DEFAULT_SOURCE) -> str:
    """沿链往低 steps 阶的形态名（越界夹到最低阶）。"""
    cur = name
    for _ in range(steps):
        nxt = prev_of(cur, source)
        if nxt is None:
            break
        cur = nxt
    return cur


def is_lowest(name: str, source: DataSource = DEFAULT_SOURCE) -> bool:
    """是否链最低阶（萌化施加拦截依据）。"""
    return name in _index(source)[1]


def boss_targets_of(name: str, source: DataSource = DEFAULT_SOURCE) -> tuple[str, ...]:
    """首领化分支（**仅 boss 的上一阶**非空；空 = 不可首领化 / 无首领血脉）。"""
    return _index(source)[2].get(name, ())


def base_stats_of(name: str, source: DataSource = DEFAULT_SOURCE) -> dict[str, int]:
    """某形态的种族值（英文 key、int）——萌化退化 / 首领化替换读它。

    full_spirits.json 的 stats 字段即种族值（teambuilder 同一口径）。
    """
    sp = load_spirits(source).get(name)
    if sp is None:
        raise KeyError(f"进化链引用的形态不在精灵表：{name}")
    return dict(sp.stats)
