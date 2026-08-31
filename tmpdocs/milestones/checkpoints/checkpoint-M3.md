# 里程碑 M3 检查点

日期：2026-08-23　状态：**☑ 通过**（含 2 次功能迭代）

## 目标
Web UI（SSE 流式对话）：浏览器流式对话 + 会话历史/重置 + 无 key 离线降级 + 纯静态前端（零构建）；并迭代：折叠思考卡、token 统计、Markdown 渲染双视图。

## 我做了什么
| 文件 | 改动 |
|---|---|
| `ui/context.py` | ChatContext：按 session 隔离历史，共享一个无状态 ChatAgent，LRU 上限 64，线程安全 |
| `ui/server.py` | create_chat_app 应用工厂（llm_factory 注入缝）+ REST/SSE 路由 + extra="forbid" |
| `ui/__main__.py` | run_ui()：uvicorn 起在 127.0.0.1:8001（ROCK_UI_HOST/PORT 可覆盖） |
| `ui/static/index.html` | 聊天页骨架（极简黑风格，后续可随时调） |
| `ui/static/chat.js` | SSE 消费 / 折叠思考卡 / 打字机 / Markdown 双视图 / POST 兜底 |
| `ui/static/app.js` | DOM 工具 + 零依赖 Markdown 渲染器（先转义防 XSS） |
| `ui/static/style.css` | 极简黑样式 + 折叠卡 + token 统计 + Markdown 排版 |
| `src/rock_pvp_agent/__main__.py` | --serve 从占位改为真启动 |
| `src/rock_pvp_agent/agent.py` | ChatReply 加 usage；reasoning_content 捕获为 thinking；_content_text 兼容 thinking 块 |
| `src/rock_pvp_agent/llm.py` | ReasoningChatOpenAI 子类：透传网关 reasoning_content（通用版会丢弃） |
| `tests/test_agent_ui.py` | 14 个 REST/SSE 契约测试（TestClient + fake LLM，零网络） |
| `pyproject.toml` | 静态文件进 wheel（force-include） |

## 三次功能迭代（用户驱动）
1. **基础 Web UI**：SSE 流式 `meta→thinking*→tool*→reply→done` + 会话管理 + 离线降级。
2. **折叠思考卡 + token 统计**：
   - 修 CSS bug：`.reason-body` 的 `display:flex` 覆盖了 `hidden` 属性 → 改用 `.open` class 切换，默认真正折叠。
   - 思维链缺失：诊断发现模型思维链在 `reasoning_content` 字段，langchain 通用 `ChatOpenAI` 明确丢弃它 → 子类 `ReasoningChatOpenAI` 在 `_create_chat_result` 捞回 `additional_kwargs`，agent 提取为 thinking 事件。真实网关验证事件序列变为 `meta→thinking→tool→reply→done`。
   - token 统计：累加每次响应的 `usage_metadata`，reply 事件与同步接口携带 usage，前端显示"⚡ tokens 输入/输出/总计"。
3. **Markdown 渲染双视图**：默认渲染 + 原文视图 + 复制按钮；零依赖手写渲染器（先转义防 XSS，行内代码抽出保护防二次渲染）。

## 验收命令与结果
- `uv run pytest --cov=rock_pvp_agent -q` → **84 passed / 94%** ✅
- 真实 SSE（真实 key）：`meta→thinking→tool→reply→done`，usage 1308/138/1446 ✅
- 同步 POST /api/chat → 含 usage ✅
- 静态资源 200 ✅；历史接口串出完整会话 ✅
- Markdown 渲染器 node 单测 12 项全过（含 XSS 转义、行内代码保护）✅

## 记录
- **思维链在哪**：OpenAI 兼容网关联通推理文本放在 `reasoning_content` 字段；langchain 通用 `ChatOpenAI` 丢弃之（其文档字符串明确声明）。解法：子类化覆盖 `_create_chat_result` 捞回。
- **CSS hidden 陷阱**：`hidden` 属性（display:none）会被作者样式的 `display:flex` 覆盖——折叠控件要用显式 class 切换。
- **行内代码保护**：`` `**x**` `` 里不能渲染 markdown——行内代码先抽占位符再还原。
- 前端渲染器零依赖手写，内网可用；先转义后转换保证 XSS 免疫。

## 下一步
M4 收尾：README 完整化 / 协作协议成文 / 扩展手册 / CHANGELOG / 冷启动验证 / v0.1.0 tag。
