# ui — Web 界面（FastAPI + SSE）

聊天页沿用原生 HTML/CSS/JavaScript，按真实模型轮次展示默认折叠的卡片。展开后分别显示
主模型在同一次响应中提供的思考摘要、确定性的执行结果摘要、工具列表及按需加载的详情。
最终回答按类型显示可读文本或队伍看板，保留原文和复制。会话侧栏支持标题搜索、分页、
重命名、归档/恢复、删除；队伍支持保存、下载以及在组队页编辑。

## 运行与预算

```powershell
uv sync --extra ui --group dev --inexact
.\.venv\Scripts\python.exe -m ui
```

CLI/Web 顾问默认最多 100 次模型调用，模型和工具共用 555 秒总预算。配置源为
`CHAT_MAX_LLM_ROUNDS`、`CHAT_MAX_TOTAL_SECONDS`；提示词和进度读取有效配置。
思考摘要与主模型工具调用同次返回，不额外调用摘要模型。

`CHAT_DB_PATH` 默认 `artifacts/chat/sessions.sqlite3`，启动时解析绝对路径。
数据库在 FastAPI lifespan 中打开，使用 SQLite WAL、foreign_keys、busy_timeout 和
synchronous=FULL；同一数据库由进程锁独占，运行时请保持单 worker。
`CHAT_MAX_CONCURRENT_TURNS` 默认 4；同一会话最多一项运行任务，不隐式排队。

## 模块与数据

| 模块 | 职责 |
|---|---|
| `server.py` | 应用工厂、lifespan、静态页面和旧兼容 API |
| `chat_store.py` | SQLite 事务、公开记录和快照、事件序号、结果快照、checkpoint |
| `turn_coordinator.py` | 任务创建、单点事件消费、取消、完成屏障、错误收束 |
| `routes_chat.py` | 新会话与任务 REST、只订阅 SSE、有界工具详情 |
| `legacy_chat.py` | 旧 API 输出适配，委托同一持久化任务服务 |
| `team_service.py` / `routes_chat_artifacts.py` | 共享校验、v2 序列化、结果快照及保存对账 |
| `static/sessions.js` / `team-advice-card.js` | 会话侧栏、草稿和只读队伍看板 |
| `static/chat-state.js` | 实时事件与历史快照共用状态 reducer |
| `static/round-card.js` | 保持展开状态的 Round／工具卡片 |
| `static/chat.js` | 页面、轻量会话路由、创建去重、订阅／查询恢复 |

用户可见记录包含用户原消息、最终答复、公开思考摘要和执行事实。checkpoint 使用
规范化 Human/AI 最终答复配对、最新合法主队和已加载工具名称，不序列化原始 provider
消息、reasoning 或未配对 ToolMessage。`CHAT_CONTEXT_MAX_CHARS=32000`、
`CHAT_CONTEXT_MAX_TURNS=20` 限制模型输入，按完整 Turn 裁剪；全部可见历史仍保留。
完整主队、用户约束及其来源优先保留，假设单独记录。必要状态超预算时报可读错误。
损坏/落后的 checkpoint 从可见结果及 artifact 重建，不清空原历史。
思考摘要保存在卡片记录中，不自动把全部过程摘要重新注入下一轮用户请求。

## 会话与任务 API

| 接口 | 行为 |
|---|---|
| `POST /api/chat/sessions` | 新建；返回 id，前端打开 `/chat/{id}` |
| `GET /api/chat/sessions?cursor=...&q=...&archived=false` | 标题搜索和稳定分页 |
| `PATCH /api/chat/sessions/{id}` | revision 必填；可更新 title、archived |
| `DELETE /api/chat/sessions/{id}?revision=N` | 空闲时删除，保留 tombstone；独立队伍文件不删除 |
| `GET /api/chat/sessions/{id}` | 会话信息与 active_turn_id |
| `GET /api/chat/sessions/{id}/messages?before=...&limit=20` | 按完整 Turn 分页，返回消息、卡片和最终结果快照 |
| `POST /api/chat/sessions/{id}/turns` | `{request_id, message, retry_of?}`，首次 202，重复请求 200 |
| `GET /api/chat/turns/{id}` | 查询同一任务的状态、卡片、结果及 last_seq |
| `GET /api/chat/turns/{id}/events?after_seq=N` | 只读订阅，支持 Last-Event-ID（合法时优先） |
| `POST /api/chat/turns/{id}/cancel` | 幂等取消；已完成任务保持原状态 |
| `GET /api/chat/turns/{id}/tools/{execution_id}` | 已脱敏、有界详情；不会重新执行工具 |

消息为 1–2000 字符且非空白。相同会话、相同 request_id 和内容复用同一任务；不同内容
返回 409 REQUEST_ID_CONFLICT。会话已有其他任务返回 409 SESSION_BUSY（含 active_turn_id），
全局满额返回 429 SERVER_BUSY。未知 id 返回 404，删除的 Session 返回 410，读取不隐式创建会话。
首条消息前 24 字符生成默认标题，手工标题优先；标题上限 100 字符。
消息原文保留，strip 后判空并计算幂等摘要；retry_of 必须指向同一会话已结束的 Turn。

