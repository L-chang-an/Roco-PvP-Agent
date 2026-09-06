"""沙箱工具参数、数据授权与 AST 减面策略。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from roco_pvp_agent.sandbox.catalog import DATASET_FILES, DatasetCatalog, DatasetCatalogError
from roco_pvp_agent.sandbox.models import SandboxHealth
from roco_pvp_agent.sandbox.policy import SandboxPermissionPolicy
from roco_pvp_agent.sandbox.schema import SandboxPythonQueryArgs


HEALTHY = SandboxHealth(True, "macos", "macos", True)


def test_query_contract_is_strict_and_dataset_ids_are_unique():
    valid = SandboxPythonQueryArgs(
        code="emit_result(1)", dataset_ids=["full_spirits", "valid_skills"])
    assert valid.dataset_ids == ["full_spirits", "valid_skills"]

    invalid_payloads = [
        {"code": "emit_result(1)", "dataset_ids": ["full_spirits", "full_spirits"]},
        {"code": "", "dataset_ids": ["full_spirits"]},
        {"code": "emit_result(1)", "dataset_ids": ["missing"]},
        {"code": "emit_result(1)", "dataset_ids": ["full_spirits"], "path": "/tmp"},
        {"code": "x" * 16_001, "dataset_ids": ["full_spirits"]},
    ]
    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            SandboxPythonQueryArgs.model_validate(payload)


def test_all_eight_registered_datasets_resolve_from_packaged_root():
    resolved = DatasetCatalog().resolve(tuple(DATASET_FILES))
    assert set(resolved) == set(DATASET_FILES)
    assert all(path.is_file() for path in resolved.values())


def test_catalog_rejects_dataset_symlink(tmp_path: Path):
    outside = tmp_path / "outside.json"
    outside.write_text("[]", encoding="utf-8")
    root = tmp_path / "data"
    root.mkdir()
    try:
        (root / "full_spirits.json").symlink_to(outside)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink creation requires Developer Mode or privilege; junction is tested separately")
        raise
    with pytest.raises(DatasetCatalogError, match="symlink"):
        DatasetCatalog(root).resolve(("full_spirits",))


@pytest.mark.parametrize(
    "code,rule_id",
    [
        ("import os\nemit_result(1)", "SBX-005"),
        ("open('/etc/passwd')\nemit_result(1)", "SBX-007"),
        ("emit_result((1).__class__)", "SBX-006"),
        ("emit_result(pd.read_csv('/etc/passwd'))", "SBX-007"),
        ("emit_result(eval('1'))", "SBX-007"),
        ("emit_result(1)\nemit_result(2)", "SBX-008"),
        ("x = 1", "SBX-008"),
        ("emit_result(", "SBX-003"),
    ],
)
def test_policy_denies_unsafe_or_invalid_code_with_stable_rule(code: str, rule_id: str):
    decision = SandboxPermissionPolicy().evaluate(code, ("full_spirits",), HEALTHY)
    assert decision.allowed is False
    assert decision.rule_id == rule_id


def test_policy_allows_readonly_numpy_pandas_query():
    code = """\
from game_data import load_dataset, emit_result
import pandas as pd
rows = load_dataset('full_spirits')
frame = pd.DataFrame(rows)
emit_result({'count': len(frame)})
"""
    decision = SandboxPermissionPolicy().evaluate(code, ("full_spirits",), HEALTHY)
    assert decision.allowed is True
    assert decision.rule_id == "SBX-100"


def test_unhealthy_backend_is_a_hard_deny():
    health = SandboxHealth(True, "linux", "linux", False, "bubblewrap_missing")
    decision = SandboxPermissionPolicy().evaluate(
        "emit_result(1)", ("full_spirits",), health)
    assert decision.rule_id == "SBX-001"
