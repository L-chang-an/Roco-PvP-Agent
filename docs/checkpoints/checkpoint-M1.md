# 里程碑 M1 检查点

日期：2026-08-23　状态：**☑ 通过**（修复 1 个协议缺陷后通过）

## 目标
最小 chat mode 核心（无 UI）：一条命令与真实 LLM 对话；calculator 工具跑通 ReAct 工具循环；无 key 时离线降级可用；CLI 支持 -q 单发 + 交互 REPL。

## 我做了什么
| 文件 | 改动 |
|---|---|
| `src/rock_pvp_agent/config.py` | Settings + load_settings（LLM_API_KEY 回退 OPENAI_API_KEY）+ get_settings 单例 |
| `src/rock_pvp_agent/prompts.py` | CHAT_SYSTEM_PROMPT（含显式终稿协议） |
| `src/rock_pvp_agent/llm.py` | normalize_base_url（自动补 /v1）+ build_chat_llm 注入缝 + 线程安全缓存 |
| `src/rock_pvp_agent/tools.py` | 实例级闭包工具：calculator（ast 安全求值）+ final_answer（终结工具） |
| `src/rock_pvp_agent/agent.py` | ChatAgent：在线工具循环（显式终稿）+ 离线降级 + 事件发射 |
| `src/rock_pvp_agent/__main__.py` | -q 单发 / 交互 REPL / --debug / --serve 占位 |
| `tests/conftest.py` / `tests/fakes.py` | agent_settings 夹具 + EchoLLM/ScriptedLLM/AlwaysToolLLM |
| `tests/test_agent_smoke.py` | 9 条测试（事件契约、轮次兜底、错误吞掉、历史拼接、回归） |
| `mydocs/rebuild-plan.md` | M1 部分固化设计决策（final_answer/thinking/多工具/confirm） |

## 验收命令与结果
- 用户实际运行 REPL 时遇到网关 400（tool_use 无 tool_result）→ 定位为 final_answer 终结工具未回填 ToolMessage，导致历史悬挂 tool_use。
- 修复：终结工具也回填 ToolMessage；新增 `_content_text()` 兼容 Anthropic 块列表；轮次耗尽兜底补全未回填 tool_result。
- `uv run pytest -q` → **11 passed** ✅
- 离线 CLI `-q "你好"` → 正常 ✅
- 在线 LLM 构造（bind_tools）→ 正常 ✅

## 用户的疑问 / 修改要求
- Q1 隐式 vs 显式终稿 → 采纳用户设想，引入 `final_answer` 终结工具 + thinking 事件
- Q2 多工具/异步/权限确认 → 顺序支持多工具；异步留接口 M1 不做；tool_confirm 事件预留
- Q3 手写循环 vs LangGraph → 解释并确认手写（延迟引入复杂度）
- 修复了"终结工具历史悬挂"缺陷

## 记录
- **核心教训（格式契约）**：OpenAI/Anthropic 网关要求每个 tool_use 必须紧跟 tool_result；任何"AI 消息带工具调用但不回填结果"的历史重放都会 400。这是 chat/battle 等所有工具模式的通用约束。
- 用户的网关返回 Anthropic 风格消息（tool_use/tool_result 块），比 OpenAI 格式更严格。
- 显式终稿协议 + 兜底（无 tool_calls 且带文本 → 当终稿）已按计划落地。

## 下一步
M2 单元测试打磨：test_llm / test_agent 全量 / test_tools，pytest-cov 覆盖率。
