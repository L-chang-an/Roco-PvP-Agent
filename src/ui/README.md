# ui — Web 界面（FastAPI + SSE）

本目录是 Web 界面：聊天（组队顾问）、组队页、对战页、观战流。依赖 `rock_pvp_agent` 与 `environment`（绝对导入）。

## 模块

| 模块 | 职责 |
|---|---|
| `server.py` | FastAPI 应用工厂 `create_chat_app`：REST + SSE 聊天（meta → tool → reply → done 事件契约） |
| `context.py` | `ChatContext`：按 session 隔离的聊天上下文（LRU 逐出） |
| `battle.py` | `BattleController`：人类 vs LLM 的对战编排（含迷雾、补位、第 0 回合首发） |
| `routes_battle.py` | `/api/battle/*`：对战 REST 契约（开局/出招/补位/重放/观战 SSE） |
| `routes_team.py` | `/api/team/*`：组队页（精灵搜索 + 校验 + 队伍持久化） |
| `static/` | 前端静态资源（HTML/CSS/JS） |
| `__main__.py` | `run_ui`（uvicorn 启动入口） |

## 关键设计

- **SSE 事件契约**：`meta → thinking* → tool* → reply → done`（顾问模式关闭 thinking）。
- **应用工厂 + 测试注入缝**：`create_chat_app(settings, llm_factory=...)`，测试用 fake LLM 零网络。
- **路径安全**：组队/对战记录路径必须落在对应目录内（防 `..` 逃逸）。
- **脱敏**：`/api/config` 绝不返回 `api_key`。

## 运行

```bash
uv sync --all-extras
python -m rock_pvp_agent --serve      # 或 python -m ui
```
