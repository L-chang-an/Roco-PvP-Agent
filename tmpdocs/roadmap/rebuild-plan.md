# MySelfPlayAgent — 从零重建执行计划（人机协作版）

> **定位**：本文件是项目的"宪法"——从零重建的执行手册与协作契约。它回答四个问题：
> 1. 如何先从零初始化项目文件组织架构
> 2. 如何先实现 chat mode 的最小运行代码结构
> 3. 如何实现 chat mode 的 web ui
> 4. 如何进行人机协作，让用户一步步参与、成为项目主导者、随时扩展
>
> **原则**：用户是决策者，助手提供方案与利弊；每个里程碑一个 Gate，用户亲自运行验收命令、明确说"通过"后才进入下一步；每步可随时停下或回退。
>
> **执行状态**：**☑ 已定稿（M0–M4 全部通过，v0.1.0）**。各里程碑验收记录见下方各节"验收记录"与 `docs/checkpoints/`。

参考项目：`~/workspace/SelfPlayAgent`（Python + uv + FastAPI/SSE + 原生 JS 前端的 LLM agent，含 chat/team/battle/ai-battle 四模式）。

---

## 0. 技术栈与命名决策

| 层 | 选型 | 理由 |
|---|---|---|
| 包管理/构建 | `uv` + `hatchling` | 与参考一致；`uv sync` 一条命令可复现环境 |
| Python | `>=3.10,<3.14`（`.python-version` 写 3.12，uv 自动下载；备选 3.10） | 与参考一致 |
| LLM | `langchain-openai` + `langchain-core`（`ChatOpenAI.bind_tools` 做工具循环） | 已验证支持 DeepSeek/OpenAI 兼容网关 |
| 配置 | `pydantic` + `python-dotenv` | 轻量 |
| CLI | `argparse` + `rich` | REPL 交互体验 |
| Web | `fastapi` + `uvicorn` + SSE（`EventSource`） | 无 WebSocket、无 CORS 烦恼 |
| 前端 | 原生 HTML/JS/CSS，无构建步骤 | 零工具链 |
| 测试 | `pytest` + `pytest-asyncio`（`asyncio_mode="auto"`） | fake LLM 走鸭子类型，不 mock HTTP |

**命名**：发行名 `rock-pvp-agent`，源码目录 `src/rock_pvp_agent/`，CLI 入口 `python -m rock_pvp_agent`（已于 M0 Gate 拍板）。不用参考项目的顶层 `agent` 名（太通用，易与第三方包冲突）。**单包起步**，日后引入游戏数据域时再加 `src/environment/` 同级包（延迟拆分）。

---

## 1. 里程碑总览

| 里程碑 | 回答问题 | 产出 | 核心验收命令 | 状态 |
|---|---|---|---|---|
| **M0 初始化骨架** | Q1 | pyproject/.env/.gitignore/src 布局/tests 桩 | `uv sync` → `uv run pytest` | ☑ 通过 `cc3d96c` |
| **M1 最小 chat 核心（无 UI）** | Q2 | config→prompts→llm→ChatAgent→tools→CLI REPL | `python -m rock_pvp_agent -q "计算 3.5*4"` | ☑ 通过 `70a25bb` |
| **M2 单元测试** | Q2 质量保证 | fake LLM 夹具 + 全量测试 + 覆盖率门禁 | `uv run pytest -v` + `--cov` | ☑ 通过 `ef0aa07` |
| **M3 Web UI（SSE）** | Q3 | server/context/static 前端/`--serve` | 浏览器聊天 + `curl -N` 流式 + TestClient | ☑ 通过 `589a3ae` |
| **M4 收尾与扩展指南** | Q4 固化 | README/协作协议/扩展手册/mydocs 定稿 | 冷启动按 README 跑通 + v0.1.0 tag | ☑ 通过（见 M4 节） |

每里程碑结构：目标 → 文件清单 → 关键签名 → 验收命令 → 用户 Gate。

---

