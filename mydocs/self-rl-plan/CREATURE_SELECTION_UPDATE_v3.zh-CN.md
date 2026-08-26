# 精灵备战与选择功能更新日志 v3（schema 2.1.0）

- 日期：2026-07-31
- 范围：`rock_kingdom_world`（洛克王国：世界）手游数据
- Content Pack `schema_version`：`2.0.0` → `2.1.0`
- 数据库迁移：新增 `alembic/versions/0003_roster_eligibility_and_loadout.py`
- 上一版：`docs/CREATURE_CATALOG_UPDATE_v2.zh-CN.md`

本次更新落地两条需求：

1. 标注「只能在对战中进化」的精灵形态（首领化为代表），禁止其进入对战前的备战列表；
2. 新增选择功能：按精灵 id 获取结构化精灵信息，并在一次调用内设置血脉、四个出战技能、
   努力值与性格加成。

下面按需求说明「改了什么」和「在哪里约束」。

## 1. 备战资格标注：首领化形态进不了备战列表

### 领域模型

- 新增枚举 `RosterEligibility`（`selectable | battle_only`），语义是「对战前能否放入备战列表」。
- `CreatureSpecies` 新增两个字段：
  - `roster_eligibility`，默认 `selectable`；
  - `roster_note`，说明为什么是对战限定，例如「首领化，仅对战中觉醒」。
- `mode="before"` 校验器 `default_roster_eligibility_from_form_kind`：字段留空时，
  凡 `form_kind` 落在 `BATTLE_ONLY_FORM_KINDS`（当前为 `{leader}`）的形态自动标成 `battle_only`。
  策展人仍可显式覆盖 —— 由游戏事实决定，而不是由形态标签决定 —— 但**漏填绝不会**让
  首领化形态保持可选。
- `CreatureSpecies.is_roster_selectable` 便捷属性。
- `CreatureBuild.validate_roster_eligibility(species)`：物种 id 与构筑 id 不符时报
  `species ... does not describe build creature ...`；形态为对战限定时报
  `... cannot be put on the roster before a battle: <roster_note>`。

被标注的形态**仍是完整的目录实体**：保留自己的种族值、技能池、特性选项与进化链，
只是不能被玩家在备战阶段选中。

### 仓储层（真正的入口约束）

- `CatalogRepository._require_roster_selectable(creature)` 抛
  `CatalogError(f"{creature_id} cannot be selected before a battle: {detail}")`，
  `detail` 优先取 `roster_note`，缺失时退化为 `this form only exists during battle`。
- `create_build(...)` 在建构筑前调用该守卫 —— 备战列表的唯一写入口，绕不过去。
- `selectable_creatures(content_pack_id)` 返回过滤后的可选形态列表，供 UI 直接渲染。
- `set_roster_eligibility(pack, creature_id, value, *, roster_note, expected_revision, ...)`：
  策展人翻转标注，走乐观锁 + `change_audit`；重复设置同一值报
  `... is already selectable`（不做无意义的 revision 递增）。

### 数据库

- `creatures` 新增 `roster_eligibility`（`NOT NULL`，`server_default='selectable'`）与 `roster_note`，
  并加 CHECK 约束 `ck_creature_roster_eligibility`（取值限定两枚举值）。
- 迁移 `0003` 回填历史数据：
  `UPDATE creatures SET roster_eligibility='battle_only',
  roster_note=COALESCE(roster_note,'leader form awakens in battle only')
  WHERE form_kind IN ('leader') AND roster_eligibility='selectable'` ——
  已有的首领化行不会静默继承 `selectable` 默认值。

### CLI

- `creature add` 新增 `--roster-eligibility` / `--roster-note`；两者留空时由 `model_validate`
  触发上面的形态默认值，命令回显最终判定结果（`<creature_id>\t<eligibility>`）。
- 新增 `creature set-roster-eligibility --to --roster-note --expected-revision`。

## 2. 选择功能：一次调用配好一只精灵

新增 `src/rockpvp_data/services/selection.py`，两个入口：

### `describe_creature(pack, creature_id) -> CreatureInfo`

按精灵 id 返回「这只精灵能怎么配」的结构化信息，全部为 `StrictModel`：

| 字段 | 内容 |
| --- | --- |
| 身份 | `creature_id` / `lineage_id` / `dex_number` / `form_code` / `form_kind` / `form_name` / `name` |
| 备战资格 | `roster_eligibility` / `roster_note` / `is_roster_selectable` |
| 属性系别 | `elements: list[NamedRef]` |
| 种族值 | `base_stats: list[BaseStatView]`（含 `amount: KnownInteger`、`supports_effort`、`display_order`） |
| 特性 | `trait_options`（`is_default` / `is_fixed`） |
| 技能池 | `normal_skills` 与 `bloodline_gated_skills` **分开返回**，后者带 `required_bloodline_element_id` |
| 血脉候选 | `selectable_bloodline_elements`（`Element.is_bloodline_selectable` 过滤） |
| 性格候选 | `nature_options`（升降属性与百分比） |
| 规则 | `effort_rules` / `bloodline_rules` / `loadout_rules`，均为版本化数据而非常量 |

