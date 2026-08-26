# Rock PVP Agent

人机协作从零重建的自对战 LLM agent（参考 `~/workspace/SelfPlayAgent` 的成熟形态，目前实现 **chat mode**，team / battle 等模式留待后续扩展）。

本项目是 **human-in-the-loop（人机协作）** 方法论的产物：每个里程碑由项目负责人亲自验收，一个里程碑一个 Gate，可随时回退。协作方法论见 [docs/collaboration-protocol.md](docs/collaboration-protocol.md)，完整重建路线图（项目"宪法"）见 [mydocs/rebuild-plan.md](mydocs/rebuild-plan.md)。

## 功能特性

- **CLI 对话**：单发 `-q` + 交互 REPL，`--debug` 打印思考/工具/token 明细
- **Web UI**：SSE 流式对话，原生 JS 前端（零构建、内网可用），会话历史/重置
- **组队页**：`/team` 精灵搜索选将 + 可学技能池（非全局）+ 血脉/个体值/性格配置 + 校验 + 队伍持久化（为 battle 模式打基础）
- **工具循环（ReAct）**：`calculator`（ast 白名单安全求值）+ `final_answer`（显式终稿协议）
- **思考过程可见**：模型链式思考（`reasoning_content`）与工具调用收进可折叠卡片，默认隐藏
- **token 统计**：整轮对话输入/输出/总计 token 用量
- **Markdown 双视图**：终稿默认 Markdown 渲染，可一键切"原文 + 复制"
- **离线降级**：未配置 `LLM_API_KEY` 时返回确定性回复，绝不崩溃

## 架构

```
┌────────────────────────────── CLI / Web 两层入口 ──────────────────────────────┐
│                                                                               │
│  python -m rock_pvp_agent            Web UI (uvicorn 127.0.0.1:8001)          │
│   ├─ -q 单发 / REPL                   ├─ GET  /              (index.html)     │
│   └─ --debug 思考/工具/token           ├─ GET  /team           (team.html)     │
│                                       ├─ GET  /api/chat/stream  (SSE)         │
│                                       ├─ POST /api/chat          (同步兜底)     │
│                                       ├─ GET  /api/team/*        (组队 REST)  │
│                                       ├─ GET  /api/chat/history / reset       │
│                                       └─ GET  /api/config        (无 key)      │
└──────────────────────────────┬────────────────────────────────────────────────┘
                               │
                    ┌──────────▼───────────┐
                    │   ChatAgent (无状态)  │  ← 一个实例可服务任意多会话
                    │  在线工具循环 ≤3 轮     │  ← 历史由调用方持有并传回
                    │  离线确定性降级        │
                    └──────────┬───────────┘
            ┌──────────────────┼───────────────────┐
            │                  │                   │
     ┌──────▼─────┐    ┌──────▼──────┐    ┌───────▼──────┐
     │   tools.py │    │  llm.py     │    │  config.py   │
     │ calculator │    │ ChatOpenAI  │    │ Settings     │
     │ final_answer│   │ +bind_tools │    │ +load_dotenv │
     └────────────┘    │ Reasoning   │    └──────────────┘
                       │ 子类捞 reasoning_content
                       └─────────────┘

事件协议（前后端契约）: meta → thinking* → tool* → reply → done
```

**数据流**：用户消息 → `ChatAgent.chat()` → 系统提示词 + 历史拼装 → LLM 工具循环（中间思考记为 `thinking`，工具结果记为 `tool`）→ `final_answer` 终结 → `ChatReply`（终稿/思考/工具/token/历史）→ CLI 打印或 SSE 推送。

## 快速开始

### 环境要求