## Milestone 0 — 初始化项目骨架

**目标**：`uv sync` 即出可复现环境、`python -m rock_pvp_agent` 可运行、pytest 有冒烟测试的干净空壳。

**文件清单**（根目录 `~/workspace/MySelfPlayAgent/`）：
| 路径 | 用途 |
|---|---|
| `.gitignore` | 忽略 `.venv/`、`__pycache__/`、`*.env`、`.DS_Store`、`.pytest_cache/`、`dist/` |
| `.python-version` | uv 锁 Python 版本 |
| `pyproject.toml` | 元数据 + 依赖 + hatchling + pytest 配置 |
| `.env.example` | `LLM_API_KEY` / `LLM_MODEL` / `LLM_BASE_URL` / `LLM_TIMEOUT` / `DEBUG` |
| `README.md` | 空壳：一句话简介 + "构建中" |
| `docs/README.md` | 文档目录索引 |
| `mydocs/rebuild-plan.md` | 本计划文档（M0 写初版，M4 定稿） |
| `src/rock_pvp_agent/__init__.py` | `__version__ = "0.1.0"` |
| `src/rock_pvp_agent/__main__.py` | 最小 argparse 占位（打印 skeleton OK） |
| `tests/conftest.py` | 空 pytest 桩 |
| `tests/test_smoke.py` | 2 条冒烟：能 import、版本号存在 |

**关键签名**（pyproject.toml）：
```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "rock-pvp-agent"
version = "0.1.0"
requires-python = ">=3.10,<3.14"
dependencies = ["langchain-core", "langchain-openai", "pydantic>=2", "rich", "python-dotenv"]
[project.optional-dependencies]
ui  = ["fastapi", "uvicorn"]
dev = ["pytest", "pytest-asyncio", "respx"]

[tool.hatch.build.targets.wheel]
packages = ["src/rock_pvp_agent"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

**验收命令（用户亲自运行）**：
```bash
cd ~/workspace/MySelfPlayAgent
git init
uv sync && uv sync --extra ui --extra dev
uv run python -c "import rock_pvp_agent; print(rock_pvp_agent.__version__)"  # 打印 0.1.0
uv run pytest -q    # 2 passed
```

**用户 Gate（M0）**：审查 pyproject/.gitignore/.env.example，确认技术栈与命名；明确说"通过"→ 打 commit `feat: init project skeleton`。

**验收记录**：`uv sync --all-extras` ✅ · `import rock_pvp_agent` → `0.1.0` ✅ · `--version` ✅ · `uv run pytest -q` → 2 passed ✅。负责人确认通过，commit `cc3d96c`。

---

## Milestone 1 — 最小 chat mode 核心（无 UI）

**目标**：一条命令与真实 LLM 对话；calculator 工具跑通 ReAct 工具循环；无 key 时离线降级可用；CLI 支持 `-q` 单发 + 交互 REPL。

**已拍板的设计决策**（M1 Gate 确认）：
- **显式终稿协议**：工具集含 `final_answer(text)` 终结工具，AI 确定答案后必须调用它输出终稿；循环中模型所有中间 `content` 记为**思考**（`thinking` 事件），前端归入可折叠的"思考过程"区。兜底：模型不调 `final_answer` 却直接吐文本时当终稿用，保证不卡死。
- **事件契约**：`meta → thinking* → tool* → reply → done`（`meta` 由 SSE 服务端补发，CLI 无 meta）。
- **多工具支持**：一次回复可带多个 `tool_calls`，顺序逐个执行（天然支持，无需额外设计）。
- **异步并发**：M1 不做，`_invoke_tool` 只依赖 `(name, args, tool_call_id)` 留好接口，前端契约不变。
- **权限确认**：事件协议预留 `{"event":"tool_confirm", ...}`；CLI 可做 `input()` 确认；UI 确认框 M3 实现。
- `ChatReply` 增加 `thinking: list[str]` 字段。

**文件清单**：
| 路径 | 用途 |
|---|---|
| `src/rock_pvp_agent/config.py` | `Settings`（pydantic BaseModel）+ `load_settings()` 读 env + 模块级单例 `get_settings()` |
| `src/rock_pvp_agent/prompts.py` | `CHAT_SYSTEM_PROMPT` 常量（含显式终稿协议） |
| `src/rock_pvp_agent/llm.py` | `normalize_base_url()`（自动补 `/v1`）+ `build_chat_llm(settings, tools, *, llm=None)` + 模块级缓存（threading.Lock） |
| `src/rock_pvp_agent/tools.py` | `build_agent_tools()` 返回**实例级闭包**工具列表：`calculator` + `final_answer` |
| `src/rock_pvp_agent/agent.py` | `ChatAgent`（无状态）：在线工具循环 + 离线确定性路径 + `event_sink` |
| `src/rock_pvp_agent/__main__.py` | argparse：`-q/--query`、`--debug`；默认交互 REPL；`--serve` 占位（报"M3 实现"） |
| `tests/conftest.py` | 夹具：`agent_settings`（离线 Settings） |
| `tests/fakes.py` | fake LLM：`EchoLLM`/`ScriptedLLM`/`AlwaysToolLLM`（鸭子类型，不 mock HTTP） |
| `tests/test_agent_smoke.py` | 离线路径 + 在线工具循环 + 事件序列冒烟 |

**关键签名**：
```python
# config.py
class Settings(BaseModel):
    api_key: str = ""; model: str = "deepseek-chat"; base_url: str = ""
    timeout: float = 60.0; debug: bool = False
    @property
    def has_api_key(self) -> bool: ...
