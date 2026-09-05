"""独立 CPython 3.12 runner 的单 envelope 与结果归一化契约。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "sandbox-runtime" / ".venv" / "bin" / "python"
RUNNER = ROOT / "src" / "roco_pvp_agent" / "sandbox" / "runtime_runner.py"


def _run(code: str, data_file: Path, *, allowed_id: str = "full_spirits") -> dict:
    if not RUNTIME.exists():
        pytest.skip("先执行 uv sync --project sandbox-runtime --frozen")
    payload = {
        "code": code,
        "dataset_ids": [allowed_id],
        "dataset_paths": {allowed_id: str(data_file)},
        "limits": {
            "cpu_seconds": 4,
            "memory_bytes": 512 * 1024 * 1024,
            "temp_bytes": 16 * 1024 * 1024,
            "raw_output_bytes": 64 * 1024,
            "model_output_chars": 20_000,
            "max_records": 200,
            "max_depth": 8,
            "max_pids": 16,
            "enforce_nproc": False,
        },
    }
    completed = subprocess.run(
        [str(RUNTIME), "-I", str(RUNNER)],
        input=json.dumps(payload, ensure_ascii=False).encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    assert completed.stderr == b""
    return json.loads(completed.stdout)


def test_runner_normalizes_dataframe_numpy_nan_and_record_limit(tmp_path: Path):
    data = tmp_path / "full_spirits.json"
    data.write_text(json.dumps([{"n": i} for i in range(205)]), encoding="utf-8")
    result = _run(
        "rows=load_dataset('full_spirits')\n"
        "frame=pd.DataFrame(rows)\n"
        "frame.loc[0, 'n']=np.nan\n"
        "emit_result(frame)",
        data,
    )
    assert result["ok"] is True
    assert len(result["result"]) == 200
    assert result["result"][0]["n"] is None
    assert result["truncated"] is True


def test_runner_suppresses_user_stdout_and_exception_details(tmp_path: Path):
    data = tmp_path / "full_spirits.json"
    data.write_text("[]", encoding="utf-8")
    result = _run(
        "print('SECRET_STDOUT')\nraise RuntimeError('/private/secret')\nemit_result(1)",
        data,
    )
    encoded = json.dumps(result)
    assert result == {"ok": False, "error_code": "sandbox_execution_error"}
    assert "SECRET" not in encoded and "/private" not in encoded


def test_runner_requires_exactly_one_runtime_emit(tmp_path: Path):
    data = tmp_path / "full_spirits.json"
    data.write_text("[]", encoding="utf-8")
    result = _run("emit_result(1)\nemit_result(2)", data)
    assert result["error_code"] == "sandbox_output_invalid"
    missing = _run("value = 1", data)
    assert missing["error_code"] == "sandbox_output_invalid"


def test_runner_denies_ungranted_dataset_id(tmp_path: Path):
    data = tmp_path / "full_spirits.json"
    data.write_text("[]", encoding="utf-8")
    result = _run("emit_result(load_dataset('full_skills'))", data)
    assert result["error_code"] == "sandbox_execution_error"


def test_runner_normalizes_series_array_and_numpy_scalar(tmp_path: Path):
    data = tmp_path / "full_spirits.json"
    data.write_text("[]", encoding="utf-8")
    result = _run(
        "emit_result({'series':pd.Series([1,2]),"
        "'array':np.array([3,4]),'scalar':np.int64(5)})",
        data,
    )
    assert result == {
        "ok": True,
        "result": {"series": [1, 2], "array": [3, 4], "scalar": 5},
        "truncated": False,
    }


@pytest.mark.parametrize(
    "code,error_code",
    [
        ("emit_result([[[[[[[[[1]]]]]]]]])", "sandbox_output_invalid"),
        ("emit_result({'unsupported':set([1])})", "sandbox_output_invalid"),
        ("emit_result('x'*21000)", "sandbox_output_too_large"),
        ("print('x'*70000)\nemit_result(1)", "sandbox_output_too_large"),
    ],
)
def test_runner_rejects_invalid_or_oversized_output(
    tmp_path: Path, code: str, error_code: str,
):
    data = tmp_path / "full_spirits.json"
    data.write_text("[]", encoding="utf-8")
    result = _run(code, data)
    assert result["error_code"] == error_code
