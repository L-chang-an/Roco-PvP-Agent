# 里程碑 G1 检查点

日期：2026-08-31　状态：**☑ 通过**

## 目标
GlobalMem 数据层：存储 / 检索 / Q 更新，占据原 Playbook 生态位。纯确定性、不调 LLM。

## 背景决策（用户拍板）
- Playbook 机制**保留**作周期性 A/B 度量（不再每候选跑 864 局全量门）。
- 阵容**只用固定池**（LLM 自选阵容 = G4 延后为未来工作）。
- G1–G5 逐个 Gate。

## 我做了什么

| 文件 | 内容 |
|---|---|
| `evolution/globalmem.py`（新） | `roster_profile` / `matchup_key` / `similarity` / `GlobalMemStore`（append-only + supersedes）/ `search_global_mem` / `update_global_q` |
| `config.py` | 加 6 个 `globalmem_*` 字段（对齐已有 `memory_*` 模式） |
| `tests/test_globalmem.py`（新，19 例） | 迷雾口径 / key / 相似度序与对称 / append-only / token 上限三层可配 + 警告 / 硬过滤 / Q 更新 |

## 验收命令与结果

```bash
uv run pytest tests/test_globalmem.py -q   # 19 passed ✅
uv run pytest -q                          # 999 passed ✅（980 + 19，零回归）
```

真实样例：
```
a 视角 key: 3v2|my:types:光1火1草1/spd:high/k:a8d1s3|foe:types:普通1水2
对手画像: {"types":{"普通":1,"水":2},"names":[...]}      ← 只有 team preview 可见项
```

## 记录（坑 / 决策）

- **迷雾泄漏点（设计阶段发现并修掉）**：技能 `kind` 由技能名派生，而对手技能名开局未揭示 →
  `matchup_key` 的**对手侧只含 types/names**，测试钉死 `"k:" not in foe_seg`。
- **token 上限三层可配**（用户要求）：模块默认 `DEFAULT_MAX_STRATEGY_TOKENS=400` →
  `GlobalMemStore(max_strategy_tokens=)` → `Settings.globalmem_max_tokens`；超
  `WARN_MAX_STRATEGY_TOKENS=2000` 发警告但不阻止。
  **刻意偏离 Playbook 先例**（那里写死）：Playbook 另有有界编辑 `L_t` + 两级门禁约束，
  token 上限只是兜底；GlobalMem 无有界编辑，token 上限是唯一膨胀约束，故应可调。
  成本依据：GlobalMem 在 system prompt、每回合重发 → 单局额外输入 ≈ 上限 × 回合数
  （400×20 ≈ 8,000，占实测单局 110,000 的 ~7%）。
- **Q 的比较边界**：只在同 matchup 桶内可比（桶内阵容强度混杂抵消），跨桶比 Q 是错的——
  代码注释 + docstring 双写明；`search_global_mem` 结构上先硬过滤再桶内排序。
- 相似度权重（0.35/0.35/0.2/0.1）是拍的，G1 只保证**序**正确并单测钉住，等 G5 有真实
  命中率再调。

## 下一步
G2：`LLMPlayer` 注入 `[全局经验]` + 轨迹记录双方各用了哪条 GlobalMem。
