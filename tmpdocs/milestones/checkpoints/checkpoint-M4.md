# 里程碑 M4 检查点

日期：2026-08-23　状态：**☑ 通过**

## 目标
收尾与扩展指南：README 完整化 / 协作协议成文 / 扩展手册 / CHANGELOG / 重建计划定稿 / 冷启动验证 / v0.1.0 tag。

## 我做了什么
| 文件 | 改动 |
|---|---|
| `README.md` | 完整重写：简介 / 架构图（数据流 + 事件协议）/ 快速开始 / 命令速查 / Web UI / 测试 / 结构 / FAQ |
| `docs/collaboration-protocol.md` | 人机协作方法论成文：Gate 流程 / 检查点模板 / 验收原则 / 铁律 |
| `docs/extension-cookbook.md` | 扩展手册：加工具 / 换 LLM / sqlite 持久化 / battle 模式 / environment / 前端 / 协议扩展 / 坑速查 |
| `CHANGELOG.md` | v0.1.0：M0–M4 功能 + 修复 + 已知限制 |
| `docs/README.md` | 文档索引更新 |
| `mydocs/rebuild-plan.md` | 定稿：执行状态标记 / 总览表状态列 / M0–M4 逐节回填验收记录 / 结构图更新（gitignored 本地专用） |

## 验收命令与结果
- 冷启动验证（我自检 + 负责人验收）：`rm -rf .venv && uv sync --all-extras` → 依赖重建成功 ✅
- `uv run pytest -q` → **84 passed** ✅
- `--version` → `rock_pvp_agent 0.1.0` ✅
- 离线单发 `-q "你好"`（强制无 key）→ 离线降级回复 ✅
- `--serve` 启动 → `/api/health`、`/api/config`（无 key 泄露）、`/` 首页均 200 ✅

## 用户的疑问 / 修改要求
- 无。负责人验收后明确"通过"。

## 记录
- `mydocs/rebuild-plan.md` 定稿但 **gitignored**（本地专用，绝不入库）——验收记录只落库到 `docs/checkpoints/`。
- 冷启动验证的关键价值：确认 README 的"快速开始"可照抄跑通，环境可完全复现。
- v0.1.0 tag 落地，项目达到"从零重建到可运行 + 可测试 + 可扩展"的完整闭环。

## 下一步
v0.1.0 完成。后续功能扩展按 `docs/extension-cookbook.md` 走（加工具 / battle 模式 / 持久化等），每项仍走"讲解设计意图 → 审批 → 编码 → 验收 → Gate"的人机协作循环。