- macOS / Linux / Windows（WSL），本机已装 [uv](https://docs.astral.sh/uv/)
- Python 3.10–3.13（`.python-version` 锁定 3.12，uv 自动下载）

### 1. 安装依赖

```bash
cd ~/workspace/MySelfPlayAgent
uv sync --all-extras     # 装全部依赖（含 ui 与 dev）
```

### 2. 配置 `.env`

```bash
cp .env.example .env     # 再编辑 .env 填入以下内容
```

| 变量 | 说明 | 默认 |
|---|---|---|
| `LLM_API_KEY` | 网关 API Key（缺失时回退 `OPENAI_API_KEY`） | 空 → 离线降级 |
| `LLM_MODEL` | 模型名 | `deepseek-chat` |
| `LLM_BASE_URL` | OpenAI 兼容网关地址（自动补 `/v1`，可写完整 `/chat/completions`） | 空 → OpenAI 官方 |
| `LLM_TIMEOUT` | 请求超时秒数 | `60` |
| `DEBUG` | 调试模式（`1`/`true`） | `false` |

> 安全：`.env` 已被 `.gitignore` 忽略、绝不入库；`/api/config` 接口已脱敏，绝不返回 key。

### 3. 跑起来

```bash
# 单发提问（含工具调用）
uv run python -m rock_pvp_agent -q "请计算 3.5 * 4 再 + 2 的结果"

# 交互 REPL（exit / quit / q 退出）
uv run python -m rock_pvp_agent --debug

# Web UI → 浏览器打开 http://127.0.0.1:8001（聊天 `/`，组队 `/team`）
uv run python -m rock_pvp_agent --serve
# 或直接起 UI 包（等效，`python -m ui`）
uv run python -m ui
```

## 命令速查

| 命令 | 作用 |
|---|---|
| `uv run python -m rock_pvp_agent -q "你好"` | 单发提问 |
| `uv run python -m rock_pvp_agent --debug` | REPL + 打印思考/工具/token |
| `uv run python -m rock_pvp_agent --serve` | 启动 Web UI（`ROCK_UI_HOST`/`ROCK_UI_PORT` 可覆盖，默认 `127.0.0.1:8001`） |
| `uv run python -m ui` | 直接启动 UI 包（聊天 + 组队） |
| `uv run python -m rock_pvp_agent --version` | 打印版本 |
| `uv run pytest -q` | 跑全部测试 |
| `uv run pytest --cov=rock_pvp_agent -q` | 跑测试 + 覆盖率 |
| `curl -N "http://127.0.0.1:8001/api/chat/stream?message=你好&session_id=test1"` | 直接看 SSE 事件流 |

## Web UI

- **流式**：SSE 推送 `meta → thinking* → tool* → reply → done`，回复打字机逐字显示
- **思考卡片**：🧠 思考与工具调用默认折叠，点"显示思考过程 ▾"展开；卡头实时统计次数
- **Markdown 双视图**：终稿默认渲染，工具栏可切"查看原文"并复制
- **token 统计**：回复底部显示 `⚡ tokens 输入/输出/总计`
- **会话管理**：`sessionStorage` 记住 session，刷新不丢上下文；`reset` 一键清空
- **离线降级**：无 key 时仍可聊，回复带"离线模式"提示
- **网络兜底**：SSE 断流自动回退同步 POST

### 组队页（/team，2026-08-25）

- **精灵搜索**：按 名称/编号/系别 客户端过滤；BOSS 形态禁选；同家族冲突标黄拦截
- **队伍规模**：管理员规则 3–6（`battle_config`），切换即重排槽位
- **技能池**：选中精灵后显示**该精灵的可学池**（默认∪技能石∪传说∪血脉技能，非全局池），
  可搜索技能名；**未实装效果**的技能置灰「未实装」不可选 → 保存的队伍永远能开战
- **血脉 / 个体值 / 性格**：血脉（18 系，决定可携带的血脉技能）、IV（0–10，最多 3 维）、
  性格（31 种），属性预览逐字节复刻 `calc_combat_stats`
- **校验**：`POST /api/team/validate` 一次报全部错误（规模/技能/血脉/家族/首领/IV）
- **持久化**：保存到绝对路径或 `teams/` 下相对路径（`.json`、原子写、路径逃逸拦截），
  可列表加载/删除；加载后自动重校验（历史队伍可能因技能白名单变化失效）
- **为 battle 打基础**：保存前强制通过 `validate_team(picks, [], rules, VALID)`——
  存下的队伍即合法对战队伍，后续 battle 模式直接吃这份 roster

## 测试

```bash
uv run pytest -q            # 84 passed / 94% 覆盖率
uv run pytest tests/test_agent.py -k history -v
```

测试全部走 **fake LLM（鸭子类型）**，零网络、可离线跑。核心覆盖：离线降级、显式终稿、工具循环、错误吞掉、轮次兜底、历史可重放、事件契约、token 统计、思维链捕获、REST/SSE 契约。

## 项目结构

```
src/
├─ rock_pvp_agent/     LLM agent 核心包
│  ├─ __main__.py     CLI 入口（-q / REPL / --serve / --debug）
│  ├─ config.py       Settings + 环境变量加载（单例）
│  ├─ prompts.py      CHAT_SYSTEM_PROMPT（显式终稿协议）
│  ├─ llm.py          build_chat_llm 工厂 + ReasoningChatOpenAI（捞回 thinking）+ 缓存
│  ├─ tools.py        calculator + final_answer（实例级闭包工具）
│  ├─ agent.py        ChatAgent 无状态核心 + 事件发射 + 离线降级
├─ environment/       对战引擎（E0a–E3：数据/组队/规则/效果，独立包）
└─ ui/                Web UI（2026-08-25 从 rock_pvp_agent 提级，顶层包）
   ├─ __main__.py     run_ui()（uvicorn 启动）
   ├─ server.py       create_chat_app 应用工厂 + REST/SSE 路由
   ├─ context.py      ChatContext 会话管理（LRU 上限 64，线程安全）
   ├─ routes_team.py  组队 REST（/api/team/*：精灵/技能池/校验/保存/加载）
   └─ static/         index.html / team.html / chat.js / team.js / app.js / style.css

docs/                协作协议 + 扩展手册 + checkpoints 检查点
mydocs/rebuild-plan.md  项目宪法（完整重建路线图，gitignored 本地专用）
tests/               pytest 套件 + fake LLM 夹具
```

## 文档索引

| 文档 | 内容 |
|---|---|
| [docs/collaboration-protocol.md](docs/collaboration-protocol.md) | 人机协作方法论（Gate 流程 / 检查点模板 / 验收原则） |
| [docs/extension-cookbook.md](docs/extension-cookbook.md) | 扩展手册（加工具 / 换 LLM / 持久化 / battle 模式 / 前端） |
| [mydocs/rebuild-plan.md](mydocs/rebuild-plan.md) | 项目宪法：M0–M4 重建路线图 + 验收记录 |
| [docs/checkpoints/](docs/checkpoints/) | 每里程碑检查点（通过记录 + 复盘） |
| [CHANGELOG.md](CHANGELOG.md) | 版本变更记录 |

## FAQ

**Q: 未配置 API Key 能跑吗？**
能。进入离线模式，返回确定性模板回复，CLI/Web UI 都不会崩溃。配置 `.env` 后重启即可。

**Q: 模型为什么不会算数 / 乱调工具？**
agent 采用**显式终稿协议**：模型必须调用 `final_answer` 输出终稿，中间输出一律视为思考。若模型不守协议，`ChatAgent` 有兜底（无工具调用且带文本 → 当终稿），保证不卡死。调节提示词可改善行为。

**Q: 换一家大模型（网关）怎么配？**
只要网关兼容 OpenAI Chat Completions API，改 `.env` 的 `LLM_BASE_URL` + `LLM_MODEL` + `LLM_API_KEY` 即可。详见 [docs/extension-cookbook.md](docs/extension-cookbook.md#换-llm-后端)。

**Q: 我的思维链为什么看不到？**
模型中间推理存在网关的 `reasoning_content` 字段，通用 `ChatOpenAI` 会丢弃它。本项目通过 `ReasoningChatOpenAI` 子类捞回，已自动处理，无需配置。

**Q: 想加一个新工具？**
在 `tools.py` 的 `build_agent_tools()` 加一个闭包函数即可，agent 核心零改动，详见扩展手册。

## License

v0.1.0 · 内部学习项目，无开源 License。
