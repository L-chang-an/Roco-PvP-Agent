# E 线审计 · 00 基线冻结

> 审计对象：`feat/e-line-v2` 分支（纯 E 世界，无 R 线代码）
> 日期：2026-08-28　审计框架：`docs/project-audit-plan.md` v1.0（E 线子集）

## 基线

| 项 | 值 |
|---|---|
| 分支 / HEAD | `feat/e-line-v2` @ `19f5b0e` + 2026-08-29 改造（删 E0 / FULL 单源 / {3,6} / 队伍 JSON v2） |
| Python / uv | Python 3.12.13 / uv 0.11.23 |
| 测试 | **585 passed** |
| environment 覆盖率 | **94%** |
| 未提交文件 | 用户工作文件（docs/README.md WIP、Readme_complate.md、project-audit-plan.md） |

## 2026-08-29 基线变更（已落地）

- **E0 教学数据删除**：`e0_skills.json`/`e0_spirits.json`/`E0_EFFECTS`/`DataSource.E0` 移除；`DEFAULT_SOURCE = FULL`（553 技能 / 593 精灵）。
- **队伍规模固定 {3, 6}**：`battle_config.ALLOWED_TEAM_SIZES = (3, 6)`；UI 规模选项只有 3/6；历史 4v4/5v5 队伍加载时提示 + 吸附。
- **保存队伍 JSON v2**：每个技能带 `{name,type,desc}`、每只精灵带 `trait{name,desc}`；请求体仍最简（`extra="forbid"` 拒绝富化字段）；load 兼容 v1/v2。
- **道具改名**：`E0_ITEMS` → `ITEMS`。
- 测试从 590 调整为 585（删 7 个 E0 专属测试 + 迁移 fixtures 到 FULL + 新增 {3,6}/v2 富化测试）。


## 边界说明

- 本次审计范围 = `src/environment/`（22 文件）+ 桥层 `src/rock_pvp_agent/battle/{player,selfplay,store,prompts}.py` + `src/ui/battle.py`（E 线产物）。
- 分支为"完整 E 线、零 R 线"：无 `evaluate.py`（R0 产物）、无 `evolution/`。
- 覆盖脚本存 `/tmp/eaudit/`（审计产物不入库；如需留存移到 `docs/audit/artifacts/`）。

## 已知仓库历史事实（审计基线记录）

1. **E0b–E6.5 的测试文件直到 `19f5b0e`（R 线前）才被提交进 git**——此前只在工作区。E7 提交（`f7a687d`）本身只有 133 条测试，而 Gate 声称 590。**结论：历史 Gate 的"590 passed"是在未提交工作区上验证的，无法由 E7 提交复现；本分支（19f5b0e）是代码与测试首次真正对齐的基线。**（影响：可复现性 P2）
2. `src/environment/evaluate.py` 是 R0 塞进 E 包的文件，本分支已按"纯 E 世界"剥离。