def load_settings() -> Settings      # load_dotenv() 后拼装；LLM_API_KEY 回退 OPENAI_API_KEY

# llm.py
def build_chat_llm(settings, tools, *, llm=None):   # llm 注入则原样返回（测试缝）
    # 缓存 key=(model, base_url, api_key, timeout, tuple(sorted(tool.name)))；ChatOpenAI(..., temperature=0.7).bind_tools(list(tools))

# tools.py
def build_agent_tools():     # [calculator(expression) -> str（ast 安全求值）, final_answer(text) -> str（终结工具）]

# agent.py
@dataclass
class ChatReply:
    reply: str; tool_calls: list[dict]; thinking: list[str]
    history: list[BaseMessage]; offline: bool; rounds: int

class ChatAgent:
    def chat(self, message, history=None, *, event_sink=None) -> ChatReply
    # 在线：working=[System(prompt)]+history+[Human(message)]，循环≤max_llm_rounds(3)：
    #   llm.invoke → 调用了 final_answer → 取 args["text"] 为终稿 break
    #              → 有 content 且无 tool_calls → content 为终稿 break（兜底）
    #              → 有 content → 记为思考，发 thinking 事件
    #              → 逐个执行 tool_calls（顺序，多工具天然支持），append ToolMessage(tool_call_id)，发 tool 事件
    #              → 轮次耗尽 → 兜底文本
    # 离线（无 key）：确定性模板回复，offline=True
    # 事件顺序：[thinking*, tool*, reply, done]（meta 由服务端补发）
