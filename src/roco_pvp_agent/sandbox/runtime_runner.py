"""独立沙箱运行时入口。

该文件必须能由 CPython 3.12 以 ``-I`` 直接执行，不能导入主应用包。stdin/stdout
各承载一个 JSON envelope；用户 stdout/stderr 永远不会进入协议输出。
"""

from __future__ import annotations

import builtins
import io
import json
import math
import os
import signal
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any

if sys.platform != "win32":
    import resource


class _OutputInvalid(ValueError):
    pass


class _OutputTooLarge(ValueError):
    pass


class _LimitedText(io.TextIOBase):
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._size = 0

    def writable(self) -> bool:
        return True

    def write(self, value: str) -> int:
        size = len(value.encode("utf-8", errors="replace"))
        self._size += size
        if self._size > self._limit:
            raise _OutputTooLarge("user_output_limit")
        return len(value)


def _start_parent_watchdog() -> None:
    """macOS 没有 bwrap 的 die-with-parent，runner 自行监测父进程消失。"""

    if sys.platform != "darwin":
        return
    parent_pid = os.getppid()

    def watch() -> None:
        while True:
            time.sleep(0.2)
            if os.getppid() == parent_pid:
                continue
            try:
                os.killpg(os.getpgrp(), signal.SIGKILL)
            finally:
                os._exit(137)

    threading.Thread(target=watch, name="sandbox-parent-watch", daemon=True).start()


def _set_limit(
    which: int,
    soft: int,
    hard: int | None = None,
    *,
    strict: bool = False,
) -> None:
    try:
        resource.setrlimit(which, (soft, soft if hard is None else hard))
    except (AttributeError, OSError, ValueError):
        if strict:
            raise RuntimeError("resource_limit_setup_failed") from None


def _apply_limits(limits: dict[str, Any]) -> None:
    if sys.platform == "win32":
        # platform.machine() otherwise performs an optional WMI/COM query during
        # pandas import. Use its normal registry/environment fallback instead.
        # This compatibility setting is not part of the OS security boundary.
        sys.modules["_wmi"] = None
        # No payload switch can disable OS isolation. This helper is a verified
        # part of the standalone runtime, loaded without importing the host app.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_sandbox_win32", Path(__file__).with_name("_win32.py"))
        if spec is None or spec.loader is None:
            raise RuntimeError("windows_isolation_verifier_missing")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.api().verify_process()
        return
    cpu = int(limits.get("cpu_seconds", 4))
    memory = int(limits.get("memory_bytes", 512 * 1024 * 1024))
    temp_bytes = int(limits.get("temp_bytes", 16 * 1024 * 1024))
    strict = bool(limits.get("strict_limits", True))
    _set_limit(resource.RLIMIT_CPU, cpu, cpu + 1, strict=strict)
    if sys.platform == "darwin" and hasattr(resource, "RLIMIT_DATA"):
        # macOS 进程会预留巨大的虚拟地址空间，RLIMIT_AS 无法可靠下调；
        # 后端用 libproc 监控物理占用，这里的 DATA/RSS 仅作可用时的纵深限制。
        _set_limit(resource.RLIMIT_DATA, memory, strict=False)
        if hasattr(resource, "RLIMIT_RSS"):
            _set_limit(resource.RLIMIT_RSS, memory, strict=False)
    elif hasattr(resource, "RLIMIT_AS"):
        _set_limit(resource.RLIMIT_AS, memory, strict=strict)
    elif strict:
        raise RuntimeError("address_space_limit_unavailable")
    _set_limit(resource.RLIMIT_FSIZE, temp_bytes, strict=strict)
    _set_limit(resource.RLIMIT_NOFILE, 64, strict=strict)
    if limits.get("enforce_nproc") and hasattr(resource, "RLIMIT_NPROC"):
        _set_limit(
            resource.RLIMIT_NPROC,
            int(limits.get("max_pids", 16)),
            strict=strict,
        )
    elif limits.get("enforce_nproc") and strict:
        raise RuntimeError("process_limit_unavailable")
    if hasattr(resource, "RLIMIT_CORE"):
        _set_limit(resource.RLIMIT_CORE, 0, strict=strict)


def _safe_builtins(allowed_imports: frozenset[str]) -> dict[str, Any]:
    allowed_names = (
        "abs", "all", "any", "bool", "dict", "enumerate", "Exception", "filter",
        "float", "frozenset", "int", "isinstance", "len", "list", "map", "max",
        "min", "next", "print", "range", "reversed", "round", "set", "slice", "sorted",
        "str", "sum", "tuple", "ValueError", "TypeError", "RuntimeError", "KeyError", "zip",
    )
    result = {name: getattr(builtins, name) for name in allowed_names}
    original_import = builtins.__import__

    def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level or name.split(".", 1)[0] not in allowed_imports:
            raise ImportError("module_not_allowed")
        return original_import(name, globals, locals, fromlist, level)

    result["__import__"] = safe_import
    return result


