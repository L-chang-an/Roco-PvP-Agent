# MySelfPlayAgent — Battle Environment 从零重建执行计划（人机协作版）

> **定位**：本文件是 battle environment 子系统的执行手册，与 `mydocs/rebuild-plan.md`（chat mode，M0–M4 已定稿）**并行两条线**。它回答四个问题：
> 1. 参考项目那 12,500 行的战斗引擎，最小可运行的内核到底是哪一小块
> 2. 如何从零把这个内核写出来、并亲手验证它真的在跑
> 3. 之后每一级复杂度按什么顺序加载、每一级的 Gate 长什么样
> 4. 哪些复杂度**明确不做**，以及为它们预留了哪些缝
>
> **原则**：负责人是决策者，助手提供方案与利弊；每个里程碑一个 Gate，负责人亲自运行验收命令、明确说"通过"后才进入下一步；每步可随时停下或回退。铁律见 `docs/collaboration-protocol.md` §5。
>
> **执行状态**：**E0a ☑ 通过**（2026-08-23，见 `docs/checkpoints/checkpoint-E0a.md`）；**E0b ☑ 通过**（2026-08-24，回合内核 + 交互式补位；2026-08-26 Gate）；**E1 ⏭ 跳过**（负责人 2026-08-24 决定，无黄金基线）；**E2 ☑ 通过**（属性克制 + STAB，2026-08-25；2026-08-26 Gate）；**P1/P2 技能池 ☑ 已实现**（2026-08-25，125+54=179 个技能全部编译成效果）；**E3 ☑ 通过**（2026-08-25 真实数据对战；2026-08-26 Gate）；**E4+E5 ☑ 通过**（2026-08-25/26 迷雾观测 + 人类 vs 假LLM 对战页 + 取消平局 + 轨迹持久化，见 `mydocs/e4-e5-battle.md`；2026-08-26 Gate）；**E6 ☑ 通过**（2026-08-26 自博弈编排 + 轨迹落盘 + 引擎重放，见 `mydocs/e6-selfplay.md`）；**E6.5 ☑ 通过**（2026-08-26 真实 LLM 玩家，见 `mydocs/e6-5-llm-player.md`）；**E7 ☑ 通过**（2026-08-26 观战页，见 `mydocs/e7-spectate.md`）。E 线全部里程碑 Gate 通过。

参考项目：`~/workspace/SelfPlayAgent/src/environment`（洛克王国式回合制精灵对战引擎，12,500 行 Python，含中文技能描述→DSL 规则编译器、约 50 个效果 handler、227 个精灵特性、事件总线、10 条修饰链、天气/蓄力/巧变）。其权威设计文档是 `~/workspace/SelfPlayAgent/docs/Environment_upgrade_plan.md`，最小版设计是同目录的 `battle_design.md` 与 `battle_api.md`。

**本计划不是它的复刻。** 那 12,500 行是沿 `M0a → M0b → M1 → M2 → M7a → M3 → M4 → M5 → M6 → M7b` 十个里程碑长出来的；照抄的结果是负责人失去对代码的掌控，而这恰好违背本项目的立项原则。本计划做的是**反向蒸馏**：先切出「不可再减的回合制内核」，让它在 E0 就能自己打完一局；此后每个里程碑只加一层，每层都有一条负责人亲手敲的验收命令。

---

## 0. 范围与技术决策

| 决策 | 选型 | 理由 |
|---|---|---|
| 代码落点 | `src/environment/` **同级包** | `docs/extension-cookbook.md` §5 已拍板；铁律：`environment` 永不 import `rock_pvp_agent` |
| 第三方依赖 | **零**（只用 stdlib） | 引擎是叶子包。零依赖让「无循环 import」能用一条 grep 审计，也让 E1 的子进程确定性回放不受环境影响 |
| 里程碑命名 | **E0a / E0b / E1–E7**（E = Environment） | 与 chat mode 的 M0–M4 互不干扰，两条线可并行推进、单独回退 |
| 本计划终点 | **双 LLM 自博弈 + 轨迹落盘 + 观战**（E6 编排 + E6.5 真实 LLM + E7 观战页，全部交付） | — |
| E0 的技能库 | `mydocs/E0_skills.json` 的 **14 个技能**（原样拷入，不改一字） | 覆盖攻击 / 防御 / 状态三类，且自带应对三角；小到能逐条手写效果表 |
| E0 的精灵库 | **手写 6 只**，形状对齐真实 `spirits_details.json` | 数值取整便于手算断言；E3 换数据源时只换文件不改代码 |
| 效果表达方式 | **显式效果表**（Python dict，逐技能手写） | 引擎**永不读 `desc`**，也永不认识正则。这是不做 DSL 编译器的代价与边界 |
| 效果引擎 | **明确排除**（状态叠层 / 印记 / 227 特性 / desc→DSL 编译器） | 约 8000 行，是独立产品而非战斗内核；见 §7 |
| Python | `>=3.10` 兼容（不用 `StrEnum` / `tomllib`） | 与既有 `pyproject.toml` 一致 |
| 数值类型 | HP 与六维一律 `int` | 浮点只在伤害公式内部当中间量，在漏斗出口取整一次——参考项目全程 float，代价是 golden 里 `100.0` 与 `100` 的漂移只能靠逐字节比较兜住 |
| 测试 | 现有约定不变：平铺 `tests/`、无 `__init__.py`、鸭子类型注入、零网络 | 覆盖率门禁 ≥90%，命令扩为 `--cov=rock_pvp_agent --cov=environment` |

**唯一需要改的既有文件**：`pyproject.toml` 的 `[tool.hatch.build.targets.wheel] packages` 追加 `"src/environment"`（E0a 做）。`src/rock_pvp_agent/` 在 E0a–E4 期间**一行不动**——它的 94% 覆盖率是回归哨兵。

---

## 1. 里程碑总览

| 里程碑 | 一句话目标 | 产出 | 核心验收命令 | 状态 |
|---|---|---|---|---|
| **E0a 组队与数据层** | 双方能自由组队并打印出合法阵容与最终六维（零引擎依赖） | 数据 + 效果表 + 属性公式 + 组队校验，9 个文件 | `uv run python -m environment --team-report` | ✅ |
| **E0b 回合内核** | 随机策略自己打完一局，同 seed 逐字节复现 | 回合循环 + 三类技能 + 应对三角 + 道具，11 个文件 | `uv run python -m environment battle --seed 20260823` | ☐ |
| **E1 确定性闸门与黄金基线** | 把已被认可的 E0b 行为冻死 | `scripts/record_env_baseline.py` + `tests/golden/` | `record_env_baseline.py --check` | ☐ |
| **E2 属性克制与系别** | 系别 / 克制表 / STAB 生效，血脉从"仅记录"变为"影响系别" | `types.py` + `Skill.type` 接线 | `record_env_baseline.py --diff` → 审阅 → `--record` | ☐ |
| **E3 真实数据接入** | 换数据源到 553 技能 / 594 精灵，升到 6v6 / 4 命 | `data/` 换文件 + 效果覆盖率白名单 | `python -m environment --data-report` | ☐ |
| **E4+E5 迷雾+对战**（合并交付） | 迷雾观测 / 人类 vs 假LLM 交互对战页 / 取消平局 / 轨迹持久化+重放 | `view.py` + `visibility.py` + `battle/player.py`(FakeLLM) + `ui/battle.py` + `ui/routes_battle.py` | `uv run python -m ui` 真人打一局 + `pytest tests/test_battle_ui.py` | ✅ |
| **E6 自博弈+轨迹+重放** | 两个隔离玩家自我对战（E6 测试期假LLM），轨迹只存提交序列+hash、可重放复现 | `battle/selfplay.py` + `battle/store.py` + `environment/replay.py` | `python -m rock_pvp_agent selfplay --games 2 --seed 7 --out runs/` | ✅ |
| **E6.5 真实 LLM 玩家** | 真实 LLM 当一方出招（无 key/异常降级假LLM），接 E6 自博弈 `--a llm` | `battle/player.py`(LLMPlayer) + `battle/prompts.py` | `python -m rock_pvp_agent selfplay --a llm --b llm` | ✅ |
| **E7（可选）观战页** | 人类全局视角观战双 LLM（LLM 仍迷雾）；SSE 流 | `/api/battle/stream` + `spectate.js` | 浏览器看完一局 + `curl -N` SSE | ✅ |

每里程碑结构：目标 → 文件清单 → 关键签名 → 验收命令 → 用户 Gate。

**为什么是这个顺序**（负责人若想调换，先读这一段）：

- **E0a 必须早于 E0b。** 引擎要有队伍才能打；而组队本身可以独立验收（打印一份合法阵容与最终六维，不需要任何回合逻辑）。先把「数据 → 效果表 → 六维 → 阵容」这条链打通，E0b 就只剩纯粹的状态推进。
- **黄金闸门（E1）必须紧跟 E0b，不能等系统长大。** 参考项目是在 12,500 行成型之后才录基线，于是把「所有防御技能固定减伤 30%」这个缺陷一起冻进了 golden，后来只能靠专门的门控绕开。在行为还能手算核对时录基线，之后每个里程碑白拿一张回归网，且没有任何缺陷被追认。
- **E1 不能与 E0b 合并。** E0b 的 Gate 是负责人对行为的判断（"这个回合流对不对"），E1 的 Gate 是机械比对。把还没被人认可的行为冻起来等于给错误发通行证——先验收，再冻结。
- **E2 必须在 E1 之后**：它是第一个会改变既有伤害数字的里程碑，需要一张能审阅的 diff。E0 的 14 个技能全是「普通」系，所以克制表在 E0 是纯 no-op，放到 E2 才有意义。
- **E3 在 E4 / E6.5 之前**：LLM 需要真名字与真效果才能做出可评估的决策；迷雾的遮蔽粒度也只有在有真实技能表时才定得下来。
- **E4 迷雾必须在 LLM 玩家（E6.5）之前。** LLM 的提示词是从观测渲染出来的。先对着全局视图调好提示词再加遮蔽，等于提示词工程做两遍，而且中间那一版会泄露信息、产出的对局数据全部作废。（E6 已把迷雾收口在 `run_match` 给 `view()`，E6.5 的 LLMPlayer 一接入即自动只见白名单。）
- **`Player` 协议在 E0b 就位、LLM 玩家只是再挂一个实现。** 这是引擎可测的前提——零 LLM 就能驱动整局。

---

## 2. 领域词汇表（一次钉死，全程不改）

命名漂移是这类项目最贵的隐性成本——参考项目里同一个概念同时叫 `team` / `side`，同一件事同时叫 `mp` / `lives`，事件里两个同义字段都写。先定表，再写码。

| 概念 | 代码里的名字 | 说明 |
|---|---|---|
| 阵营 | `side: str`，取值 `"a"` / `"b"` | 全局常量 `SIDES = ("a", "b")`，**一切遍历都走它**，绝不遍历 `set`（`set` 迭代序带 `PYTHONHASHSEED` 盐，会毁掉跨进程复现） |
| 单位（精灵） | `Unit` | 场上一只精灵。E0 每方 3 只 |
| 在场单位 | `SideState.active` 下标 + `active_unit` | 下标是唯一真相，不另存对象引用 |
| 技能定义 | `Skill`（frozen） | 静态定义，不可变、可共享；`desc` 只用于展示 |
| 技能类别 | `SkillCategory` ∈ 攻击 / 防御 / 状态 | 由 `kind`（物攻 / 魔攻 / 防御 / 状态）派生；**应对判定只看类别** |
| 主动作 | `action: dict` | 闭集三类：`skill` / `switch` / `recharge` |
| 一回合的提交 | `Decision(action, item)` | 一个主动作 + 可选一个道具（附赠动作） |
| 增益层 | `StatModifier(stat, mode, layers, permanent)` | **1 层 = 10%（pct）或 +10（flat）**；上限 99 层／属性；换人清除非永久层 |
| 应对 | `TurnContext.counters(side)` | 本方技能类别正好克制**对手本回合声明的**类别 |
| 事件 | `event: dict` | 必带 `type` 与 `side`；由 `ev()` 唯一构造器产出 |
| 命数 | `SideState.lives` | 参考项目叫 `mp_a` / `mp_b`，本项目**只叫 lives** |
| 能量 | `Unit.energy` | 释放技能的资源，`recharge` 动作补充 |
| 回合 | `BattleState.turn` | 从 1 开始；每个 `execute_turn` 恰好推进 1 |
| 一局 | `MatchResult` | 一整局的提交序列 + 逐回合 `state_hash` + `digest()` |

**术语铁律两条**：① 同一个量只有一个名字、一个事实源；② 同一个上限只写在 `BattleRules` 一处（参考项目的减伤上限一处读配置、一处硬编码 `min(0.9, …)`，两个事实源）。

---

## 3. 战斗规则速查（E0 口径）

这一节是负责人写代码时的对照表，也是 `BattleRules` 每个字段的语义来源。

| 项 | E0 取值 |
|---|---|
| 队伍 | 每方 **3 只**精灵，每只带 **1–3 个**技能（至少 1、至多 3） |
| 命数 | 每方 **2 条**（与队伍规模解耦：命归零即输，第 3 只只能用来换人） |
| 能量 | 上限 10、开局 10；聚能 +5；技能能耗 1–4；**付不起 = 非法动作** |
| 每回合提交 | 一个 `Decision` = 一个主动作（技能 / 换人 / 聚能）+ 可选一个道具 |
| 技能三类 | **攻击**（物攻走 `atk/def`，魔攻走 `sp_atk/sp_def`）／**防御**（本回合减伤标记）／**状态**（属性增益层） |
| 应对三角 | 攻击 应对 状态 → 伤害 **×1.5**；防御 应对 攻击 → **减伤 70/80/85%**；状态 应对 防御 → 增益 **额外 +1~2 层** |
| 应对的判定依据 | **对手本回合声明的技能类别**。因为双方同时提交，两个声明在任何结算之前都已知——这就是「同时提交」必须存在的原因 |
| 应对失败的代价 | 能量照扣、效果落空（防御方遇到状态技能 = 白防一回合）。这是三角的赌注 |
| 增益层 | 1 层 = 10%（pct）或 +10（flat）；`增加90%` = 9 层 pct；`速度增加80` = 8 层 flat；上限 **99 层／属性** |
| 增益生命周期 | 精灵在场期间持续、可叠加；**离场时清除全部非「永久」层**（E0 的 5 个状态技能都不是永久） |
| 道具 | **草魔法**：回复当前在场精灵 50% 最大 HP（回满为止），**整局一次**；优先级 **99** |
| 出手顺序 | 优先级降序 → 在场精灵速度降序 → 种子硬币（50%）。道具与换人同为 99，技能 = `skill.priority`（E0 全 0），聚能 = 0 |
| 伤害公式 | `int((atk/def) × power × 0.9 × 应对乘子 × (1 − 减伤))`，最低 1；`power ≤ 0` → 0。**只取整一次** |
| 阵亡 | HP 归零 → 该方 −1 命 → 首个存活后备自动补位 |
| 终局 | 某方命归零 **或** 无存活精灵 → 对方胜；超过 50 回合 → 平局 |

三条从 `mydocs/E0_skills.json` 里读出来、必须落进代码的事实：

- **抓挠（基础款）没有应对子句，撞击（基础款）有。** 所以效果表必须**逐技能**手写，不能按 `kind` 推。
- **加速度是扁平 +80，其余四个状态技能是百分比。** 所以 `StatModifier.mode` 必须从第一天就区分 `pct` / `flat`，不能统一成一个数。
- **撞击2 能耗 3，而抓挠2 能耗 4。** 数据如此，不要"顺手修正"——数据是输入，不是待优化对象。

---

## Milestone E0a — 组队与数据层

**目标**：双方能自由组队——选精灵、为每只精灵选 1–3 个技能、选血脉、设个体值、设性格——并把这份阵容校验通过、算出最终六维、打印出来。**全程不需要任何回合逻辑**，所以它能独立验收。

