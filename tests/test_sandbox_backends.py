"""后端 argv/profile 构造不变量；真实隔离由 tests/platform conformance 验证。"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import threading

import pytest

from roco_pvp_agent.sandbox.backends.cgroup import CgroupV2Scope
from roco_pvp_agent.sandbox.backends.docker import DockerSandboxBackend
from roco_pvp_agent.sandbox.backends.linux import LinuxSandboxBackend
from roco_pvp_agent.sandbox.backends.macos import MacOSSandboxBackend, _seatbelt_literal
from roco_pvp_agent.sandbox.models import (
    SandboxExecutionControl,
    SandboxExecutionRequest,
    SandboxLimits,
)
from roco_pvp_agent.sandbox.runtime import RuntimeInfo


def _runtime() -> RuntimeInfo:
    return RuntimeInfo(
        executable=Path("/opt/runtime/bin/python"),
        executable_resolved=Path("/opt/python/bin/python3.12"),
        prefix=Path("/opt/runtime"),
        base_prefix=Path("/opt/python"),
        purelib=Path("/opt/runtime/lib/python3.12/site-packages"),
        platlib=Path("/opt/runtime/lib/python3.12/site-packages"),
        numpy_version="2.4.2",
        pandas_version="3.0.1",
    )


def test_linux_argv_has_isolated_namespaces_seccomp_and_selected_mount_only(tmp_path: Path):
    backend = object.__new__(LinuxSandboxBackend)
    backend._bwrap = Path("/usr/bin/bwrap")
    backend._runtime = _runtime()
    selected = tmp_path / "full_spirits.json"
    selected.write_text("[]", encoding="utf-8")
    argv, mapped = backend._argv({"full_spirits": selected}, 9)
    joined = " ".join(argv)
    assert "--unshare-all" in argv
    assert "--disable-userns" in argv
    assert argv[argv.index("--seccomp") + 1] == "9"
    assert "--cap-drop ALL" in joined
    assert str(selected) in argv
    assert mapped == {"full_spirits": "/data/full_spirits.json"}
    assert "/mnt/c" not in joined
    assert "docker.sock" not in joined
    assert "/usr/bin " not in joined


def test_macos_profile_quotes_paths_and_never_adds_unselected_data(tmp_path: Path):
    with pytest.raises(ValueError):
        _seatbelt_literal(Path("bad\npath"))
    assert _seatbelt_literal(PurePosixPath('/tmp/a"b')) == '"/tmp/a\\"b"'

    backend = object.__new__(MacOSSandboxBackend)
    backend._runtime = _runtime()
    selected = tmp_path / "data" / "full_spirits.json"
    selected.parent.mkdir()
    selected.write_text("[]", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    request = SandboxExecutionRequest(
        run_id="a" * 32,
        code="emit_result(1)",
        dataset_ids=("full_spirits",),
        dataset_paths={"full_spirits": selected},
        data_digest="digest",
        limits=SandboxLimits(),
        control=SandboxExecutionControl(None, threading.Event()),
    )
    profile = backend._profile(request, work)
    assert _seatbelt_literal(selected) in profile
    assert "full_skills.json" not in profile
    assert "(deny network*)" in profile
    assert "(deny default)" in profile


def test_cgroup_without_delegated_controllers_falls_back_without_residue(tmp_path: Path):
    root = tmp_path / "cgroup"
    root.mkdir()
    membership = tmp_path / "membership"
    membership.write_text("0::/\n", encoding="utf-8")
    scope = CgroupV2Scope.try_create(
        "b" * 32, SandboxLimits(),
        cgroup_root=root, membership_file=membership,
    )
    assert scope is None
    assert list(root.iterdir()) == []


def test_docker_backend_is_explicitly_degraded_until_followup_milestone():
    backend = DockerSandboxBackend(Path("/nonexistent"))
    assert backend.health.healthy is False
    assert backend.health.reason_code == "docker_backend_not_implemented"
