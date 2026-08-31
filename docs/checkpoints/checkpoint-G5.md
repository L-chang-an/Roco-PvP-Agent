# 里程碑 G5 检查点

日期：2026-08-31　状态：**☑ 通过**

## 目标
把 GlobalMem 完整链路端到端串起来（固定池取阵容 → 战斗双重注入 → 双视角复盘 → 双库落地 → Q 更新 → 周期性 A/B），并提供「能证明它有效」的度量。

## 我做了什么

| 文件 | 内容 |
|---|---|
| `evolution/globalmem_run.py`（新） | `run_battles`（七阶段主循环）+ `measure_ab`（开/关 A/B）+ `_OfflineGlobalMemPlayer` + `DeterministicAnalyst` |
| `__main__.py` | `evolve battles` 子命令（`--fake-analyst` / `--ab-every` / `--ab-instances` / `--ab-seeds` / `--progress`） |
| `tests/test_globalmem_run.py`（新，14 例） | 全链路 / 双库 / append-only / Q 更新 / 采纳 / 轨迹 / 三种开关 / 注入缝 / A/B / 确定性 |

## 验收命令与结果

```bash
uv run pytest tests/test_globalmem_run.py -q   # 14 passed ✅
uv run pytest -q                              # 1038 passed ✅（1024 + 14，零回归）

uv run python -m roco_pvp_agent evolve battles --n 6 --instances 2 --fake-analyst \
  --globalmem-dir /tmp/g5/gm --memory-dir /tmp/g5/mem --ab-every 3 --ab-instances 2
#   #1 create,create  loaded(a=-,b=-)                  ← 冷启动 miss
#   #3 update,update  loaded(a=gm_77e37b051,b=...)     ← 命中 → supersede
#   A/B: 开=0.333 关=0.333 delta=+0.000（n=3/边）
#   final GlobalMem active=6 total=12 · 局部记忆 entries=231
```

`active=6 / total=12` = append-only 生效（6 活跃 + 6 被 supersede 的旧文本留库可回滚）。

## 记录（坑 / 决策）

- **几乎全是复用**：七个阶段里六个直接用既有组件（`build_instances`/`run_selfplay`/
  `make_global_retriever`/`_make_memory_retriever`/`analyze_both`/`apply_decision`/
  `store_experiences`/`update_global_q`/`apply_adoption`/`paired_eval`），新写的只有编排循环
  与 A/B 封装——前面五个里程碑分层的回报。
- **Q 只更新本局真正用过的那条**：先落库、再用 `record["global_mem_{side}"]` 更新。即使分析把
  它 supersede 了，append-only 保证旧条目仍在，不会把胜负错记到新建条目上。
- **A/B 是「开/关对比」而非「新旧候选对比」**：GlobalMem 无候选/冠军概念。它证明「记忆整体
  有用」，**不证明「某一条有用」**——单条价值只能靠同 matchup 桶内 Q 排序间接反映
  （拍板选 B 方案时已接受此取舍，比 Playbook 门禁弱）。
- **离线 A/B 的 delta 恒 ≈ 0**（实测 +0.000）：离线玩家是确定性随机策略，**不读**注入文本，
  所以离线只验证编排链路与度量管线；真实效果必须 `--llm`（同 R4「离线基线不产生晋级」哲学）。
- **`--fake-analyst` 产出的不是真经验**：零 LLM 确定性占位文本，只为无 key 环境端到端验证闭环。
- **`_OfflineGlobalMemPlayer`**：`FakeLLMPlayer` 不接检索器，用它跑离线编排会让「检索→记录→
  Q 更新」整段无法验证；本类把检索/记录做真、决策仍走确定性随机流。
- rich markup 坑：`loaded[...]` 里的方括号被 rich 当样式标签吞掉，改用圆括号 + `markup=False`。

## 成本

| | Playbook `evolve steps --n 1` | GlobalMem `evolve battles --n 1` |
|---|---|---|
| 对局数 | 1748 | **1** |
| LLM 调用 | ~30,500 | **~42**（40 对战 + 2 分析） |

真实 LLM 推荐首跑：`--n 10 --llm --instances 5 --ab-every 5` ≈ 500 次调用，比原来一个 step 便宜 60 倍。

## G 线状态
G1（数据层）→ G2（注入）→ G3（分析师）→ G5（编排）**全部闭环**。
G4（LLM 自选阵容）按拍板列为未来工作。