**为什么把组队单独立一个里程碑**：组队是"进入战斗前"的完整一环，它的输入是玩家意图、输出是引擎唯一认识的 roster spec。把这条链先打通，E0b 就只剩纯粹的状态推进，两边的 bug 不会互相掩盖。而且 `calc_combat_stats`（属性公式）从占位公式起步，**在 E0a 换成负责人给定的真实公式时只换了函数体**——调用方 `build_roster` 与测试的形状一行未动，隔离的价值在第一次真实替换时就兑现了。

**文件清单**（约 700 行，9 个源码文件 + 2 个测试文件）：

| 路径 | 用途 |
|---|---|
| `src/environment/__init__.py` | 包标记（E0b 再补对外 re-export） |
| `src/environment/rules.py` | `BattleRules`（frozen dataclass）+ `DEFAULT_RULES`：**唯一的数值常量表** |
| `src/environment/data/e0_skills.json` | 从 `mydocs/E0_skills.json` 原样拷入（14 条，一字不改） |
| `src/environment/data/e0_spirits.json` | 手写 6 只精灵，形状对齐真实 `spirits_details.json`（中文六维 / `type[]` / `trait` / `skills{默认,血脉}`） |
| `src/environment/dataset.py` | 加载 + 归一：`strong`/`energy` 字符串 → int、中文六维 → 英文 key、按名建索引、`lru_cache` |
| `src/environment/skillbook.py` | 14 个技能的**显式效果表**（`SkillEffect`）。引擎永不读 `desc` |
| `src/environment/statline.py` | `NATURE_BONUS` 性格表（30 种非中性 + 中性「坦率」兜底）+ `calc_combat_stats(base, iv, nature)`（**真实公式**，见 E0a.2） |
| `src/environment/teambuilder.py` | `TeamPick` / `build_roster()` / `validate_team()`：把玩家意图变成 roster spec |
| `src/environment/__main__.py` | `--team-report`：打印双方阵容与最终六维；`--data-report`：数据自检 |
| `tests/test_environment_dataset.py` | 归一 / 效果表齐全 / 性格表完备性（30 种一对不重不漏）/ 属性公式手算断言 |
| `tests/test_environment_team.py` | 组队校验的每一条拒绝理由 |

### E0a.1 数据与效果表

**做什么**：把两个 JSON 读成引擎认识的结构，并把 14 个技能的效果**逐条手写**成结构化数据。

```python
# dataset.py
def _to_int(raw: str | int | None, default: int = 0) -> int:
    """字符串数字 → int。**绝不写 `raw or default`**——`"0"` 与 `0` 都是合法值，
    而 `0` 是 falsy。只写 `default if raw is None or raw == "" else int(raw)`。
    参考项目正是在这里栽了：防御技能 `strong: null` → `power=0.0` → 走进
    `float(... or 30)` → 52 个防御技能的 50%~100% 减伤全部退化成固定 30%。"""

STAT_KEY_MAP = {"生命": "hp", "物攻": "atk", "魔攻": "sp_atk",
                "物防": "def", "魔防": "sp_def", "速度": "speed"}

def load_skills() -> dict[str, RawSkill]: ...    # lru_cache，按 name 索引
def load_spirits() -> dict[str, RawSpirit]: ...  # lru_cache，按 name 索引，六维已归一为 int
```

```python
# skillbook.py
class SkillCategory(str, Enum):
    ATTACK = "攻击"; DEFENSE = "防御"; STATUS = "状态"

KIND_TO_CATEGORY = {"物攻": ATTACK, "魔攻": ATTACK, "防御": DEFENSE, "状态": STATUS}

@dataclass(frozen=True)
class SkillEffect:
    """一个技能的全部结构化效果。**引擎永不读 desc**——desc 只用于展示与将来的提示词。
    这是"不做 DSL 编译器"的代价与边界：14 条手写，553 条时才需要编译器。"""
    category: SkillCategory
    self_energy_gain: int = 0            # 抓挠系：自己回复 1 能量
    reduction_pct: float = 0.0           # 防御系：减伤比例
    stat: str = ""                       # 状态系：作用于哪个属性
    mode: str = ""                       # "pct"（每层 10%）| "flat"（每层 +10）
    layers: int = 0                      # 基础层数
    counter_vs: SkillCategory | None = None   # 应对哪一类
    counter_damage_mult: float = 1.0     # 攻击系应对成功时的伤害乘子
    counter_extra_layers: int = 0        # 状态系应对成功时的额外层数

E0_EFFECTS: dict[str, SkillEffect] = { ... }   # 14 条，见下表
```

`E0_EFFECTS` 的全部内容（`power` / `energy_cost` 来自 JSON 的 `strong` / `energy`，不在这张表里重复）：

| 技能 | 类别 | 威力/能耗 | 主效果 | 应对 | 应对加成 |
|---|---|---|---|---|---|
| 抓挠 | 攻击 | 60 / 2 | 物伤 + 自身能量 +1 | — | — |
| 抓挠1 | 攻击 | 80 / 3 | 物伤 + 自身能量 +1 | 状态 | 伤害 ×1.5 |
| 抓挠2 | 攻击 | 95 / 4 | 物伤 + 自身能量 +1 | 状态 | 伤害 ×1.5 |
| 撞击 | 攻击 | 60 / 2 | 魔伤 | 状态 | 伤害 ×1.5 |
| 撞击1 | 攻击 | 80 / 3 | 魔伤 | 状态 | 伤害 ×1.5 |
| 撞击2 | 攻击 | 95 / 3 | 魔伤 | 状态 | 伤害 ×1.5 |
| 防御 | 防御 | 0 / 1 | — | 攻击 | 减伤 70% |
| 防御1 | 防御 | 0 / 2 | — | 攻击 | 减伤 80% |
| 防御2 | 防御 | 0 / 3 | — | 攻击 | 减伤 85% |
| 加物攻 | 状态 | 0 / 1 | `atk` pct **9 层** | 防御 | +2 层 |
| 加魔攻 | 状态 | 0 / 1 | `sp_atk` pct **9 层** | 防御 | +2 层 |
| 加魔防 | 状态 | 0 / 1 | `sp_def` pct **8 层** | 防御 | +1 层 |
| 加物防 | 状态 | 0 / 1 | `def` pct **8 层** | 防御 | +1 层 |
| 加速度 | 状态 | 0 / 1 | `speed` **flat** **8 层** | 防御 | +2 层 |

注意防御系的减伤**本身就是应对效果**——`应对攻击时，减伤70%` 意味着对手没出攻击就什么也不发生。所以它的 `reduction_pct` 只在 `counter_vs` 命中时生效，表里两列都填。

**自检**：`load_skills()` 出 14 条且 `防御.power == 0`（不是 30）；`E0_EFFECTS` 的键集合与 `load_skills()` 的键集合完全相等（一条测试断言双向包含，防止加数据忘了加效果）。

### E0a.2 属性公式（个体值 / 性格）

**做什么**：把种族值 + 个体值 + 性格算成最终六维。**真实公式由负责人给定**，替代原占位公式——替换时只换了函数体，调用方 `build_roster` 与测试的形状一行未动。

```python
# statline.py —— 真实公式（负责人给定，替代原占位公式）
NATURE_BONUS: dict[str, tuple[str, str]] = {
    # 30 种非中性性格：提升六维之一 × 降低另外五维之一（6 × 5），命名格式固定
    # 「加『某维』减『另一维』」，如 "加攻击减速度": ("atk", "speed")。
    # 中性性格「坦率」不进本表——由 NEUTRAL_NATURE + 未知回退（get 落空 → ("", "")）承担。
}

NEUTRAL_NATURE = "坦率"

def is_valid_nature(name: str) -> bool:
    """合法性格 = 中性「坦率」或 30 种非中性之一。"""

def calc_combat_stats(base: dict[str, int], iv: dict[str, int] | None = None,
                      nature: str = NEUTRAL_NATURE) -> dict[str, int]:
    """真实属性公式，逐项 int() 向下取整：

        生命：    (1.7 × (种族值 + 个体值×3) + 70) × 性格修正 + 100
        其他五维：(1.1 × (种族值 + 个体值×3) + 50) × 性格修正 + 50

    个体值每点折合 +3（取值域 0–10，即 iv_max）；性格修正：命中提升项 ×1.20、命中降低项 ×0.90、
    其余 ×1.0（`+100` / `+50` 在性格修正之后加）；未知性格回退中性而不抛。
    """
```

**自检**：中性口径能对着公式手算——如迪莫 `hp = 1.7×120+70+100 = 374`、`atk = 1.1×80+50+50 = 188`；`iv={"atk":10}` 让 `atk = 1.1×(80+30)+50+50 = 221`；`nature="加攻击减速度"` 让 `atk = (1.1×80+50)×1.2+50 = 215`、`speed = (1.1×92+50)×0.9+50 = 186`；未知性格回退中性而不抛。**注意中性值已不再是种族值**（占位公式的「中性恒等」性质随真实公式取消），golden 手算口径改为「对着本公式算」，而非「等于种族值」。

### E0a.3 组队与校验

**做什么**：定义"玩家意图"的形状，把它校验通过后变成引擎唯一认识的 roster spec。

```python
# teambuilder.py
@dataclass(frozen=True)
class TeamPick:
    """一只精灵的组队意图。这是**玩家侧**的输入形状。"""
    spirit: str                       # 精灵名，必须在 e0_spirits.json 里
    skills: list[str]                 # 1–rules.skill_slots 个（至少 1、至多 3），都在该精灵可学池内
    bloodline: str = ""               # 血脉（系别）；见下方"血脉在 E0 的语义"
    nature: str = "坦率"
    iv: dict[str, int] = field(default_factory=dict)   # 每项 0–iv_max，最多 3 个维度有投入，缺省 0

def learnable_skills(spirit: str, bloodline: str = "") -> list[str]:
    """该精灵的可学技能池 = `skills.默认` ∪（选了血脉则并上 `skills.血脉`）。"""

def validate_team(picks: list[TeamPick], items: list[str],
                  rules: BattleRules = DEFAULT_RULES) -> list[str]:
    """返回全部错误（中文），空列表 = 合法。**一次报全部错误，不是遇到第一个就返回**
    ——组队是人在填表，一次看清所有问题比来回试八次强。

    校验项：队伍规模 == rules.team_size；每只技能数 1–rules.skill_slots（至少 1、至多 3）；
    技能不在可学池；技能名不存在；精灵名不存在；血脉不在该精灵的血脉列表；
    性格未知；个体值键不是六维之一 / 值越界（0–iv_max）/ 有投入的维度 > 3；
    道具名不存在；同名精灵重复（可选，见判断 3）。
    """

def build_roster(picks: list[TeamPick]) -> list[dict]:
    """校验通过后产出 roster spec —— 引擎唯一认识的形状（E0b 的 build_unit 吃它）。
    E3 的真实数据加载器产出**同一个形状**，所以 E0b 的引擎测试到 E3 一条都不用改。"""
```

**roster spec 的形状**（数据层与引擎之间唯一的缝）：

```python
{"name": "迪莫",
 "types": ["光"],                      # 精灵自身系别，血脉不改写（克制/STAB 的依据）
 "stats": {"hp": 374, "atk": 188, "sp_atk": 188,
           "def": 215, "sp_def": 215, "speed": 201},   # calc_combat_stats 的输出（真实公式中性值）
 "skills": ["抓挠1", "加物攻"],          # 技能名，E0b 按名从 skillbook 取定义与效果
 "nature": "坦率", "bloodline": "", "iv": {}}         # 存档字段，进 to_dict
```

**血脉在 E0 的语义（说清楚，不假装实现）**：E0 的 14 个技能全是「普通」系，所以血脉在 E0 **对战斗没有任何影响**。它做三件真实的事：① 被校验（必须是该精灵的合法血脉）；② 拓宽可学技能池；③ 进 `to_dict()` 与 `--team-report` 输出。**血脉系别不改写 `Unit.types`**（负责人 2026-08-25 澄清）：`types` 恒为精灵自身系别，是克制/STAB 的依据；血脉只决定可携带的血脉技能是哪个系（规则 2）。把这条写在文档里，好过让负责人以为自己漏实现了什么。

**验收命令（用户亲自运行）**：

```bash
cd ~/workspace/MySelfPlayAgent
uv run python -m environment --data-report
#   E0 数据自检
#   技能 14 条（攻击 6 / 防御 3 / 状态 5）· 效果表 14 条 · 键集合一致 ✅
#   精灵 6 只 · 六维已归一 · 可学池最小 4 最大 9
#   防御.power = 0（不是 30）✅      ← 专门盯 `0.0 is falsy` 这个坑

uv run python -m environment --team-report
#   队伍 a
#     迪莫   光    坦率  iv={}          抓挠1 / 加物攻
#            六维 hp374 atk188 spa188 def215 spd215 spe201   ← 中性迪莫，真实公式手算值
#     ...（3 只）
#     道具  草魔法 ×1
#   队伍 b  ...（3 只）
#   校验：两队合法 ✅
# 人眼核对三件事：① 每只都是 1–3 个技能且都在它自己的可学池里
#                ② 六维能对着真实公式手算（如迪莫中性 hp=1.7×120+70+100=374、atk=1.1×80+50+50=188）
#                ③ 改一处非法（技能写错名 / iv 写 99）→ 报出该条错误且不崩

uv run pytest tests/test_environment_dataset.py tests/test_environment_team.py -q
```

**用户 Gate（E0a）**：亲自跑通三条命令，确认「组队能选精灵/技能/血脉/个体值/性格、非法阵容会被逐条拒绝、六维算得对」。明确说"通过"→ 打 commit `feat: e0a team building and data layer`。

**验收记录**：✅ 2026-08-23 通过。三次修改要求（30 种性格 / 真实属性公式 / 技能 1–3·个体值 ≤10·≤3 维度）全部落地；验收记录见 `docs/checkpoints/checkpoint-E0a.md`。

---

## Milestone E0b — 回合内核

**目标**：拿 E0a 组好的两支队伍，让两个随机策略互相对打、自动打完一整局到分出胜负，逐回合事件流人眼可读；同 seed 重跑逐字节复现，换 seed 轨迹不同。全程零第三方依赖、零 LLM、零网络、零文件写入。

**核心不变式（回合制的本质，也是负责人的第 1 个问题的正面回答）**：

> `resolve_turn` 的输出**只**由 `(state.to_dict(), decision_a, decision_b)` 决定；
> 阵亡后的补位由阵亡方玩家选择（判断 8），选择是玩家策略的一部分、同样确定。
> 等价说法：`state` 是一份**完备的回合间快照**；回合内派生量（谁应对了谁、减伤多少、出手顺序）一律住在局部 `TurnContext`，**不落状态**。

这条不是口头承诺，用一条测试机械证明：把状态 `to_dict()` → `from_dict()` 往返一次，再跑同一回合，事件流逐字节相同、`state_hash()` 相同。参考项目做不到这一点——它的 `_defense_skill_a`（回合内减伤标记）住在状态里、`revealed_a/b` 是 `set` 且被 `to_dict()` 排除、而且它**根本没有 `from_dict`**，所以"从快照恢复"这件事从未被验证过。

**这一层为什么是"不可再减"的**：拿掉其中任何一条，它就不再是这个游戏的回合制战斗。