精灵不存在时抛 `NotFoundError("creature not found: ...")`。
配套 `selectable_creatures(pack)` 给出备战阶段的候选列表（已排除对战限定形态）。

### `select_creature(pack, selection, *, operator, reason) -> SelectedCreature`

入参 `CreatureSelection` 一次性描述玩家的完整配置：

```
build_id, creature_id, name,
bloodline_element_id, nature_id, trait_id,
effort_allocations: [EffortChoice(stat_id, bonus)],
equipped_skills:    [SkillSlotChoice(slot, skill_id)]
```

模型层校验器 `check_no_duplicate_choices` 拒绝重复槽位、重复技能、重复属性。

服务层 `_precheck(...)` 在**任何写入之前**校验全部规则，因此非法选择不会留下半成品构筑：

1. 对战限定形态直接拒绝（需求 1 的第三道防线）；
2. 构造一份临时 `CreatureBuild`，跑 `validate_loadout` + `validate_effort`，
   把 `ValueError` 统一包成 `CatalogError`；
3. 技能必须在该形态技能池内，否则报
   `these skills are not in <creature_id>'s skill pool: [...]`；
4. 性格必须存在（`NotFoundError`），特性必须在该形态的特性选项内。

通过后按固定顺序写入：建构筑 → 血脉系别 → 性格 → 特性 → 努力值 → 技能（按槽位排序）。
**血脉门控技能排在最后**，因为它们依赖已经落库的血脉系别。每步递增本地 `revision`，
最终返回 `SelectedCreature`（含解析后的血脉、性格、特性、技能槽与 `revision`）。

### 四个出战技能是规则，不是常量

新增 `LoadoutRules`（表 `loadout_rules`，每个 Content Pack 一行）：

- `equipped_skill_slot_count` 默认 `4`（约束 1～16）；
- `require_nature` 默认 `true` —— 这就是「性格加成必填」的落点；
- `require_full_skill_set` 默认 `true`。

`CreatureBuild.validate_loadout(rules)` 依据该规则校验：槽位越界报
`skill slots must be between 1 and N`，数量不符报 `must equip exactly N skills`，
缺性格报 `a battle-ready build must select a nature`。
`CatalogRepository.equip_build_skill` 的槽位上界也改为从 `loadout_slot_count(pack)` 读取，
而不再写死 4。补丁改成 2 技能位时，只需改一行规则数据，构筑校验与 CLI 立即跟随。

### 性格加成

新增 `Nature`（表 `natures`）：`increased_stat_id` / `decreased_stat_id` /
`increase_percent` / `decrease_percent`（默认各 10）。规则：

- 要么「升一维 + 降一维」，要么两者皆空（中性性格），半填报
  `a nature either boosts one stat and hinders another, or is neutral on both`；
- 升降不能是同一维，报 `a nature cannot boost and hinder the same stat`；
- `ContentPack` 层校验性格引用的属性必须存在，否则报 `unknown stat`。

数据库侧同样用 CHECK 约束兜底：`ck_nature_stat_pair`（两列同时为空或同时非空）、
`ck_nature_distinct_stats`、以及两个百分比区间约束。
`creature_builds` 新增 `nature_row_id`，仓储入口为 `select_build_nature(...)`。

血脉与努力值沿用 v2 已有的模型（玩家选一个系别；恰好三维、每维 +21～30），
本次只是把它们接入同一个选择入口。

### CLI

新增三个子命令组，命令组总数 20：

- `loadout set-rules --rules-id --slots --require-nature/--no-require-nature`
- `nature add --id --name --up --down --increase-percent --decrease-percent`
- `selection show --pack --creature [--json]`：人读多行或结构化 JSON
- `selection list --pack`：备战候选列表
- `selection apply --pack --file selection.json`：读入一份 `CreatureSelection`，
  回显 `SelectedCreature` 的 JSON
- 补齐 `build select-nature`、`build select-trait`、`build clear-skill`

## 数据库变更清单

新增表：`loadout_rules`、`natures`。

| 表 | 变更 |
| --- | --- |
| `creatures` | 新增 `roster_eligibility`（`NOT NULL DEFAULT 'selectable'`，CHECK 限定枚举）、`roster_note` |
| `creature_builds` | 新增 `nature_row_id` → `natures.id` |
| `loadout_rules` | 新表：`content_pack_id`(PK)、`rules_id`、`equipped_skill_slot_count`、`require_nature`、`require_full_skill_set`、`notes`，CHECK `1 <= slots <= 16` |
| `natures` | 新表：`(content_pack_id, nature_id)` 唯一，升降属性外键 + 4 条 CHECK |

