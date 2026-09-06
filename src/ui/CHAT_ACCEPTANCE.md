# Web UI 全量优化验收记录

日期：2026-09-06。需求依据为 `mydocs/chatmode/web-ui-optimization-plan.md`、
`web-ui-session-management-plan.md` 及其引用的轮次卡片、队伍看板专项。

## 交付行为

- 轮次卡片继续按真实模型调用展示，默认折叠；公开行动说明与工具事实分开，详情按需加载。
- SQLite v1 自动备份并迁移到 v2；会话标题、搜索、分页、重命名、归档/恢复、删除和 revision 冲突检查齐全。
- 新旧聊天 API 共用持久化任务服务。POST 创建幂等任务，SSE/轮询读取同一个任务；终稿、结果、checkpoint 和 done 原子提交。
- 页面刷新、会话切换及独立进程重启后恢复记录；中断任务不自动重跑。停止、重试、双标签页忙碌冲突和草稿均有明确状态。
- 续聊保留完整当前队伍、命数/道具、来源约束、未确认假设和工具观察；默认 32,000 字符、最多 20 个完整近期 Turn。损坏/落后 checkpoint 从可见结果重建。
- 队伍看板显示成员、配置、中文六维属性、协同及证据边界。合法主队可保存、下载、在组队页编辑；备选需显式校验后才能保存。
- 保存与下载共用 v2 serializer；覆盖需界面确认，保存请求固定路径/时间/内容，重试及启动时对账已写文件。独立副本不随会话删除。
- 编辑器支持 artifact/path 深链、加载变更提示和校验失效提示；修改副本不会改变历史建议。

## 自动验证

主回归：**325 passed, 1 skipped，91.89 秒**。包括 Agent、顾问、Dispatcher、CLI、
聊天 API、组队、对战、沙箱服务及 Windows 单元测试，并包含 14 个 Chromium 浏览器用例。

最终错误提示与退出收尾改动后专项复测：**58 passed，58.21 秒**。
进一步补充浏览器“修改性格 → 重新校验 → 保存副本 → 返回原建议”断言后，该完整流程复测通过。

| 用户场景 | 验证依据与结果 |
|---|---|
| 每个模型轮次形成独立工作卡 | 三轮、并发工具、失败修复、零轮模板、100 轮长流程、展开与键盘焦点均通过 |
| 队伍生成后直观看到完整成员和配置 | 合法 3/6 人 API 用例通过；浏览器确认卡片、可读终稿、无整段 JSON 首屏 |
| 保存、下载、编辑与返回 | 文件 v2、技能顺序、显式空道具、修改性格后保存及原 artifact 不变均通过 |
| A/B 会话独立保留 | 侧栏切换、标题、草稿、归档/恢复/删除、前进后退及迟到回调隔离均通过 |
| 刷新与跨重启续聊 | SQLite 重新打开及损坏 checkpoint 重建后，“把第二只换掉”读取完整原队伍 |
| 双标签页并发提交 | 一项执行，另一项保留草稿并恢复同一任务；未产生第二份用户消息 |
| 请求响应丢失或 SSE 断开 | 复用 request_id 或查询原 Turn；没有重复模型调用或重复气泡 |
| 异常、停止与崩溃 | 模型取消、迟到结果、数据库写失败及独立进程四处崩溃注入均通过 |

专项故障测试还覆盖超过 64 个会话、分页游标、v1 一致备份、未知数据库版本、数据库锁冲突、
旧工具失效与会话隔离、非法备选、当前数据版本变化、保存覆盖确认及文件写完后的启动对账。
浏览器额外检查中文输入法、多行输入、窄屏无横向溢出、滚动、恶意 HTML 文本及迟到 artifact 响应。

`git diff --check` 返回 0；Windows 换行转换提示不属于空白错误。

## 可重复运行

```powershell
$tests = @(
  'test_chat_management', 'test_chat_sessions', 'test_chat_rounds', 'test_chat_browser',
  'test_agent', 'test_agent_ui', 'test_advisor_agent', 'test_advisor_advice',
  'test_advisor_scope', 'test_advisor_tool_schemas', 'test_ui_team',
  'test_tool_dispatcher', 'test_tool_deferred_loading', 'test_agent_reliability',
  'test_cli', 'test_cli_full', 'test_battle_ui', 'test_battle_config',
  'test_sandbox_windows_unit', 'test_sandbox_service'
) | ForEach-Object { "tests/$_.py" }
$tests += 'tests/platform/test_sandbox_windows_stability.py'
.\.venv\Scripts\python.exe -m pytest @tests -q --browser chromium
```

截图：[桌面](../../artifacts/web-ui-acceptance/desktop.png)、[手机](../../artifacts/web-ui-acceptance/mobile.png)。
截图在本地忽略的 artifacts 目录中，使用测试数据，不代表真实模型生成的推荐。

## 验证边界与运行说明

- 所有模型流程使用 Scripted/Fake LLM；未调用真实模型服务，也未验证推荐的实战效果。
- 1 项跳过为真实 Windows 沙箱持续压力测试，要求显式启用 `RUN_SANDBOX_INTEGRATION=1`、
  `RUN_SANDBOX_STABILITY=1` 及已准备的运行环境。本次 Windows 单元和沙箱服务回归通过。
- 首期仍为本地单用户、单 worker；同步模型 HTTP 线程取消后依赖客户端超时回收。
- 已丢失的旧内存记录无法恢复；旧 artifact 没有当时展示快照时明确标为旧版恢复结果。
- 数据库中保留全部可见历史；有限模型窗口不承诺逐字记住所有旧讨论，超过必要状态预算时给出明确错误。
- 重启本地 Web 服务加载本次代码；预算默认仍为 100 轮、555 秒、最多 4 个活跃 Turn。