1. 两个阵营各有一个在场单位，各带 HP 与一种资源（能量）。
2. 一个闭合的主动作集合：一个攻击、一个位置动作、一个资源动作——资源动作是"付不起会怎样"这个问题存在的前提。
3. **双方同时提交，然后按序结算。** 两个 `Decision` 必须在任何结算之前都收齐——否则应对三角（第 5 条）在语义上无法实现。这是整个引擎最重要的一个结构决定。
4. **一个附赠动作通道**（道具），与主动作同回合提交、走**同一条优先级队列**。
5. **应对三角**：攻击 > 状态 > 防御 > 攻击。没有它，状态技能严格劣于攻击、防御只在被打时有用，自博弈学不到任何东西——三类技能会退化成"比谁威力高"。这是本项目的技能表**内含**的机制，不是可选装饰。
6. 出手顺序：优先级 → 速度 → **注入的**种子硬币。三层都要有；硬币那层是"必须从第一天就有注入式 RNG"的原因。
7. 每类动作一个结算函数，返回事件 dict 列表。
8. **一条伤害公式、一个扣血口、一个回复口。** 哪怕现在只有裸算式与一个道具，也要走这三个漏斗。
9. 属性增益是**离散的层对象列表**，`aggregate_stats()` 只是派生视图。求和字典做不到"离场清除非永久层"，更做不到将来的驱散 N 层 / 层数翻倍。
10. 阵亡 → 扣命 → **由阵亡方玩家决定补位**（判断 8）→ 判胜负。**阵亡即回合结束**
    （判断 9）：剩余队列条目不再结算，补位完成后才走回合末收尾。
11. 终局条件：胜（命归零或无存活单位）+ `max_turns` 平局。平局兜底不是可选项——随机或 LLM 策略没有它会永远循环。
12. 回合末清理 + `turn += 1`，**只在唯一一条路径上执行**。
13. 注入式种子 RNG + `state_hash()` + `to_dict()` / `from_dict()` / `clone()`。第一天加是几十行，事后补要审计每一个调用点。
14. `legal_actions` 与 `validate_decision` **共用一个谓词**；非法提交不消耗回合。
15. 一条事件日志（纯 JSON dict，每条带 `type` 与 `side`），以及一个策略缝：`Player` 协议 + 脚本 / 随机两个实现。**在 LLM 出现之前，整台引擎就要能被跑完。**

**E0b 明确不做**：属性克制 / STAB（14 个技能全是普通系，克制表在 E0 是 no-op）、血脉的战斗效果、道具的第二种、状态叠层（中毒/灼烧）、印记、精灵特性、迷雾、LLM、`BattleManager` 式全吞异常门面、async、Web、效果 DSL、修饰链、`选择：A或B` 分支、技能冷却、多段连击。

**文件清单**（约 900 行，11 个源码文件 + 1 个测试助手 + 3 个测试文件）：

| 路径 | 用途 |
|---|---|
| `src/environment/rng.py` | `BattleRng`：种子必填的注入式 RNG，只暴露 `choice()`，每次抽取计数 |
| `src/environment/models.py` | `SIDES` / `ActionType` / `Skill` / `StatModifier` / `Unit` / `SideState` / `BattleState` + `aggregate_stats` / `build_unit` / `new_battle` / `to_dict` / `from_dict` / `clone` / `state_hash` |
| `src/environment/events.py` | `EVENT_TYPES` 登记表 + `ev()` 唯一事件构造器 |
| `src/environment/actions.py` | 动作工厂 + `Decision` + `skill_block_reason` 唯一谓词 + `legal_actions` / `legal_items` / `validate_decision` |
| `src/environment/damage.py` | `compute_damage`（纯函数）+ `apply_hp_loss` + `apply_heal`（三个唯一入口） |
| `src/environment/engine.py` | `TurnContext` / `build_turn_context` / `entry_priority` / `build_queue` / `resolve_*` / `settle_faints` / `check_winner` / `process_queue` / `end_of_turn` / `execute_turn` / `step` |
| `src/environment/session.py` | `BattleSession`：同时提交缓冲 + 合法性闸门 + 只读观测 |
| `src/environment/players.py` | `Player` Protocol + `ScriptedPlayer` + `RandomPlayer` |
| `src/environment/match.py` | `TurnRecord` / `MatchResult`（含 `digest()`）+ `run_match` |
| `src/environment/__init__.py`（改） | 对外 re-export：`new_battle` / `BattleSession` / `run_match` / `RandomPlayer` / `step` |
| `src/environment/__main__.py`（改） | 加 `battle` 子命令：`--seed` / `--repeat` / `--quiet` / `--json` |
| `tests/rosters.py` | 手写阵容助手（辅助模块，非测试） |
| `tests/test_environment_actions.py` | 动作空间、道具与合法性（约 12 组） |
| `tests/test_environment_engine.py` | 回合循环、应对三角、增益层、终局（约 28 组） |
| `tests/test_environment_match.py` | 整局编排、马尔可夫性与确定性（约 12 组） |

### E0b.1 规则与随机源

**做什么**：先把"所有可调数字"和"所有随机性"各收进一个地方。这两件事事后再收的成本最高。

```python
# rules.py（E0a 已建，E0b 补齐战斗字段）
@dataclass(frozen=True)
class BattleRules:
    """一局对战的全部数值常量。构造一次、挂在 state 上、**永不按调用覆盖**。"""
    team_size: int = 3
    skill_slots: int = 3
    lives: int = 2                      # 与 team_size 解耦，不要写 lives = len(units)
    energy_max: int = 10
    energy_start: int = 10
    recharge_amount: int = 5
    max_turns: int = 50
    switch_priority: int = 99
    item_priority: int = 99             # 道具与换人同为最高一级
    item_before_main_action: bool = True  # 同方同优先级时道具先结算（见判断 5）
    stat_pct_per_layer: float = 0.10    # 1 层 pct = 10%
    stat_flat_per_layer: int = 10       # 1 层 flat = +10
    stat_layer_cap: int = 99            # 每 (单位, 属性) 的总层数上限
    damage_coefficient: float = 0.9
    min_damage: int = 1
    iv_max: int = 10

DEFAULT_RULES = BattleRules()
ITEM_HEAL_PCT = 0.5                     # 草魔法：回复 50% 最大 HP
E0_ITEMS = {"草魔法": 1}                 # 道具 → 每方每局可用次数
```

测试造平局场景用 `dataclasses.replace(DEFAULT_RULES, max_turns=3)`，不要给 `execute_turn` 加 `max_turns` 参数——参考项目的 `max_turns` 从 `Settings` → `MatchConfig` → `manager.resolve` → `execute_full_turn` 传了四层，任何一层漏传都会静默改变判平局的时机。

```python
# rng.py —— seed 是必填位置参数，不存在"未注入时回退全局 random"的分支（见 §8 陷阱 2）
@dataclass
class BattleRng:
    seed: int
    calls: int = 0
    def choice(self, seq): ...          # calls += 1 后返回选中项
    def audit(self) -> dict: ...        # {"seed": …, "calls": …}，进 to_dict
```

**自检**：同 seed 两次构造得到同一序列；`calls` 正确递增；不给 seed 构造应当 `TypeError`。

### E0b.2 领域模型

**做什么**：把"一局对战的全部**回合间**事实"落成 dataclass，并让它能完整往返序列化——这是核心不变式的物质基础。

三条边现在就拆：① 持久状态按方收进 `SideState`（消灭参考项目里约 30 处 `team_a if s=="a" else team_b` 三元分支）；② 运行时服务（RNG）只把 `seed`/`calls` 进 `to_dict()`；③ **回合内派生量根本不进状态**（住在 `TurnContext`，见 E0b.4）。

```python
# models.py
SIDES: tuple[str, str] = ("a", "b")

class ActionType(str, Enum):
    SKILL = "skill"; SWITCH = "switch"; RECHARGE = "recharge"

@dataclass(frozen=True)
class Skill:
    """静态定义，不可变、可共享。effect 来自 skillbook.E0_EFFECTS。"""
    name: str; kind: str                 # 物攻 / 魔攻 / 防御 / 状态
    type: str                            # E0 全为「普通」，E2 才生效
    power: int; energy_cost: int
    priority: int = 0
    desc: str = ""                       # 只用于展示，引擎永不解析
    effect: SkillEffect = ...

@dataclass
class StatModifier:
    """一条增益/减益记录。**层数是记账单位**：1 层 = 10%(pct) 或 +10(flat)。
    离散对象而不是求和字典——离场清除非永久层、将来驱散 N 层／层数翻倍都要求可枚举。"""
    stat: str                            # atk / sp_atk / def / sp_def / speed
    mode: str                            # "pct" | "flat"
    layers: int
    permanent: bool = False              # E0 的 5 个状态技能全是 False
    source: str = ""                     # 技能名，便于事件与调试
```

```python
@dataclass
class Unit:
    """场上单位。**没有 take_damage / heal 方法**——`current_hp` 与 `fainted` 的唯一
    写者是 damage.apply_hp_loss / apply_heal。只经 build_unit 构造。"""
    name: str
    types: list[str]
    stats: dict[str, int]                # calc_combat_stats 的输出，**只读基线**
    skills: list[Skill]
    nature: str = "坦率"; bloodline: str = ""; iv: dict[str, int] = field(default_factory=dict)
    max_hp: int = 0; current_hp: int = 0
    energy: int = 0; fainted: bool = False
    stat_mods: list[StatModifier] = field(default_factory=list)

def aggregate_stats(unit: Unit, rules: BattleRules = DEFAULT_RULES) -> dict[str, int]:
    """派生视图：`base × (1 + 0.10 × Σpct层) + 10 × Σflat层`，每个属性的总层数先夹到
    `rules.stat_layer_cap`。**绝不把结果写回 unit.stats**——基线值必须保持可回溯，
    否则「离场清除增益」就没有可以退回的原点。伤害计算一律读这个函数，不读 unit.stats。"""

@dataclass
class SideState:
    """一方的持久状态。引入它是为了消灭约 30 处 a-if-else 三元分支。"""
    units: list[Unit]
    lives: int
    active: int = 0
    item_uses: dict[str, int] = field(default_factory=dict)   # 道具名 → 剩余次数
    @property
    def active_unit(self) -> Unit: ...
    def first_living_bench(self) -> int | None: ...   # 排除 active；无则 None
    def has_living(self) -> bool: ...
    def to_dict(self) -> dict: ...

@dataclass
class BattleState:
    """一局对战的全部**回合间**事实。rng 无默认值 → 构造不出没有种子的对局。
    这里**没有任何**回合内字段——那是 TurnContext 的事。"""
    side_a: SideState; side_b: SideState
    rng: BattleRng
    rules: BattleRules = DEFAULT_RULES
    turn: int = 1
    winner: str | None = None; done: bool = False
    battle_id: str = ""

    def side(self, s: str) -> SideState: ...    # 唯一的 a/b 分支点
    def foe(self, s: str) -> SideState: ...
    def active(self, s: str) -> Unit: ...

    def to_dict(self) -> dict:
        """全量 JSON 快照。**只含 JSON 原生类型**；`json.dumps` 不许传 `default=`，
        塞进非序列化对象要当场炸（见 §8 陷阱 8）。要序列化集合就 sorted()。"""
    @classmethod
    def from_dict(cls, d: dict) -> "BattleState":
        """to_dict 的逆。**这是核心不变式的验证工具，不是可选项。**
        RNG 还原方式：`Random(seed)` 之后**丢弃 calls 次抽取**，随机流位置完全复原。
        `rules` 也从 dict 还原，于是自定义 rules 的对局也能往返。"""
    def clone(self) -> "BattleState":
        """E0 实现 = `from_dict(to_dict())`：显然正确，且天然复用序列化往返测试。
        性能不够时再优化，**别用 copy.deepcopy**（参考项目为此付过代价）。"""
    def state_hash(self) -> str:
        """sha256(json.dumps(to_dict(), sort_keys=True, ensure_ascii=False))。"""

def build_unit(spec: dict, rules: BattleRules = DEFAULT_RULES) -> Unit:
    """唯一构造路径：吃 E0a 的 roster spec，按名从 skillbook 取技能定义，
    派生 max_hp / current_hp / energy。非法 spec 抛 ValueError。"""

def new_battle(roster_a, roster_b, *, seed: int, items_a=None, items_b=None,
               rules=DEFAULT_RULES, battle_id: str = "") -> BattleState: ...
```

```python
# events.py
EVENT_TYPES: frozenset[str] = frozenset({
    "damage", "heal", "item_use", "stat_change", "reduce_arm", "recharge",
    "switch", "replace", "faint", "life_loss", "skipped", "battle_end", "error",
})

def ev(etype: str, side: str, **fields) -> dict:
    """事件唯一构造器：保证每条都带 type 与 side（无归属的全局错误用 ""）。
    参考项目每条事件同时写 team 和 side 两个同义字段，是双事实源；本项目只有 side。"""
    return {"type": etype, "side": side, **fields}
```

E0b 的事件全集（13 种，形状在此定死，后续里程碑只加字段不改形状）：

```python
{"type":"reduce_arm", "side":"b","skill":"防御","pct":0.7,"armed":True}   # armed=False 表示对手没出攻击，白防
{"type":"damage",     "side":"a","attacker":"迪莫","skill":"抓挠1","target":"布布",
                      "damage":108,"target_hp_left":12,"counter":"状态","mult":1.5,"reduced":0.0}
{"type":"stat_change","side":"a","unit":"迪莫","skill":"加物攻","stat":"atk","mode":"pct",
                      "layers":11,"total_layers":11,"counter":"防御"}
{"type":"item_use",   "side":"a","item":"草魔法","unit":"迪莫","uses_left":0}
{"type":"heal",       "side":"a","unit":"迪莫","applied":60,"overflow":0,"hp":120,"source":"草魔法"}
{"type":"recharge",   "side":"a","unit":"迪莫","gained":5,"energy":10}
{"type":"switch",     "side":"a","out":"迪莫","in":"布布","cleared_layers":11}   # 离场清除的非永久层数
{"type":"replace",    "side":"b","out":"布布","in":"火苗"}                        # 阵亡后由该方玩家选择的补位
{"type":"faint",      "side":"b","unit":"布布"}
{"type":"life_loss",  "side":"b","unit":"布布","lives_left":1}
{"type":"skipped",    "side":"b","kind":"main","unit":"布布","reason":"已被击倒"}
{"type":"battle_end", "side":"a","winner":"a","turn":9}      # 平局：side="both", winner=None, 带 message
{"type":"error",      "side":"", "message":"对局已结束。"}
```

**自检**：`new_battle(...)` 两次独立构造同 seed 同阵容，`state_hash()` 相等；一次扣血后不等；`json.dumps(state.to_dict())` **不传 `default=`** 也能过；`from_dict(to_dict(s)).state_hash() == s.state_hash()`。

### E0b.3 动作、伤害与回复

**做什么**：把"哪些提交合法"收进一个谓词，把"伤害怎么算、血怎么加减"收进三个函数。

```python
# actions.py
def skill_action(index: int) -> dict: ...
def switch_action(index: int) -> dict: ...
def recharge_action() -> dict: ...
# 一律用工厂函数，**不设模块级常量 dict**——共享可变对象被下游 mutate 就全局串味

@dataclass(frozen=True)
class Decision:
    """一方一回合的完整提交：一个主动作 + 可选一个道具（附赠动作）。
    道具**不占用**主动作，两者同回合生效、各自入队。"""
    action: dict
    item: str = ""                       # "" = 本回合不用道具

def skill_block_reason(state, unit: Unit, index: int) -> str | None:
    """唯一的技能门控谓词：可用 → None，否则中文原因。
    `legal_actions` 与 `validate_decision` **只能**通过它判断技能可用性。
    E0 的门只有两道：① 槽位越界（含非 int）② 能量不足。
    将来的冷却 / 禁足 / 蓄力锁 / 号位锁全部只加进本函数。"""

def legal_actions(state, side: str) -> list[dict]: ...
    # **只含合法项**——不像参考项目那样把被门控的技能也塞进池子再靠 reason 字段标记
    # （那样每个消费者都得记得过滤，而它自己的随机兜底就漏过了一次）。
    # recharge 恒在池中 → 池永不为空 → 不会死锁。

def legal_items(state, side: str) -> list[str]: ...   # 剩余次数 > 0 的道具名
def validate_decision(state, side: str, dec: Decision) -> str | None: ...
    # 合法 → None；非法 → 原因字符串（调用方据此拒绝且**不消耗回合**）。
    # 校验项：对局已结束 / 未知 action type / value 类型与范围 / 换人目标是自己或已倒下 /
    #        技能走 skill_block_reason / 道具名未知或次数已尽。
```

