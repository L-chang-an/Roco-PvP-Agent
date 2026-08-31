# 里程碑 E4+E5 检查点（合并交付）

日期：2026-08-25/26　状态：**☑ 通过**（Gate：E4、E5 两个 gate 均通过——迷雾白名单 + 人类 vs 假LLM 对战 + 无平局 + 轨迹持久化；负责人 2026-08-26 确认）

## 目标（负责人 2026-08-25 提出的 7 点，合并 E4/E5/E7）
1. 人类与 LLM 经战斗引擎交互对战（测试期 LLM = **固定回复 + 随机动作的 FakeLLM**）；
2. 战前双方组队，**复用组队模块**（战斗页选**已存队伍**），进战前 `validate_team(…, VALID)` 强制校验，测试期 LLM 用固定预设队；
3. **不完全信息**：引擎持有完整状态；给玩家的展示按白名单屏蔽——己方全见；敌方只可见 精灵名 / 系别 / **血量百分比**（非绝对）/ 能量值 / 剩余命数 / 特性描述 / **已揭示技能**（用过才揭示，含详情）；
4. 轨迹持久化 + 随机性可复现（马尔可夫不变式）；
5. **取消平局**：超时按 ①命数 → ②血量百分比和 → ③随机硬币 定胜负；
6. 战斗 UI，为人类玩家提供便利交互。

两个负责人拍板的决策：**敌方系别可见**（公开图鉴）；战斗页组队 = **选已存队伍**。

## 我做了什么
| 文件 | 改动 |
|---|---|
| `src/environment/view.py` | `observe(state, viewer, mode="partial")` → `{me 全量, opponent 白名单}`；纯函数不写状态 |
| `src/environment/visibility.py` | `filter_events_for(viewer, events, state)` 展示级事件变换；**未登记事件类型 fail-closed 丢弃 + 告警** |
| `src/environment/models.py` | `SideState.revealed: set[(队内下标, 技能名)]`——进 to_dict/from_dict（快照回放不丢迷雾，马尔可夫用例守护）；`BattleState.reveal_skill` |
| `src/environment/engine.py` | `resolve_skill` 能量支付后揭示技能；`timeout_winner`（①命数 ②血量百分比和 ③`rng.choice(SIDES)` 走引擎流）；`end_turn` 平局分支改用它并带判定依据 |
| `src/environment/presets.py` | `valid_spirit_candidates`/`p1_team`/`p1_preset`/`fixed_team`（从 __main__ 抽出，CLI 与战斗页单一来源） |
| `src/environment/session.py` | `view(side)` → `observe(state, side, "partial")`（迷雾观测口） |
| `src/environment/__main__.py` | battle 加 `--viewer a|b`（过滤事件 + 打印敌方屏蔽观测摘要） |
| `src/rock_pvp_agent/battle/player.py` | `FakeLLMPlayer`：kind="fake_llm"，委托 `RandomPlayer`（独立 RNG 流），固定前缀回复「（假LLM）快速思考后决定：…」，`last_reply` 供 UI/日志 |
| `src/ui/battle.py` | `BattleController`：锁 + BattleSession + 假LLM + 阶段机 `decision→replacement→done`；`act`/`replace`/`snapshot`/`record`；逐回合 `(decisions, replacements, llm_reply, events, state_hash)` 原子写盘 |
| `src/ui/routes_battle.py` | REST `/api/battle/*`：start/saved/load/replay/`{id}`/act/replace；校验闸门（`extra="forbid"`、VALID 校验、相对路径防逃逸）；replay 重建全新 session 逐回合比 state_hash |
| `src/ui/static/battle.html`+`battle.js`+`style.css` | 三屏（组队/战斗/记录）+ 迷雾卡（敌方血量条按 %、技能 ？？？→揭示）+ 行动面板 + 事件日志 + 结果横幅 |
| 测试 | `test_environment_view`(18) / `test_environment_draw`(6) / `test_battle_fake_llm`(6) / `test_battle_ui`(20) |

## E4 修正（2026-08-26，负责人两轮细化）
1. **增减益可见**：双方阵营都有状态栏——常规增减益层数 / 特性层数 / 能耗减益。`view.py` 的敌方单位也输出 `stat_mods` / `energy_cost_mods`；`visibility.py` 对 `stat_change`/`switch` 不再剥字段。显示规范 = **`单位加成 * 层数`**（例：攻击 +100% → `物攻10% * 10`）。
2. **战斗页交互**：技能**悬停显示描述**（浮层，视口钳制）；当前回合不可释放的技能**按钮保留但 disabled**（点击无反应、仍可看描述）而非隐藏；**换人单按钮** → 点击弹出可换名单再选，而非每种换人一个按钮。
3. **回合信息文本**：战斗日志每条回合开头插一行回合头 `── 第 N 回合 ── 我方：<动作摘要> · 敌方：<动作摘要>`；补位续步与出招步同回合 → 不重复插头，事件按步增量追加（`events_turn` 由控制器显式给出）。顺带修了一个隐藏 bug：`replace` 原来返回整回合累积事件导致日志重复，改只返回增量事件。

## 验收命令与结果
- `uv run pytest tests/test_environment_view.py tests/test_environment_draw.py tests/test_battle_fake_llm.py tests/test_battle_ui.py -q` → 通过
- `uv run python -m environment battle --seed 7 --viewer a` → 迷雾演示：敌方血量%、技能 ???
- `uv run python -m environment battle --seed 7 --repeat 2 --quiet` → 确定性未破坏
- `uv run python -m environment battle --preset asym --quiet` → 不再平局，超时定胜负（行为变化：打到 20 回合上限的预设现在抽决胜硬币）
- `uv run python -m ui` → http://127.0.0.1:8001/battle 真人打一局
- 全套 `uv run pytest -q` → 全绿（E6 起点 539 条）

## 记录
- **敌方系别可见** → 伤害事件的 `eff`/`stab` 倍率保留给玩家（公开图鉴数据）。
- **`revealed` 必须进序列化**：参考项目不序列化 revealed 是已知弱点（快照回放丢迷雾）；本项目的马尔可夫性测试当场守护。揭示时机 = 技能**确实释放**才揭示（`resolve_skill` 能量支付后）。
- **无平局的代价**：原先打到 20 回合平局的预设（asym / VALID p1 4v4）现在超时定胜负 → `rng_calls` 可能变为 1；`test_fast_slow_rng_calls_zero` 相应改为 `calls <= 1`（语义：无出手顺序硬币，唯一一枚是决胜硬币）。
- **玩家 RNG 流与引擎流彻底分离**：FakeLLM/随机玩家用 `seed+1`/`seed+2` 独立流；同 seed 重放提交日志逐字节复现。
- **replay 是马尔可夫不变式的可执行验证**：按 `(rules, seed, 双方 roster, 逐回合提交)` 重建全新 session，逐回合 `state_hash` 比对。
- **术语**：轨迹记录 `battles/{id}.json`（gitignored），只存 `(rules, seed, rosters, 逐回合 decisions+replacements+events+state_hash)`。

## 下一步
E6 自博弈编排 + 轨迹落盘 + 引擎层重放（两个隔离玩家自我对战，轨迹可重放复现）——E6 测试期暂用 FakeLLM，真实 LLM 玩家在 E6.5。
