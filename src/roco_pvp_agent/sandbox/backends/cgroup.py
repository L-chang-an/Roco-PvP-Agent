"""可选的 cgroup v2 请求作用域；无 delegation 时由严格 rlimit 回退。"""

from __future__ import annotations

import time
from pathlib import Path

from ..models import SandboxLimits


class CgroupV2Scope:
    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def try_create(
        cls,
        run_id: str,
        limits: SandboxLimits,
        *,
        cgroup_root: Path = Path("/sys/fs/cgroup"),
        membership_file: Path = Path("/proc/self/cgroup"),
    ) -> "CgroupV2Scope | None":
        path: Path | None = None
        try:
            membership = membership_file.read_text(encoding="utf-8")
            unified = next(
                line.split("::", 1)[1].strip()
                for line in membership.splitlines()
                if line.startswith("0::")
            )
            root = cgroup_root.resolve(strict=True)
            parent = (root / unified.lstrip("/")).resolve(strict=True)
            if parent != root and root not in parent.parents:
                return None
            path = parent / f"roco-sandbox-{run_id}"
            path.mkdir(mode=0o700, exist_ok=False)
            required = ("memory.max", "pids.max", "cpu.max", "cgroup.procs")
            if any(not (path / name).exists() for name in required):
                raise OSError("cgroup controllers not delegated")
            (path / "memory.max").write_text(str(limits.memory_bytes), encoding="ascii")
            swap = path / "memory.swap.max"
            if swap.exists():
                swap.write_text("0", encoding="ascii")
            (path / "pids.max").write_text(str(limits.max_pids), encoding="ascii")
            period = 100_000
            share = max(0.01, min(1.0, limits.cpu_seconds / limits.wall_seconds))
            (path / "cpu.max").write_text(
                f"{max(1_000, int(period * share))} {period}", encoding="ascii")
            oom_group = path / "memory.oom.group"
            if oom_group.exists():
                oom_group.write_text("1", encoding="ascii")
            return cls(path)
        except (OSError, StopIteration, ValueError):
            if path is not None:
                try:
                    path.rmdir()
                except OSError:
                    pass
            return None

    def attach(self, pid: int) -> None:
        (self.path / "cgroup.procs").write_text(str(pid), encoding="ascii")

    def resource_reason(self) -> str | None:
        if self._event_count("memory.events", "oom_kill") > 0:
            return "memory"
        if self._event_count("pids.events", "max") > 0:
            return "pids"
        return None

    def close(self) -> None:
        try:
            procs = (self.path / "cgroup.procs").read_text(encoding="ascii").split()
        except OSError:
            procs = []
        if procs:
            killer = self.path / "cgroup.kill"
            if killer.exists():
                try:
                    killer.write_text("1", encoding="ascii")
                except OSError:
                    pass
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                try:
                    if not (self.path / "cgroup.procs").read_text(
                            encoding="ascii").strip():
                        break
                except OSError:
                    break
                time.sleep(0.02)
        try:
            self.path.rmdir()
        except OSError:
            pass

    def _event_count(self, filename: str, key: str) -> int:
        try:
            for line in (self.path / filename).read_text(encoding="ascii").splitlines():
                name, value = line.split(maxsplit=1)
                if name == key:
                    return int(value)
        except (OSError, ValueError):
            pass
        return 0


__all__ = ["CgroupV2Scope"]