```python
# damage.py —— 全局唯一的三个口子，任何伤害/回复都不许绕过
def compute_damage(state, attacker: Unit, defender: Unit, skill: Skill, *,
                   counter_mult: float = 1.0, reduction: float = 0.0) -> int:
    """唯一伤害公式（纯函数：零副作用、零 RNG、零事件）。

        raw = (atk / def) × power × rules.damage_coefficient × counter_mult × (1 − reduction)
        dmg = 0 if power <= 0 else max(rules.min_damage, int(raw))

    物攻读 aggregate_stats 的 atk/def，魔攻读 sp_atk/sp_def；atk/def 下限 1。
    **全程 float，只在出口 int() 一次**——参考项目 int() 截断与 int(round()) 混用，
    而且减伤是第二次取整，于是"改一个乘子"会在两处产生不同的舍入。
    应对乘子与减伤都是**显式入参**，不读 TurnContext——这样它是可单测的纯函数。
    """

@dataclass(frozen=True)
class HpLoss:
    requested: int; applied: int; fainted: bool

@dataclass(frozen=True)
class HealResult:
    requested: int; applied: int; overflow: int

def apply_hp_loss(state, target: Unit, amount: int, *, source: str) -> HpLoss:
    """唯一扣血入口：本函数是 `current_hp` / `fainted` 的唯一写者之一。
    amount 夹到 [0, current_hp]；归零置 fainted=True。不发事件（事件由 engine 发）。"""

def apply_heal(state, target: Unit, amount: int, *, source: str) -> HealResult:
    """唯一回复入口。amount 夹到 [0, max_hp − current_hp]；`overflow` 记录被夹掉的量
    （将来"若敌方本回合回复生命，改为失去 2 倍含溢出量"这类效果要读夹取前的值）。
    **已阵亡单位不可回复**（applied=0）——E0 没有复活语义。"""
```

草魔法的量：`amount = unit.max_hp // 2`（`ITEM_HEAL_PCT = 0.5`），夹取由 `apply_heal` 负责，所以"回满为止"是漏斗的自然结果，不需要在道具代码里再写一遍 min。

为什么伤害与回复要走漏斗、哪怕现在只有一条公式和一个道具：将来的减伤链、「受致命伤害时保留 1 点生命」、吸血、on-damaged 钩子、前瞻预测（「若对手这招足以击败我」）全都挂在这三个函数上。参考项目就是因为一开始没有漏斗，后来不得不用「临时改攻击方属性 → 算 → 改回来」的写法，导致伤害路径不纯、前瞻无法复用。

**自检**（用 atk=100 / def=100 这组好算的数手验，然后把它们写成测试）：

| 场景 | 算式 | 期望 |
|---|---|---|
| 抓挠（power 60），无应对无减伤 | `(100/100) × 60 × 0.9` | **54** |
| 抓挠1（power 80），应对状态 ×1.5 | `1.0 × 80 × 0.9 × 1.5` | **108** |
| 抓挠1 撞上「防御」（减伤 70%） | `1.0 × 80 × 0.9 × 1.0 × 0.3` | **21** |
| 抓挠1 应对状态 ×1.5 且被减伤 70% | `1.0 × 80 × 0.9 × 1.5 × 0.3` | **32**（一次取整：`int(32.4)`） |
| 叠了 加物攻 9 层（+90%）后的抓挠 | `(190/100) × 60 × 0.9` | **102**（`int(102.6)`） |
| 防御 / 状态技能（power 0） | — | **0** |

另外三条：超额伤害的 `applied == current_hp` 且 `fainted`；`compute_damage` 调用前后 `state.rng.calls` 不变；把最后两行的乘子顺序调换，结果**不变**（证明只取整一次）。

### E0b.4 回合循环（本里程碑唯一的难点）

**做什么**：一个函数，五步，一条清理路径，一个局部上下文。这段伪代码就是 E0b 的全部难度，其余都是数据搬运。

```python
@dataclass(frozen=True)
class TurnContext:
    """本回合的全部派生量。**是局部对象，不是 BattleState 的字段。**

    这是核心不变式的关键：回合内临时量一旦住进状态，就必须记得清理（参考项目的
    `_defense_skill_a` 正是如此，`end_of_turn_cleanup` 手工清 8 个字段），而且
    「状态只承载回合间事实」这条不变式当场破产。做成局部对象，跨回合泄漏在结构上
    不可能发生，`end_of_turn()` 也就不必知道任何回合内的事。
    """
    decision_a: Decision; decision_b: Decision
    category_a: SkillCategory | None      # 本方声明的技能类别（换人/聚能 → None）
    category_b: SkillCategory | None
    reduction_a: float                    # 已武装的减伤比例（未武装 = 0.0）
    reduction_b: float
    def decision(self, side) -> Decision: ...
    def category(self, side) -> SkillCategory | None: ...
    def reduction(self, side) -> float: ...
    def counters(self, side) -> bool:
        """本方技能的 counter_vs 是否命中对手本回合声明的类别。"""

def build_turn_context(state, dec_a, dec_b) -> tuple[TurnContext, list[dict]]:
    """① 读出双方声明的技能类别；② 为防御方武装减伤（**仅当对手声明了攻击**，
    且能量足以支付——付不起在 E0 压根就是非法动作，所以这里天然成立）。
    返回 ctx 与 `reduce_arm` 事件列表。"""
```

```python
def resolve_turn(state, dec_a: Decision, dec_b: Decision) -> tuple[list[dict], str | None]:
    """结算一回合的队列，直到第一个阵亡或回合正常结束。**不做回合末收尾、不自动补位。**

    返回 (事件, 需要补位的方 或 None)：
    - None 且 state.done：终局（无存活 / 命归零，无补位可问）；
    - None 且 not done：回合正常结束（无阵亡）；
    - 某方：该方在场阵亡且有存活后备，**回合在此结束**，等待该方玩家选择补位。

    # ── 0) 守卫：终局后不再推进（零状态变更）
    if state.done:
        return [ev("error", "", message="对局已结束。")], None

    events = []

    # ── 1) DECLARE：双方声明已知 → 定应对关系 + 武装减伤
    #    必须在任何结算之前：同时出招语义要求后手防御方也能为先手攻击减伤
    ctx, arm_events = build_turn_context(state, dec_a, dec_b)
    events += arm_events

    # ── 2) ORDER + 3) ACT：统一优先级队列（最多 4 条）
    for entry in build_queue(state, ctx):
        if entry.actor.fainted:
            events.append(ev("skipped", entry.side, kind=entry.kind,
                             unit=entry.actor.name, reason="已被击倒"))
            continue                     # 防御性兜底：正常流程阵亡即暂停，到不了这里
        events += resolve_entry(state, ctx, entry)
        faint_events, need_side = settle_faints(state)   # faint → life_loss，不自动补位
        events += faint_events
        if need_side is not None:
            winner = check_winner(state)
            if winner is not None:
                state.winner, state.done = winner, True
                return events, None      # 无存活 / 命归零 → 终局，无补位可问
            return events, need_side     # 有存活后备 → 暂停等玩家补位（回合在此结束）

    return events, None


def apply_replacement(state, side: str, bench_idx: int) -> list[dict]:
    """应用玩家选择的补位：active = bench_idx，发 replace 事件。阵亡单位已死，无需清层。"""

def end_turn(state) -> list[dict]:
    """回合末统一收尾：**整个代码库里唯一推进回合号的地方**，battle_end 只在这里发射。
    终局 → battle_end(winner)；超过回合上限 → 平局；否则只推进回合号。"""

def execute_turn(state, dec_a: Decision, dec_b: Decision) -> list[dict]:
    """推进一回合。**默认补位策略**：阵亡时自动取第一个存活后备。
    execute_turn 是确定性整回合转移（供 step / MCTS / 回放用）；玩家**交互式**补位由
    session 驱动 resolve_turn → apply_replacement → end_turn。"""
    if state.done:
        return [ev("error", "", message="对局已结束。")]
    events, need_side = resolve_turn(state, dec_a, dec_b)
    if need_side is not None:
        bench = state.side(need_side).first_living_bench()
        events += apply_replacement(state, need_side, bench)
    events += end_turn(state)
    return events


def step(state, dec_a, dec_b) -> tuple[BattleState, list[dict]]:
    """**纯转移语义**：不改入参 state，返回 (新状态, 事件流)。实现 = clone 后调 execute_turn。
    前瞻 / MCTS / 回放校验用它；正常对局用 session 的交互式流程。
    这个函数是负责人第 1 个问题的可执行答案：`step` 就是 f(state, a_a, a_b) → state'。"""
```

出手顺序与队列：

```python
@dataclass(frozen=True)
class QueuedEntry:
    side: str
    kind: str                # "item" | "main"
    actor: Unit              # **入队时**的在场单位；执行前若已阵亡则整条跳过
    priority: int
    speed: int               # 入队时 aggregate_stats(actor)["speed"]

def entry_priority(state, ctx, side, kind) -> int:
    """道具 = rules.item_priority(99)；换人 = rules.switch_priority(99)；
    技能 = skill.priority（E0 全为 0）；聚能 = 0。"""

def build_queue(state, ctx) -> list[QueuedEntry]:
    """最多 4 条。排序键，依次比较：
        ① priority 降序
        ② 在场精灵速度降序（读 aggregate_stats，所以本回合之前叠的速度层算数）
        ③ 同方同优先级：道具先于主动作（rules.item_before_main_action）
        ④ 跨方仍完全相同：state.rng.choice 抛硬币（50%）
    第 ④ 步是 E0b **唯一**的 RNG 抽取点。注意 rng.calls 因此**不等于**回合数——
    只有跨方平手才抽，用速度互异的阵容跑完整局应当 calls == 0。"""
```

**为什么道具是优先级 99 而不是"随主动作附赠"**：给道具一个优先级，它就自动落进同一条排序规则里——不需要为它开一个特殊阶段，也不需要回答"附赠动作算不算一次行动"。副作用是 E0 里道具**总是先于所有技能结算**（99 > 0），于是"这回合先回血再挨打"成立；而 `item_before_main_action` 决定"换人 + 道具"时草魔法治的是**离场的那只**（默认 True）。这一条是 `BattleRules` 里的一个布尔，负责人若想改成"先换人再回血"，只改这一个字段。

四个结算函数与三个收尾函数：

```python
def resolve_item(state, ctx, entry) -> list[dict]:
    """扣 1 次道具次数 → 对**当前在场**单位调 apply_heal(max_hp // 2)
    → 发 item_use + heal。次数已尽在 validate_decision 就被拦住，这里不再兜。"""

def resolve_skill(state, ctx, entry) -> list[dict]:
    """支付能量 → 按 effect.category 分三支：
      攻击：compute_damage(counter_mult=1.5 if ctx.counters(side) else 1.0,
                          reduction=ctx.reduction(对手方)) → apply_hp_loss → damage 事件
            再处理 self_energy_gain（抓挠系 +1，夹到 energy_max）
      防御：什么也不做 —— 减伤已在 build_turn_context 武装完毕
      状态：layers = effect.layers + (effect.counter_extra_layers if ctx.counters else 0)
            → 合并进 unit.stat_mods 的同 (stat, mode, permanent) 记录 → stat_change 事件
    """

def resolve_switch(state, ctx, entry) -> list[dict]:
    """改 active 下标 → **清除离场单位的全部非永久增益层**
    （`mods = [m for m in mods if m.permanent]`）→ switch 事件带 cleared_layers。"""

def resolve_recharge(state, ctx, entry) -> list[dict]:
    """energy = min(energy_max, energy + recharge_amount) → recharge 事件带实际 gained。"""

def settle_faints(state) -> tuple[list[dict], str | None]:
    """按 SIDES 固定序检查双方在场单位：阵亡 → faint → life_loss(−1)，**不自动补位**。
    返回 (事件, 需要补位的方 或 None)。一次只处理第一个阵亡——回合在第一个阵亡处结束
    （判断 9）。是否终局由调用方用 check_winner 判定。补位由 apply_replacement 应用。"""

def apply_replacement(state, side, bench_idx) -> list[dict]:
    """应用玩家选择的补位：active = bench_idx，发 replace 事件。阵亡单位已死，无需清层。"""

def check_winner(state) -> str | None:
    """某方 lives <= 0 或已无存活单位 → 对方获胜；否则 None。
    第二个条件是死锁兜底。E0 默认 3 只 / 2 命，所以走的是第一个条件；
    要测第二个条件得用 `replace(DEFAULT_RULES, lives=5)`。"""

def end_of_turn(state) -> None:
    """回合末清理。**E0b 函数体是 pass，而且理应如此**——回合内临时量都在 TurnContext
    这个局部对象里，随函数返回自然消失。
    将来的状态叠层 tick / 冷却递减 / 增益 duration 递减挂进本函数，回合推进仍只有一处。"""
```

**四条不变式**（写代码时反复回头对照）：

1. **回合号推进与 `battle_end` 发射只发生在 `end_turn` 一处。** 参考项目把它散在三处（`engine.py` 的终局分支、常规分支、`tick.py`），代价是决胜回合的回合号与上一回合相同，上层的去重守卫把决胜回合整条丢掉——轨迹里永远看不到胜负是怎么产生的。
2. **阵亡即回合结束（判断 9）。** `resolve_turn` 在第一个阵亡处暂停，剩余队列条目整体丢弃；该方有存活后备 → 等玩家补位，无存活 / 命归零 → 直接终局。
3. **终局回合也走统一收尾**（`end_turn`），回合号照常推进。
4. **`state` 里不允许出现任何回合内字段。** 新增一个字段前先问："下个回合还需要它吗？"不需要就放 `TurnContext`。

**自检**：常规回合 turn 1→2；决胜回合也 1→2 且 `done`；平局回合同样推进；`grep -c "state.turn += 1" src/environment/engine.py` 输出 `1`；`grep -c "TurnContext" src/environment/models.py` 输出 `0`（回合内类型不许出现在状态模块里）。

### E0b.5 跑通一局

**做什么**：给引擎套一层"同时提交"的门面，加两个零 LLM 的策略，加一个能打完整局的编排器和一个人能看懂的 CLI。这一步做完，E0 才算"可运行"。

```python
# session.py —— 对局状态机：SUBMIT → RESOLVE → (阵亡?) REPLACE_QUERY → END_TURN
class BattleSession:
    """同时提交缓冲 + 合法性闸门 + 只读观测。**不做 try/except Exception
    的全吞降级**（理由见判断 7）；"非法输入不抛"由显式校验实现，不由兜底捕获实现。
    非线程安全：并发由未来的 web 层持锁。"""
    @classmethod
    def start(cls, roster_a, roster_b, *, seed: int, items_a=None, items_b=None,
              rules=DEFAULT_RULES, battle_id: str = "") -> "BattleSession": ...
    def pending_sides(self) -> list[str]: ...            # 稳定序，取自 SIDES
    def legal_actions(self, side: str) -> list[dict]: ...
    def legal_items(self, side: str) -> list[str]: ...
    def replacement_options(self, side: str) -> list[int]: ...   # 阵亡后可选的存活后备槽位
    def submit(self, side: str, dec: Decision) -> dict:
        """校验并缓冲一方提交（同时出招语义：只入缓冲、不推进状态）。
        非法 → {"ok": False, "error": …}，缓冲不变、turn 不变、state_hash 不变。"""
    def resolve(self) -> dict:
        """双方齐备 → 结算到第一个阵亡（**暂停**返回 need_replacement）或回合正常结束。
        返回 events / need_replacement / done / winner / state_hash / observation。
        未齐备 → {"ok": False, "error": "双方提交未齐备。"}，零状态变更。"""
    def submit_replacement(self, side: str, bench_idx: int) -> dict:
        """阵亡方玩家提交补位选择：应用补位 → 回合末收尾（回合号推进 / battle_end）。
        非法 → {"ok": False, "error": …}，state 不变、回合未推进。"""
    def observe(self, side: str = "") -> dict: ...        # E0 对称全量观测（迷雾在 E4）
```

