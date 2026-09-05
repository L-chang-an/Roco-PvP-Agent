"""真实 macOS Seatbelt conformance；CI/本机显式开启，不能由 mock 替代。"""

from __future__ import annotations

import os
import platform
import threading
import time
from pathlib import Path

import pytest

from roco_pvp_agent.config import Settings
from roco_pvp_agent.sandbox.models import SandboxLimits
from roco_pvp_agent.sandbox.service import SandboxQueryService, build_sandbox_setup


pytestmark = pytest.mark.skipif(
    platform.system() != "Darwin" or os.getenv("RUN_SANDBOX_INTEGRATION") != "1",
    reason="requires real macOS Seatbelt and RUN_SANDBOX_INTEGRATION=1",
)


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "sandbox-runtime" / ".venv" / "bin" / "python"


@pytest.fixture(scope="module")
def sandbox_service():
    setup = build_sandbox_setup(Settings(
        sandbox_enabled=True,
        sandbox_backend="macos",
        sandbox_runtime_python=str(RUNTIME),
    ))
    assert setup.health.healthy, setup.health.reason_code
    assert setup.service is not None
    return setup.service


def test_real_seatbelt_query_returns_structured_result(sandbox_service):
    outcome = sandbox_service.execute(
        code=("rows=load_dataset('full_skills')\n"
              "emit_result({'count':len(rows),'first':rows[0]['name']})"),
        dataset_ids=["full_skills"],
        deadline=None,
        cancel_event=threading.Event(),
    )
    assert outcome.ok is True
    assert '"count":553' in outcome.content


@pytest.mark.parametrize("code", [
    "emit_result(pd.read_csv('/etc/passwd').to_dict())",
    "emit_result(pd.read_json('http://127.0.0.1:9/'))",
])
def test_real_seatbelt_blocks_host_file_and_network(sandbox_service, code: str):
    # 绕过 AST policy 会让请求到达 OS 边界；仍然不得成功。
    original = sandbox_service._policy
    sandbox_service._policy = type("Allow", (), {
        "evaluate": lambda _self, *_args: type("Decision", (), {
            "allowed": True, "rule_id": "TEST-ALLOW", "reason": "conformance",
        })(),
    })()
    try:
        outcome = sandbox_service.execute(
            code=code,
            dataset_ids=["full_skills"],
            deadline=None,
            cancel_event=threading.Event(),
        )
    finally:
        sandbox_service._policy = original
    assert outcome.ok is False


def test_real_seatbelt_kills_infinite_loop_without_residual_worker(sandbox_service):
    short_service = SandboxQueryService(
        sandbox_service._backend,
        limits=SandboxLimits(wall_seconds=0.25, cpu_seconds=1),
    )
    started = time.monotonic()
    outcome = short_service.execute(
        code="while True:\n    pass\nemit_result(1)",
        dataset_ids=["full_skills"],
        deadline=None,
        cancel_event=threading.Event(),
    )
    assert outcome.error_code in {"sandbox_timeout", "sandbox_resource_limit"}
    assert time.monotonic() - started < 2.0
