"""uvicorn 启动入口：python -m ui（默认 127.0.0.1:8001，环境变量可覆盖）。

原 `python -m rock_pvp_agent.ui` 已随提级改为 `python -m ui`；
`python -m rock_pvp_agent --serve` 仍可用（rock_pvp_agent.__main__ 懒加载本模块）。
"""

import os

HOST = os.environ.get("ROCK_UI_HOST", "127.0.0.1")
PORT = int(os.environ.get("ROCK_UI_PORT", "8001"))


def run_ui() -> int:
    # 懒加载：不装 ui extra 时，import 失败给友好提示而不是裸报错
    try:
        from .server import app
        import uvicorn
    except ImportError:
        print("Web UI 依赖未安装，请先运行：uv sync --all-extras")
        return 1
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_ui())
