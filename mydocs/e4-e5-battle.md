# E4+E5：迷雾观测 + 人类 vs LLM（假LLM）对战 + 无平局 + 战斗 Web UI（2026-08-25）

状态：已实施（537 测试全绿，含新增 48 个：view 15 / draw 6 / fake_llm 6 / battle_ui 21）　前置：E0a–E3 + P1/P2 + UI 提级/组队页

## 1. 需求与四个 Part

负责人 2026-08-25 提出的 7 点，合并原计划 E4（观测隔离）/ E5（LLM 玩家）/ E7（战斗页）并加一条规则修改：

1. 人类与 LLM 经战斗引擎交互对战（测试期 LLM = **固定回复 + 随机动作的 FakeLLM**）；
2. 战前双方组队，**复用组队模块**（战斗页选**已存队伍**），进战前 `validate_team(…, VALID)` 强制校验，测试期 LLM 用固定预设队；
3. **不完全信息**：引擎持有完整状态；给玩家的展示按白名单屏蔽；
4. 轨迹持久化 + 随机性可复现（马尔可夫）；
5. **取消平局**：超时按 ①命数 → ②血量百分比和 → ③随机硬币 定胜负；
6. 战斗 UI。

两个负责人拍板的决策：
- **敌方系别可见**（公开图鉴数据，精灵名已可见）→ 伤害事件的 `eff`/`stab` 倍率保留；
- 战斗页组队 = **选已存队伍**（不在战斗页重造组队器）。

## 2. 迷雾口径（`view.py` + `visibility.py`）

**敌方白名单**（其余一律隐藏）：精灵名 / 系别 / `hp_pct`（百分比，非绝对血量）/ 能量值 / 剩余命数 /
特性描述（图鉴公开）/ **已揭示技能**（起始全未知，某精灵释放某技能后才揭示该技能详情含 desc）/
**增减益层数**（E4 修正 2026-08-26：双方阵营都有状态栏——常规增减益 / 特性层数 / 能耗减益，
`stat_mods` / `energy_cost_mods` 对敌方也输出；印记将来加）。
隐藏：六维、性格、血脉、IV、绝对血量、道具次数、未揭示技能。

- `environment/view.py`：`observe(state, viewer, "partial")` → `{me 全量, opponent 白名单}`；纯函数、不写状态。
- `environment/visibility.py`：`filter_events_for(viewer, events, state)` 展示级事件变换——敌方绝对血量
  → 百分比、敌方回复量/道具剩余次数 剥掉；**增减益可见** → `stat_change` / `switch`（cleared_layers）
  原样保留；**未登记事件类型 fail-closed 丢弃**。
- `environment/models.py`：`SideState.revealed: set[(队内下标, 技能名)]`——**进 to_dict/from_dict**
  （快照回放不丢迷雾，马尔可夫用例当场守护；参考项目不序列化 revealed 是已知弱点，本项目不做）。
- 揭示时机 = `engine.resolve_skill` 能量支付后（技能**确实释放**才揭示）。

## 3. 无平局（`engine.py`）

`end_turn` 超时分支改为 `timeout_winner(state) -> (胜方, 依据)`：
① 剩余命数多者胜 → ② `逐只 current*100//max` 的血量百分比和胜 → ③ `state.rng.choice(SIDES)`（走引擎流，
同 seed 可复现，`calls+1`）。`battle_end` 事件 `side == winner`，`message` 带判定依据。
**行为变化**：原先打到 20 回合平局的预设（asym / VALID p1 4v4）现在超时定胜负 → `rng_calls` 可能变为 1，
`test_environment_match.py::test_fast_slow_rng_calls_zero` 相应改为 `calls <= 1`。

## 4. 假 LLM 玩家（`rock_pvp_agent/battle/player.py`）

`FakeLLMPlayer(side, *, seed)`：`kind="fake_llm"`，`decide` 委托 `RandomPlayer`（随机合法动作，独立 RNG 流），
每回合生成**固定前缀**回复（`（假LLM）快速思考后决定：…`）供 UI/日志；`choose_replacement` 随机。
真实 LLM 玩家以后实现同一个 `Player` Protocol 即可（假玩家是可注入占位）。

## 5. 战斗编排（`ui/battle.py` + `ui/routes_battle.py`）

