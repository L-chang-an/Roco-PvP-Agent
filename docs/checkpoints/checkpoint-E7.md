# 里程碑 E7 检查点

日期：2026-08-26　状态：**☑ 通过**（Gate：浏览器全局视角看完一局 + LLM 仍迷雾；负责人 2026-08-26 确认）

## 目标（负责人 2026-08-26 指定）
人类用户以**全局视角**观看两个 LLM 之间的对战博弈过程；但两个 LLM 实际对战**仍遵循不完全信息的迷雾视角**。

## 我做了什么
| 文件 | 改动 |
|---|---|
| `src/environment/match.py` | **迷雾收口重构**：`run_match` 的回合循环体抽成 `drive_turn(session, players) -> TurnOutcome`（`turn`/`decisions`/`replaces`/`events`）——观测一律 `session.view(side)`、`on_turn_result` 事件一律 `filter_events_for`；`run_match` 与观战流共用，任何路径下 LLM 都拿不到全量信息（行为不变） |
| `src/rock_pvp_agent/battle/selfplay.py` | `_global_view(session)`（`observe(partial)["me"]` 取**双方**全量——绝对血量/全部技能/性格/血脉/IV）+ `run_spectate` 生成器：逐回合调 `drive_turn`，帧协议 `meta → state → turn* → done / error` |
| `src/ui/routes_battle.py` | `GET /api/battle/stream`（`seed/a/b/team_size/lives/max_turns`）→ `StreamingResponse`；**声明在 `/{battle_id}` 之前**；配置错误 → `error` 帧 |
| `src/ui/server.py` | `GET /spectate` → `FileResponse(spectate.html)` |
| `src/ui/static/spectate.html` + `spectate.js` | 观战页：URL query 自动连 EventSource（或配置条手动）；全量单位卡（绝对血量 + 全部技能悬停 TIP + 增减益 + 性格血脉 IV）+ 逐回合事件日志（回合头 + 双方决策摘要 + 全量事件行） |
| `src/ui/static/style.css` | `#spectate-field`（复用战场 grid）+ `#spectate-banner` + `#spectate-meta` |
| `src/ui/static/index.html` / `team.html` / `battle.html` | top-nav 各加 `👀 观战` |
| 测试 | `tests/test_selfplay.py`(+2：帧序 / 迷雾双证) + `tests/test_battle_ui.py`(+3：SSE 全局流 / 非法 kind error 帧 / 观战页) |

## 验收命令与结果
- `uv run pytest -q` → **590 passed**（原 585 + 新增 5）
- `uv run pytest -q --cov=environment --cov=rock_pvp_agent` → 94% / 95%（都 ≥90%）
- `uv run python -m ui &` + `curl -N "http://127.0.0.1:8001/api/battle/stream?seed=7&a=llm&b=random"` → meta → state → turn* → done 连续 data: 帧（空 key 时 `a=llm` 降级 fake_llm，meta 如实显示）
- 浏览器 `http://127.0.0.1:8001/spectate?seed=7&a=llm&b=llm` 看完一局
- `uv build --wheel` → 出包回归（spectate.html/js 已入 wheel）

## 迷雾双闸证明（关键正确性）
`test_spectate_players_get_fogged_views`：用 `_CapturePlayer` 包两个玩家跑 `run_spectate`——
① 玩家 decide 观测的敌方单位键 == 白名单（无绝对血量/隐藏字段）；② 观战帧 `state.a/b` 都是全量
（含 `max_hp`/`current_hp`）。观战全局 + LLM 迷雾同时成立，且由同一个 `drive_turn` 驱动（单一迷雾口）。

## 记录
- **`/stream` 字面路由必须在 `/{battle_id}` 之前**：FastAPI 按声明序匹配，否则 `stream` 被当 battle_id 吞掉（`/load` 已有先例）。
- **逐回合真直播**：`run_spectate` 是生成器，每回合结算后即 yield；真实 LLM 的秒级决策停顿如实流给观众，而非整局打完再播。
- **全局快照复用公开 `observe`**：`observe(state, side, "partial")["me"]` 本就是全量口径（己方），取双方即上帝视角——不新增引擎 API。
- **无 key 降级在 `build_player` 收口**：观战默认 `a=b=llm`，空 key 自动降级假LLM（`meta.players` 如实显示，不撒谎）。
- **前端复用而非重造**：unit-card / hpbar / skill-chip / status-bar / battle-log / log-turn / TIP 全部复用 battle 页既有样式与逻辑。

## 下一步
E 线（E0a–E7）全部里程碑交付完毕，`v0.2.0` 已打。后续可选：观战页加「LLM 视野」面板（显示每个 LLM 的迷雾观测，直观展示迷雾差异）；或转入 chat mode 的 M 线收尾。