```python
# players.py
class Player(Protocol):
    """对局内核的玩家端口。E0 的两个实现零 LLM、零网络。"""
    side: str
    kind: str
    def on_match_start(self, observation: dict) -> None: ...
    def decide(self, observation: dict, legal: list[dict], items: list[str]) -> Decision: ...
    def choose_replacement(self, observation: dict, bench: list[int]) -> int: ...
        # 己方在场阵亡时被调用：从存活后备槽位里选一个（判断 8）
    def on_turn_result(self, observation: dict, events: list[dict]) -> None: ...

class ScriptedPlayer:      # 固定 Decision 序列；耗尽后返回 Decision(recharge_action())
    def __init__(self, side: str, script: list[Decision] | None = None) -> None: ...
    def choose_replacement(self, observation, bench): return bench[0]   # 固定取第一个

class RandomPlayer:
    """从 legal 里随机取一个主动作，并以 `item_prob` 的概率带上一个可用道具。
    **自带独立 BattleRng(seed)，绝不共用引擎的 RNG 流**——混流的后果是换个策略就
    改变引擎的平手硬币，于是"用同一 seed 重放一条提交日志"再也复现不出同一条轨迹，
    E6 的轨迹回放直接失效。"""
    def __init__(self, side: str, *, seed: int, item_prob: float = 0.2) -> None: ...
    def choose_replacement(self, observation, bench): return self._rng.choice(bench)
```

注意 `decide(observation, legal, items)` 的形状：合法池由**编排器显式传入**，玩家不持有 session 句柄。参考项目的 `random_legal_action(manager, side)` 把兜底策略耦合到门面对象上，于是策略无法脱离引擎单测。

```python
# match.py
@dataclass
class TurnRecord:
    turn: int                     # **提交时**的回合号
    decision_a: Decision; decision_b: Decision
    events: list[dict]
    state_hash: str

@dataclass
class MatchResult:
    battle_id: str; seed: int
    winner: str | None; done: bool
    turns: list[TurnRecord] = field(default_factory=list)
    @property
    def turn_count(self) -> int: ...
    def digest(self) -> str:
        """整局指纹 = sha256(逐回合 state_hash 拼接)。确定性闸门只比这一个字符串。"""

def run_match(session: BattleSession, players: dict[str, Player]) -> MatchResult:
    """把一局跑到 done：每回合 双方 decide → submit → resolve → on_turn_result。"""
```

`TurnRecord.turn` 取**提交时**的回合号，于是 `[t.turn for t in turns] == list(range(1, n+1))`、`len(turns) == turns[-1].turn`。参考项目的 50 回合上限会报 `turn=51`，那个 off-by-one 只能靠注释解释。

```python
# __main__.py 的 battle 子命令
#   --seed N        引擎 seed（默认 20260823）
#   --seed-a/-b N   玩家 seed（默认 seed+1 / seed+2）
#   --preset NAME   mirror（默认，双方同规格同速 → 平手硬币必然触发）| asym（速度互异，永不平手）
#   --repeat N      同参数跑 N 次并比对 digest（默认 1）
#   --turns N       覆盖 rules.max_turns
#   --quiet / --json
```

**九个设计判断**（负责人会问，这里先给结论和理由）：

1. **3 只精灵 / 2 条命**（负责人指定）。注意这让「命归零」成为唯一的常规败因——第 3 只只能用来换人，永远轮不到被"花掉"。副作用是 `check_winner` 的第二个条件（无存活单位）在默认配置下走不到，测它需要 `replace(DEFAULT_RULES, lives=5)`；这条要写进测试注释，否则日后有人会以为它是死代码而删掉。
2. **`switch` 与道具同为优先级 99**。于是 E0b 的队列是**真队列**（最多 4 条），而不是"先手/后手两格"。这提前兑现了参考项目直到很晚才补的 `ACT_LOOP` 队列——而它当初是靠 `break` 处理"行动者已阵亡"，队列一变长就是 bug。
3. **同名精灵是否允许重复入队？** 建议**允许**（`validate_team` 不拦），因为 E0 只有 6 只精灵、3 个槽位，禁止重复会让组队空间小到无趣。这是 `BattleRules` 之外的一个策略选择，写在 `validate_team` 的 docstring 里。
4. **能量不足 = 非法动作，不自动降级为聚能。** 参考项目两者都做（谓词拦 + 结算降级），同一条件两套行为，于是「到底哪个才是规则」没人说得清——「零能量仍白拿防御标记」就是这个二义性的直接产物。而 `legal_actions` 里恒有 `recharge`，所以不会死锁。自动转聚能是「给弱决策者的仁慈」，E5 作为**玩家侧兜底**重新引入才是对的层。
5. **道具治的是离场那只**（`item_before_main_action = True`）。因为道具优先级 99 与换人相同，同方同优先级需要一个确定的次序；选"道具先"是因为它是附赠动作。想改成"先换人再回血"，只翻这一个布尔。
6. **HP 与六维全用 `int`**，浮点只在伤害公式内部当中间量，在出口向下取整**一次**。整型化让 `state_hash` 天生稳定，也让减伤只有一个取整点。
7. **不搬参考项目的 `_guard` 全吞异常。** 铁律 2「宁失败不抛」约束的是**外部调用**；E0b 引擎是进程内纯算术，没有可降级的外部失败。参考项目的代价是每个引擎 bug 变成一次静默的错误答案（真实堆栈只在 warning 里），而 E0b 的 Gate 靠人眼看事件流，静默错误是最坏的失败模式。全吞守卫的正确位置是 **E5 的工具闭包**（LLM 是不可信调用方），且必须 `exc_info=True` 并在 `DEBUG` 时 `raise`。
8. **阵亡补位由玩家决定（负责人 2026-08-24 指定，替代原「强制补位」）。** 结算到第一个阵亡时回合**暂停**：`resolve_turn` 返回需要补位的方，`BattleSession` 进 REPLACE_QUERY 态，`Player.choose_replacement` 从存活后备里选一个，`submit_replacement` 应用后才走回合末收尾。对自对战 agent（随机/LLM）来说，补位选择是策略的一部分。`execute_turn`/`step` 保留**默认补位策略**（取第一个存活后备），供 MCTS / 回放做确定性整回合转移；核心不变式相应改为「`resolve_turn` 由 (state, dec_a, dec_b) 唯一决定，补位是玩家策略」。马尔可夫性按每步成立。
9. **阵亡即回合结束（负责人 2026-08-24 指定）。** `resolve_turn` 在第一个阵亡处停止，**剩余队列条目整体丢弃**（不再结算、连 `skipped` 都不发）——先手把后手打死，后手本回合就出手不了了。这是对原「继续结算剩余条目」语义的修改。

**验收命令（用户亲自运行）**：

```bash
cd ~/workspace/MySelfPlayAgent
uv run python -m environment battle --seed 20260823
# 预期形状（mirror 预设：双方同速 → 平手硬币必然触发）。
# 具体数值取决于你手写的 e0_spirits.json，这里只示意每一行该带哪些信息：
#   E0b 自对战  seed=20260823  规则=3v3/2命/能量10/上限50  预设=mirror
#   T01  a:加物攻 +草魔法   b:防御
#        | item_use a 草魔法(剩0) · heal a +N(overflow=0)
#        | reduce_arm b 防御 70% armed=False        ← 对手出的是状态，防御落空
#        | stat_change a atk pct 11层(应对防御 +2)
#   T02  a:抓挠1            b:加物攻
#        | damage a→<b的在场> N 应对状态 ×1.5 · faint … · life_loss b(1) · replace …
#   ...
#   终局  winner=a  turns=7  done=True  rng_calls=5  digest=4b2f0c8d…
# 人眼核对五件事：① 应对三角三种都出现过（伤害 ×1.5 / 减伤 / 额外层数）
#                ② 有完整的 faint → life_loss → replace 链，且 battle_end 是最后一条
#                ③ 换人时 switch 事件带 cleared_layers（离场清增益）
#                ④ 道具只出现一次，uses_left=0 之后再不出现
#                ⑤ **阵亡补位**：非终局阵亡 → replace 事件（阵亡方玩家/策略的选择）紧跟
#                   life_loss；命归零的阵亡则直接 battle_end、无 replace

uv run python -m environment battle --seed 20260823 --repeat 2 --quiet
#   run#1  winner=a turns=7 rng_calls=5 digest=4b2f0c8d…
#   run#2  winner=a turns=7 rng_calls=5 digest=4b2f0c8d…
#   确定性：2 次 digest 一致 ✅         （不一致则打 ❌ 并 return 1，于是这条也能进 CI）

uv run python -m environment battle --seed 20260823 --preset asym --quiet
#   rng_calls=0  ← 速度互异 ⇒ 从不平手 ⇒ 引擎一次 RNG 都没抽（证明随机点没泄漏到别处）

uv run pytest tests/test_environment_*.py -q                        # 全绿
uv run pytest -q --cov=rock_pvp_agent --cov=environment             # environment ≥90%，rock_pvp_agent 仍 94%
```

五条命令都幂等：不写任何文件、不联网、不读 `.env`。

**测试清单**：先写 `tests/rosters.py`（辅助模块，非测试）：`pick()` / `team()` / `mirror_pair()`（同规格同速 → 必平手）/ `strong_weak()`（一击必杀 vs 挠痒痒）/ `tanky_pair()`（高血高防低攻 → 只可能平局）/ `fast_slow()`（速度互异 → 永不平手）/ `duel()`。**一律用推导式逐个构造，绝不 `[spec] * n`**——参考项目的测试里就有 `[{...}] * 6`，6 个引用指向同一个 dict，任何一处 mutate 就全队串味。

| 测试文件 | 覆盖点 |
|---|---|
| `test_environment_actions.py`（约 15 组） | 合法池每条都过 `validate_decision`；`skill_block_reason` 是"池 / 谓词 / 校验"三者的唯一真相（参数化断言判定一致）；能量不足是非法而非降级且**不产生** recharge 事件；槽位越界与错类型（`9`/`None`/`"0"`/`True`）全被拒；换人拒绝自己与已倒下；未知 action type 被拒；终局后一律拒；满能量时 `recharge` 仍合法且 `gained=0`；`Decision(item="草魔法")` 在次数已尽时被拒；未知道具名被拒；动作工厂返回全新 dict；池里不出现 `reason` 键；`replacement_options` 只含存活后备、`validate_replacement` 逐条拒（自己/越界/bool/字符串/已倒下/终局后） |
| `test_environment_engine.py`（约 30 组） | **伤害**：中性六维精确值、魔攻走 sp_atk/sp_def、`power=0` → 0、只取整一次（改乘子不产生双舍入）、超额夹取与阵亡置位、**源码扫描**断言 `current_hp` 的写入只出现在 `damage.py` 与 `models.build_unit`。**应对三角**：攻击应对状态 ×1.5、抓挠（基础款）**没有**这个加成、防御应对攻击减伤、防御遇状态时 `armed=False` 且能量照扣、状态应对防御多 +1~2 层、双方同类别时三者都不触发、对手换人/聚能时一律不触发。**增益层**：`加物攻` 首次 9 层、二次叠成 18 层、夹到 `stat_layer_cap`、`加速度` 走 flat（+80 而不是 +80%）、`aggregate_stats` 不写回 `unit.stats`、离场清除非永久层且 `permanent=True` 的留下。**道具**：优先级 99 先于所有技能、次数扣减、回满为止（`overflow` 正确）、阵亡单位不可回复、同回合"换人 + 道具"治离场那只。**队列**：4 条时排序正确、同方道具先于主动作、**阵亡即回合结束**（a 先手 KO → 剩余队列条目整体丢弃、连 skipped 都不发）、跨方完全平手才抽 RNG。**终局**：`faint→life_loss→replace` 顺序与 `lives_left` 递减、`settle_faints` 只发 faint/life_loss 并返回补位方、补位后不再掉命、命归零终局、`lives=5` 时无存活单位也终局、`max_turns` 平局、终局后再 `execute_turn` 返回单条 error 且 `state_hash` 不变、`turn` 三种回合都恰好 +1、**源码扫描**断言 `state.turn += 1` 只出现一次。**事件**：跑完一整局后每条都有 `type`/`side`、`type in EVENT_TYPES`、`json.dumps` 不炸 |
| `test_environment_match.py`（约 18 组） | **马尔可夫性（最重要的一条）**：跑若干回合后 `s2 = BattleState.from_dict(s1.to_dict())`，对 `s1`/`s2` 跑同一回合 → 事件流**逐字节相同**且 `state_hash()` 相同；`clone()` 后推进不影响原状态；`step()` 不改入参。**序列化**：`to_dict()` 不传 `default=` 也能 dumps、`from_dict(to_dict(s))` hash 相同、`rules` 也往返、RNG 位置往返（还原后下一次 `choice` 与原状态一致）。**确定性**：同 seed 两次 `digest()` 相同且逐回合提交序列相同、`subprocess` 跨进程 digest 与进程内相同、换 seed digest 不同、`fast_slow` 阵容跑完整局 `state.rng.calls == 0`（玩家 RNG 流与引擎流独立）。**交互式补位**：`resolve()` 暂停返回 `need_replacement`、`submit_replacement` 应用后回合推进、补位非法（非等待方/自己/已倒下/越界/错类型）一律拒绝且零状态变更、无存活后备 → 阵亡即终局不询问、`run_match` 驱动补位产生 replace 事件。**编排**：回合号连续、决胜回合在 `result.turns` 里、随机对局必然终止、`for seed in range(50)` 全部终止且不是全平局、脚本耗尽后回落 recharge、非法提交后 `pending_sides`/`turn`/`state_hash` 全不变、单边提交时 `resolve()` 拒绝且零状态变更、CLI 冒烟 |

**用户 Gate（E0b）**：亲自跑通上面五条命令，确认「两个随机策略真的自己打完了一局、应对三角三种都看得见、道具生效一次、阵亡补位由玩家/策略决定、事件流读得懂、同 seed 能复现」；浏览 `engine.py` 的 `resolve_turn` 确认阵亡即暂停、`end_turn` 是唯一的回合号推进点、`models.py` 里没有任何回合内字段。明确说"通过"→ 打 commit `feat: e0b turn engine with counter triangle`。

**验收记录**：✅ 2026-08-26 通过（负责人 Gate 确认）。

---

## Milestone E1 — 确定性闸门与黄金基线（⏭ 跳过）

**目标**：把 E0b 里**已被负责人认可**的行为冻死。此后任何一次行为变化都必须先被人看见、再被主动确认，绝不静默漂移。

> **负责人 2026-08-24 决定：跳过 E1。** 无黄金基线；后续里程碑的验收改为「测试全绿 + 逐条审阅事件流/变化」，不再用 baseline `--diff`。E1 的能力（确定性闸门）由 `test_environment_match.py` 的同 seed 复现 / 跨进程一致测试承担，只是不固化成 golden 文件。

**文件清单**：
| 路径 | 用途 |
|---|---|
| `scripts/record_env_baseline.py` | `--record` 录基线 / `--check` 比对（CI 用，退出码 0/1）/ `--diff` 打印差异供人审阅 |
| `tests/golden/e0_baseline.json` | 7 个固定场景的**完整事件流 + 逐回合 state_hash** |
| `tests/test_environment_golden.py` | golden 逐字节比对 + 三次 hash 序列一致（同进程 ×2 + 子进程 ×1）+ 换 seed 断言不同 |

**7 个场景**（每个都用 `ScriptedPlayer`，覆盖一条独立的规则路径）：mirror 平手硬币 / 应对三角三种各一 / 道具与换人同回合 / 增益叠满触顶 / tanky 平局 / 非法提交不消耗回合。

