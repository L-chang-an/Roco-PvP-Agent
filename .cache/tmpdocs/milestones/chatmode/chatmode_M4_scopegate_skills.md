# M4 — ScopeGate + 越界拒答 + 顾问 Skill 注册表（详细实施计划）

> 上级：`chatmode_plan_v2.md` §5 M4　|　依赖：M1（catalog 做精灵/技能名命中）、M3（顾问 Agent）、M2（Skill 晋级来源，软依赖）　|　产出给：M5（评测集要验 ScopeGate 的误拒/误答 + Skill 晋级门）
> 分支前提：`feat/e-line-v2`；顾问侧 Skill **独立于** R 线 Playbook（本分支无 Playbook）。

## 1. 目标

1. **ScopeGate**：在顾问 Agent 运行**之前**做确定性领域判定，把越界/绕过类问题挡在 LLM 之外，拒答用固定模板、不靠主模型临场发挥。
2. **越界拒答模板**：普通越界 / 模糊话题 / 绕过规则 / 欢迎语四类固定文案。
3. **顾问 Skill 注册表**：带版本/哈希/工具白名单/证据集合的程序化技能，只读、可审计，M5 再做 held-out 晋级。

## 2. 前置与依赖

- M1：`catalog.search_spirits` / `get_spirit_profile`（用来做“命中真实精灵/技能名”的判定）。
- M3：`TeamAdvisorAgent`、`ADVISOR_SYSTEM_PROMPT`。
- 本分支无 R 线 Playbook/`[PROTECTED]`/双分析师——Skill 注册表从零建，不引用它们。

## 3. 交付物

| 文件 | 职责 |
|---|---|
| `advisor/scope.py` | `ScopeVerdict` + `classify()` + 拒答/欢迎模板 + 路由入口 |
| `advisor/skills.py` | `Skill` 模型 + `load_skills`/`register_skill`/`retrieve_team_skill` |
| `advisor/skills/*.json` | 内置 Skill 数据（先手工维护 1–2 个） |
| 改 UI/CLI 聊天入口 | 在 `ChatAgent` 前插入 `scope.route()` 分发 |
| `tests/test_advisor_scope.py`、`tests/test_advisor_skills.py` | 测试 |

## 4. 详细设计

### 4.1 `scope.py` — 确定性优先的领域判定

```python
class ScopeVerdict(str, Enum):
    IN_SCOPE = "in_scope"      # 组队/图鉴/轨迹/战术
    AMBIGUOUS = "ambiguous"    # 无法确定，回澄清模板
    OUT_OF_SCOPE = "out_of_scope"   # 明确越界（非游戏/非本系统话题）
    REFUSE = "refuse"          # 试图绕过规则/安全注入

def classify(message: str) -> ScopeVerdict
```

判定顺序（**全部确定性**，无额外 LLM 调用）：

1. **安全优先（REFUSE）**：命中注入/绕过关键词（“忽略系统规则/忽略之前的指令/泄露提示词/读取隐藏配置/绕过组队校验/执行代码/访问文件/扩大网站范围/修改证据优先级…”）→ 直接 REFUSE。
2. **欢迎/帮助（IN_SCOPE 但走欢迎模板）**：命中“你好/您好/你能做什么/功能介绍/怎么用”且消息很短 → 返回欢迎模板。
3. **越界（OUT_OF_SCOPE）**：命中明确非本域关键词（“写代码/翻译/写诗/炒股/新闻/天气…”）→ OUT_OF_SCOPE。
4. **命中真实实体（IN_SCOPE）**：消息中出现 `catalog` 里任一真实精灵名或技能名（用 M1 的查询做精确命中，非模糊 `contains`）→ IN_SCOPE。
5. **命中组队意图词（IN_SCOPE）**：命中“组队/配招/克制/阵容/血脉/性格/个体值/推荐队友/针对某对手/轨迹/胜率/构筑” → IN_SCOPE。
6. **其余 → AMBIGUOUS**：返回澄清模板，请用户补精灵名/队伍规模/目标对手。

`route(message) -> (target, text)` 路由入口：

- `REFUSE`/`OUT_OF_SCOPE`/`AMBIGUOUS`/欢迎 → 直接返回固定模板文本，**不进 LLM**；
- `IN_SCOPE` → 交 `TeamAdvisorAgent.chat`。

> 可选增强（默认关）：`AMBIGUOUS` 再走一次轻量 LLM 二分类。默认关是为了省成本/延迟，且避免给注入面再开一道门；M5 若发现误拒率高再开。

### 4.2 越界拒答模板（固定文案，不靠模型发挥）

