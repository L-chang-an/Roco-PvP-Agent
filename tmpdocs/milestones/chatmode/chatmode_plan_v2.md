# Chat Mode 扩展计划（修订版 v2）

> 本文是对 `chatmode_plan.md`（下称“原 plan”）的可行性审视与重写。所有「已核实」结论均以当前工作分支
> `feat/e-line-v2` 的实际代码为准，并已与 `main` 分支（R 线）逐项比对。写于 2026-08-30。

---

## 0. 结论先行（TL;DR）

原 plan 的**方向正确**（受约束的 `TeamAdvisorAgent` + 确定性校验器 + 证据溯源 + ScopeGate 拒答 + 白名单网页），
但它的**代码基线错了**：它把 R 线（`main` 分支上的 `battle/evolution/`、`environment/evaluate.py`、`data_digest`、
`BuildOracle`/`search_l3`/`Playbook`）当成“现有可复用底座”，而这些在当前分支 `feat/e-line-v2` 上**一个都不存在**。

修订版据此做出三点关键修正：

1. **先在本分支能落地的只读底座上起步**（catalog + validate + 轨迹证据 + 结构化终结 + ScopeGate），
   这些只需要 `environment` 层（已存在且稳定）。
2. **`BuildOracle`/`search_l3`/`Playbook` 一律降级为“设计参考 / 待移植”，不作为本分支既有能力**。
3. **网页搜索放到最后且默认关闭**；本项目当前无任何 HTTP 客户端依赖，这是净新增 + SSRF 硬化，不是“加个工具”。

---

## 1. 原 plan 的可行性审视

### 1.1 最重要的事实：分支错位

| 原 plan 声称 | 实际位置 | 当前分支存在？ |
|---|---|---|
| `playbook.py:60`（`class Playbook`、M5 `build_proposer`） | `main`：`src/rock_pvp_agent/battle/evolution/playbook.py` | ❌ |
| `build.py:180`（`class BuildOracle`）、`search_l3` | `main`：`.../evolution/build.py` | ❌ |
| `memory.py:80`（`data_digest` 隔离局面记忆） | `main`：`.../evolution/memory.py` | ❌ |
| `data_digest` / `rules_digest` 概念 | `main`：`evolution/reflect.py`、`memory.py` | ❌ |
| `hits_to_ko` / `situation_key` / `v_heuristic` | `main`：`environment/evaluate.py` | ❌（本分支无 `evaluate.py`） |

已核实：当前分支 `feat/e-line-v2` 的 `src/rock_pvp_agent/battle/` 只有
`__init__.py / player.py / prompts.py / selfplay.py / store.py` 五个文件；`evolution/` 是空目录、无任何代码文件。
`main` 分支上有完整 R0–R8（`evolution/` 下 `playbook/build/memory/analysis/credit/pool/smc/...` 共 20 个文件）。

这两个分支从 `19f5b0e` 分叉后各自演进：
- `main` = R 线（自对弈进化管线），建立在**旧的（E 线之前的）数据模型**上；
- `feat/e-line-v2` = E 线（进化链 / 萌化 / 首领化 / 冻结力竭），改了数据（`full_spirits.json` 985KB、
  新增 `evolution_chains.json`、`is_boss`/`family_key` 等字段），但**没有** R 线的 advisor 相关代码。

**结论**：原 plan 的“复用现有 Playbook / Build Oracle / L3 数值搜索 / data_digest 记忆”一节在本分支上不可行。
即使未来合并 `main`，R 线代码也是对着旧数据写的，仍需适配 E 线的家族/进化链/首领字段——不能“直接复用”。

### 1.2 三档判定

#### ✅ 可行（本分支即可落地，且方向正确）

