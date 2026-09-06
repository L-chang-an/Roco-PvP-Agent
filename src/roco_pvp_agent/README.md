# roco_pvp_agent — M 线：ChatAgent + 组队顾问（入口）

本目录是 Agent 层的入口：一个无状态的工具循环 `ChatAgent`，以及它之上的**组队顾问** `TeamAdvisorAgent`。

## 模块

| 模块 | 职责 |
|---|---|
| `agent.py` | `ChatAgent`：无状态工具循环（在线 LLM / 离线降级），支持工具集注入、多终结工具、墙钟预算和实时过程事件 |
| `advisor/` | 组队顾问底座（详见 [advisor/README.md](advisor/README.md)） |
| `battle/` | 对战编排 + 自博弈轨迹 + 进化管线（详见 [battle/README.md](battle/README.md)） |
| `config.py` | `Settings`（模型 / API key / base_url，默认 deepseek-chat） |
| `llm.py` | `build_chat_llm`（langchain-openai，带实例缓存） |
| `tools.py` | 基础工具与 `build_agent_registry`（`final_answer` 终结 + `echo` 演示） |
| `tooling/` | 工具注册、严格分发、安全并发、延迟发现与会话级可见性 |
| `__main__.py` | CLI 入口（chat / selfplay / evolve 子命令） |

## 两种 Agent

- **`ChatAgent`**：通用工具循环基类。工具 Registry、自定义工具和思维链外显都可注入，默认注册 `echo` 与 `final_answer`。
- **`TeamAdvisorAgent`**：组队顾问，替换了基础助手成为默认入口。它先过 ScopeGate，再按问题所需并行取证；完整配队走 `submit_team_advice` 硬闸，配招/克制/局部策略可用 `final_answer` 快速终结。默认最多 100 个模型轮次、约 555 秒墙钟预算。

CLI 和 Web SSE 都会实时展示安全的阶段摘要与工具调用。顾问不外显原始思维链；模型超时、限流或未按协议终结时，会返回已完成工具的阶段结果，而不是无限等待或只显示“达到最大轮数”。

Web 使用新的结构化 observer：每次真实模型调用生成独立 Round，并展示主模型同次响应
提供的 `round_summary`。思考摘要与确定性工具摘要分别呈现，不增加摘要模型调用。
旧 `event_sink` 与 `ChatReply` 字段继续兼容 CLI；`final_result` 提供显式结果类型。

工具 schema 不再默认全量塞入每轮请求：高频工具立即可见，低频分析、模拟、轨迹与记忆工具只以
紧凑目录出现。模型通过 `tool_search` 加载完整 schema 后，下一轮才能直接调用；加载状态由 CLI/Web
会话持有，不写入共享 Agent。只有整批均标记为 `concurrent_safe` 的只读工具才会并发执行，结果仍按
模型调用顺序回填。

运行时工具清单只有一个来源：基础 Agent 的 `build_agent_registry` 或顾问的
`_build_advisor_registry`。模型绑定、Dispatcher、延迟目录和 Skill 的 `allowed_tools` 裁剪均从同一
Registry 实例派生；项目不再维护平行的工具名常量或兼容工具数组。

## 快速开始

```bash
python -m roco_pvp_agent -q "帮我组个克制水系的三精灵队"   # 单发
python -m roco_pvp_agent                                    # REPL
python -m roco_pvp_agent --serve                            # Web UI
```

未配置 `LLM_API_KEY` 时自动降级为离线回复（`agent.py` 的 `_offline_chat`）。

详见 `tmpdocs/milestones/chatmode/` 下的 M 线设计与详设文档。