**关键签名**：
```python
SCENARIOS: dict[str, Callable[[], tuple[BattleSession, dict[str, Player]]]]

def run_scenario(name: str) -> dict:
    """跑一个场景，返回 {"events": [...], "hashes": [...], "digest": "..."}。
    必须用 ScriptedPlayer（不是 RandomPlayer）：基线要固定提交序列，
    这样"行为变了"与"策略变了"不会混在一起。"""
def main(argv=None) -> int: ...     # --record / --check / --diff
```

**验收命令（用户亲自运行）**：
```bash
uv run python scripts/record_env_baseline.py --record        # 首次生成基线，人工浏览一遍事件流
uv run python scripts/record_env_baseline.py --check         # 7 个场景与基线一致 ✅（退出码 0）
uv run pytest tests/test_environment_golden.py -q            # 含子进程确定性用例
```

**用户 Gate（E1）**：**先逐场景浏览 `e0_baseline.json` 的事件流**，确认每一条都是你认可的行为（这一步是 E1 的全部意义——冻结未被认可的行为等于给错误发通行证），再确认 `--check` 绿。明确说"通过"→ commit `feat: e1 determinism gate`。

**验收记录**：☐ 待验收。

---

## Milestone E2 — 属性克制与系别（STAB / 血脉系别语义）🔄

**目标**：让系别真正参与战斗。加两件事：克制表 + STAB 乘子（纯数据），并**澄清血脉语义**——
`types` 恒为精灵自身系别（克制/STAB 的依据），血脉系别**不改写** types、只决定可携带的血脉
技能是哪个系（规则 2）。原计划依赖 E1 的 baseline diff 审阅；**E1 已跳过**，验收改为「测试全绿 + 事件流里肉眼核对克制/STAB」。

**文件清单**：
| 路径 | 用途 |
|---|---|
| `scripts/build_type_chart.py` | 从 `mydocs/type_chart.md` 解析 Markdown 表格 → 生成 `CHART` 完整矩阵（转录一致性的来源） |
| `src/environment/types.py` | `CHART` 全矩阵（`CHART[防御方][攻击方]`，18×18）+ `normalize_type` + `type_effectiveness`（多系乘积封顶 ×3）+ `stab_multiplier` |
| `src/environment/damage.py`（改） | `compute_damage` 新增 `effectiveness` / `stab` 两个显式入参，乘进同一个表达式（**仍只取整一次**） |
| `src/environment/engine.py`（改） | `resolve_skill` 用 `type_effectiveness` / `stab_multiplier` 计算并传入，damage 事件带 `eff`/`stab` 字段 |
| `src/environment/teambuilder.py`（改） | `build_roster` 产出 `types = 精灵自身系别`（**不改写**；血脉只校验并进 spec，见规则 2） |
| `src/environment/__main__.py`（改） | damage 行渲染 `系别×N` / `本系×N` |
| `tests/test_environment_types.py` | **克制表对照 type_chart.md 原文（表格）解析** / 单系克制抵抗 / 自克自抗 / 互克对 / **多系克制封顶 ×3** / 多系抵抗乘算 / 一克制一抵抗互抵 / STAB / 血脉不改写系别 / 引擎 eff 封顶 |

> **2026-08-25 修正克制表**：原克制/抵抗关系有误，负责人已更新 `type_chart.md`，本里程碑按新表**重新转录**。
> ① 存**全矩阵** `CHART[防御][攻击]` 而非 SUPER/RESIST 两个方向——新表**不对称**（武 克 普通，普通 对 武 中性）；
> ② **多系别规则**：克制取乘积但**封顶 ×3**（火 打 草+虫 = 2×2 = 4 → 3），抵抗**正常乘算**（0.5×0.5 = 0.25），一克制一抵抗互抵（2×0.5 = 1）；
> ③ `scripts/build_type_chart.py` 从 md 生成 CHART，测试对照 md 原文逐条钉死转录一致性——旧表手抄错误正是本次返工原因。
>
> **2026-08-25 澄清血脉语义（负责人）**：血脉系别**不影响**精灵原系别——`types` 恒为自身系别，
> 才是影响克制与否的属性；血脉只决定携带的血脉技能是哪个系。原「血脉改写 types」实现已更正。
>
> **偏离原计划一处**：不改 `e0_skills.json`（E0 技能保持「普通」系）。克制/STAB 在 E0b 对战中依然可见——E0 精灵有自然系别（迪莫光/小火猴火/水蓝蓝水）；STAB 由「普通」系精灵（猫老大）+ 普通技能触发。改 E0 技能只会无谓震荡 E0b 测试，真实克制在 FULL 数据（E3 后）才完整。**新表 普通 系效果也变**（不再克 火/地/机械，改克 武、被 幽 抗）——E0 对战伤害数字随之变化，但确定性不变（同 seed 同 digest）。

**必须守住的一点**：`compute_damage` 的入参继续是**显式数值**（`counter_mult` / `reduction` / `effectiveness` / `stab`），不要让它去读 `TurnContext` 或 `state.types`。纯函数 + 显式入参是它能被表驱动单测的唯一原因，也是将来接修饰链时唯一不用改调用方的形状。

**验收命令（用户亲自运行）**：
```bash
uv run python scripts/build_type_chart.py --check          # 18×18 矩阵完整性
uv run pytest tests/test_environment_types.py -q           # 克制表一致性（对照 md）+ 倍率 + 封顶 + STAB + 血脉不改写系别
uv run python -m environment battle --seed 5               # 事件流正常（E0 全普通系技能，新表下 普通 对 E0 精灵系别全中性 → 无系别标记；克制数值由上述测试钉死）
uv run python -m environment battle --seed 5 --repeat 2 --quiet   # 同 seed 复现（确定性未破坏）
uv run pytest -q --cov=environment
```

**用户 Gate（E2）**：跑上面命令，确认克制/STAB 在事件流里肉眼可见、类型测试全绿、确定性未破坏。明确说"通过"→ commit `feat: e2 type chart and bloodline`。

**验收记录**：✅ 2026-08-26 通过（负责人 Gate 确认）。

---

## 先行交付：P1 技能效果池（batch-P1.json 125 技能 → 可对战）

**来源**：`mydocs/e0b-skill-effects-analysis.md` §8 P1 批次——纯伤害 / 纯防御 / 纯六维状态三类，
现有引擎**本来就能表达**，缺的是效果生成与多目标状态支持。

**交付内容**（2026-08-25）：
- **P1 效果编译器** `skillbook.compile_p1_effect`：把 desc 的固定模式编译成 `SkillEffect`——
  ① 纯伤害（`对敌方精灵造成物理/魔法伤害。`）→ `ATTACK`；② 纯防御（`减伤X%，应对攻击`）→ `DEFENSE`；
  ③ 纯六维状态（`自己获得/敌方获得 …`）→ `STATUS + stat_effects`（多目标/多维度/负层减益）。125/125 全部命中。
- **`SkillEffect.stat_effects`**：状态系从「单 stat 字段」扩到「(target, stat, mode, layers) 列表」；
  引擎 STATUS 分支逐条作用并带 `target` 字段发事件（E0 教学单条路径不变）。
- **可对战白名单** `battle_ready(name)` = 教学 `E0_EFFECTS` ∪ `P1_EFFECTS`（126 个：125 P1 + 抓挠）。
  `_skill_from_name` / `build_unit` 改查白名单 → **FULL roster 从此能进引擎对战**。
- **CLI**：`battle --data FULL --preset p1`（四家族真数据队）、`--data-report --data FULL --effects`（覆盖率）。

**四家族精灵池验证 E0~E2**（`tests/test_p1_integration.py`，12 条）：迪莫/喵喵/火花/水蓝蓝四家族
非首领 10 只组 P1 队 → E0a 校验（家族唯一/首领禁入/血脉系别）→ E0b 完整对局（伤害/补位/确定性/马尔可夫）
→ E2 克制/STAB（damage 事件 `eff`/`stab`，`STAB_MULT=1.25`）。`tests/test_p1_effects.py`（14 条）钉死编译器与白名单。

> **E0 教学数据与现有测试不动**：P1 是增量能力（双源共存），E0 基线 digest 逐字节不变。



## Milestone E3 — 真实数据对战（FULL 精灵 + valid_skills 白名单技能 + 管理员对局配置）✅ 已实现

**目标（2026-08-25 负责人重新定义）**：E3 的对局数据 = **全部精灵**（FULL 593 只，特性未实现的
装白板 `default` 上场）+ **已实装效果的技能**（P1∪P2 = 179，合并进 `valid_skills.json`，
格式与 `full_skills.json` 一致）。每只精灵可携带技能 **1–4 个**；对局规模与命数由**管理员接口**配置。

**文件清单**（已交付）：
| 路径 | 用途 |
|---|---|
| `scripts/build_valid_skills.py` + `data/valid_skills.json` | 从 full_skills 过滤 `battle_ready` → 179 条（格式同 full_skills，`--check` 校验） |
| `src/environment/dataset.py`（改） | 新增 `DataSource.VALID`：技能读 valid_skills.json、精灵同 FULL（593 只/家族/脏值） |
| `src/environment/teambuilder.py`（改） | VALID 可学池 = FULL 池 ∩ 白名单；家族/首领/血脉规则同样生效；非白名单技能给「效果未实装」文案；`build_roster` 增加 `rules` 参数 |
| `src/environment/rules.py`（改） | `skill_slots = 4`（1–4 技能） |
| `src/environment/battle_config.py`（新） | **管理员接口**：`validate_team_size`（3–6）/ `validate_lives`（1..team_size−1）/ `build_battle_rules`（非法 → ValueError，Web UI 捕获回显）。纯函数无全局状态 |
| `src/environment/__main__.py`（改） | `--data VALID`；battle `--team-size`/`--lives`（经管理员接口）；p1 预设规模自适应（跨家族候选池） |
| 测试 | `test_environment_dataset_valid.py`（5）/ `test_battle_config.py`（9）/ `test_environment_team_valid.py`（8）/ `test_environment_e3.py`（4，4v4/3命完整局 + 白板特性） |

**可对战白名单**：真实技能里 443/553 带中文效果描述，而本计划**不做效果引擎**。策略是全量加载，
但只把效果已实现（P1∪P2 白名单）的技能放进 `valid_skills.json`，其余登记为未支持并输出覆盖率
报告。这让"还有多少没实现"永远是一个**可见的数字**，而不是静默的 no-op。

**管理员接口（为 Web UI 准备）**：
```python
build_battle_rules(team_size=4, lives=3)   # → BattleRules(4v4/3命/4技能)；非法 → ValueError
validate_team_size(7)   # → "对局精灵数必须在 3–6 之间…"
validate_lives(4, 4)    # → "命数必须在 1–3 之间（且小于精灵数 4）…"
```
`BattleRules` 本身无约束（测试用 team_size=1/2 直构），约束只落在这层接口。

**验收命令（用户亲自运行）**：
```bash
uv run python scripts/build_valid_skills.py --check      # 179 / 全 battle_ready
uv run python -m environment --data-report --data VALID  # 179 技能 + 593 精灵（同 FULL）
uv run python -m environment battle --data VALID --preset p1 --seed 7 --team-size 4 --lives 3
uv run python -m environment battle --team-size 7        # ❌ 管理员接口拒绝（>6）
uv run python -m environment battle --lives 3 --team-size 3  # ❌ 拒绝（lives ≥ team_size）
uv run pytest tests/test_environment_e3.py -q
```

**用户 Gate（E3）**：确认真实数据能开局（任意 3v3~6v6）、`--data-report` 覆盖率可见、
管理员接口拒绝非法规模。明确说"通过"→ commit `feat: e3 real data battle with admin config`。

**验收记录**：✅ 2026-08-26 通过（负责人 Gate 确认）。

---

## Milestone E4 — 观测与信息隔离（迷雾）✅ 已交付（并入 E4+E5）

> **2026-08-26 状态**：已随 **E4+E5** 合并交付（2026-08-25/26，见 `mydocs/e4-e5-battle.md`）。
> 实际交付与本节蓝图的一处差异：**增减益可见**（E4 修正，2026-08-26 负责人拍板）——敌方单位也输出
> `stat_mods` / `energy_cost_mods`（双方阵营都有状态栏，`单位加成 * 层数`）；`visibility.py` 对
> `stat_change`/`switch` 不再剥字段。敌方系别可见（公开图鉴）。

**目标**：观测按 viewer 遮蔽。自己一方全见，对手只见"已揭示"的部分；事件按可见性等级过滤，且**未登记的事件类型 fail-closed 丢弃并告警**。这是自博弈有意义的前提——双方掌握相同信息的对局学不到侦察与试探。

**文件清单**：
| 路径 | 用途 |
|---|---|
| `src/environment/view.py` | `observe(state, viewer, mode) -> dict`；`mode` ∈ `global` / `partial`；`_mask_unit`：对手未揭示技能显示 `{"name":"???"}`、后备单位的 HP/能量置 `None`、iv/性格/血脉永不输出 |
| `src/environment/visibility.py` | `EVENT_VISIBILITY: dict[str, Visibility]`（`PUBLIC` / `OWNER_ONLY` / `GATED_BY_REVEAL`）+ `filter_events_for(viewer, events, state)` |
| `src/environment/models.py`（改） | `SideState.revealed: set[tuple[int, str]]`（队伍下标 → 已揭示技能名，整局保留）；`to_dict` 里 `sorted()` |
| `src/environment/session.py`（改） | `observe(side, mode="partial")`；对手的 `legal_actions` / `legal_items` 一律返回 `[]`（动作池本身就是隐藏信息） |
| `tests/test_environment_view.py` | 遮蔽正确性 + 揭示后可见 + **引擎发射的每个事件 type 都已登记**（反向也测：每个已登记 type 至少被一个测试发射过） |

一条 E0 遗留的注意点：`revealed` 是 `set`，必须在 `to_dict()` 里 `sorted()` 且能被 `from_dict()` 还原——否则 E0b 建立的马尔可夫性测试会当场变红。**这正是那条测试的价值**：它会在你写错的那一刻就告诉你，而不是等到 E6 回放轨迹时才发现迷雾状态丢了（参考项目就是后者，它的 `revealed_a/b` 被 `to_dict()` 直接排除）。

**验收命令（用户亲自运行）**：
```bash
uv run python -m environment battle --seed 7 --viewer a
# 对手在场单位可见 HP；对手后备单位 HP 为 null；未使用过的对手技能显示 ???
uv run pytest tests/test_environment_view.py -q
uv run pytest tests/test_environment_match.py -q         # 马尔可夫性用例必须仍绿
uv run python scripts/record_env_baseline.py --check     # **必须仍绿**：视图是只读的，零行为变更
```

**用户 Gate（E4）**：确认对手信息真的被遮住、马尔可夫性与 golden 都一字未变（若变了，说明视图层意外改了状态，必须查）。明确说"通过"→ commit `feat: e4 fog of war`。

**验收记录**：✅ 已交付（随 E4+E5，2026-08-25/26，见 `mydocs/e4-e5-battle.md`；`tests/test_environment_view.py` 16 条全绿）。

---

## Milestone E5 — LLM 对战玩家（⏭ 交付并入 E4+E5；真实 LLM 移至 E6.5）

> **2026-08-26 状态**：E5 已随 **E4+E5** 合并交付——测试期 LLM = **固定回复 + 随机动作的 FakeLLM**
> （`rock_pvp_agent/battle/player.py`），人类经 Web 战斗页与它对战（见 `mydocs/e4-e5-battle.md`）。
> 本节下面的 `LLMPlayer` / `arena_tools` / `facade` 设计是**真实 LLM 玩家的蓝图**，实现落在
> **E6.5**（接 E6 的自博弈编排）。验收命令与 Gate 一并移到 E6.5 节。

**目标**：LLM 当一方玩家出招；无 key 或调用失败时自动降级为随机策略，一局照样打完。