新 SSE 外壳为 schema_version=2、event、session_id、turn_id、seq、created_at、
round_id/index、payload。序号在 Turn 内递增，SSE id 使用同一序号。

```text
turn.started
  round.started
  round.summary（模型同轮给出的简短说明，缺失时明确标记）
  round.progress / tool.started / tool.completed
  round.completed
  ...
reply
done
```

`turn.cancelling` 表示停止已受理。每轮真实调用恰好创建／收束一次；ScopeGate 或离线
模板为零 Round。并发工具按实际完成时间通知，显示顺序和 ToolMessage 顺序仍按调用序号。
工具执行成功与业务校验成功独立，跳过／未找到／未启用／截断不伪装为校验通过。
每项工具完成后，`round.progress` 同步持久化已完成部分的执行结果摘要，不等待同轮慢工具。
页面显示真实轮数与持续更新的已用时间，实时更新保留展开状态和键盘焦点。
聊天卡片撑开整个页面，右侧页面滚动范围随卡片追加和展开增长；顶部导航与底部输入区保持可见。
自动滚底、新消息提示和历史翻页统一使用文档滚动位置，查看旧消息时保留当前位置。

语义事件和卡片快照落盘后才发布。结果、checkpoint、Turn 终态、reply/done 在同一事务
提交；写盘失败不发送成功 done，页面明确报错并停止执行，修复存储后重启恢复。
纯连接心跳不入库。订阅者定期按数据库游标补读，不保存无消费者的无限通知队列。

## 刷新、停止与兼容

新建会话保留旧会话；独立地址可收藏或重新打开，根地址恢复最近访问的会话。
刷新、切换页或 SSE 断开不取消任务；只重订阅或查询同一 Turn。创建响应丢失时使用原
request_id 重试。输入框保留忙碌冲突时的草稿，中文输入法确认不会误发送。

显式停止覆盖模型等待与工具执行，迟到结果丢弃。同步模型 HTTP 工作线程依赖客户端
超时回收，取消不会强杀 Python 线程；协作式沙箱取消沿用其清理机制。
服务启动会将上次遗留的任务收束为 interrupted，保留已完成步骤和最后完整 checkpoint，
不自动重跑模型或工具。数据库不按旧的 64 会话 LRU 自动删除历史。

旧 `/api/chat`、执行型 `/api/chat/stream`、`history`、`reset` 委托同一持久化任务服务，
保留原返回形状；旧 SSE 仍在断开时取消。无 request_id 的旧 API 不承诺跨请求幂等。
reset 清空原 Session，运行中返回冲突。新页面不使用执行型 GET 或 POST 重跑作为断线兜底。
升级前已丢失的内存会话无法恢复；SQLite v1 自动升级至 v2，升级前通过 SQLite backup
生成旁路备份，失败停止写入。旧 artifact 缺少当时展示快照时明确标记 legacy_snapshot。

## 测试

测试应用通过 `create_chat_app(settings, llm_factory=..., chat_store=...)` 注入 Fake LLM
和临时数据库；新 API 测试需要使用 `with TestClient(app)` 运行 lifespan。

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python.exe -m pytest tests/test_chat_rounds.py tests/test_chat_sessions.py -q
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest tests/test_chat_browser.py -q --browser chromium --output tmp/chat-browser-test-results
```

浏览器用真实本地 HTTP 服务和 Fake LLM 验证默认折叠、摘要、按需详情、刷新、新建与切换、
提交响应丢失、SSE 查询回退、停止、输入法、键盘焦点、滚动、窄屏及 100 轮卡片。没有调用真实模型。

## 队伍结果与文件

`GET /api/chat/artifacts/{id}` 读取不可变建议/展示快照和当前校验；`POST .../validate`
重新校验；`POST .../alternatives/{index}/validate` 校验零起始序号的备选并生成独立 artifact。
`GET .../download` 校验后返回 v2 队伍文件。`POST .../save` 接受 artifact_version、
request_id、可选 path、overwrite 和 items；道具变化生成派生配置，不覆盖原建议。

主队需完整 3/6 人；备选默认未验证。只有真实提交工具成功产生 team_advice，普通正文 JSON
无合法保存资格。终稿载荷上限 128 KiB、备选最多 6 支，超限返回可修复错误，不截断存档。
看板分开显示工具观察、建议说明和未核验引用，不拼造胜率/置信区间。

保存默认自动命名到 teams/，也支持原有用户指定绝对路径。覆盖已有文件需显式确认。
保存意图固定文件名、saved_at 和内容摘要；写文件成功但数据库提交失败后，重试对账同一个文件。
历史保存记录不保证用户未移动文件；可重新另存。下载与保存共用 v2 serializer。

`/team?artifact_id=...` 打开快照副本，`/team?path=...` 打开文件；加载后校验，规范化变更
明确提示，并可返回原会话。advice 无道具字段时显式写 items=[]；命数仅保留在建议里，
不会伪称 v2 文件已保存命数。旧 v1/v2 文件仍可加载。

会话启动不自动执行模型；无有效最近会话时显示欢迎页。草稿保存在浏览器 localStorage，
待确认提交及保存 request_id 使用 sessionStorage；关闭订阅和切换会话不停止后台任务。
浏览器测试额外覆盖侧栏管理、双标签页冲突及队伍保存/下载/编辑/返回。
