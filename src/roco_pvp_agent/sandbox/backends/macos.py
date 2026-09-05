"""macOS Seatbelt 后端。"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import ctypes
from pathlib import Path

from ..models import (
    SandboxBackendName,
    SandboxExecutionRequest,
    SandboxExecutionResult,
    SandboxHealth,
)
from ..runtime import RuntimeInfo, RuntimeUnavailable, inspect_runtime, minimal_environment
from .base import ProcessSandboxBackend, limits_payload, runner_path


class _RUsageInfoV2(ctypes.Structure):
    _fields_ = [
        ("uuid", ctypes.c_uint8 * 16),
        ("user_time", ctypes.c_uint64),
        ("system_time", ctypes.c_uint64),
        ("pkg_idle_wkups", ctypes.c_uint64),
        ("interrupt_wkups", ctypes.c_uint64),
        ("pageins", ctypes.c_uint64),
        ("wired_size", ctypes.c_uint64),
        ("resident_size", ctypes.c_uint64),
        ("phys_footprint", ctypes.c_uint64),
        ("proc_start_abstime", ctypes.c_uint64),
        ("proc_exit_abstime", ctypes.c_uint64),
        ("child_user_time", ctypes.c_uint64),
        ("child_system_time", ctypes.c_uint64),
        ("child_pkg_idle_wkups", ctypes.c_uint64),
        ("child_interrupt_wkups", ctypes.c_uint64),
        ("child_pageins", ctypes.c_uint64),
        ("child_elapsed_abstime", ctypes.c_uint64),
        ("diskio_bytesread", ctypes.c_uint64),
        ("diskio_byteswritten", ctypes.c_uint64),
    ]


def _resident_bytes(pid: int) -> int | None:
    try:
        library = ctypes.CDLL("/usr/lib/libproc.dylib")
        function = library.proc_pid_rusage
        function.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        function.restype = ctypes.c_int
        info = _RUsageInfoV2()
        if function(pid, 2, ctypes.byref(info)) != 0:
            return None
        return int(info.phys_footprint or info.resident_size)
    except (OSError, AttributeError):
        return None


def _seatbelt_literal(path: Path) -> str:
    value = str(path)
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError("invalid_seatbelt_path")
    return json.dumps(value, ensure_ascii=False)


class MacOSSandboxBackend(ProcessSandboxBackend):
    name = SandboxBackendName.MACOS

    def __init__(self, runtime_python: Path, *, configured_backend: str = "macos") -> None:
        self._sandbox_exec = Path("/usr/bin/sandbox-exec")
        self._runtime: RuntimeInfo | None = None
        architecture = platform.machine().lower()
        reason: str | None = None
        if platform.system() != "Darwin":
            reason = "platform_not_macos"
        elif architecture not in {"arm64", "x86_64"}:
            reason = "architecture_unsupported"
        elif not self._sandbox_exec.is_file():
            reason = "sandbox_exec_missing"
        elif _resident_bytes(os.getpid()) is None:
            reason = "memory_monitor_unavailable"
        else:
            try:
                self._runtime = inspect_runtime(runtime_python)
            except RuntimeUnavailable as exc:
                reason = exc.reason_code
        if reason is None and not self._probe():
            reason = "seatbelt_probe_failed"
        self._health = SandboxHealth(
            enabled=True,
            configured_backend=configured_backend,
            active_backend=self.name.value,
            healthy=reason is None,
            reason_code=reason,
            architecture=architecture,
        )

    @property
    def health(self) -> SandboxHealth:
        return self._health

    def execute(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        if not self.health.healthy or self._runtime is None:
            return SandboxExecutionResult(
                ok=False, error_code="sandbox_backend_unavailable",
                resource_reason=self.health.reason_code,
            )
        run_root = next(iter(request.dataset_paths.values())).parent.parent
        work = run_root / "work"
        work.mkdir(mode=0o700, exist_ok=False)
        profile = run_root / "seatbelt.sb"
        profile.write_text(self._profile(request, work), encoding="utf-8")
        profile.chmod(0o400)
        payload = {
            "code": request.code,
            "dataset_ids": list(request.dataset_ids),
            "dataset_paths": {key: str(value) for key, value in request.dataset_paths.items()},
            "limits": limits_payload(request, enforce_nproc=False),
        }
        argv = [
            str(self._sandbox_exec), "-f", str(profile),
            str(self._runtime.executable), "-I", str(runner_path()),
        ]
        return self._run_process(
            argv, payload=payload, request=request,
            env=minimal_environment(work), cwd=work,
            memory_probe=_resident_bytes,
        )

    def _profile(self, request: SandboxExecutionRequest, work: Path) -> str:
        assert self._runtime is not None
        reads = {
            self._runtime.prefix,
            self._runtime.base_prefix,
            runner_path(),
            Path("/System/Library"),
            Path("/System/Volumes/Preboot/Cryptexes/OS/System/Library"),
            Path("/System/Volumes/Preboot/Cryptexes/OS/usr/lib"),
            Path("/Library/Apple/System/Library"),
            Path("/usr/lib"),
            Path("/usr/share/locale"),
            Path("/usr/share/zoneinfo"),
            Path("/private/var/db/dyld"),
            Path("/private/var/db/timezone"),
            Path("/dev/null"),
            Path("/dev/random"),
            Path("/dev/urandom"),
            *request.dataset_paths.values(),
        }
        for executable in self._execution_paths():
            reads.add(executable)
            reads.add(executable.parent)
        rules = [
            "(version 1)",
            "(deny default)",
            "(deny network*)",
            # dyld/execvp 会读取根目录项本身；literal 不会授权任何子目录内容。
            "(allow file-read* (literal \"/\"))",
            "(allow signal (target self))",
            "(allow sysctl-read)",
        ]
        for executable in self._execution_paths():
            rules.append(
                f"(allow process-exec* (literal {_seatbelt_literal(executable)}))")
        for path in sorted(reads, key=lambda item: str(item)):
            operation = "subpath" if path.is_dir() else "literal"
            rules.append(f"(allow file-read* ({operation} {_seatbelt_literal(path)}))")
        metadata_paths = {
            parent
            for readable in reads
            for parent in readable.parents
            if str(parent) != "/"
        }
        for path in sorted(metadata_paths, key=lambda item: str(item)):
            rules.append(
                f"(allow file-read-metadata (literal {_seatbelt_literal(path)}))")
        rules.extend([
            f"(allow file-read-metadata (subpath {_seatbelt_literal(work.parent)}))",
            f"(allow file-read* (subpath {_seatbelt_literal(work)}))",
            f"(allow file-write* (subpath {_seatbelt_literal(work)}))",
        ])
        return "\n".join(rules) + "\n"

    def _execution_paths(self) -> tuple[Path, ...]:
        assert self._runtime is not None
        paths = {self._runtime.executable, self._runtime.executable_resolved}
        try:
            target = os.readlink(self._runtime.executable)
            linked = Path(target) if os.path.isabs(target) \
                else self._runtime.executable.parent / target
            paths.add(linked.absolute())
        except OSError:
            pass
        return tuple(sorted(paths, key=lambda item: str(item)))

    def _probe(self) -> bool:
        if self._runtime is None:
            return False
        # 探针不使用用户代码，只验证 Seatbelt profile 能启动锁定运行时。
        import tempfile

        try:
            with tempfile.TemporaryDirectory(prefix="roco-seatbelt-probe-") as raw:
                root = Path(raw)
                work = root / "work"
                work.mkdir()
                fake = type("Probe", (), {"dataset_paths": {}})()
                profile = root / "probe.sb"
                profile.write_text(self._profile(fake, work), encoding="utf-8")
                completed = subprocess.run(
                    [str(self._sandbox_exec), "-f", str(profile),
                     str(self._runtime.executable), "-I", "-c", "import numpy,pandas"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    cwd=work,
                    env=minimal_environment(work),
                    timeout=8,
                    check=False,
                )
                return completed.returncode == 0
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return False


__all__ = ["MacOSSandboxBackend"]