**关键架构决定：`LLMPlayer` 住在 agent 层，不住在 environment。** `Player` 是 `Protocol`（结构化子类型），所以 LLM 玩家完全可以放在 `src/rock_pvp_agent/battle/`，复用已有的 `llm.build_chat_llm`（含缓存与 `llm=` 注入缝）与 `agent.py` 的工具循环经验。依赖方向 `rock_pvp_agent → environment` 单向，铁律 1 天然成立，`environment` 保持零第三方依赖。参考项目在 `src/environment/` 里自带了一套 `llm.py` + `prompts.py`，于是长出了第二套 LLM 栈——本项目不重复这个错。

**文件清单**：
| 路径 | 用途 |
|---|---|
| `src/environment/arena_tools.py` | `build_side_tools(session, side)`：`battle_observe()` / `battle_act(action_type, payload, item)` 两个**实例级闭包**，`side` 锁在闭包里（模型无法操作对方）；返回纯 dict，不依赖 langchain |
| `src/rock_pvp_agent/battle/__init__.py` | 子包标记 |
| `src/rock_pvp_agent/battle/prompts.py` | `BATTLE_PLAYER_SYSTEM_PROMPT`：讲清应对三角与能量规则；每回合只输出一个 `battle_act` 调用，不输出对话文本 |
| `src/rock_pvp_agent/battle/player.py` | `LLMPlayer(side, *, llm, tools, max_retries=3)`：私有 history + 私有工具集；`battle_act` 调用被**拦截而非执行**（两侧提交必须同时结算）；非法提交把错误文本回灌 history 重试 ≤3 次 → 随机兜底；LLM 抛异常不重试，直接兜底 |
| `src/rock_pvp_agent/battle/facade.py` | `run_battle(a_kind, b_kind, *, seed)`：组队 + 构造 session + players + `run_match`；无 key 时 `llm=None` → `LLMPlayer` 全程走随机兜底 |
| `src/rock_pvp_agent/__main__.py`（改） | argparse 加 `battle` 子命令（`--a` / `--b` ∈ `llm`/`random`/`scripted`，`--seed`） |
| `tests/test_battle_player.py` | fake LLM（鸭子类型，零网络）：合法提交直通、非法重试后兜底、异常立即兜底、`battle_act` 未被真的执行、每个 `tool_use` 都回填了 `ToolMessage` |

**三个坑**：每个 `tool_use` 必须紧跟 `ToolMessage`，否则网关 400（`docs/extension-cookbook.md` §9，M1 踩过）——`battle_act` 被拦截时也要补一条合成的 `ToolMessage`；工具闭包里的全吞守卫必须带 `exc_info=True`、`DEBUG=1` 时不吞；`battle_act` 的 `payload` 里要能同时表达主动作与道具，建议 `action_type` + `payload`（槽位）+ `item` 三个显式参数，别把它们编码进一个字符串。

**验收命令（用户亲自运行）**：
```bash
uv run python -m rock_pvp_agent battle --a llm --b random --seed 7
# 逐回合打印双方提交与事件；末行 winner=…；LLM 侧偶发非法提交应看到"重试"提示而非崩溃
LLM_API_KEY= uv run python -m rock_pvp_agent battle --a llm --b random --seed 7   # 全程随机兜底，仍打完
uv run pytest tests/test_battle_player.py -q
```

**用户 Gate（E5）**：确认「大模型真的在决策 + 非法提交不死机 + 没 key 也能打完」。明确说"通过"→ commit `feat: e5 llm battle player`。

**验收记录**：✅ 测试期交付（FakeLLM + 人类对战页，随 E4+E5，2026-08-25/26）；真实 `LLMPlayer` 验收移到 **E6.5**（本节的验收命令在 E6.5 节执行）。

---

## Milestone E6 — 自博弈编排 + 轨迹落盘 + 重放复现（✅ 已交付）

**目标**：两个互相隔离的玩家自我对战，轨迹落盘，并能**由提交序列重放复现逐回合 `state_hash`**——
E0b 那条马尔可夫性不变式的回报。**负责人拍板（2026-08-26）**：E6 **暂用 FakeLLM**（与 E5 测试期一致），
真实 LLM 玩家移入 **E6.5**。E6 专注编排 + 落盘 + 引擎层重放能力本身。

> 实施细节见 `mydocs/e6-selfplay.md`（560 测试全绿，含新增 21：replay 5 / selfplay 16）。

**文件清单（已交付）**：
| 路径 | 用途 |
|---|---|
| `src/environment/match.py`（改） | `TurnRecord` 加 `replace_a` / `replace_b`（补位选择，重放的必需输入）；`run_match` 记录补位 + **4 处观测从全量 `observe()` 改迷雾 `view()`**（E4 白名单，E6.5 真实 LLM 接入即自动得正确口径） |
| `src/environment/replay.py`（新） | `replay_record(record) -> dict`：按 `(rules, roster spec, seed, 逐回合提交序列)` 重建全新 session，逐回合比 `state_hash`，首个失配即停；缺必需键 → ValueError。**引擎层单一实现**（E6 CLI / UI 端点 / 自博弈自检共用） |
| `src/environment/__main__.py`（改） | `replay <path>` 子命令：逐回合 `expected vs actual` 表，失配返回 1（可进 CI） |
| `src/rock_pvp_agent/battle/selfplay.py`（新） | `run_selfplay`：管理员规则 → p1 预设阵容 → `BattleSession` → `run_match` → lean 记录 → `TrajectoryStore.save` → **`replay_record` 现场自检**。`players=` / `roster_a/b=` 为测试注入缝（E6.5 接真实 LLMPlayer） |
| `src/rock_pvp_agent/battle/store.py`（新） | 轨迹存储：**只存 `(rules, 双方 roster spec, seed, 逐回合提交序列)` + `state_hash`**，不存整份状态；原子写（同目录临时文件 + `os.replace`）+ 追加式 `index.jsonl` + 坏行容错 + battle_id 防穿越 |
| `src/rock_pvp_agent/__main__.py`（改） | `selfplay` 子命令（`--games` / `--seed` / `--out` / `--a/--b fake_llm|random` / `--team-size` / `--lives` / `--max-turns`） |
| `src/ui/routes_battle.py`（改） | `/api/battle/replay` 归一后转交 `replay_record`——消灭第二份重放逻辑 |
| `tests/test_selfplay.py` + `tests/test_environment_replay.py`（新） | 打完/确定性/落盘保序/坏行/防穿越/**迷雾隔离 `_CapturePlayer` 钉死白名单**/run_match 记补位/篡改失配/battle_id 进 hash/CLI 冒烟 |

**为什么轨迹只存提交序列**：整份状态的体积会随后续里程碑涨一个数量级，而 `(配置, seed, 提交序列)` +
hash 序列已经足以复现与校验。前提两条——**引擎是 `(state, 双方提交) → state'` 的纯转移** + **玩家 RNG
流与引擎 RNG 流分离**——都在 E0b 就做到了，`replay.py` 因此只有几十行。`state_hash` 含 `battle_id`，
重放必须回传记录里的 battle_id（测试专门钉死）。

**偏离原文件清单两处（说明理由）**：① 不加 `TurnRecord.observation_a/b`（E6 存 lean、观测可经重放重算，
迷雾收口在 `run_match` 给 `view()`，而非落盘）；② 不做 `MatchResult.to_jsonl`（持久化归 `store.py` 一个口子）。

**验收命令（用户亲自运行）**：
```bash
uv run python -m rock_pvp_agent selfplay --games 2 --seed 7 --out runs/
#   game#1 seed=7 winner=a turns=20 rng_calls=0 replay=✅ runs/selfplay-7-1.json
#   game#2 seed=8 winner=a turns=20 rng_calls=0 replay=✅ runs/selfplay-8-2.json
#   runs/index.jsonl 新增 2 行（battle_id/胜者/回合数/玩家 kind）
uv run python -m environment replay runs/selfplay-7-1.json
#   逐回合 hash 与落盘完全一致 ✅，退出码 0
uv run python -m rock_pvp_agent selfplay --games 1 --seed 7 --out runs/   # 同 seed 再跑，轨迹逐字节一致（确定性）
uv run pytest -q                                  # 全绿（原 539 + 新增 21 = 560）
uv run pytest -q --cov=rock_pvp_agent --cov=environment      # 两个包都 ≥90%（实测 94% / 93%）
```

**用户 Gate（E6）**：跑通上面前三条命令，确认「两局自博弈完成、轨迹落盘 + index.jsonl、CLI 重放逐回合
hash 一致、传给玩家的观测是迷雾白名单（无绝对血量/隐藏字段）」。明确说"通过"→ commit `feat: e6 llm self-play with trajectories`，并考虑打 `v0.2.0`。

**验收记录**：✅ 2026-08-26 通过（负责人 Gate 确认；src 已并入 E 线整包提交，测试/文档不入库）。

---

## Milestone E6.5 — 真实 LLM 对战玩家（接 E6 自博弈）✅ 已交付

> **2026-08-26 状态**：已交付（585 测试全绿，含新增 25），实施细节见 `mydocs/e6-5-llm-player.md`。
> 与本节蓝图的三处差异：① 不做 `battle_observe` 工具（观测每回合新鲜传入，重读无意义，只留 `battle_act` 一个拦截工具）；
> ② **补堵一个泄露**——`run_match.on_turn_result` 原传原始事件流（含敌方绝对血量），改传 `filter_events_for` 过滤后的事件；
> ③ `arena_tools.py` / `facade.py` 未建（工具在 `battle/player.py`，编排复用 E6 的 `run_selfplay`，不另造门面）。

**目标**：真实 LLM 当一方出招，接进 E6 的 `run_selfplay`（`--a llm`）；无 key 或调用失败自动降级
FakeLLM/随机，一局照样打完。这是计划终点的最后一环。设计蓝图即上方 E5 节（`LLMPlayer` 住在
agent 层、`battle_act` 工具调用被拦截、`Player` Protocol 结构化子类型、工具错误回灌重试 ≤3 次、
`view()` 迷雾口径决策）。

**相对 E5 蓝图的关键更新（适配 E6 事实）**：
- 玩家端口 = `environment.players.Player` Protocol（E6 已用 `_CapturePlayer` 证明 `run_match` 只喂
  迷雾 `view()`）；`LLMPlayer` 实现 `decide`/`choose_replacement`/`on_match_start`/`on_turn_result`。
- **两路独立 LLM 实例**：`build_chat_llm(settings, tools)` 按 `(settings, 工具名集合)` 缓存——两侧工具名
  带 side 后缀（`battle_act_a` / `battle_act_b`）自然分键，不共用缓存实例；`llm=` 注入缝用于测试。
- 每侧私有 history（系统提示 + 渲染后的迷雾观测 + 自己的工具调用/结果），**绝不注入另一侧的观测**。
- `selfplay --a llm --b llm`：CLI 加 `llm` 选项，`run_selfplay(players=...)` 注入两个 LLMPlayer。

**验收命令（用户亲自运行）**：
```bash
uv run python -m rock_pvp_agent selfplay --a llm --b llm --games 1 --seed 7 --out runs/
# 逐回合打印双方提交；末行 winner=…；轨迹落盘 + replay=✅
LLM_API_KEY= uv run python -m rock_pvp_agent selfplay --a llm --b llm --games 1 --seed 7 --out runs/
# 无 key 全程降级 FakeLLM，仍打完
uv run pytest tests/test_battle_player.py tests/test_selfplay.py -q
uv run pytest -q --cov=rock_pvp_agent --cov=environment      # 两个包都 ≥90%
```

**用户 Gate（E6.5）**：确认「大模型真的在决策 + 非法提交不死机（重试提示）+ 没 key 也能打完 +
轨迹仍可重放」。明确说"通过"→ commit `feat: e6.5 real llm player`，并打 `v0.2.0`。

**验收记录**：✅ 2026-08-26 通过（负责人 Gate 确认；src 已并入 E 线整包提交，测试/文档不入库）。

---

## Milestone E7（可选）— 观战页（✅ 已交付）

**目标**：人类用户以**全局视角**观看两个 LLM 之间的对战过程，但两个 LLM 实际对战**仍遵循迷雾视角**
（己方全见、敌方只见白名单）。实施细节见 `mydocs/e7-spectate.md`（590 测试全绿，含新增 5）。

**关键结构**：
- **迷雾收口重构**：`run_match` 的回合循环抽成 `drive_turn(session, players)`（`environment/match.py`）——
  观测一律 `view()`、`on_turn_result` 事件一律 `filter_events_for`；`run_match` 与观战流共用，LLM 在任何路径下都拿不到全量信息。
- **观战流生成器** `run_spectate`（`battle/selfplay.py`）：逐回合驱动，帧协议
  `meta → state → turn* → done / error`；全局快照 `_global_view` = `observe(partial)["me"]` 取双方全量（绝对血量/全部技能/性格血脉 IV）。
- **SSE 端点** `GET /api/battle/stream`（`routes_battle.py`，**声明在 `/{battle_id}` 之前**）+ 观战页
  `GET /spectate` → `static/spectate.html` + `spectate.js`（EventSource，复用 battle 页样式）。
- 三个既有页面 top-nav 加 `👀 观战`。

**验收命令（用户亲自运行）**：
```bash
uv run python -m ui &
curl -N "http://127.0.0.1:8001/api/battle/stream?seed=7&a=llm&b=random"   # 连续 data: 帧，最后 event=done
# 浏览器 http://127.0.0.1:8001/spectate?seed=7&a=llm&b=llm 看完一局（全局视角；LLM 仍迷雾）
uv run pytest tests/test_selfplay.py tests/test_battle_ui.py -q
```

**用户 Gate（E7）**：浏览器看完一局——全局视角（绝对血量/全部技能/全量事件），且两个 LLM 的决策仍按迷雾。明确说"通过"→ 检查点 + 计划 doc 回填。

**验收记录**：✅ 2026-08-26 通过（负责人 Gate 确认）。

---

## 4. 人机协作与 Gate 约定

完全沿用 `docs/collaboration-protocol.md`，这里只写本子系统特有的三条补充。

**工作流循环**（不变）：
```
[助手讲解设计意图] → [负责人审批] → [助手逐文件编码] → [负责人亲自验证] → [复盘记录] → [负责人拍板下一里程碑]
   目标/为什么/         (可改范围        (绝不跨里程碑        (one-command-      (记坑/决策理由)   (继续/暂停/回退)
   文件地图/验收命令/    或技术决策)       连写)                to-verify)
   风险点
```

**补充一：E0 拆成 E0a / E0b 两个 Gate，各自内部再分子步骤。** E0a 是 3 个子步骤（数据与效果表 → 属性公式 → 组队与校验），E0b 是 5 个（规则与随机源 → 领域模型 → 动作与伤害 → 回合循环 → 跑通一局）。助手按子步骤讲设计意图 + 写代码，每步跑一次该步的自检；Gate 落在每个里程碑最后。若中途想停，停在任意子步骤边界都是干净的。

**补充二：E1 / E2 的 Gate 是"读 diff"，不是"看绿灯"。** 这两个里程碑要求负责人**逐条浏览基线事件流 / 差异输出**并认可每一处。这是本计划里唯一两处"看输出不够，必须看内容"的 Gate。

**补充三：检查点写进 `docs/checkpoints/checkpoint-E0a.md`…**（`docs/` 入库，`mydocs/` 不入库），模板见 `docs/collaboration-protocol.md` §3。每个 Gate 通过后、打 commit 前完成。

**随时可回退**：每个里程碑开始前打 commit；`git log --oneline` 找点，`git checkout .` / `git reset --hard <commit>` 一键回退。E 线与 M 线互不干扰，回退 E 线不影响 chat mode。

---

## 5. 最终文件结构（E6.5 完成态）

