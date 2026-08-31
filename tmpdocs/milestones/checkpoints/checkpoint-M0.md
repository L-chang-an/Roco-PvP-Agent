# 里程碑 M0 检查点

日期：2026-08-23　状态：**☑ 通过**

## 目标
初始化项目骨架：`uv sync` 即可复现环境、`python -m rock_pvp_agent` 可运行、pytest 有冒烟测试的干净空壳。

## 我做了什么
| 文件 | 改动 |
|---|---|
| `pyproject.toml` | hatchling 构建，`rock-pvp-agent` 0.1.0，依赖 + ui/dev extras，pytest 配置 |
| `.python-version` | 3.12 |
| `.gitignore` | 忽略 .venv/__pycache__/.env 等（用户补充忽略 mydocs/） |
| `.env.example` | LLM 配置模板 |
| `README.md` / `docs/README.md` | 空壳 + 文档索引 |
| `src/rock_pvp_agent/__init__.py` | `__version__ = "0.1.0"` |
| `src/rock_pvp_agent/__main__.py` | CLI 占位（`--version`） |
| `tests/conftest.py` / `tests/test_smoke.py` | 空桩 + 2 条冒烟 |
| `mydocs/rebuild-plan.md` | 计划文档（命名固化为 rock_pvp_agent） |

## 验收命令与结果
- `uv sync --all-extras` → 依赖安装成功 ✅
- `uv run python -c "import rock_pvp_agent; print(rock_pvp_agent.__version__)"` → `0.1.0` ✅
- `uv run python -m rock_pvp_agent --version` → `rock_pvp_agent 0.1.0` ✅
- `uv run pytest -q` → `2 passed` ✅

## 用户的疑问 / 修改要求
- 提问 pyproject.toml 作用与自动更新方法 → 已讲解（uv add 系列命令）
- 提问 pytest 选型与更友好的测试/调试工具 → 已讲解（pytest 开关 / breakpoint / VSCode / rich）
- 无修改要求

## 记录
- `.env.example` 受权限规则拦截，改用 shell 创建。
- 用户选择包名 `rock_pvp_agent`、Python 3.12。
- git 首次提交 `cc3d96c feat: init project skeleton`。

## 下一步
M1 最小 chat 核心（无 UI）：config→prompts→llm→ChatAgent→tools→CLI REPL。
