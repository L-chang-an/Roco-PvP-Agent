# E 线审计 · 04 发现与修复建议

> 日期：2026-08-28　审计框架：`docs/project-audit-plan.md`（风险分级 P0–P3 / 证据状态 5 级）
> 纪律：**审计与修复分离**——本文件只记录证据与建议，修复在单独步骤执行。

## 发现的汇总

| 编号 | 风险 | 证据状态 | 影响 |
|---|---|---|---|
| AUD-E-001 | 人机对战向 LLM 对手泄漏敌方绝对血量（迷雾不变量被破坏） | **已证实**（最小复现） | P0（接真实 LLM 后）/ 当前潜伏 |
| AUD-E-002 | UI 对战轨迹缺策略/模型/数据指纹，追溯性不足 | 已证实（代码路径清晰） | P3 |
| AUD-E-003 | 历史 Gate 无法由提交复现（测试未随里程碑提交） | 已证实（git 证据） | P2（可复现性） |
| AUD-E-004 | `BattleController` 与 `drive_turn` 双回合循环（重复实现） | 已证实 | P2（结构，AUD-E-001 的温床） |

---

## AUD-E-001（P0，接真实 LLM 后激活）：人机对战事件流未按玩家视角过滤

- **风险等级**：P0（按审计方案 §5.1"LLM 读取不可见状态"定义）；**现状为潜伏**（本分支只有 fake_llm/random 对手，二者 `on_turn_result` 为 no-op，无实际读取者）。
- **根因位置**：[src/ui/battle.py:201](src/ui/battle.py#L201) `self._player.on_turn_result(self._session.view("b"), cur["events"])` —— 第二个参数是**未过滤的完整事件** `cur["events"]`（含敌方绝对血量 `target_hp_left`、回复量 `applied`、道具次数 `uses_left`）。
- **对照（正确实现）**：[src/environment/match.py:112](src/environment/match.py#L112) 同一条回调传的是 `filter_events_for(s, all_events, session.state)`（E6.5 迷雾收口）。`BattleController` 自实现回合循环（早于 E6.5），**没有随 `drive_turn` 一起收口**。
- **影响面**：[src/rock_pvp_agent/battle/prompts.py:174](src/rock_pvp_agent/battle/prompts.py#L174) `render_events` 对事件里的 `target_hp_left` **直接渲染**（只在 `target_hp_pct` 存在时才用百分比）→ 未过滤事件会以绝对血量进 LLM 历史。
- **最小复现**（已运行，见 `/tmp/eaudit/verify_fog_leak.py`）：A 队 = {迪莫,喵喵,火花}，B 队 = {板板壳,水蓝蓝,鸭吉吉}；`BattleController(opponent="fake_llm", player=CapturePlayer)` 打 10 回合 → B 方 `on_turn_result` 收到 `damage@… target=迪莫 target_hp_left=325/276/227/178`（4 处绝对血量）。
- **修复建议**（对齐 `drive_turn`）：`_finalize_turn` 改为 `filter_events_for("b", cur["events"], session.state)`。理想是让 `BattleController` 复用 `drive_turn` 消掉双循环（见 AUD-E-004）。
- **修复验收**：至少一个回归测试（B 方 `on_turn_result` 收到的事件无 `target_hp_left` / 有 `target_hp_pct`）+ 现有迷雾测试不回归。
- **可能副作用**：无（fake_llm/random 不读事件，人类侧已过滤，行为不变）。

## AUD-E-002（P3）：人机对战轨迹缺策略/模型/数据指纹

- **根因位置**：[src/ui/battle.py:144](src/ui/battle.py#L144) `record()` 只含 `{battle_id, saved_at, seed, opponent, rules, team_a, team_b, winner, done, turns}`。缺：`strategy`（进化手册文本）、`model`、`data_digest`（技能/精灵集合指纹）、`data source`。
- **影响**：人机轨迹无法与自博弈轨迹同口径追溯（审计方案 §10 第 6 条；也影响将来 R 线"跨版本记忆不可信"的指纹隔离）。
- **修复建议**：`record()` 增加 `"data_digest"`（复用 `reflect._data_digest` 的口径，抽到环境层或桥层共享）+ 可选的 `strategy/model` 元数据（不进 `state_hash`，不破坏重放）。
- **修复验收**：一条人机轨迹落盘后能读出 `data_digest`；`replay_record` 忽略新键（已有兼容）。

## AUD-E-003（P2）：历史 Gate 无法由提交复现

- **证据**：`git log -- tests/` 显示 E0b–E6.5 全部测试的唯一提交点是 `19f5b0e`（R 线前）；`f7a687d`（E7）只有 10 个测试文件 / 133 条。Gate 声称的 590 是未提交工作区上的数字。
- **影响**：不可复现的"通过"声明；本分支已修正（19f5b0e 上 590 可复现）。
- **建议**：本轮 E 线扩展起，**每个里程碑的 Gate 验收必须 `git status` 干净 + 在提交的 HEAD 上跑**（写入协作纪律），避免再出现"测试没进提交"。

## AUD-E-004（P2）：`BattleController` 与 `drive_turn` 双回合循环

- **证据**：迷雾口径在 `drive_turn`（match.py）收口，但 `BattleController`（ui/battle.py）保留自己的一套 `act→resolve→replace→finalize` 循环——事件过滤、补位、回合收尾各实现一遍。
- **影响**：AUD-E-001 正是这套重复实现的漏网处；两套循环将来还会各自漂移（如补位语义、事件增量）。
- **修复建议**（结构性，可纳入 E 线扩展阶段）：让 `BattleController` 复用 `drive_turn`（把"人类提供 decision、LLM 提供 decision、补位由人类/LLM 分别决定"表达成玩家协议），或至少把事件过滤抽成一个强制入口。
- **修复验收**：`BattleController` 驱动的对局与 `run_match` 对同一 (roster, seed, 提交序列) 的 `state_hash` 序列一致。

---

## 已独立验证通过的不变量（无发现）

| 不变量 | 验证方式 | 结果 |
|---|---|---|
| 跨进程确定性 | 同 seed 双进程 `run_match` digest 比对 | ✅ 逐位一致 |
| 重放篡改拒绝 | 篡改第 2 回合决策后 `replay_record` | ✅ `all_match=False`（原记录 True） |
| 非法动作零副作用 | 越界技能提交后 `state_hash` / `rng.calls` 不变 | ✅ |
| 属性边界 | 20 局随机对局扫描 HP∈[0,max]、能量∈[0,max] | ✅ 零越界 |
| 引擎零第三方依赖 | 全包 import 扫描 | ✅ 仅 stdlib（dataclasses/enum/random/json/hashlib/pathlib/functools/types/warnings/re/argparse） |
| 魔法数字收敛 | 系数/倍率只定义于 rules.py / types.py / statline.py | ✅ |
| 无 TODO/FIXME/HACK、无宽 except | 代码扫面 | ✅ |
| `revealed` 进序列化 | E0b 马尔可夫测试（from_dict(to_dict) 后同回合逐字节） | ✅（既有测试） |
| 观战流迷雾双证 | E7 `test_spectate_players_get_fogged_views` | ✅（既有测试，`run_spectate` 走 `drive_turn`） |

---

## 可安全修改 / 签名冻结清单（E 线扩展的耦合护栏）

**签名冻结（任何扩展不得改动其接口形状）**——R 线 11 个模块 + UI 依赖，改动即断链：

- `BattleSession.start` / `state` / `submit` / `resolve` / `submit_replacement` / `view`
- `run_match` / `drive_turn` / `MatchResult` / `TurnRecord`（`replace_a/b` 是重放必需输入）
- `replay_record` / 轨迹 record 契约（`store.py` 与 `analysis.py` 的 `_RECORD_REQUIRED` 双端一致）
- `Player` Protocol（`decide` / `choose_replacement` / `on_match_start` / `on_turn_result`）
- `build_roster` / `validate_team` / `TeamPick` / `p1_team` / `p1_preset` / `valid_spirit_candidates` / `build_battle_rules`
- `BattleRules` 字段名 / `Decision` / `legal_actions` / `filter_events_for` / `observe` / `state.clone()` / `state.state_hash()`
- `NATURE_BONUS` / `NEUTRAL_NATURE` / `is_valid_nature` / `STAT_KEYS`

**可安全修改（新增字段/效果，不影响既有调用方）**：

- `SkillEffect` 增字段（有默认值，frozen dataclass 只加不改删）；`SkillStatEffect` 同
- `TRAIT_CATALOG` 增特性（`Effect`/`EffectBinding` 协议已定型，`hooks.py` 增 Hook 时机或 `_CONDITIONS` 谓词）
- `battle_ready` 白名单扩展（改 `valid_skills.json` / P1/P2 效果表）——**数据层改动，`data_digest` 自动隔离**
- `view.py` 白名单增字段（**必须同时登记 `visibility.py` 处理，否则 fail-closed 丢弃**）
- `BattleRules` 增字段（有默认值）——注意 `_rules_to_dict` 用 `fields()` 自动覆盖

**改动必守的三条不变式**：

1. 确定性：同 seed 同提交序列逐字节可复现（`state_hash` 稳定）
2. 迷雾：任何新增可观测字段 → `view.py` 白名单 + `visibility.py` 过滤双登记
3. 轨迹契约：`store.py` ↔ `analysis.py` 的必需键不漂移
