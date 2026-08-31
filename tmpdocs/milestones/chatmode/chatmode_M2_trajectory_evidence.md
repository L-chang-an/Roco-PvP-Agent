# M2 — 轨迹证据：归一化 + 聚合 + 分开统计（详细实施计划）

> 上级：`chatmode_plan_v2.md` §5 M2　|　依赖：M1（`data_digest`/`rules_digest`）　|　产出给：M3（`query_trajectory_evidence`）、M4（Skill 晋级来源）
> 分支前提：`feat/e-line-v2`；**不**用 `main` 的 `battle/evolution/analysis.py`（本分支没有），从零建一条轻量聚合层。

## 1. 目标

把两类已存在的原始轨迹（人机对战 `battles/`、自博弈 `runs/`）归一化成统一的 `TrajectoryEvidence`，做构筑级统计，并**把「版本闸」「重放闸」落到实处**：只有 `replay_ok` 且版本匹配的轨迹才能成为回答的硬证据。人机与自博弈**分别报告**，绝不合并成一个胜率。

## 2. 前置与依赖

- M1 的 `fingerprint.data_digest/rules_digest`。
- `environment.replay.replay_record`（[replay.py:27](src/environment/replay.py#L27)）——重放自检。
- `rock_pvp_agent.battle.store.TrajectoryStore`（[store.py:25](src/rock_pvp_agent/battle/store.py#L25)）——自博弈索引读取。
- 已核实的**两类记录 schema（本里程碑的归一化输入）**：

| 维度 | 人机对战 `battles/*.json` | 自博弈 `runs/*.json` |
|---|---|---|
| 顶层键 | `version,battle_id,saved_at,seed,opponent,rules,team_a,team_b,winner,done,turns` | `version,battle_id,saved_at,seed,players,rules,team_a,team_b,winner,done,turns` |
| `team_a[0]` 形态 | **TeamPick**：`{spirit,skills,bloodline,nature,iv}` | **roster**：`{name,types,stats,skills,nature,bloodline,iv,trait}`（`base_stats` 仅新记录有，旧记录缺省） |
| `turns[i]` 键 | `turn,decision_a,decision_b,replace_a,replace_b,llm_reply,events,state_hash` | `turn,decision_a,decision_b,replace_a,replace_b,state_hash` |
| 对手标识 | `opponent`（如 `fake_llm`） | `players` dict（`{a,b}`） |
| 索引 | 无（目录 glob） | `runs/index.jsonl`（`TrajectoryStore.index()`） |
| **是否含 data_digest** | **否** | **否** |

> ⚠️ 关键发现：两类记录**都不存储 `data_digest`**，且 `team_a` 形态不一致（TeamPick vs roster）。
> 这直接决定了下文的「版本闸」与「重放闸」的实现方式。

## 3. 交付物

| 文件 | 职责 |
|---|---|
| `src/rock_pvp_agent/advisor/trajectory.py` | 发现/归一化/重放门/聚合/查询 |
| 改 `src/rock_pvp_agent/battle/selfplay.py` | 落盘时 stamp `data_digest`/`rules_digest` |
| 改 `src/ui/battle.py` | `BattleController.record()` 组装人机记录时 stamp `data_digest`/`rules_digest` |
| `tests/test_advisor_trajectory.py` | 归一化 + 聚合 + CI + 版本闸用例 |

## 4. 详细设计

### 4.1 发现（Discovery）

- 自博弈：`TrajectoryStore(runs_dir).index()` → 逐条 `runs/{battle_id}.json` 读记录。
- 人机：glob `battles/*.json`（跳过 `index.jsonl` 等非记录文件）。
- 目录可配置：`battles_dir`/`runs_dir` 由 Settings 或构造参数注入（测试用 tmp 目录）。

### 4.2 归一化（Normalize）→ `TrajectoryEvidence`

```python
@dataclass(frozen=True)
class TrajectoryEvidence:
    kind: str                 # "human" | "selfplay"
    evidence_id: str          # f"{kind}:{digest8}:{battle_id}"（digest8 = data_digest 前 8 个 hex 字符；区别于 §4.2.1 team_key 的 16 位 sha256 截断）
    battle_id: str
    team_key: str             # 规范化队伍指纹（见 4.2.1）
    opponent_key: str
    winner_side: str          # "a" | "b" | None
    winner_key: str           # 胜方 team_key（用于“某构筑的胜率”）
    rules_digest: str         # 记录内 rules 的 rules_digest
    data_digest: str          # 记录内 data_digest；旧记录 = "unknown"
    replay_ok: bool
    sample_seed: int
```

#### 4.2.1 `team_key`（构筑规范化指纹）

两类记录的队伍形态不同，需统一成**可比较的键**（用于“同一构筑”去重与胜率聚合）：

- 自博弈：从 roster 直接取 `sorted((u["name"], tuple(u["skills"]), u["bloodline"], u["nature"], tuple(sorted(u["iv"].items()))) ...)`，再整体排序 → sha256 前 16 位。
- 人机：`team_a` 是 TeamPick（`spirit/skills/bloodline/nature/iv`），用同样字段拼键；`spirit` 缺失（防御性分支，正常路径不会产生）时记 `team_key = "unknown:{battle_id}"`，**不参与胜率聚合**，只计数“不可用样本”。
- `winner_key`：胜方 roster/pick 的 `team_key`。`winner` 缺失或非 `a/b` 时 `winner_key=None`（不计入胜率，只计入样本量）。

#### 4.2.2 重放闸（ReplayGate）

- 对每条记录调 `replay.replay_record(record)` 取 `all_match`。
- **前提**：`replay_record` 期望 `team_a/b` 为 roster 形态（引擎能重建 unit）。人机记录是 TeamPick 形态 → 需先 `build_roster(picks, source, rules)` 转 roster 再重放；无法转换（如 `spirit=None`、技能不在池）→ 记 `replay_ok=False`（不猜）。
- `replay_ok=False` 的记录：**不得进入回答证据**，也不进入胜率分子；可进入“样本量/失败原因”统计（透明标注）。

#### 4.2.3 版本闸（VersionGate）

- `rules_digest` 必须 == 当前 `rules_digest()`；否则排除（规则版本不同不可比）。
- `data_digest`：**新记录**（4.4 加 stamp 后）要求 == 当前 `data_digest()`；**旧记录**（无字段）标 `"unknown"`——默认**排除出硬证据**，只在“历史样本量”里可见，且由 `replay_ok` 兜底（数据变了重放必失配）。

### 4.3 聚合（Aggregate）

```python
def aggregate(evidences: list[TrajectoryEvidence]) -> dict:
    # 按 kind 分桶，每桶输出：
    # { total_games, by_team: {team_key: {games, wins, win_rate, ci95_low, ci95_high, opponents: {...}}},
    #   replay_ok_rate, digest_unknown_count }
```

- 胜率用 **Wilson 区间**（95%，不除以 0：`games==0` 时 CI 记 `None`）。
- `opponents`：该构筑面对过的对手 `opponent_key → 出现次数`（不合并人机/自博弈）。
- 任何“提升/胜率”数字必须带 `games`（样本量）、`ci95`、`replay_ok_rate`、`rules_digest`——这是项目自对弈管线的汇报纪律（“任何提升 X% 必须附样本量/种子范围/95%CI/对手构成/data_digest”）在顾问侧的同构落地。

### 4.4 落盘补 stamp（让未来记录可被版本闸精确过滤）

- `selfplay.py` 的 `record` dict 增加 `"data_digest": data_digest(), "rules_digest": rules_digest()`（调用 `advisor.fingerprint`——`battle` 允许 import `advisor`，隔离只禁反向；**依赖 M1 先行交付**）。
- 人机记录同样 stamp 两个字段；改动点在人机记录 dict 的组装处 `src/ui/battle.py` 的 `BattleController.record()`（`routes_battle.py` 的 `_save` 只负责写盘）。
- **不改旧记录**：存量 `battles/`、`runs/` 保持 `digest_unknown`，靠重放闸兜底；不迁移、不回填。

### 4.5 查询工具（M3 消费的接口）

```python
def query_trajectory_evidence(kind: "human"|"selfplay", *,
                              data_digest: str | None = None,     # 缺省用当前
                              team_filter: list[str] | None = None,  # 只统计这些 spirit 名参与的构筑
                              min_games: int = 3) -> dict:
    # 返回该 kind 下、版本匹配且 replay_ok 的聚合结果 + evidence_ids 列表
```

- `data_digest` 由调用方（M3 的顾问循环）传当前值；工具内部做 VersionGate。
- 结果里每条构筑结论都带 `evidence_ids`（该构筑对应的记录 id 集合），供 M3 的 EvidenceGate 反查溯源。

## 5. 边界与护栏

- **人机/自博弈永不合并**：`kind` 是聚合的硬分桶键，接口层面就不提供“合并胜率”。
- **replay_ok=false 与 digest 不匹配的轨迹不得成证据**：在 `query_trajectory_evidence` 内部过滤，而非靠提示词。
- **隐私**：人机记录只取 `team/winner/turns` 的统计量，不读不存用户名/自由文本/会话 ID（`opponent` 已是机器名；`turns[].llm_reply` 属于自博弈玩家回复，**不作为证据、不进入聚合、不进上下文**，仅保留原始文件）。
- **不写原始思维链**：只回聚合统计与 evidence_id，不回整段 `turns`。

## 6. 测试计划

- 归一化：用真实 `battles/`、`runs/` 样例断言 `team_key`/`opponent_key`/`replay_ok` 解析正确；用构造的 `spirit=None` fixture 断言 → `team_key` 为 `unknown`、不进胜率。
- 版本闸：`rules_digest` 不符 → 排除；`data_digest="unknown"` → 排除出硬证据但可见于 `digest_unknown_count`。
- 聚合：构造 3 胜 1 负的小样本，断言 `win_rate=0.75`、Wilson CI 边界、`opponents` 计数。
- 分开统计：同一 `team_key` 在人机桶与自博弈桶各自独立，接口无法返回合并值。
- stamp：`run_selfplay(out_dir=tmp)` 落盘后记录含 `data_digest/rules_digest` 且与当前一致。

## 7. 验收 Gate

- [ ] 两类记录 schema 以测试钉住（含旧样例的 `unknown` 分支）。
- [ ] `query_trajectory_evidence` 只回版本匹配且 replay_ok 的证据；人机/自博弈分开。
- [ ] 每个胜率结论带样本量 + CI + replay_ok_rate + evidence_ids。
- [ ] `selfplay.py`/`routes_battle.py` 新记录含 digest stamp，旧记录不回填。
- [ ] 隔离不变量（environment 不 import rock_pvp_agent）、全量 pytest 绿。
- [ ] Gate 报告 → 用户「通过」→ commit。

## 8. 风险与回滚

- **风险**：`replay_record` 对 TeamPick 形态的人机记录不兼容 → 用 `build_roster` 预转换；转换失败即 `replay_ok=False`，不硬猜。
- **风险**：旧记录无 digest → 一律排除硬证据，宁可样本少也不污染结论。
- **风险**：`llm_reply`/`events` 体积大 → 只取统计量，不回全文。
- **回滚**：新增 `trajectory.py` 纯新增；`selfplay.py`/`routes_battle.py` 只加两个 stamp 字段，`git revert` 可回退。
