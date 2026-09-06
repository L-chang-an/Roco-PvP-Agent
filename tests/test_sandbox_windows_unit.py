"""Platform-independent configuration/protocol tests and Windows ABI checks."""

from __future__ import annotations

import ctypes
import os
import time
from pathlib import Path

import pytest

from roco_pvp_agent.config import Settings
from roco_pvp_agent.sandbox.backends.windows import WindowsSandboxBackend, _ProcessOutput
from roco_pvp_agent.sandbox.models import SandboxBackendName, SandboxHealth
from roco_pvp_agent.sandbox.service import build_sandbox_setup


def test_windows_configuration_and_auto_selection(monkeypatch):
    from roco_pvp_agent.sandbox import service
    seen = []

    class Unavailable:
        def __init__(self, path, *, configured_backend):
            seen.append(path)
            self.health = SandboxHealth(True, configured_backend, "windows", False, "test_unavailable")

    monkeypatch.setattr(service.platform, "system", lambda: "Windows")
    monkeypatch.setattr(service, "WindowsSandboxBackend", Unavailable)
    for backend in ("windows", "auto"):
        result = build_sandbox_setup(Settings(sandbox_enabled=True, sandbox_backend=backend))
        assert result.service is None
        assert result.health.active_backend == "windows"
        assert result.health.reason_code == "test_unavailable"
        assert seen[-1].parts[-2:] == ("windows-runtime", "python.exe")
    assert SandboxBackendName.WINDOWS.value == "windows"


def test_windows_unavailable_never_probes_or_spawns(monkeypatch):
    from roco_pvp_agent.sandbox.backends import windows
    monkeypatch.setattr(windows.platform, "system", lambda: "Linux")
    monkeypatch.setattr(WindowsSandboxBackend, "_probe", lambda self: pytest.fail("probe called"))
    backend = WindowsSandboxBackend(Path("missing"))
    assert backend.health.reason_code == "platform_not_windows"


@pytest.mark.parametrize("payload,status,code", [
    (b'{"ok":true,"result":1}', 1, "sandbox_violation"),
    (b'{"ok":true,"result":NaN}', 0, "sandbox_output_invalid"),
    (b'{"ok":1}', 0, "sandbox_output_invalid"),
    (b'broken', 3, "sandbox_violation"),
    (b'{"ok":false,"error_code":"secret"}', 2, "sandbox_violation"),
    (b'{"ok":false,"error_code":[],"resource_reason":{}}', 2, "sandbox_violation"),
    (b'{"ok":true,"result":1e400}', 0, "sandbox_output_invalid"),
    (b'{"ok":true,"result":1,"truncated":"false"}', 0, "sandbox_output_invalid"),
    (b'{"ok":true}', 0, "sandbox_output_invalid"),
    (b'{"ok":true,"result":[' + b'0,' * 200 + b'0]}', 0, "sandbox_output_invalid"),
    (b'{"ok":true,"result":' + b'[' * 9 + b'0' + b']' * 9 + b'}', 0, "sandbox_output_invalid"),
])
def test_windows_protocol_rejects_invalid_envelopes(payload, status, code):
    result = WindowsSandboxBackend._decode(_ProcessOutput(payload, status), time.monotonic())
    assert result.error_code == code


def test_untrusted_resource_reason_does_not_reach_audit():
    result = WindowsSandboxBackend._decode(_ProcessOutput(
        b'{"ok":false,"error_code":"sandbox_execution_error","resource_reason":"SECRET"}', 2),
        time.monotonic())
    assert result.resource_reason is None


@pytest.mark.skipif(os.name != "nt", reason="Windows SDK ABI")
def test_windows_structures_match_x64_sdk_layout():
    from roco_pvp_agent.sandbox.backends.win32 import (
        StartupInfo, StartupInfoEx, FileInfo, BasicLimits, ExtendedLimits, Accounting)
    assert ctypes.sizeof(StartupInfo) == 104
    assert ctypes.sizeof(StartupInfoEx) == 112
    assert ctypes.sizeof(FileInfo) == 52
    assert ctypes.sizeof(BasicLimits) == 64
    assert ctypes.sizeof(ExtendedLimits) == 144
    assert ctypes.sizeof(Accounting) == 48


