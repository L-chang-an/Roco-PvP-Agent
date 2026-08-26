# 里程碑 E6 检查点

日期：2026-08-26　状态：**☑ 通过**（Gate：两局自博弈 + 轨迹落盘 + CLI 重放逐回合 hash 一致 + 迷雾白名单；负责人 2026-08-26 确认）

## 目标
两个互相隔离的玩家自我对战，轨迹落盘，并能**由提交序列重放复现逐回合 `state_hash`**——E0b 那条马尔可夫性不变式的回报。**负责人拍板（2026-08-26）**：E6 **暂用 FakeLLM**（与 E5 测试期一致），真实 LLM 玩家移入 **E6.5**。E6 专注编排 + 落盘 + 引擎层重放能力本身。

## 我做了什么
| 文件 | 改动 |
|---|---|
| `src/environment/match.py` | `TurnRecord` 加 `replace_a`/`replace_b`（补位选择，重放的必需输入）；`run_match` 记录补位 + **4 处观测从全量 `observe()` 改迷雾 `view()`**（E4 白名单，E6.5 真实 LLM 接入即自动得正确口径） |
| `src/environment/replay.py` | `replay_record(record)`：按 `(rules, roster spec, seed, 逐回合提交序列)` 重建全新 session，逐回合比 `state_hash`，首个失配即停；缺必需键 → ValueError。**引擎层单一实现**（E6 CLI / UI 端点 / 自博弈自检共用） |
| `src/environment/__main__.py` | `replay <path>` 子命令：逐回合 `expected vs actual` 表，失配返回 1（可进 CI） |
| `src/environment/__init__.py` | 导出 `replay_record` |
| `src/rock_pvp_agent/battle/selfplay.py` | `run_selfplay`：管理员规则 → p1 预设阵容 → `BattleSession` → `run_match` → lean 记录 → `TrajectoryStore.save` → **`replay_record` 现场自检**。`players=`/`roster_a/b=` 为测试注入缝（E6.5 接真实 LLMPlayer） |
| `src/rock_pvp_agent/battle/store.py` | 轨迹存储：**只存 `(rules, 双方 roster spec, seed, 逐回合提交序列)` + `state_hash`**，不存整份状态；原子写（同目录临时文件 + `os.replace`）+ 追加式 `index.jsonl` + 坏行容错 + battle_id 防穿越 |
| `src/rock_pvp_agent/__main__.py` | `selfplay` 子命令（`--games`/`--seed`/`--out`/`--a/--b fake_llm\|random`/`--team-size`/`--lives`/`--max-turns`） |
| `src/ui/routes_battle.py` | `/api/battle/replay` 归一后转交 `replay_record`——消灭第二份重放逻辑 |
| 测试 | `tests/test_environment_replay.py`(5) + `tests/test_selfplay.py`(16，E6 部分) |

## 验收命令与结果
- `uv run python -m rock_pvp_agent selfplay --games 2 --seed 7 --out runs/` → 两局打完，`runs/index.jsonl` 新增 2 行，`replay=✅`
- `uv run python -m environment replay runs/selfplay-7-1.json` → 逐回合 hash 与落盘完全一致 ✅，退出码 0
- `uv run python -m rock_pvp_agent selfplay --games 1 --seed 7 --out runs/` → 同 seed 再跑，轨迹逐字节一致（确定性）
- `uv run pytest -q` → **560 passed**（原 539 + 新增 21）
- `uv run pytest -q --cov=environment --cov=rock_pvp_agent` → 94% / 93%（都 ≥90%）
- `uv build --wheel` → 出包回归

## 用户的疑问 / 修改要求（负责人拍板）
- **E6 暂用 FakeLLM**：`--a fake_llm --b fake_llm`（与 E5 测试期一致）；真实 LLM 玩家留到 **E6.5**（本里程碑把迷雾收口在 `run_match`，E6.5 一接入就自动只见白名单）。

## 记录
- **重放是引擎层单一实现**：`environment.replay.replay_record` 同时服务 E6 CLI、UI 端点、自博弈自检——杜绝第二份重放逻辑漂移（UI 的 `routes_battle.replay` 归一后转交）。
- **记录存 roster spec 而非 picks**：`build_roster` 的产物（纯 JSON，`BattleSession.start` 直吃）——回放不依赖数据源、不重算六维、不漂移。
- **`state_hash` 含 `battle_id`** → 重放必须回传记录里的 battle_id（`test_replay_battle_id_in_state_hash` 专门钉死）。
- **为什么只存提交序列**：引擎是 `(state, 双方提交) → state'` 的纯转移 + 玩家 RNG 流与引擎流分离，两条都在 E0b 就做到了，`replay.py` 因此只有几十行。
- **迷雾隔离在 `run_match` 收口**：`test_selfplay_players_get_fogged_views` 用 `_CapturePlayer` 钉死「传给玩家的敌方单位键 == 白名单，无绝对血量/六维/性格/血脉/IV」。
- **偏离原文件清单两处**：① 不加 `TurnRecord.observation_a/b`（E6 存 lean、观测可经重放重算，迷雾收口在 view() 而非落盘）；② 不做 `MatchResult.to_jsonl`（持久化归 `store.py` 一个口子）。

## 下一步
E6.5 真实 LLM 对战玩家：`LLMPlayer` 实现同一个 `Player` Protocol，经 `run_selfplay(players=...)` 注入（`--a llm`）；无 key/异常自动降级假LLM，一局照样打完。
