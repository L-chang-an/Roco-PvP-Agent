"""受信任 CPython 3.12 运行时探测。"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimeInfo:
    executable: Path
    executable_resolved: Path
    prefix: Path
    base_prefix: Path
    purelib: Path
    platlib: Path
    numpy_version: str
    pandas_version: str


class RuntimeUnavailable(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def inspect_runtime(python: Path, *, standalone: bool = False) -> RuntimeInfo:
    requested = Path(python)
    if not requested.is_absolute() or not requested.exists() or not os.access(requested, os.X_OK):
        raise RuntimeUnavailable("runtime_python_missing")
    probe = (
        "import json,sys,sysconfig,numpy,pandas;"
        "print(json.dumps({'version':list(sys.version_info[:3]),"
        "'executable':sys.executable,'prefix':sys.prefix,'base_prefix':sys.base_prefix,"
        "'purelib':sysconfig.get_path('purelib'),'platlib':sysconfig.get_path('platlib'),"
        "'numpy':numpy.__version__,'pandas':pandas.__version__}))"
    )
    argv = [str(requested), "-I", "-B", "-X", "utf8"]
    if standalone:
        argv.append("-S")
        probe = (f"import sys;sys.path.insert(0,{str(requested.parent / 'Lib' / 'site-packages')!r});" + probe)
    try:
        completed = subprocess.run(
            [*argv, "-c", probe],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={
                **_windows_system_environment(),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONNOUSERSITE": "1",
                "OMP_NUM_THREADS": "1",
                "OPENBLAS_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
                "NUMEXPR_NUM_THREADS": "1",
            },
            # Newly copied DLLs can incur a cold antivirus scan. Deployment
            # gets a bounded warm-up; query budgets come from SandboxLimits.
            timeout=30 if standalone else 8,
            check=False,
        )
        if completed.returncode != 0 or len(completed.stdout) > 16_384:
            raise RuntimeUnavailable("runtime_probe_failed")
        payload = json.loads(completed.stdout)
    except RuntimeUnavailable:
        raise
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, KeyError):
        raise RuntimeUnavailable("runtime_probe_failed") from None
    version = payload.get("version") or []
    if len(version) < 2 or version[0:2] != [3, 12]:
        raise RuntimeUnavailable("runtime_python_version_mismatch")
    # 部署锁和健康探针同时钉住通用数据依赖，防止节点间查询语义漂移。
    if payload.get("numpy") != "2.4.2" or payload.get("pandas") != "3.0.1":
        raise RuntimeUnavailable("runtime_dependency_version_mismatch")
    return RuntimeInfo(
        executable=Path(payload["executable"]).absolute(),
        executable_resolved=Path(payload["executable"]).resolve(strict=True),
        prefix=Path(payload["prefix"]).resolve(strict=True),
        base_prefix=Path(payload["base_prefix"]).resolve(strict=True),
        purelib=Path(payload["purelib"]).resolve(strict=True),
        platlib=Path(payload["platlib"]).resolve(strict=True),
        numpy_version=payload["numpy"],
        pandas_version=payload["pandas"],
    )


def minimal_environment(temp_dir: Path) -> dict[str, str]:
    """固定、无宿主继承的子进程环境。"""

    return {
        **_windows_system_environment(),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HOME": str(temp_dir),
        "TMPDIR": str(temp_dir),
        **({"TEMP": str(temp_dir), "TMP": str(temp_dir),
            "USERPROFILE": str(temp_dir), "LOCALAPPDATA": str(temp_dir),
            "APPDATA": str(temp_dir)} if os.name == "nt" else {}),
        "PYTHONNOUSERSITE": "1",
        "PYTHONHASHSEED": "0",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
    }


def _windows_system_environment() -> dict[str, str]:
    if os.name != "nt":
        return {}
    from .backends.win32 import api
    return {"SystemRoot": str(api().system_directory(windows=True))}


__all__ = ["RuntimeInfo", "RuntimeUnavailable", "inspect_runtime", "minimal_environment"]
