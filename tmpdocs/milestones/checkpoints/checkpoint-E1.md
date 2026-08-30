# 里程碑 E1 检查点

日期：2026-08-24　状态：**⏭ 跳过**（负责人决定：无黄金基线）

## 目标（原计划）
E1 确定性闸门与黄金基线：把 E0b 里**已被负责人认可**的行为冻死成 golden 文件，此后任何行为变化都必须先被人看见、再被主动确认，绝不静默漂移。

## 为什么跳过（负责人 2026-08-24 拍板）
> **无黄金基线。** 后续里程碑的验收改为「测试全绿 + 逐条审阅事件流/变化」，不再用 baseline `--diff`。

E1 的**能力**（确定性）并未放弃——由 `test_environment_match.py` 的同 seed 复现 / 跨进程 digest 一致测试承担，只是不固化成 golden 文件。这样既守住「行为变化必须可见」的意图，又免去每改一次机制就要重录基线的维护成本。

## 本应交付而未交付
- `scripts/record_env_baseline.py`（`--record`/`--check`/`--diff`）
- `tests/golden/e0_baseline.json`（7 个固定场景完整事件流 + 逐回合 state_hash）
- `tests/test_environment_golden.py`

## 记录的替代守卫（已在 E0b 测试里落地）
| 原 E1 意图 | 替代测试 |
|---|---|
| 同 seed 复现 | `test_same_seed_same_digest_and_decisions` |
| 跨进程一致 | `test_subprocess_digest_matches_inprocess` |
| 快照可恢复 | `test_markov_property`（`from_dict(to_dict(s))` 后同一回合逐字节相同） |
| 换 seed 轨迹不同 | `test_diff_seed_diff_digest` |

## 下一步
E2 属性克制与系别——第一个会改变既有伤害数字的里程碑；验收按新口径「测试全绿 + 事件流里肉眼核对克制/STAB」。
