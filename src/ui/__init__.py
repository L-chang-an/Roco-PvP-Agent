"""顶层 `ui` 包（原 roco_pvp_agent.ui，2026-08-25 提级）：聊天/组队 Web UI。

- 会话上下文 ChatContext + FastAPI 路由（SSE）+ 纯静态前端。
- 组队模式：routes_team.py（/api/team/*）→ 精灵搜索、技能池、校验、队伍持久化。
- 依赖方向：`ui` → 消费 `roco_pvp_agent`（agent/config）与 `environment`（数据/组队/规则）；
  `environment` 永不反向依赖 `ui` 或 `roco_pvp_agent`。
"""
