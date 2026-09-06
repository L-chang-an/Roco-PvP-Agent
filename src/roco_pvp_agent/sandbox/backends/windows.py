"""Native Windows LPAC + read-only DACLs + atomic Job containment.

The only trusted runtime is a prepared, verified bundle. Failure never selects a
plain subprocess or a weaker token. No user-controlled switches affect isolation.
"""

from __future__ import annotations

import ctypes as C
import json
import math
import os
import platform
import socket
import tempfile
import threading
import time
import uuid
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path

from ..models import (SandboxBackendName, SandboxExecutionControl, SandboxExecutionRequest,
                      SandboxExecutionResult, SandboxHealth, SandboxLimits)
from ..runtime import RuntimeUnavailable, minimal_environment
from .base import SandboxBackend, limits_payload


@dataclass(frozen=True)
class _ProcessOutput:
    stdout: bytes = b""
    exit_status: int | None = None
    reason: str | None = None


class WindowsSandboxBackend(SandboxBackend):
    name = SandboxBackendName.WINDOWS

    def __init__(self, runtime_python: Path, *, configured_backend: str = "windows"):
        self._python = Path(runtime_python)
        self._manifest = None
        self._capability_sid = None
        reason = None
        architecture = platform.machine().lower()
        if platform.system() != "Windows":
            reason = "platform_not_windows"
        elif architecture not in {"amd64", "x86_64"} or C.sizeof(C.c_void_p) != 8:
            reason = "architecture_unsupported"
        else:
            try:
                from ..windows_runtime import verify_runtime
                from .win32 import api
                self._manifest = verify_runtime(self._python)
                self._capability_sid = api().capability_sid(self._manifest["capability_name"])
                if not self._probe():
                    reason = "windows_isolation_probe_failed"
            except RuntimeUnavailable as exc:
                reason = exc.reason_code
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                reason = "windows_backend_probe_failed"
        self._health = SandboxHealth(True, configured_backend, self.name.value,
                                     reason is None, reason, architecture)

    @property
    def health(self):
        return self._health

    def execute(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        if not self.health.healthy:
            return SandboxExecutionResult(False, "sandbox_backend_unavailable",
                                          resource_reason=self.health.reason_code)
        return self._execute(request)

    def _execute(self, request: SandboxExecutionRequest, *, probe_script: str | None = None):
        from .win32 import api, open_verified_file, reject_reparse_path
        started = time.monotonic()
        deadline = started + request.limits.wall_seconds
        if request.control.deadline is not None:
            deadline = min(deadline, request.control.deadline)
        try:
            if request.control.cancel_event.is_set() or time.monotonic() >= deadline:
                return self._failure("sandbox_timeout", started, "cancelled_or_deadline")
            # New SID per request, no profile, registry registration or writable AC/Temp.
            package = api().package_sid("roco.query." + uuid.uuid4().hex)
            with tempfile.TemporaryDirectory(prefix="roco-windows-") as raw:
                root = Path(raw).absolute()
                reject_reparse_path(root)
                api().set_acl(root, package, execute=True)
                data = root / "data"
                data.mkdir()
                api().set_acl(data, package, execute=True)
                mapped = {}
                # Only server-side dataset IDs become names. Do not copy arbitrary
                # caller path names or authorize source directories.
                from ..catalog import DATASET_FILES
                if set(request.dataset_paths) != set(request.dataset_ids):
                    raise ValueError("dataset_mapping_mismatch")
                for dataset_id in request.dataset_ids:
                    if dataset_id not in DATASET_FILES:
                        raise ValueError("dataset_not_registered")
                    target = data / f"{dataset_id}.json"
                    with open_verified_file(request.dataset_paths[dataset_id]) as reader, target.open("xb") as writer:
                        while block := reader.read(1024 * 1024):
                            writer.write(block)
                    api().set_acl(target, package)
                    mapped[dataset_id] = str(target)
                script = self._python.parent / "runner.py"
                if probe_script is not None:
                    # Private trusted health/test entry point, never a tool argument.
                    script = root / "probe.py"
                    script.write_text(probe_script, encoding="utf-8")
                    api().set_acl(script, package)
                payload = {"code": request.code, "dataset_ids": list(request.dataset_ids),
                           "dataset_paths": mapped, "limits": limits_payload(request, enforce_nproc=False),
                           "runtime_site_packages": str(self._python.parent / "Lib" / "site-packages")}
                raw_input = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                if len(raw_input) >= 32 * 1024:
                    return self._failure("sandbox_output_invalid", started, "input_envelope_limit")
                argv = [str(self._python), "-I", "-S", "-B", "-X", "utf8", str(script)]
                result = self._supervise(argv, minimal_environment(root), root, package,
                                         raw_input, request, deadline)
                if result.reason:
                    code = ("sandbox_timeout" if result.reason in {"wall_time", "cancelled"}
                            else "sandbox_output_too_large" if result.reason == "raw_output"
                            else "sandbox_resource_limit" if result.reason in {"cpu", "memory"}
                            else "sandbox_backend_unavailable")
                    return self._failure(code, started, result.reason, result.exit_status)
                return self._decode(result, started, request.limits)
        except (OSError, ValueError, TypeError):
            if hasattr(self, "_health"):
                self._health = replace(self._health, healthy=False,
                                       reason_code="windows_execution_setup_failed")
            return self._failure("sandbox_backend_unavailable", started, "windows_execution_setup_failed")

    def _supervise(self, argv, env, root, package, raw_input, request, deadline):
        from .win32 import api
        win = api()
        output, errors = bytearray(), bytearray()
        reason = None
        with ExitStack() as stack:
            desktop = stack.enter_context(win.desktop("roco-" + uuid.uuid4().hex, package))
            job = stack.enter_context(win.create_job(request.limits))
            pipes = []
            for parent_reads in (False, True, True):
                reader, writer = win.pipe(parent_reads=parent_reads)
                stack.enter_context(reader)
                stack.enter_context(writer)
                pipes.append((reader, writer))
            stdin, stdout, stderr = pipes
            process, primary_thread, pid = win.spawn(
                argv, env, root, package, self._capability_sid, job,
                (stdin[0].value, stdout[1].value, stderr[1].value), desktop=desktop)
            stack.enter_context(process)
            stack.enter_context(primary_thread)
            writer_thread = None
            try:
                # Parent must not retain copies that prevent EOF/broken-pipe.
                stdin[0].close()
                stdout[1].close()
                stderr[1].close()
                win.verify_process(process.value, job.value, package)
                if request.control.cancel_event.is_set() or time.monotonic() >= deadline:
                    return _ProcessOutput(reason="cancelled")
                if win.ResumeThread(primary_thread.value) == 0xFFFFFFFF:
                    win.check(False, "resume_process")

                def send_input():
                    try:
                        win.write(stdin[1].value, raw_input)
                    except OSError:
                        pass
                    finally:
                        stdin[1].close()

                writer_thread = threading.Thread(target=send_input, name=f"sandbox-input-{pid}")
                writer_thread.start()
                while True:
                    for pipe, destination in ((stdout[0], output), (stderr[0], errors)):
                        while block := win.read_available(pipe.value):
                            if len(destination) + len(block) > request.limits.raw_output_bytes:
                                reason = "raw_output"
                                break
                            destination.extend(block)
                        if reason:
                            break
                    if reason:
                        break
                    # Windows' Job time-limit enforcement can be coarser than
                    # this query's deadline. Also enforce the exact budget from
                    # kernel accounting, while retaining the OS Job limit.
                    if win.accounting(job.value).user_time >= request.limits.cpu_seconds * 10_000_000:
                        reason = "cpu"
                        break
                    if request.control.cancel_event.is_set():
                        reason = "cancelled"
                        break
                    if time.monotonic() >= deadline:
                        reason = "wall_time"
                        break
                    status = win.WaitForSingleObject(process.value, 0)
                    if status == 0:
                        # All writes complete when the sole worker exits. Drain
                        # once more in the next pass to include the tail bytes.
                        for pipe, destination in ((stdout[0], output), (stderr[0], errors)):
                            while block := win.read_available(pipe.value):
                                if len(destination) + len(block) > request.limits.raw_output_bytes:
                                    reason = "raw_output"
                                    break
                                destination.extend(block)
                        break
                    win.check(status == 258, "wait_process")
                    time.sleep(0.01)
            finally:
                # This covers normal exit, early cancellation, exceptions during
                # token validation, and a blocked stdin writer. Last Job close
                # independently covers host death, including suspended startup.
                win.check(win.TerminateJobObject(job.value, 1), "terminate_job")
                win.check(win.WaitForSingleObject(process.value, 1000) == 0, "worker_cleanup")
                if writer_thread:
                    writer_thread.join(timeout=1)
                    if writer_thread.is_alive():
                        raise OSError("stdin_cleanup_failed")
            exit_status = win.exit_code(process.value)
            accounting = win.accounting(job.value)
            if not reason and accounting.user_time >= request.limits.cpu_seconds * 10_000_000:
                reason = "cpu"
            if not reason and exit_status in {0xC0000017, 0xC000012D}:
                reason = "memory"
            return _ProcessOutput(bytes(output), exit_status, reason)

    @staticmethod
    def _failure(code, started, reason=None, exit_status=None):
        return SandboxExecutionResult(False, code, duration_ms=round((time.monotonic() - started) * 1000, 3),
                                      resource_reason=reason, exit_status=exit_status)

    @classmethod
    def _decode(cls, output, started, limits=None):
        limits = limits or SandboxLimits()
        try:
            envelope = json.loads(output.stdout.decode("utf-8"),
                                  parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            if not isinstance(envelope, dict) or type(envelope.get("ok")) is not bool:
                raise ValueError("invalid_envelope")
        except (UnicodeDecodeError, ValueError, TypeError, RecursionError):
            return cls._failure("sandbox_violation" if output.exit_status else "sandbox_output_invalid",
                                started, exit_status=output.exit_status)
        if not envelope["ok"]:
            code = envelope.get("error_code")
            if not isinstance(code, str) or code not in {"sandbox_execution_error", "sandbox_output_invalid",
                            "sandbox_output_too_large", "sandbox_resource_limit"}:
                code = "sandbox_violation"
            resource = envelope.get("resource_reason")
            # Untrusted strings never flow into audit records.
            resource = resource if isinstance(resource, str) and resource in {"memory", "cpu"} else None
            return cls._failure(code, started, resource, output.exit_status)
        if output.exit_status != 0:
            return cls._failure("sandbox_violation", started, exit_status=output.exit_status)
        # The worker is untrusted even if it forges the runner's envelope.
        def valid(value, depth=0):
            if depth > limits.max_depth:
                return False
            if isinstance(value, float):
                return math.isfinite(value)
            if isinstance(value, (dict, list)):
                children = value.values() if isinstance(value, dict) else value
                return len(value) <= limits.max_records and all(valid(item, depth + 1) for item in children)
            return True

        if ("result" not in envelope or type(envelope.get("truncated", False)) is not bool
                or not valid(envelope["result"])):
            return cls._failure("sandbox_output_invalid", started, exit_status=output.exit_status)
        if len(json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))) > limits.model_output_chars:
            return cls._failure("sandbox_output_too_large", started, exit_status=output.exit_status)
        return SandboxExecutionResult(True, result=envelope.get("result"),
                                      duration_ms=round((time.monotonic() - started) * 1000, 3),
                                      truncated=bool(envelope.get("truncated")), exit_status=0)

    def _probe(self):
        """Positive dependency/query control plus direct, real OS denial checks."""
        import winreg
        with tempfile.TemporaryDirectory(prefix="roco-windows-probe-") as raw, ExitStack() as stack:
            root = Path(raw)
            data = root / "full_spirits.json"
            secret = root / "host-secret.txt"
            data.write_text('[{"name":"迪莫"}]', encoding="utf-8")
            secret.write_text(uuid.uuid4().hex, encoding="utf-8")
            registry_key = "Software\\RocoSandboxProbe" + uuid.uuid4().hex
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, registry_key) as key:
                winreg.SetValueEx(key, "canary", 0, winreg.REG_SZ, "host-only")
            stack.callback(winreg.DeleteKey, winreg.HKEY_CURRENT_USER, registry_key)
            request = SandboxExecutionRequest(
                uuid.uuid4().hex, "emit_result({'name':pd.DataFrame(load_dataset('full_spirits')).iloc[0]['name'],'sum':int(np.array([1,2]).sum())})",
                ("full_spirits",), {"full_spirits": data}, "probe", SandboxLimits(),
                SandboxExecutionControl(None, threading.Event()))
            query = self._execute(request)
            if not query.ok or query.result != {"name": "迪莫", "sum": 3}:
                return False
            endpoints = []
            for family, address in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
                for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
                    listener = stack.enter_context(socket.socket(family, kind))
                    listener.bind((address, 0))
                    if kind == socket.SOCK_STREAM:
                        listener.listen(4)
                    endpoints.append((int(family), int(kind), address, listener.getsockname()[1]))
            script = "\n".join([
                "import ctypes as C, json, os, socket, subprocess, sys, winreg",
                "payload=json.load(sys.stdin)",
                "checks=[]",
                "def denied(fn):",
                "    try: fn()",
                "    except OSError as e:",
                "        code=getattr(e,'winerror',None)",
                "        return code in (5,367,10013,1260,1816) or (code is None and e.errno==13)",
                "    return False",
                f"checks.append(denied(lambda: open({str(secret)!r}, 'rb')))",
                "checks.append(denied(lambda: open(os.path.join(os.getcwd(),'write-denied'),'wb')))",
                "checks.append(denied(lambda: open(payload['dataset_paths']['full_spirits'],'wb')))",
                "checks.append(denied(lambda: subprocess.run([sys.executable,'-c','pass'])))",
                f"checks.append(denied(lambda: winreg.OpenKey(winreg.HKEY_CURRENT_USER,{registry_key!r},0,winreg.KEY_READ)))",
                f"checks.append(denied(lambda: winreg.OpenKey(winreg.HKEY_CURRENT_USER,{registry_key!r},0,winreg.KEY_SET_VALUE)))",
                "def connect(family,kind,address,port):",
                "    with socket.socket(family,kind) as s:",
                "        s.settimeout(0.2)",
                "        return s.connect((address,port)) if kind==1 else s.sendto(b'probe',(address,port))",
                f"for endpoint in {endpoints!r}:",
                "    checks.append(denied(lambda: connect(*endpoint)))",
                "dns=C.WinDLL('dnsapi',use_last_error=True)",
                "dns.DnsQuery_W.restype=C.c_uint32",
                "dns.DnsQuery_W.argtypes=[C.c_wchar_p,C.c_uint16,C.c_uint32,C.c_void_p,C.c_void_p,C.c_void_p]",
                "records=C.c_void_p()",
                f"status=dns.DnsQuery_W({'roco-' + uuid.uuid4().hex + '.invalid'!r},1,0x108,None,C.byref(records),None)",
                # Identical valid calls are positively controlled in conformance.
                # DNS APIs report EINVAL when unavailable to a profile-less LPAC.
                "checks.append(status in (5,87,10013))",
                "ui=C.WinDLL('user32',use_last_error=True)",
                "ui.OpenDesktopW.restype=C.c_void_p",
                "ui.OpenDesktopW.argtypes=[C.c_wchar_p,C.c_uint32,C.c_int,C.c_uint32]",
                "desktop=ui.OpenDesktopW('Default',0,False,0x101)",
                "checks.append(not desktop and C.get_last_error()==5)",
                "if desktop:",
                "    ui.CloseDesktop.argtypes=[C.c_void_p]",
                "    ui.CloseDesktop(desktop)",
                "ui.OpenClipboard.argtypes=[C.c_void_p]",
                "opened=ui.OpenClipboard(None)",
                "checks.append(not opened and C.get_last_error()==5)",
                "if opened: ui.CloseClipboard()",
                "print(json.dumps({'ok':True,'result':checks}))",
            ])
            security = self._execute(request, probe_script=script)
            return security.ok and security.result == [True] * 13


__all__ = ["WindowsSandboxBackend"]
