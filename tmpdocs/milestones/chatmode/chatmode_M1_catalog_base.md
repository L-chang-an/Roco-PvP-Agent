# M1 — 只读底座：数据指纹 + catalog 查询 DSL + validate 硬闸（详细实施计划）

> 上级：`chatmode_plan_v2.md` §5 M1　|　依赖：无（本线起点）　|　产出给：M2（`data_digest`）、M3（catalog/validate）、M6（web）
> 分支前提：在 `feat/e-line-v2` 上做；**不**依赖 `main` 的 R 线（`battle/evolution/`、`evaluate.py`、`data_digest` 均不存在于本分支）。

## 1. 目标

在 `src/rock_pvp_agent/advisor/` 新建顾问包，落地三件“纯确定性、可单测”的事：

1. **数据指纹**（`fingerprint.py`）：给“当前数据/规则版本”一个可复现的摘要，作为 M2 的 VersionGate、M3 的回答引用的唯一锚点。
2. **catalog 查询工具**（`catalog.py`）：用白名单查询 DSL 让 Agent 只读**引擎归一化后的**数据，不给 `exec`/`grep`/任意文件。
3. **validate 硬闸**（`validate.py`）：把 `teambuilder.validate_team` 包装成结构化错误码，成为 LegalityGate 的代码事实。

本里程碑**不**引入 LLM 决策、不引入网络、不修改 `environment`（只消费它）。

## 2. 前置与依赖

- `environment.dataset`（已存在）：`load_spirits/load_skills/load_families/load_evolution_chains/load_skipped_spirits`、`DataSource{FULL,VALID}`、`RawSpirit/RawSkill`。
- `environment.teambuilder`（已存在）：`TeamPick`、`learnable_skills`、`validate_team`。
- `environment.rules`（已存在）：`BattleRules`（frozen dataclass，16 字段）、`DEFAULT_RULES`。
- 数据文件（已存在）：`src/environment/data/{full_spirits,full_skills,valid_skills,families,evolution_chains}.json`。

## 3. 交付物

新增包（全部在 `src/rock_pvp_agent/advisor/`）：

| 文件 | 职责 |
|---|---|
| `__init__.py` | 包导出 |
| `fingerprint.py` | `data_digest()` / `rules_digest()` |
| `catalog.py` | `get_catalog_version` / `search_spirits`（DSL）/ `get_spirit_profile` / `get_skill_profile` / `get_build_options` + DSL 白名单分发表 |
| `validate.py` | `validate_team` 结构化封装 + 错误码映射 |

测试：`tests/test_advisor_fingerprint.py`、`tests/test_advisor_catalog.py`、`tests/test_advisor_validate.py`。

## 4. 详细设计

### 4.1 `fingerprint.py` — 数据指纹

```python
DATA_FINGERPRINT_FILES = (
    "full_spirits.json", "full_skills.json", "valid_skills.json",
    "families.json", "evolution_chains.json",
)  # 固定顺序

def data_digest() -> str:
    """对 5 个数据文件字节串按固定顺序取 sha256（64 位十六进制）。
    数据文件任何字节变化 → digest 变化；未变 → 稳定。纯确定性、无 IO 依赖外部。"""

def rules_digest() -> str:
    """对 BattleRules 的 16 个字段按 dataclasses.fields 顺序做稳定序列化再 sha256。
    复用 selfplay._rules_dict 的“字段名→值”模式，但键序固定为字段声明序。"""
```

实现要点：

