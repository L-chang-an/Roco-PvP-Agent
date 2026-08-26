# E6：自博弈编排 + 轨迹落盘 + 重放复现（2026-08-26）

状态：已实施（560 测试全绿，含新增 21：replay 5 / selfplay 16）　前置：E4+E5（迷雾 + 人类 vs 假LLM + 战斗页）

## 1. 范围与负责人拍板

E6 是计划终点「双 LLM 自博弈 + 轨迹落盘 + 重放复现」的第一站。**负责人拍板（2026-08-26）**：
E6 **暂用 FakeLLM**（与 E5 测试期一致），真实 LLM 玩家留到 **E6.5**。E6 专注三件事：
① 两个隔离玩家自我对战（`selfplay`）；② 轨迹只存 `(配置, seed, 逐回合提交序列 + state_hash)`；
③ 引擎层重放能力（`environment/replay.py`）——按提交序列重建全新 session、逐回合比对 `state_hash`
（马尔可夫不变式的可执行验证）。

**E6.5 预留缝**：真实 `LLMPlayer` 实现同一个 `environment.players.Player` Protocol 后由
`selfplay.run_selfplay(players=...)` 注入即可；迷雾口径已在本里程碑收口在 `run_match`（见 §3），
LLMPlayer 一接入就自动拿到正确的白名单观测。

## 2. 引擎层：`run_match` 语义升级 + `replay.py`

**`match.py`**（两处行为变化，均对既有 random/scripted 玩家透明）：
- `TurnRecord` 追加 `replace_a` / `replace_b`（**放末尾、带默认**）：本回合双方的补位选择（无 → None）。
  `run_match` 在补位循环里记录，写进 TurnRecord——**重放的必需输入**（补位是玩家策略的一部分）。
- `run_match` 4 处观测从 `session.observe(s)`（全量）改为 **`session.view(s)`**（E4 迷雾白名单）。
  这是迷雾隔离的收口点：玩家永远拿不到全量状态。`test_selfplay` 用 `_CapturePlayer` 钉死
  「传给玩家的敌方单位键 == 白名单，无绝对血量/六维/性格/血脉/IV」。

**`replay.py`（新）** `replay_record(record) -> dict`：
- 记录格式 = `{rules, team_a, team_b, seed, battle_id, turns:[{turn, decision_a, decision_b,
  replace_a, replace_b, state_hash}]}`，`team_a/b` 存 **roster spec**（`build_roster` 产物，
  `BattleSession.start` 直吃）——回放不依赖数据源、不重算六维、不漂移。
- 逐回合：`submit(Decision(**decision_a/b)) → resolve → need==a/b 时 submit_replacement(记录下标)`
  → 比对 `state_hash`。**首个失配即停**；缺必需键 → ValueError。
- `state_hash` 含 `battle_id`（models.py）→ 重放必须回传记录里的 battle_id（测试专门钉死）。
- 为什么能重放：引擎是 `(state, 双方提交) → state'` 的纯转移 + 玩家 RNG 流与引擎 RNG 流分离，
  所以「提交序列 + seed」足以逐字节复现。

**CLI**：`python -m environment replay runs/<id>.json` —— 逐回合 `expected vs actual` ✅/❌ 表，
末行「全部一致 ✅/存在失配 ❌」，失配返回 1（可进 CI）。

## 3. agent 层：`selfplay.py` + `store.py`

**`selfplay.py`** `run_selfplay(*, seed, team_size=3, lives=2, max_turns, a_kind="fake_llm",
b_kind="fake_llm", roster_a/b, players, out_dir, battle_id, saved_at) -> dict`：
- 管理员规则 `build_battle_rules` → 缺省 roster = `p1_preset(team_size)` + `build_roster(VALID)`
  → `BattleSession.start` → `run_match`（玩家见迷雾 view）→ 组 lean 记录 → 落盘（若 out_dir）
  → **`replay_record(record)` 自检**（每局现场验证轨迹可重放）。
- a/b 玩家独立 RNG 流（`seed+1` / `seed+2`），绝不共用引擎流；`players=` / `roster_a/b=` 为测试注入缝。

**`store.py`** `TrajectoryStore(out_dir)`：`save(record) -> Path`（battle_id 防穿越 → 同目录
tempfile.mkstemp + json.dump + fsync + `os.replace` 原子写 → 追加 `index.jsonl` 行）；
`index() -> list[dict]`（按文件序读，坏行/非 UTF-8/空行跳过——坏行容错）。记录 = `{version,
battle_id, saved_at, seed, players:{a,b}, rules, team_a, team_b, winner, done, turns}`。

**CLI**：`python -m rock_pvp_agent selfplay --games 2 --seed 7 --out runs/`
（`--a/--b fake_llm|random`、`--team-size`、`--lives`、`--max-turns`、`--verbose`）。
逐局 `seed_i = seed + i - 1`、`battle_id = selfplay-{seed_i}-{i}`，打
`game#i seed=.. winner=.. turns=.. rng_calls=.. replay=✅/❌ <path>`；任一局 replay 失配 → 返回 1。
真实 LLM 玩家（`--a llm`）在 E6.5 接入。

## 4. UI 复用：`routes_battle.py` 重放端点改用 `replay_record`

原 `POST /api/battle/replay` 自带一份重放循环；E6 把它**归一后转交引擎实现**：
`build_roster([TeamPick(**p)…], VALID, rules)` 得到 spec → `{**data, team_a: roster_a, team_b: roster_b}`
→ `replay_record`。行为不变（现有 20 条 test_battle_ui 作守卫），消灭第二份重放逻辑。

## 5. 验证

```
uv run pytest -q                                  # 560 passed（原 539 + 新增 21）
uv run pytest -q --cov=environment --cov=rock_pvp_agent   # 94% / 93%（都 ≥90%）
uv run python -m rock_pvp_agent selfplay --games 2 --seed 7 --out runs/   # 两局 + index.jsonl 2 行 + replay=✅
uv run python -m environment replay runs/selfplay-7-1.json                # 逐回合 hash 一致，退出码 0
uv run python -m rock_pvp_agent selfplay --games 1 --seed 7 --out runs/   # 同 seed 再跑，轨迹逐字节一致（确定性）
uv build --wheel                                  # 出包回归
```

**不变式**：`state.turn += 1` 仍在 `end_turn` 一处；玩家 RNG 流与引擎流分离；`replay_record`
零副作用（重建全新 session，不改输入）。

## 6. 边界与 E6.5

- **真实 LLMPlayer**：`Player` Protocol + `run_selfplay(players=)` 缝已备好；LLMPlayer 在 `view(side)`
  迷雾口径上决策（本里程碑已用 `_CapturePlayer` 证明 run_match 只喂白名单）。
- **双 LLM 并行决策**：E6 的 fake 玩家是串行 decide；真实 LLM 的私有 history / 独立实例 / battle_act
  拦截在 E6.5。
- **记录未存事件/观测**：体积小、可复现；观测可经重放重算。若要训练数据（每回合观测文本），
  E6.5 在 store 加可选字段即可，重放语义不受影响。
