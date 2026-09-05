"""Docker 后端占位：协议已冻结，镜像与 conformance 验收属于后续里程碑。"""

from __future__ import annotations

import platform
from pathlib import Path

from ..models import (
    SandboxBackendName,
    SandboxExecutionRequest,
    SandboxExecutionResult,
    SandboxHealth,
)
from .base import SandboxBackend


class DockerSandboxBackend(SandboxBackend):
    name = SandboxBackendName.DOCKER

    def __init__(self, _runtime_python: Path, *, configured_backend: str = "docker") -> None:
        self._health = SandboxHealth(
            enabled=True,
            configured_backend=configured_backend,
            active_backend=self.name.value,
            healthy=False,
            reason_code="docker_backend_not_implemented",
            architecture=platform.machine().lower(),
        )

    @property
    def health(self) -> SandboxHealth:
        return self._health

    def execute(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        return SandboxExecutionResult(
            ok=False,
            error_code="sandbox_backend_unavailable",
            resource_reason=self.health.reason_code,
        )


__all__ = ["DockerSandboxBackend"]
