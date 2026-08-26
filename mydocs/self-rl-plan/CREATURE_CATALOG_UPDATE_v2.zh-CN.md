# 精灵图鉴数据层更新日志 v2（schema 2.0.0）

- 日期：2026-07-30
- 依据：`docs/CREATURE_CATALOG_IMPLEMENTATION_v1.en.md` 第 12 节「需求补充与后续建议」
- 范围：`rock_kingdom_world`（洛克王国：世界）手游数据，经典页游数据仍然排除在外
- Content Pack `schema_version`：`1.0.0` → `2.0.0`
- 数据库迁移：新增 `alembic/versions/0002_forms_governance_and_player_rules.py`

本次更新把 10 条需求逐条落到领域模型、数据库、仓储写入层、CLI、采集适配器与测试中。
下面按需求编号说明「改了什么」和「在哪里约束」。

## 1. 形态与进化链：编号不再是身份

网页精灵编号（`dex_number`）只是展示字段，不再是唯一标识。

- 身份三元组拆分：`creature_id`（唯一身份）、`lineage_id`（同一精灵的形态族，用于聚合展示）、
  `form_code` + `form_kind` + `form_name`（区分基础态 / 首领化 / 异色等形态）。
  `CreatureFormKind` 取值 `base | leader | alternate | unknown`。
- 数据库唯一约束从 `(content_pack_id, dex_number)` 改为
  `(content_pack_id, creature_id)` 与 `(content_pack_id, dex_number, form_code)`。
  同编号可以有多行，同编号同形态不行。
- 每个形态各自持有种族值、技能池、特性选项，互不共享；首领化形态可以有与基础态不同的种族值。
- 新增 `CreatureEvolutionLink` / `creature_evolution_links` 表，两端都引用 `creature_id`，
  带 `EvolutionMethod`（`level_up | item | event | leader_awakening | other | unknown`）。
- 新增 CLI：`evolution link --from --to --method --condition`；
  `creature add` 增加 `--lineage --form-code --form-kind --form-name`。
- 采集适配器不再假设编号即身份：payload 改为输出 `form_kind_raw` / `form_label_raw`，
  形态无法判定时产生警告，要求人工在离开 draft 之前指定 `form_code`。

## 2. 只记录「能学什么」，不记录「怎么获得」

- `CreatureSkillPoolEntry` 字段收缩为 `{skill_id, notes}`，删除
  `acquisition_method` / `unlock_level` / `condition`。
- 迁移 `0002` 从 `creature_skill_pool` 删除对应三列。
- 唯一保留的获取语义是「血脉技能只能选一个」，它不落在物种技能池上，
  而是通过 `SkillKind`（`normal | bloodline | will_impact`）+ 构筑校验实现（见需求 7）。
- CLI 相应简化为 `skill-pool attach --creature --skill [--notes]`。

## 3. 字段级版本区间：补丁不覆盖历史

- `VersionValidity{first_game_version, last_game_version}` 为半开区间，
  `last_game_version` 为空表示「当前生效」，首尾相同会被拒绝。
  `CreatureSpecies` / `Skill` / `Trait` 均带 `validity`。
- 新增只追加的 `field_value_versions` 表与 `FieldValueVersion` 模型，
  按 `(entity_type, entity_id, json_pointer, first_game_version)` 唯一。
- `CatalogRepository.record_field_version(...)` 写新版本时**关闭**上一段区间
  （给旧行补 `last_game_version`），不修改旧值；对同一版本重复写入会报
  `... already has a value for <version>; correct that version instead of appending an identical range`。
- 查询入口 `field_history(entity_type, entity_id, json_pointer)` 按 `first_game_version` 排序。
- CLI：`field record --entity-type --entity-id --pointer --value --game-version`、
  `field history --entity-type --entity-id --pointer`。
- `stat set-base --game-version` 与 `trait update --game-version` 会自动追加字段历史。

