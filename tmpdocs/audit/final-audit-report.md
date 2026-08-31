# E 线审计 · 最终报告

> 日期：2026-08-28　分支：`feat/e-line-v2` @ `19f5b0e`（590 passed / env 覆盖 94%）
> 审计原则：文档是声明不是事实 / 测试通过不等于设计正确 / 审计与修复分离 / 优先验证不变量

## 总体结论

**E 线引擎（`src/environment/` 22 文件）是高质量代码：架构分层清晰、不变量文档化、无第三方依赖、无 TODO/FIXME/宽 except、魔法数字收敛、核心不变量（确定性/重放/非法动作/属性边界）全部独立验证通过。**

但存在 **1 个 P0 结构缺陷（AUD-E-001）**：人机对战 `BattleController` 的回合循环未随 E6.5 迷雾收口，把未过滤事件喂给对手 `on_turn_result`。**当前分支为潜伏**（无真实 LLM 读取者），**一旦按计划接入真实 LLM 对手即激活为"LLM 读取不可见状态"**（审计方案 P0 示例定义）。这是扩展 E 线时**最优先**要修的。

## 声明-实现-验证矩阵（E 线关键声明抽查）

| ID | 功能声明（checkpoint） | 代码入口 | 现有测试 | 独立运行证据 | 结论 |
|---|---|---|---|---|---|
| REQ-001 | 同 seed 逐字节复现（E0b） | `engine.step`/`rng.py` | `test_environment_match` | 双进程 digest 一致（本审计 V2.1） | ✅ 有证据通过 |
| REQ-002 | 非法动作不改变状态（E0b） | `session.submit`→`validate_decision` | `test_environment_actions` | hash/rng.calls 不变（V2.3） | ✅ 有证据通过 |
| REQ-003 | 轨迹可确定性重放（E6） | `replay_record` | `test_environment_replay` | 篡改第 2 回合被拒（V2.2） | ✅ 有证据通过 |
| REQ-004 | 玩家只见迷雾白名单（E4） | `view.observe` + `visibility.filter_events_for` | `test_environment_view` | **观战路径 ✅；人机对战路径 ❌（AUD-E-001）** | ⚠️ 部分失败 |
| REQ-005 | 引擎零第三方依赖 | 全包 import | `test_environment_dataset` | import 扫描 | ✅ 有证据通过 |
| REQ-006 | 观战全局 + LLM 迷雾（E7） | `drive_turn` + `run_spectate` | `test_spectate_players_get_fogged_views` | 既有测试 + 双闸证明 | ✅ 有证据通过 |

## 修复优先级建议

1. **AUD-E-001（P0）**：`BattleController` 事件过滤修复 → 这是 E 线扩展前必须做的（否则你后面加特性/技能、再接 LLM 对手，泄漏就激活）。
2. **AUD-E-004（P2，结构）**：随 001 一起把 `BattleController` 归一化到 `drive_turn`（消灭双循环）——一次性做，避免两套循环继续漂移。
3. **AUD-E-003（P2，纪律）**：写入协作协议——Gate 验收必须 `git status` 干净 + 在提交 HEAD 上跑。
4. **AUD-E-002（P3）**：人机轨迹补 `data_digest`/strategy/model 元数据（为将来 R 线重基线预留）。

## 下一步

- 需要的话，我可以在本分支按"审计与修复分离"先交付 **AUD-E-001 的修复**（一行过滤 + 回归测试 + 负向测试），或者先继续你规划里的 E 线扩展（特性/技能效果）——但建议 **001 先修**，因为它直接决定扩展后迷雾是否安全。
