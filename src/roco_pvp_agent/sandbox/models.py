"""沙箱查询的稳定内部协议。"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


class SandboxBackendName(str, Enum):
    MACOS = "macos"
    LINUX = "linux"
    DOCKER = "docker"


class PermissionAction(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True)
class PermissionDecision:
    action: PermissionAction
    rule_id: str
    reason: str

    @property
    def allowed(self) -> bool:
        return self.action is PermissionAction.ALLOW


@dataclass(frozen=True)
class SandboxLimits:
    wall_seconds: float = 8.0
    cpu_seconds: int = 4
    memory_bytes: int = 512 * 1024 * 1024
    max_pids: int = 16
    temp_bytes: int = 16 * 1024 * 1024
    raw_output_bytes: int = 64 * 1024
    model_output_chars: int = 20_000
    max_records: int = 200
    max_depth: int = 8


@dataclass(frozen=True)
class SandboxHealth:
    enabled: bool
    configured_backend: str
    active_backend: str | None
    healthy: bool
    reason_code: str | None = None
    architecture: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "configured_backend": self.configured_backend,
            "active_backend": self.active_backend,
            "healthy": self.healthy,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class SandboxExecutionControl:
    deadline: float | None
    cancel_event: threading.Event


@dataclass(frozen=True)
class SandboxExecutionRequest:
    run_id: str
    code: str
    dataset_ids: tuple[str, ...]
    dataset_paths: Mapping[str, Path]
    data_digest: str
    limits: SandboxLimits
    control: SandboxExecutionControl


@dataclass(frozen=True)
class SandboxExecutionResult:
    ok: bool
    error_code: str | None = None
    result: Any = None
    duration_ms: float = 0.0
    truncated: bool = False
    exit_status: int | None = None
    resource_reason: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SandboxAuditRecord:
    run_id: str
    backend: str
    architecture: str
    dataset_ids: tuple[str, ...]
    code_sha256: str
    code_chars: int
    permission_rule_id: str
    data_digest: str
    duration_ms: float
    exit_status: int | None
    resource_reason: str | None
    result_sha256: str
