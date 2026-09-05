"""延迟工具的会话级发现与加载状态。

ToolRegistry 描述“系统拥有哪些工具”；ToolVisibility 只描述“一次聊天会话已经向
模型展示过哪些延迟工具 schema”。两者生命周期必须分开，避免一个会话的搜索结果
泄漏到其他会话。
"""

from __future__ import annotations

import re
import threading
from copy import deepcopy
from dataclasses import dataclass
from typing import Iterable, Mapping

from .models import ToolEntry, ToolExposure
from .registry import ToolRegistry


@dataclass(frozen=True)
class ToolMatch:
    """一次稳定的延迟工具搜索命中。"""

    name: str
    score: int
    schema: Mapping[str, object]
    cache_hit: bool

    def to_payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "score": self.score,
            "load_state": "cached" if self.cache_hit else "loaded",
            "schema": deepcopy(dict(self.schema)),
        }


@dataclass(frozen=True)
class ToolSearchResult:
    """工具发现的可观察结果；未命中是正常数据，不抛异常。"""

    matches: tuple[ToolMatch, ...] = ()
    missing: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return bool(self.matches)

    def to_payload(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "matches": [match.to_payload() for match in self.matches],
            "missing": list(self.missing),
        }


class ToolVisibility:
    """一个聊天会话的延迟 schema 缓存。

    即时工具始终可见；延迟工具只有经 ``load_by_name``/``search`` 命中后才进入
    ``visible_names``。缓存只保存从同一 ToolRegistry 生成的防御性 schema 快照。
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._loaded_schemas: dict[str, dict[str, object]] = {}
        self._lock = threading.Lock()

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def loaded_names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._loaded_schemas)

    def visible_names(self) -> frozenset[str]:
        immediate = {entry.name for entry in self._registry.immediate_entries()}
        with self._lock:
            return frozenset(immediate | self._loaded_schemas.keys())

    def load_by_name(self, tool_names: Iterable[str]) -> ToolSearchResult:
        """按调用者顺序加载精确名称，同一次请求内折叠重复项。"""

        matches: list[ToolMatch] = []
        missing: list[str] = []
        for name in dict.fromkeys(tool_names):
            entry = self._registry.get(name)
            if entry is None or entry.exposure is not ToolExposure.DEFERRED:
                missing.append(name)
                continue
            matches.append(self._load_entry(entry, score=100))
        return ToolSearchResult(matches=tuple(matches), missing=tuple(missing))

    def search(self, queries: Iterable[str], *, top_k: int = 3) -> ToolSearchResult:
        """确定性关键词检索：分数降序，名称升序打破平分。"""

        if top_k < 1:
            raise ValueError("top_k 必须大于等于 1")
        normalized = tuple(query.strip().lower() for query in queries if query.strip())
        if not normalized:
            return ToolSearchResult(missing=("<empty query>",))

        ranked: list[tuple[int, str, ToolEntry]] = []
        for entry in self._registry.deferred_entries():
            score = _match_score(entry, normalized)
            if score > 0:
                ranked.append((score, entry.name, entry))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        if not ranked:
            return ToolSearchResult(missing=normalized)
        matches = tuple(
            self._load_entry(entry, score)
            for score, _name, entry in ranked[:top_k]
        )
        return ToolSearchResult(matches=matches)

    def directory_prompt(self) -> str:
        """渲染启动目录；只含名称和一句话用途，不包含完整 schema。"""

        with self._lock:
            loaded = frozenset(self._loaded_schemas)
        rows = [
            row for row in self._registry.deferred_directory()
            if row["name"] not in loaded
        ]
        if not rows:
            return ""
        lines = "\n".join(
            f"- {row['name']}: {row['description']}"
            for row in rows
        )
        return (
            "【延迟工具目录】\n"
            "以下工具当前只有名称和用途，尚不可直接调用。需要时先调用 "
            "tool_search，按 tool_names 精确加载或按 queries 检索；看到完整 schema 后，"
            "在下一轮直接调用已加载工具。\n"
            f"{lines}"
        )

    def _load_entry(self, entry: ToolEntry, score: int) -> ToolMatch:
        with self._lock:
            cache_hit = entry.name in self._loaded_schemas
            if not cache_hit:
                record = self._registry.model_schema_records({entry.name})[0]
                record.pop("exposure", None)
                self._loaded_schemas[entry.name] = record
            schema = deepcopy(self._loaded_schemas[entry.name])
        return ToolMatch(
            name=entry.name,
            score=score,
            schema=schema,
            cache_hit=cache_hit,
        )


def _tokens(text: str) -> tuple[str, ...]:
    """同时保留英文 token、连续中文短语与中文单字，支持中英文目录检索。"""

    chunks = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", text.lower().replace("_", " "))
    tokens: list[str] = []
    for chunk in chunks:
        tokens.append(chunk)
        if re.fullmatch(r"[\u4e00-\u9fff]+", chunk) and len(chunk) > 1:
            tokens.extend(chunk)
    return tuple(dict.fromkeys(tokens))


def _match_score(entry: ToolEntry, queries: tuple[str, ...]) -> int:
    """精确名称最高，名称 token 次之，目录描述命中再次之。"""

    name = entry.name.lower()
    name_tokens = set(_tokens(name))
    description = entry.directory_description.lower()
    score = 0
    for query in queries:
        normalized_name = re.sub(r"\s+", "_", query)
        if query == name or normalized_name == name:
            score += 100
        if query in name and query != name:
            score += 30
        if query in description:
            score += 15
        for term in _tokens(query):
            if term in name_tokens:
                score += 20
            elif term in name:
                score += 10
            if term in description:
                score += 3
    return score


__all__ = ["ToolMatch", "ToolSearchResult", "ToolVisibility"]
