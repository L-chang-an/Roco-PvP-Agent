# 里程碑 记忆接线（Memory Injection）检查点

日期：2026-08-30　状态：**☑ 通过**

## 目标
补 R 线"情境记忆"外挂缺失的最后一块——让记忆真正进入 LLM 对战决策，兑现「沉淀的记忆要提升真实 LLM 决策」的设计初衷。

## 背景（为什么是修复）
核查确认 `two_phase_search` 在整条 R 线（含 main）零调用——记忆只存（R1 落库）不用（从不注入 LLM 决策）。这是 main 实现相对 plan §七「`decide` 加 `[记忆]`」的遗漏。

## 我做了什么

| 文件 | 改动 |
|---|---|
| `battle/player.py` | `LLMPlayer.__init__` 加 `memory`（检索器 callable）+ `decide` 注入 `[记忆]` 块 + `_render_memories`（强制标注"历史经验非当前局面"） |
| `evolution/run.py` | `_make_memory_retriever` + `_strategy_player`/`_play_rollout` 穿透 + `run_steps`/`run_epochs` 加 `memory_dir` + rollout 后接入采纳判定 |
| `evolution/memory_inject.py`（新） | `apply_adoption`：比对 record action vs 记忆 action，命中则采纳 + `update_q`（reward=终局胜负） |
| `tests/test_memory_inject.py`（新） | 渲染标注 / LLMPlayer 注入 / 采纳判定 + Q 更新 |

## 验收命令与结果

```bash
uv run pytest tests/test_memory_inject.py -q   # 3 passed ✅
uv run pytest -q   # 981 passed ✅（无失败）
```

## 记录
- **循环 import 解耦**：player 不 import evolution，用 `memory` callable 依赖注入（检索器由 run.py 构造）。
- **注入范围**：rollout 路径（训练期真实对局）注入记忆；评测路径（D_sel 门禁）保持 `memory_retriever=None` 测纯手册。
- **闭环四步**：存 → 检索 → 注入 → 采纳（Q 更新），Q 值从此反向影响检索排序。
- **迷雾不泄密**：experience_text 是 R1 的 show_v=False 白名单文本。

## 下一步
R6 构筑元游戏（PSRO 外层：Build Oracle + α-rank σ*，可选扩展）。
