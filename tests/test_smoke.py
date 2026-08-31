"""M0 冒烟测试：包可导入、版本号正确。"""

import re
from pathlib import Path

import roco_pvp_agent


def test_import_package() -> None:
    assert roco_pvp_agent is not None


def test_version() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", roco_pvp_agent.__version__)
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    assert f'version = "{roco_pvp_agent.__version__}"' in pyproject.read_text()
