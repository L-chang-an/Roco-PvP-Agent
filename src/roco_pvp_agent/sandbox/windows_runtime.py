"""Provision/verify an application-owned, relocatable Windows runtime.

Run only during deployment: python -m roco_pvp_agent.sandbox.windows_runtime
--source sandbox-runtime/.venv/Scripts/python.exe --destination <new directory>.
No packages are installed or updated while serving a query.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import tempfile
import uuid
from pathlib import Path

from .backends.base import runner_path
from .runtime import RuntimeUnavailable, inspect_runtime

MANIFEST = "roco-runtime.json"


def _files(root: Path) -> dict[str, str]:
    from .backends.win32 import open_verified_file, reject_reparse_path
    output = {}
    for path in sorted(root.rglob("*")):
        reject_reparse_path(path)
        if path.is_file() and path != root / MANIFEST:
            with open_verified_file(path) as stream:
                digest = hashlib.sha256()
                while block := stream.read(1024 * 1024):
                    digest.update(block)
            output[path.relative_to(root).as_posix()] = digest.hexdigest()
    return output


def verify_runtime(python: Path) -> dict:
    if not python.is_absolute() or python.name.lower() != "python.exe":
        raise RuntimeUnavailable("windows_runtime_not_prepared")
    try:
        from .backends.win32 import open_verified_file
        with open_verified_file(python.parent / MANIFEST) as stream:
            manifest = json.load(stream)
        if (manifest.get("format") != 1 or manifest.get("architecture") != "x64"
                or manifest.get("versions") != {"python": "3.12", "numpy": "2.4.2", "pandas": "3.0.1"}
                or _files(python.parent) != manifest["files"]):
            raise ValueError("manifest_mismatch")
        # These files are trusted code, so also bind them to the installed host
        # version, rather than accepting a stale deployment's local manifest.
        expected = {"runner.py": runner_path(), "_win32.py": Path(__file__).parent / "backends" / "win32.py"}
        for name, source in expected.items():
            if manifest["files"][name] != hashlib.sha256(source.read_bytes()).hexdigest():
                raise ValueError("runner_version_mismatch")
        return manifest
    except (OSError, ValueError, KeyError, TypeError):
        raise RuntimeUnavailable("windows_runtime_integrity_failed") from None


def prepare_runtime(source: Path, destination: Path) -> Path:
    if os.name != "nt" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeUnavailable("windows_x64_required")
    from .backends.win32 import api, reject_reparse_path
    source, destination = source.absolute(), destination.absolute()
    reject_reparse_path(source)
    reject_reparse_path(destination.parent)
    if destination.exists():
        raise ValueError("destination_exists: choose a new directory; stop users before replacing a runtime")
    runtime = inspect_runtime(source)
    win = api()
    capability_name = "roco.runtime." + uuid.uuid4().hex
    capability_sid = win.capability_sid(capability_name)
    stage = Path(tempfile.mkdtemp(prefix=".windows-runtime-", dir=destination.parent))
    try:
        win.set_acl(stage)
        # A venv's Windows python.exe is a redirector. Copy the base interpreter
        # instead, with a private standard library and locked site-packages.
        for path in runtime.base_prefix.iterdir():
            if path.is_file() and (path.suffix.lower() in {".dll", ".zip"}
                                   or path.name.lower() == "python.exe"):
                reject_reparse_path(path)
                shutil.copyfile(path, stage / path.name)
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "site-packages", "test", "tests")
        for name in ("Lib", "DLLs"):
            origin = runtime.base_prefix / name
            if origin.is_dir():
                reject_reparse_path(origin)
                shutil.copytree(origin, stage / name, ignore=ignore)
        shutil.copytree(runtime.purelib, stage / "Lib" / "site-packages",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copyfile(runner_path(), stage / "runner.py")
        shutil.copyfile(Path(__file__).parent / "backends" / "win32.py", stage / "_win32.py")
        # Check relocation without site startup hooks or bytecode writes.
        versions = inspect_runtime(stage / "python.exe", standalone=True)
        if versions.prefix != stage or versions.base_prefix != stage:
            raise RuntimeUnavailable("windows_runtime_not_relocatable")
        # Probe may have produced pyc files; these are included and read-only.
        manifest = {"format": 1, "architecture": "x64", "capability_name": capability_name,
                    "versions": {"python": "3.12", "numpy": "2.4.2", "pandas": "3.0.1"},
                    "files": _files(stage)}
        (stage / MANIFEST).write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
        for path in stage.rglob("*"):
            win.set_acl(path, capability_sid, execute=True)
        win.set_acl(stage, capability_sid, execute=True)
        stage.rename(destination)
        return destination / "python.exe"
    finally:
        if stage.exists():
            # Only our freshly-created staging directory; never follow links.
            reject_reparse_path(stage)
            shutil.rmtree(stage)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(prepare_runtime(args.source, args.destination))


if __name__ == "__main__":
    main()