@pytest.mark.skipif(os.name != "nt", reason="Windows file-handle snapshot verification")
def test_windows_snapshot_rejects_hardlink_and_allows_normal_file(tmp_path):
    from roco_pvp_agent.sandbox.catalog import DatasetCatalog, DatasetCatalogError
    root = tmp_path / "data"
    root.mkdir()
    source = root / "full_spirits.json"
    source.write_bytes(b'[{"value":1}]')
    catalog = DatasetCatalog(root)
    snapshot = catalog.snapshot_into(("full_spirits",), tmp_path / "snapshot")
    assert snapshot.paths["full_spirits"].read_bytes() == source.read_bytes()
    os.link(source, tmp_path / "link.json")
    with pytest.raises(DatasetCatalogError, match="not_regular"):
        catalog.snapshot_into(("full_spirits",), tmp_path / "denied")


@pytest.mark.skipif(os.name != "nt", reason="Windows path admission")
def test_windows_rejects_unc_and_ads():
    from roco_pvp_agent.sandbox.backends.win32 import reject_reparse_path
    for value in (r"\\server\share\file.json", r"C:\data\file.json:stream"):
        with pytest.raises(ValueError, match="local_path"):
            reject_reparse_path(Path(value))


def test_advisor_health_tracks_backend_failure(tmp_path):
    from types import SimpleNamespace
    from roco_pvp_agent.advisor.agent import TeamAdvisorAgent
    from roco_pvp_agent.sandbox.service import SandboxQueryService, SandboxSetup
    backend = SimpleNamespace(health=SandboxHealth(True, "windows", "windows", True))
    service = SandboxQueryService(backend, data_root=tmp_path)
    agent = TeamAdvisorAgent(Settings(), sandbox_setup=SandboxSetup(service, backend.health))
    backend.health = SandboxHealth(True, "windows", "windows", False, "windows_job_setup_failed")
    assert not agent.sandbox_health.healthy
    assert agent.sandbox_health.reason_code == "windows_job_setup_failed"


@pytest.mark.skipif(os.name != "nt", reason="Windows junction admission")
def test_windows_snapshot_rejects_junction_without_symlink_privilege(tmp_path):
    import _winapi
    from roco_pvp_agent.sandbox.backends.win32 import open_verified_file
    target = tmp_path / "target"
    target.mkdir()
    (target / "data.json").write_text("[]", encoding="utf-8")
    junction = tmp_path / "junction"
    _winapi.CreateJunction(str(target), str(junction))
    try:
        with pytest.raises(ValueError, match="reparse_point_denied"):
            with open_verified_file(junction / "data.json"):
                pytest.fail("junction was admitted")
    finally:
        # Remove only the junction itself, never recurse into its target.
        junction.rmdir()
    assert (target / "data.json").read_text() == "[]"


@pytest.mark.skipif(os.name != "nt", reason="Windows snapshot race protection")
def test_windows_snapshot_handle_prevents_file_and_ancestor_replacement(tmp_path):
    from roco_pvp_agent.sandbox.backends.win32 import open_verified_file
    directory = tmp_path / "data"
    directory.mkdir()
    source = directory / "full_spirits.json"
    source.write_bytes(b'[{"value":1}]')
    with open_verified_file(source) as stream:
        for origin, destination in ((source, directory / "replaced.json"),
                                    (directory, tmp_path / "replaced")):
            with pytest.raises(OSError) as error:
                origin.rename(destination)
            assert error.value.winerror in {5, 32}
        assert stream.read() == b'[{"value":1}]'
    # The handles must also be released on successful completion.
    source.rename(directory / "after.json")
    directory.rename(tmp_path / "after")
