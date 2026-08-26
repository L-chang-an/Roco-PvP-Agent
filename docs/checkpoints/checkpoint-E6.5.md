# 里程碑 E6.5 检查点

日期：2026-08-26　状态：**☑ 通过**（Gate：真实 LLM 决策 + 非法重试不死机 + 无 key 降级仍打完 + 轨迹可重放；负责人 2026-08-26 确认）

## 目标
真实 LLM 当一方出招，接进 E6 的 `run_selfplay`（`--a llm`）；**无 key / LLM 异常自动降级**，一局照样打完；轨迹仍可重放。这是计划终点的最后一环。

## 我做了什么
| 文件 | 改动 |
|---|---|
| `src/rock_pvp_agent/battle/prompts.py` | `BATTLE_PLAYER_SYSTEM_PROMPT` + `render_observation`/`render_replacement`/`render_events` + `_describe_action`（只消费白名单字段，100% 覆盖） |
| `src/rock_pvp_agent/battle/player.py` | `build_side_tools`（唯一工具 `battle_act_{side}`，@tool 构造，函数体从不被调用）+ `LLMPlayer`（98% 覆盖） |
| `src/environment/match.py` | `on_turn_result` 改传 `filter_events_for` 过滤后的事件（堵敌方绝对血量泄露） |
| `src/rock_pvp_agent/battle/selfplay.py` | `build_player` 支持 `llm`（无 key 降级假LLM）+ `run_selfplay` 增 `settings` 参数 |
| `src/rock_pvp_agent/__main__.py` | `selfplay --a/--b` choices 加 `llm` |
| 测试 | `tests/test_battle_player.py`(20) + `tests/test_selfplay.py`(+5) |

## LLMPlayer 设计要点
- **拦截式工具循环**：模型调 `battle_act_{side}` → `_parse_decision` 解析成 `Decision` → 补一条**合成 ToolMessage**（「行动已提交。」/非法原因）——历史可重放（网关 400 的坑：每个 tool_use 必须有 ToolMessage）。
- **两路独立 LLM 实例**：工具名带 side 后缀 → `build_chat_llm` 缓存键（settings + 工具名集合）自然分键 → 不共用缓存实例。
- **非法重试 ≤max_retries**：非法提交把原因回灌 ToolMessage，LLM 看到后修正；耗尽 → 随机兜底（`RandomPlayer` 独立 RNG 流）。**LLM 抛异常不重试，直接兜底**（宁失败不抛）。
- **每回合恰好一次 battle_act**：模型没调（或调未知工具）→ 提示重试；一个响应里多余调用 → 忽略取第一个。
- **私有 history**：系统提示 + 每回合渲染后的迷雾观测 + 自己的工具往来 + 回合事件摘要；绝不注入另一侧。

## 迷雾口径的两道闸
1. **观测**：`run_match` 只喂 `session.view(side)`（E6 已收口）。
2. **事件**：**E6.5 新发现并修补**——`run_match.on_turn_result` 原传**原始事件流**（含敌方绝对血量 `target_hp_left`），真实 LLM 一读就泄露；改传 `filter_events_for` 过滤后的事件。`match.py` 一处改动，既有 random/scripted 玩家（不读事件）透明。
3. **渲染**：`render_observation` 只读白名单字段（敌方 name/types/hp_pct/energy/trait/已揭示技能/stat_mods/energy_cost_mods）——白名单里根本没有绝对血量等键，渲染层结构上碰不到。

## 验收命令与结果
- `uv run python -m rock_pvp_agent selfplay --a llm --b llm --games 1 --seed 7 --out runs/` → 有 key：真实 LLM；无 key：降级假LLM（记录 players 如实显示 `fake_llm`），仍打完 + `replay=✅`
- `uv run python -m environment replay runs/selfplay-7-1.json` → 轨迹仍可重放 ✅
- `uv run pytest tests/test_battle_player.py tests/test_selfplay.py -q` → 通过
- `uv run pytest -q` → **585 passed**（原 560 + 新增 25）
- `uv run pytest -q --cov=environment --cov=rock_pvp_agent` → 94% / 95%（都 ≥90%）
- `uv build --wheel` → 出包回归

## 记录
- **与 E5 蓝图的三处差异**：① 不做 `battle_observe` 工具（观测每回合新鲜传入，重读无意义，只留 `battle_act` 一个拦截工具）；② **补堵 `run_match` 事件泄露**（蓝图为覆盖，本节实现时才发现的真实泄密点）；③ `arena_tools.py`/`facade.py` 未建（工具在 `battle/player.py`，编排复用 E6 的 `run_selfplay`，不另造门面）。
- **无 key 降级在 `build_player` 收口**：`settings.has_api_key` 为 False → 返回 FakeLLMPlayer，轨迹记录 kind 如实为 `fake_llm`——不撒谎说它是 LLM。
- **历史隔离测试**：`test_selfplay_llm_history_isolation` 用记录 fake 钉死「b 渲染的己方全量文本（含绝对血量）绝不出现在 a 的 history」——结构保证 + 测试钉死双保险。
- **工具函数体从不执行**：`battle_act` 只是 schema 载体；`test_battle_act_tool_standalone_sentinel` 证明独立调用返回哨兵文案，而 `test_decide_intercepts_battle_act_into_decision` 证明历史里的 ToolMessage 是合成的「行动已提交。」而非工具返回值。
- **不变式**：玩家 RNG 流（含兜底 RandomPlayer）与引擎流分离；`run_match` 观测/事件都是迷雾口径；`environment` 仍零第三方依赖（LLMPlayer 住在 agent 层，`Player` 是 Protocol 跨线）。

## 下一步
E7（可选）观战页：浏览器观战（SSE 流），复用既有事件协议——人类 vs 假LLM 的**对战页**已随 E4+E5 交付，E7 只补观战流。或考虑打 `v0.2.0` 收尾。
