# 里程碑 R1 检查点

日期：2026-08-30　状态：**☑ 通过**

## 目标
打通「轨迹 → 反思产物 → 记忆条目 → 检索」的 MemRL 底座。R1 只**记录不改变决策**（检索结果先落日志，R4 才接线注入 LLM 玩家）。

## 我做了什么

| 文件 | 改动 |
|---|---|
| `evolution/memory.py`（新） | 从 main 移植：`MemoryStore`/`KeywordEmbedder`/`two_phase_search`/`update_q`/`make_entry_id`（纯 stdlib，零适配） |
| `evolution/health.py`（新） | 从 main 移植：`memory_health` |
| `evolution/feedback.py`（新） | 从 main 移植：`render_feedback`（μ_f 确定性渲染，事件机制部分）——**R0 漏掉、R1 补上** |
| `evolution/reflect.py`（新，切片） | 只移植 R1 部分：`extract_experiences`/`store_experiences` + 4 helper；R3 的 `ReflectionService` 暂缓 |
| `config.py` | 加 `memory_enabled=False` + 9 个 memory 字段（默认全关） |
| `__main__.py` | `evolve reflect` 加 `--out`；新增 `evolve health --memory` |
| `tests/test_evolution_r1.py`（新） | 检索边界/侧锁/digest 隔离/roundtrip/Q 更新/轨迹提取 |

## 验收命令与结果

```bash
uv run pytest tests/test_evolution_r1.py -q   # 6 passed ✅
uv run pytest -q   # 936 passed ✅（837 既有 + 6 R1 + 负责人并行 M 线 ~93，零回归）
uv run python -m rock_pvp_agent evolve reflect --traj runs/selfplay-11-1.json --out /tmp/mem_test   # 写 40 条 ✅
uv run python -m rock_pvp_agent evolve health --memory /tmp/mem_test   # count=39 · Q 全 0 · FR=0 ✅
```

## 用户的疑问 / 修改要求
- 面试模拟：询问 R1 设计理由、记忆数据协议各字段来源与消费——已在对话中讲解，无代码改动。

## 记录
- **data_digest 复用 S0 指纹**：`reflect.py` 的 `_data_digest`/`_rules_version` 读 record stamp（旧记录回退 `datafingerprint`），与轨迹版本同口径（对齐 AUD-E-002）。
- **R0 缺口**：`extract_experiences` 依赖 `render_feedback`，feedback.py 本应 R0 交付、漏了，R1 补上。
- **去重正确性**：40 条写入 → 39 条（相邻同局面同动作 → 同 entry_id 幂等去重）。
- **迷雾口径**：experience_text 里未揭示的对手技能只显示槽位（`foe_revealed`），记忆不泄密。
- **并行工作**：负责人并行做 M 线（agent.py 三注入缝 + advisor/ 包），本次 commit **只 stage R1 文件**，不碰 M 线。

## 下一步
R2 信度分配（校准偏差 + 价值落差 + 反事实回放）：`evolution/credit.py` + `player.py` 加 prediction 参数 + `selfplay.py` 写 analysis_a/b。