```

**验收命令（用户亲自运行）**：
```bash
uv run python -m rock_pvp_agent -q "你好，用一句话介绍自己"      # 在线单发
uv run python -m rock_pvp_agent -q "请计算 3.5 * 4 再 + 2 的结果" # 工具循环
LLM_API_KEY= uv run python -m rock_pvp_agent -q "你好"          # 离线降级
uv run python -m rock_pvp_agent                                   # 交互 REPL（q 退出）
uv run pytest -q
```

**用户 Gate（M1）**：亲自跑通以上命令，确认"大模型能说话 + 工具能被调用 + 没 key 不死机"。明确"通过"→ commit。

**验收记录**：负责人实际运行 REPL 时遇到网关 400（tool_use 无 tool_result）→ 定位为 `final_answer` 终结工具未回填 ToolMessage，导致历史悬挂 tool_use。修复后：`uv run pytest -q` → 11 passed ✅ · 离线 CLI ✅ · 在线 bind_tools 构造 ✅。负责人确认通过（修 1 个协议缺陷），commit `70a25bb`。

---

## Milestone 2 — 单元测试

**目标**：用 fake LLM 钉死 M1 逻辑：在线工具循环、错误吞掉、轮次终止、历史拼接、离线路径、事件顺序、工具求值。

**文件清单**：`tests/conftest.py`（扩充 fake 夹具）、`tests/test_llm.py`（注入/缓存/`normalize_base_url`）、`tests/test_agent.py`（一轮工具后出终稿、工具抛错被吞仍继续、轮次耗尽兜底、history 传入回填、离线确定性、事件序列恰为 `[meta, tool*, reply, done]`）、`tests/test_tools.py`（calculator 正确求值/非法输入返回错误文本）、`tests/test_agent_ui.py`（占位）。

**fake LLM 形态（鸭子类型，不 mock HTTP）**：
```python
class ScriptedLLM:
    def __init__(self, replies: list[AIMessage]): ...
    def invoke(self, messages) -> AIMessage: return self.replies.pop(0)
