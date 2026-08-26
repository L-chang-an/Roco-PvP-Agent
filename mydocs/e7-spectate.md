# E7：观战页（人类全局视角观看双 LLM 对战，LLM 仍迷雾）（2026-08-26）

状态：已实施（590 测试全绿，含新增 5：selfplay 2 / battle_ui 3）　前置：E6（自博弈）+ E6.5（真实 LLMPlayer）

## 1. 需求与关键正确性点

负责人要求：**人类用户以全局视角观看两个 LLM 之间的对战过程，但两个 LLM 实际对战仍遵循不完全信息的迷雾视角。**

正确性点的结构保障：
- **观战者**拿到上帝视角——双方全量（绝对血量 / 全部技能含 desc / 性格 / 血脉 / IV / 增减益）+ 全量事件（含绝对数值）。
- **两个 LLM 玩家**经 `drive_turn` 只拿各自的 `view()` 白名单 + `filter_events_for` 过滤后事件——
  观战流不是第二条泄密路径。

## 2. 迷雾收口重构：`drive_turn`（engine/match.py）

把 `run_match` 的回合循环体（decide→submit→resolve→补位循环→on_turn_result）抽成
`drive_turn(session, players) -> TurnOutcome`（`turn`/`decisions`/`replaces`/`events`）：
**迷雾口径收口在这一处**——观测一律 `session.view(side)`、`on_turn_result` 事件一律
`filter_events_for(side, …)`。`run_match`（回放/记录）与观战流（`run_spectate`）共用它，任何路径下
LLM 都拿不到全量信息。行为不变，既有 69 条相关测试守卫重构。

## 3. 观战流：`run_spectate`（battle/selfplay.py）

`run_spectate(*, seed, a_kind="llm", b_kind="llm", team_size, lives, max_turns, settings, players=…, …)`
是**生成器**：装配与 `run_selfplay` 一致（管理员规则 → p1 预设阵容 → BattleSession → build_player），
`on_match_start` 喂 `view()`，逐回合调 `drive_turn`，每回合 yield 一帧。帧协议：

```
meta   {battle_id, seed, rules, players: {a,b 实际 kind}, team_a, team_b}   ← 降级后 kind 如实
state  {turn: 0, state: 全局快照}                                             ← 开局上帝视角
turn*  {turn, state: 全局快照, decisions: {a,b}, events: 全量事件}            ← 每回合
done   {winner, turn}                                                          ← 终局
error  {message}                                                               ← 配置错误（如非法 kind）
```

`_global_view(session)` 用公开 `observe(state, side, "partial")["me"]` 取**双方**全量——观战者无所遮蔽；
与玩家拿的 `session.view(s)` 是两套口径。

## 4. UI：SSE 端点 + 观战页

- `GET /api/battle/stream`（`routes_battle.py`，**声明在 `/{battle_id}` 之前**，否则被 path 参数吞掉）：
  query `seed/a/b/team_size/lives/max_turns` → `StreamingResponse` 包 `run_spectate`，`data: {json}\n\n`；
  配置错误 → `error` 帧。
- `GET /spectate`（`server.py`）→ `static/spectate.html`。
- `spectate.html` + `spectate.js`：读 URL query 自动连 EventSource（或配置条手动开始）；渲染全量单位卡
  （绝对血量 / 全部技能悬停 TIP / 增减益 / 性格血脉 IV）+ 逐回合事件日志（回合头 + 双方决策摘要 +
  全量事件行）。复用 battle 页的 unit-card / hpbar / skill-chip / status-bar / battle-log / log-turn 样式。
- 三个既有页面（index/team/battle）top-nav 各加 `👀 观战`。

## 5. 验证

```
uv run pytest -q                                  # 590 passed（原 585 + 新增 5）
uv run pytest -q --cov=environment --cov=rock_pvp_agent   # 94% / 95%（都 ≥90%）
uv run python -m ui &                             # http://127.0.0.1:8001
curl -N "http://127.0.0.1:8001/api/battle/stream?seed=7&a=llm&b=random"   # meta → state → turn* → done
# 浏览器 http://127.0.0.1:8001/spectate?seed=7&a=llm&b=llm 看完一局
uv build --wheel                                  # 出包回归
```

**迷雾双闸证明（测试）**：`test_spectate_players_get_fogged_views` —— 用 `_CapturePlayer` 包两个
玩家跑 `run_spectate`，断言玩家 decide 观测敌方键 == 白名单（无绝对血量），而发出的 turn 帧
`state.a/b` 都是全量（含 `max_hp`/`current_hp`）——观战全局 + LLM 迷雾同时成立。

## 6. 边界

- **真实 LLM 逐回合停顿如实直播**：`run_spectate` 是生成器，每回合结算后即 yield；真实 LLM 决策的
  秒级停顿会如实流给观众（而非整局打完再播）。同步生成器在 StreamingResponse 线程池里跑，单用户本地够用。
- **观战页默认 a=b=llm**：无 key 自动降级假LLM（`meta.players` 如实显示）；`?a=fake_llm&b=random` 等
  任意组合可用。
- **可选增强**：在观战页加「LLM 视野」面板（显示每个 LLM 的迷雾观测）可直观看到迷雾差异——当前未做，
  帧已含玩家决策与全量事件，加面板只需渲染 `session.view(s)` 快照。