- 读文件用 `Path.read_bytes()`，`DATA_DIR` 复用 `environment.dataset.DATA_DIR`（或从 `dataset` 的 `_FILE` 常量取路径，避免硬编码重复）。
- `rules_digest` 用 `dataclasses.fields(BattleRules)` 枚举字段名（[selfplay.py:38](src/rock_pvp_agent/battle/selfplay.py#L38) 已有同款写法），值需把 `float` 规整为 `repr`（`0.1` 的浮点表示稳定即可，不跨 Python 版本要求）。
- **缓存**：`functools.lru_cache(maxsize=1)`——指纹在一进程内不变，避免每次工具调用重读 1MB+ 文件。
- **不做**“内容语义归一化”的指纹（那需要解析 JSON），字节级哈希已足够；数据文件重排但内容不变会变指纹，这是**可接受且偏保守**的行为（宁可多隔离，不冒险合并）。

### 4.2 `catalog.py` — 白名单查询 DSL + catalog 工具

#### 4.2.1 白名单执行层（核心安全边界）

```python
# SpiritFilter 是 DSL 的原子；每个 field 只能走一张已审计函数表。
@dataclass(frozen=True)
class SpiritFilter:
    field: str   # ∈ {name, type, trait, learnable_skill, family, is_boss, number}
    op: str      # ∈ {eq, in, contains}
    value: str | list[str] | bool

_FIELD_DISPATCH = {
    "name":             lambda sp, v: sp.name,            # dataset.RawSpirit 字段
    "type":             lambda sp, v: sp.types,           # tuple[str,...]
    "trait":            lambda sp, v: sp.trait_name,
    "is_boss":          lambda sp, v: sp.is_boss,
    "number":           lambda sp, v: sp.number,
    "learnable_skill":  ...,   # 走 teambuilder.learnable_skills(name, bloodline, source)
    "family":           ...,   # 走 dataset.load_families(source) 反查 name → family_key
}
```

- **表外一律拒绝**：`field` 不在 `_FIELD_DISPATCH`、`op` 不在允许集合、`value` 类型不符 → 抛 `QueryNotAllowed`（工具层转成错误文本回给模型）。
- **没有** `eval`/`exec`/`__import__`/`open`/`subprocess`/网络调用；DSL 只是“把结构化过滤条件编译到已审计函数”，不是“执行模型写的一串代码”。
- `source`（FULL/VALID）由工具签名锁定，默认 `VALID`（对齐 LegalityGate“实战推荐只用 VALID”）；图鉴回答需要 FULL 时由 `get_spirit_profile`/`get_skill_profile` 显式带 `source` 参数。

#### 4.2.2 工具签名

```python
def get_catalog_version() -> dict:
    # {data_digest, rules_digest, spirit_count, skill_count, valid_skill_count, families_count}

def search_spirits(filters: list[list[SpiritFilter]], *, source: DataSource = VALID,
                   limit: int = 20) -> list[dict]:
    # 析取范式：外层 OR、内层 AND。返回每只命中精灵的精简档案
    # {name, types, trait_name, is_boss, family_key, number, learnable_skill_count}

def get_spirit_profile(name: str, *, source: DataSource = VALID) -> dict:
    # RawSpirit 全字段 + 由 learnable_skills 计算的可学池 + 合法血脉(18系) + 是否首领

def get_skill_profile(name: str, *, source: DataSource = VALID) -> dict:
    # {name, type, kind, power, energy_cost, desc, implemented: bool}
    # implemented = name in load_skills(VALID)

def get_build_options(name: str, bloodline: str = "", *, source: DataSource = VALID) -> dict:
    # {learnable_skills: [...], valid_natures: [...], iv_max, iv_dims: 3}
    # valid_natures = [NEUTRAL_NATURE] + list(NATURE_BONUS)
```

关键实现要点：

- `search_spirits` 的 `learnable_skill` 过滤：对候选精灵逐一调 `teambuilder.learnable_skills(name, "", source)` 判 `contains`——这是 O(候选数) 的确定性计算，不是存储字段，**必须**走计算而不是 raw JSON。
- `family` 过滤：用 `dataset.load_families(source)` 得到 `{family_key: member_names}`，反查 `name → family_key`；值可传精灵名或 `family_key`。
- 排序稳定：命中结果按 `name` 排序，保证同参同果（可测、可缓存）。
- 所有返回值**不含**原始 JSON 里未归一化的字段（如 raw `evolution` 数组、raw `skills` 四池原文），只回 `RawSpirit/RawSkill` 归一化字段。

### 4.3 `validate.py` — 结构化硬闸

```python
@dataclass(frozen=True)
class TeamValidation:
    ok: bool
    errors: list[dict]          # [{code, message, pick_index|None}]

def validate_team(picks: list[TeamPick], items: list[str], *,
                  rules: BattleRules = DEFAULT_RULES,
                  source: DataSource = VALID) -> TeamValidation:
    # 直接调 teambuilder.validate_team；空列表 → ok=True
    # 否则把确定性中文错误串映射为 code（见下）
```

错误码（对齐 [teambuilder.py:59](src/environment/teambuilder.py#L59) 逐条输出）：

| code | teambuilder 触发的条件 |
|---|---|
| `TEAM_SIZE` | 队伍规模 != rules.team_size |
| `SPIRIT_NOT_FOUND` | 精灵名不在表内 |
| `BLOODLINE_INVALID` | 血脉不是合法系别 |
| `BOSS_NOT_ALLOWED` | 首领形态入队 |
| `SKILL_COUNT` | 技能数不在 1–skill_slots |
| `SKILL_NOT_IMPLEMENTED` | 技能在 FULL 但不在 VALID 白名单 |
| `SKILL_NOT_FOUND` | 技能不存在 |
| `BLOODLINE_SKILL_REQUIRED` | 血脉技能未选血脉 |
| `BLOODLINE_SKILL_MISMATCH` | 血脉技能系别与血脉不符 |
| `SKILL_NOT_LEARNABLE` | 技能不在可学池 |
| `NATURE_UNKNOWN` | 性格未知 |
| `IV_INVALID` | 个体值键/值/维度越界 |
| `IV_TOO_MANY_DIMS` | 投入维度 > 3 |
| `FAMILY_CONFLICT` | 同家族多只入队 |
| `ITEM_DUPLICATE` / `ITEM_NOT_FOUND` | 道具重复/不存在 |

实现策略（诚实标注）：teambuilder 返回的是**中文自由文本**。M1 用一个**确定性关键字映射器**把每条消息映射到 code，并写一条**钉住映射**的测试（把 teambuilder 所有分支的错误文案作为固定用例，映射器改文案就红）。若该映射在后续里程碑被证明脆弱，再单独立项把 teambuilder 升级为“直接返回结构化 code”（这属于 `environment` 改动，允许——environment 只是不能 import rock_pvp_agent，改动本身可做）。

## 5. 边界与护栏

- **不引入任意代码执行**：DSL 只编译到 `_FIELD_DISPATCH`，无 `eval/exec/open/网络`。
- **source 由工具锁死**，模型不可传任意 source 字符串绕过 FULL/VALID 语义。
- **只读**：本里程碑所有工具不写任何文件、不改任何 `environment` 状态。
- **返回体积上限**：`search_spirits` 默认 `limit=20`，单条档案只含精简字段，防止把整表灌进模型上下文。

## 6. 测试计划

- `fingerprint`：同进程两次 `data_digest()` 相等；临时改一个数据文件字节（用 tmp 副本 + monkeypatch 路径）→ digest 变化；恢复 → 还原。
- `catalog`：
  - 已知精灵名 `get_spirit_profile` 返回的 `types/trait/is_boss` 与 `dataset.load_spirits` 一致；
  - `search_spirits(filters=[["type in 火"]])` 返回的都是火系；
  - 组合查询（外层 OR / 内层 AND）语义正确；
  - 非法 `field/op` 抛 `QueryNotAllowed`（含 `eval`/`open` 字样时绝不执行）。
  - `get_build_options` 的可学池 == `learnable_skills`，且 VALID 下不含未实装技能。
- `validate`：
  - 合法阵容 → `ok=True`；
  - 逐一注入非法项（首领入队 / 血脉技能不符 / 家族冲突 / 未实装技能）→ 对应 code 命中；
  - 映射钉住测试覆盖 teambuilder 全部错误分支文案。

## 7. 验收 Gate

- [ ] 三个模块纯函数、可确定性测试，无网络、无 LLM、无随机。
- [ ] `environment` 未被 import rock_pvp_agent（隔离不变量）。
- [ ] DSL 白名单无任何可绕过路径（review 视角：正确性 / 一致性 / 隔离）。
- [ ] 全量 pytest 绿，覆盖率不降。
- [ ] 展示 Gate 报告 → 用户「通过」→ commit。

## 8. 风险与回滚

- **风险**：错误码映射器靠中文文案，teambuilder 改文案即断裂 → 用钉住测试兜底；长期升级 teambuilder 返回 code。
- **风险**：字节级指纹对数据文件重排敏感 → 偏保守，可接受；文档里写清“重排也会变指纹”。
- **回滚**：本里程碑纯新增包，无现有行为改动，`git revert` 即可完全回退。
