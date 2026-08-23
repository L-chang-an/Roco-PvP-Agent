"""配置：从环境变量 / .env 读取 LLM 相关设置。

优先级：环境变量 > .env 文件 > 默认值。
"""

import os

from dotenv import load_dotenv
from pydantic import BaseModel


class Settings(BaseModel):
    """运行配置。缺 key 也安全：离线降级路径可用。"""

    api_key: str = ""
    model: str = "deepseek-chat"
    base_url: str = ""  # 空则走 OpenAI 官方默认
    timeout: float = 60.0
    debug: bool = False

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key.strip())


def load_settings() -> Settings:
    """读取环境变量拼装 Settings；LLM_API_KEY 缺失时回退 OPENAI_API_KEY。"""
    load_dotenv()
    api_key = os.getenv("LLM_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
    return Settings(
        api_key=api_key,
        model=os.getenv("LLM_MODEL", "deepseek-chat"),
        base_url=os.getenv("LLM_BASE_URL", ""),
        timeout=float(os.getenv("LLM_TIMEOUT", "60")),
        debug=os.getenv("DEBUG", "").lower() in ("1", "true", "yes"),
    )


_settings = load_settings()


def get_settings() -> Settings:
    """模块级单例（进程内共享）。"""
    return _settings
