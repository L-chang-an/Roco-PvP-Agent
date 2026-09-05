"""纯 AST 前置减面策略；操作系统沙箱仍是最终安全边界。"""

from __future__ import annotations

import ast

from .catalog import DATASET_FILES
from .models import PermissionAction, PermissionDecision, SandboxHealth


ALLOWED_IMPORTS = frozenset({
    "game_data", "numpy", "pandas", "json", "math", "statistics", "collections",
    "itertools", "functools", "re", "decimal", "fractions", "datetime",
})
_BANNED_CALLS = frozenset({
    "open", "eval", "exec", "compile", "__import__", "input", "breakpoint",
    "getattr", "setattr", "delattr", "globals", "locals", "vars",
})
_BANNED_ATTRS = frozenset({
    "system", "popen", "spawn", "fork", "forkpty", "execv", "execve", "kill",
    "remove", "unlink", "rmdir", "rename", "replace", "chmod", "chown",
    "read_csv", "read_fwf", "read_table", "read_json", "read_pickle", "read_html",
    "read_xml", "read_sql", "read_excel", "read_hdf", "read_parquet", "read_feather",
    "read_orc", "read_sas", "read_spss", "read_stata", "read_clipboard",
    "to_pickle", "to_csv", "to_json", "to_excel", "to_sql", "to_parquet",
    "to_feather", "to_hdf", "to_orc", "to_stata", "to_clipboard",
    "load", "save", "savez", "savez_compressed", "loadtxt", "savetxt", "genfromtxt",
    "fromfile", "tofile", "memmap",
})


class SandboxPermissionPolicy:
    def __init__(self, *, max_ast_nodes: int = 2_000) -> None:
        self._max_ast_nodes = max_ast_nodes

    def evaluate(
        self,
        code: str,
        dataset_ids: tuple[str, ...],
        health: SandboxHealth,
    ) -> PermissionDecision:
        if not health.enabled or not health.healthy:
            return self._deny("SBX-001", "sandbox_backend_unavailable")
        if any(dataset_id not in DATASET_FILES for dataset_id in dataset_ids):
            return self._deny("SBX-002", "dataset_not_registered")
        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError:
            return self._deny("SBX-003", "code_parse_failed")
        nodes = list(ast.walk(tree))
        if len(nodes) > self._max_ast_nodes:
            return self._deny("SBX-004", "ast_node_limit_exceeded")

        emit_calls = 0
        for node in nodes:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".", 1)[0] not in ALLOWED_IMPORTS:
                        return self._deny("SBX-005", "module_not_allowed")
            elif isinstance(node, ast.ImportFrom):
                if node.level or not node.module \
                        or node.module.split(".", 1)[0] not in ALLOWED_IMPORTS:
                    return self._deny("SBX-005", "module_not_allowed")
                for alias in node.names:
                    if alias.name.startswith("__") or alias.name in _BANNED_ATTRS \
                            or alias.name in _BANNED_CALLS:
                        return self._deny("SBX-007", "dangerous_import_denied")
            elif isinstance(node, ast.Attribute):
                if node.attr.startswith("__") or node.attr.endswith("__"):
                    return self._deny("SBX-006", "dunder_access_denied")
                if node.attr in _BANNED_ATTRS:
                    return self._deny("SBX-007", "dangerous_attribute_denied")
            elif isinstance(node, ast.Name):
                if node.id.startswith("__") or node.id in _BANNED_CALLS:
                    return self._deny("SBX-006", "dangerous_name_denied")
            elif isinstance(node, ast.Call):
                name = self._call_name(node.func)
                if name == "emit_result":
                    emit_calls += 1
                if name in _BANNED_CALLS or name.rsplit(".", 1)[-1] in _BANNED_ATTRS:
                    return self._deny("SBX-007", "dangerous_call_denied")

        if emit_calls != 1:
            # 这是可修复的普通代码契约错误，仍用策略规则在 handler 前拒绝。
            return self._deny("SBX-008", "emit_result_must_appear_once")
        return PermissionDecision(PermissionAction.ALLOW, "SBX-100", "readonly_query_allowed")

    @staticmethod
    def _call_name(node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = SandboxPermissionPolicy._call_name(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        return ""

    @staticmethod
    def _deny(rule_id: str, reason: str) -> PermissionDecision:
        return PermissionDecision(PermissionAction.DENY, rule_id, reason)


__all__ = ["ALLOWED_IMPORTS", "SandboxPermissionPolicy"]
