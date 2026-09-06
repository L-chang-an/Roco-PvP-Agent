"""服务端数据集注册表：模型只能提交稳定 ID，永远不能提交路径。"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import BinaryIO, Mapping

from environment.dataset import DATA_DIR


DATASET_FILES: Mapping[str, str] = MappingProxyType({
    "full_spirits": "full_spirits.json",
    "full_skills": "full_skills.json",
    "valid_skills": "valid_skills.json",
    "type_chart": "type_chart.json",
    "families": "families.json",
    "evolution_chains": "evolution_chains.json",
    "p1_skills": "p1_skills.json",
    "p2_skills": "p2_skills.json",
})


class DatasetCatalogError(ValueError):
    pass


@dataclass(frozen=True)
class DatasetSnapshot:
    paths: Mapping[str, Path]
    digest: str


class DatasetCatalog:
    def __init__(self, data_root: Path | None = None) -> None:
        root = Path(data_root) if data_root is not None else DATA_DIR
        if os.name == "nt":
            from .backends.win32 import reject_reparse_path
            try:
                reject_reparse_path(root)
            except ValueError as exc:
                raise DatasetCatalogError(str(exc)) from None
        self._root = root.resolve(strict=True)

    @property
    def root(self) -> Path:
        return self._root

    def resolve(self, dataset_ids: tuple[str, ...]) -> dict[str, Path]:
        resolved: dict[str, Path] = {}
        for dataset_id in sorted(dataset_ids):
            filename = DATASET_FILES.get(dataset_id)
            if filename is None:
                raise DatasetCatalogError("unknown_dataset")
            candidate = self._root / filename
            # 原始目录内的软链接也拒绝；不能借 resolve 合法化外部目标。
            if candidate.is_symlink():
                raise DatasetCatalogError("dataset_symlink_denied")
            # Keep the original Windows path until open_verified_file has
            # checked every component and pinned it with a handle. Resolving
            # here could erase a non-symlink reparse point before admission.
            path = candidate.absolute() if os.name == "nt" else candidate.resolve(strict=True)
            if path.parent != self._root or not path.is_file():
                raise DatasetCatalogError("dataset_path_invalid")
            resolved[dataset_id] = path
        return resolved

    def snapshot_into(self, dataset_ids: tuple[str, ...], destination: Path) -> DatasetSnapshot:
        """用 O_NOFOLLOW 打开后复制，避免解析与执行之间被软链接替换。"""

        destination.mkdir(mode=0o700, parents=True, exist_ok=False)
        paths = self.resolve(dataset_ids)
        digest = hashlib.sha256()
        output: dict[str, Path] = {}
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        for dataset_id in sorted(dataset_ids):
            source = paths[dataset_id]
            if os.name == "nt":
                from .backends.win32 import open_verified_file
                target = destination / f"{dataset_id}.json"
                try:
                    with open_verified_file(source) as reader, target.open("xb") as writer:
                        self._copy_and_hash(reader, writer, digest, dataset_id)
                except ValueError as exc:
                    raise DatasetCatalogError(str(exc)) from None
                # Windows isolation is a DACL applied by the backend. DOS readonly
                # attributes are neither a security boundary nor needed for cleanup.
                output[dataset_id] = target
                continue
            fd = os.open(source, os.O_RDONLY | nofollow)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode):
                    raise DatasetCatalogError("dataset_not_regular")
                target = destination / f"{dataset_id}.json"
                with os.fdopen(os.dup(fd), "rb") as reader, target.open("xb") as writer:
                    self._copy_and_hash(reader, writer, digest, dataset_id)
                target.chmod(0o400)
                output[dataset_id] = target
            finally:
                os.close(fd)
        return DatasetSnapshot(paths=MappingProxyType(output), digest=digest.hexdigest()[:24])

    @staticmethod
    def _copy_and_hash(reader: BinaryIO, writer: BinaryIO, digest, dataset_id: str) -> None:
        digest.update(dataset_id.encode("utf-8") + b"\0")
        while True:
            chunk = reader.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            writer.write(chunk)


__all__ = ["DATASET_FILES", "DatasetCatalog", "DatasetCatalogError", "DatasetSnapshot"]
