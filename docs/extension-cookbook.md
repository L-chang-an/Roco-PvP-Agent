# 扩展手册（Extension Cookbook）

> 本手册回答"我想给 Rock PVP Agent 加 X，改哪里？"。前提是理解两条铁律：
> 1. **无循环 import**：`ui/` 依赖核心层（`agent/config/llm/tools`），核心层绝不反向依赖展示层。
> 2. **宁失败不抛**：所有外部调用出错都降级为错误文本/None/[]，绝不把异常抛给上层。

---

## 1. 加一个新工具

**最常用的扩展，agent 核心零改动。**

改 `src/rock_pvp_agent/tools.py` 的 `build_agent_tools()`：

```python
def build_agent_tools():
    @tool
    def calculator(expression: str) -> str:
        """计算数学表达式并返回字符串结果。"""
        return _safe_eval(expression)

    @tool
    def final_answer(text: str) -> str:
        """输出最终答案给用户。确定可以回答时，必须调用本工具。"""
        return text

    @tool
    def now_time() -> str:                      # ← 新工具
        """返回当前日期时间。"""
        return datetime.now().isoformat()

    return [calculator, final_answer, now_time]
```

- 工具在 `build_agent_tools()` 内**闭包构造**，实例级、不污染全局。
- 参数用类型注解，`bind_tools` 会自动生成 JSON Schema 给模型。
- 返回值必须是 `str`（agent 统一 `str(result)` 兜底）。

**配套动作**：
- `tests/test_tools.py` 加用例（正确输入 / 非法输入）。
- `tests/test_agent.py` 可加一条"新工具被调用后出终稿"的用例。
- 模型是否会用新工具，靠系统提示词引导；`prompts.py` 的 `CHAT_SYSTEM_PROMPT` 第 2 条已声明"信息不足或需要计算时调用可用工具"。

**注意**：`build_chat_llm` 的缓存 key 包含工具名集合，工具集合变化会自动重建 LLM，无需手动清缓存。

---

## 2. 换 LLM 后端

**换 OpenAI 兼容网关（最常见）**：只改 `.env`，零代码。

```env
LLM_API_KEY=你的key
LLM_MODEL=你的模型名
LLM_BASE_URL=https://你的网关地址      # 自动补 /v1；也接受完整 /chat/completions
```

**换一家非 OpenAI 兼容的提供商**：改 `src/rock_pvp_agent/llm.py` 的 `build_chat_llm()`，在 `ReasoningChatOpenAI` 分支旁加一个新分支（例如 Anthropic、本地 Ollama）。接口契约只需两点：

1. `.invoke(messages) -> 带 content / tool_calls / additional_kwargs 的对象`
2. `.bind_tools(tools)`（或 agent 侧等价地手工把 tool schema 塞进消息）

**适配注意事项**：
- 如果你的提供商没有 `reasoning_content`，`ReasoningChatOpenAI._create_chat_result` 的捞回逻辑自动不生效，不影响正确性。
- 历史重放铁律跨提供商通用：**每个 `tool_use` 必须紧跟 `tool_result`**，否则网关 400（M1 踩过的坑）。

---

## 3. 会话持久化（内存 → sqlite）

当前 `ChatContext`（`src/rock_pvp_agent/ui/context.py`）用 `OrderedDict` 存会话历史，LRU 上限 64，重启即丢。

**改法**：`ChatContext` 对外接口已稳定，把存储层从 dict 换成 sqlite 即可，**上层（server/前端）零改动**：

```python
class ChatContext:
    def __init__(self, agent):
        self._agent = agent
        self._conn = sqlite3.connect("sessions.db")   # 建表 sessions(session_id, seq, type, content, tool_call_id)

    def _load_history(self, session_id):  # SELECT ... ORDER BY seq
    def chat(self, message, session_id, *, event_sink=None):
        history = self._load_history(session_id)
        reply = self._agent.chat(message, history=history, event_sink=event_sink)
        self._save_history(session_id, reply.history)  # INSERT 新轮次
        return reply

    def reset(self, session_id):  # DELETE FROM sessions WHERE session_id=?
    def get_history(self, session_id):  # SELECT 重放
```

注意历史消息是 langchain 消息对象，持久化时需按类型序列化（`HumanMessage/AIMessage/ToolMessage` + `tool_call_id`），读回时反序列化。可写一个 `_serialize/_deserialize` 辅助，参照 `server.py` 的 `_serialize_history`。

---

## 4. 加新模式（battle / team）

参考项目有 chat/team/battle/ai-battle 四模式。目前只有 chat mode，加新模式的骨架：

1. **新增工具集**：在 `tools.py` 加 `build_battle_tools()`（复用 `calculator`、加模式特有工具）。
2. **新增 agent 逻辑**：battle 是两个角色对弈，通常需要两个 `ChatAgent`（各自系统提示词/工具集），外加一个仲裁器（决定胜负、轮流）。可新增 `src/rock_pvp_agent/battle.py`，**只 import 核心层**（agent/llm/tools/config），不依赖 `ui/`。
3. **CLI 入口**：`__main__.py` 的 argparse 加 `battle` 子命令分支，复用 `ChatAgent` 的 `event_sink` 协议。
4. **Web 接入**：如果 battle 也要 UI，沿用 SSE 事件协议（`thinking/tool/reply/done`），在 `server.py` 加一个 `/api/battle/stream` 路由即可，前端可复用现有渲染。

