# UI 提级 + 组队页实现（2026-08-25）

状态：已实施（485 测试全绿，含 29 个组队页契约测试）　前置：E0a–E3（数据/组队/规则/效果）

## 1. 需求与两个 Part

负责人 2026-08-25 提出：

1. 把 `src/rock_pvp_agent/ui` **提级**到 `src/ui`，**回归不变性**（不破坏原有 UI 功能）；
2. 扩展 UI 实现**组队页**：选精灵（按名搜索）→ 配技能（**该精灵可学池**，非全局）→
   配血脉/个体值/性格 → 保存/加载到路径，作为将来 battle 组队的基础；
3. 参考 `~/workspace/SelfPlayAgent/src/agent/ui` 的编队实现。

## 2. Part 1：提级 `rock_pvp_agent.ui` → `ui`

| 改动 | 内容 |
|---|---|
| `git mv src/rock_pvp_agent/ui src/ui` | 目录整体上移（模块名 `rock_pvp_agent.ui` → `ui`） |
| [server.py](src/ui/server.py) / [context.py](src/ui/context.py) | `..agent`/`..config` 相对导入 → `rock_pvp_agent.agent`/`config` 绝对导入 |
| [rock_pvp_agent/__main__.py](src/rock_pvp_agent/__main__.py) | `--serve` 懒加载 `from .ui.__main__` → `from ui.__main__`（`python -m rock_pvp_agent --serve` 入口不变） |
| [pyproject.toml](pyproject.toml) | `packages` 加 `src/ui`；**移除冗余 force-include**（packages 模式已含各包目录全部文件——原 force-include 会与 packages 重复，`uv build --wheel` 一直失败，本次顺带修复） |
| [test_agent_ui.py](tests/test_agent_ui.py) / [test_cli.py](tests/test_cli.py) | 导入路径 / monkeypatch 目标随模块名更新 |

**回归验证**：22 个 UI/CLI/smoke 测试原样绿；`uv build --wheel` 现在能出包，且**从 wheel 安装的干净 venv 里** `ui.server`/`environment` 正常 import、`/team` `/static/team.js` `/api/team/config` 全部 200（可移植性实证）；E0 battle digest `506f5061…` 两次一致（马尔可夫不变式未被破坏）。

## 3. Part 2：组队页后端（`src/ui/routes_team.py`，`/api/team/*`）

数据源**全部来自 environment 层**，服务端权威、前端不自行计算规则：

| 端点 | 作用 |
|---|---|
| `GET /api/team/config` | UI 元数据：队伍规模 3–6、技能槽 4、IV 0–10 且最多 3 维、31 性格、18 系、source=VALID |
| `GET /api/team/spirits?search=` | FULL 全量 593 只（裁剪字段 + is_boss/family_key），按 名/编号/系别 过滤 |
| `GET /api/team/skills?spirit=&bloodline=` | 该精灵**可学池**（默认∪技能石∪传说＋血脉技能按所选系别过滤），每条带 `battle_ready` 标记（是否已实装效果 P1∪P2） |
| `POST /api/team/validate` | `validate_team(picks, [], rules, VALID)` 一次报全部错误；合法时返回 `build_roster` 权威属性 |
| `POST /api/team/save` | **校验通过才写**；路径：绝对路径 or `teams/` 下相对路径；`.json` + 原子写 |
| `GET /api/team/saved` | 列出 `teams/` 下已存队伍（名/规模/精灵/mtime） |
| `GET /api/team/load?path=` | 加载已存队伍（原样返回，前端自动重校验） |
| `POST /api/team/delete` | 删除已存队伍 |

**协议闸门**（沿用 chat 纪律）：请求体 `extra="forbid"`；`team_size` 经 `battle_config.validate_team_size`（3–6）；相对路径必须落在 `TEAMS_DIR` 内（`..` 逃逸 → 422）。

**关键语义决策（与 environment 契约同源）**：
- **技能池 = 全可学 + battle_ready 标记**：未实装技能前端置灰「未实装」不可选 →
  **保存的队伍永远能开战**（这就是"为 battle 打基础"——保存前强制过 `validate_team(…, VALID)`）。
- **血脉不改写系别**：types 恒为精灵自身系别（克制/STAB 依据），血脉只决定可携带的血脉技能是哪个系。
- 首领不可入队、同家族只能入队一只、IV 最多 3 维——全部由 `validate_team` 兜底，前端只做提示。

## 4. Part 2：组队页前端（`src/ui/static/team.html` + `team.js` + `style.css`）

三栏布局（左精灵目录 / 中槽位编辑器 / 右技能池），顶部导航（聊天/组队）+ 队伍规模选择 + 校验按钮：

- **选将**：搜索框客户端过滤（名称/编号/系别）；BOSS 禁选；同家族冲突卡片标黄拦截；
- **槽位**：3–6 槽随规模重排（缩小前确认丢弃非空槽）；点槽加载该精灵技能池；
- **配置**：技能 chips（点击移除）、血脉下拉（无 + 18 系，变更即重拉技能池）、
  性格下拉（31 种带修正文案）、IV 输入（0–10，超 3 维拦截）、属性预览；
- **属性预览逐字节复刻** `statline.calc_combat_stats`（floor 顺序 / 性格 ×1.2·×0.9 / 生命 100·其余 50），
  保存时以服务端 `build_roster` 为准；
- **保存/加载**：文件名（进 `teams/`）或绝对路径；已存列表下拉 + 加载 + 删除；
  加载后自动重跑 `/validate`（历史队伍可能因技能白名单变化失效）。

## 5. 验证

```
uv run pytest -q                      # 485 passed（原 456 + 新 29）
uv run pytest tests/test_ui_team.py   # 29 个组队页契约测试
uv run python -m environment battle --seed 7 --json  # digest 两次一致（506f5061…）
uv build --wheel                      # 修复后的 wheel 可出包；干净 venv 安装后 UI 全功能可用
```

新增 [test_ui_team.py](tests/test_ui_team.py) 覆盖：config 元数据；精灵目录/搜索/BOSS 标记；
技能池 battle_ready 集合 == VALID 可学池、血脉只加系别匹配技能；validate 全闸门（规模/未实装/
首领/同家族/IV 超维/IV 越界/team_size 越界/extra 字段）；保存加载往返（绝对/相对/嵌套子目录/
逃逸/非 json/404/删除）；`/team` 页与聊天回归哨兵。

## 6. 边界与下一步

- **数据口径**：精灵池 = FULL（593），技能池 = 可学 ∩ 已实装（VALID 语义）。将来 P3/P4 实装更多技能后，
  `battle_ready` 标记自动点亮，旧队伍重载即重新校验——无需改 UI。
- **battle 模式基础**：`/api/team/save` 产出的 roster 文件就是 `build_roster(…, VALID)` 的形状，
  后续 battle 模式（参考 SelfPlayAgent 的 `/api/battle/*`）可直接吃 `team` 字段开战。
- **下一步候选**：battle 模式路由 + 战场页；组队页与聊天页共享会话/导航的整合；`teams/` 目录
  的版本化/导出。
