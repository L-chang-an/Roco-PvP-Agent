# E6.5：真实 LLM 对战玩家（接 E6 自博弈，计划终点最后一环）（2026-08-26）

状态：已实施（585 测试全绿，含新增 25：test_battle_player 20 / test_selfplay +5）　前置：E6（自博弈编排 + 轨迹 + 重放）

## 1. 目标与范围

真实 LLM 当一方出招，接进 E6 的 `run_selfplay`（`--a llm --b llm`）；**无 key / LLM 异常自动降级**，
一局照样打完；轨迹仍可重放。E6.5 之前 E5 只交付了测试期 FakeLLM，本节补上真实玩家。

**三件事各归其位**：
- `LLMPlayer`（`battle/player.py`）：真实 LLM 走 tool-call 循环出招；
- `battle/prompts.py`：系统提示 + 迷雾观测/补位/事件渲染（只消费白名单字段）；
- 集成：`run_selfplay` / CLI 的 `--a/--b llm`，无 key 降级假LLM。

## 2. LLMPlayer：拦截式工具循环

`LLMPlayer(side, *, settings, seed, llm=None, max_retries=3)` 实现 `environment.players.Player`
Protocol（`on_match_start` / `decide` / `choose_replacement` / `on_turn_result`）。

- **唯一工具 `battle_act_{side}`**（`build_side_tools`，`@tool` 构造，函数体从不被调用——只是
  schema 载体）。工具名带 side 后缀 → `build_chat_llm` 的缓存键（settings + 工具名集合）自然分键
  → **两侧各持一个独立 LLM 实例**，不共用缓存实例（E6.5 的核心隔离要求）。
- **battle_act 拦截而非执行**：模型调它 → `_parse_decision` 解析成 `Decision`（action_type ∈
  skill|switch|recharge；skill/switch 的 target 是槽位下标）→ 补一条**合成 ToolMessage**
  （「行动已提交。」/非法原因）——历史可重放（网关 400 的坑：每个 tool_use 必须有 ToolMessage）。
- **非法重试 ≤max_retries**：非法提交把原因回灌 ToolMessage，LLM 看到后修正；耗尽 → 随机兜底
  （`RandomPlayer`，独立 RNG 流，不碰引擎流）。**LLM 抛异常不重试，直接兜底**（宁失败不抛）。
- **每回合恰好一次 battle_act**：模型没调（或调未知工具）→ 提示重试；一个响应里多余调用 → 忽略取第一个。
- **私有 history**：系统提示 + 每回合渲染后的迷雾观测 + 自己的工具往来 + 回合事件摘要；
  绝不注入另一侧的观测（测试 `test_selfplay_llm_history_isolation` 钉死：b 的全量视图文本不出现在 a 的 history）。

## 3. 迷雾口径的两道闸

1. **观测**：`run_match` 只喂 `session.view(side)`（E6 已收口）；LLMPlayer 渲染它。
2. **事件**：**E6.5 新发现并修补**——`run_match.on_turn_result` 原传**原始事件流**（含敌方绝对血量
   `target_hp_left`），真实 LLM 一读就泄露。改为传 `filter_events_for(side, events, state)` 过滤后的
   事件（敌方血量 → 百分比）。`match.py` 一处改动，E6.5 的 LLM 与既有 random/scripted 玩家（不读事件）均透明。
3. **渲染**：`render_observation` 只读白名单字段（我方全量 / 敌方 name/types/hp_pct/energy/trait/已揭示技能/
   stat_mods/energy_cost_mods）。白名单里根本没有绝对血量等键 → 渲染层结构上碰不到（`test_render_observation_foe_has_no_absolute_hp`）。

## 4. 无 key / 异常降级

- **无 key**：`build_player("a", "llm", ...)` 在 `settings.has_api_key` 为 False 时返回 **FakeLLMPlayer**
  （记录里 players kind 如实显示 `fake_llm`）——CLI `--a llm --b llm` 无 key 照样打完。
- **异常**：`LLMPlayer.decide` 内 `llm.invoke` 抛异常 → 当回合随机兜底，不崩、轨迹不中断。

## 5. 文件清单

| 路径 | 用途 |
|---|---|
| `src/rock_pvp_agent/battle/prompts.py`（新） | `BATTLE_PLAYER_SYSTEM_PROMPT` + `render_observation` / `render_replacement` / `render_events` + `_describe_action`（100% 覆盖） |
| `src/rock_pvp_agent/battle/player.py`（改） | `build_side_tools` + `LLMPlayer`（98% 覆盖） |
| `src/environment/match.py`（改） | `on_turn_result` 传 `filter_events_for` 后的事件 |
| `src/rock_pvp_agent/battle/selfplay.py`（改） | `build_player` 支持 `llm`（无 key 降级）+ `run_selfplay` 增 `settings` 参数 |
| `src/rock_pvp_agent/__main__.py`（改） | `selfplay --a/--b` choices 加 `llm` |
| `tests/test_battle_player.py`（新） | 拦截/重试/兜底/历史可重放/迷雾渲染/工具独立/边界 20 条 |
| `tests/test_selfplay.py`（改） | 双 LLMPlayer 自博弈、无 key 降级、历史隔离、CLI +5 条 |

**偏离 E5 蓝图一处（说明理由）**：不做 `battle_observe` 工具——观测每回合由编排器新鲜传入提示词，
中途重读无意义；只留 `battle_act` 一个拦截工具，简化拦截逻辑与测试面。

## 6. 验证

```
uv run pytest -q                                  # 585 passed（原 560 + 新增 25）
uv run pytest -q --cov=environment --cov=rock_pvp_agent   # 94% / 95%（都 ≥90%）
uv run python -m rock_pvp_agent selfplay --a llm --b llm --games 1 --seed 7 --out runs/   # 有 key：真实 LLM；无 key：降级假LLM
LLM_API_KEY= uv run python -m rock_pvp_agent selfplay --a llm --b llm --games 1 --seed 7 --out runs/   # 无 key 降级，仍打完 + replay=✅
uv run python -m environment replay runs/selfplay-7-1.json   # 轨迹仍可重放 ✅
uv build --wheel                                  # 出包回归
```

**不变式**：玩家 RNG 流（含兜底 RandomPlayer）与引擎流分离；`run_match` 观测/事件都是迷雾口径；
`environment` 仍零第三方依赖（LLMPlayer 住在 agent 层，`Player` 是 Protocol 跨线）。

## 7. 边界

- **训练数据**：E6 记录未存观测文本；E6.5 的 LLMPlayer 每次渲染都在内存里。要采训练数据，
  在 store 加可选字段记录每回合 `render_observation` 输出即可，重放语义不受影响。
- **UI 战斗页接真实 LLM**：`BattleController._finalize_turn` 目前把**原始事件**传给 FakeLLM（它不读，
  安全）。若把 LLMPlayer 接进 Web 对战页，`on_turn_result` 那处也要过滤（同 run_match 的做法）。
- **历史增长**：20 回合每侧约百条消息，token 可控；更长对局/多场复用时可加裁剪（truncate），暂不做。
