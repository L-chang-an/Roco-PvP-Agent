"""pytest 共享夹具。

M1：agent_settings（完全离线）；fake LLM 类放在 tests/fakes.py。
"""

import pytest

from roco_pvp_agent.config import Settings


@pytest.fixture
def agent_settings() -> Settings:
    """完全离线的 Settings：无 key、指向无效 base_url，杜绝误触网络。"""
    return Settings(api_key="", base_url="http://test.invalid", model="test-model", timeout=5.0)
