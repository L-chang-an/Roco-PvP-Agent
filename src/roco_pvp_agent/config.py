"""配置：从环境变量 / .env 读取 LLM 相关设置。

优先级：环境变量 > .env 文件 > 默认值。
"""

import os
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field


class Settings(BaseModel):
    """运行配置。缺 key 也安全：离线降级路径可用。"""

    api_key: str = ""
    model: str = "deepseek-chat"
    base_url: str = ""  # 空则走 OpenAI 官方默认
    timeout: float = 60.0
    debug: bool = False
    chat_max_llm_rounds: int = Field(default=100, ge=1)
    chat_max_total_seconds: float = Field(default=555.0, ge=0)
    chat_db_path: str = "artifacts/chat/sessions.sqlite3"
    chat_max_concurrent_turns: int = Field(default=4, ge=1)

    # Chat Mode 只读查询沙箱。默认关闭；生产应显式指定后端与独立 CPython 3.12。
    sandbox_enabled: bool = False
    sandbox_backend: Literal["auto", "macos", "linux", "docker", "windows"] = "auto"
    sandbox_runtime_python: str = ""
    sandbox_max_concurrency: int = Field(default=2, ge=1, le=32)

    # R1 记忆库配置（默认全关：memory_enabled=False 时检索/更新全链路 no-op）。
    memory_enabled: bool = False
    memory_dir: str = "artifacts/mem"
    memory_embedder: str = "keyword"
    memory_delta: float = 0.5
    memory_k1: int = 10
    memory_lam: float = 0.5
    memory_k2: int = 3
    memory_alpha: float = 0.3
    memory_w_used: float = 0.3
    memory_counterfactual_m: int = 24

    # GlobalMem 配置（G1）：全局对局经验（占据原 Playbook 生态位）。
    # `globalmem_max_tokens` 是唯一的膨胀约束——它注入 system prompt 并随每回合重发，
    # 单局额外输入 ≈ 该值 × 回合数（详见 evolution/globalmem.py 的常量注释）。
    globalmem_dir: str = "artifacts/gm"
    globalmem_max_tokens: int = 400
    globalmem_delta: float = 0.5
    globalmem_lam: float = 0.5
    globalmem_top_k: int = 1
    globalmem_alpha: float = 0.3

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
        chat_max_llm_rounds=int(os.getenv("CHAT_MAX_LLM_ROUNDS", "100")),
        chat_max_total_seconds=float(os.getenv("CHAT_MAX_TOTAL_SECONDS", "555")),
        chat_db_path=os.getenv("CHAT_DB_PATH", "artifacts/chat/sessions.sqlite3"),
        chat_max_concurrent_turns=int(os.getenv("CHAT_MAX_CONCURRENT_TURNS", "4")),
        sandbox_enabled=os.getenv("SANDBOX_ENABLED", "").lower() in ("1", "true", "yes"),
        sandbox_backend=os.getenv("SANDBOX_BACKEND", "auto"),
        sandbox_runtime_python=os.getenv("SANDBOX_RUNTIME_PYTHON", ""),
        sandbox_max_concurrency=int(os.getenv("SANDBOX_MAX_CONCURRENCY", "2")),
    )


_settings = load_settings()


def get_settings() -> Settings:
    """模块级单例（进程内共享）。"""
    return _settings
