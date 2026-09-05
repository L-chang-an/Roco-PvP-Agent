"""真实 Linux/WSL2 bwrap+seccomp conformance；由对应架构 runner 显式开启。"""

from __future__ import annotations

import os
import platform
import threading
from pathlib import Path

import pytest

from roco_pvp_agent.config import Settings
from roco_pvp_agent.sandbox.service import build_sandbox_setup


pytestmark = pytest.mark.skipif(
    platform.system() != "Linux" or os.getenv("RUN_SANDBOX_INTEGRATION") != "1",
    reason="requires real bwrap/libseccomp and RUN_SANDBOX_INTEGRATION=1",
)


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "sandbox-runtime" / ".venv" / "bin" / "python"


def test_real_linux_or_wsl2_query_and_mount_isolation():
    setup = build_sandbox_setup(Settings(
        sandbox_enabled=True,
        sandbox_backend="linux",
        sandbox_runtime_python=str(RUNTIME),
    ))
    assert setup.health.healthy, setup.health.reason_code
    assert setup.service is not None
    success = setup.service.execute(
        code="emit_result(len(load_dataset('full_spirits')))",
        dataset_ids=["full_spirits"],
        deadline=None,
        cancel_event=threading.Event(),
    )
    assert success.ok is True

    # WSL2 下 /mnt/c 不在 mount namespace；普通 Linux 上也同样不可见。
    original = setup.service._policy
    setup.service._policy = type("Allow", (), {
        "evaluate": lambda _self, *_args: type("Decision", (), {
            "allowed": True, "rule_id": "TEST-ALLOW", "reason": "conformance",
        })(),
    })()
    try:
        escape_paths = [
            "/mnt/c/Windows/System32/cmd.exe",
            "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
            "/mnt/c/Windows/System32/wsl.exe",
            "/var/run/docker.sock",
        ]
        escaped = [setup.service.execute(
            code=f"emit_result(np.fromfile({path!r},dtype=np.uint8,count=2).tolist())",
            dataset_ids=["full_spirits"],
            deadline=None,
            cancel_event=threading.Event(),
        ) for path in escape_paths]
    finally:
        setup.service._policy = original
    assert all(outcome.ok is False for outcome in escaped)