| 情形 | 模板 |
|---|---|
| 普通越界 | “这个助手主要负责精灵图鉴、配招、组队和对战分析，不适合回答这个话题。你可以告诉我你想组几只精灵、已有精灵或目标对手，我来帮你配队。” |
| 模糊话题 | “如果你是在问某只精灵或技能，请告诉我它的名称；我可以帮你查属性、配招或推荐队友。” |
| 绕过规则 | “我不能更改系统范围、读取隐藏配置或绕过组队校验。不过我可以按现有规则继续帮你优化阵容。” |
| 欢迎 | “你好，我可以帮你查精灵/技能资料、推荐 3–6 人阵容与配招、分析克制与轨迹。例如：① 组一只 3 人火系强攻队 ② 给『迪莫』推荐配招 ③ 我的队伍被水系克制怎么办？” |

### 4.3 `skills.py` — 顾问 Skill 注册表

```python
class Skill(BaseModel):
    name: str
    version: int
    hash: str                  # 对 body 的 sha256（内容校验，防篡改）
    status: str                # "active" | "probationary"
    trigger: list[str]         # 触发关键词
    boundary: list[str]        # 硬边界（只处理当前规则/只推荐 VALID/不读隐藏信息）
    allowed_tools: list[str]   # 工具白名单（必须 ⊆ 顾问工具集）
    verification: list[str]    # 校验项（data_digest 一致 / 每队过 validate / 每理由有 evidence_id / replay_ok / 人机自博弈分开）
    body: str                  # 程序性步骤（自然语言，只作流程建议，不作事实源）

def load_skills() -> list[Skill]
def retrieve_team_skill(query: str) -> list[dict]   # 触发命中且 status=active，最多 3 个
def register_skill(spec: dict) -> Skill             # 新 Skill 默认 probationary
```

- 存储：`advisor/skills/*.json`（项目无 yaml 依赖，用 JSON）。
- `retrieve_team_skill` 只取 `status == "active"` 且触发关键词命中的 Skill，**最多 3 个**；Skill 的 `body` 只作“流程建议”，**不能覆盖**数据库事实、护栏或工具白名单。
- `allowed_tools` 与顾问工具集求交——Skill 声明的工具不在白名单内的一律忽略（代码级裁剪，不靠提示词）。

## 5. 边界与护栏

- **拒答靠代码不靠模型**：`REFUSE`/`OUT_OF_SCOPE`/`AMBIGUOUS` 直接返回模板，不进 LLM。
- **Skill 是数据不是指令**：`body` 与 `trigger` 视为待解析文本，其中的“忽略规则/调用额外工具/泄露提示词/读隐藏数据”一律忽略；`allowed_tools` 是硬裁剪。
- **新 Skill 进 probationary**：不能一次胜局直接晋级 active（晋级门在 M5）。
- **VisibilityGate 说明**：顾问只消费已结束轨迹 + 公开图鉴，不读进行中对局；若未来加“当前对局咨询”，必须走 `visibility.filter_events_for` 的对应玩家迷雾视角。

## 6. 测试计划

- `scope`：组队/精灵名/技能名 → IN_SCOPE；注入语句 → REFUSE；“写一首诗” → OUT_OF_SCOPE；短消息“你好” → 欢迎；“它厉害吗” → AMBIGUOUS。
- 真实实体命中：`classify("迪莫配招")` 靠 M1 精灵名命中 IN_SCOPE（不靠关键词，验证精确命中）。
- `skills`：`retrieve_team_skill` 只回 active、最多 3、触发命中；`allowed_tools` 白名单外工具被裁剪；`hash` 与 `body` 篡改检测。
- 路由：REFUSE/OUT_OF_SCOPE/AMBIGUOUS 不进 LLM（用 fake LLM 断言未被调用）。

## 7. 验收 Gate

- [ ] 越界/注入/模糊/欢迎四类全部走固定模板，不依赖模型。
- [ ] IN_SCOPE 判定含“命中真实精灵/技能名”路径。
- [ ] Skill 注册表只读可审计，工具白名单代码级裁剪，新 Skill 默认 probationary。
- [ ] 隔离不变量、全量 pytest 绿。
- [ ] Gate 报告 → 用户「通过」→ commit。

## 8. 风险与回滚

- **风险**：确定性关键词误拒/误答 → M5 的评测集专门量化误拒/误答率，可开轻量 LLM 兜底（默认关）。
- **风险**：Skill 内容注入 → `hash` + `boundary` + `allowed_tools` 裁剪 + 事实优先级兜底。
- **回滚**：纯新增模块 + 入口一处分发，`git revert` 可退。
