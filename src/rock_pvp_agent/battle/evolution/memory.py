"""R1 记忆库与两阶段检索（MemRL 底座）：MemoryEntry / MemoryStore / KeywordEmbedder /
`two_phase_search` / `update_q`。

§6.3 的部署期记忆：三元组 (意图 z, 经验 e, 效用 Q)。两阶段检索：
- **Phase A 相似度门**：`sim ≥ δ` 且**硬过滤**（命数档/能量档/data_digest 必须匹配，
  跨版本记忆不可信）→ top-`k1`；
- **Phase B 效用重排**：`score = (1−λ)·z(sim) + λ·z(sigmoid(Q))` → top-`k2`
  （MemRL：z-score 标准化是关键——去掉它 Forgetting Rate 0.041→0.073）。

R1 只做**沉淀 + 检索 + 更新 Q**，**不改变决策**（R3 才接线注入 LLMPlayer）。
`KeywordEmbedder` 是零外部依赖兜底（决策 8）：situation_key 结构化字段相似度 + 硬过滤，
确定性、离线可测；`Embedder` Protocol 可插拔真实嵌入。

持久化复用 `TrajectoryStore` 的原子写模式（同目录临时文件 + fsync + os.replace），
`entries.jsonl` 追加式、坏行容错。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

# 检索默认参数（§6.3 / §十四，MemRL 原文校准）。
DEFAULT_DELTA = 0.5
DEFAULT_K1 = 10
DEFAULT_LAM = 0.5
DEFAULT_K2 = 3
DEFAULT_ALPHA = 0.3

_ENTRIES_FILE = "entries.jsonl"

# MemoryEntry 必需的顶层键（add 前校验）。
_ENTRY_REQUIRED = ("entry_id", "side", "lineage_family", "situation_key",
                   "situation_text", "experience_text", "action", "Q",
                   "n_used", "n_adopted", "provenance")


def sigmoid(x: float) -> float:
    """`1/(1+e^−x)`（Q → [0,1]，Phase B 效用项）。**数值稳定**：正侧算 e^−x、
    负侧算 e^x——绝不计算 e^{|x|>0} 的大指数（float64 上限 ~709.8），任意 x 不溢出。"""
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _zscore(values: list[float]) -> list[float]:
    """z-score 标准化：`(x − mean)/std`；std=0（全相等）→ 全 0（不放大噪声）。

    MemRL 关键：去掉它检索退化成纯相似度。确定性。
    """
    if not values:
        return []
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    std = var ** 0.5
    if std < 1e-12:
        return [0.0] * len(values)
    return [(v - mean) / std for v in values]


def make_entry_id(side: str, situation_key: str, action: dict, *, scope: str = "") -> str:
    """确定性 entry_id：`mem_` + sha256(side|situation_key|action|scope) 前 16 位（64 bit）。

    - 同 (side, situation_key, action, scope) 恒得同一 id → `MemoryStore.add` 幂等去重；
    - **16 位 hex（64 bit）**：存储按「只增不删」增长（§6.3），8 位 hex（32 bit）在
      ~1 万条时已有 ~1% 生日碰撞，add 会静默丢记忆、update_q 会串 Q——必须留足安全边际；
    - `scope` 是数据指纹（data_digest）：**跨版本（规则/数据源变化）同局面同行动不合并**
      （§6.3「跨版本记忆不可信」——不同版本进不同 id，Q 不混淆）。
    """
    payload = json.dumps({"side": side, "situation_key": situation_key, "action": action,
                          "scope": scope},
                         sort_keys=True, ensure_ascii=False)
    return "mem_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class MemoryQuery:
    """检索查询：当前局面的 situation_key + 侧锁 + 数据隔离键。"""

    situation_key: str
    side: str = ""
    data_digest: str = ""


class Embedder:
    """嵌入器协议（决策 8：可插拔真实嵌入，默认零依赖 KeywordEmbedder）。"""

    def similarity(self, query_key: str, entry_key: str) -> float:
        """两条 situation_key 的相似度 ∈ [0,1]（越大越相似）。"""
        raise NotImplementedError

    def hard_match(self, query: MemoryQuery, entry: dict) -> bool:
        """硬过滤：命数档/能量档/data_digest/侧锁必须匹配（跨版本记忆不可信）。"""
        raise NotImplementedError


class KeywordEmbedder(Embedder):
    """零依赖兜底嵌入：situation_key 结构化字段相似度 + 硬过滤。

    sim = 除「在场精灵名」（太特异）外 8 个字段的匹配比例——命数/能量档/已揭示数/
    阶段/后备数。硬过滤 = 命数两字段 + 能量档两字段精确一致 + data_digest/侧锁一致。
    """

    # situation_key 分段（§6.3）：0-1 命数, 2-3 在场精灵名, 4-5 能量档,
    # 6 对手已揭示技能数, 7 阶段, 8-9 存活后备数。参与相似度的 = 除名字外全部。
    _SIM_FIELDS: tuple[int, ...] = (0, 1, 4, 5, 6, 7, 8, 9)
    # 硬过滤：命数 + 能量档必须精确一致（其他字段只参与相似度）。
    _HARD_FIELDS: tuple[int, ...] = (0, 1, 4, 5)

    def similarity(self, query_key: str, entry_key: str) -> float:
        q = query_key.split("/")
        e = entry_key.split("/")
        if len(q) != 10 or len(e) != 10:
            return 0.0
        hits = sum(1 for i in self._SIM_FIELDS if q[i] == e[i])
        return hits / len(self._SIM_FIELDS)

    def hard_match(self, query: MemoryQuery, entry: dict) -> bool:
        q = query.situation_key.split("/")
        e = entry.get("situation_key", "").split("/")
        if len(q) != 10 or len(e) != 10:
            return False
        for i in self._HARD_FIELDS:
            if q[i] != e[i]:
                return False
        if query.side and entry.get("side") and query.side != entry["side"]:
            return False
        prov = entry.get("provenance") or {}
        if query.data_digest and prov.get("data_digest") \
                and query.data_digest != prov["data_digest"]:
            return False
        return True


def two_phase_search(store, query: MemoryQuery, *, delta: float = DEFAULT_DELTA,
                     k1: int = DEFAULT_K1, lam: float = DEFAULT_LAM,
                     k2: int = DEFAULT_K2, embedder: Embedder | None = None) -> list[dict]:
    """两阶段检索（§6.3）→ 返回 top-`k2` 的 MemoryEntry 列表（按 Phase B 分数降序）。

    - Phase A：`hard_match` + `sim ≥ δ` → 按 sim 取 top-`k1`；
    - Phase B：`score = (1−λ)·z(sim) + λ·z(sigmoid(Q))` → top-`k2`。
    确定性（同 store + 同 query 恒同结果）；空候选 → 空列表。
    """
    embedder = embedder or KeywordEmbedder()
    candidates: list[tuple[float, dict]] = []
    for entry in store.all():
        if not embedder.hard_match(query, entry):
            continue
        sim = embedder.similarity(query.situation_key, entry["situation_key"])
        if sim < delta:
            continue
        candidates.append((sim, entry))
    candidates.sort(key=lambda x: -x[0])
    candidates = candidates[:k1]

    sims = [c[0] for c in candidates]
    qs = [sigmoid(c[1].get("Q", 0.0)) for c in candidates]
    z_sims, z_qs = _zscore(sims), _zscore(qs)
    scored = [((1 - lam) * zs + lam * zq, entry)
              for zs, zq, (_, entry) in zip(z_sims, z_qs, candidates)]
    scored.sort(key=lambda x: -x[0])
    return [entry for _, entry in scored[:k2]]


def update_q(store, entry_id: str, reward: float, *, alpha: float = DEFAULT_ALPHA) -> float:
    """Q 的 EMA 更新：`Q ← Q + α·(r − Q)`（§6.3；α=0.3 原文校准）。返回新 Q。

    条目不存在 → KeyError（宁失败不抛，防把脏更新写进库）。
    """
    entry = store.get(entry_id)
    if entry is None:
        raise KeyError(f"记忆条目不存在：{entry_id}")
    new_q = entry["Q"] + alpha * (reward - entry["Q"])
    store.update(entry_id, {"Q": round(new_q, 4)})
    return round(new_q, 4)


class MemoryStore:
    """记忆库读写口：`entries.jsonl` 追加式 + 原子写（复用 TrajectoryStore 模式）。

    单用户本地工具，串行写；`update` 用「读全量 → 改 → 原子重写」保证一致性。
    """

    def __init__(self, dir_path: str | Path) -> None:
        self._dir = Path(dir_path)

    @property
    def dir(self) -> Path:
        return self._dir

    @property
    def entries_path(self) -> Path:
        return self._dir / _ENTRIES_FILE

    # ── 写 ──
    def add(self, entry: dict) -> str:
        """追加一条记忆；`entry_id` 已存在 → 跳过（幂等，重跑提取不重复）。"""
        missing = [k for k in _ENTRY_REQUIRED if k not in entry]
        if missing:
            raise ValueError(f"记忆条目缺少必需键：{missing}。")
        eid = entry["entry_id"]
        if self.get(eid) is not None:
            return eid
        self._dir.mkdir(parents=True, exist_ok=True)
        self._atomic_write(self.entries_path, json.dumps(entry, ensure_ascii=False),
                           append=True)
        return eid

    def update(self, entry_id: str, fields: dict) -> bool:
        """按 entry_id 更新字段（Q 等），原子重写。不存在 → False。"""
        entries = self.all()
        target = next((e for e in entries if e["entry_id"] == entry_id), None)
        if target is None:
            return False
        target.update(fields)
        self._write_all(entries)
        return True

    # ── 读 ──
    def get(self, entry_id: str) -> dict | None:
        return next((e for e in self.all() if e["entry_id"] == entry_id), None)

    def all(self) -> list[dict]:
        """全部条目（按写入顺序）；坏行（非 JSON / 缺 entry_id）跳过——坏行容错。"""
        if not self.entries_path.exists():
            return []
        out: list[dict] = []
        try:
            lines = self.entries_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return []
        for line in lines:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and "entry_id" in item:
                out.append(item)
        return out

    def count(self) -> int:
        return len(self.all())

    # ── 内部 ──
    def _write_all(self, entries: list[dict]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        text = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries)
        self._atomic_write(self.entries_path, text)

    @staticmethod
    def _atomic_write(target: Path, text: str, *, append: bool = False) -> None:
        """同目录临时文件 + fsync + `os.replace`（原子替换）。追加模式先拷入现有内容。"""
        existing = ""
        if append and target.exists():
            existing = target.read_text(encoding="utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".mem-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                if existing:
                    f.write(existing)
                    if not existing.endswith("\n"):
                        f.write("\n")
                f.write(text)
                if append:
                    f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