- `BattleController`：锁 + `BattleSession` + 假LLM + 阶段机 `decision →（人类阵亡）replacement → done`。
  `act(action, item)`：人类提交 → 假LLM 决定 → 双方 `submit` → `resolve` →（阵亡：a 暂停等 `replace`，
  b 自动代打）→ `end_turn`。逐回合记录 `(decisions, replacements, llm_reply, events, state_hash)` 原子写盘
  （`battles/{battle_id}.json`）。
- REST `/api/battle/*`：`start`（校验+固定预设）、`{id}` 快照、`{id}/act`、`{id}/replace`、
  `saved` / `load` / `replay`。`replay` 按 `(rules, seed, 双方 roster, 逐回合提交)` 重建全新 session 重放，
  逐回合 `state_hash` 比对——马尔可夫不变式的可执行验证。
- 固定预设队从 `__main__.py` 抽出到 `environment/presets.py`（`valid_spirit_candidates` / `p1_team` /
  `p1_preset` / `fixed_team`），CLI 与战斗页共用单一来源。

## 6. 战斗页（`ui/static/battle.html` + `battle.js` + `style.css`）

三屏：`#screen-build`（我方/对手已存队伍下拉 + 规模/命数/上限/种子 + 开始）→ `#screen-battle`
（我方全量卡 + 敌方迷雾卡[血量条按 %、能量、系别、特性、技能 ？？？→揭示、**双方状态栏**] +
行动面板 + 事件日志[含假LLM 回复行] + 结果横幅）→ `#screen-records`（已存对局：加载轨迹 + 重放验证）。
顶部导航三页互通（index/team/battle 的 top-nav 各加对战链接）。

**交互细节（E4 修正 2026-08-26）**：
- **状态栏**：双方阵营每张精灵卡下方显示增减益（`stat_mods` / `energy_cost_mods`），规范 = `单位加成 * 层数`
  （例：攻击 +100% → `物攻10% * 10`；特性触发的带 `特性·` 前缀；能耗减益 → `全技能能耗-1 * N`）；
- **技能悬停描述**：行动面板技能按钮 + 双方精灵卡的技能 chip，鼠标悬停弹出浮层
  （技能名 / 系别 / 类别 / 威力 / 能耗 / 中文描述），浮层跟随鼠标并钳制在视口内；
- **不可释放技能置灰**：当前回合能量不足的技能**按钮保留但 disabled**（点击无反应、悬停仍可看描述），
  不隐藏——玩家一眼看到「有这个技能但用不了」；
- **换人单按钮**：行动面板只有一个「⇄ 换人」按钮，点击弹出可换名单（存活后备），玩家从名单选择；
  无存活后备时按钮置灰。
- **回合信息文本**（2026-08-26）：战斗日志每条回合开头插一行回合头
  `── 第 N 回合 ── 我方：<动作摘要> · 敌方：<动作摘要>`（仿 CLI 的 `_turn_header`，从本回合事件反推）；
  补位续步与出招步同回合 → 不重复插头，事件按步增量追加（`events_turn` 由控制器显式给出，
  出招步/续步同值）。

## 7. 验证

```
uv run pytest -q                                  # 537 passed（原 489 + 新增 48）
uv run python -m environment battle --seed 7 --viewer a   # 迷雾演示：敌方血量%、技能???
uv run python -m environment battle --seed 7 --repeat 2 --quiet   # 确定性未破坏
uv run python -m environment battle --preset asym --quiet        # 不再平局，超时定胜负
uv run python -m ui                               # http://127.0.0.1:8001/battle 真人打一局
uv build --wheel                                  # 出包回归
```

**不变式**：E0 教学 digest 在非超时对局逐字节不变；`state.turn += 1` 仍只在 `end_turn` 一处；
`models.py` 无回合内字段（`revealed` 是回合间持久状态，进序列化）。

## 8. 边界与下一步

- **真实 LLM 玩家**：`Player` Protocol 已备好，FakeLLM 是占位；真实 LLM 应在 `view(side)` 迷雾口径 +
  `filter_events_for` 过滤后的事件上决策（当前随机策略不读观测，不涉密）。
- **观战 / 双 LLM 自博弈**：`timeout_winner` 与轨迹重放已就位，可作 E6 的基础。
- **SSE 推送**：当前人类是唯一主动方，请求-响应模型足够；将来加观战再上 SSE。