```

**验收命令**：`uv run pytest -v`（20+ 用例全绿）、`uv run pytest tests/test_agent.py -k history`。

**用户 Gate（M2）**：浏览测试文件，确认每个核心行为都有用例；"通过"→ commit。这是后续扩展的防回归地基。

**验收记录**：`uv run pytest -v` → **62 passed** ✅ · `pytest tests/test_agent.py -k history` ✅ · `uv run pytest --cov=rock_pvp_agent -q` → **97%**（≥90% 门禁达成）✅。测试逼出两处真实缺陷并修复（空回复死循环、openai 空 key 校验崩溃）。负责人确认通过，commit `ef0aa07`。

---

## Milestone 3 — Web UI（SSE）

**目标**：浏览器流式对话；SSE 推送 `meta/tool/reply/done`；会话历史与重置；无 key 时前端降级到离线回复；纯静态前端。

**文件清单**：
| 路径 | 用途 |
|---|---|
| `src/rock_pvp_agent/ui/__init__.py` | ui 子包标记 |
| `src/rock_pvp_agent/ui/context.py` | `ChatContext`：按 `uuid4().hex` 分会话，上限 64 LRU 逐出；每会话 `threading.RLock` + 全局 `_guard`；共享**一个** `ChatAgent`；`chat/reset/get_history` |
| `src/rock_pvp_agent/ui/server.py` | `create_chat_app(settings, *, llm_factory=None)` 应用工厂；状态挂 `app.state`；模块底部 `app = create_chat_app(get_settings())`；Pydantic body `extra="forbid"` |
| `src/rock_pvp_agent/ui/static/index.html` | 聊天页骨架 |
| `src/rock_pvp_agent/ui/static/chat.js` | `EventSource` 按 `e.data.event` 分发；`sessionStorage` 存 session_id；`fallbackPost()` SSE 失败回退 POST；打字机效果 |
| `src/rock_pvp_agent/ui/static/app.js` | DOM 助手 |
| `src/rock_pvp_agent/ui/static/style.css` | 极简样式 |
| `src/rock_pvp_agent/ui/__main__.py` | `run_ui()`：uvicorn 起在 `127.0.0.1:8001`（env 可覆盖）；懒加载 fastapi/uvicorn |
| `src/rock_pvp_agent/__main__.py`（改） | `--serve` 分支 → `run_ui()` |
| `tests/test_agent_ui.py`（填实） | `TestClient(create_chat_app(settings, llm_factory=...))` 断言全部路由 + SSE 事件序列 |

**路由契约**：GET `/`（index.html）、GET `/api/health`、GET `/api/config`（脱敏，不含 key）、POST `/api/chat`（同步兜底，`asyncio.to_thread`）、GET `/api/chat/stream`（SSE，`message`/`session_id` 放 **query params**，EventSource 仅 GET）、POST `/api/chat/reset`、GET `/api/chat/history`。

**SSE 核心模式**（镜像参考项目）：
```python
_SENTINEL = object()
@app.get("/api/chat/stream")
async def chat_stream(message: str, session_id: str | None = None):
    events: queue.Queue = queue.Queue()
    threading.Thread(target=lambda: _run(ctx, session_id, message, events.put_nowait), daemon=True).start()
    async def gen():
        while True:
            item = await asyncio.to_thread(events.get)   # 阻塞 get 进线程池，不堵事件循环
            if item is _SENTINEL: break
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")
```
注意：SSE 只支持 GET → message/session_id 走 query params 并 URL 编码；同源部署无需 CORS；事件顺序严格 `[meta, tool*, reply, done]`，前端收到 `done` 后 `eventsource.close()`。

**验收命令（用户亲自运行）**：
```bash
uv run python -m rock_pvp_agent --serve
# 浏览器 http://127.0.0.1:8001：1) "计算 3.5*4" → 打字机 + 工具事件 + 答案
#   2) 换话题 → 上下文连续；3) reset → 历史清空；4) 无 key → 仍能离线回复
curl -N "http://127.0.0.1:8001/api/chat/stream?message=你好&session_id=test1"   # 连续 data: 帧，最后 event=done
uv run pytest tests/test_agent_ui.py -v
```

**用户 Gate（M3）**：完成浏览器四项 + curl 流式 + 契约测试；"通过"→ commit。这是最"看得见"的里程碑。

**验收记录**：基础 Web UI 通过后，负责人提出 3 次功能迭代：① 折叠思考卡 + token 统计（修 CSS hidden 陷阱、ReasoningChatOpenAI 捞回思维链）；② Markdown 渲染双视图。最终：`uv run pytest --cov=rock_pvp_agent -q` → **84 passed / 94%** ✅ · 真实 SSE 事件序列 `meta→thinking→tool→reply→done` 含 usage ✅ · 渲染器 node 单测 12 项 ✅。负责人确认通过，commit `589a3ae`。

---

## Milestone 4 — 收尾与扩展指南

**文件清单**（已交付）：
| 文件 | 内容 |
|---|---|
| `README.md` | 完整版：简介 / 架构图（数据流 + 事件协议）/ 快速开始 / 命令速查 / Web UI / 测试 / 结构 / FAQ |
| `docs/collaboration-protocol.md` | 人机协作方法论成文：Gate 流程 / 检查点模板 / 验收原则 / 铁律 |
| `docs/extension-cookbook.md` | 扩展手册：加工具 / 换 LLM / sqlite 持久化 / battle 模式 / 前端 / 协议扩展 / 坑速查 |
| `mydocs/rebuild-plan.md` | 本文件定稿：回填 M0–M4 验收记录 + 状态列 |
| `CHANGELOG.md` | v0.1.0：M0–M4 全部功能 + 修复 + 已知限制 |
| `docs/README.md` | 文档索引更新 |

**验收（冷启动验证）**：
```bash
rm -rf .venv && uv sync --all-extras && uv run pytest -q \
  && uv run python -m rock_pvp_agent -q "你好" \
  && uv run python -m rock_pvp_agent --version
```

**验收记录**：冷启动（删 `.venv` 重建环境）→ `uv sync --all-extras` ✅ · `uv run pytest -q` → 84 passed ✅ · 单发 `-q` ✅ · `--serve` 启动 ✅。负责人确认后打 `v0.1.0` tag。

---

## 2. 人机协作方法论（Q4）

**里程碑工作流循环**：
```
[我讲解设计意图] → [用户审批] → [我逐文件编码] → [用户亲自验证] → [复盘记录] → [用户拍板下一里程碑]
   (目标/为什么/文件地图/   (用户可改范围     (绝不跨里程碑连写)  (one-command-     (记坑与决策理由)   (可继续/暂停/回退)
    验收命令/风险点)        或技术决策)                           to-verify)