## 4. 冲突进审核队列，不按来源顺序静默覆盖

- 新增 `review_queue_items` 表与 `ReviewQueueItem` 模型
  （`candidate_values` / `source_ids` / `staging_ids` / `status` / `resolved_*`）。
- `reconcile_flat_records(records, source_by_staging_id=...)`：来源取值一致才进 `merged`，
  不一致的字段被**排除**在 `merged` 之外，生成带来源归属的 `FieldConflict`，
  再由 `FieldConflict.to_queue_item(...)` 入队。`ReconciliationResult.is_clean` 表示无冲突。
- `resolve_conflict(...)` 只接受候选值之一，否则报
  `the resolved value must be one of the recorded candidates`；解决时记录 reviewer、时间与备注。
- CLI：`review list`、`review resolve --id --value --reviewer --note`。

## 5. 不处理图片与素材

代码库中不存在任何图片、立绘、图标字段或素材下载路径；采集层的响应媒体类型白名单
只允许 `text/html` / `application/json` / `text/plain`，二进制素材无法进入管道。

## 6. 努力值：为三维各加 21～30

- `EffortRules` 由「上限模型」改为「加成模型」：
  `boosted_stat_count=3`、`min_bonus_per_stat=21`、`max_bonus_per_stat=30`。
  旧字段 `per_stat_cap` / `total_cap` 删除。
- `build_effort_allocations.value` 重命名为 `bonus`，语义是「在种族值之上额外增加的数值」。
- 双层校验：
  - `CreatureBuild.validate_effort(rules)` 校验一份完整分配（必须恰好 3 维、每维 21～30）；
  - `CatalogRepository.set_build_effort(...)` 校验单步写入：该属性支持努力值、
    `bonus` 在区间内（`effort bonus must be between 21 and 30`）、
    已分配维度不超过 3（`effort may boost at most 3 stats; clear one before assigning another`）。
- CLI：`build update-effort --stat --bonus`、新增 `build clear-effort --stat`（腾出维度）。
- 六维本身仍由 `stat_definitions` 定义（`supports_effort` 标记哪些维度可加努力值），
  不写死枚举，便于后续版本增减维度。

## 7. 血脉：玩家从 18 系中选一个系别

血脉**不再是独立目录实体**，而是「玩家为构筑选择的一个系别」。

- 删除 `bloodlines`、`creature_bloodline_options` 两张表，删除 `Bloodline` /
  `CreatureBloodlineOption` 模型与 `schemas/v1/bloodline.schema.json`。
- 新增 `BloodlineRules`（`selectable_element_count=18`、`max_bloodline_skills_per_build=1`）
  与 `bloodline_rules` 表；`Element.is_bloodline_selectable` 标记哪些系别可作血脉。
- `CreatureBuild.bloodline_element_id`（表列 `creature_builds.bloodline_element_row_id`）
  保存玩家选择；旧列 `selected_bloodline_row_id` 删除。
- `Skill.kind` 引入 `bloodline` / `will_impact`，`Skill.requires_bloodline_element` 为真时受血脉门控；
  血脉技能必须带 `element_id`（否则模型校验报 `element_id is required`）。
- 门控规则（`CatalogRepository._check_bloodline_gate` 与
  `CreatureBuild.validate_bloodline_skills`）：
  1. 未选血脉系别就装备血脉/愿力冲击技能 →
     `select a bloodline element before equipping a bloodline or will-impact skill`；
  2. 技能系别与所选血脉系别不符 → `skill does not match the build's bloodline element`；
  3. 血脉技能数超过 1 → `a build may equip at most 1 bloodline skill`。
- 已装备门控技能后改选其他血脉系别会被拒绝
  （`these equipped skills belong to another bloodline element: ...`）。
- CLI：`bloodline set-rules`（替代原 `bloodline add` / `bloodline attach`）、
  `build select-bloodline --element`。

