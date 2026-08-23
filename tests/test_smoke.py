"""M0 冒烟测试：包可导入、版本号正确。"""

import rock_pvp_agent


def test_import_package() -> None:
    assert rock_pvp_agent is not None


def test_version() -> None:
    assert rock_pvp_agent.__version__ == "0.1.0"
