# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 新增

- **E 线规则施工**：进化链数据与索引、萌化结算（退化重算/最低阶拦截/解除回升）、首领化道具（一阶进化/多分支选择/入场触发）、冻结批技能与特性（Gate B–G）
- **R 线（策略进化）完整落地**：R0 可观测指标（evaluate/analysis/bench/league）、R1 情境记忆（两阶段检索）、R2 信用分配（价值落差 + 反事实回放）、R3 反思与有界 Playbook 编辑、R4 Pareto 策略池 + 两级门禁 + 剥削者 + 回归门、R5 慢更新（Meta Playbook + 价值函数 + D_test 汇报）
- **记忆注入**：情境记忆接线、采纳判定与 Q 值更新（与 R 线衔接）
- **顾问 Agent 完善（M1–M5）**：catalog 白名单 DSL + validate 硬闸、轨迹证据（版本闸/重放闸）、结构化终结 + analyze_team + simulate_matchups + EvidenceGate、ScopeGate 越界拒答、回答行为评测集 + 审计日志

### 变更

- 项目采用 Apache-2.0 开源协议（新增 `LICENSE`）

## [0.2.0] - 2026-08-26

### 新增（E 线：对战引擎完整交付）

- **`environment` 包**：精灵数据、规则校验、战斗状态机、迷雾视角与确定性重放（纯 Python、零引擎依赖）
- **对战玩家**：`Random` / `FakeLLM` / `LLM` / `Playbook` 玩家
- **自博弈编排**：`selfplay` + 轨迹保存（`store`）
- **Web 对战与组队页面**：battle / team 路由与静态页、对局 REST/SSE 接口
- **测试**：environment 数据集与队伍校验测试（`test_environment_*`）

### 修复

- 对局状态推进、防御冷却与能耗口径修正

## [0.1.0] - 2026-08-23

### 新增（M0–M4 完整交付）

- **项目骨架**：`uv` + `hatchling` 工程，Python 3.12，`rock_pvp_agent` 包（v0.1.0）
- **CLI chat**：单发 `-q` / 交互 REPL / `--debug` / `--version` / `--serve`
- **配置**：pydantic `Settings` + `.env` 加载（`LLM_API_KEY` 回退 `OPENAI_API_KEY`），缺 key 自动离线降级
- **LLM 层**：`build_chat_llm` 工厂（线程安全缓存、注入缝）+ `normalize_base_url`（自动补 `/v1`）
- **`ReasoningChatOpenAI`**：捕获网关 `reasoning_content` 思维链（通用 `ChatOpenAI` 会丢弃）
- **ReAct 工具循环**：`calculator`（ast 白名单安全求值）+ `final_answer`（显式终稿协议），≤3 轮兜底
- **思考与工具可视化**：`thinking` / `tool` 事件协议，`ChatReply` 携带 thinking 列表与 token 用量
- **Web UI**：FastAPI + SSE 流式对话，纯静态原生 JS 前端（零构建）
  - 会话历史/重置（LRU 上限 64，线程安全）
  - 折叠的"思考与工具调用"卡片（默认隐藏，点击展开）
  - token 统计（输入/输出/总计）
  - 终稿 Markdown 渲染双视图（渲染/原文 + 复制按钮）
  - 零依赖、XSS 免疫的 Markdown 渲染器
  - SSE 断流自动回退同步 POST
- **测试**：84 个用例 / 94% 覆盖率（fake LLM 鸭子类型，零网络）
- **文档**：README / 协作协议 / 扩展手册 / 里程碑检查点（M0–M4）/ 重建计划定稿

### 修复（里程碑中由验收与测试逼出）

- 终结工具 `final_answer` 未回填 `tool_result` 导致历史悬挂、网关 400（M1）
- 空回复死循环：`if content and not calls` → `if not calls`（M2）
- openai 客户端空 key 强制校验导致缓存测试崩溃 → monkeypatch 假客户端（M2）
- REPL 测试死循环：lambda 每次重建迭代器 → 迭代器只建一次（M2）
- 折叠卡片被 CSS `display:flex` 覆盖 `hidden` 属性 → 改用 `.open` class 切换（M3）
- 行内代码内 markdown 二次渲染 → 行内代码先抽占位符保护（M3）

### 已知限制

- 仅 chat mode；team / battle / ai-battle 模式留待后续扩展
- 会话历史仅存内存，重启即失（持久化方案见扩展手册第 3 节）
- 工具顺序执行，异步并发尚未实现
- 权限确认事件（`tool_confirm`）已预留接口，未落地