```
MySelfPlayAgent/
├─ pyproject.toml                        # 改：wheel packages += "src/environment"
├─ src/rock_pvp_agent/
│  ├─ __init__.py  __main__.py(改)  config.py  prompts.py  llm.py  tools.py  agent.py
│  ├─ battle/  __init__.py  player.py  selfplay.py  store.py  prompts.py   # E5/E6/E6.5 ✅
│  │                    （player.py 含 FakeLLMPlayer + LLMPlayer）          # E6.5
│  └─ ui/      __init__.py  __main__.py  server.py  context.py  battle.py
│              routes_battle.py  routes_team.py  static/…                          # E4+E5/E7
├─ src/environment/                      # 新包，零第三方依赖
│  ├─ __init__.py  rules.py  __main__.py                                              # E0a/E0b
│  ├─ dataset.py  skillbook.py  statline.py  teambuilder.py  data/e0_*.json           # E0a
│  ├─ rng.py  models.py  events.py  actions.py  damage.py  engine.py                  # E0b
│  ├─ session.py  players.py  match.py                                                # E0b
│  ├─ types.py  data/skills.json  data/spirits.json                                   # E2/E3
│  ├─ view.py  visibility.py                                                          # E4
│  └─ replay.py                                                                       # E6
├─ scripts/record_env_baseline.py                                                      # E1
├─ tests/
│  ├─ conftest.py  fakes.py  test_smoke.py  test_llm.py  test_agent.py  …             # 既有
│  ├─ test_environment_dataset.py  test_environment_team.py                            # E0a
│  ├─ rosters.py  test_environment_{actions,engine,match}.py                           # E0b
│  ├─ test_environment_{types,view}.py                                                 # E2/E4
│  ├─ test_selfplay.py  test_environment_replay.py  test_battle_fake_llm.py            # E5/E6
│  └─ test_battle_player.py（E6.5）                                                    # E6.5
├─ docs/checkpoints/checkpoint-E0a..E6.md                                               # 入库
└─ mydocs/environment-plan.md                                                          # 本文件（gitignored）
```

---

## 6. 为后续预留的九条缝

E0 里有九处看起来"多余"的间接层。它们不是过度设计——每一条都对应参考项目后来不得不返工的一处。**这九条必须在 E0 就位**，其余抽象一概延后。

| # | 缝 | 现在长什么样 | 将来接什么 |
|---|---|---|---|
| 1 | 伤害与回复漏斗 | `compute_damage` + `apply_hp_loss` + `apply_heal`，`current_hp` 的唯一写者 | 减伤链、"受致命伤时保留 1 生命"、吸血、on-damaged 钩子、前瞻预测 |
| 2 | 数值常量表 | `BattleRules` frozen dataclass，构造一次挂在 state 上 | 所有新规则常量；**永不按调用逐层传参** |
| 3 | 显式效果表 | `skillbook.E0_EFFECTS`，逐技能手写；引擎永不读 `desc` | 553 条时换成 desc→DSL 编译器的产物，`SkillEffect` 的消费方一行不改 |
| 4 | 纯转移语义 | `to_dict` / `from_dict` / `clone` / `step`，加一条马尔可夫性测试 | E1 的确定性闸门、E6 的轨迹重放、将来的 MCTS 前瞻 |
| 5 | 局部 `TurnContext` | 回合内派生量一律局部，`end_of_turn()` 是 `pass` | 回合末状态 tick / 冷却递减挂进 `end_of_turn`；回合内新机制挂进 `TurnContext`，两边永不混 |
| 6 | 离散增益层 | `StatModifier(stat, mode, layers, permanent)` + `aggregate_stats` 派生视图 | 驱散 N 层 / 层数翻倍 / 增益交换 / 整体转移——求和字典做不到其中任何一个 |
| 7 | 单一门控谓词 | `skill_block_reason` 被 `legal_actions` 与 `validate_decision` 共用 | 冷却、禁足、眩晕、蓄力锁、号位锁——全部只加进这一个函数 |
| 8 | 真优先级队列 | `entry_priority` + `build_queue` + `process_queue` 分离，最多 4 条 | 应对改写出手顺序、引擎自动插入的动作、追加行动——扩条目而不改回合循环 |
| 9 | 策略端口 | `Player` Protocol + `ScriptedPlayer` + `RandomPlayer`，`decide` 显式收合法池 | E5 的 `LLMPlayer` 只是第三个实现；引擎在 LLM 出现前就已可完整测试 |

一条元原则，来自参考项目 M0a 的做法：**先把扩展点以空实现落地，并用逐字节相同的回放证明行为没变，再往里填内容。** 但只对上面九条这么做——参考项目的 `SideState` / `FieldState` 空了三个里程碑，那种"预留"是纯负债。

---

## 7. 后续扩展路线图（明确排除的部分）

本计划**不做**下面这些。它们不是"以后一定要做"，而是"想做的时候有路可循"。

| 参考项目的复杂度 | 规模 | 为什么排除 | 本计划留的缝 |
|---|---|---|---|
| 中文 desc → DSL 规则编译器（lexer + 子句规则表 + 两个 compiler） | ~2700 行 | 这是"给 553 条中文技能描述写一个编译器"，是独立产品而非战斗内核。它只有在技能数远超手写能力时才划得来——14 条手写，553 条才需要它 | 缝 3：`SkillEffect` 是编译器将来的输出形状，消费方零改动；E3 输出「可直接对战」覆盖率报告，把缺口变成可见数字 |
| 效果执行器 + 约 50 个 handler + 效果注册表 | ~2100 行 | DSL 的下游。只有在效果种类超过约 20–30 种时才划得来；低于这个数，`resolve_skill` 里三个分支更清楚 | `resolve_skill` 保持单一函数，将来在此处分叉到注册表 |
| 层叠状态（中毒 / 灼烧 / 冻结）/ 印记 / 回合末 tick 链 | ~400 行 | 每个都会新增一条"回合末结算 + 终局判定 + 回合推进"路径——正是参考项目三处 `turn += 1` 的来源（`tick.py` 就是第三处） | 缝 5：一律挂进 `end_of_turn()` 内部，回合推进仍只有一处 |
| 事件总线 + `Timing` 枚举 + 227 个精灵特性 | ~580 行 | 引入 Observable 通道后，事件顺序不再能从 `engine.py` 单文件读出来 | 事件已是统一 dict；订阅者为空时 emit 是零成本的，将来加 bus 约 9 行 |
| 修饰链（伤害 / 致命性 / 能耗 / 连击数 / tick 次数 / 系别 / 回复方向，共 10 条） | ~150 行 | 只有一条伤害公式时，链是纯抽象开销——参考项目自己的测试都写着"M0a 链空 → 恒等"。它真正的价值是"特性能重写引擎规则"，而那需要先有特性 | 缝 1 的三个唯一入口 + `compute_damage` 的显式乘子入参，就是链将来的挂点 |
| 永久修改层（技能实例的 `power_perm_delta` / 使用计数 / 冷却） | ~200 行 | 需要 `SkillDef` / `SkillInstance` 的 Flyweight 拆分 | `Skill` 已是 frozen 的共享定义；`StatModifier.permanent` 已经在区分永久与否 |
| `选择：A或B` 分支 / 应对改写出手顺序 / 打断 / 多段连击 | ~600 行 | 与自博弈的核心目标无关；E0 的 14 个技能都用不到 | 缝 8：队列已是真队列；`Decision` 加一个 `choice` 字段即可，线协议不变 |
| 天气 / 蓄力 / 巧变 | ~300 行 | 同上 | 缝 5 与缝 2 |
| 第二种以上的道具、道具作为独立动作 | — | 一种道具已经把"附赠动作通道"这条结构验证完了；加第二种是配表工作，不是设计工作 | `E0_ITEMS` 是一张表，`resolve_item` 按道具名分派 |
| async 对局会话层 + 对局注册表（TTL / 并发上限） | ~400 行 | 属于 web 层（E7），不属于引擎 | `BattleSession` 显式声明非线程安全，并发由 web 层持锁 |

想做的时候，参考项目的推进顺序是可信的路线图：**`M0a → M0b → M1 → M2 → M7a → M3 → M4 → M5 → M6 → M7b`**（注意文件名顺序不等于执行顺序，`M7a` 必须早于 `M5`，因为 M5 的特性有 13.7% 要读技能装配、14.1% 要读场下精灵）。权威设计文档：`~/workspace/SelfPlayAgent/docs/Environment_upgrade_plan.md`；各里程碑的交付记录：`~/workspace/SelfPlayAgent/src/environment/M*_milestone.md`。

---

## 8. 风险与常见坑

下表每一条都是参考项目**实测踩过**的缺陷，不是推测。左列是坑，中列是它在参考项目里的实证，右列是本计划的结构性对策——注意都是"让缺陷在结构上不可能发生"，而不是"记得别写错"。

| 坑 | 参考项目的实证 | 本计划的对策 | 抓它的测试 |
|---|---|---|---|
| 回合推进有三处 | `engine.py` 终局分支 / 常规分支 / `tick.py` 各推进一次 | 只在统一收尾处一次；`process_queue` 只置 `winner`/`done`，不清理不推进 | 源码扫描 `state.turn += 1` 只出现一次；决胜回合也 +1 |
| 回合内临时量住进状态 | `_defense_skill_a/b` 是状态字段，`end_of_turn_cleanup` 手工清 8 个字段 + 两个 for 循环；漏一个就跨回合泄漏 | 回合内派生量全在局部 `TurnContext`，随函数返回消失；`end_of_turn()` 是 `pass` | 马尔可夫性用例；源码扫描 `models.py` 里不出现 `TurnContext` |
| 无法从快照恢复 | 全项目 `to_dict()` 有 11 处，`from_dict` **只有 1 处**且不是状态的——"从快照回放"这件事从未被验证 | `from_dict` 与 `to_dict` 成对交付，`clone()` 直接复用它们 | `from_dict(to_dict(s))` 后同一回合事件流逐字节相同 |
| `rng is None` 静默回退全局 `random` | `models.py` 的 `next_rng()` 有 `else: return random`——手工构造的 state 不可复现，而手工构造正是写测试的主要方式 | `BattleRng(seed)` 是 `BattleState` 的必填字段，无 None 分支；只暴露 `choice()` 且每次计数 | 同 seed 两次同序；`asym` 阵容跑完整局 `rng.calls == 0`；跨进程 digest |
| 队列里用 `break` 而不是 `continue` | `engine.py:1019`；参考项目队列恒为 2 条所以缺陷潜伏，第 3 条动作出现当天爆炸——**本项目 E0b 的队列已经是 4 条**，这个坑当场就会踩 | 写 `continue` + 发 `skipped`；`build_queue` 与 `process_queue` 分离，测试能直接喂手造队列 | 手造**三条**队列，中间那条 actor 已阵亡，断言第三条仍结算 |
| 回合内标记建立太晚 | 必须在建队列之前建防御标记，否则后手防御方无法为先手攻击减伤 | `build_turn_context` 是回合循环第 ① 步，位置写死；减伤是它的返回值而不是状态字段 | "慢速方防御、先手方伤害已减" |
| `0.0 is falsy` | `float(skill.get("power", 30) or 30)`：防御技能 `strong: null` → `power=0.0` → 走 `or 30` → 52 个技能的 50%~100% 减伤全部失效。**本项目的 9 个防御/状态技能 `strong` 全是 `"0"`，第一天就会撞上** | 硬规则：数值字段**绝不写 `x or default`**，只写 `default if x is None else x`；减伤比例是 `SkillEffect.reduction_pct` 一等字段，由效果表填入，引擎不认识正则 | `--data-report` 打印 `防御.power = 0`；测试断言不等于 30 |
| 零能量白拿减伤 | `setup_defense_marks` 从不检查能量；根因是"能量不足"有两套行为 | 一个条件一套行为：付不起 = 动作非法，压根进不到武装阶段 | "能量不足是非法而非降级"且不产生 recharge 事件 |
| 增益做成求和字典 | 参考项目自己的结论：驱散按层/按种、翻倍、交换、整体转移在求和字典上**一个都做不到** | `StatModifier` 是离散层对象；`aggregate_stats` 只是派生视图，**绝不写回 `unit.stats`** | 离场清除非永久层后，`aggregate_stats` 回到基线值 |
| 轨迹丢掉决胜回合 | 终局提前 return、不推进回合 → 回合号与上一回合相同 → 上层去重守卫把决胜回合整条丢掉 | 终局也走统一收尾；`TurnRecord.turn` 取提交时的回合号，于是 `[t.turn] == range(1, n+1)` | 决胜回合在 `result.turns` 里 + 回合号连续 |
| `set` 混进序列化 | `revealed_a/b` 是 `set` 且被 `to_dict()` 排除，从快照回放**已经**丢迷雾状态；`state_hash` 用 `default=str`，set 会按 `repr` 计入哈希，而 str 哈希带 `PYTHONHASHSEED` 盐 → 跨进程不一致且**不报错** | `to_dict()` 只含 JSON 原生类型，`json.dumps` **不传 `default=`**；一切遍历走 `SIDES` 元组；要序列化集合就 `sorted()` | 无 `default=` 也能 dumps；跨进程 digest 一致；E4 引入 `revealed` 时马尔可夫性用例会立刻抓住漏写 |
| 同一上限两个事实源 | 减伤上限一处读配置常量、另一处硬编码 `min(0.9, …)` | 所有数值常量只住在 `BattleRules`（含 `stat_layer_cap` / `item_priority` / `item_before_main_action`） | 改 rules 的字段，两条路径同步变化 |
| 事件同时带 `team` 和 `side` | 每条事件都写两遍同义字段 | 只有 `side`，由 `ev()` 唯一构造器保证存在 | 每条事件都有 `type`/`side` 且 `type in EVENT_TYPES` |
| 阵亡重复扣命 | `check_fainted_and_deduct_mp` 每次调用只要 active 是 fainted 就扣命；靠"紧接着 check_winner 结束对局"才没出事，是隐式耦合 | 保持"每次 `settle_faints` 后立刻 `check_winner`"的不变式并写进 docstring；不加 `life_counted` 字段（E0 用不到的状态就是死重量） | 连续两次 `settle_faints`，命数只掉 1 |
| 效果参数从错误的字段反推 | 减伤比例从 `power` 反推、`re.search(r"(\d+)%")` 写进了 `engine.py` | 效果参数**只**从 `skillbook.E0_EFFECTS` 读；引擎里出现正则就是设计事故 | 源码扫描 `engine.py` / `damage.py` 里不出现 `re.` |
| 测试 monkeypatch 模块级 `random` | `engine.py` 里留着 `import random  # noqa` 专供测试 patch，把测试钉在实现细节上 | 测试永不 patch 全局；随机性只经 `BattleRng` 注入，靠**换 seed** 断言两种顺序 | 换 seed 得到两种先手顺序 |
| `[spec] * n` 造 roster | 参考测试里的 `[{...}] * 6`：6 个引用指向同一个 dict，任何一处 mutate 就全队串味 | `tests/rosters.py` 的 `team()` 一律用推导式逐个构造 | `test_roster_helper_returns_distinct_dicts` |
| `_guard` 全吞异常 | `except Exception → logger.warning + default` 把每个引擎 bug 变成一次静默的错误答案（真实堆栈只在 warning 里） | E0/E1 引擎层不吞异常；「宁失败不抛」只在 E5 的工具闭包边界生效，且带 `exc_info=True`、`DEBUG` 时 `raise` | E5 的"工具异常被吞但日志有堆栈" |

**另外两条本项目特有的约束**：

- **Python 3.10 兼容**：不用 `StrEnum`、不用 `tomllib`；每个文件写 `from __future__ import annotations` + PEP 604 联合类型（`int | None`）。
- **`environment` 永不 import `rock_pvp_agent`**（铁律 1）。E5 的 `LLMPlayer` 因此住在 agent 层——靠 `Player` 是 `Protocol`（结构化子类型）跨过这条线，而不是靠反向依赖。

---

*关联：本计划是 `mydocs/rebuild-plan.md` §2 扩展表里「引入游戏数据域 → 新增 `src/environment/` 同级包」与「加新模式（battle）」两行的正式落地方案；协作方法论见 `docs/collaboration-protocol.md`；改哪里的速查见 `docs/extension-cookbook.md` §4 / §5。*
