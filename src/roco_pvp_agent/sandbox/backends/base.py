"""沙箱后端公共进程监督器。"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO, Callable, Mapping, Sequence

from ..models import (
    SandboxBackendName,
    SandboxExecutionRequest,
    SandboxExecutionResult,
    SandboxHealth,
)


class SandboxBackend(ABC):
    name: SandboxBackendName

    @property
    @abstractmethod
    def health(self) -> SandboxHealth:
        raise NotImplementedError

    @abstractmethod
    def execute(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        raise NotImplementedError


class ProcessSandboxBackend(SandboxBackend):
    """只接受服务端生成 argv 的进程后端；从不启用 shell。"""

    def _run_process(
        self,
        argv: Sequence[str],
        *,
        payload: Mapping,
        request: SandboxExecutionRequest,
        env: Mapping[str, str],
        cwd: Path,
        pass_fds: tuple[int, ...] = (),
        memory_probe: Callable[[int], int | None] | None = None,
        on_spawn: Callable[[int], None] | None = None,
    ) -> SandboxExecutionResult:
        started = time.perf_counter()
        raw_input = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        try:
            process = subprocess.Popen(
                list(argv),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                env=dict(env),
                shell=False,
                start_new_session=True,
                pass_fds=pass_fds,
            )
        except OSError:
            return self._failure(
                "sandbox_backend_unavailable", started, resource_reason="spawn_failed")

        if on_spawn is not None:
            stopped = False
            try:
                os.kill(process.pid, signal.SIGSTOP)
                stopped = True
                on_spawn(process.pid)
            except (OSError, ProcessLookupError):
                # cgroup attach 失败时 runner 的严格 rlimit 仍会先于用户代码生效。
                pass
            finally:
                if stopped:
                    try:
                        os.kill(process.pid, signal.SIGCONT)
                    except ProcessLookupError:
                        pass

        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        overflow = threading.Event()
        stdout = bytearray()
        stderr = bytearray()
        readers = [
            threading.Thread(
                target=self._bounded_read,
                args=(process.stdout, stdout, request.limits.raw_output_bytes, overflow),
                daemon=True,
            ),
            threading.Thread(
                target=self._bounded_read,
                args=(process.stderr, stderr, request.limits.raw_output_bytes, overflow),
                daemon=True,
            ),
        ]
        for reader in readers:
            reader.start()
        try:
            process.stdin.write(raw_input)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass

        deadline = time.monotonic() + request.limits.wall_seconds
        if request.control.deadline is not None:
            deadline = min(deadline, request.control.deadline)
        timed_out = False
        cancelled = False
        memory_exceeded = False
        while process.poll() is None:
            if overflow.is_set():
                self._kill_process_group(process)
                break
            if request.control.cancel_event.is_set():
                cancelled = True
                self._kill_process_group(process)
                break
            if memory_probe is not None:
                resident = memory_probe(process.pid)
                if resident is not None and resident > request.limits.memory_bytes:
                    memory_exceeded = True
                    self._kill_process_group(process)
                    break
            if time.monotonic() >= deadline:
                timed_out = True
                self._kill_process_group(process)
                break
            time.sleep(0.02)
        try:
            return_code = process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            self._kill_process_group(process)
            return_code = process.wait(timeout=1.0)
        for reader in readers:
            reader.join(timeout=0.2)

        if cancelled or timed_out:
            return self._failure(
                "sandbox_timeout", started, exit_status=return_code,
                resource_reason="cancelled" if cancelled else "wall_time",
            )
        if memory_exceeded:
            return self._failure(
                "sandbox_resource_limit", started, exit_status=return_code,
                resource_reason="memory",
            )
        if overflow.is_set():
            return self._failure(
                "sandbox_output_too_large", started, exit_status=return_code,
                resource_reason="raw_output",
            )
        resource_signals = {
            signal.SIGXCPU: "cpu",
            signal.SIGKILL: "memory_or_process",
            signal.SIGSEGV: "memory",
            signal.SIGXFSZ: "temp_space",
        }
        if return_code < 0 and -return_code in resource_signals:
            return self._failure(
                "sandbox_resource_limit", started, exit_status=return_code,
                resource_reason=resource_signals[-return_code],
            )
        try:
            envelope = json.loads(bytes(stdout).decode("utf-8"))
            if not isinstance(envelope, dict) or not isinstance(envelope.get("ok"), bool):
                raise ValueError
        except (UnicodeDecodeError, ValueError, TypeError):
            return self._failure(
                "sandbox_violation" if return_code else "sandbox_output_invalid",
                started,
                exit_status=return_code,
            )
        if not envelope["ok"]:
            code = envelope.get("error_code")
            allowed = {
                "sandbox_execution_error", "sandbox_output_invalid",
                "sandbox_output_too_large", "sandbox_resource_limit",
            }
            if code not in allowed:
                code = "sandbox_violation"
            return self._failure(
                code,
                started,
                exit_status=return_code,
                resource_reason=envelope.get("resource_reason"),
            )
        return SandboxExecutionResult(
            ok=True,
            result=envelope.get("result"),
            duration_ms=self._elapsed(started),
            truncated=bool(envelope.get("truncated")),
            exit_status=return_code,
        )

    @staticmethod
    def _bounded_read(
        stream: BinaryIO,
        destination: bytearray,
        limit: int,
        overflow: threading.Event,
    ) -> None:
        while True:
            try:
                chunk = stream.read(4096)
            except OSError:
                return
            if not chunk:
                return
            if len(destination) + len(chunk) > limit:
                overflow.set()
                return
            destination.extend(chunk)

    @staticmethod
    def _kill_process_group(process: subprocess.Popen) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                process.kill()
            except ProcessLookupError:
                pass

    @staticmethod
    def _failure(
        code: str,
        started: float,
        *,
        exit_status: int | None = None,
        resource_reason: str | None = None,
    ) -> SandboxExecutionResult:
        return SandboxExecutionResult(
            ok=False,
            error_code=code,
            duration_ms=ProcessSandboxBackend._elapsed(started),
            exit_status=exit_status,
            resource_reason=resource_reason,
        )

    @staticmethod
    def _elapsed(started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 3)


def runner_path() -> Path:
    return Path(__file__).resolve().parent.parent / "runtime_runner.py"


def limits_payload(request: SandboxExecutionRequest, *, enforce_nproc: bool) -> dict[str, object]:
    limits = request.limits
    return {
        "cpu_seconds": limits.cpu_seconds,
        "memory_bytes": limits.memory_bytes,
        "temp_bytes": limits.temp_bytes,
        "raw_output_bytes": limits.raw_output_bytes,
        "model_output_chars": limits.model_output_chars,
        "max_records": limits.max_records,
        "max_depth": limits.max_depth,
        "max_pids": limits.max_pids,
        "enforce_nproc": enforce_nproc,
        "strict_limits": True,
    }


__all__ = ["ProcessSandboxBackend", "SandboxBackend", "limits_payload", "runner_path"]
