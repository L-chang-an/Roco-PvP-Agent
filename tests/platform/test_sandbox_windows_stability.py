"""Opt-in sustained acceptance; one green short conformance run is insufficient.

Set RUN_SANDBOX_INTEGRATION=1 and RUN_SANDBOX_STABILITY=1 on the target host.
This deliberately stops at the first failure instead of retrying a crashed query.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from roco_pvp_agent.sandbox.backends.windows import WindowsSandboxBackend
from roco_pvp_agent.sandbox.models import (
    SandboxExecutionControl, SandboxExecutionRequest, SandboxLimits)


pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("RUN_SANDBOX_INTEGRATION") != "1"
    or os.environ.get("RUN_SANDBOX_STABILITY") != "1",
    reason="opt-in sustained Windows acceptance on a provisioned runtime")


def test_real_repeated_normal_queries(tmp_path):
    root = Path(__file__).resolve().parents[2]
    runtime = Path(os.environ.get("SANDBOX_WINDOWS_TEST_RUNTIME",
                                 str(root / "sandbox-runtime/windows-runtime/python.exe")))
    backend = WindowsSandboxBackend(runtime)
    assert backend.health.healthy, backend.health.reason_code
    data = tmp_path / "full_spirits.json"
    data.write_text('[{"name":"迪莫","value":2}]', encoding="utf-8")
    examples = [
        ("emit_result(pd.DataFrame(load_dataset('full_spirits')))", [{"name": "迪莫", "value": 2}]),
        ("emit_result({'series':pd.Series([1,2]),'array':np.array([3,4]),'scalar':np.int64(5)})",
         {"series": [1, 2], "array": [3, 4], "scalar": 5}),
        ("emit_result(1)", 1),
    ]
    for number in range(100):
        code, expected = examples[number % len(examples)]
        request = SandboxExecutionRequest(
            f"stability-{number}", code, ("full_spirits",), {"full_spirits": data},
            "test", SandboxLimits(), SandboxExecutionControl(None, threading.Event()))
        result = backend.execute(request)
        assert result.ok, (number, result)
        assert result.result == expected