**约束**：battle 模式绝不改 chat mode 的核心逻辑；事件协议是公共契约，新模式的中间过程同样走 `thinking/tool` 事件进可折叠卡片。

---

## 5. 引入游戏数据域（environment）

参考项目的游戏数据域（SQLite/TeamBuilder/意图识别）**暂未实现**。日后引入时：

- 新增 `src/environment/` **同级包**（延迟拆分，M0 决策）。
- 铁律：`environment` 永不 import `rock_pvp_agent` 的 agent 层；agent 侧加一个 **facade** 消费 environment 的数据（如 `TeamBuilder` 提供 `query_team_stats()`）。
- facade 以工具形式暴露给模型（见第 1 节"加工具"），模型无需知道数据在哪。

---

## 6. 前端扩展

前端是**纯静态文件**（`src/rock_pvp_agent/ui/static/`），改完即生效、无构建步骤。改完记得同步 `pyproject.toml` 的 wheel `force-include` 已覆盖整个 `ui/static` 目录，构建产物自动带上。

| 想做的事 | 改哪里 |
|---|---|
| 调整配色/布局 | `style.css` 的 `:root` CSS 变量（`--bg`/`--accent` 等），全局一处生效 |
| 加新的事件渲染 | `chat.js` 的 `onEvent()` switch 加一个 case |
| 改打字机速度/效果 | `chat.js` 的 `renderReply()` 里 `setTimeout(tick, 8)` |
| 加 Markdown 语法 | `app.js` 的 `renderMarkdown()` / `inlineMd()` |
| 加用户头像/时间戳 | `chat.js` 的 `createBubble()` |
| 需要构建工具 | 不建议引入——零依赖、内网可用是当前设计目标，需要时再评估 |

**前端安全铁律**：模型输出永远先 `escapeHtml` 再进 DOM，`renderMarkdown()` 内部已保证"先转义后转换"，新增渲染分支也要守住这条。

---

## 7. 扩展事件协议（异步 / 权限确认）

事件协议是**前后端契约**：`meta → thinking* → tool* → reply → done`。当前预留了两个扩展位：

- **异步并发工具**：`agent._invoke_tool(tools_map, call)` 是顺序执行的，接口已按 `(name, args, tool_call_id)` 解耦。要并发时，改成 `concurrent.futures` 并行执行一轮里的多个 `tool_calls`，事件顺序需加序号或按 tool_call_id 关联。
- **权限确认**：M1 预留 `{"event":"tool_confirm", ...}`。实现时：agent 在执行危险工具前发 `tool_confirm` 事件，CLI 用 `input()`、前端弹确认框；收到用户确认后才执行工具、发 `tool` 事件。

改协议 = 改三处：`agent.py`（发射）、`ui/static/chat.js`（消费）、`tests/test_agent.py` + `tests/test_agent_ui.py`（契约）。

---

## 8. 测试怎么跟着扩展

测试全部走 **fake LLM（鸭子类型）**，零网络：

```python
# tests/fakes.py
class ScriptedLLM:
    """按顺序返回预设回复。"""
    def __init__(self, replies): self.replies = list(replies)
    def invoke(self, messages): return self.replies.pop(0)
```

- **加工具** → `tests/test_tools.py` 加求值/边界用例；agent 行为用例给 `ScriptedLLM` 塞一条带该工具 `tool_calls` 的 `AIMessage`。
- **加模式** → 新 `tests/test_battle.py`，同样构造 fake LLM 钉死对弈逻辑。
- **改存储** → `tests/test_agent_ui.py` 的 TestClient 契约不变，换存储后应全绿（接口稳定是换存储的前提）。
- **覆盖率门禁**：M2 定 `≥90%`，当前 94%。新代码上线前跑 `uv run pytest --cov=rock_pvp_agent -q`。

---

## 9. 常见坑速查

| 坑 | 解法 |
|---|---|
| 网关 400：tool_use 无 tool_result | 每个 tool_use 必须紧跟 ToolMessage 回填，包括 `final_answer`（agent.py 已保证，别删） |
| 思维链丢失 | `reasoning_content` 会被通用 ChatOpenAI 丢弃；保留 `ReasoningChatOpenAI` 子类（llm.py） |
| `hidden` 属性被 CSS 覆盖 | 折叠控件用 `.open` class 切换，别依赖 `hidden`（style.css 的 `.reason-body` 注释） |
| 新工具模型不用 | 检查系统提示词是否提及；工具参数要带类型注解 |
| 改事件协议前端没同步 | 协议是契约，agent/server/chat.js/测试四处必须一起改 |
| `.env` 被提交 | `.gitignore` 已忽略；`/api/config` 脱敏不含 key，别往里面加 |
