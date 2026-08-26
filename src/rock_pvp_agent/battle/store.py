"""自博弈轨迹存储（E6）：**只存** `(组队配置, seed, 逐回合提交序列 + state_hash)`，不存整份状态。

为什么只存提交序列：整份状态的体积会随后续里程碑涨一个数量级，而 `(配置, seed, 提交序列)` +
逐回合 hash 已经足以复现与校验（引擎是 `(state, 双方提交) → state'` 的纯转移，玩家 RNG 流与
引擎 RNG 流分离）。记录格式与 `environment.replay.replay_record` 的消费方完全对应。

持久化：每局一个 JSON 文件（同目录临时文件 + fsync + `os.replace` 原子写）+ 追加式
`index.jsonl`（坏行容错，读时跳过）。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

INDEX_NAME = "index.jsonl"

# 记录必需键（save 前校验，防把残缺记录写进磁盘）。
_RECORD_REQUIRED = ("version", "battle_id", "saved_at", "seed", "players", "rules",
                    "team_a", "team_b", "winner", "done", "turns")


class TrajectoryStore:
    """轨迹目录的读写口。单用户本地工具：不做并发锁（自博弈 CLI 串行写）。"""

    def __init__(self, out_dir: str | Path) -> None:
        self._dir = Path(out_dir)

    @property
    def dir(self) -> Path:
        """轨迹目录（懒创建，save 时 mkdir）。"""
        return self._dir

    # ── 写 ──
    def save(self, record: dict) -> Path:
        """落盘一条轨迹记录（原子写 JSON + 追加 index 行），返回记录文件路径。"""
        missing = [k for k in _RECORD_REQUIRED if k not in record]
        if missing:
            raise ValueError(f"轨迹记录缺少必需键：{missing}。")
        bid = record["battle_id"]
        if "/" in bid or "\\" in bid or bid in (".", "..") or ".." in bid.split("/"):
            raise ValueError(f"battle_id 非法（可能路径穿越）：{bid!r}。")
        self._dir.mkdir(parents=True, exist_ok=True)
        target = self._dir / f"{bid}.json"
        self._atomic_write(target, record)
        self.append_index(self._meta(record, target.name))
        return target

    def append_index(self, meta: dict) -> None:
        """追加一行索引元数据到 index.jsonl（原子写：临时文件 + os.replace）。"""
        self._dir.mkdir(parents=True, exist_ok=True)
        self._atomic_write(
            self._dir / INDEX_NAME,
            text=json.dumps(meta, ensure_ascii=False),
            append=True,
        )

    # ── 读 ──
    def index(self) -> list[dict]:
        """读全部索引行（按文件顺序）；坏行（非 JSON / 非 dict）跳过——坏行容错。"""
        path = self._dir / INDEX_NAME
        if not path.exists():
            return []
        out: list[dict] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return []
        for line in lines:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                out.append(item)
        return out

    # ── 内部 ──
    @staticmethod
    def _meta(record: dict, name: str) -> dict:
        """记录 → index 行元数据（轻量，供列表页不读整局）。"""
        return {
            "battle_id": record["battle_id"],
            "path": name,
            "saved_at": record["saved_at"],
            "seed": record["seed"],
            "players": record["players"],
            "winner": record["winner"],
            "done": record["done"],
            "turn_count": len(record["turns"]),
        }

    @staticmethod
    def _atomic_write(target: Path, payload: dict | None = None, *,
                      text: str | None = None, append: bool = False) -> None:
        """同目录临时文件写入 + fsync + `os.replace`（原子替换）。追加模式下先拷入现有内容。"""
        payload_str = text if text is not None else json.dumps(payload, ensure_ascii=False, indent=2)
        existing = ""
        if append and target.exists():
            existing = target.read_text(encoding="utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".trace-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                if existing:
                    f.write(existing)
                    if not existing.endswith("\n"):
                        f.write("\n")
                f.write(payload_str)
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