```

**用户如何始终主导**：
- 每个里程碑一个 **Gate**：无用户口头"通过"，绝不进入下一步。
- **每条命令用户亲自运行**：助手只给命令 + 预期输出，执行权在用户手里。
- **随时可回退**：每里程碑开始前打 commit；`git log --oneline` 找点，`git checkout .` / `git reset --hard <commit>` 一键回退。
- **决策权在用户**：技术栈/命名/范围/拆分，助手给"选项 + 利弊表"，用户拍板。
- **随时可扩展**：任何里程碑中途可喊停去扩展，完成后回到原进度。

**检查点模板**（`docs/checkpoints/checkpoint-MN.md`）：日期/状态（通过/修改后通过/暂停）→ 目标 → 我做了什么 → 验收命令与结果 → 用户疑问 → 记录（坑/决策理由/遗留 TODO）→ 下一步。

**扩展手册**（随时扩展的路线图）：
| 你想做的事 | 改哪里 |
|---|---|
| 加新工具 | `tools.py` 的 `build_agent_tools()` 加闭包函数 + `test_tools.py` 加用例；agent 核心零改动 |
| 换 LLM 后端 | 改 `.env` 的 `LLM_BASE_URL`/`LLM_MODEL`，或 `llm.py` 加分支 |
| 会话持久化 | `context.py` 的 dict 换 sqlite（接口不变，前端零改动） |
| 引入游戏数据域 | 新增 `src/environment/` 同级包（environment 永不 import agent），agent 侧加 facade 消费 |
| 加新模式（battle） | CLI 加子命令分支，复用 `ChatAgent` + 新工具集，走同一事件协议 |
| 前端加功能 | 直接改 `static/`，无构建 |

**铁律**（镜像参考项目）：无循环 import；外部调用"宁失败不抛"（返回错误文本/None/[]）；工具一律实例级闭包、不污染全局；数据单向流动；可注入即测试。

**"一条命令验证"原则**：每里程碑只给 1–3 条自检命令 + "你应看到什么"。验收 = 用户敲命令、看输出、对照预期、说通过。

---

## 3. 最终文件结构（M4 完成态）

```
MySelfPlayAgent/                                  # ☑ M4 完成态（v0.1.0）
├─ pyproject.toml  .python-version  .gitignore  .env.example  .env(不入库)
├─ README.md  CHANGELOG.md
├─ docs/  README.md  collaboration-protocol.md  extension-cookbook.md
│         checkpoints/checkpoint-M0..M4.md
├─ mydocs/rebuild-plan.md            # 本计划定稿（项目宪法，gitignored 本地专用）
├─ src/rock_pvp_agent/
│  ├─ __init__.py  __main__.py  config.py  prompts.py  llm.py  tools.py  agent.py
│  └─ ui/  __init__.py  __main__.py  server.py  context.py  static/{index.html,chat.js,app.js,style.css}
└─ tests/  conftest.py  fakes.py  test_smoke.py  test_llm.py  test_agent.py  test_tools.py  test_cli.py  test_agent_ui.py
```

---

## 4. 风险与常见坑

- **pydantic v2**：用 `BaseModel`，别用 v1 的 `class Config`。
- **base_url 缺 `/v1`**：网关代理常见，`normalize_base_url()` 统一处理。
- **SSE 只支持 GET**：message/session_id 走 query params 并 URL 编码。
- **SSE 线程安全**：`queue.Queue` + daemon 线程 + `_SENTINEL`；`asyncio.to_thread(events.get)` 避免阻塞事件循环。
- **Python 3.10 兼容**：别用 3.11+ 特性（`StrEnum`/`tomllib`）。
- **事件协议是前端契约**：`meta → thinking* → tool* → reply → done` 一旦定下前后端都以此为准，改协议要同步改测试。
- **显式终稿协议**：模型可能不守 `final_answer` 约定，必须保留"无 tool_calls 且带文本 → 当终稿"的兜底。
