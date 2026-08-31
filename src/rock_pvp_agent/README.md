# rock_pvp_agent — M 线：ChatAgent + 组队顾问（入口）

本目录是 Agent 层的入口：一个无状态的工具循环 `ChatAgent`，以及它之上的**组队顾问** `TeamAdvisorAgent`。

## 模块

| 模块 | 职责 |
|---|---|
| `agent.py` | `ChatAgent`：无状态工具循环（在线 LLM / 离线降级），支持工具集注入、多终结工具、思维链外显开关 |
| `advisor/` | 组队顾问底座（详见 [advisor/README.md](advisor/README.md)） |
| `battle/` | 对战编排 + 自博弈轨迹 + 进化管线（详见 [battle/README.md](battle/README.md)） |
| `config.py` | `Settings`（模型 / API key / base_url，默认 deepseek-chat） |
| `llm.py` | `build_chat_llm`（langchain-openai，带实例缓存） |
| `tools.py` | 基础工具（`final_answer` 终结 + `echo` 演示） |
| `__main__.py` | CLI 入口（chat / selfplay / evolve 子命令） |

## 两种 Agent

- **`ChatAgent`**：通用工具循环基类。工具集、终结工具名、思维链外显都可注入，默认行为是「只有 `final_answer` 的基础助手」。
- **`TeamAdvisorAgent`**：组队顾问，替换了基础助手成为默认入口。它在 `chat()` 顶部先过 ScopeGate（越界/注入/模糊/问候走固定模板不进 LLM），组队问题才走结构化流程（catalog → 轨迹 → analyze → simulate → `submit_team_advice`）。

## 快速开始

```bash
python -m rock_pvp_agent -q "帮我组个克制水系的三精灵队"   # 单发
python -m rock_pvp_agent                                    # REPL
python -m rock_pvp_agent --serve                            # Web UI
```

未配置 `LLM_API_KEY` 时自动降级为离线回复（`agent.py` 的 `_offline_chat`）。

详见 `tmpdocs/milestones/chatmode/` 下的 M 线设计与详设文档。