## 8. 特性支持人工修改

- `Trait` 增加 `revision`（乐观锁）与 `validity`；`traits` 表增加 `revision` 与首末版本列。
- 新增 `CatalogRepository.update_trait(..., expected_revision, name_zh_cn/description/effects,
  game_version=None)`：
  - 无任何字段变更时报 `a trait update must change at least one field`；
  - `expected_revision` 不匹配时抛 `RevisionConflictError`；
  - 成功后 `revision + 1`，写 `change_audit`；
  - 传入 `game_version` 时为每个改动字段追加一条字段历史（需求 3）。
- CLI：`trait update --id --expected-revision --name --description --effect --game-version`。

## 9. 发布治理：Draft → Reviewed → Published

- `PublicationDecision` 要求「至少两条独立证据」或「一次人工复核」：
  目标为 `published` 且独立来源去重后少于 2 条时，模型校验直接拒绝
  （`... 2 independent sources ...`）；`reviewed` 可凭 `human_reviewer` 通过。
- `CatalogRepository.promote_status(...)` 三重校验：
  1. 状态机 `STATUS_TRANSITIONS` 合法性（`draft` 不能直接跳到 `published`，
     否则报 `illegal transition ...`）；
  2. `expected_revision` 乐观锁；
  3. 该实体存在待处理冲突时阻断发布
     （`unresolved source conflict on <pointer> blocks promotion`）。
- 受治理实体：`creature` / `skill` / `trait`。CLI：
  `governance promote --entity-type --entity-id --to --source ... --human-reviewer --note`。

## 10. 来源许可：可抓不等于可再发布

- `SourceSite` 与采集侧 `SourceConfig` 同步新增
  `reuse_rights_confirmed` / `reuse_rights_reviewer` / `reuse_rights_reviewed_at` / `reuse_rights_note`
  （`source_sites` 表同步加列）。
- 校验规则：确认许可必须同时给出复核人与复核时间；
  `enabled: true` 但未确认许可时直接拒绝加载配置：
  `crawlability is not republication permission: confirm reuse rights before enabling a source
  for production collection`。
- `AdapterRegistry.create(require_enabled=True)` 与 `enabled_sources(...)` 都会检查该开关；
  `rockpvp-crawl` 的来源解析同样拒绝未确认许可的来源。
- `config/sources.yaml` 中四个来源全部 `reuse_rights_confirmed: false` 并写明待确认原因；
  RocoDex 的许可备注说明其 CC BY-NC-SA 声明只覆盖「部分素材」、未指明字段范围，
  必须由具名复核人做出许可决定后才能用于生产采集。
- 页游来源 `https://17roco.qq.com/` 与 `https://news.4399.com/luoke/luokechongwu/`
  仍在 `blocked_url_prefixes` 中。

## 数据库变更清单

新增表：`bloodline_rules`、`creature_evolution_links`、`field_value_versions`、`review_queue_items`。

删除表：`bloodlines`、`creature_bloodline_options`。

列变更：

| 表 | 变更 |
| --- | --- |
| `creatures` | 新增 `lineage_id`、`form_code`、`form_kind`、`form_name`、`first_game_version`、`last_game_version`；唯一约束改为 `(content_pack_id, dex_number, form_code)` |
| `elements` | 新增 `is_bloodline_selectable` |
| `skills` | 新增 `kind`、`first_game_version`、`last_game_version` |
| `traits` | 新增 `revision`、`first_game_version`、`last_game_version` |
| `effort_rules` | 新增 `boosted_stat_count`、`min_bonus_per_stat`、`max_bonus_per_stat`；删除 `per_stat_cap`、`total_cap` |
| `creature_skill_pool` | 删除 `acquisition_method`、`unlock_level`、`condition` |
| `creature_builds` | 新增 `bloodline_element_row_id`；删除 `selected_bloodline_row_id` |
| `build_effort_allocations` | `value` 重命名为 `bonus` |
| `source_sites` | 新增 4 个 `reuse_rights_*` 列 |
| `field_evidence` | 新增 `source_id` |

