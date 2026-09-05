"""Linux / WSL2 bubblewrap + libseccomp 后端。"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import platform
import shutil
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path, PurePosixPath

from ..models import (
    SandboxBackendName,
    SandboxExecutionRequest,
    SandboxExecutionResult,
    SandboxHealth,
)
from ..runtime import RuntimeInfo, RuntimeUnavailable, inspect_runtime, minimal_environment
from .base import ProcessSandboxBackend, limits_payload, runner_path
from .cgroup import CgroupV2Scope


_DENIED_SYSCALLS = (
    "socket", "socketpair", "connect", "bind", "listen", "accept", "accept4",
    "mount", "umount2", "pivot_root", "setns", "unshare", "ptrace", "keyctl",
    "add_key", "request_key", "bpf", "perf_event_open", "open_by_handle_at",
    "name_to_handle_at", "kexec_load", "kexec_file_load", "init_module",
    "finit_module", "delete_module", "userfaultfd", "io_uring_setup",
    "io_uring_enter", "io_uring_register", "fork", "vfork", "clone3",
)


class _SeccompFilter:
    def __init__(self) -> None:
        library_name = ctypes.util.find_library("seccomp")
        if not library_name:
            raise OSError("libseccomp_missing")
        self._library = ctypes.CDLL(library_name, use_errno=True)
        self._library.seccomp_init.argtypes = [ctypes.c_uint32]
        self._library.seccomp_init.restype = ctypes.c_void_p
        self._library.seccomp_release.argtypes = [ctypes.c_void_p]
        self._library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
        self._library.seccomp_syscall_resolve_name.restype = ctypes.c_int
        self._library.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._library.seccomp_export_bpf.restype = ctypes.c_int
        self._library.seccomp_rule_add.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint,
        ]
        self._library.seccomp_rule_add.restype = ctypes.c_int
        self._context = self._library.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
        if not self._context:
            raise OSError("seccomp_init_failed")
        action_errno = 0x00050000 | errno.EPERM
        for name in _DENIED_SYSCALLS:
            syscall = self._library.seccomp_syscall_resolve_name(name.encode("ascii"))
            if syscall < 0:  # 规则按当前架构解析；不存在的 syscall 不硬编码编号。
                continue
            result = self._library.seccomp_rule_add(
                self._context, action_errno, syscall, 0,
            )
            if result != 0:
                self.close()
                raise OSError("seccomp_rule_failed")
        self.file = tempfile.TemporaryFile()
        if self._library.seccomp_export_bpf(self._context, self.file.fileno()) != 0:
            self.close()
            raise OSError("seccomp_export_failed")
        self.file.flush()
        os.lseek(self.file.fileno(), 0, os.SEEK_SET)

    @property
    def fd(self) -> int:
        return self.file.fileno()

    def close(self) -> None:
        context = getattr(self, "_context", None)
        if context:
            self._library.seccomp_release(context)
            self._context = None
        file = getattr(self, "file", None)
        if file:
            file.close()

    def __enter__(self) -> "_SeccompFilter":
        return self

    def __exit__(self, *_args) -> None:
        self.close()


class LinuxSandboxBackend(ProcessSandboxBackend):
    name = SandboxBackendName.LINUX

    def __init__(self, runtime_python: Path, *, configured_backend: str = "linux") -> None:
        self._bwrap = Path(shutil.which("bwrap") or "/nonexistent/bwrap")
        self._runtime: RuntimeInfo | None = None
        architecture = platform.machine().lower()
        reason: str | None = None
        if platform.system() != "Linux":
            reason = "platform_not_linux"
        elif architecture not in {"x86_64", "aarch64", "arm64"}:
            reason = "architecture_unsupported"
        elif self._is_wsl1():
            reason = "wsl1_unsupported"
        elif not self._bwrap.is_file():
            reason = "bubblewrap_missing"
        elif not ctypes.util.find_library("seccomp"):
            reason = "libseccomp_missing"
        else:
            try:
                self._runtime = inspect_runtime(runtime_python)
                self._runtime_layout()
            except RuntimeUnavailable as exc:
                reason = exc.reason_code
            except ValueError:
                reason = "runtime_layout_invalid"
        if reason is None and not self._probe():
            reason = "linux_sandbox_probe_failed"
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
        cgroup = CgroupV2Scope.try_create(request.run_id, request.limits)
        try:
            with _SeccompFilter() as seccomp:
                argv, paths = self._argv(request.dataset_paths, seccomp.fd)
                payload = {
                    "code": request.code,
                    "dataset_ids": list(request.dataset_ids),
                    "dataset_paths": paths,
                    "runtime_site_packages": self._sandbox_site_packages(),
                    "limits": limits_payload(request, enforce_nproc=True),
                }
                result = self._run_process(
                    argv,
                    payload=payload,
                    request=request,
                    env=minimal_environment(run_root),
                    cwd=run_root,
                    pass_fds=(seccomp.fd,),
                    on_spawn=cgroup.attach if cgroup is not None else None,
                )
                if cgroup is not None:
                    resource_reason = cgroup.resource_reason()
                    if resource_reason and not result.ok:
                        result = replace(
                            result,
                            error_code="sandbox_resource_limit",
                            resource_reason=resource_reason,
                        )
                return result
        except (OSError, ValueError, AttributeError):
            return SandboxExecutionResult(
                ok=False, error_code="sandbox_backend_unavailable",
                resource_reason="linux_sandbox_setup_failed",
            )
        finally:
            if cgroup is not None:
                cgroup.close()

    def _runtime_layout(self) -> tuple[Path, Path]:
        assert self._runtime is not None
        try:
            executable_relative = self._runtime.executable_resolved.relative_to(
                self._runtime.base_prefix)
            site_relative = self._runtime.purelib.relative_to(self._runtime.prefix)
        except ValueError:
            raise ValueError("runtime paths must be contained") from None
        return executable_relative, site_relative

    def _sandbox_site_packages(self) -> str:
        _, site_relative = self._runtime_layout()
        return str(PurePosixPath("/runtime") / PurePosixPath(site_relative.as_posix()))

    def _argv(
        self,
        dataset_paths: dict[str, Path] | object,
        seccomp_fd: int,
    ) -> tuple[list[str], dict[str, str]]:
        assert self._runtime is not None
        executable_relative, _ = self._runtime_layout()
        argv = [
            str(self._bwrap),
            "--die-with-parent", "--new-session", "--unshare-all",
            "--disable-userns",
            "--uid", "65534", "--gid", "65534", "--cap-drop", "ALL",
            "--clearenv",
            "--ro-bind", str(self._runtime.prefix), "/runtime",
            "--ro-bind", str(self._runtime.base_prefix), "/runtime-base",
            "--ro-bind", str(runner_path()), "/runner.py",
            # 仅提供 ELF loader/glibc/locale 所需的库目录，不挂载 /usr/bin 或宿主根。
            "--ro-bind-try", "/lib", "/lib",
            "--ro-bind-try", "/lib64", "/lib64",
            "--ro-bind-try", "/usr/lib", "/usr/lib",
            "--ro-bind-try", "/usr/lib64", "/usr/lib64",
            "--dir", "/etc",
            "--ro-bind-try", "/etc/ld.so.cache", "/etc/ld.so.cache",
            "--ro-bind-try", "/etc/localtime", "/etc/localtime",
            "--tmpfs", "/work", "--tmpfs", "/tmp",
            "--proc", "/proc", "--dev", "/dev", "--dir", "/data",
        ]
        mapped: dict[str, str] = {}
        for dataset_id, host_path in dict(dataset_paths).items():
            target = f"/data/{dataset_id}.json"
            argv.extend(["--ro-bind", str(host_path), target])
            mapped[dataset_id] = target
        argv.extend([
            "--chdir", "/work",
            "--setenv", "LANG", "C.UTF-8",
            "--setenv", "LC_ALL", "C.UTF-8",
            "--setenv", "HOME", "/work",
            "--setenv", "TMPDIR", "/tmp",
            "--setenv", "PYTHONNOUSERSITE", "1",
            "--setenv", "PYTHONHASHSEED", "0",
            "--setenv", "OMP_NUM_THREADS", "1",
            "--setenv", "OPENBLAS_NUM_THREADS", "1",
            "--setenv", "MKL_NUM_THREADS", "1",
            "--setenv", "NUMEXPR_NUM_THREADS", "1",
            "--seccomp", str(seccomp_fd),
            str(PurePosixPath("/runtime-base") / PurePosixPath(executable_relative.as_posix())),
            "-I", "/runner.py",
        ])
        return argv, mapped

    def _probe(self) -> bool:
        if self._runtime is None:
            return False
        try:
            with tempfile.TemporaryDirectory(prefix="roco-bwrap-probe-") as raw:
                root = Path(raw)
                with _SeccompFilter() as seccomp:
                    argv, _ = self._argv({}, seccomp.fd)
                    # 替换 runner 命令为最小启动探针；依赖版本已由 inspect_runtime 检查。
                    argv[-2:] = ["-I", "-c", "pass"]
                    completed = subprocess.run(
                        argv,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        cwd=root,
                        env=minimal_environment(root),
                        pass_fds=(seccomp.fd,),
                        timeout=8,
                        check=False,
                    )
                    return completed.returncode == 0
        except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
            return False

    @staticmethod
    def _is_wsl1() -> bool:
        try:
            release = platform.release().lower()
            version = Path("/proc/version").read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            return False
        is_wsl = "microsoft" in release or "microsoft" in version
        if not is_wsl:
            return False
        return "wsl2" not in release and "microsoft-standard" not in release


__all__ = ["LinuxSandboxBackend"]
