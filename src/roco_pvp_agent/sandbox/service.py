"""权限决策、数据快照、后端执行、结果协议与脱敏审计的统一编排。"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from roco_pvp_agent.tooling import ToolOutcome

from .backends import DockerSandboxBackend, LinuxSandboxBackend, MacOSSandboxBackend
from .backends.base import SandboxBackend
from .catalog import DatasetCatalog, DatasetCatalogError
from .models import (
    SandboxAuditRecord,
    SandboxExecutionControl,
    SandboxExecutionRequest,
    SandboxHealth,
    SandboxLimits,
)
from .policy import SandboxPermissionPolicy


_AUDIT_LOG = logging.getLogger("roco_pvp_agent.sandbox.audit")
_SEMAPHORE_LOCK = threading.Lock()
_GLOBAL_SEMAPHORES: dict[int, threading.BoundedSemaphore] = {}


def _shared_semaphore(limit: int) -> threading.BoundedSemaphore:
    with _SEMAPHORE_LOCK:
        return _GLOBAL_SEMAPHORES.setdefault(limit, threading.BoundedSemaphore(limit))


@dataclass(frozen=True)
class SandboxSetup:
    service: "SandboxQueryService | None"
    health: SandboxHealth


class SandboxQueryService:
    def __init__(
        self,
        backend: SandboxBackend,
        *,
        data_root: Path | None = None,
        max_concurrency: int = 2,
        limits: SandboxLimits | None = None,
        policy: SandboxPermissionPolicy | None = None,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("sandbox max_concurrency 必须大于等于 1")
        self._backend = backend
        self._catalog = DatasetCatalog(data_root)
        self._limits = limits or SandboxLimits()
        self._policy = policy or SandboxPermissionPolicy()
        self._semaphore = _shared_semaphore(max_concurrency)

    @property
    def health(self) -> SandboxHealth:
        return self._backend.health

    def execute(
        self,
        *,
        code: str,
        dataset_ids: list[str],
        deadline: float | None,
        cancel_event: threading.Event,
    ) -> ToolOutcome:
        ids = tuple(dataset_ids)
        run_id = uuid.uuid4().hex
        code_digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
        decision = self._policy.evaluate(code, ids, self.health)
        if not decision.allowed:
            retryable = decision.rule_id in {"SBX-003", "SBX-008"}
            error_code = (
                "sandbox_execution_error" if retryable
                else "sandbox_backend_unavailable"
                if decision.rule_id == "SBX-001"
                else "sandbox_policy_denied"
            )
            outcome = ToolOutcome(
                content="沙箱查询代码未通过执行前检查",
                ok=False,
                error_code=error_code,
                retryable=retryable,
                details={"rule_id": decision.rule_id, "reason": decision.reason},
            )
            self._audit_denied(
                run_id=run_id,
                ids=ids,
                code_digest=code_digest,
                code_chars=len(code),
                rule_id=decision.rule_id,
                error_code=error_code,
            )
            return outcome

        acquired = self._acquire(deadline, cancel_event)
        if not acquired:
            return ToolOutcome(
                content="沙箱查询未能在截止时间前获得执行配额",
                ok=False,
                error_code="sandbox_timeout",
            )
        try:
            with tempfile.TemporaryDirectory(prefix="roco-sandbox-") as raw_root:
                # macOS 的 /var 是 /private/var 符号链接；Seatbelt literal 必须使用
                # 内核看到的规范路径，否则数据文件授权不会命中。
                run_root = Path(raw_root).resolve(strict=True)
                snapshot = self._catalog.snapshot_into(ids, run_root / "data")
                request = SandboxExecutionRequest(
                    run_id=run_id,
                    code=code,
                    dataset_ids=ids,
                    dataset_paths=snapshot.paths,
                    data_digest=snapshot.digest,
                    limits=self._limits,
                    control=SandboxExecutionControl(deadline=deadline, cancel_event=cancel_event),
                )
                result = self._backend.execute(request)
                if result.ok:
                    envelope = {
                        "ok": True,
                        "run_id": run_id,
                        "backend": self.health.active_backend,
                        "data_digest": snapshot.digest,
                        "dataset_ids": list(ids),
                        "evidence_id": f"sandbox:{snapshot.digest}:{run_id}",
                        "result": result.result,
                        "metrics": {
                            "duration_ms": result.duration_ms,
                            "truncated": result.truncated,
                        },
                    }
                    content = json.dumps(
                        envelope, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":"), allow_nan=False,
                    )
                    if len(content) > self._limits.model_output_chars:
                        outcome = ToolOutcome(
                            content=self._safe_error_message("sandbox_output_too_large"),
                            ok=False,
                            error_code="sandbox_output_too_large",
                            details={"run_id": run_id},
                        )
                    else:
                        outcome = ToolOutcome(
                            content=content,
                            details={"evidence_id": envelope["evidence_id"]},
                        )
                else:
                    code_value = result.error_code or "sandbox_execution_error"
                    outcome = ToolOutcome(
                        content=self._safe_error_message(code_value),
                        ok=False,
                        error_code=code_value,
                        retryable=code_value in {
                            "sandbox_execution_error", "sandbox_output_invalid",
                        },
                        details={"run_id": run_id},
                    )
                self._audit(
                    run_id=run_id,
                    ids=ids,
                    code_digest=code_digest,
                    code_chars=len(code),
                    rule_id=decision.rule_id,
                    data_digest=snapshot.digest,
                    result=result,
                )
                return outcome
        except DatasetCatalogError:
            return ToolOutcome(
                content="数据集快照校验失败",
                ok=False,
                error_code="sandbox_violation",
            )
        except OSError:
            return ToolOutcome(
                content="沙箱后端当前不可用",
                ok=False,
                error_code="sandbox_backend_unavailable",
            )
        finally:
            self._semaphore.release()

    def _acquire(self, deadline: float | None, cancel_event: threading.Event) -> bool:
        while not cancel_event.is_set():
            timeout = 0.05
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                timeout = min(timeout, remaining)
            if self._semaphore.acquire(timeout=timeout):
                return True
        return False

    def _audit(
        self,
        *,
        run_id: str,
        ids: tuple[str, ...],
        code_digest: str,
        code_chars: int,
        rule_id: str,
        data_digest: str,
        result,
    ) -> None:
        result_summary = json.dumps(
            result.result if result.ok else {"error_code": result.error_code},
            ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"),
        )
        record = SandboxAuditRecord(
            run_id=run_id,
            backend=self.health.active_backend or "unavailable",
            architecture=self.health.architecture or "unknown",
            dataset_ids=ids,
            code_sha256=code_digest,
            code_chars=code_chars,
            permission_rule_id=rule_id,
            data_digest=data_digest,
            duration_ms=result.duration_ms,
            exit_status=result.exit_status,
            resource_reason=result.resource_reason,
            result_sha256=hashlib.sha256(result_summary.encode("utf-8")).hexdigest(),
        )
        _AUDIT_LOG.info("sandbox_query %s", json.dumps(asdict(record), sort_keys=True))

    def _audit_denied(
        self,
        *,
        run_id: str,
        ids: tuple[str, ...],
        code_digest: str,
        code_chars: int,
        rule_id: str,
        error_code: str,
    ) -> None:
        summary = json.dumps({"error_code": error_code}, sort_keys=True)
        record = SandboxAuditRecord(
            run_id=run_id,
            backend=self.health.active_backend or "unavailable",
            architecture=self.health.architecture or "unknown",
            dataset_ids=ids,
            code_sha256=code_digest,
            code_chars=code_chars,
            permission_rule_id=rule_id,
            data_digest="",
            duration_ms=0.0,
            exit_status=None,
            resource_reason="permission_denied",
            result_sha256=hashlib.sha256(summary.encode("utf-8")).hexdigest(),
        )
        _AUDIT_LOG.info("sandbox_query %s", json.dumps(asdict(record), sort_keys=True))

    @staticmethod
    def _safe_error_message(code: str) -> str:
        return {
            "sandbox_backend_unavailable": "沙箱后端当前不可用",
            "sandbox_timeout": "沙箱查询执行超时",
            "sandbox_resource_limit": "沙箱查询超过资源限制",
            "sandbox_violation": "沙箱阻止了违规操作",
            "sandbox_execution_error": "沙箱代码执行失败",
            "sandbox_output_invalid": "emit_result 返回值不符合输出契约",
            "sandbox_output_too_large": "沙箱查询输出超过限制",
        }.get(code, "沙箱查询失败")


def build_sandbox_setup(
    settings: Any,
    *,
    data_root: Path | None = None,
    backend: SandboxBackend | None = None,
) -> SandboxSetup:
    enabled = bool(getattr(settings, "sandbox_enabled", False))
    configured = str(getattr(settings, "sandbox_backend", "auto"))
    if not enabled:
        return SandboxSetup(
            service=None,
            health=SandboxHealth(
                enabled=False,
                configured_backend=configured,
                active_backend=None,
                healthy=False,
                reason_code="sandbox_disabled",
                architecture=platform.machine().lower(),
            ),
        )
    runtime_value = str(getattr(settings, "sandbox_runtime_python", "")).strip()
    if runtime_value:
        runtime_python = Path(runtime_value)
    else:
        project_root = Path(__file__).resolve().parents[3]
        runtime_python = project_root / "sandbox-runtime" / ".venv" / "bin" / "python"

    selected = configured
    if configured == "auto":
        selected = {"Darwin": "macos", "Linux": "linux"}.get(platform.system(), "")
        if not selected:
            health = SandboxHealth(
                enabled=True,
                configured_backend=configured,
                active_backend=None,
                healthy=False,
                reason_code="platform_unsupported",
                architecture=platform.machine().lower(),
            )
            return SandboxSetup(None, health)
    if backend is None:
        factory = {
            "macos": MacOSSandboxBackend,
            "linux": LinuxSandboxBackend,
            "docker": DockerSandboxBackend,
        }.get(selected)
        if factory is None:
            health = SandboxHealth(
                enabled=True,
                configured_backend=configured,
                active_backend=None,
                healthy=False,
                reason_code="backend_invalid",
                architecture=platform.machine().lower(),
            )
            return SandboxSetup(None, health)
        try:
            backend = factory(runtime_python, configured_backend=configured)
        except Exception:
            health = SandboxHealth(
                enabled=True,
                configured_backend=configured,
                active_backend=selected,
                healthy=False,
                reason_code="backend_probe_failed",
                architecture=platform.machine().lower(),
            )
            return SandboxSetup(None, health)
    if not backend.health.healthy:
        return SandboxSetup(None, backend.health)
    try:
        service = SandboxQueryService(
            backend,
            data_root=data_root,
            max_concurrency=int(getattr(settings, "sandbox_max_concurrency", 2)),
        )
    except (OSError, ValueError):
        health = SandboxHealth(
            enabled=True,
            configured_backend=configured,
            active_backend=backend.health.active_backend,
            healthy=False,
            reason_code="dataset_registry_unavailable",
            architecture=backend.health.architecture,
        )
        return SandboxSetup(None, health)
    return SandboxSetup(service, backend.health)


__all__ = ["SandboxQueryService", "SandboxSetup", "build_sandbox_setup"]