迁移 `0002` 由 inspector 驱动、幂等：全新库上等价于 no-op（`0001` 已按当前模型建表），
v1 老库上执行真实迁移，最后用 `Base.metadata.create_all` 补齐仍缺的表。

同时修正了一处 SQLAlchemy 2.0 类型推断缺陷：`Mapped[Any] = mapped_column(JSON)` 会被推断为
`NOT NULL`，导致「待处理的审核队列项」（`resolved_value` 合法为空）无法插入。
`review_queue_items.resolved_value`、`field_value_versions.value`、
`field_evidence.raw_value/normalized_value`、`change_audit.before/after` 均显式声明 `nullable=True`。

## 其他同步改动

- `schemas/v1/` 重新生成，从 10 个增至 16 个：新增 `bloodline-rules`、`creature-evolution-link`、
  `effort-rules`、`field-value-version`、`publication-decision`、`review-queue-item`，
  删除 `bloodline`。`tests/test_schema_export.py` 继续做漂移检测。
- Content Pack 导出/导入：删除 `bloodlines.json`，新增 `bloodline_rules.json`、`evolution_links.json`；
  规范化 JSON 的精灵排序键改为 `(dex_number, form_code, creature_id)`，
  进化链按 `(from, to)` 排序，保证摘要与集合顺序无关。
- `content-packs/rock-kingdom-world/initial/` 的 `manifest.yaml` 升到 `schema_version: 2.0.0`，
  数据文件仍为空集合（`null` 表示「未知」，不表示零）。
- RocoDex 适配器版本号 `rocodex_html_v1` → `rocodex_html_v2`：payload 移除 `bloodline_raw`
  与 `effort_values_raw`（二者都是玩家选择，不是物种事实），技能分组新增 `skill_kind`
  （按「愿力」「血脉」关键字归类），并在页面出现努力值标签时产生「这是玩家示例」的警告。
- CLI 子命令组扩展为 17 组，新增 `evolution`、`field`、`review`、`governance`。

## 验证结果

- `pytest`：34 项通过（`tests/test_smallest_palindrome.py` 依赖缺失的 `tests.tmp`，
  与本次改动无关，已排除）。测试仍受 autouse socket 守卫保护，禁止实网访问。
- `ruff check`：通过。`mypy src`（strict + pydantic 插件）：30 个文件无错误。
- `alembic upgrade head` 在临时 SQLite 库上成功，抽查建表结果符合上述清单。
- 端到端手工流程走通：建库 → 建包 → 系别/属性/精灵（基础态+首领化同编号）→ 进化链 →
  种族值 → 技能 → 技能池 → 特性人工改版并留下字段历史 → 构筑三维努力值 →
  选血脉系别并装备血脉/愿力冲击技能 → 追加字段版本 → `draft→reviewed→published` →
  导出 → 校验 → 导入空库，导出与重新导入的 SHA-256 摘要一致。
- 负面用例同样验证：第 4 个努力值维度、区间外的 `bonus`、跨系别血脉技能、
  重复形态、过期 `expected_revision`、非候选值的冲突裁决全部被拒绝。
- 未运行任何实网抓取；采集功能保持默认关闭。

## 待办

1. 由具名复核人完成 RocoDex 的许可确认，才能开启生产采集。
2. 人工双源校验后写入第一份非空 Content Pack（当前初始包仍为空集合）。
3. 为 Staging → Canonical 的显式晋升补一个审阅 CLI。
4. 用一次真实版本补丁演练 Content Pack diff、回归与回滚。
5. `docs/CREATURE_CATALOG_IMPLEMENTATION_v1.*` 仍描述 v1 模型（`Bloodline` 实体、
   获取方式、编号唯一等），下次修订时应与本文档合并。