def _normalize(
    value: Any,
    *,
    max_records: int,
    max_depth: int,
    depth: int = 0,
    seen: set[int] | None = None,
) -> tuple[Any, bool]:
    if depth > max_depth:
        raise _OutputInvalid("output_depth_exceeded")
    if value is None or isinstance(value, (str, bool, int)):
        return value, False
    if isinstance(value, float):
        return (value if math.isfinite(value) else None), False

    # 依赖由独立运行时锁定；放在函数内只是让协议错误仍能被输出。
    import numpy as np
    import pandas as pd

    if value is pd.NA or value is pd.NaT:
        return None, False
    if isinstance(value, np.generic):
        return _normalize(
            value.item(), max_records=max_records, max_depth=max_depth,
            depth=depth, seen=seen,
        )
    if isinstance(value, pd.DataFrame):
        truncated = len(value.index) > max_records
        return _normalize(
            value.head(max_records).to_dict(orient="records"),
            max_records=max_records,
            max_depth=max_depth,
            depth=depth,
            seen=seen,
        )[0], truncated
    if isinstance(value, pd.Series):
        truncated = len(value.index) > max_records
        normalized, nested_truncated = _normalize(
            value.head(max_records).tolist(),
            max_records=max_records,
            max_depth=max_depth,
            depth=depth,
            seen=seen,
        )
        return normalized, truncated or nested_truncated
    if isinstance(value, np.ndarray):
        if value.ndim > max_depth:
            raise _OutputInvalid("output_depth_exceeded")
        array = value
        truncated = len(array) > max_records if array.ndim else False
        if truncated:
            array = array[:max_records]
        normalized, nested_truncated = _normalize(
            array.tolist(), max_records=max_records, max_depth=max_depth,
            depth=depth, seen=seen,
        )
        return normalized, truncated or nested_truncated

    seen = seen or set()
    identity = id(value)
    if identity in seen:
        raise _OutputInvalid("output_cycle")
    if isinstance(value, (list, tuple)):
        seen.add(identity)
        truncated = len(value) > max_records
        output = []
        for item in value[:max_records]:
            normalized, child_truncated = _normalize(
                item, max_records=max_records, max_depth=max_depth,
                depth=depth + 1, seen=seen,
            )
            output.append(normalized)
            truncated = truncated or child_truncated
        seen.remove(identity)
        return output, truncated
    if isinstance(value, dict):
        seen.add(identity)
        truncated = len(value) > max_records
        output = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_records:
                break
            if not isinstance(key, (str, int, float, bool)) or key is None:
                raise _OutputInvalid("output_key_invalid")
            normalized, child_truncated = _normalize(
                item, max_records=max_records, max_depth=max_depth,
                depth=depth + 1, seen=seen,
            )
            output[str(key)] = normalized
            truncated = truncated or child_truncated
        seen.remove(identity)
        return output, truncated
    raise _OutputInvalid("output_type_invalid")


def _run(payload: dict[str, Any]) -> dict[str, Any]:
    limits = payload.get("limits") or {}
    _apply_limits(limits)
    _start_parent_watchdog()

    runtime_site = payload.get("runtime_site_packages")
    if runtime_site:
        # 仅后端可写该 envelope 字段；模型契约中不存在路径参数。
        sys.path.insert(0, str(runtime_site))

    import numpy as np
    import pandas as pd

    dataset_paths = payload.get("dataset_paths") or {}
    allowed_ids = frozenset(payload.get("dataset_ids") or [])
    cache: dict[str, Any] = {}
    emitted: list[Any] = []

    def load_dataset(dataset_id: str) -> Any:
        if dataset_id not in allowed_ids or dataset_id not in dataset_paths:
            raise PermissionError("dataset_not_authorized")
        if dataset_id not in cache:
            with Path(dataset_paths[dataset_id]).open("r", encoding="utf-8") as handle:
                cache[dataset_id] = json.load(handle)
        return cache[dataset_id]

    def emit_result(value: Any) -> None:
        if emitted:
            raise _OutputInvalid("emit_result_called_multiple_times")
        emitted.append(value)

    game_data = types.ModuleType("game_data")
    game_data.load_dataset = load_dataset
    game_data.emit_result = emit_result
    sys.modules["game_data"] = game_data

    allowed_imports = frozenset({
        "game_data", "numpy", "pandas", "json", "math", "statistics", "collections",
        "itertools", "functools", "re", "decimal", "fractions", "datetime",
    })
    namespace = {
        "__builtins__": _safe_builtins(allowed_imports),
        "load_dataset": load_dataset,
        "emit_result": emit_result,
        "np": np,
        "pd": pd,
    }
    raw_limit = int(limits.get("raw_output_bytes", 64 * 1024))
    sys.stdout = _LimitedText(raw_limit)
    sys.stderr = _LimitedText(raw_limit)
    try:
        compiled = compile(str(payload.get("code", "")), "<sandbox-query>", "exec")
        exec(compiled, namespace, namespace)
        if len(emitted) != 1:
            raise _OutputInvalid("emit_result_not_called")
        result, truncated = _normalize(
            emitted[0],
            max_records=int(limits.get("max_records", 200)),
            max_depth=int(limits.get("max_depth", 8)),
        )
        envelope = {"ok": True, "result": result, "truncated": truncated}
        encoded = json.dumps(envelope, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded) > int(limits.get("model_output_chars", 20_000)):
            raise _OutputTooLarge("result_envelope_too_large")
        return envelope
    except _OutputTooLarge:
        return {"ok": False, "error_code": "sandbox_output_too_large"}
    except _OutputInvalid:
        return {"ok": False, "error_code": "sandbox_output_invalid"}
    except MemoryError:
        return {"ok": False, "error_code": "sandbox_resource_limit", "resource_reason": "memory"}
    except BaseException:
        return {"ok": False, "error_code": "sandbox_execution_error"}


def main() -> int:
    protocol_stdout = sys.__stdout__
    try:
        raw = sys.stdin.buffer.read(32 * 1024)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("invalid_envelope")
        result = _run(payload)
    except BaseException:
        result = {"ok": False, "error_code": "sandbox_execution_error"}
    protocol_stdout.write(json.dumps(
        result, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
    protocol_stdout.flush()
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
