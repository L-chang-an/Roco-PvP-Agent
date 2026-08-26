# 里程碑 E0b 检查点

日期：2026-08-24　状态：**☑ 通过**（Gate：五条验收命令 + 负责人 2026-08-26 确认；阵亡补位 / 阵亡即回合结束两项指定落地）

## 目标
E0b 回合内核：注入式种子 RNG + 领域模型（可完整往返序列化的回合间快照）+ 动作/伤害漏斗 + 回合循环（局部 `TurnContext`）+ 两个零 LLM 策略自己打完一局，同 seed 逐字节复现。全程零第三方依赖、零网络、零文件写入。

## 我做了什么
| 文件 | 改动 |
|---|---|
| `src/environment/rng.py` | `BattleRng`：seed 必填的注入式 RNG，只暴露 `choice()`，每次抽取计数 |
| `src/environment/models.py` | `SIDES`/`ActionType`/`Skill`/`StatModifier`/`Unit`/`SideState`/`BattleState` + `aggregate_stats`/`build_unit`/`new_battle`/`to_dict`/`from_dict`/`clone`/`state_hash` |
| `src/environment/events.py` | `EVENT_TYPES` 登记表 + `ev()` 唯一事件构造器（每条必带 type/side） |
| `src/environment/actions.py` | 动作工厂 + `Decision` + `skill_block_reason` 唯一谓词 + `legal_actions`/`legal_items`/`validate_decision` |
| `src/environment/damage.py` | `compute_damage`（纯函数）+ `apply_hp_loss` + `apply_heal`（三个唯一入口） |
| `src/environment/engine.py` | `TurnContext`/`build_turn_context`/`build_queue`/`resolve_*`/`settle_faints`/`check_winner`/`end_turn`/`execute_turn`/`step` |
| `src/environment/session.py` | `BattleSession`：同时提交缓冲 + 合法性闸门 + 只读观测（SUBMIT→RESOLVE→补位→END_TURN） |
| `src/environment/players.py` | `Player` Protocol + `ScriptedPlayer` + `RandomPlayer`（独立 RNG 流） |
| `src/environment/match.py` | `TurnRecord`/`MatchResult`（含 `digest()`）+ `run_match` 整局驱动 |
| `src/environment/__init__.py` | 对外 re-export（`new_battle`/`BattleSession`/`run_match`/`RandomPlayer`/`step`） |
| `src/environment/__main__.py` | `battle` 子命令（`--seed`/`--repeat`/`--preset`/`--quiet`/`--json`） |
| `tests/rosters.py` | 手写阵容助手（`pick`/`team`/`mirror_pair`/`strong_weak`/`tanky_pair`/`fast_slow`/`duel`/`spec`，杜绝 `[spec]*n`） |
| `tests/test_environment_actions.py` | 18 条：动作空间 / 道具 / 合法性（池与谓词与校验三者一致） |
| `tests/test_environment_engine.py` | 39 条：伤害 / 应对三角 / 增益层 / 队列 / 终局 / 事件 |
| `tests/test_environment_match.py` | 23 条：马尔可夫性 / 序列化 / 确定性 / 交互式补位 / 编排 |
| `docs/checkpoints/checkpoint-E0b.md` | 本文件 |

## 验收命令与结果
- `uv run python -m environment battle --seed 20260823` → 完整对局事件流人眼可读：应对三角三种都出现、`faint→life_loss→replace` 链、`battle_end` 最后一条、道具只一次
- `uv run python -m environment battle --seed 20260823 --repeat 2 --quiet` → 两次 digest 一致 ✅（确定性）
- `uv run python -m environment battle --seed 20260823 --preset asym --quiet` → `rng_calls=0`（速度互异 ⇒ 从不抽平手硬币 ⇒ 随机点没泄漏到别处）
- `uv run pytest tests/test_environment_actions.py tests/test_environment_engine.py tests/test_environment_match.py -q` → 通过
- 全套 `uv run pytest -q` → 全绿

## 用户的疑问 / 修改要求（两项指定，2026-08-24）
1. **阵亡补位由玩家/策略决定**（替代原「强制补位」）：结算到第一个阵亡时回合**暂停**，`Player.choose_replacement` 从存活后备里选，`submit_replacement` 应用后才走回合末收尾；`execute_turn`/`step` 保留默认补位（第一个后备）供 MCTS/回放。
2. **阵亡即回合结束**：`resolve_turn` 在第一个阵亡处停止，剩余队列条目整体丢弃（连 `skipped` 都不发）。

## 记录
- **回合推进只在一处**：`state.turn += 1` 与 `battle_end` 发射只在 `end_turn`；源码扫描断言只出现一次。
- **回合内派生量不落状态**：全部住在局部 `TurnContext`，`end_of_turn()` 是 `pass`；源码扫描断言 `models.py` 里不出现 `TurnContext`。
- **马尔可夫性测试**（最重要的一条）：`s2 = BattleState.from_dict(s1.to_dict())` 后跑同一回合 → 事件流逐字节相同、`state_hash` 相同——参考项目做不到（它没有 `from_dict`）。
- **玩家 RNG 流与引擎 RNG 流分离**：`RandomPlayer` 自带独立 `BattleRng(seed)`，绝不共用引擎流；否则「同 seed 重放提交日志」复现不了轨迹（E6 轨迹回放直接失效）。
- **`rng is None` 静默回退全局 `random` 的坑**：`BattleRng(seed)` 是 `BattleState` 必填字段，无 None 分支。
- **`0.0 is falsy`**：数值字段绝不写 `x or default`；减伤比例是一等字段，引擎不认识正则。
- **队列用 `continue` 不用 `break`**：真队列最多 4 条，中间条目阵亡仍结算后续（发 `skipped`）。
- **防御标记武装在建队列之前**：`build_turn_context` 是回合循环第①步，后手防御方能为先手攻击减伤。
- **术语已定**：只有 `lives`（不叫 mp）、`side`（不叫 team）、回合从 1 开始、`TurnRecord.turn` 取提交时回合号。
- **`[spec]*n` 造 roster 的坑**：参考测试用共享 dict 引用，任何 mutate 全队串味；本测试助手一律推导式逐个构造。

## 下一步
E2 属性克制与系别（克制表 + STAB + 血脉系别语义澄清）——E1 黄金基线已由负责人 2026-08-24 决定跳过，验收改为「测试 + 逐条审阅」。
