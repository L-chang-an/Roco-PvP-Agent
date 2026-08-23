# 里程碑 M2 检查点

日期：2026-08-23　状态：**☑ 通过**

## 目标
单元测试打磨：用 fake LLM（鸭子类型，零网络）钉死 M1 全部核心行为；引入 pytest-cov 覆盖率门禁（≥90%）。

## 我做了什么
| 文件 | 改动 |
|---|---|
| `tests/conftest.py` | agent_settings 夹具（全离线）+ 继续复用 |
| `tests/fakes.py` | EchoLLM / ScriptedLLM / AlwaysToolLLM + tool_call 构造助手 |
| `tests/test_llm.py` | 注入缝原样返回 / 注入绕过缓存 / normalize_base_url 7 例 / 模块级缓存身份 |
| `tests/test_agent.py` | 离线 3 例、显式终稿 3 例、工具循环 5 例、思考文本提取、事件契约 3 例、历史可重放 4 例 |
| `tests/test_tools.py` | calculator 正确求值 / 非法输入 / 白名单边界 / final_answer / 实例级工具 |
| `tests/test_cli.py` | --version / -q 单发 / --serve 占位 / --debug 打印 / REPL（空行+历史+退出）/ EOF 优雅退出 |
| `tests/test_agent_ui.py` | M3 占位（TestClient + SSE 契约） |
| `src/rock_pvp_agent/agent.py` | **修复**：空回复死循环（`if not calls` 即终稿） |
| `.gitignore` | 忽略 `.coverage` 产物 |
| `pyproject.toml` | 去重 dev 依赖，统一进 `[dependency-groups] dev`，加入 pytest-cov |

## 修复的两处真实缺陷（测试逼出）
1. **空回复死循环**：模型返回"空内容 + 无工具调用"时旧条件 `if content and not calls` 不成立 → 继续调 LLM 直到轮次耗尽。改为 `if not calls` 即视为终稿（空则 EMPTY_REPLY）。
2. **openai 凭据强制校验**：新 openai 客户端在 `ChatOpenAI` 构造时即校验非空 key，空 key 抛 `OpenAIError`。缓存测试改用 monkeypatch 假客户端测缓存逻辑，不建真实客户端。

## 验收命令与结果
- `uv run pytest -v` → **62 passed** ✅
- `uv run pytest tests/test_agent.py -k history -v` → 历史可重放 4 例全过 ✅
- `uv run pytest --cov=rock_pvp_agent -q` → **97%**（≥90% 目标达成）✅
- 剩余 8 行未覆盖均为不可测防御分支（`if __name__ == "__main__"` 守卫等）

## 记录
- REPL 测试最初死循环：`lambda prompt="": iter([...]).__next__()` 每次调用都新建迭代器、永远返回首元素。迭代器只建一次后修复——测试代码也要当心状态泄漏。
- 覆盖率门禁定为 ≥90%，当前 97%，为后续 battle 等模式扩展留足余量。

## 下一步
M3 Web UI（SSE）：ui/ 子包（context 会话管理 + FastAPI 路由 + SSE 流式 + 原生 JS 前端）+ `--serve` + TestClient 契约测试。
