# evolution — R 线：自博弈进化管线

本目录是**自博弈进化**的完整管线：让 Agent 通过「打自博弈 → 归因 → 反思 → 编辑 Playbook」循环，
在确定性的引擎上自我改进。四个外挂：**Playbook**（可训练战术手册）+ **情境记忆** + **价值函数** + **构筑/对手池**。

## 里程碑 → 模块

| 里程碑 | 模块 | 职责 |
|---|---|---|
| **R0** 可观测度量 | `analysis` | 轨迹重放分析：一条自博弈记录 → 逐回合 `TurnAnalysis`（`analyze_record` 自己重放，不调 LLM） |
| | `bench` | 配对评测：固定实例集（d_sel/d_test）+ 双向先后手胜率 + 95% CI |
| | `league` | 联赛基架：Elo / α-rank / 收益矩阵 / 对手采样（R4 补全为完整池） |
| | `styles` | 启发式/极端风格对手（确定性基线） |
| | `feedback` | 反馈函数 μ_f：把回合分析确定性渲染成反思器可见文本（**不调 LLM**） |
| **R1** 情境记忆 | `memory` | MemRL 底座：MemoryEntry / MemoryStore / 两阶段检索 / Q 更新 |
| | `reflect` | 反思管线（最小版）：轨迹 → 确定性摘要 → 初始 MemoryEntry |
| | `health` | 记忆健康度：Q 分布 / Forgetting Rate / 检索命中率 |
| **R2** 信度分配 | `credit` | 把「输了」归到具体回合：校准偏差 + 价值落差 + 反事实回放（三信号交集） |
| **R3** 反思编辑 | `editor` | 有界编辑：L_t 上限 + `[PROTECTED]` 保护 + 编辑审计 |
| | `playbook` | Playbook：5 模块战术手册 + 版本 + token 上限 |
| | `run` | 单步编排 `run_step`：rollout → credit → reflect → edit |
| **R4** 池与门禁 | `pool` | Pareto 候选池（GEPA 实例级 Pareto 选择 + SkillOpt 晋级门） |
| **R5** 慢更新 | `meta` | Meta Playbook（只给优化器，不进对战玩家提示） |
| | `valuefn` | 价值函数档位（默认 V_heuristic，可选轨迹回归档位） |
| **R6** 记忆注入 | `memory_inject` | 记忆采纳判定 + Q 更新（把「注入记忆是否被采纳」反馈进 Q 值） |

## 核心循环（R3 `run_step`）

```
rollout（自博弈）→ credit（信度分配定位关键回合）→ reflect（双分析师提案）→ edit（有界编辑 Playbook）
```

每个 step 产出一份 `edit_apply_report`（可审计：哪些编辑 applied / rejected，为什么）。

## 设计要点

- **确定性可复现**：注入确定性玩家 + 假反思 LLM 时可逐位复现整个闭环；真实 LLM 无 key 自动降级。
- **Playbook 是有界可训练状态**：`[PROTECTED]` 保护区 + L_t 截断 + 非法 JSON 降级，防策略坍缩。
- **记忆按 `data_digest` 隔离**：跨版本记忆自动失效，不污染结论。

## 使用

```bash
python -m roco_pvp_agent evolve eval --bench d_sel       # 配对评测
python -m roco_pvp_agent evolve reflect --traj runs/x.json   # 轨迹分析
python -m roco_pvp_agent evolve credit --traj runs/x.json    # 信度分配
python -m roco_pvp_agent evolve step                         # 单步进化
python -m roco_pvp_agent evolve health --memory <dir>        # 记忆健康度
```

详见 `tmpdocs/milestones/evolution/` 下的 R 线方案与 R0–R5 复盘。