迁移 `0003` 与 `0002` 同样由 inspector 驱动、幂等：全新库上 `0001` 已按当前模型建表，
加列部分等价于 no-op，只有 `Base.metadata.create_all` 起作用；老库上执行真实迁移并回填首领化标注。

`downgrade` 有一处 SQLite 特有的坑并已修正：批处理重建表时会**重新生成**
`ck_creature_roster_eligibility` 这条 CHECK 约束，而它引用的正是要删除的列，
于是 `_alembic_tmp_creatures` 报 `no such column: roster_eligibility`。
解决办法是把 `drop_constraint` 与 `drop_column` 放进同一个
`batch_alter_table("creatures")` 块内。

## 其他同步改动

- `schemas/v1/` 从 16 个增至 21 个：新增 `creature-info`、`creature-selection`、
  `loadout-rules`、`nature`、`selected-creature`；`content-pack`、`content-pack-manifest`、
  `creature-species`、`creature-build` 因新字段重新生成。漂移检测测试继续生效。
- Content Pack 导出/导入新增 `loadout_rules.json`（`null` 表示未知）与 `natures.json`。
  `ContentPackService._creature` 与 `services/importer.py` 都显式透传
  `roster_eligibility` / `roster_note` —— 少了任何一处，导出再导入会**静默抹掉**备战标注，
  因此专门加了一个往返测试钉住这条路径。导入顺序上性格排在属性定义之后（它引用属性）。
- `content-packs/rock-kingdom-world/initial/` 的 `manifest.yaml` 升到 `schema_version: 2.1.0`，
  并补齐 `loadout_rules.json`（`null`）、`natures.json`（`[]`）；数据文件仍为空集合。

## 验证结果

- `pytest`：46 项通过（`tests/test_smallest_palindrome.py` 依赖缺失的 `tests.tmp`，
  与本次改动无关，已排除）。autouse socket 守卫仍禁止实网访问。
- `ruff check .`：通过。`mypy src/rockpvp_data`（strict + pydantic 插件）：31 个文件无错误。
- 新增 `tests/test_selection.py`（6 项）：首领化被标注且 `create_build` 与
  `select_creature` 双双拒绝、拒绝后数据库无残留；策展人可翻转标注且重复翻转报错；
  `describe_creature` 返回全部可选项（含 `stat_attack=120` 的种族值与按血脉门控拆分的技能池）；
  一次调用写入血脉/技能/努力值/性格；六种违规选择逐一被拒且 `creature_builds` 保持为空；
  把 `equipped_skill_slot_count` 改成 2 后 4 技能选择立即失败。
- `tests/test_domain.py` 新增 5 项：形态默认标注与策展人覆盖、`validate_roster_eligibility`
  的两种失败、`validate_loadout` 的三种失败、`Nature` 成对规则、性格引用未知属性。
- `tests/test_content_pack.py` 新增往返测试：导出含两个新文件、首领化标注在导出与
  重新导入后都保持 `battle_only`、摘要一致、性格与 loadout 规则完整还原。
- 迁移在临时 SQLite 库上走通 `upgrade → downgrade 0002 → upgrade` 全程：
  降级后两列两表全部消失；在 `0002` 形态的库中种入 `c_base`/base 与 `c_leader`/leader
  再升级，得到 `('c_leader','leader','battle_only','leader form awakens in battle only')`
  与 `('c_base','base','selectable',NULL)`。临时库已删除。
- 未运行任何实网抓取；采集功能保持默认关闭。

## 待办

1. 补充「对战限定」的其他形态类型：目前 `BATTLE_ONLY_FORM_KINDS` 只含 `leader`，
   若后续确认异色/超进化等形态也有同类限制，需要双源证据后扩充并再写一次回填迁移。
2. 性格的实际数值需要双源核对：`increase_percent` / `decrease_percent` 目前是 10% 的
   默认值，尚无来源证据，全部性格条目仍应停留在 `draft`。
3. `Nature` 只表达「一维加、一维减」；若游戏中存在影响命中或先手的特殊性格，
   需要扩展模型而不是塞进百分比。
4. `selection apply` 目前只做单只精灵；备战列表本身（队伍容量、重复精灵限制）
   仍未建模，属于下一步的对战规则层。
5. `docs/CREATURE_CATALOG_IMPLEMENTATION_v1.*` 仍描述 v1 模型，与 v2、v3 两份更新日志
   存在冲突，下次修订时应合并为一份现状文档。