| 原 plan 主张 | 判定 | 依据（当前分支已核实） |
|---|---|---|
| chat mode 现状 = 通用助手，只有 `calculator` + `final_answer` | ✅ 准确 | [prompts.py:3](src/rock_pvp_agent/prompts.py#L3)、[tools.py:52](src/rock_pvp_agent/tools.py#L52) |
| 用 `validate_team` / `build_roster` 当不可绕过的合法性硬闸 | ✅ 可行 | [teambuilder.py:59](src/environment/teambuilder.py#L59)、[:169](src/environment/teambuilder.py#L169) |
| 自博弈轨迹含规则/阵容/逐回合提交/state_hash 且可重放校验 | ✅ 可行 | [selfplay.py:95](src/rock_pvp_agent/battle/selfplay.py#L95)、[replay.py:27](src/environment/replay.py#L27) |
| “原始轨迹/网页/用户 Skill 是不可信数据，不得成为系统指令” | ✅ 正确 | 这是护栏核心，必须保留 |
| 人机轨迹与自博弈轨迹分开统计（不混一个胜率） | ✅ 正确 | 两类轨迹格式本就不同（见 §2） |
| 取消 `final_answer` 自由文本、改结构化终结 | ✅ 可行 | 需改 [agent.py:190](src/rock_pvp_agent/agent.py#L190) 的终结分支 |
| 取消“展示中间推理”、改为“展示查了哪些证据/过了哪些校验” | ✅ 可行 | [agent.py:164](src/rock_pvp_agent/agent.py#L164) 捕获 `reasoning_content`（167–169 发射） |

#### ⚠️ 需改进（方向对，但原 plan 的落点或假设不成立）

| 原 plan 主张 | 问题 | 改进方向 |
|---|---|---|
| “FULL/VALID 593 精灵 / VALID 179 技能” | 数据又变了：VALID 技能现在 **217** 条（`valid_skills.json` 已扩到 P1∪P2 之外），精灵注释里的 “179” 同样过时 | 所有数量一律运行时从 loader 读，顾问答案只报运行时指纹（这正是原 plan 自己主张的，但正文又写死了 179） |
| `get_catalog_version` 返回 `data_digest`/`rules_digest` | 本分支**没有任何** digest 基础设施 | 先补一个极小的确定性指纹（§6.1），再谈版本闸 |
| `analyze_team` / `simulate_matchups` | 原 plan 没落到真实函数；且误以为要 `hits_to_ko` | 本分支已有 `types.type_effectiveness`/`stab_multiplier`、`prediction.predict_damage`、`match.run_match`，直接用这些（§6） |
| ScopeGate“主 Agent 运行前分类” | 若做成每次一个独立 LLM 分类调用，成本/延迟翻倍且引入新的提示注入面 | **确定性优先**：白名单关键词 + 命中真实精灵/技能名 → IN_SCOPE；命中黑名单/安全词 → 拒答；其余才允许（可选）走一个轻量分类 |
| “让现有 `BuildOracle/search_l3` 负责确定性构筑与数值搜索” | 本分支无此物 | 降级为“后续里程碑”，并写清依赖 `main` 合并或按 E 线数据移植 |
| 网页工具“仅 HTTPS GET + 域名白名单 + 反 SSRF” | 原则对，但项目**没有任何 HTTP 依赖**（`httpx/requests` 均无） | 作为独立里程碑 + 新依赖审批，默认空白名单 = 工具关闭 |
| “Skill 必须有版本/哈希/工具白名单/probationary” | 原则对，但原 plan 把它和 R 线 `Playbook`（`[PROTECTED]`、双分析师）混为一谈 | 顾问侧 Skill 是**独立的、只读的程序化技能注册表**，不依赖 R 线的 Playbook/editor 机制 |

#### ❌ 不可行（按原样做不了）

1. **“复用 `playbook.py:60`、`build.py:180`、`memory.py:80` 作为底座”** —— 这些文件在本分支不存在。做不了，除非先合并 `main` 或单独移植。
2. **“已按 `data_digest` 隔离的局面记忆可直接用于顾问检索”** —— 本分支无 `data_digest`、无局面记忆库（`battle/evolution/memory.py` 在 main）。
3. **“轨迹证据 `query_trajectory_evidence` 返回样本量/胜负/版本/对手/置信区间/evidence_id”** —— 本分支有原始轨迹（`battles/`、`runs/`），但**没有**任何“轨迹→构筑级统计”的聚合/分析层（`analysis.py` 在 main）。必须新写。
4. **“网页结论要带 URL + 采集时间 + 可信等级”** —— 无 HTTP 能力，且无“允许网站”配置。整块是净新增。
5. **“证据不足时用‘理论构筑/启发式建议/样本不足’，不得声称最强/稳定上分”** —— 这条是提示词纪律，做得到，但原 plan 把实现重心放在 R 线复用上，导致“纪律”和“底座”混在一个里程碑里，无法按原顺序落地。

---

## 2. 本分支的事实基线（已核实）

数据（经 `load_*` 实测，非注释）：

| 项 | 数量 | 来源 |
|---|---|---|
| FULL 精灵（loaded） | 593 | 594 原始 − 1 脏记录「学院呱呱」 |
| FULL 技能 | 553 | `full_skills.json` |
| VALID 技能 | **217** | `valid_skills.json`（注释里的 179 已过时） |
| 系别 | 18 | `types.TYPE_NAMES` |
| 道具 | 2（草魔法 / 首领进化） | `rules.ITEMS` |

两类轨迹的实际落点（格式不同，需归一化）：

| 类型 | 落盘 | 格式 | 是否有 replay 自检 |
|---|---|---|---|
| 人机对战（UI） | `battles/*.json` | `{version, battle_id, saved_at, seed, opponent, rules, team_a, team_b, winner, done, turns}`（[routes_battle.py:8](src/ui/routes_battle.py#L8)） | 否（未见 `replay_ok` 字段） |
| 自博弈 | `runs/*.json` + `index.jsonl` | `TrajectoryStore` 记录，含逐回合 `state_hash`，`run_selfplay` 返回 `replay_ok`（[selfplay.py:130](src/rock_pvp_agent/battle/selfplay.py#L130)） | 是 |

可复用的真实符号（当前分支已确认存在）：

- 数据：`dataset.load_spirits/load_skills/load_families/load_evolution_chains/load_skipped_spirits`，`DataSource{FULL,VALID}`
- 组队：`teambuilder.TeamPick`、`learnable_skills`、`validate_team`、`build_roster`
- 数值：`statline.calc_combat_stats`、`is_valid_nature`、`NATURE_BONUS`
- 克制/伤害：`types.type_effectiveness`、`types.stab_multiplier`、`prediction.predict_damage`、`damage.compute_damage`
- 对局：`match.run_match`/`drive_turn`、`session.BattleSession`、`replay.replay_record`、`visibility.filter_events_for`
- LLM：`llm.build_chat_llm`、`ReasoningChatOpenAI`、`config.Settings`（默认 `deepseek-chat`）

---

## 3. 修订后总体架构

```
用户
  │
  ▼
ScopeGate（确定性优先：白名单关键词 + 命中真实精灵/技能名 → IN_SCOPE
           黑名单/安全词 → REFUSE；其余 → AMBIGUOUS，可选轻量 LLM 二分类）
  │ IN_SCOPE
  ▼
TeamAdvisorAgent（复用 ChatAgent 的工具循环，换 system_prompt + 工具集 + 结构化终结）
  ├─ catalog 工具      ：版本指纹 / 精灵检索 / 精灵档案 / 技能档案 / 合法构筑项
  ├─ trajectory 工具   ：人机轨迹证据 / 自博弈轨迹证据（分开、带样本量与 CI）
  ├─ analysis 工具     ：属性攻防覆盖 / 配招缺口（纯 `environment` 计算）
  ├─ simulate 工具     ：validate_team 硬闸 + 多 seed 成对模拟
  └─ web 工具（可选）  ：白名单站点，默认关闭
  │
  ▼
结构化终结 submit_team_advice（pydantic schema）
  │
  ▼
EvidenceGate 校验 → 校验失败允许修复一次 → 仍失败则安全降级答案
```

轨迹离线生产链（与用户侧顾问解耦，且**不依赖 R 线**）：

```
原始轨迹（battles/ 人机 + runs/ 自博弈）
  → 归一化（统一 record schema）
  → replay 校验（replay_record；人机记录补做）
  → 构筑级统计（成对胜率 + 样本量 + 95%CI + 对手构成 + 规则版本）
  → 候选经验（带 evidence_id）
  → held-out 验证 → 晋级为顾问 Skill（probationary 起步）
```

---

## 4. 分支策略决策（必须先拍板）

推荐**按优先级分层**，而不是一次性解决分叉：

- **P0（本计划直接做）**：在 `feat/e-line-v2` 上建顾问的**只读底座**——只用 `environment` 层 + 现有轨迹文件，
  不碰 R 线代码。这是“能证明答案合法”的最小闭环。
- **P1（条件触发）**：`BuildOracle/search_l3/Playbook/局面记忆` 的重用，**等待** `main` 与 `feat/e-line-v2`
  汇合，或由负责人决定“按 E 线数据单独移植”。在此之前，`simulate_matchups` 用现有 `run_match` + 固定 seed
  直接做，不做“构筑搜索”。
- **P2（最后）**：网页白名单搜索（新依赖 + SSRF 硬化，默认关闭）。

> 记录到记忆：当前分支是 `feat/e-line-v2`（E 线），R 线（`battle/evolution/` 等）在 `main`，二者从
> `19f5b0e` 分叉，数据模型不一致，任何“复用 R 线”都需先合并或移植。

---

## 5. 分阶段实施计划

每个里程碑 = 实现 + 对抗性 review（正确性/一致性/隔离 3 视角）→ Gate 报告 → 用户「通过」→ commit。
沿用项目既定纪律：environment 绝不 import rock_pvp_agent；不跑真实 LLM 批量命令（无 key 时确定性降级）。

### M1 — 只读底座：数据指纹 + catalog 工具 + validate 硬闸

新建包 `src/rock_pvp_agent/advisor/`（与 `battle/` 平行，`environment` 不 import 它）。

- `fingerprint.py`：`data_digest()` = 对 5 个数据文件（`full_spirits.json/full_skills.json/valid_skills.json/families.json/evolution_chains.json`）按固定顺序字节取 sha256；
  `rules_digest()` = 对 `BattleRules` 规范字段的稳定序列化取 sha256。**极小、纯确定性、可测**。
- `catalog.py`：`get_catalog_version`、`search_spirits`、`get_spirit_profile`、`get_skill_profile`、
  `get_build_options`（直接封装 `dataset.*` + `teambuilder.learnable_skills`）。其中 `search_spirits`
  实现为**白名单查询 DSL**（见 §6.1）——只允许调用已审计的 `environment` 只读函数，不给 `exec`/`grep`。
- `validate.py`：`validate_team` 的薄封装，返回结构化错误码（精灵不存在/首领不可入队/血脉技能不匹配/
  家族冲突/技能未实装/性格未知/IV 越界），而非裸中文串。
- 测试：每个工具的确定性用例 + `data_digest` 在数据文件改动后变化、未改动时不变。

### M2 — 轨迹证据：归一化 + 聚合 + 分开统计

- `trajectory.py`：
  - `normalize_human(record)` / `normalize_selfplay(record)` → 统一 `TrajectoryEvidence`；
  - `aggregate(records)` → 每构筑的成对胜率、样本量、95%CI（Wilson）、对手构成、规则/数据版本；
  - `query_trajectory_evidence(filters)` 分别返回人机 / 自博弈两段（**绝不合并成一个数**），
    每条证据带 `evidence_id`；`replay_ok=false` 或 `data_digest` 不匹配的直接排除（VersionGate + ReplayGate）。
- 人机记录补做 replay 校验：若记录不含逐回合 `state_hash`，则只按 `rules/team_a/team_b/seed/turns` 可重放的
  子集纳入，否则降级为“仅统计不引为硬证据”。

### M3 — 结构化终结 + 属性/模拟分析 + 顾问 Agent

- `advice.py`：`submit_team_advice` 用 **pydantic schema**（规则假设 / 阵容 / 每只精灵配招与理由 /
  协同与薄弱对局 / 证据 / 不确定性与替换方案），不再是自由文本 `final_answer`。
- `analysis.py`：`analyze_team` = 用 `types.type_effectiveness`/`stab_multiplier` 算攻防覆盖，
  用 `statline` 算速度分层/能量曲线，输出“角色缺口/被克制系别”清单（纯计算，无 LLM）。
- `simulate.py`：`simulate_matchups` = `build_roster` → `BattleSession` → `run_match`，固定规则、成对换边、
  多个 seed，返回逐对胜率 + seed 清单。
- `agent.py`：`TeamAdvisorAgent` 复用 `ChatAgent` 循环，注入 advisor 工具集与 system_prompt；
  终结分支改为“解析 schema → EvidenceGate 校验 → 失败允许修复一次 → 仍失败返回安全降级”。
- 同步改 `agent.py` 展示策略：**不再发射 `reasoning_content` 原始思维链**，改为发射“已查询证据/已通过校验”。

### M4 — ScopeGate + 越界拒答模板 + 顾问 Skill 注册表

- `scope.py`：确定性优先（见 §9）；`REFUSE` 走固定模板，不靠主模型临场发挥。
- `skills.py`：顾问 Skill 注册表（只读、带 `version/hash/allowed_tools/evidence_ids`），
  触发条件匹配后最多取 3 个已激活且版本匹配的 Skill。**独立于 R 线 Playbook**，先手工维护，后由 M5 的
  held-out 验证晋级（probationary 起步）。

### M5 — 回答行为评测集 + 持续改进闭环

- 建立“回答行为评测集”（held-in / held-out）：越界误答、越界误拒、非法技能被推荐、旧版本轨迹污染、
  胜率样本太少、理由与工具证据不一致、网页提示注入——按失败签名聚类。
- 每次只改一个可审计表面（system prompt / 工具 schema / 检索规则 / Skill / 输出校验器），
  安全/隐私/合法性指标零回归。（对齐原 plan §九，但**不依赖 R 线的 `run_epochs`**，用简单 pytest 门即可起步。）

### M6（P1，条件触发）— 网页白名单搜索

- 新增依赖 `httpx`（需负责人批准）；域名**精确白名单**（非字符串包含）、仅 HTTPS GET、禁止重定向到私网、
  限响应大小/超时/调用次数、缓存快照、只回正文片段+URL+采集时间。默认白名单为空 = 工具禁用。

---

## 6. 工具清单与签名（落到真实符号）

```python
# advisor/catalog.py
get_catalog_version() -> dict                # {data_digest, rules_digest, spirit_count, skill_count, valid_skill_count, families_count}（键定义以 M1 §4.2.2 为准）
search_spirits(filters: list[list[SpiritFilter]]) -> list[dict]
    # 白名单查询 DSL（析取范式：外层 OR / 内层 AND）；只允许已审计的 dataset.*/teambuilder.learnable_skills（§6.1）
get_spirit_profile(name: str) -> dict        # dataset.RawSpirit 全字段 + 合法血脉/可学池
get_skill_profile(name: str) -> dict         # dataset.RawSkill 全字段（type/kind/power/energy_cost/desc）
get_build_options(name: str, bloodline: str="") -> dict   # 合法 nature / iv 范围 / 可学技能池（teambuilder.learnable_skills）

# advisor/validate.py
validate_team(picks: list[TeamPick], items: list[str], *, rules=DEFAULT_RULES, source=VALID) -> TeamValidation
    # TeamValidation = {ok: bool, errors: [{code, message, pick_index|None}]}（定义见 M1 §4.3）

# advisor/analysis.py
analyze_team(roster: list[dict]) -> dict     # 属性攻防覆盖（types.type_effectiveness/stab_multiplier）、速度分层、角色缺口

# advisor/trajectory.py
query_trajectory_evidence(kind: "human"|"selfplay", *, data_digest: str | None = None,
                          team_filter: list[str] | None = None, min_games: int = 3) -> dict
    # data_digest 缺省=当前；返回 M2 §4.3 的聚合结果 {total_games, by_team:{...}, replay_ok_rate, digest_unknown_count, evidence_ids}
    # 每条构筑结论带 evidence_id = f"{kind}:{data_digest[:8]}:{battle_id}"（定义见 M2 §4.2）

# advisor/simulate.py
simulate_matchups(roster: list[dict], opponents: list[list[dict]],
                  seeds: list[int]) -> dict   # 成对换边胜率 + seed 清单（match.run_match）

# advisor/web.py（M6 可选，默认关闭）
search_curated_web(query: str) -> list[dict]  # {url, snippet, fetched_at, trust}

# advisor/skills.py
retrieve_team_skill(query: str) -> list[dict] # 最多 3 个已激活、版本匹配的 Skill

# advisor/advice.py
submit_team_advice(payload: TeamAdviceSchema) -> TeamAdviceSchema  # 结构化终结 + 触发最终校验；字段定义见 M3 §4.1
```

关键差异（相对原 plan）：`analyze_team` 与 `simulate_matchups` **不依赖** `hits_to_ko`/`search_l3`（那在本分支不存在），
而是直接用 `types` + `prediction` + `match` 现有纯函数与引擎。

### 6.1 白名单查询 DSL（`search_spirits` 的实现形态）

`search_spirits` 不做成“传几个固定参数”，而是做成一个**可组合过滤 DSL**，同时**只允许调用已审计的
`environment` 只读函数**——这是 `tools.calculator` 的 `_safe_eval`（AST 白名单）思想从“算术”延伸到“数据查询”。

- **过滤原子** `SpiritFilter(field, op, value)`：
  - `field` ∈ {`name`, `type`, `trait`, `learnable_skill`, `family`, `is_boss`, `number`}
  - `op` ∈ {`eq`, `in`, `contains`}
- **组合**：`filters: list[list[SpiritFilter]]`，外层 OR、内层 AND（析取范式），
  等价于“满足 (a 且 b) 或 (c 且 d)”，覆盖“火系或电系的高速强攻精灵”这类自然查询。
- **白名单执行层**：每个 `field` 只能映射到一张**已审计函数表**：
  - `name`/`type`/`trait`/`is_boss`/`number` → `dataset.load_spirits(source)` 的 `RawSpirit` 字段（FULL/VALID 由工具锁定，默认 VALID）
  - `learnable_skill` → `teambuilder.learnable_skills(name, bloodline, source)`
  - `family` → `dataset.load_families(source)`
  表外的函数、`__import__`/`eval`/`exec`/`open`/网络调用一律拒绝——**没有任意代码执行、没有文件系统写、没有网络**。
- **为什么不是 `grep`/`python` 直接查 JSON**：原始 JSON 未归一化（`strong:null`→`power=0` 的 0-falsy 陷阱、
  脏记录跳过、家族/首领字段是派生而非原始字段、`learnable_skills` 是计算而非存储），直接查文件会得到
  引擎不认可的事实，并绕开 FULL/VALID 语义与 LegalityGate。DSL 让 Agent 有表达力，但查询对象永远是
  “引擎的归一化结果”。
- **可测试/可审计**：DSL 编译到白名单函数后，每个查询可写确定性单元测试、可缓存，实际命中的过滤条件
  记进 EvidenceGate 的审计日志。

---

## 7. System Prompt 草案（修订）

保留原 plan §四的主体，做三处修正：

1. **删除**“调用 `BuildOracle/search_l3`”相关步骤（本分支没有），改为“调用 `validate_team` 与 `simulate_matchups`”。
2. **明确**“证据不足时只能标注 理论构筑/启发式建议/样本不足”，并把它作为 `EvidenceGate` 的硬校验项而非仅提示词。
3. **去掉**“展示中间推理”——system prompt 明确“不输出思维链；只输出查了哪些证据、过了哪些校验”。

核心骨架：

```
你是 Rock PVP Team Advisor。职责范围 = 精灵图鉴/技能/组队/配招/克制/构筑比较/对局轨迹/战术分析。
事实优先级：① 当前 data_digest 对应的本地数据库与引擎计算 → ② replay 通过且版本匹配的轨迹
→ ③ 允许网站（带 URL+采集时间）→ ④ Skill 程序性建议。冲突时以 ① 为准并说明差异。
工作流程：判范围 → 提取队伍规模/命数/已有精灵/禁用/目标对手/偏好（缺省列明假设）→ 查数据 →
查轨迹（人机/自博弈分开）→ 提候选 → validate_team + analyze_team + simulate_matchups →
未过 validate_team 的绝不输出为推荐 → 每条理由关联证据 → 结构化终结。
```

---

## 8. 组队 Skill 设计（修订）

方向沿用原 plan §六（触发条件 + 流程 + 边界 + 验证 + 证据引用），但做两点修正：

1. **它是顾问侧独立的只读技能注册表**，不是 R 线 `Playbook`（那套 `[PROTECTED]`/双分析师/epoch 慢更新是“怎么打赢”的
   训练态，本分支没有，也不该把顾问回答的解释/拒答/用户约束塞进去）。
2. `allowed_tools` 里**去掉** `BuildOracle/search_l3`，改为 `validate_team / analyze_team / simulate_matchups`。

```yaml
name: rock-team-advisor
version: 1
trigger: [组队, 配招, 克制, 阵容替换, 针对某对手]
boundary: [只处理当前游戏规则, 只推荐 VALID 技能, 不读取进行中战局的隐藏信息]
allowed_tools: [get_catalog_version, search_spirits, get_spirit_profile, get_skill_profile,
                query_trajectory_evidence, analyze_team, simulate_matchups, validate_team, submit_team_advice]
verification:
  - data_digest 完全一致
  - 每支队伍通过 validate_team
  - 每项核心理由至少一个 evidence_id
  - 轨迹必须 replay_ok
  - 人机与 selfplay 证据分别报告
```

---

## 9. 安全护栏（六道闸 + 运行时触发）

沿用原 plan §七的六道闸，但把**两道原本依赖 R 线的闸落到本分支可实现的锚点上**：

1. **ScopeGate** —— 确定性优先：白名单关键词/命中真实精灵或技能名 → IN_SCOPE；黑名单/安全词 → REFUSE；
   其余 → AMBIGUOUS（可选轻量 LLM 二分类，默认关闭以省成本）。
2. **VersionGate** —— `data_digest` + `rules_digest` 完全匹配才可引轨迹（M1 的指纹是锚点）。
3. **ReplayGate** —— `replay_ok=false` 的轨迹不得进入记忆或回答证据（`replay.replay_record`）。
4. **LegalityGate** —— 推荐阵容必须过 `validate_team`；图鉴回答可用 FULL，实战推荐只用 VALID。
5. **EvidenceGate** —— 精灵名/技能名/数值/胜率/网页结论均需可追溯证据；缺证据只能标“理论构筑”。
6. **VisibilityGate** —— 进行中对局只读对应玩家迷雾视角（`visibility.filter_events_for`），
   不得借观战数据或历史库推断隐藏配置。

补充（本分支可落地）：
- 人机轨迹去用户名/自由文本/会话 ID，按用户隔离（`routes_battle.py` 的 `opponent` 字段已是机器名，无需再含真人标识）。
- 工具异常/连续非法阵容/网站冲突用**代码级运行时触发器**处理，不在提示词里写“请重试”。
- 不保存原始思维链，只保存工具调用、证据 ID、最终答案与用户反馈（M3 改展示策略后天然满足）。

---

## 10. 越界拒答模板

沿用原 plan §八（普通越界 / 模糊话题 / 绕过规则三模板 + 欢迎语），由 `scope.py` 用固定模板生成，不靠主模型临场发挥。

---

## 11. 验收与评测（回答行为评测集）

- 单元层：M1–M4 每个工具/闸门确定性用例；`data_digest` 稳定性；`validate_team` 结构化错误码；轨迹聚合的 CI 计算。
- 行为层（M5）：held-in / held-out 用例集，按失败签名聚类，安全/隐私/合法性指标零回归。
- 不恢复整场事件流 golden（对齐原 plan §九对 checkpoint E1 的判断），只重建“回答行为评测集”。
- 复现原 plan 的教训：**顾问答案永远报运行时指纹，不引用注释或文档数字**（原 plan 自己写死的 “179” 已过时）。

---

## 12. 明确不做的事（范围外）

- 不把顾问做成“能搜很多东西、但无法证明答案合法”的通用 ReAct Agent。
- 不在本分支上假装 `BuildOracle/search_l3/Playbook/局面记忆` 已存在；这些是 P1 条件项。
- 不给 Agent 任意文件 / 任意 URL / 执行代码的工具。
- 不在 M1 就上网页搜索（无依赖、无白名单、无 SSRF 硬化前一律关闭）。

---

## 13. 分篇详细计划索引

| 里程碑 | 详细计划 |
|---|---|
| M1 只读底座（指纹 + catalog DSL + validate） | [chatmode_M1_catalog_base.md](chatmode_M1_catalog_base.md) |
| M2 轨迹证据（归一化 + 聚合 + 分开统计） | [chatmode_M2_trajectory_evidence.md](chatmode_M2_trajectory_evidence.md) |
| M3 结构化终结 + 分析/模拟 + 顾问 Agent | [chatmode_M3_advisor_agent.md](chatmode_M3_advisor_agent.md) |
| M4 ScopeGate + 越界拒答 + Skill 注册表 | [chatmode_M4_scopegate_skills.md](chatmode_M4_scopegate_skills.md) |
| M5 回答行为评测集 + 持续改进闭环 | [chatmode_M5_answer_eval.md](chatmode_M5_answer_eval.md) |
| M6 网页白名单搜索（P1 条件触发） | [chatmode_M6_curated_web.md](chatmode_M6_curated_web.md) |
